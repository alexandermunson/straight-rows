#!/usr/bin/env python3
"""Threshold-stability report: perturb every numeric threshold by +/-25% and report what moves.

    python scripts/stability.py                # writes docs/STABILITY.md (takes ~10-25 min; runs in parallel)
    python scripts/stability.py --rel 0.5 --params cn0_drop_db,jam_step_db

Why: the labelled dev window has only 12 episodes, so a 40/40 dev score says little about robustness. For each parameter
and direction this reports (a) the official diagnostic score and false positives on dev, and (b) how the evaluation-day
detection list changes (episodes lost, added, or re-classified). No labels exist for the eval day, so (b) is a stability
measure, NOT an accuracy measure. Reuses calibrate.GRID and score_window; changes no thresholds.
"""
import argparse
import copy
import multiprocessing as mp
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from straightrows.calibrate import GRID
from straightrows.config import DEV_END, DEV_START, EVAL_END, EVAL_START, Params
from straightrows.detect import Detector
from straightrows.evaluate import score_window
from straightrows.io import load_all

EXTRA = ["jam_step_db", "jam_track_r2", "mp_dip_db", "mp_dip_max_db", "link_gap_s", "kp_lead_h", "uniform_spread_db",
         "link_bad_frac", "xte_cn0_ok_db", "merge_gap_s"]
B = None            # bundle shared with forked workers


def eval_list(p):
    cs = Detector(B, p, (EVAL_START, EVAL_END)).run()
    real = [(c["cls"], tuple(c["tractors"]), pd.Timestamp(c["start"]), pd.Timestamp(c["end"])) for c in cs if c["cls"] != "MULTIPATH"]
    return real, sum(c["cls"] == "MULTIPATH" for c in cs)


def run_one(args):
    name, factor, base_val = args
    p = Params()
    if name is not None:
        setattr(p, name, type(base_val)(base_val * factor))
    p.max_detections = 10 ** 9
    res, _, _ = score_window(B, p, (DEV_START, DEV_END), B.train)
    real, nmp = eval_list(p)
    return dict(name=name, factor=factor, value=None if name is None else getattr(p, name), score=res["diagnostic_score"],
                fp=len(res["false_positives"]), missed=[e["episode"] for e in res["episodes"] if not e["matched"]], real=real, nmp=nmp)


def overlap(a, b):
    return max(0.0, (min(a[3], b[3]) - max(a[2], b[2])).total_seconds())


def compare(base, new):
    """Match detections by time overlap (>= 50% of the shorter one). Returns (lost, flipped, added) as short strings."""
    lost, flipped, used = [], [], set()
    for x in base:
        best = max(range(len(new)), key=lambda i: overlap(x, new[i]), default=None)
        dur = min((x[3] - x[2]).total_seconds(), (new[best][3] - new[best][2]).total_seconds()) if best is not None else 1
        if best is None or overlap(x, new[best]) < 0.5 * dur:
            lost.append(f"{x[0][:5]} {x[2]:%H:%M}")
        else:
            used.add(best)
            if new[best][0] != x[0]:
                flipped.append(f"{x[2]:%H:%M} {x[0][:5]}->{new[best][0][:5]}")
    added = [f"{n[0][:5]} {n[2]:%H:%M}" for i, n in enumerate(new) if i not in used]
    return lost, flipped, added


def main():
    global B
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--rel", type=float, default=0.25)
    ap.add_argument("--params", help="comma list; default = calibrate.GRID plus the jam/multipath/link/Kp/uniformity parameters")
    ap.add_argument("--workers", type=int, default=min(8, max(1, (mp.cpu_count() or 2) - 1)))
    ap.add_argument("--out", default=str(ROOT / "docs" / "STABILITY.md"))
    a = ap.parse_args()
    B = load_all(a.data)
    names = a.params.split(",") if a.params else list(dict.fromkeys(list(GRID) + EXTRA))
    p0 = Params()
    jobs = [(None, 1.0, None)] + [(n, f, getattr(p0, n)) for n in names for f in (1 - a.rel, 1 + a.rel)]
    print(f"{len(jobs)} runs on {a.workers} workers...", flush=True)
    with ProcessPoolExecutor(a.workers, mp_context=mp.get_context("fork")) as ex:
        out = []
        for i, r in enumerate(ex.map(run_one, jobs), 1):
            out.append(r)
            print(f"  {i}/{len(jobs)} {r['name']} x{r['factor']:.2f}: dev {r['score']:.2f}", flush=True)
    base, rest = out[0], out[1:]
    rows, fragile = [], []
    for r in rest:
        lost, flipped, added = compare(base["real"], r["real"])
        d_dev = r["score"] - base["score"]
        eval_txt = "same" if not (lost or flipped or added) else "; ".join(
            [f"LOST {', '.join(lost)}" for _ in [0] if lost] + [f"FLIP {', '.join(flipped)}" for _ in [0] if flipped] + [f"ADDED {', '.join(added)}" for _ in [0] if added])
        rows.append((r["name"], r["factor"], r["value"], d_dev, r["fp"], r["missed"], eval_txt, r["nmp"] - base["nmp"]))
        if d_dev <= -1.0 or lost or flipped or added or r["fp"] > base["fp"]:
            fragile.append(rows[-1])
    lines = [f"# Threshold stability (+/-{int(a.rel * 100)}%)", "",
             f"Baseline (defaults): dev {base['score']:.2f}/40, {base['fp']} false positives, missed {base['missed'] or 'none'}; "
             f"eval {len(base['real'])} non-multipath detections + {base['nmp']} multipath.", "",
             "Dev is scored with the official diagnostic scorer. The eval column compares the detection LIST with the baseline list "
             "(no eval labels exist, so this measures stability, not accuracy). Multipath count is the change in recurring-pass reports.", "",
             "| parameter | factor | value | dev score change | dev FP | dev missed | eval list vs baseline | multipath count change |", "|---|---|---|---|---|---|---|---|"]
    for n, f, v, d, fp, miss, ev, dm in rows:
        lines.append(f"| {n} | x{f:.2f} | {v} | {d:+.2f} | {fp} | {', '.join(miss) or '-'} | {ev} | {dm:+d} |")
    lines += ["", "## Flagged (dev score drop >= 1.0, a new dev false positive, or any change to the eval detection list)", ""]
    lines += [f"- `{n}` x{f:.2f} ({v}): dev {d:+.2f}, FP {fp}, eval: {ev}" for n, f, v, d, fp, miss, ev, dm in fragile] or ["- none"]
    Path(a.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines[-(len(fragile) + 4):]))
    print(f"\nwritten {a.out}")


if __name__ == "__main__":
    main()
