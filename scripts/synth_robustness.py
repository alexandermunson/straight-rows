"""Pipeline robustness on several synthetic seeds (dev AND eval, scored with the official scorer).
This validates the code path, NOT real-data accuracy: the synthetic physics are my guess."""
import sys, tempfile, json
sys.path.insert(0, "src")
from straightrows import synth, io, config
from straightrows.evaluate import score_window
import pathlib
hard = '--hard' in sys.argv
for seed in (1, 2, 3, 4, 5):
    d = pathlib.Path(tempfile.mkdtemp())
    synth.write(d, seed, hard)
    b = io.load_all(d / "data")
    key = json.loads((d / "eval_key.json").read_text())["episodes"]
    p = config.Params()
    rd, _, _ = score_window(b, p, (config.DEV_START, config.DEV_END), b.train)
    re, _, _ = score_window(b, p, (config.EVAL_START, config.EVAL_END), key)
    print(f"seed {seed}: dev {rd['diagnostic_score']:.1f}/40 (FP {len(rd['false_positives'])}) | eval {re['diagnostic_score']:.1f}/40 (FP {len(re['false_positives'])}, actions {re['action_agreement']}/8)")
