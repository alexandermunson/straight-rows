# Viewer feature inventory (redesign parity checklist)

Baseline: commit `b43bee3` (snapshot of the working tree before the redesign, including the then-uncommitted Spectral tab).
Code: `scripts/viewer.py` (server, CLI, data prep), `scripts/viewer_spectral.py` (spectral computation),
`scripts/viewer_static/index.html` (all CSS and JS). `scripts/gui_matplotlib.py` is out of scope (shared loader only).

**Verified by** refers to the evidence collected after the redesign:
- `P:values`, `P:env`, `P:hash`, `P:step <name>`: automated readback in `redesign/tools/parity.mjs`, compared old vs new by `compare_parity.mjs` (identical = verified). Results in `redesign/compare/parity-diff.txt`.
- `S:<name>`: before/after screenshot pair in `redesign/compare/<name>.png` (left old, right new).
- `B`: `?bench` timings, `redesign/compare/bench.txt`. `C`: contrast check `redesign/compare/contrast.txt`. `K`: keyboard pass `redesign/compare/keyboard.txt`. `M`: manual/code check described in REPORT.md.

## 1. Server and command line (`viewer.py`)

| # | Feature | Implemented in | Verified by |
|---|---|---|---|
| 1.1 | `--port N`, default 8765 | `main` | M (second instance on another port, REPORT 4.7) |
| 1.2 | `--no-open` (else opens the browser after 0.5 s) | `main` | M |
| 1.3 | `--refresh` re-runs the detector, else `outputs/gui_cache.pkl` | `main`, `gui_matplotlib.Data` | M (unchanged file) |
| 1.4 | `--submission file.json`: detections from the file; reasoning, operator cards and source markers empty | `Data._from_submission` | S:sub-* |
| 1.5 | `--data dir` | `main` | M (baseline and new servers both started with it) |
| 1.6 | Endpoints `/`, `/index.html`, `/meta.json`, `/spectral.json`, `/lvl/1.bin`, `/lvl/10.bin`, `/lvl/60.bin`, 404 otherwise | `make_handler.do_GET` | M (`viewer.py` not modified) |
| 1.7 | `Cache-Control: no-store` on every response | `make_handler._send` | M (not modified) |
| 1.8 | Test hooks: `GET /ready.png` waits for `POST /ready`; `POST /perf` prints timings | `do_GET`, `do_POST` | every capture run, B |
| 1.9 | Levels: float32, per resolution, per tractor, per channel (cn0, mincn0, xte, sats, fix, link, lat−46.9, lon+97.2, spd) | `build_levels` | P:values |
| 1.10 | Meta JSON: events (kind, id, cls, tr, s, e, conf, action, ev, why, src, ops), kp, s4, dst, rfi, adv, base, calib, field, fix_names, mp, mp_lat | `build_meta` | P:values, all screenshots |
| 1.11 | Spectral JSON (PSD per channel and scope, spectrogram per channel and tractor) computed once at startup | `viewer_spectral.compute` | S:spec-* |

## 2. Page-wide

| # | Feature | Implemented in | Verified by |
|---|---|---|---|
| 2.1 | Three tabs: Investigate, Operator actions, Spectral (buttons at the top of the left column) | `showTab`, `#tabs` | P:step spectral tab / investigate tab, S:ops-*, S:spec-* |
| 2.2 | Ops and Spectral hide the Investigate controls (sidebar shows title and tabs only) and the map column | CSS `body.ops`, `body.spec` | S:ops-day3, S:spec-default |
| 2.3 | Light and dark theme from `prefers-color-scheme`, redraw on change | CSS media query, `matchMedia` listeners | M (dark tokens checked in C) |
| 2.4 | Hash read on load: `t0`, `t1`, `ev`, `map=all`, `tab=ops&day=N[&open]`, `tab=spec&ch&scope&tr` | `init` | P:hash (10 forms incl. a link written by the old version) |
| 2.5 | Hash written 300 ms after any move or selection: `#t0=…&t1=…&ev=…` (drops `tab`/`map`, as before) | `scheduleHash` | P:hash (savedHash identical) |
| 2.6 | Keys: `n`/`p` next/previous visible event (wraps), ←/→ pan a fifth, `+`/`=` zoom in ×0.6, `-` zoom out ×1.6, `0` show all and clear selection; ignored in a `<select>` or with Ctrl/Meta | `bindInput` keydown, `step`, `pan`, `zoomAt` | P:step key * |
| 2.7 | Smooth animated moves (exponential, 75 ms time constant), 20 s minimum window, clamped to the 72 h | `frame`, `setTarget` | P:step key +/-, M |
| 2.8 | Detail level: 1 s, 10 s or 60 s so a series has ≤ 20,000 points; levels load 60 s → 10 s → 1 s | `pickRes`, `init`, `loadLevel` | P:env (res per window), S:inv-* |
| 2.9 | `?test` holds the load event until drawn; `?bench` posts timings | `init`, `bench` | capture runs, B |

