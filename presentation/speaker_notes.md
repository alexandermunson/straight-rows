# Speaker notes: Straight Rows (5 minutes)

Times are cumulative targets (about 150 words per minute). LIVE marks a switch to the viewer; each has a fallback: stay on the slide, which shows the same data.

Before you start: run `python scripts/viewer.py --no-open` (port 8765) from the current `main` branch and open http://127.0.0.1:8765/ in a second tab.

| Slide | Viewer address |
|---|---|
| 7 | http://127.0.0.1:8765/#t0=156600&t1=162000&ev=21 (the 5 May, 20:00 UTC event, shown 19:30–21:00) |
| 9 | http://127.0.0.1:8765/#tab=spec&ch=mincn0&scope=0&tr=0 (Spectral tab, weakest-satellite signal, all three days) |

## 1. Same symptom, five causes, five actions

0:00–0:25. Three tractors near Casselton, North Dakota, steer themselves using satellite positioning. When that guidance goes wrong, the operator sees much the same thing every time: the signal weakens or the tractor drifts off its row. But there are five different reasons, and each one needs a different response. Our system reads the tractors’ data every second, together with space-weather readings and interference reports, names the most likely cause, recommends what to do, and shows the evidence behind the call.

## 2. Space weather

0:25–0:45. Space weather. A solar storm disturbs the upper atmosphere, and satellite signals fade across the whole region at once. So every tractor loses the same amount of signal, at the same moment, while the space-weather readings show a storm. This is a labelled example from the development data. The response is to stop, or to keep working at reduced accuracy. The costly mistake is calling a jammer a storm, because then nobody reports the interference.

## 3. Jamming

0:45–1:05. Jamming. A transmitter on the ground drowns out the satellite signals near it. Every tractor loses signal, but by different amounts, because each one is a different distance from the source. The sky is calm, and an interference report may arrive ten to thirty minutes later, as one did here. The response is to stop and report the interference. If it was really a storm, the only cost is one unneeded report; stopping was right either way.

## 4. Spoofing

1:05–1:25. Spoofing. Someone transmits false satellite signals that slowly pull one tractor’s computed position away from where it really is. Its signal looks perfectly healthy, so the only sign is the tractor drifting off its planned row for minutes while its neighbours hold their lines. In this example tractor 1 drifts about twelve metres. The response is to stop, check the position with an independent source, and report it. If we miss it, the tractor plants metres off the row with no warning.

## 5. Equipment fault

1:25–1:45. Equipment fault. Part of one tractor fails: its antenna, its receiver, or the radio that brings in corrections from the base station. Only that tractor shows the problem. Here tractor 1’s correction link keeps dropping out for 48 minutes while its signal, and the other two tractors, stay normal. The response is to send that tractor for repair and let the others keep working; stopping everyone would idle healthy machines.

## 6. Multipath

1:45–2:05. Multipath. Near one edge of the field, signals bounce off a structure and interfere with themselves. Every time a tractor passes that spot, its weakest satellite dips for between 8 seconds and 3 minutes, then recovers; steering is unaffected. Here tractor 3 dips for 17 seconds about every six minutes, once per lap. It is a false alarm, so the response is to keep working. Over the three days there are 1,415 of these dips; stopping for each would end the working day.

## 7. One event, end to end: 5 May, 20:00 UTC

2:05–2:50. LIVE: http://127.0.0.1:8765/#t0=156600&t1=162000&ev=21 (fallback: stay on this slide; it shows the same panels).
One labelled event from the development data, 5 May at 20:00 UTC. A storm has been running since 19:00: the geomagnetic index is at storm level, scintillation is high, and a storm warning is in force. At 20:00 all three tractors lose about 22 decibels of signal at once. Their satellites fall from twelve to as few as three, precise positioning is lost the whole time, and they wander 22 to 27 metres off their rows. The correction link stays up, so it is not an equipment fault. The question is whether the storm got worse, or something else started.

## 8. Storm or jammer: the reasoning

2:50–3:35. On the usual vote the storm wins four points to nothing: every space-weather reading points to it, and no interference report arrived. But our storm model, fitted over all three days, says this much scintillation explains only about 9 of the 22 decibels lost. On top of the storm, the signal fell a further 12 decibels in one sudden step, and came back 36 minutes later. And a single transmitter on the ground fits 97 percent of the pattern of losses. That sudden step is the rule that decides it: a jammer inside a storm. Without it, the call would be storm. So we stop and report, costing 1.8 tractor-hours, and the storm stays its own detection. Confidence is 0.50, our lowest, because the storm offers a real alternative and no report confirms it. Against the label, the call is correct, with the start time exact.

## 9. A repeat every 357 seconds

3:35–4:20. LIVE: http://127.0.0.1:8765/#tab=spec&ch=mincn0&scope=0&tr=0 (fallback: this slide).
The spectral view asks a different question: does anything in the signal repeat on a regular rhythm? We take each tractor’s weakest-satellite signal, cut it into hour-long stretches while the tractor is moving, and measure how strongly each rhythm is present. All three tractors show the same clear repeat every 357 seconds, plus its harmonics: one spot in the field, passed once every lap. That is the reflection site. The lower panel shows the same thing over time for tractor 1: the evenly spaced bright lines are that repeat while the tractors work, and the bright vertical bands are events such as jams and antenna faults. It is a second way to look at the data, not part of the detector: the detector never reads it, and recognises a reflection from where the dip happens and what the dip looks like.

---

# Backup slides (not timed)

## B1. From raw data to a decision

Backup. The tractors’ data and the space-weather feeds go into a set of signals, each tuned to one kind of problem: a drop across the whole fleet, a drop on one tractor, drift off the row, loss of the correction link, and short dips. A points vote plus a few explicit rules, such as “a sudden extra drop inside a storm means a jammer”, names the cause. Each detection then gets an action, a confidence tally and written evidence, which go into the submission file and the interactive viewer. Seven independent cross-checks, including a storm model, a ground-transmitter fit, a live-alarm test and a probability check, add evidence but never change the cause; the one exception is that a good ground-transmitter fit adds 0.10 to a jam’s confidence.

## B2. Results on the labelled days (hidden)

Backup. The official scorer, run on the two labelled development days. All twelve examples were found with the right cause and the right action, and there were no false alarms. Start times are exact except for the three spoofing attacks, which ramp up slowly and so are seen 20 to 190 seconds late, and the correction-link fault, 10 seconds late. One jam lists a third tractor that the label leaves out; it lost only a little signal. This is a practice score, because the rules were developed on these same examples.

## B3. Confidence against the labels (hidden)

Backup. Every labelled detection, grouped by its confidence. All of them were correct in every band, so these data cannot show whether 0.50 really means less likely to be right than 0.90; twelve calls is too small a sample. What the table does show is that the low-confidence calls are exactly the hard cases: a jammer inside a storm, a spoof inside a storm, and an antenna fault that nothing measures directly. The confidence is a transparent tally of evidence points, capped at 0.90, not a measured probability.
