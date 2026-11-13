# Straight Rows — GNSS guidance degradation: detect, attribute, recommend

> “My tractor is drifting. Is it the Sun, an adversary, or my own hardware?”

Individual entry for the Bluestaq × NDSU Straight Rows hackathon. The system finds degraded-guidance episodes in a
simulated three-tractor fleet, attributes each to one of five causes using farm telemetry **and** SDA feeds, recommends
an operator action, and writes evidence a human can check against the raw data.

> **Status:** TODO(kickoff) fill in real dev-window results below. Until the dataset is released the code has only been
> exercised on a synthetic stand-in (`src/straightrows/synth.py`), so all numbers marked *synthetic* validate the
> pipeline, not accuracy on the real data.

## Setup and exact run commands

Python 3.11+ (developed on 3.14). Dependencies are pinned in `requirements.txt` (numpy, pandas, matplotlib only).

```bash
python -m venv .venv && source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/get_data.py                                  # downloads + checksum-verifies the dataset into data/
python scripts/inspect_data.py --data data                  # schema/range sanity dump
python -m straightrows.calibrate --data data --out params.json --cv   # optional; params.json is committed
python -m straightrows.cli dev --data data --params params.json       # score on the 12 labeled dev episodes
python scripts/compare_baseline.py data params.json                   # official starter baseline vs this solution
```

**Regenerate the submitted results (the one command):**

```bash
PYTHONPATH=src python -m straightrows.cli run --data data --params params.json --output submission.json \
  --github-handle YOUR_GITHUB_USERNAME --repo-url https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPO
```

(or `make reproduce HANDLE=... REPO=...`). It is deterministic: no randomness, no network, no credentials, no external
services. Tests: `python -m unittest discover -s tests -v`. Demo figure:
`python scripts/plot_episode.py --data data --start <ISO> --end <ISO> --submission submission.json`.

## Approach

1. **Features** (`features.py`). 1 Hz telemetry → 10 s bins per tractor (not 1 min, so 1–3 minute multipath dips
   survive). Each machine is compared with *its own* robust baseline (median over working time). The **regional**
   component is the healthiest working machine's smoothed C/N0 deviation; the **local residual** is what remains per
   machine. This is what lets a machine fault, a jammer or a spoofer be found *inside* a regional storm.
2. **Channels** (`detect.py`), each tuned to one signature:
   * **R** regional C/N0 collapse → `SPACE_WEATHER` if S4 is elevated (+Kp/Dst/advisory support), else `JAMMING`
     (all machines, a wide local source). RFI reports corroborate jamming.
   * **L** local C/N0 residual → `JAMMING` (deep, sustained, neighbors partly affected, RFI) vs `EQUIPMENT_FAULT`.
   * **E/D** correction-link outage and intermittent antenna dropouts (satellite count / min-C/N0 relative to
     neighbors) → `EQUIPMENT_FAULT`.
   * **X** cross-track-error excess over the neighbors **without** signal collapse → `SPOOFING`. The simulated XTE is a
     separate guidance monitor, so reported lat/lon is never treated as truth. Onset is back-extrapolated along the ramp.
   * **M** brief dips that recur at the same place (clustering within 40 m, across all 72 h so dev occurrences locate the
     obstruction) → `MULTIPATH` → `CONTINUE_FALSE_ALARM`.
3. **Hysteresis segmentation** (a high threshold to confirm, a lower one to find the true onset) and merging per cause,
   so overlapping causes remain separate detections with their concurrency noted in the evidence.
4. **Evidence + confidence** (`report.py`). Evidence cites window, per-machine measured values vs baseline, neighbor
   comparison, S4/Kp/Dst/advisories/RFI with times, what was ruled out, and a concurrency note. Confidence is an additive,
   explainable score (each term is printed in the evidence); it is bounded to [0.30, 0.90] because it is heuristic.
5. **Calibration** (`calibrate.py`). Coordinate search over thresholds on the labeled dev window, choosing the *center of
   the near-optimal plateau* rather than the arg-max, a ±25 % sensitivity table, and a day-split cross-validation. The
   same published diagnostic scorer used by the organizers is vendored (`official_scorer.py`).

### How the labeled data is used
Only for threshold calibration, the onset-bias fit, the multipath-location prior, and error analysis. No per-episode
hard-coding. The 12 episodes are too few to fit a model, hence rules with explicit, inspectable thresholds.

## Results
TODO(kickoff): paste `cli dev` + `compare_baseline.py` output and the CV line here.

*Synthetic stress test (pipeline only, not real accuracy):* official starter baseline 17.7/40 vs this solution 37.0/40 on
the “hard” synthetic dev window after calibration; on easy synthetic seeds both dev and eval ≈ 37–40/40.

## Failure modes and limitations
* **Thresholds are data-dependent.** Real noise/effect sizes may differ; sensitivity tables show `cn0_drop_db` and
  `sats_drop` are the most fragile parameters.
* **Spoofing vs storm-induced XTE.** XTE excess is judged relative to the neighbors; a spoofer hitting *all* machines
  together, or a storm inflating only one machine's XTE, will be mislabeled or merged.
* **Jamming vs antenna fault on one machine** is the hardest split; it relies on sustained-vs-bursty behaviour, depth,
  neighbors and RFI. A missing RFI report lowers confidence by design (reports can be missing or false).
* **S4 lags Kp**, so Kp is used as supporting (not deciding) evidence; the S4 window is widened by ±90 min.
* **Multipath** relies on recurrence; the first occurrence at a never-seen spot will not be reported.
* **Overlapping same-class causes** on different machines may be merged into one detection.
* Retrospective: onset accuracy is not online latency.

## What additional data would help
Per-satellite C/N0 and elevation/azimuth (separates multipath, spoofing and jamming geometrically); raw IQ/AGC
(jamming), an independent IMU/odometry position (true spoofing check), RTCM base-station health, an RFI sensor network
with direction-of-arrival, and real-time (not delayed) space-weather feeds.

## Layout
```
src/straightrows/  io, config, features, sda, detect, report, evaluate, calibrate, synth, official_scorer, cli
scripts/           get_data, inspect_data, plot_episode, compare_baseline, synth_robustness, validate_submission
tests/             unit + end-to-end tests on synthetic data
docs/KICKOFF.md    kickoff-hour playbook and list of assumptions to verify
```
No credentials, external services or assets are required. All challenge data is synthetic and unclassified.