## 3. Investigate: left column

| # | Feature | Implemented in | Verified by |
|---|---|---|---|
| 3.1 | Tractor toggles T1–T3 (plots, strip, map, multipath ticks, hover) | `buildUI` `#trs`, `S.vis` | P:step tractor toggle 0–2, S:inv-tractor-T2-hidden |
| 3.2 | Window buttons 10 min, 1 h, 3 h, 6 h, 12 h, 24 h, 72 h (keep centre), all | `buildUI` `#spans` | P:step window button 0–7 |
| 3.3 | "Layers on the plots", closed by default: truth lines, our detections, multipath, baselines (all on), XTE log (off) | `buildUI` `#opts` | P:step layer toggle 0–4, S:inv-layers-open, S:inv-layers-all-toggled |
| 3.4 | Cause filter (options = causes present) | `buildUI` `#fCls`, `visibleEvents` | P:step cause filter *, S:inv-filter-jamming |
| 3.5 | Event row: cause dot, cause name, machines or "all machines", event id, start `MM-DD HH:MMZ`, duration (s/m/h), confidence chip, "✓ TRN-xx" match | `buildList`, `matchLabels` | P:step (rows list), S:inv-all-72h-60s |
| 3.6 | Missed row for an unmatched labelled episode ("TRN-xx missed"); none occur in this data | `buildList` | S:inv-missed-row-synthetic (forced in the page) |
| 3.7 | Matched labelled episodes have no row of their own | `visibleEvents` | P:step (rows) |
| 3.8 | Row click: select, zoom to 4× duration (≥ 10 min), centre, mark row, scroll into view, fill info, highlight map path, write hash | `select` | P:step click event row *, S:inv-sel-* |
| 3.9 | Key hints at the bottom | `#keys` | S:inv-all-72h-60s |

## 4. Investigate: centre

