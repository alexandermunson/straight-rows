"""SDA helpers: ask 'what did Kp / Dst / S4 / advisories / RFI look like for this farm event?'"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .geo import haversine_km


class SDA:
    def __init__(self, bundle, p):
        self.b, self.p = bundle, p
        self.s4_base = float(bundle.s4["s4"].median()) if len(bundle.s4) else np.nan
        self.s4_mad = float((bundle.s4["s4"] - self.s4_base).abs().median() * 1.4826) if len(bundle.s4) else np.nan

    def kp_at(self, t):
        k = self.b.kp[self.b.kp.ts <= t]
        return float(k.kp.iloc[-1]) if len(k) else np.nan

    def kp_max(self, a, b, lead_h=6):
        k = self.b.kp
        # a Kp value is valid for 3 h after its timestamp
        m = k[(k.ts + pd.Timedelta(hours=3) > a - pd.Timedelta(hours=lead_h)) & (k.ts <= b)]
        return (float(m.kp.max()), m.loc[m.kp.idxmax(), "ts"]) if len(m) else (np.nan, None)

    def dst_min(self, a, b, lead_h=6):
        d = self.b.dst
        if not len(d):
            return np.nan
        m = d[(d.ts >= a - pd.Timedelta(hours=lead_h)) & (d.ts <= b)]
        return float(m.dst.min()) if len(m) else np.nan

    def s4(self, a, b):
        lag = pd.Timedelta(minutes=self.p.s4_lag_min)
        s = self.b.s4
        inside = s[(s.ts >= a) & (s.ts <= b)]
        if not len(inside):          # event shorter than the 15 min cadence: use the latest sample just before it
            inside = s[(s.ts >= a - pd.Timedelta(minutes=15)) & (s.ts <= b)].tail(1)
        wide = s[(s.ts >= a - lag) & (s.ts <= b + lag)]
        # elevated is None (unknown) when the feed has no sample for the event: missing data is NOT evidence of quiet
        elevated = None if not len(inside) else bool(inside.s4.mean() >= max(self.p.s4_high, self.s4_base + 5 * (self.s4_mad or 0.02)))
        return {
            "mean": float(inside.s4.mean()) if len(inside) else np.nan,
            "max": float(wide.s4.max()) if len(wide) else np.nan,
            "base": self.s4_base,
            "elevated": elevated,
        }

    def advisories(self, a, b, pad_h=6):
        """Advisories whose validity overlaps [a, b]. Validity is [ts, ts + pad_h] by default; a SPACE_WEATHER advisory
        that states 'for A-B h' is valid for B hours (24 h if it states nothing). Each hit gets _ts and _age_h."""
        out = []
        for ad in self.b.advisories:
            ts = None
            for k in ("ts", "issued", "time", "start", "valid_from"):
                if k in ad:
                    try:
                        ts = pd.Timestamp(ad[k]).tz_convert("UTC") if pd.Timestamp(ad[k]).tzinfo else pd.Timestamp(ad[k], tz="UTC")
                    except Exception:
                        pass
                    break
            if ts is None:
                continue
            valid_h = pad_h
            if str(ad.get("type", "")).upper() == "SPACE_WEATHER":
                m = re.search(r"(\d+)\s*-\s*(\d+)\s*h", str(ad.get("text", "")))
                valid_h = float(m.group(2)) if m else 24.0
            if ts <= b + pd.Timedelta(hours=1) and ts + pd.Timedelta(hours=valid_h) >= a:
                out.append({**ad, "_ts": ts, "_age_h": (a - ts).total_seconds() / 3600.0, "_valid_h": valid_h})
        return out

    def rfi_matches(self, a, b, lat=None, lon=None):
        """Reports arrive 10-30 min late, so accept report time in [a + min delay, b + window] (both with 5 min slack).
        Spatial test if fields exist. Each hit carries _delay_min (after onset) and _weight (1.0, or 0.5 for a report
        whose own confidence is below rfi_conf_min)."""
        r = self.b.rfi
        if r is None or not len(r):
            return []
        tcol = next((c for c in r.columns if c.lower() in ("ts", "reported_at", "time", "timestamp", "report_time")), None)
        if tcol is None:
            return []
        w = r[(r[tcol] >= a + pd.Timedelta(minutes=self.p.rfi_min_delay_min)) & (r[tcol] <= b + pd.Timedelta(minutes=self.p.rfi_window_min))]
        cc = next((c for c in r.columns if c.lower() == "confidence"), None)
        out = []
        latc = next((c for c in r.columns if c.lower() in ("lat", "latitude", "center_lat", "centerlat")), None)
        lonc = next((c for c in r.columns if c.lower() in ("lon", "lng", "longitude", "center_lon", "centerlon")), None)
        uc = next((c for c in r.columns if "uncert" in c.lower() or "radius" in c.lower() or c.lower().replace("_", "").startswith("semimajor")), None)
        for _, row in w.iterrows():
            rec = row.to_dict()
            rec["_ts"] = row[tcol]
            rec["_delay_min"] = (row[tcol] - a).total_seconds() / 60.0
            rec["_weight"] = 0.5 if cc and pd.notna(row[cc]) and float(row[cc]) < self.p.rfi_conf_min else 1.0
            if latc and lonc and lat is not None and pd.notna(row[latc]):
                rec["_dist_km"] = float(haversine_km(lat, lon, row[latc], row[lonc]))
                u = float(row[uc]) if uc and pd.notna(row[uc]) else 0.0
                rec["_within"] = rec["_dist_km"] <= self.p.rfi_dist_km + u
            else:
                rec["_within"] = True
            out.append(rec)
        return out
