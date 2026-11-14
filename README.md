# Straight Rows — GNSS guidance degradation: detect, attribute, recommend

> “My tractor is drifting. Is it the Sun, an adversary, or my own hardware?”

Individual entry for the Bluestaq × NDSU Straight Rows hackathon. The system finds degraded-guidance episodes in a
simulated three-tractor fleet, attributes each to one of five causes using farm telemetry **and** SDA feeds, recommends
an operator action, and writes evidence a human can check against the raw data.

> **Status:** final. Runs on the released dataset with the built-in default thresholds (`src/straightrows/config.py`).
> Dev-window numbers below are **in-sample** (the rules were developed on those 12 episodes); see "Results".

## Setup and exact run commands

Python 3.11+ (developed and tested on 3.14.9). Direct dependencies are pinned in `requirements.txt` (numpy, pandas,
matplotlib; tornado only for the optional legacy matplotlib GUI); `requirements.lock` is the full `pip freeze` of the
tested environment. The package is run from `src/` without installation, so every `python -m straightrows...` command
needs `PYTHONPATH=src` (the Makefile sets it).

```bash
python -m venv .venv && source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt                   # or requirements.lock for the exact tested set
python scripts/get_data.py                                  # downloads + checksum-verifies the dataset into data/
python scripts/inspect_data.py --data data                  # schema/range sanity dump
PYTHONPATH=src python -m straightrows.cli dev --data data   # score on the 12 labeled dev episodes (about 5 s)
python scripts/compare_baseline.py data                     # official starter baseline vs this solution, same scorer
python -m unittest discover -s tests -v                     # 29 tests, 5 of them on the real data
```

**Regenerate the submitted results (the one command):**

```bash
PYTHONPATH=src python -m straightrows.cli run --data data --output submission.json \
  --github-handle YOUR_GITHUB_USERNAME --repo-url https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPO
```

(or `make reproduce HANDLE=... REPO=...`). It is deterministic (byte-identical reruns): no randomness, no network, no
credentials, no external services. It validates the file and exits non-zero if validation fails. Thresholds are the
dataclass defaults in `config.py`; `--params file.json` overrides them and a missing file is an error.

Optional tools: `PYTHONPATH=src python -m straightrows.calibrate --data data --out outputs/params_calibrated.json --cv`
(day-split cross-validation and a grid search, about 9 minutes; its output is **not** used by the submission, see
Results), `python scripts/stability.py` (rewrites `docs/STABILITY.md`), `python scripts/viewer.py --refresh` (browser
viewer), and a demo figure:
`python scripts/plot_episode.py --data data --start <ISO> --end <ISO> --submission submission.json`.

## Approach

1. **Features** (`features.py`). 1 Hz telemetry -> 10 s bins per tractor. Each machine is compared with *its own* robust
   baseline (median over working time, speed > 1 m/s, after Chauvenet trimming). The **regional** component is the
   healthiest working machine's smoothed C/N0 deviation; the **local residual** is what remains per machine. This is what
   lets a machine fault, a jammer or a spoofer be found *inside* a regional storm.
2. **Channels** (`detect.py`), each tuned to one signature:
   * **R** regional C/N0 collapse -> `SPACE_WEATHER` or `JAMMING` by a vote: S4 elevated over the event, Kp >= 5 in the
     3 h before, Dst <= -50 nT, a storm advisory in force, and "uniform across machines and >= 60 min" count for a storm;
     a matching RFI report, quiet S4 and unequal loss between machines count for jamming. A missing S4 feed is neutral,
     not "quiet". An abrupt extra step inside a storm is a jammer on top of it (separate detection).
   * **L** local C/N0 residual -> `JAMMING` (deep, sustained, neighbors partly affected, RFI) vs `EQUIPMENT_FAULT`.
   * **E/D** correction-link outage (explicit flag, flicker bridged) and intermittent antenna dropouts (satellite count /
     min C/N0 relative to the neighbors) -> `EQUIPMENT_FAULT`.
   * **X** cross-track-error excess over the neighbors **without** signal collapse -> `SPOOFING`. XTE is a separate
     guidance monitor, so reported lat/lon is never treated as truth. Onset is back-extrapolated along the ramp. XTE
     blow-ups inside a jam are a consequence of the jam and are not reported as spoofing.
   * **M** multipath, at 1 Hz: min C/N0 dips 6-16 dB below the machine's own 15-minute rolling median for 8-180 s while
     the satellite count holds, kept only inside the 20 m north-south band where such dips recur (the band is found from
     all 72 h, without labels). One single-tractor report per pass -> `MULTIPATH` -> `CONTINUE_FALSE_ALARM`.
