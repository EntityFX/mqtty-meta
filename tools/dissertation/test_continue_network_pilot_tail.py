import copy
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import continue_network_pilot_tail as continuation
from continue_network_pilot_tail import select_tail, summary


class ContinueNetworkPilotTailTests(unittest.TestCase):
    def fixture(self):
        plan = {"jobs": [{"id": f"pilot.{i}", "kind": "pilot"} for i in range(30)] +
                        [{"id": "matrix.0", "kind": "matrix"}]}
        rows = {f"pilot.{i}": {"id": f"pilot.{i}", "kind": "pilot", "technicalError": None,
                                "returnCode": 0} for i in range(24)}
        rows["pilot.23"].update(technicalError="RuntimeError: Federation CLI exit code 2", returnCode=2)
        review = {"decision": "continue-unattempted-pilots-only", "executionManifestSha256": "manifest",
                  "failedJobId": "pilot.23", "failedAttemptSha256": "receipt", "failedSummarySha256": "summary",
                  "reviewer": "inspected operator", "reason": "Retain failure, inspect missing pilot regimes"}
        failed = {"status": "failed", "error": "System.TimeoutException: drain timed out; partial evidence retained.",
                  "phases": [{"name": "measure", "status": "completed"}, {"name": "drain", "status": "failed"}]}
        return plan, rows, review, failed

    def select(self, plan, rows, review, failed):
        return select_tail(plan, rows, review, "manifest", "receipt", "summary", failed)

    def test_only_six_unattempted_selected_old_failure_unchanged(self):
        plan, rows, review, failed = self.fixture()
        before = copy.deepcopy(rows)
        tail = self.select(plan, rows, review, failed)
        self.assertEqual([j["id"] for j in tail], [f"pilot.{i}" for i in range(24, 30)])
        self.assertEqual(rows, before)
        for job in tail:
            rows[job["id"]] = dict(job, technicalError=None)
        result = summary(plan, rows, "manifest", "review")
        self.assertEqual((result["coverage"], result["success"], result["failure"]), (30, 29, 1))
        self.assertFalse(result["pilotAccepted"])
        self.assertFalse(result["matrixAdmitted"])

    def test_review_binding_and_operator_required(self):
        plan, rows, review, failed = self.fixture()
        for field in review:
            changed = dict(review, **{field: ""})
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.select(plan, rows, changed, failed)

    def test_other_failure_or_changed_return_code_is_rejected(self):
        plan, rows, review, failed = self.fixture()
        rows["pilot.1"]["technicalError"] = "other failure"
        with self.assertRaises(ValueError):
            self.select(plan, rows, review, failed)
        rows["pilot.1"]["technicalError"] = None
        rows["pilot.23"]["returnCode"] = -9
        with self.assertRaises(ValueError):
            self.select(plan, rows, review, failed)

    def test_non_drain_failure_and_failed_measurement_are_rejected(self):
        plan, rows, review, failed = self.fixture()
        for change in (dict(failed, error="OutOfMemoryException"),
                       dict(failed, status="completed"),
                       dict(failed, phases=[{"name": "measure", "status": "failed"},
                                            {"name": "drain", "status": "failed"}])):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.select(plan, rows, review, change)

    def test_empty_or_wrong_cohort_never_becomes_accepted(self):
        plan, rows, review, failed = self.fixture()
        for changed in ({"jobs": []}, {"jobs": plan["jobs"][:-2]}):
            with self.assertRaises(ValueError):
                self.select(changed, rows, review, failed)
        empty = summary({"jobs": []}, {}, "manifest", "review")
        self.assertFalse(empty["pilotAccepted"])
        self.assertFalse(empty["matrixAdmitted"])

    def test_matrix_attempts_cannot_be_reused_or_launched(self):
        plan, rows, review, failed = self.fixture()
        rows["matrix.0"] = {"id": "matrix.0", "kind": "matrix", "technicalError": None}
        with self.assertRaises(ValueError):
            self.select(plan, rows, review, failed)

    def execute_stub(self, fail_first=False):
        """Exercise the real continuation loop using a saved-runner test double."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        mount = Path(temporary.name)
        root = mount / "study"
        root.mkdir()
        plan, rows, review, failed_summary = self.fixture()
        for job in plan["jobs"]:
            job["config"] = "configs/" + job["id"] + ".json"
        digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
        def save(path, value):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")
        for row in rows.values():
            row["attempt"] = f"runs/{row['id']}/attempt-001"
            save(root / row["attempt"] / "result.json", row)
        failed_path = root / rows["pilot.23"]["attempt"]
        save(failed_path / "output/summary.json", failed_summary)
        (root / "execution-manifest.json").write_text("frozen manifest")
        frozen_code = root / "tools/dissertation/run_network_study.py"
        frozen_code.parent.mkdir(parents=True)
        frozen_code.write_text("# frozen test runner")
        manifest_sha = digest(root / "execution-manifest.json")
        review.update(executionManifestSha256=manifest_sha,
                      failedAttemptSha256=digest(failed_path / "result.json"),
                      failedSummarySha256=digest(failed_path / "output/summary.json"))
        review_path = mount / "review.json"
        save(review_path, review)
        attempted, published = [], []
        class Guard:
            def __init__(self, mount_path, folder, reserve):
                self.mount, self.root, self.device = Path(mount_path), Path(folder), 7
            def check(self):
                pass
            def save(self, path, value):
                save(path, value)
        def attempt(root_path, job, manifest, manifest_sha, guard, on_state):
            attempted.append(job["id"])
            return {"id": job["id"], "kind": "pilot",
                    "technicalError": "new error" if fail_first else None}
        fake = SimpleNamespace(
            __file__=str(frozen_code), sha=digest, StorageGuard=Guard,
            verify_snapshot=lambda root: {"mountRoot": str(mount), "reserveBytes": 0,
                "mountDevice": 7, "afterUnits": [],
                "files": {"tools/dissertation/run_network_study.py": digest(frozen_code)}},
            program_check=lambda _: plan, isolation=lambda _: None,
            lock=lambda _: nullcontext(), load_rows=lambda *args: copy.deepcopy(rows),
            contained=lambda base, rel: base / rel,
            verify_output=lambda *args: (failed_summary, ["deliveryLoss"]),
            progress_context=lambda *args: {"attempts": {"pilot": {"total": 24, "failed": 1}}},
            attempt=attempt, publish=lambda *args, **kwargs: published.append((args, kwargs)))
        output = mount / "new-operation"
        with patch.object(continuation, "load_frozen_runner", return_value=fake):
            if fail_first:
                with self.assertRaisesRegex(RuntimeError, "New technical failure"):
                    continuation.run(root, review_path, output)
            else:
                continuation.run(root, review_path, output)
        self.assertEqual(digest(failed_path / "result.json"), review["failedAttemptSha256"])
        self.assertEqual(digest(failed_path / "output/summary.json"), review["failedSummarySha256"])
        self.assertFalse((root / "results-pilot.json").exists())
        self.assertTrue(all(entry[0][4] == "partial" for entry in published))
        return attempted, json.loads((output / "tail-summary.json").read_text())

    def test_real_loop_only_runs_six_missing_and_never_publishes_acceptance(self):
        attempted, result = self.execute_stub()
        self.assertEqual(attempted, [f"pilot.{i}" for i in range(24, 30)])
        self.assertEqual((result["coverage"], result["success"], result["failure"]), (30, 29, 1))
        self.assertFalse(result["pilotAccepted"])
        self.assertFalse(result["matrixAdmitted"])

    def test_real_loop_stops_at_first_new_technical_failure(self):
        attempted, result = self.execute_stub(fail_first=True)
        self.assertEqual(attempted, ["pilot.24"])
        self.assertEqual((result["coverage"], result["success"], result["failure"]), (25, 23, 2))
        self.assertFalse(result["matrixAdmitted"])


if __name__ == "__main__":
    unittest.main()
