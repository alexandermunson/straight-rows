"""Turn candidates into submission detections: action, calibrated-ish confidence, checkable evidence."""
from __future__ import annotations

import json
import re
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


def to_submission(cands, p, handle, repo_url, display_name=None, plain=False):
    """plain=True adds a `reasoning` field (plain-language sections) per detection for the viewer. The submission
    writer never sets it, so submission.json is unaffected."""
    dets = []
    for c in cands:
        conf, terms = confidence(c, p)
        dets.append({
            "start": pd.Timestamp(c["start"]).isoformat(), "end": pd.Timestamp(c["end"]).isoformat(),
            "tractors": sorted(c["tractors"]), "class": c["cls"], "action": ACTION[c["cls"]],
            "confidence": conf, "evidence": evidence(c, p, terms),
        })
        if plain:
            dets[-1]["reasoning"] = explain(c, p, conf, terms)
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


# ---------------------------------------------------------------- plain-language reasoning (viewer only)
PLAIN_CLASS = {"SPACE_WEATHER": "a space-weather (ionospheric) disturbance", "JAMMING": "radio jamming",
               "SPOOFING": "GNSS spoofing", "EQUIPMENT_FAULT": "a fault on the machine itself",
               "MULTIPATH": "a harmless signal reflection (multipath)"}
PLAIN_ACTION = {
    "HALT_OR_DEGRADED_MODE": "Stop precision work or switch to a degraded mode until the disturbance passes. Nobody needs to be notified: the cause is natural.",
    "HALT_AND_REPORT": "Stop the affected machines and report the interference. Treat every listed machine's position as unreliable until the signal returns.",
    "HALT_CROSSCHECK_AND_REPORT": "Stop the machine, check its position against something independent of GNSS (inertial sensors, wheel odometry, a visual check of the rows), and report it.",
    "SERVICE_MACHINE_OTHERS_CONTINUE": "Take this one machine out for service. The others are unaffected and can keep working.",
    "CONTINUE_FALSE_ALARM": "Keep working. This is a false alarm as far as guidance is concerned.",
}


def _machines(trs):
    trs = list(trs)
    return trs[0] if len(trs) == 1 else ", ".join(trs[:-1]) + " and " + trs[-1]


_TERM_PLAIN = [
    (r"^S4 elevated: scintillation is an alternative$", "the ionosphere was disturbed, so a storm could explain part of it"),
    (r"^S4 elevated$", "the ionosphere was measurably disturbed"),
    (r"^S4 not elevated$", "the ionosphere was not measurably disturbed"),
    (r"^S4 unavailable.*$", "no scintillation reading was available"),
    (r"^S4 quiet$", "the ionosphere was calm"),
    (r"^Kp >= (\d+)$", r"geomagnetic activity was at storm level (Kp \1 or more)"),
    (r"^all machines affected$", "every machine was affected"),
    (r"^RFI report corroborates$", "an interference report matches in time and place"),
    (r"^low-confidence RFI report$", "a low-confidence interference report matches"),
    (r"^no RFI report.*$", "no interference report was filed (they are sometimes missing)"),
    (r"^deep C/N0 loss on the worst machine$", "the worst-hit machine lost more than 8 dB of signal"),
    (r"^loss differs between machines \(([\d.]+) dB\).*$", r"the machines were hit unequally (\1 dB apart), unlike a storm"),
    (r"^signal health intact$", "the signal stayed healthy while the position drifted"),
    (r"^large XTE$", "the machine went more than 2 m off its line"),
    (r"^XTE drift slope CI excludes zero$", "the drift was steady rather than random"),
    (r"^storm active: XTE could be ionospheric$", "a storm was active or recent, which can also disturb position"),
    (r"^several machines at once.*$", "several machines drifted together, leaving no healthy neighbour to compare with"),
    (r"^explicit link-down flag$", "the machine itself reported its correction link down"),
    (r"^neighbors healthy$", "the neighbouring machines were healthy"),
    (r"^recurs (\d+)x at one spot$", r"the same dip recurs \1 times at one spot"),
    (r"^lasted >= 10 min$", "it lasted at least 10 minutes"),
    (r"^short event$", "it lasted under 5 minutes"),
    (r"^overlaps another cause$", "it overlaps another cause"),
]


def _term(n):
    for pat, rep in _TERM_PLAIN:
        if re.match(pat, n):
            return re.sub(pat, rep, n)
    return n


