"""Unit tests. Run: python -m unittest discover -s tests -v   (uses a small synthetic dataset, ~20 s)"""
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from straightrows import config, detect, io, report, stats, synth
from straightrows.evaluate import score_window
from straightrows.sda import SDA


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


class Stats(unittest.TestCase):
    def test_t_quantile_matches_tables(self):
        self.assertAlmostEqual(stats.t_quantile(0.975, 10), 2.2281, places=3)
        self.assertAlmostEqual(stats.t_quantile(0.975, 1), 12.706, places=2)
        self.assertAlmostEqual(stats.t_quantile(0.025, 10), -2.2281, places=3)
        self.assertAlmostEqual(stats.t_quantile(0.95, 5), 2.015, places=3)

    def test_r_crit_matches_table(self):
        self.assertAlmostEqual(stats.r_crit(10), 0.632, places=3)
        self.assertAlmostEqual(stats.r_crit(5), 0.878, places=3)
        self.assertGreater(stats.r_crit(5), stats.r_crit(50))

    def test_pearson_and_significance(self):
        x = np.arange(20.0)
        self.assertAlmostEqual(stats.pearson_r(x, 2 * x + 1)[0], 1.0)
        self.assertAlmostEqual(stats.pearson_r(x, -x)[0], -1.0)
        self.assertTrue(np.isnan(stats.pearson_r(x, np.ones(20))[0]))
        rng = np.random.default_rng(0)
        self.assertTrue(stats.correlate(x, x + rng.normal(0, 1, 20))["significant"])
        self.assertFalse(stats.correlate(rng.normal(size=8), rng.normal(size=8))["significant"])

    def test_chauvenet(self):
        self.assertAlmostEqual(stats.chauvenet_z(10), 1.96, places=2)
        self.assertAlmostEqual(stats.chauvenet_z(3), 1.38, places=2)
        x = np.array([1, 2, 2, 3, 2, 1, 2, 3, 2, 50.0, np.nan])
        k = stats.chauvenet_keep(x)
        self.assertFalse(k[9]); self.assertFalse(k[10]); self.assertTrue(k[:9].all())
        # clean normal data: iteration may trim a few tail points, but never a large share
        self.assertLess((~stats.chauvenet_keep(np.random.default_rng(1).normal(size=2000))).mean(), 0.01)

    def test_mean_ci_and_blocks(self):
        ci = stats.mean_ci([1, 2, 3, 4, 5])
        self.assertAlmostEqual(ci["half"], 2.7764 * np.sqrt(2.5 / 5), places=3)
        self.assertTrue(np.isnan(stats.mean_ci([3.0])["lo"]))
        np.testing.assert_allclose(stats.block_means([1, 3, 5, 7, 9], 2), [2, 6])


class Regression(unittest.TestCase):
    def test_linfit_exact_and_noisy(self):
        x = np.arange(30.0)
        f = stats.linfit(x, 3 * x + 2)
        self.assertAlmostEqual(f["slope"], 3.0); self.assertAlmostEqual(f["intercept"], 2.0)
        self.assertAlmostEqual(f["r2"], 1.0); self.assertTrue(f["slope_signif"])
        rng = np.random.default_rng(3)
        g = stats.linfit(x, rng.normal(0, 1, 30))
        self.assertFalse(g["slope_signif"])                      # pure noise: slope CI straddles zero
        self.assertAlmostEqual(g["syx"], np.std(rng.normal(0, 1, 5000)), delta=0.35)
        self.assertTrue(np.isnan(stats.linfit([1, 2], [1, 2])["slope"]))

    def test_slope_ci_matches_hand_calc(self):
        x = np.array([1, 2, 3, 4, 5.0]); y = np.array([2.1, 3.9, 6.2, 7.8, 10.1])
        f = stats.linfit(x, y)
        sxx = ((x - x.mean()) ** 2).sum()
        self.assertAlmostEqual(f["slope_half"], 3.1824 * f["syx"] / np.sqrt(sxx), places=3)   # t(0.975, 3) = 3.1824

    def test_std_residuals(self):
        x = np.arange(40.0); y = 0.1 * x + np.where(x % 2 == 0, 0.2, -0.2)
        f = stats.linfit(x, y)
        z = stats.std_residuals([100.0], [0.1 * 100 + 5.0], f)
        self.assertGreater(z[0], 10)


