import copy
import json
from pathlib import Path
import tempfile
import unittest

from prepare_twin_package import ROOT, SOURCES, build, reconcile


class TwinPackageChecks(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / SOURCES["accuracy"]).read_bytes())

    def test_full_observed_failure_set_reconciles(self):
        rows = reconcile(self.data)
        self.assertEqual(len(rows), 85)
        self.assertEqual(sum(r["broker"] == "ActiveMQ" for r in rows), 43)
        self.assertTrue(all(r["causeStatus"] == "notIdentifiedPerRun" for r in rows))

    def test_duplicate_criterion_cannot_replace_missing_one(self):
        self.data["criteria"][1] = copy.deepcopy(self.data["criteria"][0])
        with self.assertRaisesRegex(ValueError, "duplicate criterion"):
            reconcile(self.data)

    def test_missing_run_is_rejected(self):
        self.data["criteria"] = self.data["criteria"][4:]
        with self.assertRaisesRegex(ValueError, "coverage"):
            reconcile(self.data)

    def test_omitted_failure_is_rejected(self):
        self.data["failures"].pop()
        with self.assertRaisesRegex(ValueError, "Failure list"):
            reconcile(self.data)

    def test_qos_zero_ack_is_not_silently_passed(self):
        row = next(r for r in self.data["criteria"] if r["qos"] == 0 and r["name"] == "latencyP99Ms")
        row["status"] = "passed"
        with self.assertRaisesRegex(ValueError, "QoS 0"):
            reconcile(self.data)

    def test_missing_native_point_is_rejected(self):
        self.data["points"].pop()
        with self.assertRaisesRegex(ValueError, "native-context"):
            reconcile(self.data)

    def test_repeat_generation_is_identical_and_overwrite_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder)/"a", Path(folder)/"b"
            build(first); build(second)
            self.assertEqual({p.name:p.read_bytes() for p in first.iterdir()},
                             {p.name:p.read_bytes() for p in second.iterdir()})
            with self.assertRaisesRegex(ValueError, "new output"):
                build(first)


if __name__ == "__main__":
    unittest.main()
