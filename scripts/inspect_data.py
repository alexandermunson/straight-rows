#!/usr/bin/env python3
"""FIRST thing to run at kickoff: dump the real schema/ranges of every file so loaders and thresholds can be checked.

    python scripts/inspect_data.py --data data
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pandas as pd

ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data"); a = ap.parse_args()
d = Path(a.data)
for f in sorted(d.rglob("*.json")):
    obj = json.loads(f.read_text())
    print(f"\n=== {f.relative_to(d)}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list):
                print(f"  key '{k}': list[{len(v)}]; first: {json.dumps(v[0])[:300] if v else None}")
            else:
                print(f"  key '{k}': {json.dumps(v)[:200]}")
    else:
        print(f"  list[{len(obj)}]; first: {json.dumps(obj[0])[:300]}")
t = pd.read_csv(d / "farm/tractor_telemetry.csv.gz", nrows=5)
print("\n=== telemetry head\n", t.to_string())
full = pd.read_csv(d / "farm/tractor_telemetry.csv.gz", parse_dates=["ts"])
print("\nrows", len(full), "| tractors", sorted(full.tractor_id.unique()), "| ts", full.ts.min(), "->", full.ts.max())
print(full.describe().T.to_string())
for c in ("fix_state", "base_link_ok"):
    print(f"\n{c}:\n{full[c].value_counts().to_string()}")
w = full[full.speed_mps > 1]
print("\nworking-time per-tractor medians:\n", w.groupby("tractor_id")[["mean_cn0_dbhz", "min_cn0_dbhz", "num_sats", "cross_track_error_m"]].median().to_string())
try:
    from straightrows import io
    b = io.load_all(d)
    print(f"\nLOADER OK: kp {len(b.kp)} dst {len(b.dst)} s4 {len(b.s4)} adv {len(b.advisories)} rfi {len(b.rfi)} train {len(b.train)}")
    print("RFI columns:", list(b.rfi.columns)); print("train labels:")
    for e in b.train: print("  ", e)
except Exception as e:
    print("\n!!! LOADER FAILED - fix src/straightrows/io.py:", repr(e))
