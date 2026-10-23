import copy
from pathlib import Path
import tempfile
import sys
import time
import unittest
from unittest.mock import patch

from sensitivity_campaign import execute, sha, storage, tick_units, variant_config, write, read


class SensitivityConfigurationTests(unittest.TestCase):
    def config(self):
        return {"experiment": {"warmupTicks": 100000, "measurementTicks": 200000,
                               "drainTimeoutTicks": 500000, "offeredRps": 321., "randomSeed": 42},
                "ticks": {"tickPeriod": "00:00:00.0000100", "outgoingWaitTicks": 1, "receiveWaitPeriod": "00:00:01"},
                "network": {"transferTicks": 15, "throughputWindowTicks": 1000, "queueCapacity": 100000,
                            "capacityBytesPerSecond": 125000000}}

    def test_halved_tick_preserves_every_physical_window_and_transport_duration(self):
        original = self.config(); before = copy.deepcopy(original)
        changed = variant_config(original, "halfTick")
        t0, t1 = [tick_units(c["ticks"]["tickPeriod"]) for c in (original, changed)]
        self.assertEqual(t0, 2 * t1)
        for group, names in (("experiment", ("warmupTicks", "measurementTicks", "drainTimeoutTicks")),
                             ("ticks", ("outgoingWaitTicks",)),
                             ("network", ("transferTicks", "throughputWindowTicks"))):
            for name in names:
                self.assertEqual(original[group][name] * t0, changed[group][name] * t1)
        self.assertEqual(changed["experiment"]["offeredRps"], 321)
        self.assertEqual(changed["experiment"]["randomSeed"], 42)
        self.assertEqual(changed["network"]["queueCapacity"], 100000)
        self.assertEqual(original, before)

    def test_longer_window_changes_only_measurement_duration(self):
        original = self.config(); actual = variant_config(original, "doubleWindow")
        self.assertEqual(actual["experiment"]["measurementTicks"], 400000)
        actual["experiment"]["measurementTicks"] = 200000
        self.assertEqual(actual, original)

    def test_baseline_is_exact_copy_and_unknown_or_unrepresentable_variant_rejected(self):
        original = self.config(); baseline = variant_config(original, "baseline")
        self.assertEqual(baseline, original); self.assertIsNot(baseline, original)
        with self.assertRaises(ValueError): variant_config(original, "unknown")
        original["ticks"]["tickPeriod"] = "00:00:00.0000001"
        with self.assertRaisesRegex(ValueError, "representable"): variant_config(original, "halfTick")

    def test_missing_mount_rejected_without_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "new"
            with self.assertRaises(ValueError): storage(Path(directory), target)
            self.assertFalse(target.exists())

    def test_atomic_progress_write_leaves_no_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            write(path, {"status": "running", "counts": {"done": 2}})
            write(path, {"status": "completed", "counts": {"done": 3}})
            self.assertEqual(read(path)["counts"]["done"], 3)
            self.assertFalse(path.with_suffix(".json.partial").exists())

    def test_real_child_failure_criteria_are_data_and_baseline_mismatch_retains_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "rows").mkdir()
            fake = root / "cli.py"
            fake.write_text('''import sys,json
from pathlib import Path
target=Path(sys.argv[sys.argv.index('--output')+1])/'run'
target.mkdir(parents=True)
(target/'summary.json').write_text(json.dumps({'status':'completed','acceptanceCriteria':[{'status':'failed'}]}))
(target/'latency-histogram.json').write_text('{}')
(target/'counters.json').write_text(json.dumps({'brokers':{},'receivers':{'r':1},'receiverScope':'test'}))
sys.exit(3)
''', encoding="utf-8")
            config = root / "config.json"; config.write_text("{}")
            job = {"id": "test", "kind": "matrix", "variant": "baseline", "config": str(config),
                   "configSha256": sha(config), "expectedOriginalSummary": None}
            plan = {"mount": str(root), "runtime": str(fake), "jobTimeoutSeconds": 30, "jobRssLimitBytes": 1024**3}
            original_sleep = time.sleep
            with patch("sensitivity_campaign.storage"), patch("sensitivity_campaign.time.sleep", side_effect=lambda _: original_sleep(.02)):
                row = execute(job, root, plan, sys.executable, lambda *args: None)
                self.assertIsNone(row["technicalError"])
                self.assertTrue(row["fidelityFailed"])
                job["expectedOriginalSummary"] = {"wrong": "reference"}
                failed = execute(job, root, plan, sys.executable, lambda *args: None)
            self.assertIn("Baseline differs", failed["technicalError"])
            self.assertTrue((root / "runs/test/attempt-001.json").is_file())
            self.assertTrue((root / "runs/test/attempt-002.json").is_file())


if __name__ == "__main__": unittest.main()
