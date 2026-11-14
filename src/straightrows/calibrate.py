"""Fit detector thresholds on the labeled development window, defensively.

Only 12 labeled episodes exist, so a plain arg-max would overfit. Instead:
  * coordinate search over a coarse grid per parameter (2 sweeps);
  * pick the CENTER of the near-optimal plateau, not the single best value;
  * report sensitivity tables (how fast does the score fall off around the choice?);
  * optional 2-fold cross-validation split by day (calibrate on day 1, test day 2, and vice versa);
  * finally fit the onset bias (median signed onset error of matched episodes).
"""
from __future__ import annotations

import argparse
import copy
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DEV_END, DEV_START, Params
from .evaluate import score_window
from .io import load_all

GRID = {
    "cn0_drop_db": [2.5, 3.0, 4.0, 5.0, 6.0],
    "regional_drop_db": [2.0, 3.0, 4.0, 5.0],
    "min_signal_s": [120, 180, 240, 300],
    "gap_bridge_s": [30, 60, 120, 180],
    "xte_abs_m": [0.5, 0.75, 1.0, 1.5, 2.0],
    "xte_k_mad": [4.0, 6.0, 10.0],
    "xte_min_s": [120, 180, 300],
    "sats_drop": [2.0, 3.0, 4.0, 6.0],
    "min_cn0_dip_db": [8.0, 10.0, 12.0, 16.0],
    "dropout_density": [0.15, 0.3, 0.5],
    "s4_high": [0.2, 0.25, 0.3, 0.4],
    "smooth_s": [10, 30, 50],
}


def objective(res):
    tiou = [e.get("time_iou", 0.0) for e in res["episodes"] if e.get("correct")]
    return res["diagnostic_score"] + 0.5 * (np.mean(tiou) if tiou else 0.0) - 0.05 * len(res["false_positives"])


def evaluate(bundle, p, window, eps):
    p = copy.copy(p)
    p.max_detections = 10 ** 9          # never thin multipath while scoring (48 h dev would exceed the 495 cap)
    res, _, _ = score_window(bundle, p, window, eps)
    return objective(res), res


def calibrate(bundle, window, eps, p0: Params, sweeps=2, verbose=True):
    p = copy.deepcopy(p0)
    best, _ = evaluate(bundle, p, window, eps)
    table = {}
    for sweep in range(sweeps):
        for name, values in GRID.items():
            scores = []
            for v in values:
                q = copy.deepcopy(p)
                setattr(q, name, type(getattr(p, name))(v))
                scores.append(evaluate(bundle, q, window, eps)[0])
            table[name] = dict(zip(values, np.round(scores, 2)))
            top = max(scores)
            plateau = [v for v, s in zip(values, scores) if s >= top - 0.25]      # near-optimal set
            choice = float(np.median(plateau)) if len(plateau) > 1 else plateau[0]
            choice = min(values, key=lambda v: abs(v - choice))                   # snap to a grid value
            if scores[values.index(choice)] >= top - 0.25:
                setattr(p, name, type(getattr(p, name))(choice))
            else:
                setattr(p, name, type(getattr(p, name))(values[int(np.argmax(scores))]))
            if verbose:
                print(f"sweep {sweep} {name:18} -> {getattr(p, name)!s:6} (plateau {plateau}, best {top:.2f})")
    score, res = evaluate(bundle, p, window, eps)
    return p, table, res


def fit_onset_bias(bundle, window, eps, p):
    _, res = evaluate(bundle, p, window, eps)
    errs = [e["onset_error_s"] for e in res["episodes"] if e.get("correct") and e["truth_class"] != "MULTIPATH"]
    if len(errs) >= 4:
        shift = float(np.clip(-np.median(errs), -300, 300))
        # only adopt if it actually helps (guards against fitting noise)
        q = copy.deepcopy(p); q.onset_shift_s = shift
        if evaluate(bundle, q, window, eps)[0] > evaluate(bundle, p, window, eps)[0] + 0.05:
            return q
    return p


def sensitivity(bundle, window, eps, p, rel=0.25):
    """Score when each numeric parameter is perturbed by +/- rel. Large drops = fragile parameter."""
    base, _ = evaluate(bundle, p, window, eps)
    rows = []
    for name in GRID:
        v = getattr(p, name)
        out = []
        for f in (1 - rel, 1 + rel):
            q = copy.deepcopy(p)
            setattr(q, name, type(v)(v * f))
            out.append(evaluate(bundle, q, window, eps)[0] - base)
        rows.append((name, v, out[0], out[1]))
    return base, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="params.json")
    ap.add_argument("--cv", action="store_true", help="2-fold day-split cross-validation report first")
    ap.add_argument("--sweeps", type=int, default=2)
    a = ap.parse_args()
    b = load_all(a.data)
    p0 = Params()
    day = pd.Timestamp("2026-05-05T00:00:00Z")
    if a.cv:
        d1 = [e for e in b.train if pd.Timestamp(e["start"]) < day]
        d2 = [e for e in b.train if pd.Timestamp(e["start"]) >= day]
        for name, tr, te, wtr, wte in (("fit day1 -> test day2", d1, d2, (DEV_START, day), (day, DEV_END)),
                                       ("fit day2 -> test day1", d2, d1, (day, DEV_END), (DEV_START, day))):
            if not tr or not te:
                continue
            p, _, _ = calibrate(b, wtr, tr, p0, sweeps=1, verbose=False)
            s_def, _ = evaluate(b, p0, wte, te)
            s_fit, _ = evaluate(b, p, wte, te)
            print(f"CV {name}: held-out score default {s_def:.2f} vs calibrated {s_fit:.2f}  ({len(tr)} fit / {len(te)} test episodes)")
    p, table, res = calibrate(b, (DEV_START, DEV_END), b.train, p0, a.sweeps)
    p = fit_onset_bias(b, (DEV_START, DEV_END), b.train, p)
    p.to_json(a.out)
    base, rows = sensitivity(b, (DEV_START, DEV_END), b.train, p)
    print(f"\ncalibrated dev objective {base:.2f}; params written to {a.out}\nsensitivity (+/-25%):")
    for n, v, lo, hi in rows:
        print(f"  {n:18} {v!s:7} -25%: {lo:+.2f}  +25%: {hi:+.2f}")
    Path(a.out).with_suffix(".sens.json").write_text(json.dumps({"table": {k: {str(x): float(y) for x, y in v.items()} for k, v in table.items()},
                                                                  "sensitivity": [list(r) for r in rows]}, indent=1))


if __name__ == "__main__":
    main()
