"""SDA helpers: ask 'what did Kp / Dst / S4 / advisories / RFI look like for this farm event?'"""
from __future__ import annotations

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
        inside = s[(s.ts >= a - pd.Timedelta(minutes=15)) & (s.ts <= b)]
        wide = s[(s.ts >= a - lag) & (s.ts <= b + lag)]
        return {
            "mean": float(inside.s4.mean()) if len(inside) else np.nan,
            "max": float(wide.s4.max()) if len(wide) else np.nan,
            "base": self.s4_base,
            "elevated": bool(len(inside) and inside.s4.mean() >= max(self.p.s4_high, self.s4_base + 5 * (self.s4_mad or 0.02))),
        }

    def advisories(self, a, b, pad_h=6):
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
            if ts is not None and a - pd.Timedelta(hours=pad_h) <= ts <= b + pd.Timedelta(hours=1):
                out.append(ad)
        return out

    def rfi_matches(self, a, b, lat=None, lon=None):
        """Reports arrive 10-30 min late, so accept report time in [a, b + window]. Spatial test if fields exist."""
        r = self.b.rfi
        if r is None or not len(r):
            return []
        tcol = next((c for c in r.columns if c.lower() in ("ts", "reported_at", "time", "timestamp", "report_time")), None)
        if tcol is None:
            return []
        w = r[(r[tcol] >= a) & (r[tcol] <= b + pd.Timedelta(minutes=self.p.rfi_window_min))]
        out = []
        latc = next((c for c in r.columns if c.lower() in ("lat", "latitude", "center_lat")), None)
        lonc = next((c for c in r.columns if c.lower() in ("lon", "lng", "longitude", "center_lon")), None)
        uc = next((c for c in r.columns if "uncert" in c.lower() or "radius" in c.lower() or "semi_major" in c.lower()), None)
        for _, row in w.iterrows():
            rec = row.to_dict()
            rec["_ts"] = row[tcol]
            if latc and lonc and lat is not None and pd.notna(row[latc]):
                rec["_dist_km"] = float(haversine_km(lat, lon, row[latc], row[lonc]))
                u = float(row[uc]) if uc and pd.notna(row[uc]) else 0.0
                rec["_within"] = rec["_dist_km"] <= self.p.rfi_dist_km + u
            else:
                rec["_within"] = True
            out.append(rec)
        return out
