"""Regression tests on the REAL dataset (skipped when data/ has not been downloaded).

They pin the labeled dev score, the evaluation-day detection list, and check that the numbers quoted in evidence strings
match the raw telemetry for the window the evidence states.
"""
import re
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from straightrows import config, io                      # noqa: E402
from straightrows.detect import Detector                 # noqa: E402
from straightrows.evaluate import score_window           # noqa: E402
from straightrows.report import to_submission            # noqa: E402

DATA = ROOT / "data"
EXPECTED = [  # class, tractors, start, end (2026-05-06, UTC)
    ("EQUIPMENT_FAULT", ["T1"], "01:00:00", "02:00:00"),
    ("JAMMING", ["T1", "T2", "T3"], "02:12:00", "02:54:00"),
    ("SPACE_WEATHER", ["T1", "T2", "T3"], "13:00:00", "16:00:00"),
    ("SPOOFING", ["T1"], "16:30:20", "17:12:00"),
    ("JAMMING", ["T1", "T2", "T3"], "18:30:00", "19:00:00"),
    ("EQUIPMENT_FAULT", ["T2"], "19:48:10", "20:28:40"),
    ("SPOOFING", ["T3"], "21:48:30", "22:24:00"),
]


@unittest.skipUnless((DATA / "farm/tractor_telemetry.csv.gz").exists(), "real dataset not downloaded")
class RealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = io.load_all(DATA)
        cls.p = config.Params()
        cands = Detector(cls.b, cls.p, (config.EVAL_START, config.EVAL_END)).run()
        cls.sub = to_submission(cands, cls.p, "x", "https://github.com/x/y")

    def test_dev_score_is_full_with_no_false_positives(self):
        res, _, _ = score_window(self.b, self.p, (config.DEV_START, config.DEV_END), self.b.train)
        self.assertAlmostEqual(res["diagnostic_score"], 40.0, places=6)
        self.assertEqual(res["false_positives"], [])

    def test_eval_detection_list(self):
        real = [(d["class"], d["tractors"], d["start"][11:19], d["end"][11:19])
                for d in self.sub["detections"] if d["class"] != "MULTIPATH"]
        self.assertEqual(real, EXPECTED)

    def test_multipath_reports_fit_the_scorer_exemption_and_the_validator(self):
        mp = [d for d in self.sub["detections"] if d["class"] == "MULTIPATH"]
        self.assertLessEqual(len(self.sub["detections"]), 500)
        self.assertGreater(len(mp), 400)
        for d in mp:
            self.assertEqual(len(d["tractors"]), 1)
            self.assertLessEqual((pd.Timestamp(d["end"]) - pd.Timestamp(d["start"])).total_seconds(), 180)

    def test_evidence_numbers_match_raw_telemetry(self):
        t = self.b.tele
        pat = re.compile(r"(T\d): mean C/N0 ([\d.]+) dB-Hz .*?\|XTE\| mean ([\d.]+) m / max ([\d.]+) m, "
                         r"correction link up (\d+)%, non-RTK-fixed (\d+)%")
        checked = 0
        for d in self.sub["detections"]:
            if d["class"] == "MULTIPATH":
                continue
            a, b = pd.Timestamp(d["start"]), pd.Timestamp(d["end"])
            w = t[(t.ts >= a) & (t.ts < b) & (t.speed_mps > self.p.work_speed)]
            for line in d["evidence"]:
                m = pat.match(line)
                if not m:
                    continue
                g = w[w.tractor_id == m.group(1)]
                self.assertAlmostEqual(float(m.group(2)), g.mean_cn0_dbhz.mean(), delta=0.15, msg=line)
                self.assertAlmostEqual(float(m.group(3)), g.cross_track_error_m.abs().mean(), delta=0.02, msg=line)
                self.assertAlmostEqual(float(m.group(4)), g.cross_track_error_m.abs().max(), delta=0.02, msg=line)
                self.assertAlmostEqual(float(m.group(6)), 100 * (g.fix_state != "RTK_FIXED").mean(), delta=1.0, msg=line)
                checked += 1
        self.assertGreaterEqual(checked, 13)      # 1+3+3+1+3+1+1 per-machine lines


if __name__ == "__main__":
    unittest.main()
