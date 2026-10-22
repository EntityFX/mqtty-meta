import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

import study


class StudyVerificationTests(unittest.TestCase):
    def test_mean_and_ci95_use_three_seed_sample_standard_deviation(self):
        value = study.estimate([10, 20, 30])
        self.assertEqual(value["mean"], 20)
        self.assertAlmostEqual(value["ci95HalfWidth"], study.T95_DF2 * 10 / math.sqrt(3))
        self.assertEqual(study.estimate([1, 1, 1])["ci95HalfWidth"], 0)
        self.assertIsNone(study.estimate([1, 2])["ci95HalfWidth"])
        self.assertIsNone(study.estimate([])["mean"])

    def test_zero_denominator_is_not_applicable(self):
        self.assertIsNone(study.safe_ratio(0, 0))
        self.assertIsNone(study.ratio_loss(0, 0))
        self.assertEqual(study.ratio_loss(9, 10), 1 - .9)

    def test_quantile_merges_count_ranks_not_backend_percentiles(self):
        self.assertEqual(study.quantile({"1": 990, "100": 10}, .99, .1), .1)
        self.assertEqual(study.quantile({"100": 10, "1": 990}, .999, .1), 10)
        self.assertIsNone(study.quantile({}, .99, .1))

    def test_saved_evidence_hash_tampering_and_path_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "summary.json"
            study.write(file, {"status": "completed"})
            study.write(root / "sha256.json", {"summary.json": study.digest(file)})
            study.verify_seal(root)
            file.write_text("tampered", encoding="utf-8")
            with self.assertRaises(ValueError):
                study.verify_seal(root)
            study.write(root / "sha256.json", {"../escape.json": "0" * 64})
            with self.assertRaises(ValueError):
                study.verify_seal(root)

    def test_independent_recalculation_rejects_incorrect_rates_and_drain(self):
        fixture = study.ROOT / "MqttBenchmark/artifacts/load-balancing-v1-verified-smoke/007-Aedes-RoundRobin-q1-s42/summary.json"
        if not fixture.exists():
            self.skipTest("Optional local smoke fixture unavailable; algebraic tests remain independent.")
        original = study.read(fixture)
        study.validate_summary(original)
        for key in ("uniqueGoodput", "backendCompletedRps", "uniqueReceivedDuringDrain", "publishP99Ms", "backpressureUnsent"):
            altered = copy.deepcopy(original)
            altered[key] += 1
            with self.subTest(key=key), self.assertRaises(ValueError):
                study.validate_summary(altered)

    def test_groups_keep_failed_runs_visible_and_do_not_fabricate_three_repeats(self):
        records = [dict(family="demo", load=1000, qos=0, variant="single16", summary={
            "status": "completed", "backendCompletedRps": 1000, "uniqueGoodput": 1000,
            "publishP99Ms": None, "receiverP99Ms": 1, "attemptDeliveryLoss": 0,
            "backendPublishFailureRate": 0, "backendDeliveryLossRate": 0,
            "delivery": {"uniqueDelivered": 10}, "scheduledPublishes": 10}),
            dict(family="demo", load=1000, qos=0, variant="single16", summary={"status": "failed"})]
        value = study.groups(records)[0]
        self.assertEqual(value["successful"], 1)
        self.assertEqual(value["planned"], 3)
        self.assertIsNone(value["uniqueGoodput"]["ci95HalfWidth"])
        self.assertEqual(value["scheduleFulfillment"]["mean"], 1)
        self.assertIsNone(value["publishP99Ms"]["mean"])


if __name__ == "__main__":
    unittest.main()
