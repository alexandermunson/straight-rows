# Viewer redesign: report

Branch `redesign/viewer`. The only product file changed is `scripts/viewer_static/index.html`. `viewer.py`,
`viewer_spectral.py`, `gui_matplotlib.py`, the detector, the Makefile and the requirements are byte-identical to the
snapshot (`git diff --stat b43bee3 -- scripts/viewer.py scripts/viewer_spectral.py scripts/gui_matplotlib.py src` is empty).

## 1. Step 0: protecting the uncommitted work

`git status` before starting showed three uncommitted paths: `scripts/viewer.py` (modified),
`scripts/viewer_static/index.html` (modified) and `scripts/viewer_spectral.py` (untracked), i.e. the Spectral tab.
I created `redesign/viewer` from `main` and committed them unchanged as **`b43bee3` "Snapshot: Spectral tab and
viewer_spectral.py as found"**. Nothing was discarded, reset or stashed. There were no other uncommitted files.
`main` is untouched; the Spectral tab is therefore only on this branch until it is merged.

Two viewer servers were already running when I started (ports 8765 and 8766). I left both alone and used ports
8781–8784 for testing.

## 2. Commits

| Commit | Concern |
|---|---|
| `b43bee3` | snapshot of the working tree as found |
| `b9998e6` | inventory, design brief, baseline captures, verification tools |
| `8a58526` | tokens: light/dark design tokens; canvases read tractor and cause colours from them |
| `58893e8` | layout: one surface with hairlines, vertical tabs, segmented controls, two-line event rows, neutral chips, one fold control |
| `c842cc3` | charts: panel titles above panels, baselines instead of frames, swatch-labelled overlays, ink tooltips, mono ticks |
| `1858604` | map: dashed field outline, neutral labels, mono scale bar, titled caption |
| `7641c75` | tabs: Operator and Spectral restyled; `--submission` empty state for operator cards |
| `4d75bbb` | interaction: keyboard access for rows and lane blocks, focus rings, empty states |
| `3db9a44` | polish: AA fix for tertiary text, themed scrollbars, shorter map hint |
| (last) | final captures, comparisons and this report |

## 3. What changed

- **Colour.** One neutral base (`#FAFAF9` / `#111316`), ink as the only accent, colour reserved for tractors, causes and
  interference. All values are tokens on `:root` with dark-theme values; the canvases read the same tokens
  (`readTheme()`), so plots and HTML always agree and follow the system theme. T3 changed from reddish purple to
  graphite; spoofing orange darkened to pass 3:1.
- **Structure.** No cards, no shadows, no gaps between boxes: three columns on one surface split by 1 px hairlines.
  4 px spacing grid, 4 px radius on controls, 3 px on chips, 0 on panels.
- **Type.** System sans for UI, monospace with tabular numerals for timestamps, ids, chips and axis values. Sizes 11/13/15.
- **Sidebar.** Tabs became a short vertical list with an ink bar (same idiom as a selected event). Window buttons in a
  4×2 grid. Tractor toggles show a line swatch in the tractor's colour (the text stays ink; the old green label text
  failed contrast). Event rows are two lines: cause, machines and match tag on top; id, start, duration and the
  confidence chip below.
- **Confidence chip.** One rule everywhere: mono 11 px, hairline border, ink-2 text, no fill. The old white-on-colour pills
  failed AA for spoofing (2.5:1) and multipath (4.0:1).
- **Folds.** One chevron control for every `<details>` (layers, info sections, operator cards, nested folds, spectral help,
  trust box). Every default open/closed state is unchanged.
- **Charts.** Panel titles are horizontal, with units, in a row above each panel, so they never sit on data (an earlier
  iteration put them inside the plot on a translucent backing; I moved them out because that could hide a spike in the
  corner). Panel frames replaced by one baseline. Overlay labels are ink text after a cause swatch: filled for ours,
  outlined for labelled, so the two differ without colour as well as by dashed-versus-shaded. Tooltips are ink on the base
  with tractor swatches instead of tractor-coloured text. Spectrogram: 9-stop viridis (was 6), a mid tick on the colour bar,
  wider label margin (the frequency labels were clipped).
