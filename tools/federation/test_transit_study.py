import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import study
import transit_study


class TransitEvidenceTests(unittest.TestCase):
    def fixture(self, root):
        study.write(root / "run-config.json", dict(mode="relay", topology="star", brokers=10, publishers=160, ideal=True))
        counters = dict(InboundPackets=10, OutboundPackets=10, TransferPackets=10, Inbound=100, Outbound=100, Refused=0)
        delta = dict(InboundPackets=1800, OutboundPackets=1800, TransferPackets=1800, Inbound=180000, Outbound=180000, Refused=0)
        evidence = dict(networkCount=11, brokerCount=10, name="transit", hasAttachedEndpoints=False,
            availablePaths=[dict(source=a, target=b, networks=[f"site{a}", "transit", f"site{b}"])
                            for a in range(10) for b in range(10) if a != b],
            beforeMeasurement=dict(tick=10, queuedPackets=0, counters=counters),
            final=dict(tick=100, queuedPackets=0, counters={k: v + delta[k] for k, v in counters.items()}),
            measurementAndDrainCounters=delta)
        study.write(root / "network-evidence.json", evidence)
        (root / "topology-physical.mmd").write_text("\n".join(f'S{i} <-->|"сеть ↔"| S10' for i in range(10)), encoding="utf-8")
        (root / "topology-logical.mmd").write_text("\n".join(f'S{a} -->|"источник {a}"| S{b}'
            for a in range(10) for b in range(10) if a != b), encoding="utf-8")
        return evidence

    @patch("transit_study.study.verify_run")
    def test_valid_transit_evidence_and_forged_counter_delta(self, raw_verifier):
        raw_verifier.return_value = dict(forwardAccepted=1800, apiAttempts=200, uniqueDeliveries=2000,
                                         forwardAttempts=1800, duplicateDeliveries=0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self.fixture(root)
            transit_study.verify_network(root)
            evidence["measurementAndDrainCounters"]["InboundPackets"] += 1
            study.write(root / "network-evidence.json", evidence)
            with self.assertRaises(AssertionError):
                transit_study.verify_network(root)

    @patch("transit_study.study.verify_run")
    def test_bypass_missing_direction_or_endpoint_on_transit_is_rejected(self, raw_verifier):
        raw_verifier.return_value = dict(forwardAccepted=1800, apiAttempts=200, uniqueDeliveries=2000,
                                         forwardAttempts=1800, duplicateDeliveries=0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = self.fixture(root)
            cases = []
            bypass = copy.deepcopy(original)
            bypass["availablePaths"][0]["networks"] = ["site0", "site1"]
            cases.append(bypass)
            missing = copy.deepcopy(original)
            missing["availablePaths"].pop()
            cases.append(missing)
            endpoint = copy.deepcopy(original)
            endpoint["hasAttachedEndpoints"] = True
            cases.append(endpoint)
            for evidence in cases:
                study.write(root / "network-evidence.json", evidence)
                with self.assertRaises(AssertionError):
                    transit_study.verify_network(root)


if __name__ == "__main__":
    unittest.main()