3. **Hysteresis segmentation** (a high threshold to confirm, a lower one to find the onset) and merging per cause, so
   overlapping causes remain separate detections with their concurrency noted in the evidence.
4. **Evidence + confidence** (`report.py`). After merging, every number is re-measured over the final window, so the
   evidence describes exactly the window it states. It cites per-machine values vs baseline, the neighbor comparison,
   S4/Kp/Dst/advisories/RFI with times and delays, what was ruled out, and a concurrency note. Confidence is an additive,
   explainable score (each term is printed in the evidence), bounded to [0.30, 0.90] because it is heuristic.
5. **Seven independent cross-checks** (`analysis.py`). They run on every finished detection, never change its class, and
   are written into the evidence. Each tests the label from a different angle:
   * *Storm model*: fleet loss regressed on S4 over the whole record (about 2.3 dB per 0.1 of S4, r = 1.00 after trimming
     non-ionospheric bins). Per event: how much loss S4 predicts and how much is left over. Storms leave under 1 dB; the
     jams leave 5-17 dB, and the jam inside a storm leaves 13 dB on top of the 9 dB the storm explains.
   * *Ground-source test*: can one transmitter on the ground explain every machine's loss from its distance? For all five
     jams a single location explains 97-99.7 % of the variation, falling 19.7-21.0 dB per tenfold distance (free space is
     20). For the storms nothing fits (0-2 %). This adds +0.10 to jamming confidence and is the main support for the
     18:30Z jam, which has no RFI report. The fitted spots are 0.5-1.8 km from the three matching RFI reports.
   * *Positioning modes*: share of time in RTK fixed / float / DGPS / autonomous / no fix. Spoofed machines stay RTK fixed
     100 % of the time while metres off line; the link fault only ever drops to float; strong jams reach no fix.
   * *Relative geometry* (spoofing): the machine's reported position slides sideways relative to a neighbour by the same
     amount as the cross-track error (r > 0.999 in all five cases), so the drift is in the position solution itself.
   * *Periodicity* (multipath): the dips repeat every 356 s per machine; the 191 dips outside the headland band are spread
     evenly along the field, so there is no second recurring spot.
   * *Live-alarm check*: a backward-looking CUSUM gives the delay a live system would have had: 10-20 s for step events
     in quiet conditions, 110 s for the jam inside a storm, 150-220 s for spoofing ramps, 620 s for the spoof inside a storm.
   * *Probability cross-check*: naive Bayes over about ten yes/no observations with hand-set likelihoods. It agrees with
     all 17 dev + eval labels, but only after it was adjusted to leave out observations already explained by an overlapping
     cause (it first disagreed on two overlap cases), so it is a consistency check, not independent validation.
6. **Why so many multipath detections.** The dip recurs on every headland pass (about every 5.9 min per machine, 486
   passes on the evaluation day). The scored multipath episode is one unknown representative pass, and the published
   scorer exempts other short recurrences, so each measurable pass is reported with its own measured dip (472 of 486;
   the other 14 fall inside a jam or antenna fault where the dip cannot be measured).

