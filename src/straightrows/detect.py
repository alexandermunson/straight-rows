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
            for a_, b_ in segments(bad, bad, p, p.equip_min_s, hysteresis=False):
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

        # --- L: local residual ------------------------------------------------
        loc_hi = {tr: ((resid[tr] < -p.cn0_drop_db) & work[tr]).values & win for tr in TRACTORS}
        loc_lo = {tr: ((resid[tr] < -p.cn0_drop_db * 0.5) & work[tr]).values & win for tr in TRACTORS}
        brief = []
        for tr in TRACTORS:
            for a_, b_ in segments(loc_hi[tr], loc_lo[tr], p, p.mp_min_s, gap_s=min(p.gap_bridge_s, 30)):
                dur = (b_ - a_) * p.bin_s
                a, b = self._span(a_, b_)
                if dur < p.min_signal_s:
                    brief.append(dict(tr=tr, start=a, end=b))
                    continue
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
        out = self._absorb_into_equipment(out)
        out += self._multipath(brief, out)
        out = self._merge(out)
        for c in out:
            c["start"] = c["start"] + pd.Timedelta(seconds=self.p.onset_shift_s)
            c["start"] = max(c["start"], self.window[0])
            if c["end"] <= c["start"]:
                c["end"] = c["start"] + pd.Timedelta(seconds=self.p.bin_s)
        return sorted(out, key=lambda c: c["start"])

    # ------------------------------------------------------------ attribution
    def _common(self, c):
        a, b = c["start"], c["end"]
        c["stats"] = {tr: self._stats(tr, a, b) for tr in c["tractors"]}
        c["neighbors"] = {tr: self._stats(tr, a, b) for tr in TRACTORS if tr not in c["tractors"]
                          if _w(self.F["work"][tr], a, b).mean() > 0.5}
        lat = np.nanmean([s["lat"] for s in c["stats"].values()])
        lon = np.nanmean([s["lon"] for s in c["stats"].values()])
        c["centroid"] = (lat, lon)
        c["s4"] = self.sda.s4(a, b)
        c["kp_max"], c["kp_ts"] = self.sda.kp_max(a, b)
        c["kp_start"] = self.sda.kp_at(a)
        c["dst_min"] = self.sda.dst_min(a, b)
        c["advisories"] = self.sda.advisories(a, b)
        c["rfi"] = self.sda.rfi_matches(a, b, lat, lon)
        c["notes"] = []
        return c

    def _attr_regional(self, c):
        self._common(c)
        sw = (2.0 if c["s4"]["elevated"] else 0.0) + (1.0 if c["kp_max"] >= self.p.kp_high else 0.0) \
            + (0.5 if c["dst_min"] <= self.p.dst_low else 0.0)
        rfi_hit = any(r["_within"] for r in c["rfi"])
        jam = (1.5 if rfi_hit else 0.0) + (1.0 if not c["s4"]["elevated"] else 0.0)
        c["cls"] = "SPACE_WEATHER" if sw > jam else "JAMMING"
        c["scores"] = {"space_weather": sw, "jamming": jam}
        return c

    def _attr_local(self, c):
        self._common(c)
        (tr,), s = c["tractors"], None
        s = c["stats"][tr]
        rfi_hit = any(r["_within"] for r in c["rfi"])
        neighbor_dip = [n for n, ns in c["neighbors"].items() if ns["cn0_dev_mean"] < -self.p.cn0_drop_db * 0.4]
        deep = s["resid_mean"] < -self.p.cn0_drop_db * 1.5
        frac = max(s["sats_dropout_frac"], s["mincn0_dip_frac"])
        bursty = 0.1 < frac < 0.85          # intermittent dropouts; jamming is sustained (frac ~ 1)
        jam = (1.5 if rfi_hit else 0) + (1.0 if neighbor_dip else 0) + (1.0 if deep else 0) \
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

    # ------------------------------------------------------------ multipath
    def _multipath(self, brief, existing):
        """Brief dips recurring at the same place (headland obstruction). Uses all 72 h, so dev-labeled
        occurrences establish the location before the evaluation window."""
        p = self.p
        # recompute brief events over the full 72 h for location clustering
        allb = []
        F = self.F
        for tr in TRACTORS:
            hi = ((F["resid"][tr] < -p.cn0_drop_db) & F["work"][tr]).values
            lo = ((F["resid"][tr] < -p.cn0_drop_db * 0.5) & F["work"][tr]).values
            for a_, b_ in segments(hi, lo, p, p.mp_min_s, gap_s=min(p.gap_bridge_s, 30)):
                if (b_ - a_) * p.bin_s < p.min_signal_s:
                    a = self.idx[a_]; b = self.idx[b_ - 1] + pd.Timedelta(seconds=p.bin_s)
                    st = _w(F["lat"][tr], a, b).mean(), _w(F["lon"][tr], a, b).mean()
                    x, y = to_xy(*st)
                    allb.append(dict(tr=tr, start=a, end=b, x=float(x), y=float(y)))
        out = []
        if not allb:
            return out
        xy = np.array([[e["x"], e["y"]] for e in allb])
        used = np.zeros(len(allb), bool)
        cid = np.full(len(allb), -1)
        k = 0
        for i in range(len(allb)):
            if used[i]:
                continue
            d = np.hypot(*(xy - xy[i]).T)
            members = np.where((d < p.mp_cell_m) & ~used)[0]
            used[members] = True
            cid[members] = k
            k += 1
        for k_ in range(k):
            members = [allb[i] for i in np.where(cid == k_)[0]]
            if len(members) < p.mp_min_recur:
                continue
            trs = sorted({m["tr"] for m in members})
            cx, cy = np.mean([[m["x"], m["y"]] for m in members], axis=0)
            for m in members:
                if not (self.window[0] <= m["start"] < self.window[1]):
                    continue
                if any(e["start"] <= m["start"] < e["end"] and m["tr"] in e["tractors"] for e in existing
                       if e["cls"] in ("SPACE_WEATHER",)):
                    pass   # keep: multipath can recur inside a storm, evidence will note the overlap
                a, b = m["start"], min(m["end"], m["start"] + pd.Timedelta(seconds=p.mp_max_s))
                c = dict(cls="MULTIPATH", tractors=[m["tr"]], start=a, end=b, channel="M",
                         mp=dict(n_total=len(members), spot_xy=(float(cx), float(cy)), n_window=sum(
                             self.window[0] <= q["start"] < self.window[1] for q in members),
                             times=[q["start"] for q in members], all_tractors=trs))
                self._common(c)
                c["scores"] = {"multipath": 1.0 + min(len(members), 6) * 0.3}
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
