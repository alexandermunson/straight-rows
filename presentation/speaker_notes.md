# Speaker notes: Straight Rows (5 minutes)

Times are cumulative targets (about 150 words per minute). LIVE marks a switch to the viewer; each has a fallback: stay on the slide, which shows the same data.

Before you start: check out branch `redesign/viewer` (its colours match the deck; `main` draws T3 in pink), run `python scripts/viewer.py --no-open` (port 8765) and open http://127.0.0.1:8765/ in a second tab.

| Slide | Viewer URL |
|---|---|
| 7 | http://127.0.0.1:8765/#t0=156600&t1=162000&ev=21 (D837, 05-05 19:30–21:00Z) |
| 9 | http://127.0.0.1:8765/#tab=spec&ch=mincn0&scope=0&tr=0 (Spectral tab, min C/N0, all 72 h) |

## 1. Same symptom, five causes, five actions

0:00–0:25. Three tractors near Casselton steer by RTK GPS. When guidance degrades, the operator sees much the same thing: the signal drops or the machine drifts. There are five reasons, and each needs a different response. The system reads the telemetry and the space-weather and interference feeds, names the cause, recommends the action and shows its evidence.

## 2. Space weather

0:25–0:45. Space weather. A geomagnetic storm makes the ionosphere scintillate, so every receiver loses signal together: C/N0 falls in step, S4 above 0.30, Kp 5 or more. Dev episode TRN-01. Halt or run degraded. The costly mistake is calling a jammer a storm: no report gets filed.

## 3. Jamming

0:45–1:05. Jamming. A ground transmitter drowns the signals. C/N0 drops on every machine by different amounts, since each is a different distance away; S4 stays quiet; a report may arrive 10 to 30 minutes later, here RFI-001 in TRN-03. Halt and report. If it was really a storm, we filed one needless report.

## 4. Spoofing

1:05–1:25. Spoofing. Fake signals pull one receiver off its line while the signal looks healthy. So we look for cross-track error above the neighbours for three minutes or more with normal C/N0. TRN-07: T1 ramps off, the others stay flat. Halt, cross-check, report. Miss it and the machine works metres off the row.

## 5. Equipment fault

1:25–1:45. Equipment fault. One machine’s antenna, receiver or correction radio fails, and only that machine shows it. In TRN-10, T1’s correction link flickers for 48 minutes while everything else stays normal. Service that machine and let the others work; halting everyone idles healthy machines.

## 6. Multipath

1:45–2:05. Multipath. Signals bounce off a structure at one spot: short dips, 8 to 180 seconds, in the same 20-metre band every pass. TRN-11 is one 17-second dip on T3; the same dip repeats every few minutes. False alarm: continue. There are 1,415 such reports in 72 hours; halting for each would end the day.

## 7. One event: 5 May, 20:00Z (dev day)

2:05–2:50. LIVE: http://127.0.0.1:8765/#t0=156600&t1=162000&ev=21 (fallback: stay on this slide, same panels).
One labelled dev event: 5 May, 20:00 UTC. A storm has run since 19:00: Kp 6.8, S4 0.44, an advisory in force. At 20:00 all three machines lose about 22 dB at once; satellites fall from 12 to 3, the RTK fix is lost throughout, cross-track error reaches 22 to 27 metres. The correction link stays up, so not equipment. Did the storm get worse, or did something else start?

## 8. Storm or jammer: the reasoning

2:50–3:35. On the ordinary vote the storm wins 4 to 0, and there is no interference report. But the storm model predicts 9.0 dB of loss from this S4; the machines lost 22.4. The regional level steps down a further 12 dB abruptly and recovers, and one ground transmitter explains 97.4% of the losses. That abrupt step decides it: jammer on top of a storm. Without it, the call would be storm. Halt and report, 1.8 machine-hours; the storm stays its own detection. Confidence 0.50, our lowest. On label TRN-05 it is correct, 0 s onset error.

## 9. Spectral view: one spot, every 357 s

3:35–4:20. LIVE: http://127.0.0.1:8765/#tab=spec&ch=mincn0&scope=0&tr=0 (fallback: this slide).
We take each tractor’s 1 Hz minimum C/N0, cut it into 68-minute half-overlapping windows while moving, and average their power spectra. All three show a repeat every 357 seconds with overtones: one spot revisited each pass, the multipath site. It is display only; the detector never reads it. Multipath is labelled from where the dip is and its shape.

## 10. Limits, and what would help

4:20–4:55. Limits. 40 of 40 on the dev days is in-sample: the first version scored 25.3, and the rest came from fixing errors on those same twelve episodes. Our least certain evaluation call is the 18:30 jam: no report backs it. Confidence is a tally of evidence points, not a probability. Per-satellite C/N0 and angles, an independent position and RF direction finding would separate causes directly. One command regenerates everything. Thank you.

---

# Backup slides (development data, not timed)

## B1. Dev results by cause

Backup. Official scorer on the two labelled development days. All twelve episodes matched with the right class and action, no false positives. Onset errors are zero except the three spoofing ramps and the link fault, never early. TRN-04 lists one tractor more than the label, giving tractor overlap 0.67. This is in-sample.

## B2. Confidence against the labels

Backup. Every labelled detection, grouped by its confidence. All are correct in every band, so these data cannot show whether 0.50 really means less likely to be right than 0.90. Twelve calls is too small a sample. The confidence is a transparent tally of evidence points, capped at 0.90, not a measured probability. The 0.85-and-above band is D155, D250, D379, D483, D529, D560 and D803.

## B3. Pipeline

Backup. Telemetry and the space-weather feeds go into channels, each tuned to one signature. Candidate spans are attributed by a weighted vote plus a few explicit rules, such as jammer on top of a storm. Each detection gets an action, a confidence tally and evidence strings, which go to submission.json and the viewer. The seven cross-checks in analysis.py annotate and never re-classify; the one exception to “text only” is that a ground-source fit with R² of at least 0.9 adds 0.10 to a jamming call’s confidence.
