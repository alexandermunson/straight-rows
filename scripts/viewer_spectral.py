"""Frequency-domain views for the viewer's "Spectral" tab: a Welch PSD and an STFT spectrogram per tractor.

numpy only (no scipy). Everything here is display-only: the detector and submission.json never read it.

Both start from the 1 Hz telemetry. A window is used only if every sample in it is present (gaps of up to 5 s are
interpolated first), so the FFT never sees a hole. The window mean is removed, so slow level shifts do not leak into
the low-frequency bins; a sudden step still shows up as broadband energy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FS = 1.0                      # Hz, the raw telemetry rate
FMAX = 0.05                   # Hz shown (periods down to 20 s); the 17 s multipath dips have harmonics up to here
PSD_NPER, PSD_HOP = 4096, 2048        # 68 min windows, 50 % overlap: resolves 0.24 mHz
SPEC_NPER, SPEC_HOP = 2048, 512       # 34 min windows every 8.5 min: resolves 0.49 mHz
CHANNELS = [("mincn0", "min C/N0 (dB-Hz)"), ("cn0", "mean C/N0 (dB-Hz)"), ("xte", "|XTE| (m)")]
PEAK_BAND = (0.001, 0.02)     # Hz searched for a repeat period (50 s to 1000 s)
EDGE_PERIOD_S = 800           # a best period longer than this is slow drift leaking in, not a repeating pattern
WORK_SPEED = 1.0              # m/s; the PSD only uses windows where the machine is working


def _windows(x: np.ndarray, nper: int, hop: int):
    """(start index, windowed FFT power) for every full window; ok[i] False if the window has a missing sample."""
    view = np.lib.stride_tricks.sliding_window_view(x, nper)[::hop]
    starts = np.arange(0, len(x) - nper + 1, hop)
    ok = np.isfinite(view).all(axis=1)
    w = np.hanning(nper)
    seg = np.where(np.isfinite(view), view, 0.0)
    seg = (seg - seg.mean(axis=1, keepdims=True)) * w
    p = np.abs(np.fft.rfft(seg, axis=1)) ** 2 / (FS * np.sum(w ** 2))
    p[:, 1:-1] *= 2.0                                   # one-sided
    return starts, p, ok


def _peak(f: np.ndarray, db: np.ndarray) -> dict | None:
    """Repeat period by harmonic sum. Narrow periodic dips put as much energy in the overtones as in the fundamental
    (sometimes more), so picking the tallest bin can land on period/2. Instead each candidate fundamental f0 is scored
    by the mean excess (dB above the spectrum's median) at f0, 2 f0, 3 f0 ... up to 6 harmonics or the plot limit."""
    if not np.isfinite(db).all() or len(f) < 8:
        return None
    floor = float(np.median(db[f >= PEAK_BAND[0]]))
    cand = np.arange(PEAK_BAND[0], PEAK_BAND[1], (f[1] - f[0]) / 8)
    best, best_s = None, -1e9
    for f0 in cand:
        ks = np.arange(1, min(6, int(f[-1] / f0)) + 1)
        score = float(np.mean(np.maximum(np.interp(ks * f0, f, db) - floor, 0.0)))
        if score > best_s:
            best, best_s = float(f0), score
    if best is None or 1 / best > EDGE_PERIOD_S:        # best candidate sits at the slow edge of the band: slow drift, not a repeat
        return None
    return dict(hz=best, period_s=1 / best, strength_db=best_s)


def compute(frames: dict, tractors: list[str], t_min: pd.Timestamp, total_s: int) -> dict:
    """frames: {tractor: 1 s DataFrame with cn0, mincn0, xte, spd}. Returns a JSON-able dict."""
    idx = pd.date_range(t_min, periods=total_s, freq="1s")
    series = {}
    for tr in tractors:
        g = frames[tr].reindex(idx)
        series[tr] = {ch: g[ch].interpolate(limit=5, limit_area="inside").to_numpy(dtype=float) for ch, _ in CHANNELS}
        series[tr]["spd"] = g["spd"].interpolate(limit=5, limit_area="inside").to_numpy(dtype=float)

    # ---------------------------------------------------------------- PSD, per scope (all 72 h, then each day)
    nf_psd = int(FMAX * PSD_NPER / FS) + 1
    f_psd = np.fft.rfftfreq(PSD_NPER, 1 / FS)[:nf_psd]
    scopes = [("All 72 h", 0, total_s)] + [(f"Day {d + 1}", d * 86400, min((d + 1) * 86400, total_s)) for d in range(total_s // 86400)]
    psd = {ch: {name: {} for name, _, _ in scopes} for ch, _ in CHANNELS}
    for tr in tractors:
        for ch, _ in CHANNELS:
            starts, p, ok = _windows(series[tr][ch], PSD_NPER, PSD_HOP)
            spd = np.lib.stride_tricks.sliding_window_view(np.nan_to_num(series[tr]["spd"]), PSD_NPER)[::PSD_HOP]
            working = (spd > WORK_SPEED).mean(axis=1) >= 0.95
            use = ok & working
            for name, a, b in scopes:
                sel = use & (starts >= a) & (starts + PSD_NPER <= b)
                if sel.sum() == 0:
                    psd[ch][name][tr] = None
                    continue
                db = 10 * np.log10(np.maximum(p[sel].mean(axis=0)[:nf_psd], 1e-12))
                psd[ch][name][tr] = dict(db=[round(float(v), 2) for v in db], n=int(sel.sum()), peak=_peak(f_psd, db))

    # ---------------------------------------------------------------- spectrogram
    nf_spec = int(FMAX * SPEC_NPER / FS) + 1
    f_spec = np.fft.rfftfreq(SPEC_NPER, 1 / FS)[:nf_spec]
    spec, t_ctr = {ch: {} for ch, _ in CHANNELS}, None
    for tr in tractors:
        for ch, _ in CHANNELS:
            starts, p, ok = _windows(series[tr][ch], SPEC_NPER, SPEC_HOP)
            db = 10 * np.log10(np.maximum(p[:, :nf_spec], 1e-12))
            cols = [[round(float(v), 1) for v in row] if good else None for row, good in zip(db, ok)]
            spec[ch][tr] = cols
            t_ctr = (starts + SPEC_NPER / 2).astype(int).tolist()
    return dict(
        fs=FS, fmax=FMAX, tractors=tractors, channels=[[c, n] for c, n in CHANNELS], peak_band=list(PEAK_BAND),
        psd=dict(nper=PSD_NPER, df=float(f_psd[1]), nf=nf_psd, scopes=[s for s, _, _ in scopes], data=psd),
        spec=dict(nper=SPEC_NPER, hop=SPEC_HOP, df=float(f_spec[1]), nf=nf_spec, t=t_ctr, data=spec),
    )