def _confidence_plain(conf, terms):
    terms = [terms[0]] + [(_term(n), d) for n, d in terms[1:]]
    word = "high" if conf >= 0.85 else "fairly high" if conf >= 0.7 else "moderate" if conf >= 0.5 else "low"
    base = terms[0][1]
    up = [f"{n} (+{d:.2f})" for n, d in terms[1:] if d > 0]
    down = [f"{n} ({d:.2f})" for n, d in terms[1:] if d < 0]
    txt = f"Confidence is {conf:.2f} ({word}). It starts from {base:.2f} for this kind of cause"
    txt += (", goes up for: " + "; ".join(up)) if up else ""
    txt += (", and comes down for: " + "; ".join(down)) if down else ""
    return txt + ". The score is a transparent tally of these points, capped at 0.90; it is not a measured probability."


def _sda_plain(c, p):
    """Space-side picture in sentences: scintillation, geomagnetic activity, advisories, interference reports."""
    s4, out = c["s4"], []
    if s4["elevated"] is None:
        out.append("No scintillation (S4) reading is available for this period, so it counts neither for nor against a storm.")
    else:
        out.append(f"The ionospheric scintillation index S4 averaged {s4['mean']:.2f} during the event "
                   + (f"(it peaked at {s4['max']:.2f} within {p.s4_lag_min} minutes either side). " if np.isfinite(s4["max"]) else ". ")
                   + (f"That is well above the usual quiet level of {s4['base']:.2f} and above the {p.s4_high:.2f} level we treat as disturbed."
                      if s4["elevated"] else f"That is about the usual quiet level ({s4['base']:.2f}), so the ionosphere was calm."))
    if np.isfinite(c["kp_max"]):
        when = f" (the 3-hour reading starting {fmt(c['kp_ts'])})" if c["kp_ts"] is not None else ""
        out.append(f"Geomagnetic activity (Kp) reached {c['kp_max']:.1f} in the {p.kp_lead_h:g} hours before and during the event{when}; "
                   + (f"{p.kp_high:g} or more means a geomagnetic storm." if c["kp_max"] >= p.kp_high else f"storm level starts at {p.kp_high:g}, so this is quiet.")
                   + (f" The storm-time index Dst was as low as {c['dst_min']:.0f} nT in the preceding {p.dst_lead_h:g} hours"
                      + (", which shows a storm earlier in the day that is still recovering." if c["dst_min"] <= p.dst_low and c["kp_max"] < p.kp_high
                         else "." ) if np.isfinite(c["dst_min"]) else ""))
    for a_ in c["advisories"]:
        out.append(f"Advisory {a_.get('id', '')} was in force (issued {fmt(a_['_ts'])}, {a_['_age_h']:.0f} hours before this event, valid for "
                   f"{a_['_valid_h']:.0f} hours): \"{str(a_.get('text') or a_.get('type'))}\"")
    if c["rfi"]:
        for r in c["rfi"]:
            out.append(f"An interference report ({r.get('id', 'unnamed')}) was filed at {fmt(r['_ts'])}, {r['_delay_min']:.0f} minutes after this event began"
                       + (f", locating a source about {r['_dist_km']:.1f} km from the machines" if "_dist_km" in r else "")
                       + (", which is inside the report's own location uncertainty." if r["_within"] else ", which is too far away to be the same thing.")
                       + (" The report itself is marked low-confidence, so it counts half." if r["_weight"] < 1 else ""))
    else:
        out.append("No interference report was filed for this period. Reports normally arrive 10 to 30 minutes late and are sometimes "
                   "never filed, so their absence proves little.")
    return " ".join(out)


def _machine_plain(t, s):
    loss = -s["cn0_dev_mean"]
    sig = (f"signal strength averaged {s['cn0_mean']:.1f} dB-Hz, {loss:.1f} dB below its normal {s['cn0_base']:.1f}" if loss >= 1
           else f"signal strength stayed normal ({s['cn0_mean']:.1f} dB-Hz against its usual {s['cn0_base']:.1f})")
    fix = (f"it had lost its full-precision (RTK) fix {100 * s['fixbad_frac']:.0f}% of the time" if s["fixbad_frac"] >= 0.005
           else "it kept its full-precision (RTK) fix throughout")
    link = "" if s["link_frac"] > 0.995 else f"; its correction link from the base station was down {100 * (1 - s['link_frac']):.0f}% of the time"
    return (f"{t}: {sig}; its weakest satellite dropped to {s['mincn0_min']:.1f} dB-Hz (normally about {s['mincn0_base']:.1f}); "
            f"it tracked as few as {s['sats_min']:.0f} satellites (normally {s['sats_base']:.0f}); {fix}; "
            f"it was off its line by {s['xte_mean']:.2f} m on average and {s['xte_max']:.2f} m at worst{link}.")