- **Map.** Field outline dashed, report and source labels neutral, scale bar mono, caption titled. All overlays and the
  three data resolutions unchanged.
- **Operator and Spectral.** Same surface and hairline tables; day buttons and spectrogram tractor buttons are segmented
  controls; cards use a 2 px cause rule instead of a 5 px border and a shadow.
- **Empty and loading states.**
  - `--submission` mode, Operator tab: an explicit note that cards come from the detector (was "No action needed this day"
    and "0 of 0 detections", which read as findings).
  - `--submission` mode, info panel: a note that reasoning is unavailable, then the evidence lines.
  - Empty cause filter: "No events of this cause."
  - Status line adds "· no events in view" when the window holds none.
  - Info panel before a selection: a plain prompt.
- **Keyboard.** Event rows and operator lane blocks are focusable and respond to Enter/Space. `:focus-visible` rings on every
  control. Tab order runs tabs → tractors → window → layers → filter → events → map controls.

## 4. Parity gate

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Every FEATURES.md item verified | done | each row names its evidence |
| 2 | Before/after screenshots in the same state | 44 pairs (35 states, 4 `--submission`, 5 dark theme) | `redesign/compare/*.png`, raw in `baseline/` and `after/` |
| 3 | Rendered series values identical | **0 differences** in 81 sample keys (9 timestamps × 3 resolutions × 3 tractors × 9 channels) and in the min/max envelopes and y ranges of all panels in 4 windows (72 h/60 s, 12 h/10 s, 1 h/1 s, 10 min/1 s) | `compare/parity-diff.txt`, `tools/parity.mjs` |
| 4 | URL-hash forms restore the same view | **0 differences** in 10 forms, including the hash the *old* version wrote after selecting an event, `&map=all`, `#tab=ops&day=0/1/2[&open=1]`, `#tab=spec&ch&scope&tr` | `compare/parity-diff.txt` |
| 5 | Keys, window buttons, layers, tractors, cause filter, event selection and map highlight | **0 differences** in 40 interaction steps (real key presses for `n` `p` ← → `+` `-` `0`); map highlight in `compare/inv-sel-*` | `compare/parity-diff.txt` |
| 6 | Performance | see below | `compare/bench.txt` |
| 7 | `make gui`, `--submission`, second instance | new version ran as two instances at once (8782 normal, 8784 `--submission`) next to two baseline instances (8781, 8783) | `compare/sub-*`, server logs |
| 8 | 1280×720 and larger, keyboard-only, contrast | 1280×720 and 1920×1080 states captured; keyboard pass: 40/40 tab stops show a focus ring (baseline 0/40), Enter/Space select rows and open lane cards (baseline: not reachable); contrast: every text pair ≥ 4.5:1 and every mark ≥ 3:1 in both themes | `compare/size-*`, `compare/keyboard*.txt`, `compare/contrast.txt` |

The state readback taken with every screenshot (`baseline/state*.json` vs `after/state*.json`: tab, window, resolution,
selection, layers, tractors, filter, row count, map mode, operator day, spectral settings) is identical for all states.

**Envelope comparison note.** A first run compared envelopes at each version's actual panel width and showed differences:
the centre column is now 776 px instead of 774 px, so the per-pixel min/max columns fall differently. The decimation code
is unchanged, so the parity script now evaluates envelopes at a fixed 800 px in both versions, where they are identical;
the per-panel minimum and maximum were identical in both runs.

**Performance (item 6).** Interleaved headless runs, medians of 9:

| Window | Baseline full redraw | New | Hover layer (both) |
|---|---|---|---|
| 72 h | 6.2 ms | 6.3 ms | 0.20 ms |
| 24 h | 5.8 ms | 6.2 ms | 0.15 ms |
| 6 h | 4.0 ms | 4.3 ms | 0.15 ms |
| 1 h | 4.2 ms | 4.5 ms | 0.15 ms |
| 10 min | 3.1 ms | 3.3 ms | 0.15 ms |

