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
    if cls == "SPACE_WEATHER":
        if c["s4"]["elevated"]:
            terms.append(("S4 elevated", +0.2))
        else:
            terms.append(("S4 not elevated", -0.2))
        if c["kp_max"] >= p.kp_high:
            terms.append(("Kp >= %.0f" % p.kp_high, +0.1))
        if len(c["tractors"]) >= 3:
            terms.append(("all machines affected", +0.05))
    elif cls == "JAMMING":
        if any(r["_within"] for r in c["rfi"]):
            terms.append(("RFI report corroborates", +0.2))
        else:
            terms.append(("no RFI report (reports can be missing)", -0.05))
        if c["s4"]["elevated"]:
            terms.append(("S4 elevated: scintillation is an alternative", -0.15))
        else:
            terms.append(("S4 quiet", +0.1))
        if abs(st["cn0_dev_mean"]) > 8:
            terms.append(("deep C/N0 loss", +0.1))
    elif cls == "SPOOFING":
        if abs(st["cn0_dev_mean"]) < 2:
            terms.append(("signal health intact", +0.15))
        if st["xte_max"] > 2.0:
            terms.append(("large XTE", +0.1))
        if c["s4"]["elevated"] or c["kp_max"] >= p.kp_high:
            terms.append(("storm active: XTE could be ionospheric", -0.1))
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


def evidence(c, p: Params, conf_terms):
    cls = c["cls"]
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
    s4 = c["s4"]
    sda = (f"SDA: S4 over window mean {s4['mean']:.2f} (max within +/-{p.s4_lag_min} min {s4['max']:.2f}; quiet baseline "
           f"{s4['base']:.2f}); Kp max {c['kp_max']:.1f} in the 6 h before through the end of the event"
           + (f" (bin {fmt(c['kp_ts'])})" if c["kp_ts"] is not None else "")
           + f", Kp at onset {c['kp_start']:.1f}; Dst min {c['dst_min']:.0f} nT over the same span.")
    ev.append(sda)
    if c["advisories"]:
        ev.append("Advisories nearby: " + "; ".join(str(a_.get("text") or a_.get("type") or a_)[:80] for a_ in c["advisories"]))
    if c["rfi"]:
        ev.append("RFI reports: " + "; ".join(
            f"{fmt(r['_ts'])}" + (f", {r['_dist_km']:.1f} km from the machines" if "_dist_km" in r else "")
            + (" (within uncertainty)" if r["_within"] else " (too far)") for r in c["rfi"]))
    else:
        ev.append(f"No RFI report within [start, end+{p.rfi_window_min} min]; reports are delayed 10-30 min and can be missing, so absence is weak evidence.")
    if cls == "SPACE_WEATHER":
        ev.append("Attribution: every working machine lost C/N0 together" + (" while S4 is elevated" if s4["elevated"] else "")
                  + (f" and Kp reached {c['kp_max']:.1f}" if c["kp_max"] >= p.kp_high else "")
                  + "; a regional ionospheric cause fits. Kp led the farm impact where its bin precedes the onset.")
        ev.append("Ruled out: a single-machine fault (all machines affected); local jamming is not excluded and is reported "
                  "separately when a machine shows extra loss beyond the regional drop.")
    elif cls == "JAMMING":
        ev.append("Attribution: excess C/N0 loss beyond the regional level"
                  + (" with S4 quiet, which argues against scintillation" if not s4["elevated"] else ", S4 elevated so a storm contribution is possible")
                  + (", and a matching RFI report" if any(r["_within"] for r in c["rfi"]) else "") + ".")
        ev.append("Operator note: report to the appropriate authority; treat all affected machines as unreliable.")
    elif cls == "SPOOFING":
        t0 = c["stats"][tr[0]]
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
    elif cls == "MULTIPATH":
        m = c["mp"]
        ev.append(f"Attribution: brief dip ({(b - a).total_seconds():.0f} s) recurring {m['n_total']}x at the same location "
                  f"(within {p.mp_cell_m:.0f} m; {m['n_window']} in the evaluation window, e.g. "
                  + ", ".join(fmts(t) for t in m['times'][:5]) + "); no regional effect: benign reflection.")
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
    part = {"github_handle": handle}
    if display_name:
        part["display_name"] = display_name
    return {"participant": part, "repository_url": repo_url, "detections": dets}
