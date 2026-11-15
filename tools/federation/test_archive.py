import hashlib
import tempfile
import unittest
from pathlib import Path

import archive


class FederationArchiveTests(unittest.TestCase):
    def test_verified_archive_rejects_wrong_members_without_removing_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "evidence"
            work.mkdir()
            source = work / "raw.json"
            source.write_bytes(b'{"observed":1}')
            target = root / "evidence.tar.gz"
            receipt = archive.create(work, target)
            self.assertTrue(receipt["allMembersVerified"])
            archive.verify(target, work.name, {"raw.json": hashlib.sha256(source.read_bytes()).hexdigest()})
            with self.assertRaises(ValueError):
                archive.verify(target, work.name, {"raw.json": "0" * 64})
            with self.assertRaises(ValueError):
                archive.create(work, target)
            self.assertTrue(source.exists())

    def test_corrupt_archive_does_not_touch_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "evidence"
            work.mkdir()
            source = work / "raw.json"
            source.write_bytes(b'important source bytes')
            bad = root / "bad.tar.gz"
            bad.write_bytes(b'not a gzip archive')
            with self.assertRaises(Exception):
                archive.verify(bad, work.name, {"raw.json": "0" * 64})
            self.assertEqual(source.read_bytes(), b'important source bytes')


if __name__ == "__main__":
    unittest.main()
