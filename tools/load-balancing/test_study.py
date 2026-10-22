import copy
import json
import math
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import study
import topologies


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
        fixture = Path(__file__).with_name("fixtures") / "ideal-qos1-summary.json"
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

    def test_topology_check_rejects_wrong_receiver_segment_and_extra_network_link(self):
        namespace = "{http://graphml.graphdrawing.org/xmlns}"
        root = ET.Element(namespace + "graphml")
        graph = ET.SubElement(root, namespace + "graph")
        for identity, label, kind in (("n0", "clients", "Network"), ("n1", "backend0-hop1", "Network"),
                                      ("n2", "backend0-hop2", "Network"), ("p", "publisher0000", "Client"),
                                      ("r", "receiver0000", "Client"), ("b", "broker0000", "Server")):
            node = ET.SubElement(graph, namespace + "node", id=identity)
            ET.SubElement(node, namespace + "data", key="label").text = label
            ET.SubElement(node, namespace + "data", key="type").text = kind
        for source, target in (("n0", "n1"), ("n1", "n2"), ("p", "n0"), ("b", "n2")):
            ET.SubElement(graph, namespace + "edge", source=source, target=target)
        receiver_edge = ET.SubElement(graph, namespace + "edge", source="r", target="n0")
        options = {"backendCount": 1, "networkLength": 2, "totalPublishers": 1}
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "topology.graphml"
            ET.ElementTree(root).write(file, encoding="utf-8", xml_declaration=True)
            self.assertTrue(topologies.verify_graph(file, options)["allClientAttachmentsVerified"])
            receiver_edge.set("target", "n2")
            ET.ElementTree(root).write(file, encoding="utf-8", xml_declaration=True)
            with self.assertRaises(ValueError):
                topologies.verify_graph(file, options)
            receiver_edge.set("target", "n0")
            ET.SubElement(graph, namespace + "edge", source="n0", target="n2")
            ET.ElementTree(root).write(file, encoding="utf-8", xml_declaration=True)
            with self.assertRaises(ValueError):
                topologies.verify_graph(file, options)


if __name__ == "__main__":
    unittest.main()
