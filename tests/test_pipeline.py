"""Unit tests. Run: python -m unittest discover -s tests -v   (uses a small synthetic dataset, ~20 s)"""
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from straightrows import config, detect, io, report, synth
from straightrows.evaluate import score_window


class Segmentation(unittest.TestCase):
    def test_runs_and_bridge(self):
        m = np.array([0, 1, 1, 0, 1, 0, 0, 0, 1], bool)
        self.assertEqual(detect._runs(m), [(1, 3), (4, 5), (8, 9)])
        self.assertTrue(detect._bridge(m, 1)[3])
        self.assertFalse(detect._bridge(m, 1)[6])

    def test_hysteresis_extends_onset(self):
        p = config.Params(bin_s=10)
        hi = np.zeros(100, bool); hi[50:80] = True
        lo = np.zeros(100, bool); lo[40:85] = True
        self.assertEqual(detect.segments(hi, lo, p, 180), [(40, 85)])
        self.assertEqual(detect.segments(hi, lo, p, 600), [])      # too short to count


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        synth.write(cls.tmp.name, seed=11)
        cls.b = io.load_all(Path(cls.tmp.name) / "data")
        cls.key = json.loads((Path(cls.tmp.name) / "eval_key.json").read_text())["episodes"]
        cls.p = config.Params()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_loader_shapes(self):
        self.assertEqual(len(self.b.tele), 777_600)
        self.assertEqual(len(self.b.train), 12)
        self.assertTrue(self.b.tele.ts.dt.tz is not None)

    def test_dev_scores_high_on_synth(self):
        res, _, _ = score_window(self.b, self.p, (config.DEV_START, config.DEV_END), self.b.train)
        self.assertGreater(res["diagnostic_score"], 30)

    def test_eval_submission_valid_and_scored(self):
        res, sub, cands = score_window(self.b, self.p, (config.EVAL_START, config.EVAL_END), self.key,
                                       repo="https://github.com/someone/solution")
        sub["participant"]["github_handle"] = "someone"
        f = Path(self.tmp.name) / "s.json"; f.write_text(json.dumps(sub))
        r = subprocess.run([sys.executable, str(ROOT / "scripts/validate_submission.py"), str(f)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertGreater(res["diagnostic_score"], 30)
        for d in sub["detections"]:                       # evidence must be checkable: numbers + timestamps
            joined = " ".join(d["evidence"])
            self.assertRegex(joined, r"\d{2}:\d{2}:\d{2}Z")
            self.assertIn("S4", joined)
            self.assertTrue(0 <= d["confidence"] <= 1)
            self.assertEqual(d["action"], config.ACTION[d["class"]])

    def test_no_detections_outside_window(self):
        for c in detect.Detector(self.b, self.p).run():
            self.assertGreaterEqual(c["start"], config.EVAL_START)
            self.assertLessEqual(c["end"], config.EVAL_END)

    def test_storm_does_not_hide_machine_fault(self):
        """Causes may overlap: antenna fault on T3 and spoofing on T1 inside the dev storm must stay separate."""
        cands = detect.Detector(self.b, self.p, (config.DEV_START, config.DEV_END)).run()
        classes = {(c["cls"], tuple(c["tractors"])) for c in cands}
        self.assertIn(("SPACE_WEATHER", ("T1", "T2", "T3")), classes)
        self.assertIn(("EQUIPMENT_FAULT", ("T3",)), classes)
        self.assertIn(("SPOOFING", ("T1",)), classes)


class Privacy(unittest.TestCase):
    def test_no_email_in_evidence_templates(self):
        src = (ROOT / "src/straightrows/report.py").read_text()
        self.assertNotIn("@", src.replace("@dataclass", ""))


if __name__ == "__main__":
    unittest.main()
