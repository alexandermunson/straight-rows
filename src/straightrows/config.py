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
    chauvenet_baseline: bool = True # iteratively reject Chauvenet outliers before taking each machine's baseline
    corr_block_s: int = 60          # block-mean length for correlation/CI (1 Hz and 10 s bins are autocorrelated)
    glitch_filter: bool = True      # (inactive) parameters of Detector._sustained, kept for its unit test; see docs/ME457_METHODS.md
    glitch_z: float = 2.0           # |e/S_yx| above this counts as a real departure from the local trend
    glitch_ctx_s: int = 600         # context either side used to fit the local trend
    glitch_min_run: int = 2         # consecutive bins beyond glitch_z needed ("consistent with neighbours")
    glitch_syx_floor: float = 0.25  # dB floor on S_yx so a very quiet context does not make everything significant
    lag_step_min: int = 15          # S4 lag regression step (S4 cadence)
    corr_alpha: float = 0.05        # significance level for Pearson r (r_crit) and the t confidence intervals
    # --- signal (C/N0) channel ---
    cn0_drop_db: float = 4.0        # per-tractor deviation below its own baseline that counts as degraded
    regional_drop_db: float = 3.0   # median-of-fleet deviation that counts as a regional event
    min_signal_s: int = 180         # shortest non-multipath signal event
    gap_bridge_s: int = 120         # bridge dropouts inside one event
    link_gap_s: int = 300           # link outages flicker (~90 s down every ~4 min): bridge this far
    jam_track_r2: float = 0.8       # per-machine C/N0 tracks the fleet this well inside a jam -> same cause, not a 2nd fault
    kp_lead_h: float = 3.0          # Kp credit only from bins overlapping the event or this many hours before it
    uniform_spread_db: float = 1.0  # per-machine C/N0 loss differing by >= this is NOT a uniform (ionospheric) pattern
    cm_min_machines: int = 2        # common-mode spoof guard: this many machines with large XTE at once
    jam_step_db: float = 6.0        # regional level this far below the surrounding storm level = jammer on top of a storm
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
    mp_min_recur: int = 5           # occurrences (all 72 h) needed before a spot counts as a recurring obstruction
    mp_dip_db: float = 6.0          # min C/N0 this far below the machine's own 15-min median = multipath dip
    mp_dip_max_db: float = 16.0     # deeper than this is an antenna dropout, not a reflection
    mp_ctx_s: int = 900             # rolling-median context for the min C/N0 baseline (follows storms)
    mp_evt_min_s: int = 8           # shortest dip kept (1 Hz)
    mp_band_m: float = 20.0         # recurring spot = north-south band this wide around the modal latitude
    max_detections: int = 495       # validator hard limit is 500
    # --- SDA corroboration ---
    s4_high: float = 0.30           # S4 considered elevated above this (relative to baseline below)
    s4_lag_min: int = 90            # S4 can lag Kp; look this far around the farm event
    kp_high: float = 5.0
    dst_low: float = -50.0
    rfi_window_min: int = 35        # reports arrive 10-30 min late: accept report time up to end + this (30 + 5 slack)
    rfi_min_delay_min: int = 5      # ...and no earlier than start + this (10 min minimum delay, 5 slack)
    rfi_conf_min: float = 0.5       # reports whose own confidence is below this count half
    dst_lead_h: float = 6.0         # Dst look-back (Dst recovers slowly, so longer than the Kp look-back)
    storm_min_s: int = 3600         # a uniform fleet-wide loss lasting at least this long earns storm credit without S4
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
