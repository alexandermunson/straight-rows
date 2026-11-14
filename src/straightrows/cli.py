"""python -m straightrows.cli {run,dev,inspect}  — see README."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from .config import DEV_END, DEV_START, EVAL_END, EVAL_START, Params
from .evaluate import calibration_table, report, score_window
from .io import load_all
from .report import to_submission
from .detect import Detector


def _params(a):
    """Defaults (config.Params) unless --params names a file. A named file that does not exist is an error, not a fallback."""
    if not a.params:
        print("params: built-in defaults (src/straightrows/config.py)")
        return Params()
    if not Path(a.params).exists():
        sys.exit(f"params file not found: {a.params} (omit --params to use the built-in defaults)")
    print(f"params: {a.params}")
    return Params.from_json(a.params)


def cmd_dev(a):
    b = load_all(a.data)
    p = _params(a)
    p.max_detections = 10 ** 9          # dev spans 48 h: do not thin the recurring multipath reports when scoring
    res, sub, _ = score_window(b, p, (DEV_START, DEV_END), b.train)
    report(res, "development window (labeled)")
    calibration_table(res, sub)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2, default=str))


def cmd_run(a):
    b = load_all(a.data)
    p = _params(a)
    cands = Detector(b, p, (EVAL_START, EVAL_END)).run()
    sub = to_submission(cands, p, a.github_handle, a.repo_url, a.display_name)
    Path(a.output).write_text(json.dumps(sub, indent=2) + "\n")
    print(f"wrote {len(sub['detections'])} detections to {a.output}")
    for d in sub["detections"]:
        print(f"  {d['start'][11:19]}-{d['end'][11:19]}  {d['class']:16} {','.join(d['tractors']):9} conf {d['confidence']:.2f}  {d['action']}")
    v = Path(__file__).resolve().parents[2] / "scripts/validate_submission.py"
    sys.exit(subprocess.run([sys.executable, str(v), a.output]).returncode)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(required=True)
    d = sp.add_parser("dev"); d.add_argument("--data", default="data"); d.add_argument("--params"); d.add_argument("--json")
    d.set_defaults(f=cmd_dev)
    r = sp.add_parser("run"); r.add_argument("--data", default="data"); r.add_argument("--params")
    r.add_argument("--output", default="submission.json"); r.add_argument("--github-handle", required=True)
    r.add_argument("--repo-url", required=True); r.add_argument("--display-name"); r.set_defaults(f=cmd_run)
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
