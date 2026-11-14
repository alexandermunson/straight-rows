#!/usr/bin/env python3
"""Demo figure for a detection or a labeled episode: signal health, XTE, sats, link, with SDA strip.

    python scripts/plot_episode.py --data data --start 2026-05-06T10:00Z --end 2026-05-06T11:00Z --out fig.png
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from straightrows import io

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="data"); ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
ap.add_argument("--pad-min", type=int, default=30); ap.add_argument("--out", default="episode.png")
ap.add_argument("--submission", help="overlay detections from this submission.json")
a = ap.parse_args()
b = io.load_all(a.data)
s, e = pd.Timestamp(a.start), pd.Timestamp(a.end)
s = s.tz_localize("UTC") if s.tzinfo is None else s
e = e.tz_localize("UTC") if e.tzinfo is None else e
lo, hi = s - pd.Timedelta(minutes=a.pad_min), e + pd.Timedelta(minutes=a.pad_min)
seg = b.tele[(b.tele.ts >= lo) & (b.tele.ts < hi)]
fig, ax = plt.subplots(5, 1, figsize=(12, 10), sharex=True)
for tr, g in seg.groupby("tractor_id"):
    g = g.set_index("ts").resample("10s").agg({"mean_cn0_dbhz": "mean", "min_cn0_dbhz": "min", "cross_track_error_m": "mean", "num_sats": "mean", "base_link_ok": "mean"})
    ax[0].plot(g.index, g.mean_cn0_dbhz, lw=.9, label=tr); ax[1].plot(g.index, g.min_cn0_dbhz, lw=.9)
    ax[2].plot(g.index, g.cross_track_error_m.abs(), lw=.9); ax[3].plot(g.index, g.num_sats, lw=.9)
    ax[4].plot(g.index, g.base_link_ok + 0.02 * "T1 T2 T3".split().index(tr), lw=.9)
for x, lab in zip(ax, ["mean C/N0", "min C/N0", "|XTE| m", "sats", "link ok"]):
    x.set_ylabel(lab); x.axvspan(s, e, color="orange", alpha=.15)
ax[0].legend(ncols=3)
k, s4 = b.kp[(b.kp.ts >= lo - pd.Timedelta(hours=6)) & (b.kp.ts < hi)], b.s4[(b.s4.ts >= lo) & (b.s4.ts < hi)]
ax2 = ax[0].twinx(); ax2.step(s4.ts, s4.s4, color="purple", alpha=.5, where="post"); ax2.set_ylabel("S4", color="purple")
ax[0].set_title(f"{s:%Y-%m-%d %H:%M} - {e:%H:%M}Z   Kp: " + ", ".join(f"{t:%H}h={v:.0f}" for t, v in zip(k.ts, k.kp)))
if a.submission:
    for d in json.load(open(a.submission))["detections"]:
        ds, de = pd.Timestamp(d["start"]), pd.Timestamp(d["end"])
        if de > lo and ds < hi:
            ax[1].axvspan(ds, de, color="green", alpha=.15); ax[1].text(ds, ax[1].get_ylim()[0] + 1, f" {d['class']}", fontsize=8)
ax[0].set_xlim(lo, hi)
plt.tight_layout(); plt.savefig(a.out, dpi=110); print("wrote", a.out)
