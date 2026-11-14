# Figure list

All figures are rendered by `presentation/make_figures.py` (run from the repo root with `PYTHONPATH=src`). The script reads
the challenge data in `data/` and `presentation/data/*.json` (written by `presentation/extract_facts.py`). It uses Noto Sans
and the viewer palette (redesign branch): T1 #0072B2, T2 #009E73, T3 #3B424D, background #FAFAF9, cause fills as in
`redesign/DESIGN.md`. The figures have no legends; each slide caption carries the tractor key.

| Slide | File | Size (px) | Shows | Data source |
|---|---|---|---|---|
| 2 Space weather | `figures/fm_space_weather.png` | 960×620 | Mean C/N0 for T1–T3 and S4 (dashed line at 0.30), 05-05 10:00–17:00Z; TRN-01 shaded 11:00–16:00 | `data/farm/tractor_telemetry.csv.gz`, SDA S4 feed, `data/labels/train_episodes.json` |
| 3 Jamming | `figures/fm_jamming.png` | 960×620 | Mean C/N0 for T1–T3, TRN-03 shaded 16:30–17:12Z on 05-04; red line marks RFI-001 at 16:44:48 | telemetry, `data/sda/rfi_reports.json`, labels |
| 4 Spoofing | `figures/fm_spoofing.png` | 960×620 | Cross-track error and mean C/N0 for T1–T3, TRN-07 shaded 00:30–01:18Z on 05-05 | telemetry, labels |
| 5 Equipment fault | `figures/fm_equipment.png` | 960×620 | Correction link up/down for each tractor, plus mean C/N0; TRN-10 shaded 02:00–02:48Z on 05-05 | telemetry, labels |
| 6 Multipath | `figures/fm_multipath.png` | 960×620 | T3 min C/N0 at 1 Hz, 05-04 13:50–14:20Z; TRN-11 (17 s at 14:04:18) marked | telemetry, labels |
| 7 Event | `figures/event_D837.png` | 1664×660 | Operator view, 05-05 19:30–21:00Z: mean C/N0, satellites, \|XTE\|, fix state, S4 (Kp 6.8); D837 shaded 20:00–20:36 | telemetry, SDA feeds; window from detector output (`facts.json` `D837`) |
| 9 Spectral | `figures/spectral_psd.png` | 1120×660 | Welch power spectrum of min C/N0 per tractor, all 72 h; dashed lines at 357 s and its overtones | `presentation/data/psd_mincn0_all.json` (from `scripts/viewer_spectral.compute`) |

Slides 1, 8, 10 and B1–B3 have no figures. Their numbers come from `presentation/facts.md`.
