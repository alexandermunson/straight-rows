"""Score the official starter baseline and this solution on the SAME labeled dev window.
The baseline hard-codes the evaluation window, so we patch its constants (module globals) to the dev window."""
import sys, json, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
import baseline_official as base
from straightrows import config, io, official_scorer
from straightrows.evaluate import score_window, report
data = sys.argv[1] if len(sys.argv) > 1 else "data"
params = config.Params.from_json(sys.argv[2]) if len(sys.argv) > 2 and Path(sys.argv[2]).exists() else config.Params()
base.EVAL_START, base.EVAL_END = config.DEV_START, config.DEV_END
sub = {"participant": {"github_handle": "x"}, "repository_url": "https://github.com/x/y", "detections": base.detect(data)}
b = io.load_all(data)
key = {"episodes": [{**e, "recommended_action": config.ACTION[e["class"]]} for e in b.train]}
report(official_scorer.score(sub, key), "OFFICIAL STARTER BASELINE on dev window")
res, _, _ = score_window(b, params, (config.DEV_START, config.DEV_END), b.train)
report(res, "THIS SOLUTION on dev window")
