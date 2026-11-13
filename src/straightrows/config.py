"""Tunable parameters. Defaults are educated guesses; `calibrate.py` refits them on the labeled dev window."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import pandas as pd

EPOCH = pd.Timestamp("2026-05-04T00:00:00Z")
DEV_START, DEV_END = EPOCH, pd.Timestamp("2026-05-06T00:00:00Z")
EVAL_START, EVAL_END = DEV_END, pd.Timestamp("2026-05-07T00:00:00Z")
TRACTORS = ["T1", "T2", "T3"]

ACTION = {
    "SPACE_WEATHER": "HALT_OR_DEGRADED_MODE",
    "JAMMING": "HALT_AND_REPORT",
    "SPOOFING": "HALT_CROSSCHECK_AND_REPORT",
    "EQUIPMENT_FAULT": "SERVICE_MACHINE_OTHERS_CONTINUE",
    "MULTIPATH": "CONTINUE_FALSE_ALARM",
}


@dataclass
class Params:
    bin_s: int = 10                 # feature resolution (s); keep small so 2-3 min multipath survives
    smooth_s: int = 30              # median smoothing of per-bin deviations (odd number of bins)
    work_speed: float = 1.0         # m/s: "working" if mean speed above this
    # --- signal (C/N0) channel ---
    cn0_drop_db: float = 4.0        # per-tractor deviation below its own baseline that counts as degraded
    regional_drop_db: float = 3.0   # median-of-fleet deviation that counts as a regional event
    min_signal_s: int = 180         # shortest non-multipath signal event
    gap_bridge_s: int = 120         # bridge dropouts inside one event
    # --- XTE (spoofing) channel ---
    xte_abs_m: float = 1.0          # |XTE| above this while moving
    xte_k_mad: float = 6.0          # ...and above median + k*MAD of healthy XTE (whichever is larger)
    xte_cn0_ok_db: float = 2.5      # spoofing = XTE excess WITHOUT cn0 collapse deeper than this
    xte_min_s: int = 180
    # --- equipment channel ---
    link_bad_frac: float = 0.7      # base_link_ok fraction below this = link fault
    sats_drop: float = 4.0          # sats below own median by this = antenna dropout
    min_cn0_dip_db: float = 12.0    # min_cn0 below own median by this while mean is OK = dropout
    equip_min_s: int = 120
    dropout_density: float = 0.3    # share of bins flagged inside a dropout episode
    # --- multipath ---
    mp_max_s: int = 180             # multipath events are brief
    mp_min_s: int = 20
    mp_cell_m: float = 40.0         # same-place clustering radius
    mp_min_recur: int = 2
    # --- SDA corroboration ---
    s4_high: float = 0.30           # S4 considered elevated above this (relative to baseline below)
    s4_lag_min: int = 90            # S4 can lag Kp; look this far around the farm event
    kp_high: float = 5.0
    dst_low: float = -50.0
    rfi_window_min: int = 45        # reports arrive 10-30 min late; match reports ts in [start, end+this]
    rfi_dist_km: float = 15.0
    # --- timing / confidence ---
    onset_shift_s: float = 0.0      # fitted bias between detected and true onset on dev
    merge_gap_s: int = 300
    conf_floor: float = 0.30
    conf_cap: float = 0.90

    def to_json(self, path):
        Path(path).write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def from_json(cls, path):
        raw = json.loads(Path(path).read_text())
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in names})