The new version is **0.1–0.4 ms slower per full redraw (2–7%)**. Breaking it down, the strip and map are unchanged and
the cost is in the plot redraw. I traced about 0.06 ms to the panel titles and about 0.1 ms to the plot canvas being 1.7%
taller (896×915 vs 894×900 px, because the cards' padding is gone). Every case stays above 150 frames per second
against a 16.7 ms frame budget. This is a measurable regression, though not a perceptible one; I report it rather than call
it parity. Detail-level switching and the y-range logic are unchanged code, and the y ranges match the baseline exactly
(item 3), so there is no new jump at a resolution switch.

**What I could not check myself.**
- `make gui` itself: port 8765, its fixed default, is occupied by a viewer process that was already running and that I
  did not start, so `make gui` would fail to bind. The same command with `--port` worked; `viewer.py` and the Makefile are
  unchanged.
- Smoothness by eye: pan/zoom animation code is unchanged and benchmarks cover redraw cost, but I could not watch it
  interactively. Please drag and zoom once.
- Real browsers other than Firefox: all checks ran in headless Firefox 159. The page uses only standard CSS and canvas
  (`roundRect` was already used before).

## 5. What intentionally did not change

- Every endpoint, binary layout, JSON schema, CLI flag, default port, hash format, shortcut, the 20 s minimum window,
  animation constants, decimation budget and envelope drawing, y ranges, reference lines, fix-quality order and base-link
  semantics.
- Cause hues (kept so the demo deck and the operator's memory still match), viridis for the spectrogram.
- Default open/closed state of every fold. The hash writer still drops `tab=` and `map=all` after any move (G4).
- No new fields were needed, so none were added.

## 6. Features I could not preserve

None. Every row in FEATURES.md behaves as before; the parity dump shows identical state for every scripted action.

## 7. Optional additions not built

- Export (PNG/CSV of the current view) and threshold controls: the viewer deliberately lacks both, and they would add logic
  beyond a restyle.
- Writing `tab=` and `map=all` into the hash on every move (G4), and scoping keyboard shortcuts to the visible tab (G5):
  behaviour changes, so listed below for your decision.
- Direct labels at the right end of each trace: the tractor toggles and tooltip swatches already identify lines, and
  end labels would crowd the narrow panels.

## 8. Decisions for you

1. **Accent = ink (achromatic).** Active tab, active buttons, selection bar, focus ring and the strip's window use ink.
   A coloured accent collides with a data colour. Keep, or name an accent colour.
2. **T3 is now graphite** (was `#CC79A7`, too close to the space-weather purple). Keep, or choose another separable colour.
3. **Spoofing orange darkened** to `#E06A00` (was `#FF7F00`, 2.5:1 for marks). The demo deck still uses the old orange.
4. **G4:** should the hash keep `tab=` and `map=all` after moving? Today a link copied after any pan loses the tab.
5. **G5:** should `n`/`p`/arrows/`+`/`-`/`0` be ignored while the Operator or Spectral tab is open? Today they move the
   hidden Investigate view.
6. **Merge.** The Spectral tab exists only on this branch (from the snapshot). Merging `redesign/viewer` into `main` brings
   both the Spectral tab and the redesign; the snapshot commit lets you take only the Spectral tab if you prefer.

## 9. How to re-run the checks

```bash
# servers: baseline from a worktree of b43bee3, new from this branch (any free ports)
git worktree add /tmp/viewer-baseline b43bee3
(cd /tmp/viewer-baseline && PYTHONPATH=src ../straight-rows/.venv/bin/python scripts/viewer.py --data "$PWD/../straight-rows/data" --refresh --no-open --port 8781) &
PYTHONPATH=src .venv/bin/python scripts/viewer.py --refresh --no-open --port 8782 &
cd redesign/tools
node capture.mjs http://127.0.0.1:8782 ../after            # screenshots + state readback (add "sub" or DARK=1 ... dark)
node parity.mjs  http://127.0.0.1:8781 ../baseline/parity.json
node parity.mjs  http://127.0.0.1:8782 ../after/parity.json
node compare_parity.mjs ../baseline/parity.json ../after/parity.json ../compare/parity-diff.txt
node kb.mjs      http://127.0.0.1:8782 ../compare/keyboard.txt
node bench.mjs   http://127.0.0.1:8782 3                   # timings appear in the server's log
```

The tools need only Node (built-in WebSocket) and Firefox (WebDriver BiDi); no packages were installed.