class GlitchRule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        synth.write(cls.tmp.name, seed=11)
        cls.d = detect.Detector(io.load_all(Path(cls.tmp.name) / "data"), config.Params())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_sustained_vs_single_bin(self):
        F = self.d.F
        n = len(F["resid"]); tr = "T1"
        rng = np.random.default_rng(5)
        F.f["resid"] = F["resid"].copy(); F.f["work"] = F["work"].copy()
        F.f["resid"][tr] = rng.normal(0, 0.5, n); F.f["work"][tr] = True
        y = F.f["resid"][tr].values.copy()
        y[3000:3006] = -8.0                      # sustained 60 s dip
        y[5000] = -8.0                           # one-bin spike
        F.f["resid"][tr] = y
        self.assertTrue(self.d._sustained(tr, 3000, 3006)[0])
        self.assertFalse(self.d._sustained(tr, 5000, 5001)[0])
        F.f["work"]["T2"] = False                            # not working: no usable context -> never filter on missing data
        self.assertTrue(self.d._sustained("T2", 3000, 3006)[0])


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
            if d["class"] != "MULTIPATH":                 # multipath evidence is deliberately compact (one report per pass)
                self.assertIn("S4", joined)
            else:
                self.assertIn("recurs", joined)
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


class Controlled(unittest.TestCase):
    """Small hand-built 8 h scenarios (healthy baseline + one injected cause) so each rule is tested in isolation."""
    T0 = pd.Timestamp("2026-05-06T00:00:00Z")
    N = 8 * 3600
    TRS = ["T1", "T2", "T3"]

    @classmethod
    def build(cls, edit, s4_hi=None, kp_hi=None):
        """edit(frames, sl): mutate per-tractor numpy columns; sl(a_min, b_min) -> slice of seconds."""
        ts = pd.date_range(cls.T0, periods=cls.N, freq="1s")
        rng = np.random.default_rng(3)
        fr = {}
        for k, tr in enumerate(cls.TRS):
            fr[tr] = dict(
                lat=46.9 + (np.arange(cls.N) % 1000) * 1e-6, lon=np.full(cls.N, -97.2 + 0.0035 * k),
                xte=rng.normal(0, 0.01, cls.N), sats=np.full(cls.N, 12.0), cn0=45 + rng.normal(0, 0.3, cls.N),
                mincn0=39 + rng.normal(0, 0.4, cls.N), fix=np.full(cls.N, "RTK_FIXED", dtype=object))
        edit(fr, lambda a, b: slice(int(a * 60), int(b * 60)))
        tele = pd.concat([pd.DataFrame({
            "ts": ts, "tractor_id": tr, "lat": f["lat"], "lon": f["lon"], "speed_mps": 4.5, "cross_track_error_m": f["xte"],
            "num_sats": f["sats"].astype(int), "mean_cn0_dbhz": f["cn0"], "min_cn0_dbhz": f["mincn0"], "fix_state": f["fix"],
            "base_link_ok": True}) for tr, f in fr.items()], ignore_index=True)
        s4 = pd.DataFrame({"ts": pd.date_range(cls.T0, periods=cls.N // 900, freq="15min"), "s4": 0.05})
        if s4_hi:
            m = (s4.ts >= cls.T0 + pd.Timedelta(minutes=s4_hi[0])) & (s4.ts < cls.T0 + pd.Timedelta(minutes=s4_hi[1]))
            s4.loc[m, "s4"] = 0.45
        kp = pd.DataFrame({"ts": pd.date_range(cls.T0 - pd.Timedelta(hours=6), periods=6, freq="3h"), "kp": 2.0})
        if kp_hi is not None:
            kp.loc[kp.ts == cls.T0 + pd.Timedelta(hours=kp_hi), "kp"] = 7.0
        dst = pd.DataFrame({"ts": pd.date_range(cls.T0 - pd.Timedelta(hours=6), periods=14, freq="1h"), "dst": -10.0})
        return io.Bundle(tele, kp, dst, s4, [], pd.DataFrame(), [])

    def run_det(self, bundle):
        return detect.Detector(bundle, config.Params(), (self.T0, self.T0 + pd.Timedelta(hours=8))).run()

    @staticmethod
    def cn0_loss(fr, sl, depths, sats=None):
        for tr, d in zip(("T1", "T2", "T3"), depths):
            fr[tr]["cn0"][sl] -= d
            fr[tr]["mincn0"][sl] -= d
            if sats:
                fr[tr]["sats"][sl] = sats

    def test_heterogeneous_loss_with_quiet_space_weather_is_jamming(self):
        b = self.build(lambda fr, sl: self.cn0_loss(fr, sl(120, 160), (12, 9, 6), sats=8))
        c = [x for x in self.run_det(b) if x["cls"] != "MULTIPATH" and x["channel"] == "R"]
        self.assertEqual([x["cls"] for x in c], ["JAMMING"])
        self.assertGreater(c[0]["spread_db"], 4.0)
        ev = " ".join(report.evidence(c[0], config.Params(), [("base", 0.5)]))
        self.assertIn("Spatial uniformity", ev)
        self.assertIn("local emitter", ev)
        self.assertRegex(ev, r"Report-ready summary: .* \(onset \d\d:\d\d:\d\dZ\); location 46\.\d{6}, -97\.\d{6}; affected receivers T1, T2, T3")
        self.assertIn("suspected cause: jamming", ev)
        self.assertIn("Notify: a GNSS interference report is appropriate", ev)

    def test_uniform_loss_with_elevated_s4_is_space_weather(self):
        b = self.build(lambda fr, sl: self.cn0_loss(fr, sl(90, 180), (10, 10, 10), sats=10), s4_hi=(75, 195), kp_hi=0)
        c = [x for x in self.run_det(b) if x["channel"] == "R"]
        self.assertEqual([x["cls"] for x in c], ["SPACE_WEATHER"])
        self.assertLess(c[0]["spread_db"], 1.0)

    def test_kp_credit_respects_lookback(self):
        """A Kp value is valid for 3 h after its stamp. Stamp 7.0 at T0-6h -> valid until T0-3h."""
        b = self.build(lambda fr, sl: None)
        b.kp["kp"] = 2.0
        b.kp.loc[b.kp.ts == self.T0 - pd.Timedelta(hours=6), "kp"] = 7.0
        sda = SDA(b, config.Params())
        a, e = self.T0 + pd.Timedelta(hours=1), self.T0 + pd.Timedelta(hours=1.5)       # lookback start: T0-5h (6 h) or T0-2h (3 h)
        self.assertGreaterEqual(sda.kp_max(a, e, lead_h=6)[0], 5.0)                      # the old 6 h lookback still credits it
        self.assertLess(sda.kp_max(a, e, lead_h=config.Params().kp_lead_h)[0], 5.0)      # the 3 h default does not
        self.assertEqual(config.Params().kp_lead_h, 3.0)

    def test_common_mode_spoof_on_all_machines_is_caught(self):
        def edit(fr, sl):
            ramp = np.concatenate([np.linspace(0, 6, 30 * 60), np.full(20 * 60, 6.0)])
            for tr in self.TRS:
                fr[tr]["xte"][sl(180, 230)] += ramp
        c = [x for x in self.run_det(self.build(edit)) if x["cls"] == "SPOOFING"]
        self.assertEqual(len(c), 1)
        self.assertEqual(sorted(c[0]["tractors"]), self.TRS)
        self.assertEqual(c[0]["channel"], "XC")
        ev = " ".join(report.evidence(c[0], config.Params(), [("base", 0.5)]))
        self.assertIn("neighbour comparison used for single-machine spoofing is unavailable", ev)
        self.assertIn("suspected cause: spoofing", ev)

    def test_common_mode_guard_ignores_storm_and_jam(self):
        for depth, sats in ((10, 10), (18, 5)):                    # storm-like and jam-like signal collapse with big XTE everywhere
            def edit(fr, sl, depth=depth, sats=sats):
                self.cn0_loss(fr, sl(180, 230), (depth,) * 3, sats=sats)
                for tr in self.TRS:
                    fr[tr]["xte"][sl(180, 230)] += 3.0
            cands = self.run_det(self.build(edit, s4_hi=(165, 255)))
            self.assertFalse([x for x in cands if x["cls"] == "SPOOFING"], [(x["cls"], x["channel"]) for x in cands])

    def test_notify_guidance_per_class(self):
        b = self.build(lambda fr, sl: self.cn0_loss(fr, sl(90, 180), (10, 10, 10), sats=10), s4_hi=(75, 195), kp_hi=0)
        sw = [x for x in self.run_det(b) if x["cls"] == "SPACE_WEATHER"][0]
        ev = " ".join(report.evidence(sw, config.Params(), [("base", 0.5)]))
        self.assertIn("no interference report needed", ev)
        self.assertNotIn("Report-ready summary", ev)
        eq = dict(sw, cls="EQUIPMENT_FAULT", mode="antenna")
        self.assertIn("not a GNSS interference report", " ".join(report.operator_lines(eq, config.Params(), [("base", 0.5)])))
        self.assertEqual(report.operator_lines(dict(sw, cls="MULTIPATH"), config.Params(), [("base", 0.5)]), [])

    def test_rfi_camelcase_fields_give_distance_and_uncertainty(self):
        b = self.build(lambda fr, sl: None)
        b.rfi = pd.DataFrame([{"id": "R1", "ts": self.T0 + pd.Timedelta(minutes=10), "centerLat": 46.95, "centerLon": -97.2,
                               "semiMajor_km": 2.0, "confidence": 0.5}])
        m = SDA(b, config.Params()).rfi_matches(self.T0, self.T0 + pd.Timedelta(minutes=5), 46.9, -97.2)
        self.assertEqual(len(m), 1)
        self.assertAlmostEqual(m[0]["_dist_km"], 5.57, delta=0.2)           # real distance, not a blanket "within"
        self.assertTrue(m[0]["_within"])
        far = SDA(b, config.Params()).rfi_matches(self.T0, self.T0 + pd.Timedelta(minutes=5), 47.5, -97.2)
        self.assertFalse(far[0]["_within"])                                  # 66 km away is outside 15 km + 2 km


class Privacy(unittest.TestCase):
    def test_no_email_in_evidence_templates(self):
        src = (ROOT / "src/straightrows/report.py").read_text()
        self.assertNotIn("@", src.replace("@dataclass", ""))


if __name__ == "__main__":
    unittest.main()
