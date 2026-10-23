import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import warnings
from zipfile import ZipFile

from control_manifest_audit import BROKERS, EXPECTED_COMMIT, STAGES
from export_control_manifests import export_registry, export_to_file, inspect_archive


class ExportControlManifestsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.keys = {"m16.q1.p1.s42"}

    def make_archive(self, broker="Aedes", *, missing_config=False, duplicate=False, bad_layout=False):
        path = self.root / f"{broker}-model-evidence.zip"
        manifest = {"runId": "run-1", "createdAtUtc": "2026-01-01T00:00:00Z",
                    "runtime": ".NET 6.0.0", "provenance": {
                        "mqttYCommitSha": EXPECTED_COMMIT, "brokerProfileSha256": "a" * 64,
                        "referenceObservationsSha256": "b" * 64,
                        "brokerProfiles": [{"sha256": "a" * 64, "provenance": {"observationsSha256": "c" * 64}}]}}
        with ZipFile(path, "w") as bundle:
            for stage in STAGES:
                prefix = stage + "/m16.q1.p1.s42/"
                bundle.writestr(prefix + "experiment-manifest.json", json.dumps(manifest))
                if not missing_config:
                    bundle.writestr(prefix + "config.json", '{"test": true}')
            if duplicate:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    bundle.writestr("fidelity/m16.q1.p1.s42/config.json", "{}")
            if bad_layout:
                bundle.writestr("../escape.json", "{}")
        return {"name": path.name, "path": str(path),
                "expectedSha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_valid_archive_hashes_members_without_inventing_binary_identity(self):
        entry = self.make_archive()
        before = set(self.root.iterdir())
        archive, records = inspect_archive(entry, "Aedes", expected_keys=self.keys)
        self.assertEqual(archive["manifests"], 3)
        self.assertEqual(records[0]["configSha256"], hashlib.sha256(b'{"test": true}').hexdigest())
        self.assertEqual(records[0]["binaryIdentity"], {})
        self.assertEqual(set(self.root.iterdir()), before)

    def test_archive_hash_mismatch_rejected_before_member_use(self):
        entry = self.make_archive()
        entry["expectedSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            inspect_archive(entry, "Aedes", expected_keys=self.keys)

    def test_duplicate_missing_config_and_unsafe_layout_rejected(self):
        for variant in ("duplicate", "missing_config", "bad_layout"):
            with self.subTest(variant=variant):
                entry = self.make_archive(**{variant: True})
                with self.assertRaises(ValueError):
                    inspect_archive(entry, "Aedes", expected_keys=self.keys)

    def test_four_named_archives_required_and_registry_duplicates_rejected(self):
        entries = [self.make_archive(b) for b in BROKERS]
        result = export_registry({"compactArchives": entries}, expected_keys=self.keys)
        self.assertEqual(len(result["records"]), 12)
        for changed in (entries[1:], entries + [entries[0]]):
            with self.assertRaises(ValueError):
                export_registry({"compactArchives": changed}, expected_keys=self.keys)

    def test_full_production_matrix_required_by_default(self):
        entry = self.make_archive()
        with self.assertRaisesRegex(ValueError, "manifest matrix"):
            inspect_archive(entry, "Aedes")

    def test_existing_output_preserved_before_even_reading_registry(self):
        output = self.root / "existing.json"
        output.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            export_to_file(self.root / "missing-registry.json", output)
        self.assertEqual(output.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
