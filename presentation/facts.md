# Facts used in the presentation

Every number below is taken from the repository or from its outputs. Outputs were regenerated for this deck by
`presentation/extract_facts.py`, which calls the same functions as `cli dev`, `cli run` and the viewer, and writes
`presentation/data/facts.json`. Branch at extraction: `redesign/viewer` (detector code identical to `main`).

Sources are abbreviated: **cfg** = `src/straightrows/config.py`, **det** = `src/straightrows/detect.py`,
**ana** = `src/straightrows/analysis.py`, **rep** = `src/straightrows/report.py`, **spec** = `scripts/viewer_spectral.py`,
**FJ** = `presentation/data/facts.json` (key named), **lab** = `data/labels/train_episodes.json`.

## Problem framing

| Fact | Value | Source |
|---|---|---|
| Fleet, span, rate | 3 tractors (T1–T3), 72 h, 1 Hz | challenge guide; `data/farm/tractor_telemetry.csv.gz` (777,600 rows) |
| Windows | dev 05-04 00:00Z–05-06 00:00Z (12 labelled episodes); eval 05-06 00:00Z–05-07 00:00Z (unlabelled) | cfg l.11–12; lab |
| Cause → action | SW → HALT_OR_DEGRADED_MODE; JAM → HALT_AND_REPORT; SPOOF → HALT_CROSSCHECK_AND_REPORT; EQUIP → SERVICE_MACHINE_OTHERS_CONTINUE; MP → CONTINUE_FALSE_ALARM | cfg l.15–21 |
| Output per detection | start, end, tractors, class, action, confidence, evidence strings | `rep.to_submission` |
| Working hours | "working" = speed > 1 m/s; parked about 03Z–11Z | cfg l.28; viewer Operator tab note |

## Failure-mode signatures (what the code does)

| Cause | Signature in the code | Source |
|---|---|---|
| Space weather | Regional channel: the healthiest working machine's smoothed C/N0 deviation below −3 dB for ≥ 180 s. Vote for storm: S4 elevated +2.0, Kp ≥ 5 +1.0, Dst ≤ −50 nT +0.5, storm advisory in force +0.5, uniform loss and ≥ 60 min +0.5. Vote for jamming: matched RFI +1.5 (half for low-confidence reports), S4 quiet +1.0, loss differing ≥ 1 dB between machines +1.0 | cfg l.40–41, 46, 71–74, 79; det l.355–368 |
| Jamming | Same regional vote (jamming side); or a local residual on one machine; or **jammer on top of a storm**: the regional level drops ≥ 6 dB below the storm level abruptly and recovers (forced JAMMING) | cfg l.39, 48; det `_attr_regional`, `_attr_local`, `_jam_in_span` (l.422) |
| Spoofing | Cross-track error above the neighbours by > max(1 m, median + 6 × MAD) for ≥ 180 s **while the machine's own C/N0 residual stays above −2.5 dB**; onset back-extrapolated along the ramp; XTE blow-ups inside a jam are dropped (consequence, not spoofing) | cfg l.50–53; det l.209–213, `_drop_spoof_inside_jam` (l.477) |
| Equipment fault | One machine only: correction link up < 70% of a 10 s bin, outages bridged up to 300 s (flicker); or dropouts: satellites ≥ 4 below the neighbours or min C/N0 ≥ 12 dB below them, in ≥ 30% of bins | cfg l.43, 55–59; det l.164–165, 199 |
| Multipath | 1 Hz min C/N0 6–16 dB below the machine's own 15-min rolling median, 8–180 s long, satellites held; kept only in the 20 m north–south band where dips recur (≥ 5 occurrences); one single-tractor report per pass | cfg l.61–68; det l.518, 540 |

## Development-set results (in-sample, official scorer)

| Fact | Value | Source |
|---|---|---|
| Diagnostic score | 40.00 / 40 | FJ `dev.score` |
| Macro-F1 / mean onset credit | 1.000 / 1.000 | FJ `dev.macro_f1`, `dev.timing` |
| False positives | 0 | FJ `dev.fps` |
| Action agreement | 12 / 12 | FJ `dev.action_agree` |
| Per class TP/FP/FN | SW 2/0/0, JAM 3/0/0, SPOOF 3/0/0, EQUIP 2/0/0, MP 2/0/0 | FJ `dev.classes` |
| Onset errors | 0 s for TRN-01–05, 09, 11, 12; +10 s TRN-10; +20 s TRN-07; +30 s TRN-06; +190 s TRN-08 | FJ `dev.episodes` |
| Tractor IoU below 1 | TRN-04: 0.67 (T2 listed, truth T1, T3) | FJ `dev.episodes` |
| Calibration (labelled days) | below 0.50: 0; 0.50–0.69: 3 of 3 correct (D124, D837, D878); 0.70–0.84: 2 of 2 (D176, D804); ≥ 0.85: 7 of 7 | FJ `calibration` (same rule as `viewer.build_meta`) |
| Confidence cap | 0.90 (floor 0.30) | cfg l.84–85 |

## Dev episodes used as figures

| Slide | Episode | Window (UTC) | Tractors | Our detection | Source |
|---|---|---|---|---|---|
| Space weather | TRN-01 | 05-05 11:00–16:00 | T1–T3 | D560, 0.90 | lab; FJ `real_detections` |
| Jamming | TRN-03 | 05-04 16:30–17:12 | T1–T3 | D250, 0.90 (RFI-001 at 16:44:48) | lab; `data/sda/rfi_reports.json` |
| Spoofing | TRN-07 | 05-05 00:30–01:18 | T1 | D483, 0.90 | lab |
| Equipment fault | TRN-10 | 05-05 02:00–02:48 | T1 (correction link) | D529, 0.90 | lab |
| Multipath | TRN-11 | 05-04 14:04:18–14:04:35 (17 s) | T3 | D176, 0.80 | lab |

