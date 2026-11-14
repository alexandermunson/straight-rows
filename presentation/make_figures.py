"""Figures for the 5-minute deck, re-rendered from the raw telemetry and SDA feeds with the viewer's palette.

Run from the repo root:  PYTHONPATH=src .venv/bin/python presentation/make_figures.py
Writes presentation/figures/*.png at the exact pixel size each slide uses (1 figure px = 1 slide px on the
1920x1080 canvas), so the 26 pt figure text is 36 px on the slide (the 18 pt minimum).
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

OUT = Path("presentation/figures"); OUT.mkdir(parents=True, exist_ok=True)
# viewer palette (scripts/viewer_static/index.html, light theme)
BG, INK, INK3, GRID = "#FAFAF9", "#16191D", "#5F6873", "#E2E4E7"
TC = {"T1": "#0072B2", "T2": "#009E73", "T3": "#3B424D"}
CC = {"SW": "#6A3D9A", "JAM": "#D62728", "SPOOF": "#E06A00", "EQUIP": "#8C6D31", "MP": "#7F7F7F"}
plt.rcParams.update({"font.family": "Noto Sans", "font.size": 26, "axes.edgecolor": INK3, "axes.labelcolor": INK, "xtick.color": INK,
                     "ytick.color": INK, "axes.facecolor": BG, "figure.facecolor": BG, "savefig.facecolor": BG, "axes.grid": True,
                     "grid.color": GRID, "grid.linewidth": 1, "axes.spines.top": False, "axes.spines.right": False,
                     "xtick.major.size": 0, "ytick.major.size": 0, "lines.linewidth": 2.6, "legend.frameon": False})
DPI = 100

t = pd.read_csv("data/farm/tractor_telemetry.csv.gz"); t["ts"] = pd.to_datetime(t["ts"], utc=True)
s4 = pd.DataFrame(json.load(open("data/sda/scintillation_series.json"))["series"]); s4["ts"] = pd.to_datetime(s4["ts"], utc=True)
sgi = json.load(open("data/sda/sgi_indices.json")); kp = pd.DataFrame(sgi["kp"]); kp["ts"] = pd.to_datetime(kp["ts"], utc=True)


def win(a, b, tr=None):
    w = t[(t.ts >= pd.Timestamp(a)) & (t.ts < pd.Timestamp(b))]
    return w if tr is None else w[w.tractor_id == tr]


def per_tractor(a, b, col, rule="60s", how="mean"):
    w = win(a, b)
    return {tr: getattr(g.set_index("ts")[col].resample(rule), how)() for tr, g in w.groupby("tractor_id")}


def hours(ax, fmt="%H:%M"):
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter(fmt, tz="UTC"))


def shade(ax, a, b, key, alpha=0.13):
    ax.axvspan(pd.Timestamp(a), pd.Timestamp(b), color=CC[key], alpha=alpha, lw=0)


def label(ax, text):
    ax.set_title(text, loc="left", fontsize=26, color=INK3, pad=8)


def save(fig, name):
    fig.savefig(OUT / name, dpi=DPI); plt.close(fig); print("wrote", name)


W, H = 9.6, 6.2          # failure-mode figures: 960 x 620 px

# ---- space weather: TRN-01, 05-05 11:00-16:00
a, b = "2026-05-05T10:00Z", "2026-05-05T17:00Z"
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(W, H), sharex=True, gridspec_kw=dict(height_ratios=[1.6, 1], hspace=0.35))
for tr, s in per_tractor(a, b, "mean_cn0_dbhz", "2min").items(): ax1.plot(s.index, s.values, color=TC[tr], label=tr)
shade(ax1, "2026-05-05T11:00Z", "2026-05-05T16:00Z", "SW"); label(ax1, "mean C/N0 (dB-Hz), T1 T2 T3")
ax1.set_yticks([30, 45])
w4 = s4[(s4.ts >= a) & (s4.ts < b)]
ax2.step(w4.ts, w4.s4, where="post", color=CC["SW"]); ax2.axhline(0.30, color=INK3, ls=(0, (3, 3)), lw=1.6)
shade(ax2, "2026-05-05T11:00Z", "2026-05-05T16:00Z", "SW"); label(ax2, "S4 (dashed: 0.30)"); ax2.set_yticks([0, 0.6]); ax2.set_ylim(0, 0.85)
ax2.set_xticks(pd.to_datetime(["2026-05-05T11:00Z", "2026-05-05T13:30Z", "2026-05-05T16:00Z"])); hours(ax2)
fig.subplots_adjust(left=0.1, right=0.98, top=0.9, bottom=0.12); save(fig, "fm_space_weather.png")

# ---- jamming: TRN-03, 05-04 16:30-17:12, RFI-001 at 16:44:48
a, b = "2026-05-04T16:10Z", "2026-05-04T17:32Z"
fig, ax = plt.subplots(figsize=(W, H))
for tr, s in per_tractor(a, b, "mean_cn0_dbhz", "30s").items(): ax.plot(s.index, s.values, color=TC[tr], label=tr, lw=2.2)
shade(ax, "2026-05-04T16:30Z", "2026-05-04T17:12Z", "JAM")
ax.axvline(pd.Timestamp("2026-05-04T16:44:48Z"), color=CC["JAM"], lw=1.6); ax.text(pd.Timestamp("2026-05-04T16:46Z"), 43.5, "RFI-001", color=INK, fontsize=26)
label(ax, "mean C/N0 (dB-Hz)")   # tractor key is on the slide, the same on every failure-mode slide
ax.set_xticks(pd.to_datetime(["2026-05-04T16:30Z", "2026-05-04T17:12Z"])); hours(ax); ax.set_yticks([20, 30, 40])
fig.subplots_adjust(left=0.1, right=0.98, top=0.9, bottom=0.12); save(fig, "fm_jamming.png")

# ---- spoofing: TRN-07, T1, 05-05 00:30-01:18
a, b = "2026-05-05T00:10Z", "2026-05-05T01:38Z"
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(W, H), sharex=True, gridspec_kw=dict(height_ratios=[1.6, 1], hspace=0.35))
w = win(a, b); w = w.assign(xa=w.cross_track_error_m.abs())
for tr, g in w.groupby("tractor_id"):
    s = g.set_index("ts").xa.resample("60s").mean(); ax1.plot(s.index, s.values, color=TC[tr], label=tr)
shade(ax1, "2026-05-05T00:30Z", "2026-05-05T01:18Z", "SPOOF"); label(ax1, "|cross-track error| (m)"); ax1.set_yticks([0, 10])

for tr, s in per_tractor(a, b, "mean_cn0_dbhz", "60s").items(): ax2.plot(s.index, s.values, color=TC[tr])
shade(ax2, "2026-05-05T00:30Z", "2026-05-05T01:18Z", "SPOOF"); label(ax2, "mean C/N0 (dB-Hz)"); ax2.set_ylim(30, 50); ax2.set_yticks([30, 45])
ax2.set_xticks(pd.to_datetime(["2026-05-05T00:30Z", "2026-05-05T01:18Z"])); hours(ax2)
fig.subplots_adjust(left=0.1, right=0.98, top=0.9, bottom=0.12); save(fig, "fm_spoofing.png")

# ---- equipment fault: TRN-10, T1 correction link, 05-05 02:00-02:48
a, b = "2026-05-05T01:45Z", "2026-05-05T03:03Z"
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(W, H), sharex=True, gridspec_kw=dict(height_ratios=[1.4, 1], hspace=0.35))
w = win(a, b)
for k, (tr, g) in enumerate(sorted(w.groupby("tractor_id"))):
    s = g.set_index("ts").base_link_ok.astype(float).resample("10s").min()
    ax1.step(s.index, s.values * 0.6 + (2 - k) * 1.0, where="post", color=TC[tr])
ax1.set_yticks([2.3, 1.3, 0.3]); ax1.set_yticklabels(["T1", "T2", "T3"]); ax1.set_ylim(-0.2, 2.9); ax1.grid(False)
shade(ax1, "2026-05-05T02:00Z", "2026-05-05T02:48Z", "EQUIP"); label(ax1, "correction link (up / down)")
for tr, s in per_tractor(a, b, "mean_cn0_dbhz", "60s").items(): ax2.plot(s.index, s.values, color=TC[tr])
shade(ax2, "2026-05-05T02:00Z", "2026-05-05T02:48Z", "EQUIP"); label(ax2, "mean C/N0 (dB-Hz)"); ax2.set_ylim(30, 50); ax2.set_yticks([30, 45])
ax2.set_xticks(pd.to_datetime(["2026-05-05T02:00Z", "2026-05-05T02:48Z"])); hours(ax2)
fig.subplots_adjust(left=0.1, right=0.98, top=0.9, bottom=0.12); save(fig, "fm_equipment.png")

# ---- multipath: TRN-11, T3, labelled pass 05-04 14:04:18-14:04:35
a, b = "2026-05-04T13:50Z", "2026-05-04T14:22Z"
fig, ax = plt.subplots(figsize=(W, H))
g = win(a, b, "T3").set_index("ts").min_cn0_dbhz
ax.plot(g.index, g.values, color=TC["T3"], lw=1.4)
ax.axvspan(pd.Timestamp("2026-05-04T14:04:18Z"), pd.Timestamp("2026-05-04T14:04:35Z"), color=CC["MP"], alpha=0.35, lw=0)
ax.annotate("TRN-11, 17 s", xy=(pd.Timestamp("2026-05-04T14:04:26Z"), 27), xytext=(pd.Timestamp("2026-05-04T14:07:30Z"), 21),
            fontsize=26, color=INK, arrowprops=dict(arrowstyle="-", color=INK, lw=1.4))
label(ax, "T3 min C/N0 (dB-Hz), 1 Hz"); ax.set_yticks([20, 30, 40]); ax.set_ylim(15, 46)
ax.set_xticks(pd.to_datetime(["2026-05-04T13:52Z", "2026-05-04T14:04Z", "2026-05-04T14:16Z"])); hours(ax)
fig.subplots_adjust(left=0.1, right=0.98, top=0.9, bottom=0.12); save(fig, "fm_multipath.png")

# ---- D837: operator view, 05-05 19:30-21:00 (viewer panels, re-rendered large)
a, b = "2026-05-05T19:30Z", "2026-05-05T21:00Z"
fig, axs = plt.subplots(5, 1, figsize=(16.64, 6.6), sharex=True, gridspec_kw=dict(height_ratios=[1.3, 1, 1, 1, 1], hspace=0.62))
w = win(a, b); w = w.assign(xa=w.cross_track_error_m.abs(), fix=w.fix_state.map({"NO_FIX": 0, "AUTONOMOUS": 1, "DGPS": 2, "RTK_FLOAT": 3, "RTK_FIXED": 4}))
for tr, gg in w.groupby("tractor_id"):
    gg = gg.set_index("ts")
    axs[0].plot(gg.mean_cn0_dbhz.resample("30s").mean(), color=TC[tr], lw=2)
    axs[1].plot(gg.num_sats.resample("30s").min(), color=TC[tr], lw=2)
    axs[2].plot(gg.xa.resample("30s").max(), color=TC[tr], lw=2)
    axs[3].step(gg.fix.resample("30s").min().index, gg.fix.resample("30s").min().values, where="post", color=TC[tr], lw=2)
w4 = s4[(s4.ts >= pd.Timestamp(a) - pd.Timedelta("15min")) & (s4.ts <= pd.Timestamp(b))]; axs[4].step(w4.ts, w4.s4, where="post", color=CC["SW"], lw=2.6)
axs[4].set_xlim(pd.Timestamp(a), pd.Timestamp(b))
axs[4].axhline(0.30, color=INK3, ls=(0, (3, 3)), lw=1.4)
titles = ["mean C/N0 (dB-Hz)", "satellites", "|XTE| (m)", "fix: 0 none … 4 RTK fixed", "S4 (dashed 0.30; Kp 6.8 all window)"]
ticks = [[20, 40], [3, 12], [0, 25], [0, 4], [0.4]]
for ax, ti, tk in zip(axs, titles, ticks):
    ax.axvspan(pd.Timestamp("2026-05-05T20:00Z"), pd.Timestamp("2026-05-05T20:36Z"), color=CC["JAM"], alpha=0.14, lw=0)
    ax.set_yticks(tk); ax.set_title(ti, loc="left", fontsize=24, color=INK3, pad=4)
axs[4].set_ylim(0, 0.55)
axs[4].set_xticks(pd.to_datetime(["2026-05-05T19:30Z", "2026-05-05T20:00Z", "2026-05-05T20:36Z", "2026-05-05T21:00Z"])); hours(axs[4])
fig.subplots_adjust(left=0.05, right=0.96, top=0.93, bottom=0.09); save(fig, "event_D837.png")

# ---- spectral: power spectrum of min C/N0, all 72 h (the Spectral tab's numbers, from viewer_spectral.compute)
psd = json.load(open("presentation/data/psd_mincn0_all.json")); facts = json.load(open("presentation/data/facts.json"))
df = facts["spectral_meta"]["df"] * 1000
fig, ax = plt.subplots(figsize=(11.2, 6.6))
for tr in ["T1", "T2", "T3"]:
    db = np.array(psd[tr]["db"]); f = np.arange(len(db)) * df; m = f >= 1
    ax.plot(f[m], db[m], color=TC[tr], lw=2, label=tr)
f0 = facts["spectral"]["T1"]["peak"]["hz"] * 1000
for k in range(1, 9): ax.axvline(k * f0, color=INK3, ls=(0, (3, 4)), lw=1.2)
ax.annotate("repeat every 357 s, and its overtones", xy=(0, 1.02), xycoords="axes fraction", fontsize=26, color=INK)
ax.set_xlabel("repeat rate (mHz)"); ax.set_ylabel("power (dB)"); ax.set_xticks([0, 10, 20, 30, 40, 50]); ax.set_yticks([10, 20, 30])
fig.subplots_adjust(left=0.11, right=0.98, top=0.88, bottom=0.17); save(fig, "spectral_psd.png")