### How the labeled data is used
The 12 labeled episodes were used to design the rules, set the default thresholds by hand, and for error analysis. No
per-episode hard-coding, and the detector never reads the labels. No automatic calibration is applied: `calibrate.py`
was run as a check and changed nothing that matters (see Results), and no onset shift is fitted (`onset_shift_s = 0`).
The 12 episodes are too few to fit a model, hence rules with explicit, inspectable thresholds.

**Use of the evaluation window.** It is unlabeled but provided, and it was looked at: the detection list was hand-reviewed
once, the Kp look-back was shortened from 6 h to 3 h after seeing an earlier storm's Kp bleed into a later event, and
`docs/STABILITY.md` uses "evaluation list unchanged" as its stability criterion. Per-machine baselines and the multipath
band are computed over the whole record; running on the evaluation day alone gives the same result.

## Results

Development window, official diagnostic scorer (vendored unchanged as `official_scorer.py`), default thresholds:

| | Diagnostic /40 | Macro-F1 | Onset credit | False positives |
|---|---|---|---|---|
| Official starter baseline | 20.15 | 0.508 | 0.497 | 5 |
| This solution | **40.00** | 1.000 | 1.000 | 0 |

All 12 episodes are matched with the right class and action; onset errors are 0 s for storms, jams and the antenna
fault, +10 s for the link fault and +20 / +30 / +190 s for the three spoofing ramps (never early).

**This is an in-sample score, not a generalization estimate.** The first version of the rules scored 25.3 on these
episodes; the rest came from fixing classes of error seen on them (jammer inside a storm, link flicker, per-pass
multipath). What supports generalization is indirect:

* Day-split check from `calibrate.py --cv`: fit on day 1, test on day 2: default 40.49 vs calibrated 40.49; fit on
  day 2, test on day 1: 35.50 vs 35.50 (its objective adds 0.5 x mean time-IoU; day 1 has no space-weather episode, so
  macro-F1 over five classes is capped at 0.8 there, and all 5 day-1 episodes are correct). Calibration never beat the
  defaults on held-out data, so the defaults are shipped.
* `docs/STABILITY.md`: every threshold perturbed by +/-25 % and the tight ones scanned to their break points.
* Robustness runs: the evaluation list is unchanged with 5 % of rows dropped, 2 % NaN, duplicated or shuffled rows, no
  RFI reports, an empty S4, Kp or Dst feed, a false RFI report during the storm, or the dev days removed.

Confidence: on the 12 dev matches the mean confidence is 0.80 against a 100 % hit rate (Brier 0.060). With 12 all-correct
outcomes calibration cannot be demonstrated; the scores are ranked and auditable rather than calibrated, and are
deliberately capped at 0.90. The two overlapping dev cases get the lowest values (jammer inside a storm 0.50, spoofing
inside a storm 0.60).

Evaluation day: 7 non-multipath detections (1 space weather, 2 jamming, 2 spoofing, 2 equipment) and 472 multipath
pass reports. The 18:30Z jamming detection has no RFI report, so it rests on the telemetry: abrupt on and off, quiet S4 and Kp, and
a single ground-source fit that explains 99.7 % of the per-machine loss (T2's smaller 5.5 dB loss is what its 811 m distance
predicts, which is why it is listed). The lowest-confidence call is the 01:00Z antenna fault (0.70).