| # | Feature | Implemented in | Verified by |
|---|---|---|---|
| 4.1 | Overview strip: 72 h mean C/N0 per visible tractor, tinted detections, labelled-episode bars along the bottom, day labels, window rectangle | `drawStrip` | S:inv-* |
| 4.2 | Strip: click/drag moves the window keeping its length; wheel zooms about that time | `bindInput` strip handlers | M (handlers unchanged) |
| 4.3 | Seven panels sharing the x axis: mean C/N0, min C/N0, \|XTE\|, satellites, fix quality, base link, SDA Kp/S4 (relative heights 1.2/1.2/1/0.8/0.8/0.75/1.15) | `PANELS`, `layout` | S:inv-*, P:env |
| 4.4 | Y ranges: C/N0 data ±8% (≥ 0.5) incl. baselines; XTE 0…max(1.1×max, 0.35 / 0.5 log); fix −0.4…4.6; link −0.2…3; SDA 0…9.5 | `drawBase` | P:env (y0/y1 identical) |
| 4.5 | Time ticks and labels (date at midnight or day steps, seconds below 1 min) | `timeTicks`, `tickLabel` | S:inv-* |
| 4.6 | Dense windows: one min-to-max bar per pixel column, joined to the previous column; sparse windows: true polyline | `dec`, `strokeSeries` | P:env (envelope sums identical) |
| 4.7 | Fix quality stepped (NO FIX/AUTO/DGPS/FLOAT/FIXED), tractors offset 0.09; base link up/down steps, one row per tractor | `drawBase` | S:inv-1h-1s |
| 4.8 | Baselines dotted on the two C/N0 panels; XTE 1 m dotted reference (log-aware) | `drawBase` | S:inv-1h-1s, S:inv-layers-all-toggled |
| 4.9 | SDA: Kp step line (left axis), Kp 5 dotted; S4 on right axis 0–0.6 (capped), S4 0.3 dashed, right labels 0/0.2/0.4/0.6; RFI red line "RFI id c=conf"; advisories dashed with type | `drawSDA` | S:inv-sel-jam-source, S:inv-all-72h-60s |
| 4.10 | Detections shaded across all panels; labelled episodes dashed start/end lines; text labels when ≥ 26 px wide (ours on line 1, labelled on line 2) | `drawOverlays` | S:inv-sel-overlap-jam-in-storm |
| 4.11 | Multipath triangles on the min C/N0 panel per tractor, only when the window ≤ 6 h | `drawOverlays` | S:inv-1h-1s, S:inv-12h-10s (absent) |
| 4.12 | Selected event outlined on every panel | `drawOverlays` | S:inv-sel-* |
| 4.13 | Status: "`N` s bins · start → end", "loading…", "loading 1 s data…" | `drawBase`, `init` | S:inv-*, P (status text) |
| 4.14 | Hover: crosshair, tooltip (time to the second; per visible tractor C/N0, min, \|XTE\|, sats, fix, link ok/DOWN; Kp ≤ t, nearest S4 and Dst), dots on the first four panels, flips side and clamps | `drawTop` | S:inv-hover-tooltip |
| 4.15 | Wheel zoom about the pointer (line-mode ×16), shift/horizontal wheel pans, drag pans (3 px threshold), double-click zooms ×4, leaving hides hover | `bindInput` | M (handlers unchanged), P:step |

## 5. Investigate: right column

| # | Feature | Implemented in | Verified by |
|---|---|---|---|
| 5.1 | Map: field outline, working positions (speed > 1 m/s), latest position dot, selected event path in cause colour | `drawMap` | S:inv-sel-jam-source |
| 5.2 | Map data resolution: 1 s (window ≤ 90 min), 10 s (≤ 18 h), 60 s | `drawMap` | S:inv-1h-1s, S:inv-12h-10s, S:inv-all-72h-60s |
| 5.3 | Multipath band dashed line with label (when the multipath layer is on) | `drawMap` | S:inv-all-72h-60s |
| 5.4 | RFI ellipses with X and id (reports within ±1 h of the window); red hint when an ellipse extends past the view | `drawMap` | S:inv-sel-jam-source, S:inv-map-whole-area |
| 5.5 | Estimated source for a selected jam: circle (uncertainty radius) and cross, label placed left if it would overflow | `drawMap` | S:inv-sel-jam-source |
| 5.6 | Scale bar (10 m … 10 km), caption "Field map · id" | `drawMap` | S:inv-map-* |
| 5.7 | Map controls: Field, Whole report area (`.on` state), −, +; wheel zoom about pointer (0.02–200×), drag pan, double-click resets keeping the mode; `&map=all` | `bindInput` map block | P:step map *, P:hash map=all, S:inv-map-* |
| 5.8 | Legend: cause colours, "dashed = labelled · shaded = ours · ▲ = multipath pass" | `buildUI` | S:inv-* |
| 5.9 | Info, empty: counts and how-to hint | `buildUI` | S:inv-all-72h-60s |
| 5.10 | Info, detection: cause and confidence, machines, start → end to the second, duration, event id, matched label, overlaps; action and one-sentence reason; "See this in Operator actions"; "Why this is marked as…" and "Why not something else" open; "More detail" and "Measurements as submitted" folded | `select` | S:inv-sel-*, S:inv-info-folds-open, P:step see in operator actions |
| 5.11 | Info, labelled episode: "Labelled episode: cause", detected-by or not detected, note | `select` | S:inv-sel-labelled-episode |
| 5.12 | Info, `--submission` mode: action from the code, "Evidence lines (n)" folded | `select` | S:sub-inv-sel-jam |

