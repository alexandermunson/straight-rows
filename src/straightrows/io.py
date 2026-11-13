"""Tolerant loaders. The SDA/label JSON layouts are not documented beyond file names, so
each loader accepts several plausible shapes and fails loudly (with what it saw) otherwise."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

TS_KEYS = ("ts", "time", "timestamp", "t", "datetime", "issued", "reported_at", "valid_time")


def _records(obj, prefer=()):
    """Find the list of records inside a JSON object."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in prefer:
            if k in obj and isinstance(obj[k], list):
                return obj[k]
        lists = [v for v in obj.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if len(lists) == 1:
            return lists[0]
    raise ValueError(f"cannot find records list; top-level keys: {list(obj) if isinstance(obj, dict) else type(obj)}")


def _to_ts(series):
    return pd.to_datetime(series, utc=True, format="ISO8601")


def _first(cols, *names):
    low = {c.lower(): c for c in cols}
    for n in names:
        if n in low:
            return low[n]
    return None


def _series_frame(path, prefer, value_names):
    obj = json.loads(Path(path).read_text())
    df = pd.DataFrame(_records(obj, prefer))
    tcol = _first(df.columns, *TS_KEYS)
    if tcol is None:
        raise ValueError(f"{path}: no timestamp column in {list(df.columns)}")
    vcol = _first(df.columns, *value_names)
    if vcol is None:
        nums = [c for c in df.columns if c != tcol and pd.api.types.is_numeric_dtype(df[c])]
        if not nums:
            raise ValueError(f"{path}: no numeric value column in {list(df.columns)}")
        vcol = nums[0]
    out = pd.DataFrame({"ts": _to_ts(df[tcol]), value_names[0]: pd.to_numeric(df[vcol])})
    return out.sort_values("ts").reset_index(drop=True)


@dataclass
class Bundle:
    tele: pd.DataFrame
    kp: pd.DataFrame
    dst: pd.DataFrame
    s4: pd.DataFrame
    advisories: list = field(default_factory=list)
    rfi: pd.DataFrame = None
    train: list = field(default_factory=list)


def load_telemetry(path):
    t = pd.read_csv(path)
    t["ts"] = _to_ts(t["ts"])
    t["base_link_ok"] = t["base_link_ok"].astype(str).str.lower().isin(["true", "1", "1.0", "yes"])
    t["tractor_id"] = t["tractor_id"].astype(str)
    return t.sort_values(["tractor_id", "ts"]).reset_index(drop=True)


def load_rfi(path):
    obj = json.loads(Path(path).read_text())
    df = pd.DataFrame(_records(obj, ("reports", "rfi_reports")))
    if df.empty:
        return df
    for c in df.columns:
        if c.lower() in TS_KEYS or c.lower().endswith(("_at", "_time", "_ts")):
            try:
                df[c] = _to_ts(df[c])
            except Exception:
                pass
    return df


def load_all(data_dir):
    d = Path(data_dir)
    sgi = json.loads((d / "sda/sgi_indices.json").read_text())
    kp = pd.DataFrame(_records(sgi["kp"] if "kp" in sgi else sgi))
    dst = pd.DataFrame(_records(sgi["dst"])) if "dst" in sgi else pd.DataFrame(columns=["ts", "dst"])
    def norm(df, name):
        tcol = _first(df.columns, *TS_KEYS)
        vcol = _first(df.columns, name, "value") or [c for c in df.columns if c != tcol][0]
        return pd.DataFrame({"ts": _to_ts(df[tcol]), name: pd.to_numeric(df[vcol])}).sort_values("ts").reset_index(drop=True)
    kp, dst = norm(kp, "kp"), (norm(dst, "dst") if len(dst) else dst)
    s4 = _series_frame(d / "sda/scintillation_series.json", ("series", "s4"), ("s4", "value"))
    adv = json.loads((d / "sda/constellation_advisories.json").read_text())
    adv = _records(adv, ("advisories",)) if adv else []
    lab = d / "labels/train_episodes.json"
    train = _records(json.loads(lab.read_text()), ("episodes",)) if lab.exists() else []
    return Bundle(load_telemetry(d / "farm/tractor_telemetry.csv.gz"), kp, dst, s4, adv,
                  load_rfi(d / "sda/rfi_reports.json"), train)