## The end-to-end event: D837 (dev, labelled TRN-05)

| Fact | Value | Source |
|---|---|---|
| Window, tractors, class, action | 05-05 20:00:00–20:36:00Z (36 min), T1 T2 T3, JAMMING, HALT_AND_REPORT | FJ `D837` |
| Confidence | 0.50 | FJ `D837.confidence` |
| Confidence terms | base 0.50; no RFI −0.05; S4 elevated −0.15; deep loss +0.10; ground source (R² 0.97) +0.10; ≥ 10 min +0.05; overlaps another cause −0.05 | FJ `D837_conf_terms`; rep l.17–80 |
| Label | TRN-05 JAMMING, onset error 0 s, tractor IoU 1.00 | FJ `dev.episodes` |
| Concurrent detection | D803 SPACE_WEATHER T1–T3 19:00–22:00 (0.90), labelled TRN-02 | FJ `concurrent_D837`, `real_detections` |
| Loss per tractor | T1 −22.7 dB, T2 −22.3 dB, T3 −22.4 dB (spread 0.4 dB) | FJ `D837.evidence[1–3, 9]` |
| Regional level | storm 19:00–20:00 −9.7 dB → jam 20:00–20:36 −21.8 dB → after 20:36–22:00 −9.8 dB (step ≈ 12 dB) | `F['regional']` medians (extract run, printed) |
| Rule that decided | "jammer on top of a storm: abrupt extra regional C/N0 step" (forced JAMMING) | FJ `D837_internal.notes`; det `_jam_in_span` |
| Vote without the step rule | storm 4.0 (S4 +2, Kp +1, Dst +0.5, advisory +0.5) vs jamming 0 (no RFI, S4 elevated, spread 0.4 dB < 1) | det l.355–366 with FJ `D837_internal` |
| SDA | S4 mean 0.44 (quiet 0.06), Kp 6.8, Dst min −260 nT, advisory ADV-2026-118 in force | FJ `D837.evidence[8, 10]` |
| RFI | none between 5 min after onset and 35 min after the end | FJ `D837.evidence[11]` |
| Storm model | S4 predicts 9.0 dB, observed 22.4 dB, +13.4 dB unexplained | FJ `D837.evidence[14]` |
| Ground-source fit | 97.4% of variation, 19.8 dB per tenfold distance, ±301 m, 46.8956 −97.1985 | FJ `D837.evidence[15]` |
| Probability cross-check | JAMMING 91.8%, SW 5.8%, EQUIP 2.3%, SPOOF 0.2% (hand-set likelihoods) | FJ `D837.evidence[18]` |
| Fix / satellites / XTE | fix lost 100% on all three; satellites down to 3 of 12; |XTE| max 26.23 / 26.82 / 22.06 m; correction link up 100% | FJ `D837.evidence[1–3]` |
| Live alarm (CUSUM) | 110 s after onset | FJ `D837.evidence[17]` |
| Cost | 3 machines × 36 min = 1.8 machine-hours, report with source position | FJ `D837.ops.cost` |
| If wrong | "If this were really space weather, the only cost of the wrong label is an unnecessary report; the stop is right either way." | FJ `D837.ops.wrong` |
| Viewer link | `#t0=156600&t1=162000&ev=21` (D837 is index 21 in the viewer's event list) | `gui_matplotlib.Data.events` order |

**Why this event.** It is labelled (TRN-05), so the decision can be checked against truth, and it is the only labelled
episode where two causes are present at once (jammer inside a storm). The storm-versus-jammer question is real here:
the ordinary vote alone would say storm (4.0 vs 0); the abrupt 12 dB step is what decides jamming. The confidence (0.50,
the lowest of all detections) shows the system's uncertainty honestly. The eval-day 18:30Z jam (no RFI) was the other
candidate, but it is unlabelled, so its correctness cannot be shown.

## Spectral tab

| Fact | Value | Source |
|---|---|---|
| Role | display only: "the detector and submission.json never read it" | spec l.3 |
| PSD windows | 4096 s (68 min), 50% overlap, only windows ≥ 95% moving | spec l.16, `compute` |
| Spectrogram windows | 2048 s (34 min) every 512 s (8.5 min) | spec l.17 |
| Range | 0–50 mHz (periods down to 20 s) | spec l.15 |
| Period finder | harmonic sum over up to 6 overtones, 1–20 mHz band; best period > 800 s rejected as drift | spec `_peak`, l.19–20 |
| Result, min C/N0, all 72 h | T1, T2, T3 all 357 s; strength 18.4, 17.0, 20.2 dB; 77 windows each | FJ `spectral` |
| Detector's own periodicity (separate code) | autocorrelation of the dip indicator: 356 s on every tractor; 191 dips outside the band, ≤ 31 per 100 m: no second spot; evidence text only | ana `periodicity` l.256; FJ `multipath.period` |
| Multipath reports | 1,415 over 72 h, 472 on the evaluation day | FJ `multipath` |

## Limits and reproducibility

| Fact | Value | Source |
|---|---|---|
| Least certain eval call | D1264 JAMMING 05-06 18:30–19:00, T1–T3, 0.90, no RFI report | FJ `real_detections` |
| More data that would help | per-satellite C/N0 and elevation/azimuth; independent position (IMU/odometry); RF sensors with direction finding | README l.179 "What additional data would help" |
| Regenerate | `make reproduce HANDLE=<github-username> REPO=https://github.com/<github-username>/<repo>` | Makefile `reproduce` |
