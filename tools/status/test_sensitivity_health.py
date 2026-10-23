import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sensitivity_health import analyze, collect, service_fields


class SensitivityHealthTests(unittest.TestCase):
    def snapshot(self):
        return {"observedAtUtc": "2026-01-01T00:00:10+00:00", "progressMtimeUtc": "2026-01-01T00:00:09+00:00",
                "progress": {"status": "running", "technicalErrors": 0, "counts": {
                    "pilot": {"done": 8, "total": 8, "failedCriteria": 4},
                    "matrix": {"done": 10, "total": 216, "failedCriteria": 3}}, "active": {
                        "job": {"pid": 7, "elapsedSeconds": 20, "peakRssBytes": 100}}},
                "processes": {"7": {"alive": True}},
                "service": {"ActiveState": "active", "Result": "success"},
                "mountAvailable": True, "disk": {"free": 10000},
                "limits": {"timeoutSeconds": 100, "maxRssBytes": 1000, "minFreeBytes": 1000}}

    def check(self, snapshot):
        return analyze(snapshot, now=1767225611)

    def codes(self, report):
        return {f["code"] for f in report["findings"]}

    def test_scientific_failure_is_not_technical_or_crash(self):
        report = self.check(self.snapshot())
        self.assertEqual((report["completed"], report["scientificFailedCriteria"], report["technicalErrors"]), (18, 7, 0))
        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["observationFresh"])

    def test_missing_heartbeat_cannot_prove_stall(self):
        snapshot = self.snapshot()
        del snapshot["progressMtimeUtc"]
        report = self.check(snapshot)
        self.assertIn("heartbeatUnknown", self.codes(report))
        self.assertNotIn("heartbeatStale", self.codes(report))

    def test_stale_observation_distinct_from_heartbeat_stall(self):
        report = analyze(self.snapshot(), now=1767225700)
        self.assertFalse(report["observationFresh"])
        self.assertIn("snapshotStale", self.codes(report))
        self.assertNotIn("heartbeatStale", self.codes(report))

    def test_stale_progress_in_live_snapshot_warns(self):
        snapshot = self.snapshot()
        snapshot["progressMtimeUtc"] = "2025-12-31T23:59:00Z"
        self.assertIn("heartbeatStale", self.codes(self.check(snapshot)))

    def test_stopped_service_only_critical_if_incomplete(self):
        snapshot = self.snapshot()
        snapshot["service"] = "ActiveState=inactive\nResult=exit-code\n"
        self.assertIn("serviceStoppedIncomplete", self.codes(self.check(snapshot)))
        snapshot["progress"]["status"] = "completed"
        self.assertNotIn("serviceStoppedIncomplete", self.codes(self.check(snapshot)))

    def test_missing_pid_is_warning_and_unknown_pid_is_not_failure(self):
        snapshot = self.snapshot()
        snapshot["processes"]["7"] = {"alive": False}
        report = self.check(snapshot)
        self.assertEqual(report["status"], "warning")
        self.assertIn("activeProcessMissing", self.codes(report))
        snapshot["processes"] = {}
        self.assertNotIn("activeProcessMissing", self.codes(self.check(snapshot)))

    def test_timeout_and_rss_warn_before_limit(self):
        snapshot = self.snapshot()
        snapshot["progress"]["active"]["job"].update(elapsedSeconds=81, peakRssBytes=810)
        report = self.check(snapshot)
        self.assertEqual(report["status"], "warning")
        self.assertTrue({"timeoutApproaching", "rssApproaching"}.issubset(self.codes(report)))

    def test_timeout_and_rss_exceeded_are_critical(self):
        snapshot = self.snapshot()
        snapshot["progress"]["active"]["job"].update(elapsedSeconds=100, peakRssBytes=1000)
        self.assertEqual(self.check(snapshot)["status"], "critical")

    def test_storage_missing_or_below_reserve_critical(self):
        snapshot = self.snapshot()
        snapshot["mountAvailable"] = False
        snapshot["disk"]["free"] = 999
        self.assertTrue({"mountMissing", "diskReserveViolated"}.issubset(self.codes(self.check(snapshot))))

    def test_unknown_limits_do_not_invent_operational_guard(self):
        snapshot = self.snapshot()
        del snapshot["limits"]
        report = self.check(snapshot)
        self.assertNotIn("timeoutApproaching", self.codes(report))
        self.assertIn("storageEvidenceIncomplete", self.codes(report))

    def test_analysis_preserves_input_and_technical_reason(self):
        snapshot = self.snapshot()
        snapshot["progress"].update(technicalErrors=1, status="failed", reason="jobTimeout")
        original = copy.deepcopy(snapshot)
        report = self.check(snapshot)
        self.assertEqual(snapshot, original)
        self.assertIn("campaignFailed", self.codes(report))
        self.assertEqual(report["status"], "critical")

    def test_cli_exports_new_snapshot_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "source.json", Path(folder) / "export.json"
            source.write_text(json.dumps(self.snapshot()))
            command = [sys.executable, str(Path(__file__).with_name("sensitivity_health.py")), "--snapshot", str(source), "--save-snapshot", str(output)]
            first = subprocess.run(command, capture_output=True, check=True)
            self.assertEqual(json.loads(output.read_text()), self.snapshot())
            self.assertEqual(json.loads(first.stdout)["technicalErrors"], 0)
            second = subprocess.run(command, capture_output=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(json.loads(output.read_text()), self.snapshot())

    def test_collect_rejects_non_linux_without_probing_server(self):
        with patch("sensitivity_health.sys.platform", "win32"):
            with self.assertRaisesRegex(ValueError, "Linux-only"):
                collect("root", "mount", "service")

    def test_collect_exports_limits_and_heartbeat_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "campaign"
            root.mkdir()
            progress = {"status": "running", "active": {}, "counts": {}}
            (root / "progress.json").write_text(json.dumps(progress))
            (root / "plan.json").write_text(json.dumps({"jobTimeoutSeconds": 1800, "jobRssLimitBytes": 3072}))
            service = subprocess.CompletedProcess([], 0, stdout="ActiveState=active\nMainPID=7\n")
            with patch("sensitivity_health.sys.platform", "linux"), patch("sensitivity_health.subprocess.run", return_value=service):
                snapshot = collect(root, folder, "unit.service")
            self.assertEqual(snapshot["progress"], progress)
            self.assertEqual(snapshot["limits"]["timeoutSeconds"], 1800)
            self.assertIn("progressMtimeUtc", snapshot)
            self.assertFalse(snapshot["mountAvailable"])
            self.assertEqual(snapshot["collectionErrors"], [])
            self.assertEqual(json.loads((root / "progress.json").read_text()), progress)

    def test_collect_rejects_evidence_path_outside_mount(self):
        with patch("sensitivity_health.sys.platform", "linux"):
            with self.assertRaises(ValueError):
                collect(Path.cwd(), Path.cwd() / "other", "unit.service")

    def test_service_parse_keeps_reason_values(self):
        self.assertEqual(service_fields("Result=signal=TERM\nMainPID=7\n"), {"Result": "signal=TERM", "MainPID": "7"})


if __name__ == "__main__":
    unittest.main()
