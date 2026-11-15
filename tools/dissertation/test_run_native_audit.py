import copy
import itertools
from pathlib import Path
import tempfile
import unittest

from run_native_audit import counts, jobs_from_inputs, validate_published_matrix, verify_storage


class BatchAuditTests(unittest.TestCase):
    def test_duplicate_registry_and_uncovered_publication_rejected(self):
        row = {"archivePath": "/evidence/a.zip", "sha256": "a" * 64}
        native = {"runs": [{"archive": row}]}
        with self.assertRaisesRegex(ValueError, "Duplicate registry"):
            jobs_from_inputs([row, row], native)
        with self.assertRaisesRegex(ValueError, "missing from registry"):
            jobs_from_inputs([], native)

    def test_registry_and_publication_hashes_must_agree(self):
        row = {"archivePath": "/evidence/a.zip", "sha256": "a" * 64}
        other = dict(row, sha256="b" * 64)
        with self.assertRaisesRegex(ValueError, "disagree"):
            jobs_from_inputs([row], {"runs": [{"archive": other}]})

    def test_technical_failure_and_nonselected_attempt_are_separate(self):
        results = [{"error": {"type": "OSError"}},
                   {"error": None, "selectedPublishedRun": False, "scientific": {"status": "failed"}},
                   {"error": None, "selectedPublishedRun": True,
                    "scientific": {"differences": {}, "anomalies": {}, "unsettled": 0}},
                   {"error": None, "selectedPublishedRun": True,
                    "scientific": {"differences": {"p50": {}}, "anomalies": {}, "unsettled": 0}}]
        value = counts(results, 5)
        self.assertEqual(4, value["processed"])
        self.assertEqual(1, value["technicalErrors"])
        self.assertEqual(1, value["nonSuccessArchives"])
        self.assertEqual(1, value["selectedPassed"])
        self.assertEqual(1, value["selectedDiscrepancies"])

    def test_complete_matrix_accepted_but_duplicate_missing_and_pilot_rejected(self):
        runs = [{"broker": b, "stage": s, "key": {"messageBytes": m, "qos": q, "publishers": p, "repeat": r}}
                for b, s, m, q, p, r in itertools.product(("Aedes", "Mosquitto", "ActiveMQ", "EMQX"),
                    ("calibration", "validation"), (16, 256), (0, 1, 2), (1, 16, 64, 128), (1, 2, 3))]
        validate_published_matrix({"runs": runs})
        for invalid in [runs[:-1], runs[:1], runs[:-1] + [copy.deepcopy(runs[0])]]:
            with self.assertRaisesRegex(ValueError, "576-run matrix"):
                validate_published_matrix({"runs": invalid})

    def test_nonmounted_storage_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "output"
            with self.assertRaisesRegex(ValueError, "mounted evidence disk"):
                verify_storage(Path(folder), output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
