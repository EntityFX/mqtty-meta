import copy
import unittest

from control_manifest_audit import BROKERS, STAGES, EXPECTED_COMMIT, audit


class ControlManifestAuditTests(unittest.TestCase):
    def fixture(self):
        rows, model, metadata = [], {}, {}
        for broker in BROKERS:
            model[broker] = []
            metadata[broker] = {"calibrationObservationsSha256": "a" * 64,
                                "validationObservationsSha256": "b" * 64}
            for stage in STAGES:
                model[broker].append({"stage": stage, "key": "m16.q1.p1.s42",
                    "configSha256": "c" * 64, "directory": "/raw/run1",
                    "profileHashes": ["d" * 64],
                    "filesSha256": {"experiment-manifest.json": "e" * 64}})
                rows.append({"broker": broker, "stage": stage, "key": "m16.q1.p1.s42",
                    "configSha256": "c" * 64, "manifestSha256": "e" * 64,
                    "runId": "run1", "sourceCommitSha": EXPECTED_COMMIT,
                    "profileSha256": "d" * 64, "archiveSha256": "f" * 64,
                    "archivePath": "/evidence/original.zip", "createdAtUtc": "2026-01-01T00:00:00Z",
                    "referenceObservationsSha256": "b" * 64 if stage == "holdout" else "a" * 64,
                    "profileCalibrationObservationsSha256": "a" * 64, "binaryIdentity": None})
        return rows, model, metadata

    def run_audit(self, rows, model, metadata):
        return audit(rows, model, expected_per_stage=1, profile_metadata=metadata)

    def test_consistent_metadata_is_not_binary_or_blindness_proof(self):
        rows, model, metadata = self.fixture()
        result = self.run_audit(rows, model, metadata)
        self.assertEqual(result["status"], "metadataConsistent")
        self.assertEqual(result["binaryIdentityMissingCount"], 12)
        self.assertFalse(result["binaryBuildOrExecutionVerified"])
        self.assertFalse(result["archiveBytesVerified"])
        self.assertFalse(result["blindedValidationEstablished"])

    def test_each_identity_and_provenance_corruption_is_rejected(self):
        original, model, metadata = self.fixture()
        for field in ("configSha256", "manifestSha256", "profileSha256", "runId",
                      "sourceCommitSha", "referenceObservationsSha256",
                      "profileCalibrationObservationsSha256", "archiveSha256"):
            rows = copy.deepcopy(original)
            rows[0][field] = "corrupted"
            with self.subTest(field=field):
                self.assertEqual(self.run_audit(rows, model, metadata)["status"], "metadataMismatch")

    def test_duplicate_missing_and_foreign_records_are_rejected(self):
        rows, model, metadata = self.fixture()
        duplicate = rows + [rows[0]]
        missing = rows[1:]
        foreign = copy.deepcopy(rows)
        foreign[0]["broker"] = "Unknown"
        for changed in (duplicate, missing, foreign):
            with self.subTest(length=len(changed)):
                self.assertEqual(self.run_audit(changed, model, metadata)["status"], "metadataMismatch")

    def test_published_duplicate_is_rejected(self):
        rows, model, metadata = self.fixture()
        model["Aedes"].append(model["Aedes"][0])
        self.assertEqual(self.run_audit(rows, model, metadata)["status"], "metadataMismatch")

    def test_binary_field_is_reported_without_execution_claim(self):
        rows, model, metadata = self.fixture()
        rows[0]["binaryIdentity"] = {"applicationSha256": "1" * 64}
        result = self.run_audit(rows, model, metadata)
        self.assertEqual(len(result["binaryIdentityRecords"]), 1)
        self.assertEqual(result["binaryIdentityMissingCount"], 11)
        self.assertFalse(result["binaryBuildOrExecutionVerified"])

    def test_container_shapes_supported(self):
        rows, model, metadata = self.fixture()
        for export in ({"records": rows}, {"rows": rows}):
            result = audit(export, model, expected_per_stage=1, profile_metadata=metadata)
            self.assertEqual(result["status"], "metadataConsistent")

    def test_hash_case_and_implicit_fidelity_reference_supported(self):
        rows, model, metadata = self.fixture()
        rows[0]["profileSha256"] = rows[0]["profileSha256"].upper()
        rows[0]["referenceObservationsSha256"] = None
        self.assertEqual(self.run_audit(rows, model, metadata)["status"], "metadataConsistent")
        rows[1]["referenceObservationsSha256"] = None
        self.assertEqual(self.run_audit(rows, model, metadata)["status"], "metadataMismatch")

    def test_export_archive_catalog_cannot_disagree_with_record(self):
        rows, model, metadata = self.fixture()
        export = {"records": rows, "archives": [{"path": "/evidence/original.zip", "sha256": "0" * 64}]}
        self.assertEqual(audit(export, model, expected_per_stage=1,
                               profile_metadata=metadata)["status"], "metadataMismatch")


if __name__ == "__main__":
    unittest.main()
