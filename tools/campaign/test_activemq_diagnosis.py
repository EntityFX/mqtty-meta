import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


spec = importlib.util.spec_from_file_location("diagnosis", Path(__file__).with_name("diagnose-activemq.py"))
diagnosis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnosis)


class ActiveMQDiagnosisTests(unittest.TestCase):
    def test_jvm_exit_flag_is_not_an_out_of_memory_event(self):
        lines = ["JVM args: -XX:+ExitOnOutOfMemoryError", "java.lang.OutOfMemoryError: Java heap space"]
        self.assertEqual([lines[1]], diagnosis.warning_lines(lines))

    def fixture(self):
        events = [
            dict(event="attempt", id="ok", publisher=0, elapsedSeconds=1),
            dict(event="completed", id="ok", elapsedSeconds=1.1),
            dict(event="attempt", id="broken", publisher=1, elapsedSeconds=1),
            dict(event="failed", id="broken", error="Broken pipe", elapsedSeconds=2),
            dict(event="attempt", id="offline1", publisher=1, elapsedSeconds=2.1),
            dict(event="failed", id="offline1", error="The client is not connected.", elapsedSeconds=2.10001),
            dict(event="attempt", id="offline2", publisher=1, elapsedSeconds=2.2),
            dict(event="failed", id="offline2", error="The client is not connected.", elapsedSeconds=2.20001),
        ]
        measurement = dict(attemptedPublishes=4, completedPublishes=1, failedPublishes=3,
                           uniqueDeliveries=1, completedIdsDelivered=1, duplicateDeliveries=0,
                           deliveredAfterFailedPublish=0,
                           errorReasons={"Broken pipe": 1, "The client is not connected.": 2})
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("publishes.ndjson", "\n".join(map(json.dumps, events)))
            archive.writestr("deliveries.ndjson", json.dumps(dict(id="ok", elapsedSeconds=1.2)))
            archive.writestr("result.json", json.dumps(dict(observation=dict(measurement=measurement))))
        stream.seek(0)
        return stream

    def test_disconnection_calls_are_not_reported_as_distinct_network_disconnects(self):
        with zipfile.ZipFile(self.fixture()) as archive:
            result = diagnosis.audit_events(archive)
        self.assertEqual({}, result["differences"])
        self.assertEqual(2, result["disconnectedFailures"])
        self.assertEqual(1, result["repeatedDisconnectedFailures"])
        self.assertEqual(1, result["affectedPublishers"])
        self.assertEqual(0, result["unsettled"])
        self.assertAlmostEqual(.00001, result["failedCallDurationSeconds"]["The client is not connected."]["mean"])

    def test_archive_hash_is_verified_before_any_recount(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.zip"
            path.write_bytes(self.fixture().getvalue())
            with self.assertRaisesRegex(ValueError, "Archive SHA mismatch"):
                diagnosis.inspect_archive(path, "0" * 64, True)

    def test_evidence_mapping_rejects_escape_and_foreign_root(self):
        self.assertEqual(Path("target") / "series/a.zip", diagnosis.mapped("/evidence/series/a.zip", "/evidence", Path("target")))
        for value in ("/foreign/a.zip", "/evidence/../a.zip"):
            with self.assertRaises(ValueError):
                diagnosis.mapped(value, "/evidence", Path("target"))


if __name__ == "__main__":
    unittest.main()
