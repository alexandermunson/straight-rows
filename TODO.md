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
- [x] `get_data.py`, verify sha256, run `inspect_data.py` (loader OK, schemas as assumed)
- [x] Dev scoring: 40.0/40, 0 FP (in-sample; multipath = periodic headland dips; jam-in-storm; link flicker bridge)
- [x] Calibration check: `calibrate.py --cv` never beat the defaults on the held-out day, so defaults are shipped
- [x] Threshold stability report (`docs/STABILITY.md`) and robustness runs
- [x] Hand-review of eval detections (7 real + per-pass multipath); least certain call is the 18:30Z jam (no RFI report)
- [x] Evidence re-measured over the final window; real-data regression tests
- [x] README final
- [ ] Regenerate `submission.json` with the real handle/URL, commit, make repo public, ONE PR before 22:00Z Saturday (not early)
- [ ] Rehearse 5-min demo + 3-min Q&A
