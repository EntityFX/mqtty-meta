"""Known-answer checks for the future-trial cohort recount; no brokers launched."""
import unittest
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from audit_twin_cohort import recount


class CohortTests(unittest.TestCase):
    def phases(self, qos=1):
        return dict(measurementStartSeconds=1, measurementEndSeconds=3,
                    drainEndSeconds=4, timeOrigin="sharedMonotonic", qos=qos)

    def attempt(self, identifier, time):
        return dict(event="attempt", id=identifier, elapsedSeconds=time)

    def outcome(self, identifier, time, kind="completed", latency=10):
        return dict(event=kind, id=identifier, elapsedSeconds=time, latencyMs=latency)

    def test_exact_cohort_boundaries_drain_failed_delivery_and_pending(self):
        logs = [self.attempt("warmup", .5), self.outcome("warmup", .8),
                self.attempt("a", 1), self.outcome("a", 2),
                self.attempt("b", 2), self.outcome("b", 3.5),
                self.attempt("c", 2.1), self.outcome("c", 2.2, "failed"),
                self.attempt("pending", 2.9),
                self.attempt("outside", 3), self.outcome("outside", 3.1)]
        arrivals = [dict(id=k, elapsedSeconds=t) for k, t in
                    [("warmup", 1.1), ("a", 2.1), ("a", 2.2), ("b", 4), ("c", 2.5), ("outside", 3.2)]]
        result = recount(logs, arrivals, self.phases())
        self.assertEqual((4, 2, 1, 1), tuple(result[k] for k in ("attempted", "completed", "failed", "pending")))
        self.assertEqual(.5, result["completedRpsInWindow"])
        self.assertEqual(1, result["completedCohortPerSubmissionSecond"])
        self.assertEqual(1, result["completedDuringDrain"])
        self.assertEqual(1, result["firstDeliveriesDuringDrain"])
        self.assertEqual(1, result["deliveredAfterFailedPublish"])
        self.assertEqual(1, result["duplicates"])
        self.assertEqual(2, result["outsideCohortDeliveryEvents"])
        self.assertEqual(0, result["deliveryLossRate"])
        self.assertEqual(.25, result["publishFailureRate"])
        self.assertFalse(result["outcomesSettled"])

    def test_missing_denominators_are_undefined(self):
        result = recount([], [], self.phases())
        self.assertIsNone(result["deliveryLossRate"])
        self.assertIsNone(result["publishFailureRate"])
        self.assertIsNone(result["p99PublishMs"])

    def test_qos_zero_does_not_invent_ack_latency(self):
        result = recount([self.attempt("a", 1), self.outcome("a", 2)], [], self.phases(0))
        self.assertIsNone(result["p99PublishMs"])
        self.assertEqual("notApplicable", result["latencyStatus"])
        self.assertEqual(1, result["deliveryLossRate"])

    def test_upper_rank_quantile_is_independent_known_answer(self):
        logs = []
        for index in range(100):
            logs += [self.attempt(str(index), 1), self.outcome(str(index), 2, latency=index + 1)]
        self.assertEqual(99, recount(logs, [], self.phases())["p99PublishMs"])

    def test_duplicate_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            recount([self.attempt("a", 1), self.attempt("a", 2)], [], self.phases())

    def test_invalid_origin_or_boundary_is_rejected(self):
        for update in ({"timeOrigin": "estimated"}, {"measurementEndSeconds": 1}, {"qos": True}):
            with self.assertRaises(ValueError):
                recount([], [], self.phases() | update)

    def test_outcome_outside_drain_stays_pending(self):
        result = recount([self.attempt("a", 1), self.outcome("a", 4.1)], [], self.phases())
        self.assertEqual(1, result["pending"])
        self.assertEqual(0, result["completed"])

    def test_inconsistent_outcome_is_rejected(self):
        for logs in ([self.outcome("a", 2)],
                     [self.attempt("a", 2), self.outcome("a", 1)],
                     [self.attempt("a", 1), self.outcome("a", 2), self.outcome("a", 3, "failed")]):
            with self.assertRaises(ValueError):
                recount(logs, [], self.phases())

    def test_cli_records_hashes_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "publishes.ndjson").write_text("\n".join(json.dumps(row) for row in
                [self.attempt("a", 1), self.outcome("a", 2)]), encoding="utf-8")
            (root / "deliveries.ndjson").write_text(json.dumps(dict(id="a", elapsedSeconds=2.1)), encoding="utf-8")
            (root / "phases.json").write_text(json.dumps(self.phases()), encoding="utf-8")
            command = [sys.executable, str(Path(__file__).with_name("audit_twin_cohort.py")),
                       "--publishes", str(root / "publishes.ndjson"), "--deliveries", str(root / "deliveries.ndjson"),
                       "--phases", str(root / "phases.json"), "--output", str(root / "result.json")]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, first.returncode, first.stderr)
            saved = (root / "result.json").read_bytes()
            result = json.loads(saved)
            self.assertEqual(1, result["completedIdsDelivered"])
            self.assertEqual({"publishes", "deliveries", "phases"}, result["inputsSha256"].keys())
            self.assertTrue(all(len(value) == 64 for value in result["inputsSha256"].values()))
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(0, second.returncode)
            self.assertEqual(saved, (root / "result.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
