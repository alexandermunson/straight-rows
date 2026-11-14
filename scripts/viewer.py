#!/usr/bin/env python3
"""Straight Rows investigation viewer: fast, browser-rendered (canvas), no matplotlib in the loop.

    python scripts/viewer.py                 # serves http://127.0.0.1:8765 and opens it
    python scripts/viewer.py --port 9000 --no-open
    python scripts/viewer.py --submission submission.json --refresh

Why it is fast: Python only precomputes three resolutions (1 s, 10 s, 60 s) once and serves them as raw float32 binary.
The page keeps them in memory and draws min/max-per-pixel envelopes on <canvas>, so pan/zoom/hover never touch Python.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import pandas as pd

from gui_matplotlib import FIX_RANK, TRACTORS, Data  # data + detector loading is shared with the snapshot tool

CHANNELS = ["cn0", "mincn0", "xte", "sats", "fix", "link", "lat", "lon", "spd"]   # order is a contract with index.html
LAT0, LON0 = 46.9, -97.2
STATIC = Path(__file__).resolve().parent / "viewer_static"
READY = threading.Event()          # set by the page in ?test mode once everything has been drawn


def build_levels(d: Data) -> dict[int, bytes]:
    """Per resolution: for tractor, for channel, float32[n] (bin start = i*res seconds after the epoch)."""
    total = int((d.t_max - d.t_min).total_seconds())
    out = {}
    for res in (1, 10, 60):
        n = total // res
        idx = pd.date_range(d.t_min, periods=n, freq=f"{res}s")
        parts = []
        for tr in TRACTORS:
            f = d.frames[res][tr].reindex(idx)
            for ch in CHANNELS:
                v = f[ch].to_numpy(dtype=np.float64)
                if ch == "lat":
                    v = v - LAT0
                elif ch == "lon":
                    v = v - LON0
                parts.append(v.astype("<f4"))
        out[res] = np.concatenate(parts).tobytes()
    return out


def build_meta(d: Data) -> dict:
    ep = d.t_min
    sec = lambda ts: float((naive_ts(ts) - ep).total_seconds())
    events = []
    for e in d.events:
        events.append(dict(kind=e["kind"], id=e["id"], cls=e["cls"], tr=e["tractors"], s=sec(e["start"]), e=sec(e["end"]),
                           conf=e["conf"], action=e.get("action", ""), ev=e["evidence"],
                           why=[list(x) for x in e.get("reasoning", [])], src=e.get("source"), ops=e.get("ops")))
    # confidence vs outcome on the labelled days: a detection is right when a labelled episode of the same class overlaps it
    # (time IoU >= 0.30, tractor IoU >= 0.50, the scorer's matching thresholds)
    def right(dv):
        for tv in d.truth:
            if tv["cls"] != dv["cls"]:
                continue
            inter = (min(dv["end"], tv["end"]) - max(dv["start"], tv["start"])).total_seconds()
            union = (max(dv["end"], tv["end"]) - min(dv["start"], tv["start"])).total_seconds()
            ts_, ds_ = set(tv["tractors"]), set(dv["tractors"])
            if union > 0 and inter / union >= 0.30 and len(ts_ & ds_) / len(ts_ | ds_) >= 0.50:
                return True
        return False
    dev_end = max((tv["end"] for tv in d.truth), default=ep)
    bands, calib = [(0.0, 0.5, "below 0.50"), (0.5, 0.7, "0.50 to 0.69"), (0.7, 0.85, "0.70 to 0.84"), (0.85, 1.01, "0.85 and above")], []
    scored = [dv for dv in d.events if dv["kind"] == "DET" and dv["start"] < dev_end + pd.Timedelta(hours=2)]
    for lo, hi, name in bands:
        rows = [dv for dv in scored if lo <= dv["conf"] < hi]
        calib.append(dict(band=name, n=len(rows), right=sum(right(dv) for dv in rows)))
    series = lambda df, col: [[sec(t), float(v)] for t, v in zip(df["ts"], df[col])]
    fl = lambda x: float(x) if x is not None and np.isfinite(x) else None
    rfi = []
    if len(d.rfi):
        for _, r in d.rfi.iterrows():
            rfi.append(dict(id=str(r.get("id", "")), t=sec(r["ts"]), conf=float(r.get("confidence", 0) or 0),
                            lat=fl(r.get("centerLat", np.nan)), lon=fl(r.get("centerLon", np.nan)),
                            a=float(r.get("semiMajor_km", 1) or 1), b=float(r.get("semiMinor_km", 1) or 1),
                            text=str(r.get("text", ""))))
    adv = [dict(t=sec(naive_ts(a.get("ts"))), type=str(a.get("type", "ADV")), text=str(a.get("text", ""))) for a in d.b.advisories]
    allg = pd.concat([d.frames[60][tr] for tr in TRACTORS])
    dst = d.b.dst
    return dict(
        epoch=ep.isoformat() + "Z", total=float((d.t_max - d.t_min).total_seconds()), tractors=TRACTORS, channels=CHANNELS,
        lat0=LAT0, lon0=LON0, events=events,
        mp=[[TRACTORS.index(tr), sec(s), sec(e)] for tr, s, e in d.mp], mp_lat=(None if d.mp_lat is None else d.mp_lat),
        kp=series(d.kp, "kp"), s4=series(d.s4, "s4"),
        dst=([[sec(t), float(v)] for t, v in zip(dst["ts"], dst["dst"])] if len(dst) else []),
        rfi=rfi, adv=adv, base=d.base, calib=calib,
        field=dict(lat=[float(allg.lat.min()), float(allg.lat.max())], lon=[float(allg.lon.min()), float(allg.lon.max())]),
        fix_names=[k for k, _ in sorted(FIX_RANK.items(), key=lambda kv: kv[1])],
    )


def naive_ts(x):
    from gui_matplotlib import naive
    return naive(x)


def make_handler(meta_bytes, levels):
    html = (STATIC / "index.html").read_bytes

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                READY.clear()
                self._send(200, "text/html; charset=utf-8", html())
            elif path == "/meta.json":
                self._send(200, "application/json", meta_bytes)
            elif path.startswith("/lvl/") and path.endswith(".bin"):
                try:
                    self._send(200, "application/octet-stream", levels[int(path[5:-4])])
                except (KeyError, ValueError):
                    self._send(404, "text/plain", b"no such level")
            elif path == "/ready.png":                     # test mode: hold the page's load event until it has rendered
                READY.wait(timeout=60)
                png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
                self._send(200, "image/png", png)
            else:
                self._send(404, "text/plain", b"not found")

        def do_POST(self):
            if self.path == "/perf":
                n = int(self.headers.get("Content-Length", 0))
                print("PERF\n" + self.rfile.read(n).decode(), flush=True)
                self._send(200, "text/plain", b"ok")
            elif self.path == "/ready":
                READY.set()
                self._send(200, "text/plain", b"ok")
            else:
                self._send(404, "text/plain", b"not found")

    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--submission", help="show detections from this submission.json instead of running the detector")
    ap.add_argument("--refresh", action="store_true", help="re-run the detector instead of using outputs/gui_cache.pkl")
    a = ap.parse_args()

    print("loading data...", flush=True)
    d = Data(a.data, a.refresh, a.submission)
    print("building multi-resolution levels...", flush=True)
    levels = build_levels(d)
    meta = json.dumps(build_meta(d), allow_nan=False).encode()
    print(f"ready: {len(meta) / 1e6:.1f} MB meta, levels " + ", ".join(f"{r}s={len(b) / 1e6:.1f} MB" for r, b in levels.items()), flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(meta, levels))
    url = f"http://127.0.0.1:{a.port}/"
    print(f"serving {url}   (Ctrl+C to stop)", flush=True)
    if not a.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
