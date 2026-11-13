# Straight Rows — prep to-do list (derived from the official guide v1.4)

Legend: [x] done in prep · [ ] to do at/after kickoff (Fri Nov 13, 4:00 pm CT; lock Sat Nov 14, 4:00 pm CT = 22:00Z)

## Prep (before data exists)
- [x] Read guide, README, starter, baseline, validator, scorer, site, opening remarks
- [x] Own project repo (copy of starter conventions; private until submit)
- [x] Tolerant data loaders (schemas of SDA JSON are undocumented) + `inspect_data.py` schema dump
- [x] Synthetic data generator in the guide's schema, with all 5 causes + overlaps, to test end to end
- [x] Feature layer (10 s bins; regional vs per-machine residuals; working-hours mask)
- [x] Detector + attributor (regional / local / link / xte / multipath channels; SDA corroboration)
- [x] Evidence writer (measured values, timestamps, ruled-out alternatives, overlap notes)
- [x] Confidence model (margin- and corroboration-based, documented)
- [x] Local copy of the official diagnostic scorer + dev evaluation + FP/failure analysis
- [x] Parameter calibration with robustness/sensitivity report
- [x] Validator-clean submission writer, unit tests
- [x] README skeleton satisfying rubric (setup, pinned deps, regen command, approach, labels use, failure modes, wanted data)

## Kickoff checklist (see docs/KICKOFF.md)
- [ ] `get_data.py`, verify sha256, run `inspect_data.py`, fix loader if schema differs
- [ ] Run `evaluate.py` on dev labels; look at every miss/FP; plot episodes
- [ ] Calibrate thresholds; check sensitivity; avoid overfitting 12 episodes
- [ ] Hand-review each of the 8 eval detections (class, tractors, onset, evidence)
- [ ] Write final README, make repo public, ONE PR before 22:00Z Saturday (not early)
- [ ] Rehearse 5-min demo + 3-min Q&A
