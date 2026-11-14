"""Seven independent cross-checks run on every finished detection. They do NOT decide the class (detect.py does);
they test the decision from a different angle and their results are written into the evidence and the reasoning.

  1 storm_model      fleet signal loss regressed on S4; per event: how much loss S4 predicts and how much is left over
  2 source_fit       does a single ground transmitter (loss falling with distance) explain the per-machine losses?
  3 cusum            causal CUSUM change detector: how long after onset would a live system have alarmed?
  4 posterior        naive-Bayes combination of the evidence (hand-set likelihoods, single-cause assumption)
  5 fix_profile      which positioning modes the receiver passed through (RTK fixed/float/DGPS/autonomous/none)
  6 rel_geometry     did the reported track move relative to a neighbour by the same amount as the cross-track error?
  7 periodicity      multipath: repeat period of the dips and whether a second recurring spot exists

Everything is unsupervised (no labels) and deterministic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import TRACTORS
from .geo import M_PER_DEG_LAT, to_xy

LAT0, LON0 = 46.9, -97.2
CAUSES = ["SPACE_WEATHER", "JAMMING", "SPOOFING", "EQUIPMENT_FAULT"]
FIX_ORDER = ["RTK_FIXED", "RTK_FLOAT", "DGPS", "AUTONOMOUS", "NO_FIX"]
FIX_PLAIN = {"RTK_FIXED": "full-precision fix", "RTK_FLOAT": "reduced-precision (float) fix", "DGPS": "metre-level (DGPS) fix",
             "AUTONOMOUS": "uncorrected stand-alone fix", "NO_FIX": "no position at all"}
# P(observation is present | cause), hand-set from the physics in the brief. Order: SPACE_WEATHER, JAMMING, SPOOFING, EQUIPMENT_FAULT.
LIKELIHOOD = {
    "several machines lost signal together":            (0.95, 0.80, 0.03, 0.03),
    "the loss was the same on every machine":           (0.90, 0.15, 0.50, 0.50),
    "scintillation (S4) was elevated":                  (0.90, 0.20, 0.20, 0.20),
    "geomagnetic activity (Kp) was at storm level":     (0.70, 0.20, 0.20, 0.20),
    "an interference report matched":                   (0.05, 0.60, 0.10, 0.05),
    "the losses fit one ground transmitter":            (0.02, 0.90, 0.50, 0.50),
    "position drifted while the signal stayed healthy": (0.02, 0.02, 0.95, 0.03),
    "the receiver kept its full-precision fix":         (0.01, 0.05, 0.90, 0.40),
    "the machine reported its correction link down":    (0.01, 0.01, 0.01, 0.50),
    "one machine kept dropping satellites":             (0.02, 0.05, 0.02, 0.50),
    "the neighbours were unaffected":                   (0.02, 0.15, 0.90, 0.95),
}


def _xy_to_latlon(x, y):
    return LAT0 + y / M_PER_DEG_LAT, LON0 + x / (M_PER_DEG_LAT * np.cos(np.radians(LAT0)))


class Analysis:
    def __init__(self, det):
        self.d, self.F, self.p, self.b = det, det.F, det.p, det.b
        F = self.F
        x, y = to_xy(F["lat"].values, F["lon"].values, LAT0, LON0)
        self.X = pd.DataFrame(x, index=F.idx, columns=F["lat"].columns)
        self.Y = pd.DataFrame(y, index=F.idx, columns=F["lat"].columns)
        # one row per second per machine, time-ordered (tolerates duplicated or shuffled input rows)
        self._tele = {tr: g.drop_duplicates("ts").set_index("ts").sort_index() for tr, g in self.b.tele.groupby("tractor_id")}
        self._storm = None
        self._period = None

    # ---------------------------------------------------------------- 1. storm model
    def fit_storm_model(self, cands):
        """Robust line: fleet-median loss (dB, 15 min means) on S4, leaving out 15 min bins that touch a jamming or
        equipment detection so that only the ionosphere is modelled."""
        s4 = self.b.s4
        if s4 is None or len(s4) < 10:
            return None
        loss = (-self.F["cn0_dev"].median(axis=1)).resample("15min").mean()
        x = s4.set_index("ts")["s4"].reindex(loss.index)
        ok = loss.notna() & x.notna()
        for c in cands:
            if c["cls"] in ("JAMMING", "EQUIPMENT_FAULT"):
                ok &= ~((loss.index + pd.Timedelta(minutes=15) > c["start"]) & (loss.index < c["end"]))
        if ok.sum() < 10 or x[ok].std() == 0:
            return None
        slope, icpt = np.polyfit(x[ok].values, loss[ok].values, 1)
        for _ in range(3):                       # trimmed refit: bins more than 3 dB off the line are not ionospheric (e.g. a jam
            keep = ok & ((loss - (icpt + slope * x)).abs() <= 3.0)   # outside the window being analysed), so they do not shape it
            if keep.sum() < 10 or x[keep].std() == 0 or keep.sum() == ok.sum():
                break
            ok = keep
            slope, icpt = np.polyfit(x[ok].values, loss[ok].values, 1)
        r = float(np.corrcoef(x[ok].values, loss[ok].values)[0, 1])
        self._storm = dict(slope=float(slope), icpt=float(icpt), r=r, n=int(ok.sum()))
        return self._storm

    def storm_model(self, c):
        m, s4 = self._storm, c["s4"]
        if not m or s4["elevated"] is None or not np.isfinite(s4["mean"]):
            return None
        obs = float(np.nanmean([s["cn0_dev_mean"] for s in c["stats"].values()])) * -1.0
        pred = max(0.0, m["icpt"] + m["slope"] * s4["mean"])
        return dict(pred=pred, obs=obs, resid=obs - pred, slope=m["slope"], r=m["r"], n=m["n"])

    # ---------------------------------------------------------------- 2. single ground source
    def source_fit(self, c):
        """Least squares for loss = A + B*log10(distance) over all working machines and 10 s bins, source position by
        grid search (100 m, then 10 m). Free-space spreading gives B = -20 dB per tenfold distance."""
        a, b = c["start"], c["end"] - pd.Timedelta(seconds=1)
        xs, ys, ls, who = [], [], [], []
        for tr in TRACTORS:
            w = self.F["work"][tr].loc[a:b].values.astype(bool)
            xs.append(self.X[tr].loc[a:b].values[w]); ys.append(self.Y[tr].loc[a:b].values[w])
            ls.append(-self.F["cn0_dev"][tr].loc[a:b].values[w]); who += [tr] * int(w.sum())
        xs, ys, ls, who = np.concatenate(xs), np.concatenate(ys), np.concatenate(ls), np.array(who)
        ok = np.isfinite(xs) & np.isfinite(ls)
        xs, ys, ls, who = xs[ok], ys[ok], ls[ok], who[ok]
        if len(ls) < 30 or len(set(who)) < 2:
            return None
        sst = float(((ls - ls.mean()) ** 2).sum())
        out = dict(n=int(len(ls)), loss_sd=float(ls.std()))
        if ls.std() < 0.5:                       # nothing varies with place: no spatial structure to fit
            return dict(out, r2=0.0, flat=True)

        def search(gx, gy):
            GX, GY = np.meshgrid(gx, gy, indexing="ij")
            ld = np.log10(np.hypot(xs[None, None, :] - GX[..., None], ys[None, None, :] - GY[..., None]) + 1.0)
            ldm = ld.mean(axis=2, keepdims=True)
            sxx = ((ld - ldm) ** 2).sum(axis=2)
            B = ((ld - ldm) * (ls - ls.mean())).sum(axis=2) / np.where(sxx > 0, sxx, np.nan)
            sse = sst - B ** 2 * sxx
            i, j = np.unravel_index(np.nanargmin(sse), sse.shape)
            return float(GX[i, j]), float(GY[i, j]), float(B[i, j]), float(sse[i, j])

        cx, cy = float(xs.mean()), float(ys.mean())
        x0, y0, _, _ = search(np.arange(cx - 3000, cx + 3001, 100), np.arange(cy - 3000, cy + 3001, 100))
        x0, y0, B, sse = search(np.arange(x0 - 100, x0 + 101, 10), np.arange(y0 - 100, y0 + 101, 10))
        lat, lon = _xy_to_latlon(x0, y0)
        per = {tr: dict(dist=float(np.hypot(xs[who == tr] - x0, ys[who == tr] - y0).mean()), loss=float(ls[who == tr].mean()))
               for tr in TRACTORS if (who == tr).any()}
        out.update(r2=1.0 - sse / sst, slope=B, x=x0, y=y0, lat=float(lat), lon=float(lon), per=per, flat=False)
        hits = [r for r in c.get("rfi", []) if r.get("_within")]
        for r in hits[:1]:
            latc = next((v for k, v in r.items() if str(k).lower() in ("lat", "latitude", "centerlat", "center_lat")), None)
            lonc = next((v for k, v in r.items() if str(k).lower() in ("lon", "lng", "longitude", "centerlon", "center_lon")), None)
            if latc is not None and lonc is not None:
                rx, ry = to_xy(latc, lonc, LAT0, LON0)
                out["rfi_id"], out["rfi_km"] = r.get("id", "report"), float(np.hypot(rx - x0, ry - y0) / 1000.0)
        return out

    @staticmethod
    def source_ok(sf):
        """A convincing point source: explains >= 90 % of the variation with a physically sensible falloff."""
        return bool(sf and not sf.get("flat") and sf["r2"] >= 0.9 and -35.0 <= sf["slope"] <= -8.0)

    # ---------------------------------------------------------------- 3. causal CUSUM
    def _signal(self, c):
        """(series, direction, floor, unit, what) for the quantity that carries this cause, at 10 s resolution, unsmoothed."""
        F, tr = self.F, c["tractors"]
        if c["cls"] in ("SPACE_WEATHER", "JAMMING"):
            return F["cn0_dev"][tr].mean(axis=1), -1.0, 1.0, "dB", "mean signal strength of the affected machines"
        if c["cls"] == "SPOOFING":
            oth = [o for o in TRACTORS if o not in tr]
            ref = F["xte"][oth].median(axis=1).fillna(0.0) if oth else 0.0
            return (F["xte"][tr].mean(axis=1) - ref), +1.0, 0.3, "m", "cross-track error relative to the neighbours"
        if c.get("mode") == "link":
            return 1.0 - F["link"][tr[0]], +1.0, 0.1, "", "share of each 10 s with the correction link down"
        return F["dropout_local"][tr[0]].astype(float), +1.0, 0.1, "", "share of 10 s intervals with a satellite dropout"

    def cusum(self, c):
        """One-sided CUSUM that only ever looks backwards. Reference level and noise come from the hour that ends 30 min
        before the detected onset; the detector then runs forward from 30 min before onset. Allowance k = max(3 sigma,
        a physical floor); alarm when the sum exceeds 5 k. Returns the alarm delay after onset and any earlier false alarm."""
        y, sign, floor, unit, what = self._signal(c)
        a = c["start"]
        ref = y.loc[a - pd.Timedelta(minutes=90):a - pd.Timedelta(minutes=30)].dropna()
        run = y.loc[a - pd.Timedelta(minutes=30):c["end"]].dropna()
        if len(ref) < 30 or len(run) < 10:
            return dict(delay_s=None, what=what, k=None, unit=unit, false_alarm=False, no_ref=True)
        mu, sd = float(ref.median()), float((ref - ref.median()).abs().median() * 1.4826)
        k = max(3.0 * sd, floor)
        h = 5.0 * k
        s, alarm, false_alarm = 0.0, None, None
        for ts, v in run.items():
            s = max(0.0, s + sign * (v - mu) - k)
            if s > h:
                if ts < a - pd.Timedelta(seconds=self.p.bin_s):
                    false_alarm, s = ts, 0.0
                    continue
                alarm = ts
                break
        if alarm is None:
            return dict(delay_s=None, what=what, k=k, unit=unit, false_alarm=false_alarm is not None, no_ref=False)
        return dict(no_ref=False, delay_s=float((alarm - a).total_seconds()) + self.p.bin_s, what=what, k=k, unit=unit,
                    false_alarm=false_alarm is not None)

    # ---------------------------------------------------------------- 5. fix-state profile
    def fix_profile(self, c):
        out = {}
        for tr in c["tractors"]:
            if tr not in self._tele:
                continue
            g = self._tele[tr].loc[c["start"]:c["end"] - pd.Timedelta(seconds=1)]
            g = g[g["speed_mps"] > self.p.work_speed]
            if not len(g):
                continue
            share = g["fix_state"].value_counts(normalize=True)
            rank = g["fix_state"].map({k: i for i, k in enumerate(FIX_ORDER)}).fillna(0).astype(int)
            lost = g.index[rank.values > 0]
            out[tr] = dict(share={k: float(share.get(k, 0.0)) for k in FIX_ORDER}, worst=FIX_ORDER[int(rank.max())],
                           first_loss_s=(float((lost[0] - c["start"]).total_seconds()) if len(lost) else None),
                           changes_per_min=float((rank.diff().fillna(0) != 0).sum() / max(len(g) / 60.0, 1e-9)))
        return out or None

    # ---------------------------------------------------------------- 6. relative geometry (spoofing)
    def rel_geometry(self, c):
        """Reported position of the machine relative to each neighbour, at 1 Hz. If the machine is really being pulled,
        its reported track moves sideways relative to a neighbour holding its own line, by the same amount as the
        cross-track error. Uses the neighbour that stays in its own row for the whole event."""
        tr = c["tractors"][0]
        a, b = c["start"], c["end"] - pd.Timedelta(seconds=1)
        if tr not in self._tele:
            return None
        me = self._tele[tr].loc[a:b]
        if len(me) < 120:
            return None
        mx, my = to_xy(me["lat"].values, me["lon"].values, LAT0, LON0)
        xte = me["cross_track_error_m"].abs().values
        best = None
        for o in TRACTORS:
            if o in c["tractors"] or o not in self._tele:
                continue
            ot = self._tele[o].reindex(me.index)
            ox, oy = to_xy(ot["lat"].values, ot["lon"].values, LAT0, LON0)
            dx, dy = mx - ox, my - oy
            ok = np.isfinite(dx) & np.isfinite(dy)
            if ok.sum() < 120:
                continue
            d = np.hypot(dx - np.nanmedian(dx[ok][:60]), dy - np.nanmedian(dy[ok][:60]))
            if np.nanmax(d) > 3.0 * np.nanmax(xte) + 5.0:
                continue                           # one of the two changed rows: not a clean reference
            if np.nanstd(d[ok]) == 0 or np.nanstd(xte[ok]) == 0:
                continue
            r = float(np.corrcoef(d[ok], xte[ok])[0, 1])
            res = dict(ref=o, r=r, moved=float(np.nanmax(d)), xte_max=float(np.nanmax(xte)),
                       sep0=float(np.hypot(np.nanmedian(dx[ok][:60]), np.nanmedian(dy[ok][:60]))))
            if best is None or r > best["r"]:
                best = res
        return best

    # ---------------------------------------------------------------- 7. multipath periodicity / second spot
    def periodicity(self):
        """Repeat period of the min-C/N0 dips per machine (autocorrelation of the dip indicator over working time) and
        how the dips that fall OUTSIDE the recurring band are spread along the field (a second obstruction would pile up)."""
        if self._period is not None:
            return self._period
        p, out = self.p, {"period": {}, "ac": {}}
        for tr, g in self._tele.items():
            mc = g["min_cn0_dbhz"].where(g["speed_mps"] > p.work_speed)
            dev = (mc - mc.rolling(p.mp_ctx_s, center=True, min_periods=p.mp_ctx_s // 6).median())
            z = (dev <= -p.mp_dip_db).astype(float).values
            # longest stretch is 72 h at 1 Hz; autocorrelation by FFT
            z = z - z.mean()
            n = 1 << int(np.ceil(np.log2(2 * len(z))))
            f = np.fft.rfft(z, n)
            ac = np.fft.irfft(f * np.conj(f), n)[:1500]
            if ac[0] <= 0:
                continue
            ac = ac / ac[0]
            lag = int(np.argmax(ac[120:1200]) + 120)
            half = lag // 2
            if half >= 120 and ac[half - 3:half + 4].max() >= 0.6 * ac[lag]:      # prefer the fundamental over its double
                lag = int(np.argmax(ac[half - 3:half + 4]) + half - 3)
            out["period"][tr], out["ac"][tr] = float(lag), float(ac[lag])
        det = self.d
        y0 = getattr(det, "_mp_y0", None)
        raw = getattr(det, "_mp_raw", None)
        if y0 is not None and raw:
            ys = np.array([e["y"] for e in raw])
            off = ys[np.abs(ys - y0) > p.mp_band_m / 2]
            hist = np.histogram(off, bins=np.arange(np.floor(ys.min() / 100) * 100, ys.max() + 100, 100))[0] if len(off) else np.array([0])
            inband = int((np.abs(ys - y0) <= p.mp_band_m / 2).sum())
            out.update(n_in=inband, n_off=int(len(off)), off_max_bin=int(hist.max()), off_bins=int(len(hist)),
                       second_spot=bool(hist.max() >= 0.2 * inband))
        self._period = out
        return out

    # ---------------------------------------------------------------- 4. naive-Bayes cross-check
    def posterior(self, c, ax):
        """Posterior over the four non-multipath causes from present/absent observations, uniform prior. Single-cause
        model: when the event sits inside a separately reported storm, the storm's own indicators (S4, Kp, uniform loss)
        are left out because that storm already accounts for them."""
        st, nb, s4 = c["stats"], c["neighbors"], c["s4"]
        conc = [o for o in c.get("concurrent", []) if o["cls"] != c["cls"]]
        in_storm = c["cls"] != "SPACE_WEATHER" and any(o["cls"] == "SPACE_WEATHER" for o in conc)
        same = [o for o in conc if set(o["tractors"]) & set(c["tractors"]) and o["cls"] != "SPACE_WEATHER"]
        other_spoof = any(o["cls"] == "SPOOFING" for o in same)          # a separately reported cause on the same machine
        other_equip = any(o["cls"] == "EQUIPMENT_FAULT" for o in same)   # already accounts for these observations
        multi = sum(s["cn0_dev_mean"] < -2.0 for s in st.values()) >= 2
        obs = {"several machines lost signal together": multi}
        if multi and c.get("spread_db") is not None and not in_storm:
            obs["the loss was the same on every machine"] = c["spread_db"] < self.p.uniform_spread_db
        if s4["elevated"] is not None and not in_storm:
            obs["scintillation (S4) was elevated"] = bool(s4["elevated"])
        if np.isfinite(c["kp_max"]) and not in_storm:
            obs["geomagnetic activity (Kp) was at storm level"] = bool(c["kp_max"] >= self.p.kp_high)
        if self.b.rfi is not None and len(self.b.rfi):
            obs["an interference report matched"] = any(r["_within"] for r in c["rfi"])
        if multi and ax.get("source") and not ax["source"].get("flat"):
            obs["the losses fit one ground transmitter"] = self.source_ok(ax["source"])
        elif multi and ax.get("source"):
            obs["the losses fit one ground transmitter"] = False
        t0 = st[c["tractors"][0]]
        if not other_spoof:
            # same relative test the detector uses: error beyond the neighbours' while this machine's signal is no worse than the fleet's
            nb_x = float(np.mean([n["xte_mean"] for n in nb.values()])) if nb else 0.0
            obs["position drifted while the signal stayed healthy"] = bool(
                len(st) == 1 and t0["xte_mean"] - nb_x > self.p.xte_abs_m and t0["resid_mean"] > -self.p.xte_cn0_ok_db)
        if not in_storm:
            obs["the receiver kept its full-precision fix"] = bool(max(s["fixbad_frac"] for s in st.values()) < 0.02)
        obs["the machine reported its correction link down"] = bool(len(st) == 1 and t0["link_frac"] < 0.9
                                                                    and all(n["link_frac"] > 0.99 for n in nb.values()))
        if not in_storm and not other_equip:
            obs["one machine kept dropping satellites"] = bool(len(st) == 1 and max(t0["sats_dropout_frac"], t0["mincn0_dip_frac"]) > 0.3)
        if nb and not in_storm:
            obs["the neighbours were unaffected"] = all(n["cn0_dev_mean"] > -2.0 and n["xte_max"] < 1.5 for n in nb.values())
        logp = np.zeros(len(CAUSES))
        for name, present in obs.items():
            lk = np.array(LIKELIHOOD[name])
            logp += np.log(lk if present else 1.0 - lk)
        pr = np.exp(logp - logp.max())
        pr = pr / pr.sum()
        order = np.argsort(-pr)
        return dict(probs={CAUSES[i]: float(pr[i]) for i in range(len(CAUSES))}, top=CAUSES[order[0]], second=CAUSES[order[1]],
                    agrees=CAUSES[order[0]] == c["cls"], n_obs=len(obs), in_storm=in_storm, overlap=bool(same),
                    present=[k for k, v in obs.items() if v])

    # ---------------------------------------------------------------- driver
    def annotate(self, cands):
        real = [c for c in cands if c["cls"] != "MULTIPATH"]
        self.fit_storm_model(real)
        for c in real:
            ax = {"storm": self.storm_model(c) if c["cls"] in ("SPACE_WEATHER", "JAMMING") else None,
                  "source": self.source_fit(c) if c["cls"] in ("SPACE_WEATHER", "JAMMING") else None,
                  "cusum": self.cusum(c), "fix": self.fix_profile(c),
                  "geom": self.rel_geometry(c) if c["cls"] == "SPOOFING" and len(c["tractors"]) == 1 else None}
            ax["posterior"] = self.posterior(c, ax)
            c["ax"] = ax
        if any(c["cls"] == "MULTIPATH" for c in cands):
            per = self.periodicity()
            for c in cands:
                if c["cls"] == "MULTIPATH":
                    c["ax"] = {"period": per}
        return cands
