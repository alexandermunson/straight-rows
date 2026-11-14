"""Per-tractor feature bins (default 10 s) plus robust per-machine baselines.

Design choices (see README):
* 10 s bins, not 1 min: 1-3 minute multipath dips survive aggregation.
* deviations are measured against each machine's own robust baseline (median over working time),
  so a naturally 'hot' or 'cold' receiver doesn't look degraded.
* the *regional* component is the healthiest machine's smoothed deviation; the *local* residual is what's
  left per machine. That lets a machine fault or jammer be separated from a concurrent storm.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import TRACTORS, Params
from .stats import chauvenet_keep


@dataclass
class Features:
    idx: pd.DatetimeIndex
    f: dict            # feature name -> DataFrame(index=bin, columns=tractors)
    base: dict         # baseline stats per tractor
    p: Params

    def __getitem__(self, k):
        return self.f[k]


def build(tele: pd.DataFrame, p: Params) -> Features:
    t = tele.copy()
    t["bin"] = t["ts"].dt.floor(f"{p.bin_s}s")
    t["xa"] = t["cross_track_error_m"].abs()
    t["fixbad"] = (t["fix_state"] != "RTK_FIXED").astype(float)
    t["linkok"] = t["base_link_ok"].astype(float)
    g = t.groupby(["bin", "tractor_id"])
    agg = g.agg(cn0=("mean_cn0_dbhz", "mean"), mincn0=("min_cn0_dbhz", "min"), xte=("xa", "mean"),
                xtemax=("xa", "max"), sats=("num_sats", "mean"), satsmin=("num_sats", "min"),
                link=("linkok", "mean"), fixbad=("fixbad", "mean"), speed=("speed_mps", "mean"),
                lat=("lat", "mean"), lon=("lon", "mean"))
    wide = {c: agg[c].unstack("tractor_id").reindex(columns=TRACTORS) for c in agg.columns}
    idx = wide["cn0"].index
    work = wide["speed"] > p.work_speed
    wide["work"] = work
    base = {}
    for tr in TRACTORS:
        w = work[tr]
        b = {}
        for k in ("cn0", "mincn0", "sats", "xte"):
            v = wide[k][tr][w].dropna()
            if p.chauvenet_baseline and len(v) > 2:
                v = v[chauvenet_keep(v.values)]     # events must not contaminate the healthy baseline
            b[k] = float(v.median()) if len(v) else np.nan
        v = wide["xte"][tr][w].dropna()
        if p.chauvenet_baseline and len(v) > 2:
            v = v[chauvenet_keep(v.values)]
        b["xte_mad"] = float((v - v.median()).abs().median() * 1.4826) if len(v) else np.nan
        base[tr] = b
    dev = pd.DataFrame({tr: wide["cn0"][tr] - base[tr]["cn0"] for tr in TRACTORS})
    wide["cn0_dev"] = dev.where(work)
    wide["mincn0_dev"] = pd.DataFrame({tr: wide["mincn0"][tr] - base[tr]["mincn0"] for tr in TRACTORS}).where(work)
    wide["sats_dev"] = pd.DataFrame({tr: wide["sats"][tr] - base[tr]["sats"] for tr in TRACTORS}).where(work)
    wide["satsmin_dev"] = pd.DataFrame({tr: wide["satsmin"][tr] - base[tr]["sats"] for tr in TRACTORS}).where(work)
    sm = max(1, int(p.smooth_s / p.bin_s)) | 1
    smooth = wide["cn0_dev"].rolling(sm, center=True, min_periods=1).median()
    wide["cn0_dev_s"] = smooth.where(work)
    # regional = healthiest working machine; needs >=2 machines working to separate region from machine
    nwork = work.sum(axis=1)
    reg = smooth.where(work).max(axis=1).clip(upper=0.0)
    wide["regional"] = reg.where(nwork >= 2, 0.0)
    wide["resid"] = wide["cn0_dev_s"].sub(wide["regional"], axis=0).where(work)
    wide["nwork"] = nwork
    rel = {}
    for k in ("satsmin_dev", "mincn0_dev"):
        r = {}
        for tr in TRACTORS:
            others = [o for o in TRACTORS if o != tr]
            ref = wide[k][others].where(work[others]).median(axis=1)
            r[tr] = wide[k][tr] - ref.fillna(0.0)      # relative to neighbours: storms hit everyone, faults hit one
        rel[k] = pd.DataFrame(r)
    drop = ((rel["satsmin_dev"] <= -p.sats_drop) | (rel["mincn0_dev"] <= -p.min_cn0_dip_db)) & work
    wide["dropout_local"] = drop
    ex = {}
    for tr in TRACTORS:
        others = [o for o in TRACTORS if o != tr]
        ref = wide["xte"][others].where(work[others]).median(axis=1)
        ex[tr] = wide["xte"][tr].rolling(sm, center=True, min_periods=1).median() \
            - ref.rolling(sm, center=True, min_periods=1).median().fillna(base[tr]["xte"])
    wide["xte_excess"] = pd.DataFrame(ex).where(work)
    return Features(idx, wide, base, p)
