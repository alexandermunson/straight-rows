"""Dump every number the presentation uses, straight from the detector, scorer and spectral code (no hand-typed values)."""
import json, sys, copy
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, 'src'); sys.path.insert(0, 'scripts')
from straightrows.io import load_all
from straightrows.config import Params, EPOCH, DEV_START, DEV_END, EVAL_START, EVAL_END, ACTION
from straightrows.detect import Detector
from straightrows.report import to_submission
from straightrows.evaluate import score_window
import viewer_spectral
b = load_all('data'); out = {}
# ---- dev scores (official scorer, same call as `cli dev`)
p = Params(); res, sub, _ = score_window(b, p, (DEV_START, DEV_END), b.train)
out['dev'] = dict(score=res['diagnostic_score'], macro_f1=res['macro_f1'], timing=res['mean_timing_credit'], fps=len(res['false_positives']),
                  action_agree=res['action_agreement'], n=res['episode_count'],
                  classes={c: {k: m[k] for k in ('tp', 'fp', 'fn', 'precision', 'recall', 'f1')} for c, m in res['class_metrics'].items()},
                  episodes=[{k: e.get(k) for k in ('episode', 'truth_class', 'predicted_class', 'onset_error_s', 'time_iou', 'tractor_iou', 'correct')} for e in res['episodes']])
# ---- all detections over 72 h with plain-language fields (as the viewer builds them)
p = Params(); p.max_detections = 10 ** 9
D = Detector(b, p, (EPOCH, EVAL_END)); cands = D.run()
s = to_submission(cands, p, 'x', 'https://github.com/x/y', plain=True)
dets = sorted(s['detections'], key=lambda d: d['start'])
ids = {}
for i, d in enumerate(dets): d['viewer_id'] = f'D{i + 1:03d}'
real = [d for d in dets if d['class'] != 'MULTIPATH']
out['real_detections'] = [{k: d[k] for k in ('viewer_id', 'start', 'end', 'tractors', 'class', 'action', 'confidence')} for d in real]
pick = lambda vid: next(d for d in dets if d['viewer_id'] == vid)
out['D837'] = {k: pick('D837')[k] for k in ('start', 'end', 'tractors', 'class', 'action', 'confidence', 'evidence', 'reasoning', 'ops', 'source')}
out['D803'] = {k: pick('D803')[k] for k in ('start', 'end', 'tractors', 'class', 'action', 'confidence', 'evidence')}
c837 = next(c for c in cands if c['cls'] == 'JAMMING' and str(c['start']).startswith('2026-05-05 20:00'))
out['D837_internal'] = dict(scores=c837['scores'], spread_db=c837['spread_db'], s4=c837['s4'], kp_max=c837['kp_max'], dst_min=c837['dst_min'],
                            rfi=[r.get('id') for r in c837['rfi']], notes=c837['notes'], storm_model=c837['ax']['storm'],
                            source={k: v for k, v in c837['ax']['source'].items() if k != 'per'}, source_per=c837['ax']['source']['per'],
                            posterior=c837['ax']['posterior'], cusum=c837['ax']['cusum'], fix=c837['ax']['fix'],
                            stats={t: {k: c837['stats'][t][k] for k in ('cn0_dev_mean', 'xte_max', 'link_frac', 'fixbad_frac', 'sats_min')} for t in c837['tractors']})
from straightrows.report import confidence
out['D837_conf_terms'] = confidence(c837, p)[1]
# a spoof candidate inside the jam was dropped? count XTE excess there
out['concurrent_D837'] = [{k: o[k] for k in ('cls', 'tractors')} | {'start': str(o['start']), 'end': str(o['end'])} for o in c837['concurrent']]
# ---- multipath facts
mp = [d for d in dets if d['class'] == 'MULTIPATH']
per = next(c for c in cands if c['cls'] == 'MULTIPATH')['ax']['period']
out['multipath'] = dict(n_total=len(mp), n_eval=sum(EVAL_START.isoformat() <= d['start'] < EVAL_END.isoformat() for d in mp),
                        period=per, durations=sorted({int((pd.Timestamp(d['end']) - pd.Timestamp(d['start'])).total_seconds()) for d in mp}))
# ---- calibration (same rule as the viewer: matched label of the same class, IoU>=0.30 time, >=0.50 tractors)
truth = b.train
def right(d):
    for t in truth:
        if t['class'] != d['class']: continue
        a, bb, c_, e = map(pd.Timestamp, (d['start'], d['end'], t['start'], t['end']))
        inter = (min(bb, e) - max(a, c_)).total_seconds(); uni = (max(bb, e) - min(a, c_)).total_seconds()
        st, sd = set(t['tractors']), set(d['tractors'])
        if uni > 0 and inter / uni >= .3 and len(st & sd) / len(st | sd) >= .5: return True
    return False
dev_real = [d for d in real if d['start'] < '2026-05-06T02']
dev_scored = [d for d in dets if d['start'] < '2026-05-06T00' and (d['class'] != 'MULTIPATH' or right(d))]
cal = []
for lo, hi, name in [(0, .5, 'below 0.50'), (.5, .7, '0.50 to 0.69'), (.7, .85, '0.70 to 0.84'), (.85, 1.01, '0.85 and above')]:
    rows = [d for d in dev_scored if lo <= d['confidence'] < hi]
    cal.append(dict(band=name, n=len(rows), right=sum(right(d) for d in rows), ids=[d['viewer_id'] for d in rows]))
out['calibration'] = cal
# ---- spectral (display-only module), the numbers the Spectral tab shows
F = {tr: g.set_index('ts').rename(columns={'mean_cn0_dbhz': 'cn0', 'min_cn0_dbhz': 'mincn0', 'speed_mps': 'spd'}).assign(xte=lambda x: x['cross_track_error_m'].abs())
     for tr, g in b.tele.assign(ts=b.tele.ts.dt.tz_convert(None)).groupby('tractor_id')}
sp = viewer_spectral.compute(F, ['T1', 'T2', 'T3'], pd.Timestamp('2026-05-04'), 259200)
out['spectral'] = {tr: {k: v for k, v in sp['psd']['data']['mincn0']['All 72 h'][tr].items() if k != 'db'} for tr in ['T1', 'T2', 'T3']}
out['spectral_meta'] = dict(psd_nper=sp['psd']['nper'], spec_nper=sp['spec']['nper'], spec_hop=sp['spec']['hop'], fmax=sp['fmax'], df=sp['psd']['df'])
json.dump(sp['psd']['data']['mincn0']['All 72 h'], open('presentation/data/psd_mincn0_all.json', 'w'))
Path('presentation/data/facts.json').write_text(json.dumps(out, indent=1, default=str))
print(json.dumps({k: out[k] for k in ('dev',)}, default=str)[:600])
