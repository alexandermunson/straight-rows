# Viewer design brief

Direction: a quiet scientific instrument. The traces are the content; chrome recedes to hairlines and tone.

## 1. Colour

One neutral base, no decorative colour. Colour carries exactly three meanings: **tractor** (lines and dots), **cause**
(shading, marks, swatches) and **interference** (RFI, which shares the jamming hue on purpose). Every token lives in
`:root` and has a dark-theme value; the canvases read the same tokens, so plots and HTML always agree.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#FAFAF9` | `#111316` | the only surface; plots draw on it too (no white cards on grey) |
| `--ink` | `#16191D` | `#E8EAED` | text, Kp line, active controls, selection outline |
| `--ink-2` | `#4A525C` | `#B4BBC4` | secondary text |
| `--ink-3` | `#5F6873` | `#8C949E` | axis labels, meta (≥ 4.5:1 on `--bg`) |
| `--line` | `#E2E4E7` | `#262A30` | hairline separators |
| `--line-2` | `#C9CDD2` | `#3A4049` | control borders, axis baselines |
| `--grid` | `#EEEFF1` | `#1D2025` | gridlines (deliberately faint) |
| `--hover` / `--sel` | `#F1F2F3` / `#E9EBEE` | `#1A1D21` / `#22262C` | row hover and selected row |

**Accent = ink.** Active tab, active window button, selected row bar, focus ring and the strip's window rectangle use the
ink colour (inverted for active buttons). *Flag for review:* this is an achromatic accent. A coloured accent would
collide with a data colour (blue = T1, red = jamming, orange = spoofing, purple = space weather), and the brief asks for
no decorative colour; ink keeps state legible without competing with the traces.

**Tractors** (Okabe–Ito based, checked for deuteranopia/protanopia/tritanopia by hue *and* lightness):
T1 `#0072B2` blue (dark `#56B4E9`), T2 `#009E73` bluish green (dark `#2BB98F`), T3 `#3B424D` graphite (dark `#C9CED6`).
*Change, flag for review:* T3 was `#CC79A7` reddish purple, too close to the space-weather purple; graphite is separable
from both other tractors by lightness alone and is never used for a cause.

**Causes** (fills and marks only, never as text colour; ≥ 3:1 against `--bg` as graphical objects, WCAG 1.4.11):
space weather `#6A3D9A` (dark `#A884D8`), jamming `#D62728` (`#FF6B6B`), spoofing `#E06A00` (`#FF9F43`),
equipment fault `#8C6D31` (`#C9A35A`), multipath `#7F7F7F` (`#9AA0A6`). Hues kept from the baseline so the demo deck
and the operator's memory still match; only spoofing is darkened (it was 2.2:1).

**Text never sits on a cause colour.** Overlay labels, the info header and chips are ink on the neutral base, with a small
cause-coloured swatch beside them. This fixes the failing white-on-orange pills.

## 2. Type

- UI: the system sans stack (`system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`). No web font: the viewer
  runs offline and the brief forbids new dependencies.
- Numbers, timestamps, ids and chips: `ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace`, with
  `font-variant-numeric: tabular-nums` so updating values do not jitter.
- Three sizes: **11 px** (axis labels, meta, chips), **13 px** (body), **15 px** (section titles). Hierarchy by
  weight (400/600) and tone (`--ink` / `--ink-2` / `--ink-3`), not by more sizes. No uppercase tracking labels.

## 3. Space, borders, radius

- 4 px grid: 4, 8, 12, 16, 24. Column padding 16, section gaps 16, row padding 8.
- Columns are separated by 1 px hairlines on one continuous surface (no boxed cards, no gaps between cards, no shadows).
- Borders are 1 px. Radius: 4 px on controls, 3 px on chips, 0 on panels, plots and map.
- Folds use one control everywhere: a small chevron that turns 90° (120 ms), same in the sidebar, info panel,
  operator cards and spectral help. Default open/closed states are unchanged.

## 4. Charts

- No panel borders; one baseline hairline per panel. Gridlines at `--grid` (barely visible), tick labels 10–11 px
  tabular in `--ink-3`.
- Panel titles horizontal, top-left inside the plot area, with units ("mean C/N0 · dB-Hz"), on a translucent backing so a
  trace never hides them. Same position on every panel.
- Data colours, line widths, envelope drawing, decimation budget, y ranges and every reference line (Kp 5, S4 0.3,
  XTE 1 m, baselines) are unchanged. Truth stays dashed lines, ours stays shaded bands, so the two differ without colour.
- Overlay labels: ink text after a 6 px cause swatch (was cause-coloured bold text).
- Spectrogram keeps viridis (perceptually uniform, colour-blind safe) but with a 9-stop table for smoother ramps, plus a
  mid tick on the colour bar. Dark noise, blank-for-missing and bright-line semantics unchanged.

## 5. Components

- **Buttons**: 24 px tall, 1 px `--line-2`, transparent; hover `--hover`; active = ink fill, `--bg` text. Tabs, window
  spans, day buttons and map controls are segmented groups (shared borders).
- **Confidence chip**: monospace 11 px, 1 px `--line-2`, 3 px radius, ink-2 text, no fill. Same rule in the event list,
  info header, operator cards. Only confidence gets a chip; the "✓ TRN-xx" match stays plain mono text.
- **Event row**: 8 px cause dot, cause name (600), machines (ink-3), mono meta line; selected = `--sel` with a 2 px ink
  bar on the left; focusable, Enter/Space selects.
- **Focus**: `:focus-visible` 2 px ink outline, 2 px offset, on every control, row, lane block and summary.
- **Motion**: only the existing animated pan/zoom and the chevron turn. Nothing else animates.

## 6. Empty and loading states

- Status: monospace 11 px text on the base with a hairline border; "loading 1 s data…" keeps the same words.
- Info panel before a selection: a short, plain prompt in `--ink-3`.
- Cause filter with no matches: "No events of this cause."
- `--submission` mode: Operator tab shows an explicit note that cards come from the detector and are absent in this
  mode (instead of "No action needed"); the info panel says reasoning is unavailable and the evidence lines follow.
- A window with no events: the status line adds "· no events in view".

## 7. Explicitly not done

No new fields, endpoints, dependencies, web fonts, icons, gradients, shadows, or export/threshold controls.
