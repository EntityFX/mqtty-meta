import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from native_audit import audit_archive, compare, quantiles


class NativeAuditTests(unittest.TestCase):
    def fixture(self, folder, mutate=None, raw_tamper=False, remove_seal=False):
        identifiers = [hashlib.sha256(json.dumps(["test-calibration", "Aedes.m16.q1.p1.r1.attempt-01", 0, n],
                        separators=(",", ":")).encode()).hexdigest()[:32] for n in range(4)]
        events = []
        for n, identifier in enumerate(identifiers):
            events.extend([{"event": "attempt", "id": identifier, "publisher": 0, "sequence": n,
                            "elapsedSeconds": 1 + n / 10, "payloadBytes": 16},
                           {"event": "completed", "id": identifier, "elapsedSeconds": 1.01 + n / 10,
                            "latencyMs": n + 1}])
        deliveries = [{"id": identifiers[n], "elapsedSeconds": t, "payloadBytes": 16}
                      for n, t in [(0, 1.05), (0, 1.06), (1, 1.2), (2, 3.2)]]
        deliveries.extend([{"id": "unknown", "elapsedSeconds": t, "payloadBytes": 16} for t in [1.4, 1.5]])
        measurement = {"attemptedPublishes": 4, "completedPublishes": 4, "failedPublishes": 0,
                       "uniqueDeliveries": 3, "duplicateDeliveries": 2, "unexpectedDeliveries": 2,
                       "deliveredAfterFailedPublish": 0, "completedIdsDelivered": 3,
                       "actualMeasurementSeconds": 2, "attemptedRps": 2, "completedRps": 2,
                       "publishFailureRate": 0, "deliveryLossRate": .25, "errorReasons": {},
                       "latencyStatus": "observed", "publishLatencyMs": {
                           "minMs": 1, "p50Ms": 2, "p75Ms": 3, "p95Ms": 4, "p99Ms": 4, "maxMs": 4}}
        result = {"identity": {"campaignId": "test-calibration"}, "key": {"broker": "Aedes", "qos": 1,
                  "publishers": 1, "messageBytes": 16, "repeat": 1, "key": "Aedes.m16.q1.p1.r1"},
                  "attempt": 1, "status": "success", "failure": None, "observation": {"measurement": measurement}}
        if mutate:
            mutate(result, events, deliveries)
        data = {"result.json": json.dumps(result).encode(),
                "publishes.ndjson": b"".join(json.dumps(e).encode() + b"\n" for e in events),
                "deliveries.ndjson": b"".join(json.dumps(e).encode() + b"\n" for e in deliveries),
                "load-generator-started.json": json.dumps({"timestampFrequency": 1000}).encode(),
                "load-generator-measurement-started.json": json.dumps({"tick": 1000, "utc": "2000-01-01T00:00:01+00:00"}).encode(),
                "load-generator-measurement-ended.json": json.dumps({"tick": 3000, "utc": "2000-01-01T00:00:03+00:00"}).encode()}
        seals = {name: hashlib.sha256(raw).hexdigest() for name, raw in data.items()}
        if remove_seal:
            del seals["deliveries.ndjson"]
        if raw_tamper:
            data["deliveries.ndjson"] = data["deliveries.ndjson"].replace(b'"payloadBytes": 16', b'"payloadBytes": 17', 1)
        path = Path(folder) / "evidence.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, raw in data.items():
                archive.writestr(name, raw)
            archive.writestr("sha256.json", json.dumps(seals))
        return path, hashlib.sha256(path.read_bytes()).hexdigest(), result

    def run_fixture(self, mutate=None):
        with tempfile.TemporaryDirectory() as folder:
            path, digest, result = self.fixture(folder, mutate)
            return audit_archive(path, digest), result

    def test_counts_all_quantiles_and_conservative_phase_bounds(self):
        value, _ = self.run_fixture()
        self.assertEqual({}, value["differences"])
        self.assertEqual({}, value["anomalies"])
        self.assertEqual(2, value["publishLatencyMs"]["p50Ms"])
        self.assertEqual(.25, value["rates"]["deliveryLossRate"])
        self.assertEqual(2, value["recounted"]["duplicateDeliveries"])
        self.assertEqual(2, value["recounted"]["unexpectedDeliveries"])
        self.assertEqual(1, value["phaseCoverage"]["uniqueDeliveriesDefinitelyAfterEnd"])
        self.assertEqual(2, value["phaseCoverage"]["uniqueDeliveriesDefinitelyBeforeEnd"])
        self.assertAlmostEqual(2000, value["endToEndFirstReceiptMs"]["p99Ms"])

    def test_wrong_outer_hash_and_raw_seal_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path, digest, _ = self.fixture(folder, raw_tamper=True)
            with self.assertRaisesRegex(ValueError, "Archive SHA"):
                audit_archive(path, "0" * 64)
            with self.assertRaisesRegex(ValueError, "Raw seal"):
                audit_archive(path, digest)

    def test_unsealed_member_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path, digest, _ = self.fixture(folder, remove_seal=True)
            with self.assertRaisesRegex(ValueError, "Seal set"):
                audit_archive(path, digest)

    def test_p50_is_checked_independently_of_correct_p99(self):
        value, _ = self.run_fixture(lambda r, e, d: r["observation"]["measurement"]["publishLatencyMs"].update(p50Ms=3))
        self.assertIn("publishLatencyMs.p50Ms", value["differences"])
        self.assertNotIn("publishLatencyMs.p99Ms", value["differences"])

    def test_mutated_duration_is_detected_by_clock_and_rates(self):
        value, _ = self.run_fixture(lambda r, e, d: r["observation"]["measurement"].update(actualMeasurementSeconds=4))
        self.assertIn("clock.actualMeasurementSeconds", value["differences"])
        self.assertIn("completedRps", value["differences"])

    def test_wrong_run_identity_is_detected(self):
        value, _ = self.run_fixture(lambda r, e, d: r["identity"].update(campaignId="different"))
        self.assertEqual(4, value["anomalies"]["identityHashMismatch"])

    def test_duplicate_outcome_is_not_counted_twice(self):
        value, _ = self.run_fixture(lambda r, e, d: e.append(dict(e[-1])))
        self.assertEqual(1, value["anomalies"]["duplicateOutcomes"])
        self.assertEqual(4, value["recounted"]["completedPublishes"])

    def test_unsettled_is_explicit(self):
        value, _ = self.run_fixture(lambda r, e, d: e.pop())
        self.assertEqual(1, value["unsettled"])
        self.assertIn("completedPublishes", value["differences"])
        self.assertIsNone(value["phaseCoverage"]["originalOutcomesDuringDrain"])

    def test_failure_delivered_and_error_reasons(self):
        def mutate(r, events, deliveries):
            events[1].update(event="failed", error="Disconnected")
        value, _ = self.run_fixture(mutate)
        self.assertEqual({"Disconnected": 1}, value["errorReasons"])
        self.assertEqual(1, value["recounted"]["deliveredAfterFailedPublish"])
        self.assertEqual(2, value["recounted"]["completedIdsDelivered"])
        self.assertAlmostEqual(1 / 3, value["rates"]["deliveryLossRate"])

    def test_qos0_cannot_gain_zero_ack_latency(self):
        def mutate(r, events, deliveries):
            r["key"]["qos"] = 0
            r["key"]["key"] = "Aedes.m16.q0.p1.r1"
            replacements = {events[n * 2]["id"]: hashlib.sha256(json.dumps(
                ["test-calibration", "Aedes.m16.q0.p1.r1.attempt-01", 0, n],
                separators=(",", ":")).encode()).hexdigest()[:32] for n in range(4)}
            for event in events + deliveries:
                event["id"] = replacements.get(event["id"], event["id"])
            for event in events:
                if event["event"] == "completed":
                    event["latencyMs"] = None
            r["observation"]["measurement"].update(publishLatencyMs=None, latencyStatus="notApplicable")
        value, _ = self.run_fixture(mutate)
        self.assertIsNone(value["publishLatencyMs"])
        self.assertEqual("notApplicable", value["latencyStatus"])
        self.assertEqual({}, value["differences"])

    def test_nonfinite_latency_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path, digest, _ = self.fixture(folder, lambda r, e, d: e[1].update(latencyMs=float("nan")))
            with self.assertRaisesRegex(ValueError, "finite"):
                audit_archive(path, digest)

    def test_wrong_payload_size_is_explicit(self):
        value, _ = self.run_fixture(lambda r, e, d: d[0].update(payloadBytes=256))
        self.assertEqual(1, value["anomalies"]["deliveryPayloadSizeMismatch"])

    def test_published_snapshot_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            path, digest, result = self.fixture(folder)
            expected = json.loads(json.dumps(result))
            expected["observation"]["measurement"]["completedPublishes"] = 100
            value = audit_archive(path, digest, expected)
            self.assertIn("published.completedPublishes", value["differences"])

    def test_nearest_rank_and_missing_summary_values(self):
        self.assertEqual(2, quantiles([4, 3, 2, 1])["p50Ms"])
        self.assertIsNone(quantiles([]))
        self.assertIn("attemptedPublishes", compare({"attemptedPublishes": 0}, {}))


if __name__ == "__main__":
    unittest.main()