def explain(c, p: Params, conf, terms):
    """The same reasoning as `evidence`, written to be read: [(heading, paragraph), ...]. Nothing is left out; technical
    terms are named once in brackets so each sentence can be checked against the plots."""
    cls, tr = c["cls"], c["tractors"]
    a, b = c["start"], c["end"]
    mins = (b - a).total_seconds() / 60
    others = [o for o in c.get("concurrent", []) if o["cls"] != cls]
    overlap = ("At the same time we also report " + "; ".join(
        f"{PLAIN_CLASS[o['cls']]} on {_machines(o['tractors'])} ({fmts(o['start'])} to {fmts(o['end'])})" for o in others)
        + ". These are separate causes with their own actions, and the overlap is one reason the confidence here is lower.") if others else ""
    out = []
    if cls == "MULTIPATH":
        m, t = c["mp"], tr[0]
        per = f"about every {m['period_s'] / 60:.1f} minutes" if np.isfinite(m["period_s"]) else "on every pass"
        deep = m['base_min_cn0'] - m['min_cn0'] > p.mp_dip_max_db
        out.append(("What happened", f"For {(b - a).total_seconds():.0f} seconds ({fmts(a)} to {fmts(b)}) the weakest satellite signal on {t} dipped to "
                    f"{m['min_cn0']:.1f} dB-Hz. Around that time it was typically {m['base_min_cn0']:.1f}, so the dip was "
                    f"{m['base_min_cn0'] - m['min_cn0']:.1f} dB at its lowest and {-m['depth']:.1f} dB for most of it. "
                    + (f"The single lowest reading is deeper than a reflection produces (more than {p.mp_dip_max_db:.0f} dB) and belongs to the other event "
                       "running at the same time; the reflection is the steadier part of the dip. " if deep else "")
                    + (f"During the dip {t} tracked {m['sats_min']:.0f} satellites (typically {m['sats_base']:.0f} around then) and was at most {m['xte_max']:.2f} m off its line."
                       if others else f"Nothing else moved: {t} kept {m['sats_min']:.0f} of its usual {m['sats_base']:.0f} satellites and was never more than {m['xte_max']:.2f} m off its line.")))
        out.append(("Why we think it is a reflection", f"The dip happens at one fixed place, the headland at "
                    f"{abs(m['spot'][0]):.5f}{'N' if m['spot'][0] >= 0 else 'S'} {abs(m['spot'][1]):.4f}{'E' if m['spot'][1] >= 0 else 'W'}, inside a strip only "
                    f"{p.mp_band_m:.0f} m wide. We found the same dip {m['n_total']} times over the three days"
                    + (f" ({m['n_window']} of them in the period shown)" if m['n_window'] != m['n_total'] else "") + f", {per} as "
                    f"{t} comes back to that spot. "
                    + (f"{_machines(m['peers'])} showed the same dip within 45 seconds on this pass, so it belongs to the place, not to one machine. "
                       if m["peers"] else "No other machine was at the spot on this pass. ")
                    + "A brief dip on one satellite's signal that repeats at the same location is what a signal bouncing off a nearby obstruction looks like."))
        out.append(("What else it could be", (overlap + " The dip is measured against the machine's own level at that moment, so it is counted as a "
                    "separate, harmless effect on top of the other event.") if overlap else f"Jamming or space weather would lower the average signal on all satellites and last minutes, not seconds; "
                    f"the average did not move. An antenna fault would cost satellites and produce dips deeper than {p.mp_dip_max_db:.0f} dB; the satellite "
                    "count held. Spoofing would pull the machine off its line; it stayed on."))
    else:
        st, nb = c["stats"], c["neighbors"]
        happened = f"Between {fmts(a)} and {fmts(b)} ({mins:.0f} minutes) something affected {_machines(tr)}.\n" + "\n".join(_machine_plain(t, st[t]) for t in tr)
        if nb:
            happened += "\nMeanwhile the other machines: " + "; ".join(
                f"{n} had {('normal signal' if abs(s['cn0_dev_mean']) < 1 else f'signal {-s_dev:.1f} dB down') if (s_dev := s['cn0_dev_mean']) is not None else ''}, "
                f"was at most {s['xte_max']:.2f} m off line and had its correction link up {100 * s['link_frac']:.0f}% of the time" for n, s in nb.items()) + "."
        out.append(("What happened", happened))
        stat = c.get("stat") or {}
        pairs = [(k, v) for k, v in (stat.get("pair") or {}).items() if np.isfinite(v["r"])]
        together = ""
        if pairs:
            sig = [f"{x} and {y} ({v['r']:+.2f})" for (x, y), v in pairs if v["significant"]]
            not_ = [f"{x} and {y} ({v['r']:+.2f})" for (x, y), v in pairs if not v["significant"]]
            together = ("Minute by minute, signal strength moved together on " + ", ".join(sig) + ". " if sig else "") + \
                       ("It did not move together on " + ", ".join(not_) + ". " if not_ else "") + \
                       "(The figure in brackets is the correlation: +1 means perfectly in step, 0 means unrelated.) "
        if cls == "SPACE_WEATHER":
            sp = c.get("spread_db")
            why = ("Every working machine lost signal at the same moment and by the same amount"
                   + ((f" (the loss differs by only {sp:.1f} dB between machines)" if sp >= 0.05 else " (the loss differs by less than 0.1 dB between machines)") if sp is not None else "")
                   + ". A disturbance in the ionosphere is far above the field, so it treats every receiver alike; a transmitter on the ground "
                   "would hit the nearest machine hardest. " + together)
            lg = stat.get("lag")
            if lg:
                why += (f" Over the three hours either side, the fleet's signal loss tracks the scintillation index closely (correlation {abs(lg['r']):.2f}, "
                        f"best match {('with no delay' if lg['lag_min'] == 0 else "at a delay of %+d minutes" % lg["lag_min"])}): each 0.1 rise in S4 goes with about {abs(lg['slope']) / 10:.1f} dB of loss. "
                        + ("That link is too strong to be chance, even allowing for the 13 delays we tried."
                           if lg["significant"] else "That link is not strong enough to rule out chance."))
            lead = (a - c["kp_ts"]).total_seconds() / 60 if c["kp_ts"] is not None else -1
            if lead > 0 and c["kp_max"] >= p.kp_high:
                why += f" The geomagnetic reading turned stormy {lead:.0f} minutes before the machines felt it, which is the order you expect from a storm."
            out.append(("Why we think it is space weather", why))
            out.append(("What else it could be", "A single-machine fault is ruled out because every machine was affected identically. A distant jammer could "
                        "in principle look this uniform, but nothing points to one. If a jammer switches on during the storm it shows up as a sudden "
                        "extra drop on top of the storm level, and we report that separately. " + overlap))
        elif cls == "JAMMING":
            sp, s4 = c.get("spread_db"), c["s4"]
            forced = any("jammer on top of a storm" in n for n in c.get("notes", []))
            why = (f"Signal strength fell abruptly on {_machines(tr)} at {fmts(a)} and recovered just as abruptly at {fmts(b)}, with satellites and the "
                   "full-precision fix lost while it lasted. A sudden, strong loss across all satellites is what a transmitter drowning out the GNSS band looks like. ")
            if forced:
                why += ("This happened in the middle of a space-weather disturbance that was already lowering the signal: the level stepped down "
                        f"sharply by at least {p.jam_step_db:.0f} dB beyond the storm level and later stepped back up. A storm changes gradually; a step like that means something was switched on. ")
            if sp is not None:
                why += (f"The machines were not hit equally: the loss differs by {sp:.1f} dB between them. A disturbance in the sky affects every machine alike "
                        "(the two storms in the labeled data differ by 0.1 dB or less), so unequal loss points to a source on the ground that is closer to some machines than others. "
                        if sp >= p.uniform_spread_db else
                        f"The machines were hit almost equally (within {sp:.1f} dB), which by itself does not separate a distant jammer from a storm. ")
            why += together
            out.append(("Why we think it is jamming", why))
            alt = ("Space weather is the main alternative. " + ("The ionosphere was calm at the time, which argues against it. " if s4["elevated"] is False
                   else "The ionosphere was disturbed at the time, so part of the loss is the storm; the abrupt extra step is what we attribute to jamming, and that is why confidence is low. " if s4["elevated"]
                   else "We have no scintillation reading, so we cannot use it either way. ")
                   + "A fault on one machine cannot explain several machines losing signal at the same second. The machines also drifted off their lines "
                   "during this event, but that is what happens when the signal is drowned out, so we do not call it spoofing. ")
            if not any(r["_within"] for r in c["rfi"]):
                alt += "No interference report backs this up, so the call rests on the machines' own data. "
            out.append(("What else it could be", alt + overlap))
        elif cls == "SPOOFING":
            t0 = st[tr[0]]
            dr = (stat.get("drift") or {}).get(tr[0])
            why = (f"{_machines(tr)} drifted steadily off the planned line, reaching {max(st[t]['xte_max'] for t in tr):.2f} m, while its receiver reported "
                   f"nothing wrong: signal strength within {abs(t0['cn0_dev_mean']):.1f} dB of normal and at least {t0['sats_min']:.0f} satellites in view. ")
            if dr and np.isfinite(dr["slope"]):
                why += (f"The error grew at about {dr['slope']:.2f} m per minute (give or take {dr['slope_half']:.3f}), "
                        + ("a smooth, steady pull rather than random wander. " if dr["slope_signif"] and dr["slope"] > 0 else "though not steadily enough to be sure it is a drift. "))
            why += ("Jamming and storms degrade the signal before they degrade the position. A position that walks away while the signal looks healthy is the "
                    "signature of a counterfeit signal steering the receiver. The cross-track error comes from a separate guidance monitor; we do not trust "
                    "the receiver's own reported position here. ")
            if c.get("channel") == "XC":
                why += (f"{len(tr)} machines were pulled at once, so there was no unaffected neighbour to compare against; that is why confidence is reduced. ")
            elif nb:
                why += "The neighbouring machines stayed on their lines (" + ", ".join(f"{n} at most {s['xte_max']:.2f} m" for n, s in nb.items()) + "), so the field and the task were not the cause. "
            out.append(("Why we think it is spoofing", why))
            out.append(("What else it could be", ("A storm was active or recent, and a disturbed ionosphere can also degrade position, which is why confidence is reduced; "
                        "but a storm would lower this machine's signal and affect its neighbours too. " if c["s4"]["elevated"] or c["kp_max"] >= p.kp_high
                        else "Space weather was quiet. ") + "Jamming would show as lost signal, and an antenna or link fault would show as lost satellites or a dropped "
                        "correction link; none of those happened. " + overlap))
        else:
            t0 = st[tr[0]]
            if c.get("mode") == "link":
                why = (f"{tr[0]}'s correction link from the base station was reported down for {100 * (1 - t0['link_frac']):.0f}% of the window, dropping in and out, and "
                       f"it lost its full-precision fix {100 * t0['fixbad_frac']:.0f}% of the time as a result. Its satellite signals were normal throughout, so "
                       "nothing was wrong with reception. ")
            else:
                why = (f"{tr[0]} kept losing individual satellites: its weakest signal fell far below normal in {100 * t0['mincn0_dip_frac']:.0f}% of the 10-second "
                       f"intervals" + (f" and its satellite count dropped in {100 * t0['sats_dropout_frac']:.0f}% of them" if t0['sats_dropout_frac'] >= 0.005 else "") + ", while its average signal strength moved only "
                       f"{abs(t0['cn0_dev_mean']):.1f} dB. Intermittent dropouts with a healthy average are typical of an antenna or cable problem. ")
            if nb:
                why += ("The other machines, working the same field under the same sky, were fine (" + "; ".join(
                    f"{n} signal {('normal' if abs(s['cn0_dev_mean']) < 1 else f'{-s['cn0_dev_mean']:.1f} dB down')}, link up {100 * s['link_frac']:.0f}%" for n, s in nb.items())
                    + "). Anything in the sky or on the air would have reached them too. ")
            fl = (stat.get("fleet") or {}).get(tr[0])
            if fl and np.isfinite(fl.get("r2", np.nan)):
                why += (f"Its signal went up and down independently of the rest of the fleet (only {100 * fl['r2']:.0f}% of the variation is shared), which again points at the machine. "
                        if fl["r2"] < 0.3 else f"Its signal does follow the fleet's to some degree ({100 * fl['r2']:.0f}% shared), so a common cause cannot be fully excluded. ")
            out.append(("Why we think it is an equipment fault", why))
            out.append(("What else it could be", "Jamming and space weather are ruled out because they would affect the neighbours. Spoofing is ruled out because the machine "
                        f"stayed on its line (at most {t0['xte_max']:.2f} m off). "
                        + ("An interference report is listed above for completeness, but its timing fits a different event and this call does not rely on it. " if c["rfi"] else "")
                        + overlap))
    if cls != "MULTIPATH":
        out.insert(2, ("What the space-weather and interference feeds say", _sda_plain(c, p)))
    out.append(("What to do", f"{PLAIN_ACTION[ACTION[cls]]} (Action code: {ACTION[cls]}.)"))
    out.append(("How sure we are", _confidence_plain(conf, terms)))
    return [(h, "\n".join(" ".join(line.split()) for line in t.split("\n") if line.strip())) for h, t in out]
