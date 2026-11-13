"""Synthetic stand-in for the kickoff dataset, in the schema the guide describes.

FOR PIPELINE TESTING ONLY. Physics and magnitudes are guesses; the real data will differ.
Writes <out>/data/{farm,sda,labels} and <out>/eval_key.json (answer key in the scorer's format).

    python -m straightrows.synth --out outputs/synth
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ACTION, EPOCH, TRACTORS

LAT0, LON0 = 46.9, -97.2
M_LAT = 111_320.0
M_LON = M_LAT * np.cos(np.radians(LAT0))
ROW_LEN, ROW_SP, N_ROWS = 800.0, 6.0, 12
MP_SPOT = dict(tractor="T2", x_min=650.0, y_max=40.0)

H = lambda h: EPOCH + pd.Timedelta(hours=h)

# (id, class, tractors, start_h, end_h, extra)
DEV = [
    ("TRN-01", "JAMMING", ["T1"], 13.0, 13.6, {"rfi": True}),
    ("TRN-02", "EQUIPMENT_FAULT", ["T2"], 16.2, 16.6, {"mode": "link"}),
    ("TRN-03", "SPOOFING", ["T3"], 19.0, 19.7, {}),
    ("TRN-04", "MULTIPATH", ["T2"], 21.0, 21.03, {}),
    ("TRN-05", "JAMMING", ["T3"], 15.2, 15.5, {"rfi": False}),
    ("TRN-06", "SPOOFING", ["T2"], 26.5, 27.0, {}),
    ("TRN-07", "EQUIPMENT_FAULT", ["T1"], 22.0, 22.5, {"mode": "antenna"}),
    ("TRN-08", "SPACE_WEATHER", ["T1", "T2", "T3"], 37.0, 43.0, {}),
    ("TRN-09", "EQUIPMENT_FAULT", ["T3"], 39.0, 39.4, {"mode": "antenna"}),   # inside the storm
    ("TRN-10", "SPOOFING", ["T1"], 40.5, 41.2, {}),                            # inside the storm
    ("TRN-11", "JAMMING", ["T1", "T2"], 44.5, 45.0, {"rfi": True}),
    ("TRN-12", "MULTIPATH", ["T2"], 35.5, 35.53, {}),
]
EVAL = [
    ("EVA-1", "JAMMING", ["T1"], 49.5, 50.0, {"rfi": True}),
    ("EVA-2", "SPOOFING", ["T2"], 59.5, 60.2, {}),
    ("EVA-3", "SPACE_WEATHER", ["T1", "T2", "T3"], 60.5, 64.0, {}),
    ("EVA-4", "JAMMING", ["T2", "T3"], 62.0, 62.6, {"rfi": True}),             # inside the storm
    ("EVA-5", "SPOOFING", ["T1"], 65.0, 65.8, {}),
    ("EVA-6", "EQUIPMENT_FAULT", ["T2"], 66.0, 66.4, {"mode": "link"}),
    ("EVA-7", "MULTIPATH", ["T2"], 68.0, 68.03, {}),
    ("EVA-8", "EQUIPMENT_FAULT", ["T3"], 70.5, 71.0, {"mode": "antenna"}),
]
MP_WINDOWS = [(20.5, 24.0), (35.2, 36.8), (67.5, 70.0)]   # when the obstruction reflects


def _working(hours_of_day):
    return (hours_of_day >= 11) | (hours_of_day < 3)


def _smooth_noise(rng, n, sigma, tau=30):
    e = rng.normal(0, 1, n)
    k = np.exp(-np.arange(0, 4 * tau) / tau)
    x = np.convolve(e, k / np.sqrt((k ** 2).sum()), mode="same")
    return x * sigma


def _path(n, offset_s):
    """Boustrophedon path (x,y metres) at 4.5 m/s; returns x, y."""
    d = (np.arange(n) + offset_s) * 4.5
    row_len_total = ROW_LEN
    idx = (d // row_len_total).astype(int) % N_ROWS
    along = d % row_len_total
    x = np.where(idx % 2 == 0, along, row_len_total - along)
    return x, idx * ROW_SP


def make(seed=7, hard=False):
    E, NZ = (0.6, 1.6) if hard else (1.0, 1.0)   # effect-size and noise multipliers
    rng = np.random.default_rng(seed)
    n = 72 * 3600
    t = pd.date_range(EPOCH, periods=n, freq="1s")
    sec = np.arange(n)
    hod = (sec / 3600.0) % 24
    work = _working(hod)
    eps = DEV + EVAL

    # --- SDA ---
    kp_t = pd.date_range(EPOCH, periods=24, freq="3h")
    kp = np.clip(rng.normal(1.7, 0.5, 24).round(1), 0, 3)
    dst_t = pd.date_range(EPOCH, periods=72, freq="1h")
    dst = rng.normal(-8, 4, 72).round(0)
    s4_t = pd.date_range(EPOCH, periods=72 * 4, freq="15min")
    s4 = np.clip(rng.normal(0.08, 0.02, len(s4_t)), 0.02, None)
    for (_, cls, _, a, b, _) in [e for e in eps if e[1] == "SPACE_WEATHER"]:
        for i, ts in enumerate(kp_t):
            h = (ts - EPOCH).total_seconds() / 3600
            if a - 4 <= h < b + 3:   # Kp leads farm impact
                kp[i] = [5.0, 6.7, 7.0, 6.0, 5.3, 4.7][min(int((h - (a - 4)) // 3), 5)]
        for i, ts in enumerate(dst_t):
            h = i
            if a - 3 <= h <= b + 6:
                dst[i] = -110 * np.exp(-((h - (a + 1)) / 3.5) ** 2) - 8
        for i, ts in enumerate(s4_t):
            h = i / 4
            if a - 0.5 <= h <= b + 0.5:   # S4 lags Kp but tracks farm impact
                s4[i] = 0.5 + rng.normal(0, 0.05)
    rfi = []
    for (eid, cls, trs, a, b, ex) in eps:
        if cls == "JAMMING" and ex.get("rfi"):
            rfi.append({"id": f"RFI-{eid}", "ts": (H(a) + pd.Timedelta(minutes=int(rng.integers(10, 30)))).isoformat(),
                        "lat": LAT0 + rng.normal(0, 0.01), "lon": LON0 + rng.normal(0, 0.01),
                        "uncertainty_km": 4.0, "band": "L1"})
    rfi.append({"id": "RFI-FALSE", "ts": H(18.0).isoformat(), "lat": LAT0 + 0.2, "lon": LON0 - 0.3,
                "uncertainty_km": 9.0, "band": "L1"})
    adv = [{"id": "ADV-1", "ts": H(5).isoformat(), "type": "MAINTENANCE", "text": "Routine satellite clock maintenance"},
           {"id": "ADV-2", "ts": H(33).isoformat(), "type": "SPACE_WEATHER", "text": "Geomagnetic storm watch"}]

    rows = []
    mp_actual = {}
    for ti, tr in enumerate(TRACTORS):
        x, y = _path(n, ti * 240)
        cn0 = 44 + ti * 0.4 + _smooth_noise(rng, n, 0.6 * NZ)
        mn = cn0 - 7 + _smooth_noise(rng, n, 0.5)
        sats = np.clip(np.round(15 + _smooth_noise(rng, n, 0.7)), 10, 20)
        xte = rng.normal(0, 0.04 * NZ, n)
        link = np.ones(n, bool)
        fix = np.full(n, "RTK_FIXED", dtype=object)
        dx = np.zeros(n); dy = np.zeros(n)

        def window(a, b):
            m = np.zeros(n, bool)
            m[int(a * 3600):int(b * 3600)] = True
            return m

        for (eid, cls, trs, a, b, ex) in eps:
            if cls == "MULTIPATH" or (tr not in trs):
                if cls != "SPACE_WEATHER":
                    continue
            m = window(a, b)
            L = m.sum()
            if not L:
                continue
            ramp = np.clip((sec - a * 3600) / 600.0, 0, 1)          # 10-minute ramp
            if cls == "SPACE_WEATHER" and tr in trs:
                cn0 -= 7.5 * E * ramp * m + rng.normal(0, 0.8 * NZ, n) * m
                mn -= 9 * E * ramp * m
                sats -= 3 * E * ramp * m
                xte += rng.normal(0, 0.25 * NZ, n) * m
                fix[m & (rng.random(n) < 0.4)] = "RTK_FLOAT"
            elif cls == "JAMMING":
                cn0 -= 11 * E * m + rng.normal(0, 1.0 * NZ, n) * m
                mn -= 13 * E * m
                sats -= 4 * E * m
                xte += rng.normal(0, 0.3, n) * m
                fix[m] = "AUTONOMOUS"
            elif cls == "SPOOFING":
                prog = np.clip((sec - a * 3600) / ((b - a) * 3600), 0, 1) * m
                xte += 2.6 * E * prog + rng.normal(0, 0.05 * NZ, n) * m
                dx += 18 * prog; dy += 11 * prog
            elif cls == "EQUIPMENT_FAULT":
                if ex.get("mode") == "link":
                    link &= ~m
                    fix[m] = "DGPS"
                    xte += rng.normal(0, 0.4, n) * m
                else:
                    drop = m & (rng.random(n) < 0.25)
                    sats -= 5 * E * drop
                    mn -= 20 * E * drop
                    cn0 -= 2.5 * E * m
                    xte += rng.normal(0, 0.15, n) * m
        if tr == MP_SPOT["tractor"]:
            spot = (x > MP_SPOT["x_min"]) & (y < MP_SPOT["y_max"])
            for (a, b) in MP_WINDOWS:
                m = window(a, b) & spot
                cn0 -= 6 * E * m; mn -= 14 * E * m; xte += rng.normal(0, 0.2, n) * m
            allm = np.zeros(n, bool)
            for (a, b) in MP_WINDOWS:
                allm |= window(a, b) & spot
            d = np.diff(np.concatenate([[0], allm.view(np.int8), [0]]))
            runs = list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))
            for e in eps:
                if e[1] == "MULTIPATH":
                    r = next((r for r in runs if r[0] >= e[3] * 3600), None)
                    mp_actual[e[0]] = (r[0] / 3600, r[1] / 3600)
        cn0 = np.where(work, cn0, cn0 + 0.5)
        sats = np.clip(np.round(sats), 4, 24)
        xte = np.where(work, xte, 0.0)
        speed = np.where(work, 4.5, 0.0)
        xs = np.where(work, x, x[0]) + dx
        ys = np.where(work, y, y[0]) + dy
        fix = np.where(~link & (fix == "RTK_FIXED"), "RTK_FLOAT", fix)
        rows.append(pd.DataFrame({
            "ts": t, "tractor_id": tr, "lat": LAT0 + ys / M_LAT, "lon": LON0 + xs / M_LON,
            "speed_mps": speed, "cross_track_error_m": xte.round(3), "num_sats": sats.astype(int),
            "mean_cn0_dbhz": cn0.round(2), "min_cn0_dbhz": mn.round(2), "fix_state": fix, "base_link_ok": link,
        }))
    tele = pd.concat(rows, ignore_index=True)

    def ep_json(e):
        if e[0] in mp_actual:
            e = (e[0], e[1], e[2], mp_actual[e[0]][0], mp_actual[e[0]][1], e[5])
        return {"id": e[0], "class": e[1], "tractors": e[2], "start": H(e[3]).isoformat(), "end": H(e[4]).isoformat(),
                "recommended_action": ACTION[e[1]]}
    return {
        "tele": tele,
        "kp": [{"ts": a.isoformat(), "kp": float(b)} for a, b in zip(kp_t, kp)],
        "dst": [{"ts": a.isoformat(), "dst": float(b)} for a, b in zip(dst_t, dst)],
        "s4": [{"ts": a.isoformat(), "s4": round(float(b), 3)} for a, b in zip(s4_t, s4)],
        "rfi": rfi, "adv": adv,
        "train": [ep_json(e) for e in DEV], "key": [ep_json(e) for e in EVAL],
    }


def write(out, seed=7, hard=False):
    out = Path(out)
    g = make(seed, hard)
    for sub in ("farm", "sda", "labels"):
        (out / "data" / sub).mkdir(parents=True, exist_ok=True)
    g["tele"].to_csv(out / "data/farm/tractor_telemetry.csv.gz", index=False)
    (out / "data/sda/sgi_indices.json").write_text(json.dumps({"kp": g["kp"], "dst": g["dst"]}))
    (out / "data/sda/scintillation_series.json").write_text(json.dumps({"series": g["s4"]}))
    (out / "data/sda/constellation_advisories.json").write_text(json.dumps({"advisories": g["adv"]}))
    (out / "data/sda/rfi_reports.json").write_text(json.dumps({"reports": g["rfi"]}))
    (out / "data/labels/train_episodes.json").write_text(json.dumps({"episodes": g["train"]}, indent=1))
    (out / "eval_key.json").write_text(json.dumps({"episodes": g["key"]}, indent=1))
    print(f"synthetic dataset written to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="outputs/synth")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--hard", action="store_true", help="weaker effects, more noise")
    a = ap.parse_args()
    write(a.out, a.seed, a.hard)
