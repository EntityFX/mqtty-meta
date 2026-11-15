import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from smoke_web import verify_seal


class SmokeVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "data" / "job"
        self.output.mkdir(parents=True)
        for file in ("summary.json", "manifest.json"):
            (self.output / file).write_text("{}")
        self.seals = {name: hashlib.sha256((self.output / name).read_bytes()).hexdigest()
                      for name in ("summary.json", "manifest.json")}
        self.write_seals()

    def write_seals(self):
        (self.output / "sha256.json").write_text(json.dumps(self.seals))

    def test_all_scientific_files_checked_against_content(self):
        result = verify_seal(self.output, self.root / "data")
        self.assertEqual(2, result["verifiedFiles"])
        (self.output / "summary.json").write_text('{"tampered": true}')
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            verify_seal(self.output, self.root / "data")

    def test_summary_must_be_covered_not_just_present(self):
        del self.seals["summary.json"]
        self.write_seals()
        with self.assertRaisesRegex(RuntimeError, "not covered"):
            verify_seal(self.output, self.root / "data")

    def test_seal_cannot_read_outside_job(self):
        self.seals["../escape.json"] = "0" * 64
        self.write_seals()
        with self.assertRaisesRegex(RuntimeError, "Unsafe"):
            verify_seal(self.output, self.root / "data")

    def test_server_output_path_must_be_inside_expected_data_root(self):
        with self.assertRaisesRegex(RuntimeError, "escapes"):
            verify_seal(self.output, self.output)


if __name__ == "__main__":
    unittest.main()
