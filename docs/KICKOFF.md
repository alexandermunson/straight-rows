# Kickoff playbook (Fri Nov 13, 4:00 pm CT → lock Sat Nov 14, 4:00 pm CT = 22:00Z)

## Hour 0–1: get running
1. `python scripts/get_data.py` (needs the release to be published; verifies SHA-256).
2. `python scripts/inspect_data.py --data data` — read every line. If it prints `LOADER FAILED`, fix `io.py` first.
3. `python -m straightrows.cli dev --data data` — first real dev score with default thresholds.
4. `python scripts/compare_baseline.py data` — you should beat the official baseline on dev.

## Assumptions made WITHOUT seeing data — verify each
| # | Assumption | Where | How to check |
|---|---|---|---|
| 1 | SDA JSON shapes: `sgi_indices.json` has `kp`,`dst` lists; `scintillation_series.json` has `series`; advisories `advisories`; RFI `reports` | `io.py` | inspect script output |
| 2 | RFI report has a time field (`ts`/`reported_at`/...) and optionally lat/lon + an uncertainty field | `sda.py rfi_matches` | RFI columns printed by inspector; is the time the report time or the event time? |
| 3 | Label file is `{"episodes":[{id,start,end,class,tractors,...}]}` | `io.py` | inspector prints labels |
| 4 | `base_link_ok` parses as bool; `fix_state` baseline is `RTK_FIXED` | `io.py`, `features.py` | value counts in inspector |
| 5 | Healthy baseline = median over working time | `features.py` | plot a quiet day |
| 6 | Multipath representative window in the truth is ≤ 3 min and one occurrence | `detect._multipath` | look at the 2 dev multipath labels vs the occurrences we emit (tIoU must be ≥ 0.30) |
| 7 | Antenna dropouts show as sats / min-C/N0 drops relative to neighbors | `features.py` | look at a dev EQUIPMENT_FAULT episode |
| 8 | Spoofing = XTE grows with C/N0 intact | `detect.py X` | look at the dev SPOOFING episodes (notebook says TRN-07 is one) |
| 9 | Working window 11:00Z–03:00Z | `features` (uses speed>1) | speed column |
| 10 | Ramp onset (linear) fits SW/spoof onsets | `_ramp_onset` | onset error column in dev output |

## Hours 1–8: iterate on dev
* Per miss/FP: `python scripts/plot_episode.py --start ... --end ... --out x.png`, then decide whether it is a threshold, a
  channel, or an attribution rule problem. Fix the *class of error*, not the single episode.
* `python -m straightrows.calibrate --data data --out params.json --cv` (≈2–5 min). Check the CV line: if calibrated is
  not better than default on the held-out day, keep defaults and say so in the README.
* Confidence: `cli dev` prints a Brier check; adjust the terms in `report.confidence` if it is badly off.

## Hours 8–18: the eval window (hand review — only 8 episodes!)
* `python -m straightrows.cli run ...` then read **every** detection and its evidence. Does the story hold on the plots?
* Look for: storm + concurrent local cause; a machine parked/not working; multipath recurrence at the known spot;
  jamming with no RFI report; RFI false alarm without farm effect (must NOT become a detection).
* Don't hand-edit `submission.json`; change code/params so `make reproduce` regenerates it.

## Hours 18–23: package
* Fill README TODO(kickoff) sections with real results, real failure cases, real CV numbers.
* `python -m unittest discover -s tests`, `python scripts/validate_submission.py submission.json`.
* Repo public → fork Bluestaq/ndsu-hackathon-submissions → add ONLY `submissions/<handle>/submission.json` → one PR
  **shortly before 4:00 pm CT** (do not open early; nothing may change after the deadline). No email / student ID anywhere.
* Note the starter notebook says "hour 22" for the lock but the guide, README and site all say hour 24 / 4:00 pm CT. Trust 4:00 pm CT
  but submit with margin and confirm with the organizers if in doubt.

## Demo (5 min + 3 min Q&A)
1 min problem → 2 min one episode end to end (plot, evidence strings, action) → 1 min an ambiguous case (jam vs fault, or
storm+spoof) with the honest confidence → 1 min reproduce command + calibration/CV + limitations. Be ready to change a threshold live
and re-run `make dev`.
