# ME 457 methods applicable to Straight Rows

Source: ME 457 Ch. 1-4 slide decks (Refling, NDSU), read from extracted text. Equations, figures and worked-example
answers are partly image-only, so formulas below are the standard forms, not copied from the slides.
Nothing here is validated yet: the real data arrives at kickoff.

## Implementation status (updated 2026-11-13)

Done and unit-tested on the synthetic data only (dev 37.04 / eval 40.0 with each new switch on or off):

| # | Status | What exists | Switch |
|---|---|---|---|
| 1 | done (evidence only) | `stats.pearson_r`, `r_crit`, `correlate`; cross-machine and C/N0-vs-XTE r on 60 s block means, reported against `r_crit` | `corr_block_s`, `corr_alpha` |
| 2 | done | `stats.chauvenet_z`, `chauvenet_keep` (iterated); each machine's cn0/mincn0/sats/xte baseline is trimmed before the median | `chauvenet_baseline` |
| 4 | implemented and unit-tested, NOT active in the final pipeline | `Detector._sustained`: line fit to the C/N0 residual either side of a brief dip, standardized residual e/S_yx, needs >= 2 consecutive bins beyond 2. It filtered brief dips in the 10 s mean-C/N0 channel; real multipath turned out to be visible only in 1 Hz min C/N0 (`_mp_events`), so that brief-dip path was removed and this rule changes no output | `glitch_filter`, `glitch_z`, `glitch_ctx_s`, `glitch_min_run`, `glitch_syx_floor` |
| 5 | done (partly) | `stats.linfit`/`std_residuals`; XTE-excess drift regression per spoofing window (slope, r2, S_yx, slope CI; +0.05 confidence when the CI excludes zero); each machine regressed on the fleet median; lagged farm-C/N0-on-S4 regression over +/-3 h, printed for SPACE_WEATHER detections only, with r_crit Bonferroni-corrected for the 13 lags tried | `lag_step_min` |
| 6 | done | `stats.mean_ci` (t interval) on block means, printed in the evidence | `corr_alpha` |

Not done: 3 (already covered by median/MAD), 7, 8, 9, 10.

Notes on what was implemented:
* Only the XTE drift term feeds confidence. Everything else in 1, 5 and 6 is evidence text, not attribution logic.
* Kp is not used in the lag regression: its 3 h resolution is too coarse. Only S4 (15 min) is.
* Iterated Chauvenet trims a few extra tail points on clean normal data (under 1% at n=2000), negligible at the ~77k-bin baseline size.
* The glitch rule (item 4) is not wired into the final pipeline: it changes no detection. `Detector._sustained` and its unit test remain as a worked example.
* Pure numpy/stdlib (Student t by incomplete beta, no scipy) so the pinned requirements are unchanged.

## Priority list (best value per hour first)

