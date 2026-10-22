import base64
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

import study


class FederationEvidenceTests(unittest.TestCase):
    def fixture(self, root):
        payload = struct.pack("<qq", 1, 10)
        digest = hashlib.sha256(payload).hexdigest()
        summary = dict(apiAttempts=1, sendRejected=0, scheduledPublishes=1, backpressureUnsent=0,
            forwardAttempts=0, forwardAccepted=0, forwardRejected=0, forwardTopicPayloadBytes=0,
            expectedDeliveries=1, uniqueDeliveries=1, uniqueBeforeDrain=0, uniqueDuringDrain=1,
            duplicateDeliveries=0, fullyDeliveredPublications=1, receiverHistogramTicks={"5": 1},
            sourceCompleted=1, sourceCompletedBeforeDrain=0, sourceCompletedDuringDrain=1,
            publishHistogramTicks={"2": 1}, tickMilliseconds=.1, deliveryLoss=0, status="completed",
            sourceCompletedRps=100, uniqueGoodput=100, phases=[dict(name="drain", startTick=14)],
            sources=[dict(metrics=dict(attempted=1, admitted=1, completed=1, publishFailed=0,
                rateRejected=0, latencyHistogramTicks={"2": 1}))],
            receivers=[dict(site=0, uniqueDeliveries=1, duplicates=0, latencyHistogramTicks={"5": 1})])
        summary.update({f"publishP{q}Ms": .2 for q in (50, 95, 99)})
        summary.update({f"receiverP{q}Ms": .5 for q in (50, 95, 99)})
        files = {
            "summary.json": summary,
            "run-config.json": dict(qos=1, payloadBytes=16, measurementSeconds=.01),
            "publications.json": [dict(id=1, origin=0, sequence=1, tick=10, measurement=True,
                payload=base64.b64encode(payload).decode())],
            "transmissions.json": [dict(publicationId=1, measurement=True, payloadSha256=digest,
                payloadBytes=16, topic="federation/delivered/0", topicPayloadBytes=38,
                role="source", accepted=True)],
            "delivery-receipts.json": [dict(receiver=0, publicationId=1, topic="federation/delivered/0",
                tick=15, payloadSha256=digest)]}
        files["transmissions.json"][0]["topicPayloadBytes"] = len("federation/delivered/0".encode()) + 16
        for name, value in files.items():
            study.write(root / name, value)
        self.seal(root)
        return files

    def seal(self, root):
        study.write(root / "sha256.json", {p.name: study.sha(p) for p in root.iterdir() if p.name != "sha256.json"})

    def test_raw_recalculation_rejects_resealed_wrong_drain_or_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = self.fixture(root)
            study.verify_run(root)
            files["summary.json"]["uniqueDuringDrain"] = 0
            study.write(root / "summary.json", files["summary.json"])
            self.seal(root)
            with self.assertRaises(AssertionError):
                study.verify_run(root)
            self.fixture(root)
            tx = study.read(root / "transmissions.json")
            tx[0]["payloadSha256"] = "0" * 64
            study.write(root / "transmissions.json", tx)
            self.seal(root)
            with self.assertRaises(AssertionError):
                study.verify_run(root)

    def test_seal_rejects_changed_bytes_and_escaping_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            (root / "summary.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                study.seal(root)
            study.write(root / "sha256.json", {"../outside.json": "0" * 64})
            with self.assertRaises(ValueError):
                study.seal(root)


if __name__ == "__main__":
    unittest.main()