## Literature check (what was and was not applied)
Reviewed Radoš, Brkić, Begušić, *Recent Advances on Jamming and Spoofing Detection in GNSS*, Sensors 24(13):4210 (2024). Most of
its methods need receiver internals we do not have (correlator outputs, pseudoranges, carrier phase, AGC, antenna arrays, clock
bias, authentication) and were rejected for that reason. Analogues that *could* be built from our columns were tested on the real
data before deciding: position-derived speed vs reported speed (no difference during spoofing), C/N0 moving variance (no
difference), mean-minus-min C/N0 gap (no difference), storm-vs-jam onset sharpness (all are step changes). Rejected on
evidence. Adopted: (1) **spatial uniformity** of the per-machine C/N0 loss (adapted from its crowdsourced jammer-localisation
refs): the three storms differ by <= 0.1 dB between machines, the labeled jams outside storms by 5.1-5.7 dB, so heterogeneity counts as
evidence for a local emitter (one-sided, because a jam inside a storm was uniform at 0.4 dB); (2) Kp credit looks back 3 h
instead of 6 h, which removed a tie in which an earlier storm's Kp outvoted a quiet S4; (3) the common-mode spoof channel above;
(4) the stability report. Machine learning was not used: 12 labelled episodes (at most 3 per class) cannot support it, and the
paper's reported 92-100 % accuracies come from correlator-level features on public simulated test sets and are not comparable.
The review itself contains internal inconsistencies (e.g. a 0.4 vs 0.6 chip threshold, a figure caption that disagrees with its
text), so its numbers were treated as indicative only.

## Failure modes and limitations
* **Thresholds are data-dependent.** Real noise/effect sizes may differ. `docs/STABILITY.md` perturbs every threshold by
  +/-25 % and scans the sensitive ones to their break points on the real data: nothing moves at +/-25 %; the tightest
  margins are `mp_dip_db` (breaks at 8 dB vs 6 default) and `link_gap_s` (breaks at 120 s vs 300). Simulated effect sizes
  are large, so this does not prove robustness to real receiver noise.
* **Spoofing vs storm-induced XTE.** XTE excess is judged relative to the neighbors, which is blind when 2+ machines are
  pulled together; a separate common-mode channel (`XC`: >= 2 machines with large absolute XTE while their own C/N0 is
  healthy and the fleet is quiet) covers that case at reduced confidence. It is unit-tested on synthetic scenarios and
  fires on nothing in the real data, so it is unvalidated on a real event. A storm inflating only one machine's XTE
  would still be mislabeled.
* **Jamming vs antenna fault on one machine** is the hardest split; it relies on sustained-vs-bursty behaviour, depth,
  neighbors and RFI. A missing RFI report lowers confidence by design (reports can be missing or false).
* **S4 vs Kp.** S4 over the event is the main storm evidence and Kp/Dst/advisory are supporting votes; the S4 maximum within
  +/-90 min is printed for context only. If `s4_high` were raised above the evaluation storm's S4 mean (0.45; default
  threshold 0.30) the storm would lose its S4 vote and rest on Kp, the advisory and its uniformity.
* **Multipath** relies on recurrence; the first occurrence at a never-seen spot will not be reported. A pass inside a jam or
  antenna fault is reported only when the dip is still measurable (14 of 486 evaluation passes are not).
* **A machine fault wholly inside a jam** that already lists that machine is treated as part of the jam unless it has an
  explicit link-down flag: with these columns the two are not separable.
* **Overlapping same-class causes** on different machines may be merged into one detection.
* Retrospective: onset accuracy is not online latency.

## What additional data would help
Per-satellite C/N0 and elevation/azimuth (separates multipath, spoofing and jamming geometrically); raw IQ/AGC
(jamming), an independent IMU/odometry position (true spoofing check), RTCM base-station health, an RFI sensor network
with direction-of-arrival, and real-time (not delayed) space-weather feeds.

## Layout
```
src/straightrows/  io, config, features, sda, detect, analysis, report, stats, evaluate, calibrate, synth, official_scorer, cli
scripts/           get_data, inspect_data, plot_episode, compare_baseline, stability, viewer, synth_robustness, validate_submission
tests/             unit + end-to-end tests on synthetic data; regression tests on the real data (skipped if absent)
docs/              STABILITY.md (threshold stability), ME457_METHODS.md (statistics used), KICKOFF.md (playbook)
```
No credentials, external services or assets are required. All challenge data is synthetic and unclassified.