| # | Method (chapter) | Use in this problem | Where |
|---|---|---|---|
| 1 | Pearson r + significance threshold (Ch3) | Correlate per-tractor C/N0 deviation across machine pairs. High r among all three = regional (space weather / wide jamming); one machine uncorrelated = equipment fault. Also r(C/N0 dev, XTE): spoofing should show XTE drift with ~no C/N0 relation. Use the "min r for significance" table logic (r_crit depends on n and alpha), not a bare 0.5 cutoff. | `detect._common`, `_attr_*` |
| 2 | Chauvenet criterion (Ch3) | Sets the z threshold from sample count: flag when P(|z|>z0) < 1/(2N). For ~260k working samples per tractor that is z ~ 4.7, so thresholds are principled and scale with window length. Use it on the *healthy* baseline (iterate: reject, recompute) so events do not contaminate the baseline. | `features.build` baseline |
| 3 | 2-sigma / 3-sigma test, N>30 (Ch3) | Already approximated by the median/MAD thresholds (`xte_k_mad`); justify the choice as a robust 3-sigma (MAD*1.4826 ~ sigma). | `config.Params` |
| 4 | Curve-fit outlier rule (Ch3) | Fit a line, standardized residual e/S_yx > 2 **and consistent with neighbours** means a real change, not a glitch. Inconsistent with neighbours means a one-off glitch. That is exactly the split between a sustained event and a brief multipath/dropout spike. | `detect._multipath`, `segments` |
| 5 | Linear regression, r^2, S_yx (Ch3) | Spoofing drift: regress XTE vs time inside a candidate window; slope, r^2 and slope CI separate a steady drift from jitter. Regress each tractor on the fleet median (reference) and use residuals as the per-machine anomaly. Time-lagged regression of farm C/N0 drop on S4 (and Kp) gives lag and strength. | `features`, `sda` |
| 6 | Confidence interval of a mean, t for n<30 (Ch2) | Evidence strings: "mean C/N0 drop 7.2 dB (95% CI 6.4-8.0, n=18 bins)". Gives checkable numbers for the rubric's evidence criterion. Use t, not z, because events have few 10 s bins. | `report.evidence` |
| 7 | Bias vs precision limit, RSS, u = sqrt(B^2+P^2) (Ch4) | Confidence model: treat time-resolution of each feed as bias (Kp 3 h, Dst 1 h, S4 15 min, RFI report delay 10-30 min, RFI location uncertainty) and noise as precision. Combine by RSS to size onset and corroboration windows; shrink confidence as combined uncertainty grows. | `report.confidence`, `sda` |
| 8 | Sequential perturbation (Ch4) | Numerical sensitivity: move each `Params` value +/- its uncertainty, recompute detections, report which parameters change the result. Supports the rubric's robustness criterion. `calibrate.py` already does a form of this. | `calibrate`, `synth_robustness` |
| 9 | Chi-squared interval for variance (Ch2) | Test whether variance in a window exceeds baseline variance (noisier C/N0 under jamming or multipath, vs. a pure level shift under space weather). | `detect` |
| 10 | Probability rules (Ch2) | P(A and B) = P(A)P(B) for independent evidence, P(A or B) = P(A)+P(B)-P(AB). Use for combining independent corroboration (S4 elevated, RFI report) and for the RFI false-alarm discount, e.g. P(RFI report is false alarm and event is real). | `report.confidence` |

## By chapter

**Ch1, measurement systems.** Random error (precision, scatter) vs systematic error (bias, offset). Interference
(deterministic trend) vs noise (random): spoofing is an interference-type signature (drift in XTE), jamming and
multipath are noise-type (level drop, variance increase). Calibration terms map to per-receiver gain and zero
offset: normalise each tractor to its own baseline (already done via median over working time). Resolution error
bounds what can be claimed from quantised C/N0, sats and XTE.

**Ch2, statistics.** Histogram bin rule K = 1.87 (N-1)^0.4 + 1 for choosing baseline bins; median/mode for skewed data
(C/N0 is probably left-skewed, so keep medians); z-scores; central limit theorem and standard error sigma/sqrt(n);
t-distribution for small n; chi-squared for variance; one-sided vs two-sided intervals (a C/N0 *drop* is one-sided).

**Ch3, outliers and correlation.** Three/two-sigma (N>30), Thompson tau and Chauvenet (small N), Pearson r with
significance table, least squares, r^2, S_yx, standardized residuals, multiple and polynomial regression (e.g. C/N0
~ fleet median + speed + hour of day for a better expected-value model), transformation to linear form.

**Ch4, uncertainty.** Design-stage uncertainty, zero-order (resolution) uncertainty, bias/precision limits, RSS,
propagation by partial derivatives or sequential perturbation, Welch-Satterthwaite degrees of freedom.

## Cautions (where course tools do not transfer directly)

1. **Autocorrelation.** 1 Hz samples are strongly correlated, so n = 777,600 is not independent data. A CI built on
   raw n would be absurdly tight. Use 10 s (or event-level) block means, or bootstrap blocks, so n is honest.
2. **Non-normality.** C/N0 and XTE are likely heavy-tailed; the normal-theory tests assume otherwise. Keep
   median/MAD baselines and treat z cutoffs as rankings, not exact probabilities. Check with a histogram/QQ plot first.
3. **Events are not outliers to delete.** The outlier chapter removes bad points; here the "outliers" are the
   product. Apply rejection to the baseline estimate, not to the detections.
4. **Multiple comparisons.** Scanning thousands of windows inflates false alarms at any fixed alpha; Chauvenet's
   N-scaling is the cheap correction.
5. **Only 12 labelled episodes.** Do not fit more than a handful of thresholds; prefer methods whose cutoff comes from
   theory (Chauvenet, r_crit, t-quantile) over fitted constants.

## Suggested kickoff order

Items 1, 2 and 6 first (they fix attribution and evidence quality), then 4/5 for spoofing and multipath, then 7/8 for
confidence and the robustness write-up. Check each against `cli dev` and the official scorer before keeping it.
