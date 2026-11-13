"""Run the detector on a window and score it with the official diagnostic (copied from the organizers' repo)."""
from __future__ import annotations

import json
import sys

import pandas as pd

from . import official_scorer as scorer
from .config import ACTION, DEV_END, DEV_START, EVAL_END, EVAL_START, Params
from .detect import Detector
from .report import to_submission


def run_window(bundle, p, window):
    return Detector(bundle, p, window).run()


def score_window(bundle, p, window, episodes, repo="https://github.com/x/y"):
    cands = run_window(bundle, p, window)
    sub = to_submission(cands, p, "x", repo)
    key = {"episodes": [{**e, "recommended_action": e.get("recommended_action", ACTION.get(e["class"]))} for e in episodes]}
    return scorer.score(sub, key), sub, cands


def report(res, name=""):
    print(f"--- {name}")
    for ep in res["episodes"]:
        st = ep.get("predicted_class", "MISSED")
        err = f'{ep["onset_error_s"] / 60:+.1f} min' if "onset_error_s" in ep else "-"
        print(f'{ep["episode"]:8} {ep["truth_class"]:16} -> {st:16} onset {err:>9}  tIoU {ep.get("time_iou", 0):.2f} trIoU {ep.get("tractor_iou", 0):.2f}')
    for c, m in res["class_metrics"].items():
        print(f'  {c:16} P={m["precision"]:.2f} R={m["recall"]:.2f} F1={m["f1"]:.2f}')
    print(f'DIAGNOSTIC {res["diagnostic_score"]:.2f}/40 | macroF1 {res["macro_f1"]:.3f} | timing {res["mean_timing_credit"]:.3f} '
          f'| action agree {res["action_agreement"]}/{res["episode_count"]} | FPs {len(res["false_positives"])} '
          f'| benign multipath {res["benign_multipath_reports"]}')
    for fp in res["false_positives"]:
        print(f'  FP: {fp["class"]} {fp["tractors"]} {fp["start"]} -> {fp["end"]}')


def calibration_table(res, sub):
    """Confidence vs outcome for matched detections + Brier score (lower is better; 0.25 = always saying 0.5)."""
    by = {(d["start"], d["class"]): d["confidence"] for d in sub["detections"]}
    rows = []
    for e in res["episodes"]:
        if e["matched"]:
            conf = by.get((e["start"], e["predicted_class"]))
            if conf is not None:
                rows.append((e["episode"], e["predicted_class"], conf, float(e["correct"])))
    if rows:
        brier = sum((c - y) ** 2 for _, _, c, y in rows) / len(rows)
        print(f"confidence check on {len(rows)} matched detections: Brier {brier:.3f}; "
              f"mean conf {sum(r[2] for r in rows) / len(rows):.2f} vs hit rate {sum(r[3] for r in rows) / len(rows):.2f}")
    return rows
