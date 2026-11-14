#!/usr/bin/env python3
"""Straight Rows investigation GUI (matplotlib widgets only).

    python scripts/gui_matplotlib.py                     # opens in a native window if Tk/Qt exist, else in your browser (WebAgg)
    python scripts/gui_matplotlib.py --backend webagg    # force the browser version
    python scripts/gui_matplotlib.py --save out.png --event 3          # headless snapshot of event #3 (also good for the demo)
    python scripts/gui_matplotlib.py --save out.png --start 2026-05-06T02:00 --span 3h

What you see (7 linked time panels + map + info box):
    mean C/N0 | min C/N0 | |XTE| | satellites | fix quality | correction link | SDA (Kp, S4, RFI, advisories)
    Dashed vertical lines = labelled dev episodes (truth). Shaded spans = OUR detections. Grey ticks = multipath passes.
Keys:  n / p next / previous event   left / right pan   up / down zoom   s snapshot PNG
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd

CLASS_COLOR = {"SPACE_WEATHER": "#6a3d9a", "JAMMING": "#e31a1c", "SPOOFING": "#ff7f00",
               "EQUIPMENT_FAULT": "#8c6d31", "MULTIPATH": "#7f7f7f"}
CLASS_SHORT = {"SPACE_WEATHER": "SW", "JAMMING": "JAM", "SPOOFING": "SPOOF", "EQUIPMENT_FAULT": "EQUIP", "MULTIPATH": "MP"}
TR_COLOR = {"T1": "#0072B2", "T2": "#009E73", "T3": "#CC79A7"}
TRACTORS = ["T1", "T2", "T3"]
FIX_RANK = {"RTK_FIXED": 4, "RTK_FLOAT": 3, "DGPS": 2, "AUTONOMOUS": 1, "NO_FIX": 0}
FIX_NAME = {v: k for k, v in FIX_RANK.items()}
SPANS = {"10 min": "10min", "1 h": "1h", "3 h": "3h", "6 h": "6h", "12 h": "12h", "24 h": "24h", "72 h": "72h"}


def pick_backend(requested):
    """Native window when a toolkit exists, otherwise matplotlib's built-in browser backend."""
    import importlib.util as u
    if requested:
        return requested
    if u.find_spec("_tkinter"):
        return "TkAgg"
    for mod, be in (("PyQt6", "QtAgg"), ("PySide6", "QtAgg"), ("PyQt5", "QtAgg")):
        if u.find_spec(mod):
            return be
    if u.find_spec("tornado"):
        return "webagg"
    sys.exit("No GUI backend found. Run:  .venv/bin/python -m pip install tornado   (browser GUI)\n"
             "or install tk (pacman -S tk) for a native window.")


