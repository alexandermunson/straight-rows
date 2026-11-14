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
        sf = (c.get("ax") or {}).get("source")
        if sf and not sf.get("flat") and sf["r2"] >= 0.9 and -35.0 <= sf["slope"] <= -8.0:
            terms.append((f"losses fit one ground transmitter (R2 {sf['r2']:.2f})", +0.10))
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
    ev.extend(analysis_evidence(c, p))
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
    ev.extend(analysis_evidence(c, p))
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
            dets[-1]["ops"] = operator_card(c, p, conf, terms)
            sf = (c.get("ax") or {}).get("source")
            if c["cls"] == "JAMMING" and sf and not sf.get("flat") and sf["r2"] >= 0.9:
                dets[-1]["source"] = dict(lat=sf["lat"], lon=sf["lon"], radius_m=sf["radius_m"], r2=sf["r2"])
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
    (r"^losses fit one ground transmitter \(R2 ([\d.]+)\)$", r"one ground transmitter explains \1 of the pattern of loss"),
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


def _sda_key(c, p, cls):
    """The one or two feed facts that decided the call, for the headline reason (full detail stays in the feeds section)."""
    s4, out = c["s4"], []
    if cls == "SPACE_WEATHER":
        if s4["elevated"]:
            out.append(f"The space-weather feeds agree: the ionospheric scintillation index S4 averaged {s4['mean']:.2f} during the event, "
                       f"against a quiet level of {s4['base']:.2f}.")
        if np.isfinite(c["kp_max"]) and c["kp_max"] >= p.kp_high:
            out.append(f"Geomagnetic activity (Kp) reached {c['kp_max']:.1f}, where {p.kp_high:g} or more means a storm.")
        if any(str(a_.get("type", "")).upper() == "SPACE_WEATHER" for a_ in c["advisories"]):
            out.append("A geomagnetic storm advisory was also in force.")
    elif cls == "JAMMING":
        hits = [r for r in c["rfi"] if r["_within"]]
        if hits:
            r = hits[0]
            out.append(f"An interference report ({r.get('id', 'unnamed')}) filed {r['_delay_min']:.0f} minutes after the start places a source "
                       + (f"about {r['_dist_km']:.1f} km from the machines." if "_dist_km" in r else "near the machines."))
    return " ".join(out) + (" " if out else "")


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
        out.append(("Why this is marked as multipath (a harmless reflection)", f"The dip happens at one fixed place, the headland at "
                    f"{abs(m['spot'][0]):.5f}{'N' if m['spot'][0] >= 0 else 'S'} {abs(m['spot'][1]):.4f}{'E' if m['spot'][1] >= 0 else 'W'}, inside a strip only "
                    f"{p.mp_band_m:.0f} m wide. We found the same dip {m['n_total']} times over the three days"
                    + (f" ({m['n_window']} of them in the period shown)" if m['n_window'] != m['n_total'] else "") + f", {per} as "
                    f"{t} comes back to that spot. "
                    + (f"{_machines(m['peers'])} showed the same dip within 45 seconds on this pass, so it belongs to the place, not to one machine. "
                       if m["peers"] else "No other machine was at the spot on this pass. ")
                    + "A brief dip on one satellite's signal that repeats at the same location is what a signal bouncing off a nearby obstruction looks like."))
        out.append(("Why not something else", (overlap + " The dip is measured against the machine's own level at that moment, so it is counted as a "
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
                   "would hit the nearest machine hardest. " + _sda_key(c, p, cls) + together)
            lg = stat.get("lag")
            if lg:
                why += (f" Over the three hours either side, the fleet's signal loss tracks the scintillation index closely (correlation {abs(lg['r']):.2f}, "
                        f"best match {('with no delay' if lg['lag_min'] == 0 else "at a delay of %+d minutes" % lg["lag_min"])}): each 0.1 rise in S4 goes with about {abs(lg['slope']) / 10:.1f} dB of loss. "
                        + ("That link is too strong to be chance, even allowing for the 13 delays we tried."
                           if lg["significant"] else "That link is not strong enough to rule out chance."))
            lead = (a - c["kp_ts"]).total_seconds() / 60 if c["kp_ts"] is not None else -1
            if lead > 0 and c["kp_max"] >= p.kp_high:
                why += f" The geomagnetic reading turned stormy {lead:.0f} minutes before the machines felt it, which is the order you expect from a storm."
            out.append(("Why this is marked as space weather", why))
            out.append(("Why not something else", "A single-machine fault is ruled out because every machine was affected identically. A distant jammer could "
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
            why += _sda_key(c, p, cls) + together
            out.append(("Why this is marked as jamming", why))
            alt = ("Space weather is the main alternative. " + ("The ionosphere was calm at the time, which argues against it. " if s4["elevated"] is False
                   else "The ionosphere was disturbed at the time, so part of the loss is the storm; the abrupt extra step is what we attribute to jamming, and that is why confidence is low. " if s4["elevated"]
                   else "We have no scintillation reading, so we cannot use it either way. ")
                   + "A fault on one machine cannot explain several machines losing signal at the same second. The machines also drifted off their lines "
                   "during this event, but that is what happens when the signal is drowned out, so we do not call it spoofing. ")
            if not any(r["_within"] for r in c["rfi"]):
                alt += "No interference report backs this up, so the call rests on the machines' own data. "
            out.append(("Why not something else", alt + overlap))
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
            out.append(("Why this is marked as spoofing", why))
            out.append(("Why not something else", ("A storm was active or recent, and a disturbed ionosphere can also degrade position, which is why confidence is reduced; "
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
                why += (f"Its signal went up and down independently of the rest of the fleet ({'almost none' if fl['r2'] < 0.005 else f"only {100 * fl['r2']:.0f}%"} of the variation is shared), which again points at the machine. "
                        if fl["r2"] < 0.3 else f"Its signal does follow the fleet's to some degree ({100 * fl['r2']:.0f}% shared), so a common cause cannot be fully excluded. ")
            out.append(("Why this is marked as an equipment fault", why))
            out.append(("Why not something else", "Jamming and space weather are ruled out because they would affect the neighbours. Spoofing is ruled out because the machine "
                        f"stayed on its line (at most {t0['xte_max']:.2f} m off). "
                        + ("An interference report is listed above for completeness, but its timing fits a different event and this call does not rely on it. " if c["rfi"] else "")
                        + overlap))
    a_why, a_not, a_detail = analysis_plain(c, p)
    out = [(h, (t + " " + " ".join(a_why)) if h.startswith("Why this is") else (t + " " + " ".join(a_not)) if h.startswith("Why not") else t)
           for h, t in out]
    if cls != "MULTIPATH":
        out.insert(2, ("What the space-weather and interference feeds say", _sda_plain(c, p)))
    if a_detail:
        out.append(("Independent checks", "\n".join(a_detail)))
    out.append(("What to do", f"{PLAIN_ACTION[ACTION[cls]]} (Action code: {ACTION[cls]}.)"))
    out.append(("How sure we are", _confidence_plain(conf, terms)))
    return [(h, "\n".join(" ".join(line.split()) for line in t.split("\n") if line.strip())) for h, t in out]


# ---------------------------------------------------------------- cross-check analyses (analysis.py) in words
def _fix_words(share):
    from .analysis import FIX_ORDER, FIX_PLAIN
    parts = [f"{FIX_PLAIN[k]} {100 * share[k]:.0f}%" for k in FIX_ORDER if share.get(k, 0) >= 0.005]
    return ", ".join(parts)


def analysis_evidence(c, p: Params):
    """Compact, checkable evidence lines from the seven cross-checks (submission)."""
    from .analysis import Analysis
    ax, out = c.get("ax") or {}, []
    if c["cls"] == "MULTIPATH":
        per = ax.get("period") or {}
        t = c["tractors"][0]
        if per.get("period", {}).get(t):
            out.append(f"Periodicity: dips on {t} repeat every {per['period'][t]:.0f} s (autocorrelation {per['ac'][t]:.2f})"
                       + (f"; the {per['n_off']} dips outside the band are spread along the field (at most {per['off_max_bin']} per 100 m "
                          f"vs {per['n_in']} in the band), so there is no second recurring spot." if "n_off" in per else "."))
        return out
    sm, sf, cu, fx, gm, po = (ax.get(k) for k in ("storm", "source", "cusum", "fix", "geom", "posterior"))
    if sm:
        out.append(f"Storm model (fleet loss regressed on S4 over all 72 h, r={sm['r']:.2f}, {sm['slope']:.1f} dB per unit S4, n={sm['n']} 15-min bins): "
                   f"S4 predicts {sm['pred']:.1f} dB of loss here, observed {sm['obs']:.1f} dB, so {sm['resid']:+.1f} dB is not explained by the ionosphere.")
    if sf:
        if sf.get("flat") or sf["r2"] < 0.5:
            out.append("Ground-source test: the loss does not vary with machine position "
                       + (f"(spread {sf['loss_sd']:.1f} dB)" if sf.get("flat") else f"(a point source explains only {100 * sf['r2']:.0f}% of the variation)")
                       + ", so no single ground transmitter fits; consistent with a cause overhead.")
        else:
            out.append(f"Ground-source test: a single transmitter at {sf['lat']:.4f}, {sf['lon']:.4f} explains {100 * sf['r2']:.1f}% of the variation in loss across "
                       f"machines and time (n={sf['n']}), falling {abs(sf['slope']):.1f} dB per tenfold distance (free space is 20); "
                       f"95% region within {sf['radius_m']:.0f} m of that point if the distance model holds; "
                       + "; ".join(f"{t} {v['dist']:.0f} m away lost {v['loss']:.1f} dB" for t, v in sf["per"].items())
                       + (f"; {sf['rfi_km']:.1f} km from the centre of {sf['rfi_id']}." if "rfi_km" in sf else "."))
    if fx:
        out.append("Positioning modes: " + "; ".join(f"{t} {_fix_words(v['share'])}" for t, v in fx.items()) + ".")
    if gm:
        out.append(f"Relative geometry: {c['tractors'][0]}'s reported position moved {gm['moved']:.1f} m sideways relative to {gm['ref']} "
                   f"(which held its row, {gm['sep0']:.0f} m away), tracking the cross-track error (max {gm['xte_max']:.1f} m) with r={gm['r']:.3f}.")
    if cu:
        if cu.get("no_ref"):
            out.append("Live-alarm check: not available, the machines had not been working long enough before the onset to set a reference level.")
        elif cu["delay_s"] is not None:
            out.append(f"Live-alarm check (backward-looking CUSUM on {cu['what']}): alarm {cu['delay_s']:.0f} s after onset"
                       + (", with an earlier false alarm in the preceding 30 min." if cu["false_alarm"] else ", no false alarm in the preceding 30 min."))
    if po:
        pr = po["probs"]
        out.append(f"Probability cross-check (naive Bayes over {po['n_obs']} yes/no observations, hand-set likelihoods, equal priors): "
                   + ", ".join(f"{k} {100 * v:.1f}%" for k, v in sorted(pr.items(), key=lambda kv: -kv[1]) if v >= 0.0005)
                   + ("." if po["agrees"] else f". This disagrees with the reported class ({c['cls']}).")
                   + (" Storm indicators are left out because the concurrent storm is reported separately." if po["in_storm"] else ""))
    return out


def analysis_plain(c, p: Params):
    """(why, why_not, detail) sentences for the reasoning panel."""
    from .analysis import FIX_PLAIN, Analysis
    ax = c.get("ax") or {}
    why, why_not, detail = [], [], []
    cls, tr = c["cls"], c["tractors"]
    if cls == "MULTIPATH":
        per, t = ax.get("period") or {}, tr[0]
        if per.get("period", {}).get(t):
            why.append(f"Checked a second way, without using location at all: the dips on {t} repeat like clockwork every {per['period'][t]:.0f} seconds, "
                       "which is the time it takes to come back round to the headland.")
            if "n_off" in per:
                why.append(f"We also looked for a second such spot. The {per['n_off']} dips found elsewhere are scattered along the whole field "
                           f"(at most {per['off_max_bin']} in any 100 m stretch, against {per['n_in']} at the headland), so this is the only one.")
        return why, why_not, detail
    sm, sf, cu, fx, gm, po = (ax.get(k) for k in ("storm", "source", "cusum", "fix", "geom", "posterior"))
    src_ok = Analysis.source_ok(sf)
    if cls == "SPACE_WEATHER":
        if sm:
            why.append(f"The size of the loss is what the ionosphere predicts. Across the three days, fleet signal loss follows the scintillation index almost "
                       f"exactly (about {sm['slope'] / 10:.1f} dB for every 0.1 of S4). For this event that predicts {sm['pred']:.1f} dB; the machines lost {sm['obs']:.1f} dB."
                       + (f" The remaining {sm['resid']:.1f} dB comes from the separate event reported inside this window." if sm["resid"] > 2.5 else ""))
        if sf:
            why_not.append("We tested directly for a jammer by trying to place a single ground transmitter that would explain each machine's loss from its distance. "
                           + ("None fits: the loss does not change at all as the machines move around the field."
                              if sf.get("flat") else f"None fits: the best position explains only {100 * sf['r2']:.0f}% of the variation in loss.")
                           + " A source overhead is the only kind that looks like that.")
    elif cls == "JAMMING":
        if src_ok:
            why.append(f"The strongest evidence is geometric. If we assume one transmitter on the ground and ask where it would have to be, a single spot "
                       f"({sf['lat']:.4f}, {sf['lon']:.4f}) explains {100 * sf['r2']:.1f}% of how the loss rose and fell on every machine as they drove up and down the field. "
                       f"The loss weakens by {abs(sf['slope']):.1f} dB for every tenfold increase in distance from that spot; radio waves in open air weaken by 20. "
                       f"The data pin the spot down to within about {sf['radius_m']:.0f} m (shown on the map), about {sf['field_dist_m']:.0f} m from the nearest point any machine reached. "
                       "That precision assumes signal falls off smoothly with distance, which holds in this data but would be rougher over real terrain. "
                       + " ".join(f"{t}, on average {v['dist']:.0f} m from it, lost {v['loss']:.1f} dB." for t, v in sf["per"].items())
                       + (f" That spot is {sf['rfi_km']:.1f} km from the centre of interference report {sf['rfi_id']}, inside the report's stated uncertainty."
                          if "rfi_km" in sf else " No interference report exists for this event, so this fit is the main independent support for it.")
                       + " A machine that lost less signal was simply further away, so every listed machine belongs to the same event.")
        elif sf:
            why.append(f"We also tried to place a single ground transmitter that would explain each machine's loss from its distance. The fit is not convincing "
                       f"(it explains {100 * sf.get('r2', 0):.0f}% of the variation), so the location evidence neither helps nor hurts here.")
        if sm:
            why_not.append((f"The ionosphere cannot account for it: at the scintillation level measured, the storm model predicts {sm['pred']:.1f} dB of loss and the "
                            f"machines lost {sm['obs']:.1f} dB. " + (f"The other {sm['resid']:.1f} dB is what we attribute to the jammer."
                            if sm["pred"] > 2 else "Essentially none of the loss is explained by space weather.")))
    elif cls == "SPOOFING":
        if fx and tr[0] in fx:
            f0 = fx[tr[0]]
            if f0["share"].get("RTK_FIXED", 0) > 0.98:
                why.append(f"Throughout, {tr[0]}'s receiver claimed its best positioning mode (full-precision fix 100% of the time). A receiver that is confident "
                           "and wrong by metres is being fed a believable false signal; interference or a fault makes a receiver lose that mode.")
            else:
                why.append(f"{tr[0]}'s positioning mode did degrade ({_fix_words(f0['share'])}), but so did every other machine's because of the concurrent storm, "
                           "so the mode tells us nothing extra here.")
        if gm:
            why.append(f"The drift also shows up in the machine's own reported position, not just in the guidance monitor: relative to {gm['ref']}, which held its row "
                       f"{gm['sep0']:.0f} m away, {tr[0]}'s reported track slid {gm['moved']:.1f} m sideways, in step with the {gm['xte_max']:.1f} m cross-track error "
                       f"(correlation {gm['r']:.3f}). Two independent measurements agree that the position solution itself was walking away.")
    else:
        if fx and tr[0] in fx:
            f0 = fx[tr[0]]
            if c.get("mode") == "link":
                why.append(f"The positioning mode fits a lost correction feed and nothing worse: {_fix_words(f0['share'])}, flipping {f0['changes_per_min']:.0f} times a minute "
                           "as the link came and went. It never fell below reduced precision, which is what happens when corrections stop but the satellites are still received well.")
            elif f0["share"].get("RTK_FIXED", 0) > 0.98:
                why.append(f"The receiver kept its full-precision fix the whole time, so enough good satellites were always in view. Jamming or a storm strong "
                           "enough to matter would have cost it that fix; individual satellites dropping in and out without that points at the antenna or its cable.")
            else:
                why.append(f"Positioning modes during the event: {_fix_words(f0['share'])}.")
    if fx and cls in ("SPACE_WEATHER", "JAMMING"):
        detail.append("Positioning modes during the event. " + " ".join(f"{t}: {_fix_words(v['share'])}." for t, v in fx.items()))
    if po:
        pr = sorted(po["probs"].items(), key=lambda kv: -kv[1])
        txt = (f"As a separate check we combined {po['n_obs']} simple yes/no observations (for example: "
               + "; ".join(po["present"][:3]) + ") into a probability for each cause. It gives "
               + ", ".join(f"{PLAIN_CLASS[k].replace('a ', '', 1) if k != 'EQUIPMENT_FAULT' else 'an equipment fault'} {100 * v:.1f}%" for k, v in pr[:2])
               + (". That agrees with the label." if po["agrees"] else ". That does NOT agree with the label, which is a reason for caution.")
               + (" Because a storm was running at the same time and is reported separately, the storm's own indicators were left out of this calculation." if po["in_storm"] else "")
               + (" Another cause is reported on this machine at the same time, and the observations it accounts for were left out." if po.get("overlap") else "")
               + " The weights in this calculation were set by hand from the physics, not learned, so read it as a consistency check.")
        why_not.append(txt)
    if cu:
        if cu.get("no_ref"):
            detail.append("How fast a live system would have caught it: cannot be estimated for this event, because the machines only started working at about the "
                          "time it began and there is no earlier stretch to compare against.")
        elif cu["delay_s"] is not None:
            detail.append(f"How fast a live system would have caught it: a detector that only looks backwards in time (watching the {cu['what']}) would have raised "
                          f"the alarm {cu['delay_s']:.0f} seconds after the event began"
                          + (", though it also raised a false alarm in the half hour before." if cu["false_alarm"] else ", with no false alarm in the half hour before.")
                          + (" Slow drifts take longer to catch than sudden drops; the start time we report is worked out afterwards by tracing the drift back." if cls == "SPOOFING" else ""))
        else:
            detail.append(f"How fast a live system would have caught it: a detector that only looks backwards in time (watching the {cu['what']}) would not have "
                          "alarmed on this event with its standard settings.")
    return why, why_not, detail


# ---------------------------------------------------------------- operator view (viewer "Operator actions" tab)
ACTION_HEAD = {"HALT_OR_DEGRADED_MODE": "Stop precision work or run in degraded mode", "HALT_AND_REPORT": "Stop and report interference",
               "HALT_CROSSCHECK_AND_REPORT": "Stop, cross-check position, and report", "SERVICE_MACHINE_OTHERS_CONTINUE": "Service this machine; others continue",
               "CONTINUE_FALSE_ALARM": "Keep working (false alarm)"}


def operator_card(c, p: Params, conf, terms):
    """Decision card for the operator tab: the action, what each machine showed, what the space-side feeds showed, how
    the two connect, what is uncertain, what would change the call, and what acting (or being wrong) costs.
    Same numbers as the evidence; plain sentences."""
    cls, tr = c["cls"], c["tractors"]
    if cls == "MULTIPATH":
        return None                      # recurring reflections are summarised per machine in the tab, not carded one by one
    a, b = c["start"], c["end"]
    mins = (b - a).total_seconds() / 60
    st, nb, s4, ax = c["stats"], c["neighbors"], c["s4"], c.get("ax") or {}
    sm, sf = ax.get("storm"), ax.get("source")
    src_ok = bool(sf and not sf.get("flat") and sf["r2"] >= 0.9)
    rfi_hit = [r for r in c["rfi"] if r["_within"]]
    in_storm = any(o["cls"] == "SPACE_WEATHER" for o in c.get("concurrent", []) if o["cls"] != cls)
    quiet = s4["elevated"] is False and not (np.isfinite(c["kp_max"]) and c["kp_max"] >= p.kp_high)

    def machine(t):
        s = st[t]
        loss = -s["cn0_dev_mean"]
        bits = [f"signal {loss:.1f} dB below normal" if loss >= 1 else "signal strength normal"]
        bits.append(f"full-precision fix lost {100 * s['fixbad_frac']:.0f}% of the time" if s["fixbad_frac"] >= 0.005 else "full-precision fix held")
        bits.append(f"up to {s['xte_max']:.2f} m off its line")
        if s["sats_min"] < s["sats_base"] - 2:
            bits.append(f"as few as {s['sats_min']:.0f} of {s['sats_base']:.0f} satellites")
        if s["link_frac"] < 0.995:
            bits.append(f"correction link down {100 * (1 - s['link_frac']):.0f}% of the time")
        return "; ".join(bits) + "."

    farm = {t: machine(t) for t in tr}
    others = [t for t in nb]
    sda = _sda_plain(c, p)
    unaffected = (f"{_machines(others)} showed nothing unusual and can keep working." if others and cls in ("SPOOFING", "EQUIPMENT_FAULT")
                  else f"{_machines(others)} {'was' if len(others) == 1 else 'were'} not listed for this event." if others else "Every working machine is covered by this action.")
    amb, change = [], []
    if cls == "SPACE_WEATHER":
        link = (_sda_key(c, p, cls)
                + (f"The scintillation level predicts {sm['pred']:.1f} dB of signal loss and the machines lost {sm['obs']:.1f} dB. " if sm else "")
                + "Every machine was hit by the same amount, which only a cause far overhead produces.")
        if s4["elevated"] is None:
            amb.append("No scintillation reading was available, so the call rests on Kp, the advisory and the uniform loss.")
        else:
            amb.append("Little. The only alternative is a distant jammer that happens to affect every machine equally; we tested for a ground "
                       "transmitter and none fits.")
        if any(o["cls"] == "JAMMING" for o in c.get("concurrent", [])):
            amb.append("A jammer was also active for part of this window and is handled as its own event.")
        change.append("An interference report placing a source nearby, or the machines starting to lose signal unequally, would point to jamming instead.")
        cost = (f"All {len(tr)} machines lose precision guidance for {mins:.0f} minutes ({len(tr) * mins / 60:.1f} machine-hours). Degraded-mode work is "
                "possible if metre-level accuracy is acceptable for the task.")
        wrong = ("If this were really jamming, the machines would have been stopped anyway; what would be missed is the interference report. "
                 "If the warning were ignored, the machines would have worked up to "
                 f"{max(s['xte_max'] for s in st.values()):.1f} m off their lines.")
    elif cls == "JAMMING":
        link = (("The space-weather feeds were quiet (scintillation at its normal level, geomagnetic activity below storm level), so the sky does not explain "
                 "the loss. " if quiet else "A storm was running, but the loss stepped far beyond what the storm accounts for. ")
                + (f"The storm model predicts {sm['pred']:.1f} dB here; the machines lost {sm['obs']:.1f} dB. " if sm else "")
                + (f"Interference report {rfi_hit[0].get('id', '')} was filed {rfi_hit[0]['_delay_min']:.0f} minutes after onset"
                   + (f", {rfi_hit[0]['_dist_km']:.1f} km from the machines. " if "_dist_km" in rfi_hit[0] else ". ") if rfi_hit else "No interference report was filed for it. ")
                + (f"The machines' own losses point to a single ground source (it explains {100 * sf['r2']:.1f}% of the pattern), about "
                   f"{sf['field_dist_m']:.0f} m from the nearest machine track." if src_ok else ""))
        if not rfi_hit:
            amb.append("No interference report backs this up (reports are often late or missing), so the call rests on the machines' own data"
                       + (" and the ground-source fit." if src_ok else "."))
        if in_storm:
            amb.append("A storm was running at the same time. How the loss splits between storm and jammer is an estimate"
                       + (f" ({sm['pred']:.0f} dB storm, {sm['resid']:.0f} dB jammer)." if sm else "."))
        weak = [t for t in tr if st[t]["fixbad_frac"] < 0.2]
        if weak and len(weak) < len(tr):
            amb.append(f"{_machines(weak)} {'was' if len(weak) == 1 else 'were'} only weakly affected "
                       "(" + "; ".join("%s lost %.1f dB" % (t, -st[t]["cn0_dev_mean"]) for t in weak) + ") and mostly kept a full-precision fix. "
                       "It is listed because it is the same emitter, but whether it must stop is a judgment call for the operator.")
        if not amb:
            amb.append("Little: the feeds are quiet, a report matches, and one ground source explains the losses.")
        change.append("A rise in scintillation, or the loss becoming equal on every machine, would point to space weather. "
                      + ("A report arriving in the next 30 minutes would confirm it." if not rfi_hit else ""))
        cost = (f"{len(tr)} machines stop for {mins:.0f} minutes ({len(tr) * mins / 60:.1f} machine-hours) and an interference report is filed"
                + (f" with the estimated source position ({sf['lat']:.4f}, {sf['lon']:.4f})." if src_ok else "."))
        wrong = (f"If the machines had kept working they would have been up to {max(s['xte_max'] for s in st.values()):.1f} m off their lines with no "
                 "reliable fix. If this were really space weather, the only cost of the wrong label is an unnecessary report; the stop is right either way.")
    elif cls == "SPOOFING":
        t0 = st[tr[0]]
        link = ("No space-side feed can see spoofing directly, so their job here is to rule out the alternatives. "
                + ("They do: scintillation and geomagnetic activity were quiet, so the drift is not ionospheric. " if quiet else
                   "They only partly do: a storm was active or had just ended, which can also disturb position. ")
                + ("No interference report was filed, which fits spoofing (a spoofer does not raise the noise level the way a jammer does). " if not rfi_hit else "")
                + f"On the farm side, {_machines(tr)} drifted to {t0['xte_max']:.2f} m off line while its signal and fix stayed healthy"
                + (f" and its neighbours stayed within {max(n['xte_max'] for n in nb.values()):.2f} m." if nb else "."))
        amb.append("Spoofing is inferred, not observed: nothing in the data measures the false signal itself. A guidance or steering fault on this one machine "
                   "would look similar, which is why the action is to cross-check against an independent position source before reporting.")
        if in_storm or not quiet:
            amb.append("A storm was active or recent, which lowers confidence.")
        change.append("If the independent cross-check (inertial, odometry, visual) agrees with the GNSS position, it is a guidance-monitor fault, not spoofing. "
                      "If the drift appears on several machines at once with healthy signal, it is a wider spoofing attack.")
        cost = (f"One machine stops for {mins:.0f} minutes while its position is checked; the other machines keep working.")
        wrong = (f"If ignored, the machine would have steered up to {t0['xte_max']:.2f} m off its line while its display showed a healthy "
                 "full-precision fix, so the driver would have had no warning. If it turns out to be a monitor fault, the cost is one machine's downtime.")
    elif cls == "EQUIPMENT_FAULT":
        t0 = st[tr[0]]
        link = (("The space-weather feeds were quiet and no interference report applies" if quiet and not rfi_hit else
                 "The space-side feeds do not single out this machine")
                + (f", and {_machines(others)}, working the same field under the same sky, {'was' if len(others) == 1 else 'were'} unaffected. " if others else ". ")
                + "Anything coming from the sky or the air would have reached them too, so the cause is on this machine.")
        if c.get("mode") == "link":
            amb.append("The fault itself is not in doubt (the machine reports its correction link down). What is unknown is why: the radio, its antenna, "
                       "or the data path to the base station. A base-station fault is unlikely because the other machines kept their link.")
            wrong = (f"If ignored, the machine keeps working at reduced precision about {100 * (1 - t0['link_frac']):.0f}% of the time (up to {t0['xte_max']:.2f} m off line here).")
        else:
            amb.append("This is the least certain kind of call. Nothing in the data tests the hardware directly; a weak jammer very close to this one machine "
                       "could look similar, but would also lower its average signal, which did not happen.")
            wrong = ("If ignored, the machine keeps its fix for now but is one dropout away from losing it. If this were really a nearby jammer, "
                     "the neighbours would show it within minutes.")
        change.append("If a neighbour starts showing the same symptom, or an interference report arrives for this period, treat it as jamming and stop the fleet.")
        cost = (f"Only {tr[0]} stops for service. Stopping the whole fleet instead would have cost a further {len(others) * mins / 60:.1f} machine-hours.")
    else:
        return None
    worst = max(s_["xte_max"] for s_ in st.values())
    losses = sorted(-s_["cn0_dev_mean"] for s_ in st.values())
    loss_txt = f"{losses[0]:.0f} dB" if losses[-1] - losses[0] < 1 else f"{losses[0]:.0f} to {losses[-1]:.0f} dB"
    if cls == "SPACE_WEATHER":
        brief = f"Every machine lost {loss_txt} of signal together during a geomagnetic storm."
    elif cls == "JAMMING":
        brief = (f"{'All' if len(tr) == 3 else len(tr)} machines abruptly lost {loss_txt} of signal"
                 + (" on top of a storm" if in_storm else " with the sky quiet")
                 + ("; an interference report matches." if rfi_hit else "; no interference report, but one ground source fits."
                    if src_ok else "; no interference report."))
    elif cls == "SPOOFING":
        brief = f"{tr[0]} drifted {worst:.1f} m off its line while its signal and fix looked healthy."
    elif c.get("mode") == "link":
        brief = f"{tr[0]}'s correction link kept dropping out ({100 * (1 - st[tr[0]]['link_frac']):.0f}% of the time); the other machines were fine."
    else:
        brief = f"{tr[0]} kept dropping individual satellites; the other machines were fine."
    return dict(brief=brief, head=ACTION_HEAD[ACTION[cls]], action=ACTION[cls], farm=farm, unaffected=unaffected, sda=sda, link=" ".join(link.split()),
                ambiguity=" ".join(amb), change=" ".join(change), cost=cost, wrong=wrong, conf=_confidence_plain(conf, terms),
                overlap=[f"{PLAIN_CLASS[o['cls']]} on {_machines(o['tractors'])}, {fmts(o['start'])} to {fmts(o['end'])}"
                         for o in c.get("concurrent", []) if o["cls"] != cls and o["cls"] != "MULTIPATH"])