## 6. Operator actions

| # | Feature | Implemented in | Verified by |
|---|---|---|---|
| 6.1 | Day buttons 05-04 / 05-05 / 05-06, default evaluation day | `renderOps`, `OPS.day` | P:hash tab=ops, P:step ops day button 0, S:ops-day* |
| 6.2 | Lanes: one per tractor, one block per action, title "HH:MM–HH:MMZ · action", click opens and scrolls to the card; hour labels; cause key and parked note | `renderOps` | S:ops-day3 |
| 6.3 | Confidence trust box, folded: bands, detections, correct, caveat | `renderOps` | S:ops-trust-open |
| 6.4 | Columns: summary (actions, hours flagged, overlaps once) or "No action needed this day" | `renderOps` | S:ops-day* |
| 6.5 | Cards, folded: time, duration, cause, shared machines, confidence, action, reason; opened: seven sections; nested fold for other machines, feeds, tally; "Show event … in plots" | `renderOps` | S:ops-card-open, S:ops-card-more-open, P:step ops: show event in plots |
| 6.6 | `&open` opens the first card | `renderOps` | P:hash tab=ops&day=2&open=1 |
| 6.7 | Headland reflections note per column | `renderOps` | S:ops-day3 |

## 7. Spectral

| # | Feature | Implemented in | Verified by |
|---|---|---|---|
| 7.1 | Fetched on first open; "Computing…" and error text | `renderSpec` | S:spec-default |
| 7.2 | Intro and folded "How to read these plots" | `buildSpec` | S:spec-howto-open |
| 7.3 | Controls: signal, spectrum period, tractor checkboxes, overtone markers | `buildSpec` | P:step spectral channel xte, S:spec-overtones-off-T2-hidden |
| 7.4 | PSD: 0–50 mHz, ticks every 5 mHz with period below, dB axis from rates above 1 mHz, one line per tractor, dashed repeat-rate markers to ×8, "No working time" empty text | `drawPsd` | S:spec-* |
| 7.5 | Summary sentence and per-tractor table (period, strength, hours) | `fillSpecTable` | S:spec-default |
| 7.6 | PSD hover: crosshair, frequency, period, each tractor's dB | `drawPsd` | S:spec-hover |
| 7.7 | Spectrogram per tractor (buttons), viridis colour, robust range (3–99.5 percentile), blank for skipped windows, frequency and time ticks, event bar above (not multipath), colour bar with dB, key | `specImage`, `drawSpec` | S:spec-*, P:step spectral tractor T3 |
| 7.8 | Spectrogram hover: crosshair, time, frequency, period, power or "no data", active events | `drawSpec` | S:spec-hover |
| 7.9 | Redraw on resize and theme change | `buildSpec` listeners | M |

## 8. Findings from the inventory (gaps in the baseline)

| # | Finding | Decision |
|---|---|---|
| G1 | Event rows are `<div>`s with a click handler: not reachable by keyboard | Fixed: focusable, Enter/Space select |
| G2 | Operator lane blocks are `<a>` without `href`: not reachable by keyboard | Fixed: focusable, Enter/Space open |
| G3 | In `--submission` mode the Operator tab says "No action needed this day" and "0 of 0 detections", which reads as a finding | Fixed: an explicit empty state explaining why there are no cards |
| G4 | The hash writer drops `tab=`/`map=all` after any move, so a link copied later loses the tab | Kept (contract); listed in REPORT |
| G5 | Keyboard shortcuts move the hidden Investigate view while Ops or Spectral is open | Kept; listed in REPORT |
| G6 | White text on cause-coloured pills fails contrast (orange about 2.2:1) | Fixed by the chip restyle |
| G7 | Tractor T3 (#CC79A7) sits close to the space-weather purple | Fixed: T3 recoloured |
| G8 | Panel titles are rotated 90°, hard to read | Fixed: horizontal labels |
| G9 | No `:focus-visible` styling anywhere | Fixed |
