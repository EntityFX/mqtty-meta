"""The Linux preflight must not create outputs before checking its mount."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import network_runner_smoke as smoke


class SmokeStorageTests(unittest.TestCase):
    def test_linux_requires_explicit_mount_before_creating_any_files(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "new-study"
            with patch.object(smoke.sys, "platform", "linux"):
                with self.assertRaisesRegex(ValueError, "requires an actual dedicated"):
                    smoke.smoke(output)
            self.assertFalse(output.exists())

    def test_linux_rejects_unmounted_directory_before_creating_any_files(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "new-study"
            with patch.object(smoke.sys, "platform", "linux"), patch.object(smoke.Path, "is_mount", return_value=False):
                with self.assertRaisesRegex(ValueError, "dedicated mounted"):
                    smoke.smoke(output, folder)
            self.assertFalse(output.exists())

    def test_existing_result_is_never_reused_as_a_new_smoke(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "existing-data"
            marker.write_bytes(b"keep")
            with self.assertRaisesRegex(ValueError, "already exists"):
                smoke.smoke(folder)
            self.assertEqual(marker.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
