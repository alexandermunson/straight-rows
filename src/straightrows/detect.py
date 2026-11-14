"""Episode detection + cause attribution.

Channels (each looks at the signal that best exposes one family of causes):
  R  regional C/N0 collapse (all working machines)      -> SPACE_WEATHER | JAMMING (S4/Kp/RFI decide)
  L  local C/N0 residual after removing regional part   -> JAMMING | EQUIPMENT_FAULT | MULTIPATH
  E  correction-link outage                              -> EQUIPMENT_FAULT
  X  cross-track error without signal collapse           -> SPOOFING
Candidates carry the measurements the evidence writer needs; nothing is final until report.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import EVAL_END, EVAL_START, TRACTORS, Params
from .features import Features, build
from .geo import to_xy
from .sda import SDA
from .stats import block_means, correlate, linfit, mean_ci, std_residuals


# ---------------------------------------------------------------- segmentation helpers
def _runs(mask):
    m = np.asarray(mask, bool)
    if not m.any():
        return []
    d = np.diff(np.concatenate([[0], m.view(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))   # [i0, i1)


def _bridge(mask, gap_bins):
    m = np.asarray(mask, bool).copy()
    runs = _runs(m)
    for (a0, a1), (b0, b1) in zip(runs, runs[1:]):
        if b0 - a1 <= gap_bins:
            m[a1:b0] = True
    return m


def segments(hi, lo, p, min_s, hysteresis=True, gap_s=None):
    """Runs where `hi` holds for >= min_s, extended outward while `lo` holds (so ramps get a true onset)."""
    gap = int((p.gap_bridge_s if gap_s is None else gap_s) / p.bin_s)
    hi = _bridge(hi, gap)
    lo = _bridge(np.asarray(lo, bool) | hi, gap) if hysteresis else hi
    out = []
    for a, b in _runs(lo):
        seeds = [(x, y) for x, y in _runs(hi[a:b]) if (y - x) * p.bin_s >= min_s]
        if seeds:
            out.append((a, b))
    return out


def block_means_aligned(x, block):
    """Block means that keep nan for empty blocks so two series stay index-aligned for correlation."""
    x = np.asarray(x, float)
    n = len(x) // block
    if n == 0:
        return np.array([])
    m = x[: n * block].reshape(n, block)
    with np.errstate(all="ignore"):
        return np.array([np.nanmean(r) if np.isfinite(r).any() else np.nan for r in m])


def _w(series, a, b):
    return series.loc[a:b - pd.Timedelta(seconds=1)]


class Detector:
    def __init__(self, bundle, p: Params | None = None, window=(EVAL_START, EVAL_END)):
        self.b, self.p, self.window = bundle, p or Params(), window
        self.F: Features = build(bundle.tele, self.p)
        self.sda = SDA(bundle, self.p)
        self.idx = self.F.idx
        self.inwin = (self.idx >= window[0]) & (self.idx < window[1])

    # ------------------------------------------------------------ utilities
    def _span(self, i0, i1):
        a = self.idx[i0]
        b = self.idx[min(i1, len(self.idx)) - 1] + pd.Timedelta(seconds=self.p.bin_s)
        return a, min(b, self.window[1])

    def _stats(self, tr, a, b):
        F = self.F
        g = lambda k: _w(F[k][tr], a, b)
        w = g("work")
        s = {
            "cn0_dev_mean": float(g("cn0_dev")[w].mean()), "cn0_dev_min": float(g("cn0_dev")[w].min()),
            "cn0_mean": float(g("cn0")[w].mean()), "cn0_base": F.base[tr]["cn0"],
            "mincn0_min": float(g("mincn0")[w].min()), "mincn0_base": F.base[tr]["mincn0"],
            "sats_min": float(g("satsmin")[w].min()), "sats_mean": float(g("sats")[w].mean()),
            "sats_base": F.base[tr]["sats"],
            "xte_mean": float(g("xte")[w].mean()), "xte_max": float(g("xtemax")[w].max()),
            "link_frac": float(g("link")[w].mean()), "fixbad_frac": float(g("fixbad")[w].mean()),
            "resid_mean": float(g("resid")[w].mean()),
            "sats_dropout_frac": float((g("satsmin_dev")[w] <= -self.p.sats_drop).mean()),
            "mincn0_dip_frac": float((g("mincn0_dev")[w] <= -self.p.min_cn0_dip_db).mean()),
            "lat": float(g("lat")[w].mean()), "lon": float(g("lon")[w].mean()),
        }
        return s

    def _xte_thr(self, tr):
        b = self.F.base[tr]
        return max(self.p.xte_abs_m, b["xte"] + self.p.xte_k_mad * b["xte_mad"])

    def _ramp_onset(self, y, i0, i1, level, mad):
        """Back-extrapolate a rising ramp to where it leaves the noise floor.
        Fits a line to the segment's rising part; used only if the fit is good and moves onset earlier (<= 30 min)."""
        n = min(i1, i0 + int(1800 / self.p.bin_s))
        seg = y[i0:n]
        peak = np.nanmax(seg) if len(seg) else np.nan
        if not np.isfinite(peak) or peak < level:
            return i0
        top = int(np.nanargmax(seg > 0.9 * peak)) if (seg > 0.9 * peak).any() else len(seg) - 1
        pts = np.arange(0, top + 1)
        v = seg[pts]
        ok = np.isfinite(v)
        if ok.sum() < 6:
            return i0
        m, c = np.polyfit(pts[ok], v[ok], 1)
        r2 = 1 - np.sum((v[ok] - (m * pts[ok] + c)) ** 2) / max(np.sum((v[ok] - v[ok].mean()) ** 2), 1e-9)
        if m <= 0 or r2 < 0.8:
            return i0
        floor = 3 * max(mad, 0.01)
        t0 = (floor - c) / m
        return max(i0 + int(np.floor(t0)), i0 - int(1800 / self.p.bin_s), 0) if t0 < 0 else i0

    def _sustained(self, tr, i0, i1):
        """Curve-fit outlier rule (ME 457 Ch3). Fit a line to the machine's C/N0 residual on both sides of [i0, i1);
        the dip is a real change only if >= glitch_min_run consecutive event bins sit beyond glitch_z standard errors
        of the estimate (i.e. consistent with their neighbours). Returns (sustained, median standardized residual);
        (True, nan) when there is too little context to judge, so the filter never removes on missing data."""
        p = self.p
        y = self.F["resid"][tr].values
        w = self.F["work"][tr].values
        k = int(p.glitch_ctx_s / p.bin_s)
        ctx = np.r_[np.arange(max(0, i0 - k), i0), np.arange(i1, min(len(y), i1 + k))]
        ctx = ctx[w[ctx] & np.isfinite(y[ctx])]
        if len(ctx) < 10:
            return True, float("nan")
        fit = linfit(ctx, y[ctx], p.corr_alpha)
        ev = np.arange(i0, i1)
        z = std_residuals(ev, y[ev], fit, p.glitch_syx_floor)
        bad = np.where(np.isfinite(z), z < -p.glitch_z, False)
        run = best = 0
        for v in bad:
            run = run + 1 if v else 0
            best = max(best, run)
        need = min(p.glitch_min_run, max(1, int(p.mp_min_s / p.bin_s)))     # coarse bins: a single bin may span mp_min_s
        return bool(best >= need), float(np.nanmedian(z))

    # ------------------------------------------------------------ channels
    def run(self):
        F, p = self.F, self.p
        cands = []
        win = self.inwin
        work = F["work"]
        resid = F["resid"]
        reg = F["regional"]

        # --- E: link outage ---------------------------------------------------
        e_mask = {}
        for tr in TRACTORS:
            bad = (work[tr] & (F["link"][tr] < p.link_bad_frac)).values & win
            e_mask[tr] = bad
            for a_, b_ in segments(bad, bad, p, p.equip_min_s, hysteresis=False, gap_s=p.link_gap_s):
                a, b = self._span(a_, b_)
                cands.append(dict(cls="EQUIPMENT_FAULT", tractors=[tr], start=a, end=b, channel="E", mode="link"))

        # --- R: regional collapse ---------------------------------------------
        reg_hi = ((reg < -p.regional_drop_db) & (F["nwork"] >= 2)).values & win
        reg_lo = ((reg < -p.regional_drop_db * 0.5) & (F["nwork"] >= 2)).values & win
        regional_spans = []
        for a_, b_ in segments(reg_hi, reg_lo, p, p.min_signal_s):
            a, b = self._span(a_, b_)
            affected = [tr for tr in TRACTORS if _w(F["cn0_dev_s"][tr], a, b).mean() < -p.cn0_drop_db * 0.5
                        and _w(work[tr], a, b).mean() > 0.5]
            if not affected:
                continue
            cands.append(dict(cls="REGIONAL", tractors=affected, start=a, end=b, channel="R"))
            regional_spans.append((a_, b_))
            cands += self._jam_in_span(a_, b_, win)

        # --- L: local residual ------------------------------------------------
        loc_hi = {tr: ((resid[tr] < -p.cn0_drop_db) & work[tr]).values & win for tr in TRACTORS}
        loc_lo = {tr: ((resid[tr] < -p.cn0_drop_db * 0.5) & work[tr]).values & win for tr in TRACTORS}
        for tr in TRACTORS:
            for a_, b_ in segments(loc_hi[tr], loc_lo[tr], p, p.mp_min_s, gap_s=min(p.gap_bridge_s, 30)):
                dur = (b_ - a_) * p.bin_s
                a, b = self._span(a_, b_)
                if dur < p.min_signal_s:
                    continue            # brief mean-C/N0 dips are not episodes; multipath is found at 1 Hz in _mp_events
                overlaps_link = e_mask[tr][a_:b_].mean() > 0.5
                if overlaps_link:
                    continue        # already covered by the link-outage candidate
                cands.append(dict(cls="LOCAL", tractors=[tr], start=a, end=b, channel="L"))

        # --- D: intermittent antenna dropouts (machine-specific, mean C/N0 barely moves) ------
        for tr in TRACTORS:
            d_hi = (F["dropout_local"][tr].values & ~loc_hi[tr] & ~e_mask[tr] & win)
            for a_, b_ in segments(d_hi, d_hi, p, p.equip_min_s, hysteresis=False, gap_s=90):
                if d_hi[a_:b_].mean() < p.dropout_density:
                    continue            # sparse flags are noise, not a dropout episode
                a, b = self._span(a_, b_)
                cands.append(dict(cls="EQUIPMENT_FAULT", tractors=[tr], start=a, end=b, channel="D", mode="antenna"))

        # --- X: spoofing (XTE excess over neighbours without signal collapse) --------------
        for tr in TRACTORS:
            thr = self._xte_thr(tr)
            ok = (resid[tr] > -p.xte_cn0_ok_db).values
            ex = F["xte_excess"][tr].values
            w_ = work[tr].values & ok & ~e_mask[tr] & win
            x_hi, x_lo = (ex > thr) & w_, (ex > thr * 0.35) & w_
            for a_, b_ in segments(x_hi, x_lo, p, p.xte_min_s, gap_s=60):
                a_ = self._ramp_onset(ex, a_, b_, thr, F.base[tr]["xte_mad"])
                a, b = self._span(a_, b_)
                cands.append(dict(cls="SPOOFING", tractors=[tr], start=a, end=b, channel="X", xte_thr=thr))

        # --- XC: common-mode spoofing (the neighbour-median X channel is blind when >= 2 machines are pulled together) --
        sm = max(1, int(p.smooth_s / p.bin_s)) | 1
        flag = {}
        for tr in TRACTORS:
            xs = F["xte"][tr].rolling(sm, center=True, min_periods=1).median().values
            healthy = (F["cn0_dev_s"][tr] > -p.xte_cn0_ok_db).values          # absolute health, NOT regional-relative: storms excluded
            flag[tr] = (xs > self._xte_thr(tr)) & work[tr].values & healthy & ~e_mask[tr] & win
        quiet = (reg > -p.regional_drop_db * 0.5).values
        cm = (sum(flag[tr].astype(int) for tr in TRACTORS) >= p.cm_min_machines) & quiet
        for a_, b_ in segments(cm, cm, p, p.xte_min_s, hysteresis=False, gap_s=60):
            aff = [tr for tr in TRACTORS if flag[tr][a_:b_].mean() > 0.5]
            if len(aff) >= p.cm_min_machines:
                a, b = self._span(a_, b_)
                cands.append(dict(cls="SPOOFING", tractors=aff, start=a, end=b, channel="XC", mode="common"))

        # --- attribution ------------------------------------------------------
        out = []
        for c in cands:
            if c["cls"] == "REGIONAL":
                out.append(self._attr_regional(c))
            elif c["cls"] == "LOCAL":
                out.append(self._attr_local(c))
            elif c["cls"] == "SPOOFING":
                out.append(self._attr_spoof(c))
            else:
                out.append(self._attr_equipment(c))
        out = self._drop_spoof_inside_jam(out)
        out = self._absorb_into_equipment(out)
        out = self._absorb_into_jam(out)
        out += self._multipath(out)
        out = self._merge(out)
        for c in out:
            c["start"] = c["start"] + pd.Timedelta(seconds=self.p.onset_shift_s)
            c["start"] = max(c["start"], self.window[0])
            if c["end"] <= c["start"]:
                c["end"] = c["start"] + pd.Timedelta(seconds=self.p.bin_s)
            if c["cls"] != "MULTIPATH":
                self._measure(c)                    # windows/tractors changed by merge/onset shift: re-measure everything
        from .analysis import Analysis               # independent cross-checks; they annotate, they never re-classify
        try:
            Analysis(self).annotate(out)
        except Exception as ex:                      # a cross-check must never cost a detection: report and carry on without it
            print(f"WARNING: cross-check analyses skipped ({type(ex).__name__}: {ex})")
            for c in out:
                c.setdefault("ax", {})
        return sorted(out, key=lambda c: c["start"])

    # ------------------------------------------------------------ attribution
    def _common(self, c):
        self._measure(c)
        c["notes"] = []
        return c

    def _measure(self, c):
        """Every measured quantity for the candidate's CURRENT window and tractor list. Called again after merging and
        the onset shift so the evidence always describes the window it states (never changes cls/scores/mode)."""
        a, b = c["start"], c["end"]
        c["stats"] = {tr: self._stats(tr, a, b) for tr in c["tractors"]}
        c["neighbors"] = {tr: self._stats(tr, a, b) for tr in TRACTORS if tr not in c["tractors"]
                          if _w(self.F["work"][tr], a, b).mean() > 0.5}
        lat = np.nanmean([s["lat"] for s in c["stats"].values()])
        lon = np.nanmean([s["lon"] for s in c["stats"].values()])
        c["centroid"] = (lat, lon)
        c["s4"] = self.sda.s4(a, b)
        c["kp_max"], c["kp_ts"] = self.sda.kp_max(a, b, lead_h=self.p.kp_lead_h)
        c["kp_start"] = self.sda.kp_at(a)
        c["dst_min"] = self.sda.dst_min(a, b, lead_h=self.p.dst_lead_h)
        c["advisories"] = self.sda.advisories(a, b)
        c["rfi"] = self.sda.rfi_matches(a, b, lat, lon)
        if "spread_db" in c:
            c["spread_db"] = self._spread(c)
        c["stat"] = self._window_stats(c)
        return c

    @staticmethod
    def _spread(c):
        devs = [s["cn0_dev_mean"] for s in c["stats"].values() if np.isfinite(s["cn0_dev_mean"])]
        return float(max(devs) - min(devs)) if len(devs) >= 2 else None

    def _window_stats(self, c):
        """ME 457 evidence: t-CI of each machine's mean C/N0 deviation, and Pearson r (vs r_crit) between machines'
        C/N0 deviations and between C/N0 and XTE. Block means (corr_block_s) keep n honest despite autocorrelation."""
        F, p = self.F, self.p
        a, b = c["start"], c["end"]
        k = max(1, int(p.corr_block_s / p.bin_s))
        blk = {}
        for tr in TRACTORS:
            w = _w(F["work"][tr], a, b).values
            blk[tr] = {}
            for name in ("cn0_dev", "xte"):
                v = _w(F[name][tr], a, b).values.astype(float)
                blk[tr][name] = block_means(np.where(w, v, np.nan), k)
        out = {"ci": {}, "pair": {}, "xte_r": {}, "drift": {}, "fleet": {}, "lag": None}
        for tr in c["tractors"]:
            ex = block_means_aligned(_w(F["xte_excess"][tr], a, b).values, k)
            out["drift"][tr] = linfit(np.arange(len(ex)) * p.corr_block_s / 60.0, ex, p.corr_alpha)   # m per min
            oth = [o for o in TRACTORS if o != tr]
            ref = block_means_aligned(_w(F["cn0_dev"][oth], a, b).median(axis=1).values, k)
            me = block_means_aligned(_w(F["cn0_dev"][tr], a, b).values, k)
            out["fleet"][tr] = linfit(ref, me, p.corr_alpha)       # slope ~1: machine tracks the fleet; ~0: independent
            out["ci"][tr] = mean_ci(blk[tr]["cn0_dev"], 1 - p.corr_alpha)
            m = min(len(blk[tr]["cn0_dev"]), len(blk[tr]["xte"]))
            out["xte_r"][tr] = correlate(blk[tr]["cn0_dev"][:m], blk[tr]["xte"][:m], p.corr_alpha)
        for i, t1 in enumerate(TRACTORS):
            for t2 in TRACTORS[i + 1:]:
                # block_means drops all-nan blocks, so align on the raw block grid instead
                x = block_means_aligned(_w(F["cn0_dev"][t1], a, b).where(_w(F["work"][t1], a, b)).values, k)
                y = block_means_aligned(_w(F["cn0_dev"][t2], a, b).where(_w(F["work"][t2], a, b)).values, k)
                out["pair"][(t1, t2)] = correlate(x, y, p.corr_alpha)
        if len(c["tractors"]) >= 2:
            out["lag"] = self._s4_lag(c)
        return out

    def _s4_lag(self, c):
        """Lagged regression of the farm-wide C/N0 deviation on S4 (15 min cadence) over event +/- 3 h. Positive lag
        means the farm lags S4. Returns the best lag by |r| with its r_crit, or None if there is too little data."""
        p, s4 = self.p, self.b.s4
        if s4 is None or not len(s4):
            return None
        a, b = c["start"] - pd.Timedelta(hours=3), c["end"] + pd.Timedelta(hours=3)
        step = f"{p.lag_step_min}min"
        farm = self.F["cn0_dev_s"][c["tractors"]].median(axis=1).loc[a:b].resample(step).mean()
        s4s = s4.set_index("ts")["s4"].resample(step).mean()
        best = None
        lags = range(-int(p.s4_lag_min / p.lag_step_min), int(p.s4_lag_min / p.lag_step_min) + 1)
        for lag in lags:
            x = s4s.shift(lag).reindex(farm.index)            # farm(t) vs s4(t - lag)
            r = correlate(x.values, farm.values, p.corr_alpha / len(lags))   # Bonferroni: the best of len(lags) is reported
            if np.isfinite(r["r"]) and (best is None or abs(r["r"]) > abs(best["r"])):
                f = linfit(x.values, farm.values, p.corr_alpha)
                best = dict(r, lag_min=lag * p.lag_step_min, slope=f["slope"], r2=f["r2"], n_lags=len(lags))
        return best

    def _attr_regional(self, c):
        self._common(c)
        el = c["s4"]["elevated"]                 # True / False / None (no S4 sample: neutral, not "quiet")
        kp_high = c["kp_max"] >= self.p.kp_high
        sw = (2.0 if el else 0.0) + (1.0 if kp_high else 0.0) + (0.5 if c["dst_min"] <= self.p.dst_low else 0.0)
        if any(str(ad.get("type", "")).upper() == "SPACE_WEATHER" for ad in c["advisories"]):
            sw += 0.5                            # a storm advisory is in force
        rfi_w = max((r["_weight"] for r in c["rfi"] if r["_within"]), default=0.0)
        jam = 1.5 * rfi_w + (1.0 if el is False else 0.0)
        # spatial uniformity (cf. crowdsourced jammer localisation): a regional ionospheric event hits every machine alike,
        # a local emitter does not. One-sided: heterogeneity argues for jamming, uniformity stays neutral (a far jammer is uniform too).
        c["spread_db"] = self._spread(c)
        if c["spread_db"] is not None and c["spread_db"] >= self.p.uniform_spread_db:
            jam += 1.0
        elif c["spread_db"] is not None and (c["end"] - c["start"]).total_seconds() >= self.p.storm_min_s:
            sw += 0.5                            # uniform AND long: every jam in the data lasts <= 42 min
        # exact ties go to jamming (the safer action) unless Kp itself is at storm level
        c["cls"] = "SPACE_WEATHER" if (sw > jam or (sw == jam and kp_high)) else "JAMMING"
        c["scores"] = {"space_weather": sw, "jamming": jam}
        if c.get("forced"):
            c["cls"] = c["forced"]
            c["scores"] = {"space_weather": sw, "jamming": max(jam, sw + 1.0)}
            c["notes"].append("jammer on top of a storm: abrupt extra regional C/N0 step")
        return c

    def _attr_local(self, c):
        self._common(c)
        (tr,), s = c["tractors"], None
        s = c["stats"][tr]
        rfi_w = max((r["_weight"] for r in c["rfi"] if r["_within"]), default=0.0)
        neighbor_dip = [n for n, ns in c["neighbors"].items() if ns["cn0_dev_mean"] < -self.p.cn0_drop_db * 0.4]
        deep = s["resid_mean"] < -self.p.cn0_drop_db * 1.5
        frac = max(s["sats_dropout_frac"], s["mincn0_dip_frac"])
        bursty = 0.1 < frac < 0.85          # intermittent dropouts; jamming is sustained (frac ~ 1)
        jam = 1.5 * rfi_w + (1.0 if neighbor_dip else 0) + (1.0 if deep else 0) \
            + (0.5 if s["fixbad_frac"] > 0.8 else 0) + (0.5 if not bursty else 0)
        eq = (1.5 if bursty else 0) + (1.0 if not neighbor_dip else 0) + (0.5 if not deep else 0)
        c["scores"] = {"jamming": jam, "equipment": eq}
        c["cls"] = "JAMMING" if jam > eq else "EQUIPMENT_FAULT"
        c["mode"] = "antenna" if c["cls"] == "EQUIPMENT_FAULT" else None
        return c

    def _attr_equipment(self, c):
        self._common(c)
        c["scores"] = {"equipment": 3.0}
        return c

    def _attr_spoof(self, c):
        self._common(c)
        s = c["stats"][c["tractors"][0]]
        c["scores"] = {"spoofing": 2.0 + (1.0 if abs(s["cn0_dev_mean"]) < 2 else 0)}
        # onset: first bin that rises above the noise floor along the ramp
        return c

    def _absorb_into_equipment(self, cands):
        """A local C/N0 candidate mostly inside an equipment episode on the SAME machine is the same fault
        (antenna trouble also depresses mean C/N0), unless an RFI report independently supports jamming."""
        eq = [c for c in cands if c["cls"] == "EQUIPMENT_FAULT"]
        keep = []
        for c in cands:
            if c["channel"] == "L" and c["cls"] == "JAMMING" and not any(r["_within"] for r in c["rfi"]):
                dur = (c["end"] - c["start"]).total_seconds()
                inside = max((min(c["end"], e["end"]) - max(c["start"], e["start"])).total_seconds()
                             for e in eq if set(e["tractors"]) & set(c["tractors"])) if any(
                    set(e["tractors"]) & set(c["tractors"]) for e in eq) else 0
                if dur and inside / dur >= 0.5:
                    continue
            keep.append(c)
        return keep

    # ------------------------------------------------------------ jam on top of a storm
    def _jam_in_span(self, a_, b_, win):
        """A regional segment (storm) whose smoothed level has an ABRUPT extra drop of >= jam_step_db that lasts
        >= min_signal_s is a jammer on top of the storm. Abrupt = the level just before and just after is shallower
        by the step (so a smooth ramp or a plateau never qualifies). Returns REGIONAL candidates forced to JAMMING."""
        p, F = self.p, self.F
        reg = F["regional"].values
        seg = reg[a_:b_]
        if len(seg) < 2 * int(p.min_signal_s / p.bin_s):
            return []
        lvl = float(np.nanmedian(seg))
        hi = np.zeros(len(reg), bool); lo = np.zeros(len(reg), bool)
        hi[a_:b_] = seg < lvl - p.jam_step_db
        lo[a_:b_] = seg < lvl - 0.5 * p.jam_step_db
        ctx = int(1200 / p.bin_s)
        out = []
        for sa, sb in segments(hi & win, lo & win, p, p.min_signal_s):
            inner = float(np.nanmedian(reg[sa:sb]))
            before = reg[max(a_, sa - ctx):sa]
            after = reg[sb:min(b_, sb + ctx)]
            if len(before) < 6 or (len(after) < 6 and sb < b_ - 6):
                continue
            if np.nanmedian(before) - inner < p.jam_step_db:
                continue
            if len(after) >= 6 and np.nanmedian(after) - inner < 0.5 * p.jam_step_db:
                continue
            a, b = self._span(sa, sb)
            aff = [tr for tr in TRACTORS if _w(F["work"][tr], a, b).mean() > 0.5
                   and _w(F["cn0_dev_s"][tr], a, b).mean() < lvl - 0.5 * p.jam_step_db]
            if aff:
                out.append(dict(cls="REGIONAL", tractors=aff, start=a, end=b, channel="R", forced="JAMMING"))
        return out

    def _absorb_into_jam(self, cands):
        """A per-machine equipment candidate (antenna/local-residual; NOT an explicit link-down flag) that sits >= 50% inside
        a jamming episode that includes the same machine, and whose C/N0 deviation simply tracks the fleet median
        (regression slope >= 0.6 with r2 >= jam_track_r2), is the same cause seen from one machine, not a second fault.
        A real fault on top of a jam would show extra, fleet-independent loss and so is kept."""
        jams = [c for c in cands if c["cls"] == "JAMMING"]
        keep = []
        for c in cands:
            if c["cls"] == "EQUIPMENT_FAULT" and c.get("mode") != "link":
                tr = c["tractors"][0]
                dur = (c["end"] - c["start"]).total_seconds()
                cov = max((max(0.0, (min(c["end"], j["end"]) - max(c["start"], j["start"])).total_seconds())
                           for j in jams if tr in j["tractors"]), default=0.0)
                fl = (c.get("stat") or {}).get("fleet", {}).get(tr)
                if dur and cov / dur >= 0.5 and fl and np.isfinite(fl["r2"]) and fl["slope"] >= 0.6 and fl["r2"] >= self.p.jam_track_r2:
                    continue
                # a signal-loss candidate (local residual) that lies wholly inside a jam already listing this machine has no
                # evidence of its own outside the jam: machines near an emitter are hit unequally, so it is the same cause
                if c.get("channel") == "L" and dur and cov / dur >= 0.99:
                    continue
            keep.append(c)
        return keep

    def _drop_spoof_inside_jam(self, cands):
        """Cross-track error blows up when the signal is jammed; that is a consequence, not a spoofing signature."""
        jams = [c for c in cands if c["cls"] == "JAMMING"]
        keep = []
        for c in cands:
            if c["cls"] == "SPOOFING":
                dur = (c["end"] - c["start"]).total_seconds()
                cov = max((max(0.0, (min(c["end"], j["end"]) - max(c["start"], j["start"])).total_seconds())
                           for j in jams if set(j["tractors"]) & set(c["tractors"])), default=0.0)
                if dur and cov / dur >= 0.5:
                    continue
            keep.append(c)
        return keep

    # ------------------------------------------------------------ multipath
    def _mp_events(self, existing=()):
        """Brief min-C/N0 dips at 1 Hz: the machine's min C/N0 falls mp_dip_db..mp_dip_max_db below its own rolling
        median while the satellite count holds. Mean C/N0 barely moves, so the 10 s mean-based channels cannot see it.
        Then keep only dips in the modal latitude band (the headland obstruction), counted over all 72 h."""
        if getattr(self, "_mp_cache", None) is not None:
            return self._mp_cache
        p = self.p
        raw = []
        for tr, g in self.b.tele.groupby("tractor_id"):
            g = g.sort_values("ts").reset_index(drop=True)
            work = (g["speed_mps"] > p.work_speed).values
            k = int(p.mp_ctx_s)
            mc = g["min_cn0_dbhz"].where(work)
            sat = g["num_sats"].astype(float).where(work)
            mmed = mc.rolling(k, center=True, min_periods=k // 6).median()
            # samples OUTSIDE a jam / antenna-fault window of this machine take their baseline from other outside samples
            # only, so the first pass after an episode ends is not judged against a baseline dragged down by it
            epi = np.zeros(len(g), bool)
            for e in existing:
                if tr in e["tractors"] and e["cls"] in ("JAMMING", "EQUIPMENT_FAULT") and e.get("mode") != "link":
                    epi |= ((g["ts"] >= e["start"]) & (g["ts"] < e["end"])).values
            if epi.any():
                m_out = mc.where(~epi).rolling(k, center=True, min_periods=k // 6).median()
                mmed = mmed.where(epi, m_out.fillna(mmed))
            smed = sat.rolling(k, center=True, min_periods=k // 6).median()
            dev = (mc - mmed).values
            dip = work & (dev <= -p.mp_dip_db) & (sat.values >= smed.values - 0.6 * p.sats_drop)
            dip = _bridge(dip, 5)
            for a, b in _runs(dip):
                dur = b - a
                if not (p.mp_evt_min_s <= dur <= p.mp_max_s):
                    continue
                depth = float(np.nanmedian(dev[a:b]))
                if not np.isfinite(depth) or depth < -p.mp_dip_max_db:
                    continue
                lat, lon = float(g["lat"][a:b].mean()), float(g["lon"][a:b].mean())
                raw.append(dict(tr=tr, start=g["ts"][a], end=g["ts"][b - 1] + pd.Timedelta(seconds=1), lat=lat, lon=lon,
                                y=float(to_xy(lat, lon)[1]), depth=depth, min_cn0=float(mc[a:b].min()),
                                base_min_cn0=float(mmed[a:b].median()), sats_min=float(sat[a:b].min()),
                                sats_base=float(smed[a:b].median()), xte_max=float(g["cross_track_error_m"][a:b].abs().max())))
        self._mp_cache = []
        self._mp_raw = raw                           # every dip, before the location band is applied (analysis.periodicity)
        if len(raw) < p.mp_min_recur:
            return self._mp_cache
        ys = np.array([e["y"] for e in raw])
        bins = np.arange(ys.min() - 2.5, ys.max() + 5.0, 5.0)
        h, edges = np.histogram(ys, bins)
        y0 = float(np.median(ys[(ys >= edges[h.argmax()] - 2.5) & (ys <= edges[h.argmax() + 1] + 2.5)]))
        band = [e for e in raw if abs(e["y"] - y0) <= p.mp_band_m / 2]
        if len(band) >= p.mp_min_recur:
            self._mp_cache = sorted(band, key=lambda e: e["start"])
            self._mp_y0 = y0
        return self._mp_cache

    def _multipath(self, existing):
        """Recurring brief dips at one spot (headland obstruction) -> one single-tractor MULTIPATH report per pass.
        Single-tractor reports so that whichever machine/pass is the scored representative is covered; the rest are
        recurrences. Passes inside another episode are still reported when the dip is measurable (a dev representative
        sits inside a storm); the evidence states the overlap and the confidence is reduced."""
        p = self.p
        allb = self._mp_events(existing)
        out = []
        if not allb:
            return out
        per_tr = {tr: [e for e in allb if e["tr"] == tr] for tr in TRACTORS}
        period = {}
        for tr, es in per_tr.items():
            d = np.diff([e["start"].timestamp() for e in es])
            d = d[(d > 120) & (d < 1200)]
            period[tr] = float(np.median(d)) if len(d) else float("nan")
        spot = (float(np.mean([e["lat"] for e in allb])), float(np.mean([e["lon"] for e in allb])))
        n_win = sum(self.window[0] <= e["start"] < self.window[1] for e in allb)
        for m in allb:
            if not (self.window[0] <= m["start"] < self.window[1]):
                continue
            peers = sorted({q["tr"] for q in allb if q["tr"] != m["tr"]
                            and abs((q["start"] - m["start"]).total_seconds()) <= 45})
            c = dict(cls="MULTIPATH", tractors=[m["tr"]], start=m["start"], end=m["end"], channel="M",
                     stats={m["tr"]: {}}, neighbors={}, notes=[], scores={"multipath": 1.0 + min(len(allb), 6) * 0.3},
                     mp=dict(n_total=len(allb), n_window=n_win, spot=spot, y0=self._mp_y0, period_s=period[m["tr"]],
                             peers=peers, **{k: m[k] for k in ("depth", "min_cn0", "base_min_cn0", "sats_min", "sats_base", "xte_max")}))
            out.append(c)
        return out

    # ------------------------------------------------------------ merge
    def _merge(self, cands):
        p = self.p
        keep = [c for c in cands if c["cls"] == "MULTIPATH"]
        rest = sorted([c for c in cands if c["cls"] != "MULTIPATH"], key=lambda c: c["start"])
        merged = []
        for c in rest:
            hit = None
            for m in merged:
                if m["cls"] != c["cls"]:
                    continue
                gap = (c["start"] - m["end"]).total_seconds()
                ov = (min(m["end"], c["end"]) - max(m["start"], c["start"])).total_seconds()
                same_t = set(m["tractors"]) & set(c["tractors"])
                shorter = min((m["end"] - m["start"]).total_seconds(), (c["end"] - c["start"]).total_seconds())
                if same_t and gap <= p.merge_gap_s:
                    hit = m
                elif c["cls"] in ("JAMMING", "SPACE_WEATHER") and ov > 0.5 * shorter:
                    hit = m
                elif c["cls"] == "SPOOFING" and ov > 0.5 * shorter:
                    hit = m
                if hit:
                    break
            if hit:
                hit["start"], hit["end"] = min(hit["start"], c["start"]), max(hit["end"], c["end"])
                hit["tractors"] = sorted(set(hit["tractors"]) | set(c["tractors"]))
                hit["stats"].update(c["stats"])
                hit["neighbors"] = {k: v for k, v in hit["neighbors"].items() if k not in hit["tractors"]}
            else:
                merged.append(c)
        # annotate concurrency (shared operational event)
        allc = merged + keep
        for c in allc:
            c["concurrent"] = [o for o in merged if o is not c and o["cls"] != "MULTIPATH"
                               and min(o["end"], c["end"]) > max(o["start"], c["start"])]
        return allc
