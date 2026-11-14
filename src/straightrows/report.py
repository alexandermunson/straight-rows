"""Turn candidates into submission detections: action, calibrated-ish confidence, checkable evidence."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ACTION, Params

fmt = lambda t: pd.Timestamp(t).strftime("%Y-%m-%d %H:%MZ")
fmts = lambda t: pd.Timestamp(t).strftime("%H:%M:%SZ")


def confidence(c, p: Params):
    """Additive, explainable score. Every term is reported in the evidence so a judge can audit it."""
    cls, terms = c["cls"], []
    base = {"SPACE_WEATHER": 0.55, "JAMMING": 0.5, "SPOOFING": 0.55, "EQUIPMENT_FAULT": 0.55, "MULTIPATH": 0.5}[cls]
    dur = (c["end"] - c["start"]).total_seconds()
    st = c["stats"][c["tractors"][0]]
    worst = min((s["cn0_dev_mean"] for s in c["stats"].values() if s and np.isfinite(s.get("cn0_dev_mean", np.nan))), default=np.nan)
    if cls == "SPACE_WEATHER":
        if c["s4"]["elevated"]:
            terms.append(("S4 elevated", +0.2))
        elif c["s4"]["elevated"] is None:
            terms.append(("S4 unavailable for this window", -0.1))
        else:
            terms.append(("S4 not elevated", -0.2))
        if c["kp_max"] >= p.kp_high:
            terms.append(("Kp >= %.0f" % p.kp_high, +0.1))
        if len(c["tractors"]) >= 3:
            terms.append(("all machines affected", +0.05))
    elif cls == "JAMMING":
        rfi_w = max((r["_weight"] for r in c["rfi"] if r["_within"]), default=0.0)
        if rfi_w >= 1.0:
            terms.append(("RFI report corroborates", +0.2))
        elif rfi_w > 0:
            terms.append(("low-confidence RFI report", +0.1))
        else:
            terms.append(("no RFI report (reports can be missing)", -0.05))
        if c["s4"]["elevated"]:
            terms.append(("S4 elevated: scintillation is an alternative", -0.15))
        elif c["s4"]["elevated"] is False:
            terms.append(("S4 quiet", +0.1))
        if worst < -8:
            terms.append(("deep C/N0 loss on the worst machine", +0.1))
        if c.get("spread_db") is not None and c["spread_db"] >= p.uniform_spread_db:
            terms.append((f"loss differs between machines ({c['spread_db']:.1f} dB): not a uniform ionospheric pattern", +0.10))
    elif cls == "SPOOFING":
        if abs(st["cn0_dev_mean"]) < 2:
            terms.append(("signal health intact", +0.15))
        if st["xte_max"] > 2.0:
            terms.append(("large XTE", +0.1))
        dr = (c.get("stat") or {}).get("drift", {}).get(c["tractors"][0])
        if dr and dr["slope_signif"] and dr["slope"] > 0:
            terms.append(("XTE drift slope CI excludes zero", +0.05))
        if c["s4"]["elevated"] or c["kp_max"] >= p.kp_high:
            terms.append(("storm active: XTE could be ionospheric", -0.1))
        if c.get("channel") == "XC":
            terms.append(("several machines at once: no healthy neighbour to compare against", -0.1))
    elif cls == "EQUIPMENT_FAULT":
        if c.get("mode") == "link":
            terms.append(("explicit link-down flag", +0.25))
        if c["neighbors"] and all(n["cn0_dev_mean"] > -2 for n in c["neighbors"].values()):
            terms.append(("neighbors healthy", +0.1))
    elif cls == "MULTIPATH":
        terms.append((f"recurs {c['mp']['n_total']}x at one spot", min(0.3, 0.07 * c["mp"]["n_total"])))
    if cls != "MULTIPATH" and dur >= 600:
        terms.append(("lasted >= 10 min", +0.05))
    if cls != "MULTIPATH" and dur < 300:
        terms.append(("short event", -0.1))
    others = [o for o in c.get("concurrent", []) if o["cls"] != cls]
    if others:
        terms.append(("overlaps another cause", -0.05))
    v = float(np.clip(base + sum(d for _, d in terms), p.conf_floor, p.conf_cap))
    return round(v, 2), [(f"base {cls} {base:.2f}", base)] + terms


def stat_evidence(c, p: Params):
    """Checkable ME 457 numbers: t-CI of the mean C/N0 deviation, Pearson r vs its significance threshold."""
    st, out = c.get("stat"), []
    if not st:
        return out
    conf = int(round(100 * (1 - p.corr_alpha)))
    cis = []
    for t, ci in st["ci"].items():
        if ci["n"] >= 2 and np.isfinite(ci["half"]):
            cis.append(f"{t} {ci['mean']:+.1f} dB ({conf}% CI {ci['lo']:+.1f} to {ci['hi']:+.1f}, n={ci['n']} {p.corr_block_s} s blocks)")
    if cis:
        out.append("C/N0 deviation from own baseline, t interval on block means: " + "; ".join(cis) + ".")
    pairs = [f"{a}-{b} r={v['r']:+.2f} (n={v['n']}, r_crit {v['r_crit']:.2f}, {'significant' if v['significant'] else 'not significant'})"
             for (a, b), v in st["pair"].items() if np.isfinite(v["r"])]
    if pairs:
        out.append("Cross-machine correlation of C/N0 deviation over the window: " + "; ".join(pairs) + ".")
    xr = [f"{t} r={v['r']:+.2f} (n={v['n']}, r_crit {v['r_crit']:.2f})" for t, v in st["xte_r"].items() if np.isfinite(v["r"])]
    if xr:
        out.append("C/N0 deviation vs |XTE| within the window: " + "; ".join(xr) + ".")
    dr = [f"{t} {v['slope']:+.3f} m/min (slope CI half-width {v['slope_half']:.3f}, r2={v['r2']:.2f}, S_yx {v['syx']:.2f} m, n={v['n']})"
          for t, v in st["drift"].items() if np.isfinite(v["slope"]) and c["cls"] == "SPOOFING"]
    if dr:
        out.append("XTE excess regressed on time: " + "; ".join(dr) + ("; slope CI excludes zero, a steady drift"
                   if all(v["slope_signif"] for v in st["drift"].values() if np.isfinite(v["slope"])) else "; slope not distinguishable from zero") + ".")
    fl = [f"{t} slope {v['slope']:+.2f} on fleet median (r2={v['r2']:.2f})" for t, v in st["fleet"].items()
          if np.isfinite(v["slope"]) and c["cls"] in ("SPACE_WEATHER", "JAMMING", "EQUIPMENT_FAULT")]
    if fl:
        out.append("C/N0 deviation regressed on the other machines' median: " + "; ".join(fl) + ".")
    lg = st.get("lag")
    if lg and c["cls"] == "SPACE_WEATHER":      # storms only: for a jam the +/-3 h window mostly measures neighbouring storms
        out.append(f"Lagged regression of farm C/N0 deviation on S4 (+/-3 h): best |r|={abs(lg['r']):.2f} at lag {lg['lag_min']:+d} min "
                   f"(n={lg['n']}, r_crit {lg['r_crit']:.2f} after Bonferroni correction for the {lg['n_lags']} lags tried, "
                   f"{'significant' if lg['significant'] else 'not significant'}, slope {lg['slope']:+.1f} dB per unit S4).")
    return out


def evidence_multipath(c, p: Params, conf_terms):
    """Compact, checkable evidence: ~470 recurrences a day get one short report each."""
    m, (t,) = c["mp"], c["tractors"]
    a, b = c["start"], c["end"]
    ev = [f"{t}: {(b - a).total_seconds():.0f} s dip {fmts(a)}-{fmts(b)}: min C/N0 {m['min_cn0']:.1f} dB-Hz vs its own 15-min median "
          f"{m['base_min_cn0']:.1f} ({m['min_cn0'] - m['base_min_cn0']:+.1f} dB at the lowest point, median {m['depth']:+.1f} dB over the dip); satellites held (min {m['sats_min']:.0f} vs {m['sats_base']:.0f}); "
          f"max |XTE| {m['xte_max']:.2f} m, so guidance stayed on line."]
    per = f"a pass about every {m['period_s'] / 60:.1f} min" if np.isfinite(m["period_s"]) else "recurring passes"
    ev.append(f"Location: {abs(m['spot'][0]):.5f}{'N' if m['spot'][0] >= 0 else 'S'} {abs(m['spot'][1]):.4f}{'E' if m['spot'][1] >= 0 else 'W'} headland, "
              f"one north-south band ({p.mp_band_m:.0f} m) shared by every machine; "
              f"this dip was detected {m['n_total']}x over the 72 h ({m['n_window']} of them in the reported window), {per}.")
    ev.append("Peers: " + (f"{', '.join(m['peers'])} show the same dip within 45 s of this pass (place-specific, not machine-specific)."
                           if m["peers"] else "no other machine shows this dip at this pass."))
    others = [o for o in c.get("concurrent", []) if o["cls"] != "MULTIPATH"]
    if others:
        ev.append("Attribution: brief dip in min C/N0 at the fixed headland spot where it recurs on every pass: a reflection from a headland "
                  "obstruction, superimposed on the concurrent event below. The dip is measured against the machine's own level at that "
                  "time and lasts seconds, so it is a separate, benign cause; the concurrent event keeps its own action.")
    else:
        ev.append("Attribution: brief, shallow dip in min C/N0 only, recurring at a fixed spot with no regional, link or satellite-count change: "
                  "a reflection from a headland obstruction. Ruled out: jamming/space weather (mean C/N0 unchanged, dips last seconds), "
                  "equipment fault (satellite count holds, dip is not deeper than %.0f dB), spoofing (XTE normal)." % p.mp_dip_max_db)
    if others:
        ev.append("Concurrent distinct cause(s): " + "; ".join(
            f"{o['cls']} on {','.join(o['tractors'])} {fmts(o['start'])}-{fmts(o['end'])}" for o in others)
            + ". The dip is reported separately because it recurs at the same spot on its own schedule.")
    ev.append("Confidence built from: " + "; ".join(f"{n} {d:+.2f}" if i else n for i, (n, d) in enumerate(conf_terms)) + ".")
    return ev


def operator_lines(c, p: Params, conf_terms):
    """Report-ready summary (time, place, machines, effect, cause, corroboration) in the shape of a GNSS interference problem
    report, plus who should be told. Jamming/spoofing are interference events worth reporting; storms, equipment faults and
    multipath are not. Evidence text only: it changes no classification."""
    cls, tr = c["cls"], c["tractors"]
    conf = float(np.clip(sum(d for _, d in conf_terms), p.conf_floor, p.conf_cap))
    if cls in ("JAMMING", "SPOOFING"):
        lat, lon = c["centroid"]
        st = c["stats"]
        if cls == "JAMMING":
            worst = min(tr, key=lambda t: st[t]["cn0_dev_mean"])
            effect = (f"mean C/N0 down {abs(min(s_['cn0_dev_mean'] for s_ in st.values())):.1f} dB on the worst machine ({worst}), "
                      f"satellites down to {min(s_['sats_min'] for s_ in st.values()):.0f}, fix degraded")
        else:
            effect = (f"cross-track error up to {max(s_['xte_max'] for s_ in st.values()):.1f} m while C/N0 and satellite count stay normal")
        hits = [r for r in c["rfi"] if r["_within"]]
        rfi = (f"matches RFI report {hits[0].get('id', '?')}" + (f" ({hits[0]['_dist_km']:.1f} km away)" if "_dist_km" in hits[0] else "")
               if hits else "no matching RFI report")
        return [f"Report-ready summary: {fmt(c['start'])} to {fmt(c['end'])} (onset {fmts(c['start'])}); location {lat:.6f}, {lon:.6f}; "
                f"affected receivers {', '.join(tr)}; effect: {effect}; suspected cause: {cls.lower()}; {rfi}; confidence {conf:.2f}.",
                "Notify: a GNSS interference report is appropriate (for example the USCG NAVCEN GPS problem report; aviation and "
                "emergency channels differ). Include this window, location and the affected machines, and send it even if the event has ended."]
    if cls == "SPACE_WEATHER":
        return ["Notify: no interference report needed (natural cause). Watch the space-weather advisories and plan around degraded RTK."]
    if cls == "EQUIPMENT_FAULT":
        return ["Notify: service the machine. This is not a GNSS interference report; GNSS authorities do not handle receiver or antenna faults."]
    return []


def evidence(c, p: Params, conf_terms):
    cls = c["cls"]
    if cls == "MULTIPATH":
        return evidence_multipath(c, p, conf_terms)
    tr = c["tractors"]
    a, b = c["start"], c["end"]
    ev = [f"Window {fmts(a)} to {fmts(b)} ({(b - a).total_seconds() / 60:.1f} min) on {','.join(tr)}."]
    for t in tr:
        s = c["stats"][t]
        ev.append(
            f"{t}: mean C/N0 {s['cn0_mean']:.1f} dB-Hz vs own baseline {s['cn0_base']:.1f} ({s['cn0_dev_mean']:+.1f} dB), "
            f"min C/N0 {s['mincn0_min']:.1f} (baseline {s['mincn0_base']:.1f}), sats min {s['sats_min']:.0f} "
            f"(baseline {s['sats_base']:.0f}), |XTE| mean {s['xte_mean']:.2f} m / max {s['xte_max']:.2f} m, "
            f"correction link up {100 * s['link_frac']:.0f}%, non-RTK-fixed {100 * s['fixbad_frac']:.0f}%.")
    if c["neighbors"]:
        ev.append("Neighbors over the same window: " + "; ".join(
            f"{n} C/N0 {s['cn0_dev_mean']:+.1f} dB, |XTE| max {s['xte_max']:.2f} m, link {100 * s['link_frac']:.0f}%"
            for n, s in c["neighbors"].items()) + ".")
    ev.extend(stat_evidence(c, p))
    s4 = c["s4"]
    s4txt = (f"S4 mean over the window {s4['mean']:.2f} (max within +/-{p.s4_lag_min} min {s4['max']:.2f}; quiet baseline {s4['base']:.2f})"
             if s4["elevated"] is not None else "S4 unavailable for this window")
    sda = (f"SDA: {s4txt}; Kp max {c['kp_max']:.1f} in the {p.kp_lead_h:g} h before through the end of the event"
           + (f" (bin {fmt(c['kp_ts'])})" if c["kp_ts"] is not None else "")
           + f", Kp at onset {c['kp_start']:.1f}; Dst min {c['dst_min']:.0f} nT in the {p.dst_lead_h:g} h before through the end of the event.")
    ev.append(sda)
    if cls in ("SPACE_WEATHER", "JAMMING") and c.get("spread_db") is not None:
        sp = c["spread_db"]
        ev.append(f"Spatial uniformity: mean C/N0 loss differs by {sp:.1f} dB across the affected machines "
                  + ("(a regional ionospheric event hits every machine alike; the two labeled development storms differ by <= 0.1 dB, "
                     "so this heterogeneity points to a local emitter)." if sp >= p.uniform_spread_db else
                     "(uniform, consistent with a regional cause; a distant jammer can also look uniform, so this is neutral evidence)."))
    if c["advisories"]:
        ev.append("Advisories in force: " + "; ".join(
            f"{a_.get('id', 'advisory')} issued {fmt(a_['_ts'])} ({a_['_age_h']:.0f} h before onset, valid {a_['_valid_h']:.0f} h): "
            + str(a_.get("text") or a_.get("type"))[:220] for a_ in c["advisories"]))
    if c["rfi"]:
        ev.append("RFI reports: " + "; ".join(
            f"{r.get('id', 'report')} at {fmt(r['_ts'])} ({r['_delay_min']:.0f} min after onset)"
            + (f", {r['_dist_km']:.1f} km from the machines" if "_dist_km" in r else "")
            + (" (within its location uncertainty)" if r["_within"] else " (too far)")
            + (" (low-confidence report, half weight)" if r["_weight"] < 1 else "") for r in c["rfi"])
            + ("" if cls == "JAMMING" else ". Listed for completeness: this attribution does not rely on it."))
    else:
        ev.append(f"No RFI report between {p.rfi_min_delay_min} min after onset and {p.rfi_window_min} min after the end; "
                  "reports are delayed 10-30 min and can be missing, so absence is weak evidence.")
    if cls == "SPACE_WEATHER":
        lead = (a - c["kp_ts"]).total_seconds() / 60 if c["kp_ts"] is not None else -1
        ev.append("Attribution: every working machine lost C/N0 together" + (" while S4 is elevated" if s4["elevated"] else "")
                  + (f" and Kp reached {c['kp_max']:.1f}" if c["kp_max"] >= p.kp_high else "")
                  + "; a regional ionospheric cause fits."
                  + (f" The Kp bin with that maximum starts {lead:.0f} min before the farm onset, so Kp led the farm impact."
                     if lead > 0 and c["kp_max"] >= p.kp_high else ""))
        ev.append("Ruled out: a single-machine fault (all machines affected); local jamming is not excluded and is reported "
                  "separately when a machine shows extra loss beyond the regional drop.")
    elif cls == "JAMMING":
        ev.append("Attribution: excess C/N0 loss beyond the regional level"
                  + (" with S4 quiet, which argues against scintillation" if s4["elevated"] is False
                     else ", S4 elevated so a storm contribution is possible" if s4["elevated"] else ", S4 unavailable")
                  + (", and a matching RFI report" if any(r["_within"] for r in c["rfi"]) else "") + ".")
        ev.append("Operator note: report to the appropriate authority; treat all affected machines as unreliable.")
    elif cls == "SPOOFING":
        t0 = c["stats"][tr[0]]
        if c.get("channel") == "XC":
            ev.append(f"{len(tr)} machines ({', '.join(tr)}) show large cross-track error at the same time while their signal health is normal "
                      "and the fleet is quiet: a wide-area spoofer or a common-mode guidance fault. The neighbour comparison used for single-machine "
                      "spoofing is unavailable here, so confidence is reduced.")
        ev.append(f"Attribution: guidance error grew (|XTE| to {t0['xte_max']:.2f} m) while signal health stayed normal "
                  f"(C/N0 {t0['cn0_dev_mean']:+.1f} dB, sats min {t0['sats_min']:.0f}); position error without signal collapse is the spoofing signature. "
                  "Reported position is not ground truth, so cross-check against an independent source (inertial/odometry) before resuming.")
    elif cls == "EQUIPMENT_FAULT":
        t0 = c["stats"][tr[0]]
        if c.get("mode") == "link":
            ev.append(f"Attribution: base_link_ok false for {100 * (1 - t0['link_frac']):.0f}% of the window on {tr[0]} only; neighbors keep their link. A machine-specific correction-link fault.")
        else:
            ev.append(f"Attribution: machine-specific antenna dropouts on {tr[0]} (sats drop in {100 * t0['sats_dropout_frac']:.0f}% of bins, "
                      f"min C/N0 dips in {100 * t0['mincn0_dip_frac']:.0f}%) with neighbors comparatively healthy.")
    ev.extend(operator_lines(c, p, conf_terms))
    others = [o for o in c.get("concurrent", []) if o["cls"] != cls]
    if others:
        ev.append("Concurrent distinct cause(s) in the same operational event: " + "; ".join(
            f"{o['cls']} on {','.join(o['tractors'])} {fmts(o['start'])}-{fmts(o['end'])}" for o in others) + ".")
    ev.append("Confidence built from: " + "; ".join(f"{n} {d:+.2f}" if i else n for i, (n, d) in enumerate(conf_terms)) + ".")
    return ev


def to_submission(cands, p, handle, repo_url, display_name=None):
    dets = []
    for c in cands:
        conf, terms = confidence(c, p)
        dets.append({
            "start": pd.Timestamp(c["start"]).isoformat(), "end": pd.Timestamp(c["end"]).isoformat(),
            "tractors": sorted(c["tractors"]), "class": c["cls"], "action": ACTION[c["cls"]],
            "confidence": conf, "evidence": evidence(c, p, terms),
        })
    if len(dets) > p.max_detections:     # validator limit; thin the recurring multipath reports evenly, never real episodes
        mp = [d for d in dets if d["class"] == "MULTIPATH"]
        other = [d for d in dets if d["class"] != "MULTIPATH"]
        room = max(0, p.max_detections - len(other))
        step = len(mp) / room if room else 0
        mp = [mp[int(i * step)] for i in range(room)] if room else []
        print(f"NOTE: thinned MULTIPATH reports to {len(mp)} to stay under the {p.max_detections}-detection limit")
        dets = sorted(other + mp, key=lambda d: d["start"])
    part = {"github_handle": handle}
    if display_name:
        part["display_name"] = display_name
    return {"participant": part, "repository_url": repo_url, "detections": dets}