def naive(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_convert(None) if ts.tzinfo is not None else ts


# ------------------------------------------------------------------------------------------ data
class Data:
    def __init__(self, data_dir, refresh=False, submission=None):
        from straightrows import io
        from straightrows.config import EPOCH, EVAL_END, Params
        self.b = io.load_all(data_dir)
        self.t_min, self.t_max = naive(EPOCH), naive(EVAL_END)
        self.frames = self._frames(self.b.tele)
        self.base = {}
        for tr in TRACTORS:
            g = self.frames[1][tr]
            w = g[g.spd > 1.0]
            self.base[tr] = dict(cn0=float(w.cn0.median()), mincn0=float(w.mincn0.median()))
        self.rfi = self.b.rfi.copy() if self.b.rfi is not None and len(self.b.rfi) else pd.DataFrame()
        if len(self.rfi):
            self.rfi["ts"] = self.rfi["ts"].map(naive)
        # GUI-only naive-UTC copies; self.b must stay untouched because the detector compares against tz-aware times
        self.kp, self.s4 = (self._naive_ts(self.b.kp), self._naive_ts(self.b.s4))
        self.truth = [dict(kind="TRUTH", id=e["id"], cls=e["class"], tractors=e["tractors"], start=naive(e["start"]),
                           end=naive(e["end"]), conf=None, evidence=[], action=e.get("recommended_action", ""))
                      for e in self.b.train]
        self.dets, self.mp, self.mp_lat = [], [], None
        if submission:
            self._from_submission(submission)
        else:
            self._run_detector(refresh, Params)
        key = lambda e: e["start"]
        self.events = sorted(self.truth + [d for d in self.dets if d["cls"] != "MULTIPATH"], key=key)
        self.mp_dets = [d for d in self.dets if d["cls"] == "MULTIPATH"]

    @staticmethod
    def _naive_ts(df):
        df = df.copy()
        df["ts"] = df["ts"].map(naive)
        return df

    @staticmethod
    def _frames(tele):
        t = tele.copy()
        t["ts"] = t["ts"].dt.tz_convert(None)
        t["fix"] = t["fix_state"].map(FIX_RANK).fillna(0).astype(float)
        t["xte"] = t["cross_track_error_m"].abs()
        t["link"] = t["base_link_ok"].astype(float)
        out = {1: {}, 10: {}, 60: {}}
        for tr, g in t.groupby("tractor_id"):
            g = g.set_index("ts").sort_index()
            out[1][tr] = g.rename(columns={"mean_cn0_dbhz": "cn0", "min_cn0_dbhz": "mincn0", "num_sats": "sats",
                                           "speed_mps": "spd"})[["cn0", "mincn0", "xte", "sats", "fix", "link", "lat", "lon", "spd"]]
            for n in (10, 60):
                out[n][tr] = g.resample(f"{n}s").agg(dict(
                    mean_cn0_dbhz="mean", min_cn0_dbhz="min", xte="max", num_sats="mean", fix="min", link="min",
                    lat="mean", lon="mean", speed_mps="mean")).rename(columns={
                        "mean_cn0_dbhz": "cn0", "min_cn0_dbhz": "mincn0", "num_sats": "sats", "speed_mps": "spd"})
        return out

    def _run_detector(self, refresh, Params):
        cache = ROOT / "outputs" / "gui_cache.pkl"
        if cache.exists() and not refresh:
            try:
                self.dets, self.mp, self.mp_lat = pickle.loads(cache.read_bytes())
                return
            except Exception:
                pass
        print("Running the detector over all 72 h (about a minute, cached afterwards)...", flush=True)
        from straightrows.detect import Detector
        from straightrows.report import to_submission
        p = Params()
        p.max_detections = 10 ** 9
        det = Detector(self.b, p, (self.b_epoch(), self.t_max.tz_localize("UTC")))
        cands = det.run()
        sub = to_submission(cands, p, "gui", "https://github.com/gui/gui")
        self._from_submission(sub)
        mp = det._mp_events() if hasattr(det, "_mp_events") else []
        self.mp = [(e["tr"], naive(e["start"]), naive(e["end"])) for e in mp]
        self.mp_lat = float(np.mean([e["lat"] for e in mp])) if mp else None
        cache.parent.mkdir(exist_ok=True)
        cache.write_bytes(pickle.dumps((self.dets, self.mp, self.mp_lat)))

    @staticmethod
    def b_epoch():
        from straightrows.config import EPOCH
        return EPOCH

    def _from_submission(self, sub):
        if isinstance(sub, (str, Path)):
            sub = json.loads(Path(sub).read_text())
        self.dets = [dict(kind="DET", id=f"D{i + 1:03d}", cls=d["class"], tractors=d["tractors"], start=naive(d["start"]),
                          end=naive(d["end"]), conf=d["confidence"], evidence=d["evidence"], action=d["action"])
                     for i, d in enumerate(sorted(sub["detections"], key=lambda d: d["start"]))]
        if not self.mp:
            self.mp = [(d["tractors"][0], d["start"], d["end"]) for d in self.dets if d["cls"] == "MULTIPATH"]


# ------------------------------------------------------------------------------------------ GUI
class App:
    def __init__(self, data: Data, plt, widgets, mdates):
        self.d, self.plt, self.W, self.md = data, plt, widgets, mdates
        self.span = pd.Timedelta("72h")
        self.t0 = data.t_min
        self.res_mode = "auto"
        self.sel = None                     # index into data.events
        self.vis = {tr: True for tr in TRACTORS}
        self.opt = {"Truth (dev labels)": True, "Our detections": True, "Multipath ticks": True,
                    "Own baselines": True, "XTE log scale": False}
        self._lock = False
        self.cursor = None
        self._build()
        self.update()

    # ---------------------------------------------------------------- layout
    def _build(self):
        plt, W = self.plt, self.W
        self.fig = plt.figure(figsize=(19, 10.5))
        try:
            self.fig.canvas.manager.set_window_title("Straight Rows - investigation")
        except Exception:
            pass
        names = ["mean C/N0 (dB-Hz)", "min C/N0 (dB-Hz)", "|XTE| (m)", "satellites", "fix quality", "base link", "SDA"]
        x0, w = 0.262, 0.425
        top, bot = 0.955, 0.115
        h = (top - bot) / len(names)
        self.axs = []
        for i, n in enumerate(names):
            ax = self.fig.add_axes([x0, top - (i + 1) * h + 0.006, w, h - 0.012], sharex=self.axs[0] if self.axs else None)
            ax.set_ylabel(n, fontsize=8)
            self.axs.append(ax)
        self.ax_s4 = self.axs[-1].twinx()
        self.ax_map = self.fig.add_axes([0.72, 0.52, 0.265, 0.43])
        self.ax_info = self.fig.add_axes([0.70, 0.115, 0.295, 0.37])
        self.ax_info.axis("off")
        self.hover_txt = self.fig.text(0.262, 0.012, "", fontsize=8, family="monospace", va="bottom")

        # controls (left column)
        self.fig.text(0.012, 0.985, "Straight Rows", fontsize=13, weight="bold", va="top")
        ax = self.fig.add_axes([0.012, 0.865, 0.085, 0.085]); ax.set_title("Tractors", fontsize=9, loc="left")
        self.cb_tr = W.CheckButtons(ax, TRACTORS, [True] * 3)
        for lab, tr in zip(self.cb_tr.labels, TRACTORS):
            lab.set_color(TR_COLOR[tr]); lab.set_fontweight("bold")
        self.cb_tr.on_clicked(self._on_tr)

        ax = self.fig.add_axes([0.012, 0.64, 0.085, 0.205]); ax.set_title("Window span", fontsize=9, loc="left")
        self.rb_span = W.RadioButtons(ax, list(SPANS), active=list(SPANS).index("72 h"))
        self.rb_span.on_clicked(self._on_span)

        ax = self.fig.add_axes([0.012, 0.50, 0.085, 0.125]); ax.set_title("Resolution", fontsize=9, loc="left")
        self.rb_res = W.RadioButtons(ax, ["auto", "1 s", "10 s", "60 s"], active=0)
        self.rb_res.on_clicked(self._on_res)

        ax = self.fig.add_axes([0.105, 0.50, 0.100, 0.45]); ax.set_title("Overlays", fontsize=9, loc="left")
        self.cb_opt = W.CheckButtons(ax, list(self.opt), list(self.opt.values()))
        self.cb_opt.on_clicked(self._on_opt)

        def button(rect, label, cb):
            b = W.Button(self.fig.add_axes(rect), label); b.label.set_fontsize(8); b.on_clicked(cb); return b
        self.btn = [
            button([0.012, 0.43, 0.093, 0.04], "< prev event (p)", lambda e: self.step_event(-1)),
            button([0.110, 0.43, 0.093, 0.04], "next event (n) >", lambda e: self.step_event(+1)),
            button([0.012, 0.385, 0.093, 0.04], "< pan", lambda e: self.pan(-0.5)),
            button([0.110, 0.385, 0.093, 0.04], "pan >", lambda e: self.pan(+0.5)),
            button([0.012, 0.34, 0.093, 0.04], "zoom in (up)", lambda e: self.zoom(0.5)),
            button([0.110, 0.34, 0.093, 0.04], "zoom out (down)", lambda e: self.zoom(2.0)),
            button([0.012, 0.295, 0.191, 0.04], "full 72 h", lambda e: self.show_all()),
            button([0.012, 0.25, 0.191, 0.04], "snapshot PNG (s)", lambda e: self.snapshot()),
        ]
        self.ev_txt = self.fig.text(0.012, 0.215, "", fontsize=8, va="top", wrap=True)
        ax = self.fig.add_axes([0.262, 0.045, 0.425, 0.022])
        self.sl = W.Slider(ax, "start (h)", 0, 72, valinit=0, valfmt="%.1f")
        self.sl.label.set_fontsize(8)
        self.sl.on_changed(self._on_slider)

        c = self.fig.canvas
        c.mpl_connect("key_press_event", self._on_key)
        c.mpl_connect("button_press_event", self._on_click)

    # ---------------------------------------------------------------- callbacks
    def _on_tr(self, label):
        self.vis[label] = not self.vis[label]; self.update()

    def _on_span(self, label):
        if self._lock:
            return
        c = self.t0 + self.span / 2
        self.span = pd.Timedelta(SPANS[label]); self.t0 = c - self.span / 2; self.update()

    def _on_res(self, label):
        self.res_mode = label; self.update()

    def _on_opt(self, label):
        self.opt[label] = not self.opt[label]; self.update()

    def _on_slider(self, val):
        if self._lock:
            return
        self.t0 = self.d.t_min + pd.Timedelta(hours=float(val)); self.update(slider=False)

    def _on_key(self, ev):
        k = ev.key
        if k == "n": self.step_event(+1)
        elif k == "p": self.step_event(-1)
        elif k == "right": self.pan(+0.25)
        elif k == "left": self.pan(-0.25)
        elif k == "up": self.zoom(0.5)
        elif k == "down": self.zoom(2.0)
        elif k == "s": self.snapshot()

    def _on_click(self, ev):
        """Single click on a time panel: read values at that time (cursor line). Double click: zoom 4x there.
        Readout is click-driven, not hover-driven, because every redraw is re-sent to the browser (flicker/lag)."""
        if ev.inaxes not in self.axs or ev.xdata is None or ev.button != 1 or self._toolbar_active():
            return
        t = naive(self.md.num2date(ev.xdata))
        if ev.dblclick:
            self.span = self.span / 4
            self.t0 = t - self.span / 2
            self.cursor = None
            self.update()
        else:
            self.cursor = t
            self._readout(t)
            self.update()

    def _toolbar_active(self):
        tb = getattr(self.fig.canvas, "toolbar", None)
        mode = getattr(tb, "mode", "")
        return bool(str(mode))

    def _readout(self, t):
        lines = [f"{t:%Y-%m-%d %H:%M:%S}Z"]
        for tr in TRACTORS:
            if not self.vis[tr]:
                continue
            df = self.d.frames[self.res][tr]
            i = min(df.index.searchsorted(t), len(df) - 1)
            r = df.iloc[i]
            lines.append(f"{tr} C/N0 {r.cn0:5.1f} min {r.mincn0:5.1f} |XTE| {r.xte:6.2f} sats {r.sats:4.1f} "
                         f"{FIX_NAME.get(int(r.fix), '?'):10s} link {'ok' if r.link >= 1 else 'DOWN'}")
        self.hover_txt.set_text("\n".join(lines))

    # ---------------------------------------------------------------- navigation
    def pan(self, frac):
        self.t0 += self.span * frac; self.update()

    def zoom(self, f):
        order = list(SPANS.values()); cur = min(range(len(order)), key=lambda i: abs(pd.Timedelta(order[i]) - self.span))
        new = order[int(np.clip(cur + (-1 if f < 1 else 1), 0, len(order) - 1))]
        c = self.t0 + self.span / 2; self.span = pd.Timedelta(new); self.t0 = c - self.span / 2; self.update()

    def show_all(self):
        self.span = pd.Timedelta("72h"); self.t0 = self.d.t_min; self.sel = None; self.update()

    def step_event(self, d):
        ev = self.d.events
        if not ev:
            return
        self.sel = 0 if self.sel is None else (self.sel + d) % len(ev)
        self.goto(self.sel)

    def goto(self, i):
        self.sel = i
        e = self.d.events[i]
        dur = (e["end"] - e["start"]) * 4
        opts = [pd.Timedelta(v) for v in SPANS.values()]
        self.span = next((o for o in opts if o >= max(dur, pd.Timedelta("10min"))), opts[-1])
        self.t0 = e["start"] + (e["end"] - e["start"]) / 2 - self.span / 2
        self.update()

    def snapshot(self, path=None):
        path = Path(path) if path else ROOT / "outputs" / f"gui_{self.t0:%Y%m%dT%H%M}_{int(self.span.total_seconds() / 60)}min.png"
        path.parent.mkdir(exist_ok=True)
        self.fig.savefig(path, dpi=130)
        print("saved", path, flush=True)
        self.ev_txt.set_text(f"saved {path.name}")
        self.fig.canvas.draw_idle()

    # ---------------------------------------------------------------- drawing
    @property
    def res(self):
        if self.res_mode != "auto":
            return int(self.res_mode.split()[0])
        s = self.span.total_seconds()
        return 1 if s <= 3 * 3600 else 10 if s <= 14 * 3600 else 60

    def _clamp(self):
        lo, hi = self.d.t_min, self.d.t_max
        self.span = min(self.span, hi - lo)
        self.t0 = min(max(self.t0, lo), hi - self.span)

    def update(self, slider=True):
        self._clamp()
        d, t0 = self.d, self.t0
        t1 = t0 + self.span
        pad = self.span * 0.02
        res = self.res
        self._lock = True
        try:
            if slider:
                self.sl.set_val((t0 - d.t_min).total_seconds() / 3600)
            lbl = next((k for k, v in SPANS.items() if pd.Timedelta(v) == self.span), None)
            if lbl and self.rb_span.value_selected != lbl:
                self.rb_span.set_active(list(SPANS).index(lbl))
        finally:
            self._lock = False
        for ax in self.axs + [self.ax_s4]:
            ax.clear()
        a_cn0, a_min, a_xte, a_sat, a_fix, a_lnk, a_sda = self.axs
        for k, tr in enumerate(TRACTORS):
            if not self.vis[tr]:
                continue
            df = d.frames[res][tr].loc[t0 - pad:t1 + pad]
            col, lw = TR_COLOR[tr], 0.9 if res == 1 else 1.1
            a_cn0.plot(df.index, df.cn0, color=col, lw=lw, label=tr)
            a_min.plot(df.index, df.mincn0, color=col, lw=lw)
            a_xte.plot(df.index, df.xte, color=col, lw=lw)
            a_sat.plot(df.index, df.sats, color=col, lw=lw)
            a_fix.step(df.index, df.fix + 0.07 * k, color=col, lw=1.1, where="post")
            a_lnk.step(df.index, df.link * 0.8 + 1.0 * k, color=col, lw=1.1, where="post")
            if self.opt["Own baselines"]:
                a_cn0.axhline(d.base[tr]["cn0"], color=col, ls=":", lw=0.8, alpha=0.6)
                a_min.axhline(d.base[tr]["mincn0"], color=col, ls=":", lw=0.8, alpha=0.6)
        a_xte.set_yscale("symlog", linthresh=0.1) if self.opt["XTE log scale"] else None
        a_xte.axhline(1.0, color="k", ls=":", lw=0.6, alpha=0.4)
        a_fix.set_yticks(range(5)); a_fix.set_yticklabels([FIX_NAME[i] for i in range(5)], fontsize=6)
        a_lnk.set_yticks([0.4 + k for k in range(3)]); a_lnk.set_yticklabels([f"{t} up" for t in TRACTORS], fontsize=7)
        a_lnk.set_ylim(-0.3, 3.0)
        a_cn0.legend(loc="lower left", ncols=3, fontsize=7, frameon=False)
        res_lbl = f"{res} s bins ({'max' if res > 1 else 'raw'} |XTE|, min of min C/N0, worst fix/link)" if res > 1 else "1 Hz raw"
        a_cn0.set_title(f"{t0:%Y-%m-%d %H:%M} to {t1:%Y-%m-%d %H:%M} Z   |   {res_lbl}", fontsize=9, loc="left")
        self._sda(a_sda, self.ax_s4, t0, t1)
        self._overlays(t0, t1)
        self._map(t0, t1, res)
        self._info()
        for ax in self.axs:
            ax.grid(alpha=0.25)
            ax.tick_params(labelsize=7, labelbottom=False)
        self.axs[-1].tick_params(labelbottom=True)
        loc = self.md.AutoDateLocator(minticks=4, maxticks=9)
        self.axs[-1].xaxis.set_major_locator(loc)
        self.axs[-1].xaxis.set_major_formatter(self.md.ConciseDateFormatter(loc))
        self.axs[0].set_xlim(t0, t1)
        self.fig.canvas.draw_idle()

    def _sda(self, ax, ax2, t0, t1):
        d = self.d
        kp = d.kp[(d.kp.ts >= t0 - pd.Timedelta("6h")) & (d.kp.ts <= t1 + pd.Timedelta("6h"))]
        ax.step(kp.ts, kp.kp, where="post", color="#444444", lw=1.2, label="Kp")
        ax.axhline(5, color="#444444", ls=":", lw=0.7)
        ax.set_ylim(0, 9.5); ax.set_ylabel("SDA   Kp", fontsize=8)
        s4 = d.s4[(d.s4.ts >= t0 - pd.Timedelta("1h")) & (d.s4.ts <= t1 + pd.Timedelta("1h"))]
        ax2.plot(s4.ts, s4.s4, color="#6a3d9a", lw=1.1, label="S4")
        ax2.axhline(0.30, color="#6a3d9a", ls=":", lw=0.7)
        ax2.set_ylim(0, max(0.6, float(s4.s4.max()) * 1.1 if len(s4) else 0.6))
        ax2.set_ylabel("S4", fontsize=8, color="#6a3d9a"); ax2.tick_params(labelsize=7, colors="#6a3d9a")
        r = d.rfi
        if len(r):
            for _, x in r[(r.ts >= t0 - pd.Timedelta("1h")) & (r.ts <= t1)].iterrows():
                ax.axvline(x.ts, color="#e31a1c", lw=1.4, alpha=0.8)
                ax.text(x.ts, 9.2, f" RFI {x.get('id', '')} c={x.get('confidence', 0):.2f}", color="#e31a1c", fontsize=7, va="top")
        for a in d.b.advisories:
            ts = naive(a.get("ts"))
            if t0 <= ts <= t1:
                ax.axvline(ts, color="#888888", ls="--", lw=0.9)
                ax.text(ts, 0.3, f" {a.get('type', 'ADV')}", color="#555555", fontsize=7)

    def _overlays(self, t0, t1):
        d, span = self.d, self.span
        vis = lambda e: e["end"] >= t0 and e["start"] <= t1
        if self.opt["Our detections"]:
            for e in d.dets:
                if e["cls"] == "MULTIPATH" or not vis(e):
                    continue
                for ax in self.axs:
                    ax.axvspan(e["start"], e["end"], color=CLASS_COLOR[e["cls"]], alpha=0.13, lw=0)
                self._label(e, 0.97, "right" if False else "left", f"{CLASS_SHORT[e['cls']]} {','.join(e['tractors'])} {e['conf']:.2f}")
        if self.opt["Truth (dev labels)"]:
            for e in d.truth:
                if not vis(e):
                    continue
                for ax in self.axs:
                    for x in (e["start"], e["end"]):
                        ax.axvline(x, color=CLASS_COLOR[e["cls"]], ls="--", lw=1.1, alpha=0.9)
                self._label(e, 0.82, "left", f"TRUTH {e['id']} {CLASS_SHORT[e['cls']]} {','.join(e['tractors'])}")
        if self.opt["Multipath ticks"] and span <= pd.Timedelta("24h"):
            for tr, s, _ in d.mp:
                if t0 <= s <= t1 and self.vis[tr]:
                    self.axs[1].plot([s], [0.04 + 0.06 * TRACTORS.index(tr)], marker="^", ms=4, color=TR_COLOR[tr],
                                     transform=self.axs[1].get_xaxis_transform(), clip_on=True, ls="none")
        if self.cursor is not None and t0 <= self.cursor <= t1:
            for ax in self.axs:
                ax.axvline(self.cursor, color="k", lw=0.8, alpha=0.7)
        if self.sel is not None:
            e = d.events[self.sel]
            for ax in self.axs:
                ax.axvspan(e["start"], e["end"], facecolor="none", edgecolor="k", lw=1.2, alpha=0.8)

    def _label(self, e, y, ha, text):
        if (e["end"] - e["start"]) < self.span * 0.015:
            text = ""
        if text:
            self.axs[0].text(max(e["start"], self.t0), y, " " + text, transform=self.axs[0].get_xaxis_transform(),
                             fontsize=7, color=CLASS_COLOR[e["cls"]], va="top", ha=ha, clip_on=True, weight="bold")

    def _map(self, t0, t1, res):
        d, ax = self.d, self.ax_map
        ax.clear()
        mres = 1 if self.span <= pd.Timedelta("1h") else 10 if self.span <= pd.Timedelta("12h") else 60
        sel = d.events[self.sel] if self.sel is not None else None
        for tr in TRACTORS:
            if not self.vis[tr]:
                continue
            g = d.frames[mres][tr].loc[t0:t1]
            g = g[g.spd > 1.0] if len(g) else g
            ax.plot(g.lon, g.lat, ".", ms=1.5 if mres > 1 else 2.5, color=TR_COLOR[tr], alpha=0.5, label=tr)
            if sel is not None and tr in sel["tractors"]:
                s = g.loc[sel["start"]:sel["end"]]
                ax.plot(s.lon, s.lat, ".", ms=3.5, color=CLASS_COLOR[sel["cls"]], alpha=0.9)
            if len(g):
                ax.plot(g.lon.iloc[-1], g.lat.iloc[-1], "o", ms=6, color=TR_COLOR[tr], mec="k")
        r = d.rfi
        if len(r) and {"centerLat", "centerLon"} <= set(r.columns):
            from matplotlib.patches import Ellipse
            for _, x in r[(r.ts >= t0 - pd.Timedelta("1h")) & (r.ts <= t1 + pd.Timedelta("1h"))].iterrows():
                a = float(x.get("semiMajor_km", 1)) * 1000 / 111320 * 2
                b = float(x.get("semiMinor_km", 1)) * 1000 / 111320 * 2 / np.cos(np.radians(46.9))
                ax.add_patch(Ellipse((x.centerLon, x.centerLat), width=b, height=a, fill=False, ec="#e31a1c", lw=1.2))
                ax.plot(x.centerLon, x.centerLat, "x", color="#e31a1c")
                ax.annotate(f"RFI {x.get('id', '')}", (x.centerLon, x.centerLat), fontsize=7, color="#e31a1c")
        if d.mp_lat and self.opt["Multipath ticks"]:
            ax.axhline(d.mp_lat, color="#7f7f7f", ls="--", lw=0.9)
        allg = pd.concat([d.frames[60][tr] for tr in TRACTORS])
        pad_deg = 0.0012
        ax.set_xlim(allg.lon.min() - pad_deg * 2, allg.lon.max() + pad_deg * 2)      # ellipses may exceed the field; clip them
        ax.set_ylim(allg.lat.min() - pad_deg, allg.lat.max() + pad_deg)
        ax.set_aspect(1 / np.cos(np.radians(46.9)), adjustable="box")
        if d.mp_lat and self.opt["Multipath ticks"]:
            ax.annotate("multipath band", (ax.get_xlim()[0], d.mp_lat), fontsize=7, color="#555555", va="bottom", xytext=(3, 2), textcoords="offset points")
        ax.set_title("Field map (working samples)" + (f"; {sel['cls']} {sel['id']} highlighted" if sel else ""), fontsize=8, loc="left")
        ax.tick_params(labelsize=6); ax.grid(alpha=0.25); ax.ticklabel_format(useOffset=False)
        ax.set_xlabel("lon", fontsize=7); ax.set_ylabel("lat", fontsize=7)

    def _info(self):
        ax = self.ax_info
        ax.clear(); ax.axis("off")
        d = self.d
        if self.sel is None:
            n_det = sum(1 for e in d.dets if e["cls"] != "MULTIPATH")
            txt = (f"{len(d.truth)} labelled dev episodes, {n_det} detections (+{len(d.mp_dets)} multipath passes).\n\n"
                   "n / p  step through events (truth + ours)\nclick a panel: cursor + values at that time (bottom)\n"
                   "double-click: zoom 4x there; toolbar zoom/pan also work\n\n"
                   "Dashed lines = labelled truth (dev only)\nShaded span = our detection (label: class tractors confidence)\n"
                   "Dotted horizontal = own healthy baseline / thresholds")
            ax.text(0, 1, txt, va="top", fontsize=8, family="monospace")
            self.ev_txt.set_text("no event selected")
            return
        e = d.events[self.sel]
        dur = (e["end"] - e["start"]).total_seconds() / 60
        others = [o for o in d.events if o is not e and o["end"] > e["start"] and o["start"] < e["end"]]
        head = (f"[{self.sel + 1}/{len(d.events)}] {e['kind']} {e['id']}  {e['cls']}\n"
                f"tractors {','.join(e['tractors'])}   {e['start']:%m-%d %H:%M:%S} -> {e['end']:%H:%M:%S}Z  ({dur:.1f} min)\n"
                + (f"confidence {e['conf']:.2f}   " if e["conf"] is not None else "") + f"action {e['action']}\n"
                + ("overlaps: " + "; ".join(f"{o['kind']} {o['id']} {CLASS_SHORT[o['cls']]}" for o in others) + "\n" if others else ""))
        body = ""
        for line in e["evidence"][:7]:
            body += "\n".join(textwrap.wrap(line, 78)[:3]) + "\n"
        ax.text(0, 1, head + "\n" + body, va="top", fontsize=7.2, family="monospace", color="#111111")
        self.ev_txt.set_text(f"{e['kind']} {e['id']}\n{e['cls']}")

    def run(self):
        self.plt.show()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--backend", help="TkAgg, QtAgg, webagg (default: auto)")
    ap.add_argument("--submission", help="show detections from this submission.json instead of running the detector")
    ap.add_argument("--refresh", action="store_true", help="re-run the detector instead of using outputs/gui_cache.pkl")
    ap.add_argument("--save", help="render to this PNG and exit (headless)")
    ap.add_argument("--event", type=int, help="with --save: 1-based event number")
    ap.add_argument("--start"); ap.add_argument("--span", default="6h")
    a = ap.parse_args()

    import matplotlib
    backend = "Agg" if a.save else pick_backend(a.backend)
    matplotlib.use(backend)
    if backend.lower() == "webagg":
        matplotlib.rcParams["webagg.open_in_browser"] = True
        matplotlib.rcParams["webagg.port"] = 8988
    for k in ("keymap.pan", "keymap.back", "keymap.forward", "keymap.save", "keymap.help"):
        matplotlib.rcParams[k] = []
    matplotlib.rcParams["path.simplify_threshold"] = 0.5
    matplotlib.rcParams["agg.path.chunksize"] = 10000
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import matplotlib.widgets as widgets

    data = Data(a.data, a.refresh, a.submission)
    app = App(data, plt, widgets, mdates)
    if a.save:
        if a.event:
            app.goto(a.event - 1)
        elif a.start:
            app.span = pd.Timedelta(a.span); app.t0 = naive(a.start); app.update()
        app.snapshot(a.save)
        return
    print(f"backend {backend}" + ("  -> open http://127.0.0.1:8988 if the browser does not appear" if backend.lower() == "webagg" else ""))
    app.run()


if __name__ == "__main__":
    main()
