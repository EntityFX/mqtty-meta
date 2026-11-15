import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import linux_web_host as host


class DeploymentSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.mount = self.base / "disk"
        self.mount.mkdir()
        self.root = self.mount / "web"

    def test_missing_mount_never_creates_system_disk_fallback(self):
        with patch.object(host.os.path, "ismount", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "absent"):
                host.check_storage(self.mount, self.root)
        self.assertFalse(self.root.exists())

    def test_path_escape_and_mount_root_are_rejected(self):
        with patch.object(host.os.path, "ismount", return_value=True):
            for path in (self.mount, self.base / "outside"):
                with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, "strictly inside"):
                    host.check_storage(self.mount, path)

    def test_space_reserve_blocks_deployment(self):
        with patch.object(host.os.path, "ismount", return_value=True), patch.object(
                host.shutil, "disk_usage", return_value=type("Usage", (), {"free": 12})()):
            with self.assertRaisesRegex(RuntimeError, "reserve"):
                host.check_storage(self.mount, self.root, reserve=13)

    def make_payload(self):
        payload = self.mount / "incoming"
        payload.mkdir()
        (payload / host.DLL).write_bytes(b"assembly")
        (payload / "wwwroot").mkdir()
        (payload / "wwwroot" / "index.html").write_text("page")
        manifest = self.mount / "manifest.json"
        host.atomic_json(manifest, {"mqttyCommit": "a" * 40, "files": host.payload_files(payload)})
        return payload, manifest

    def test_corrupt_payload_rejected_before_release_created(self):
        payload, manifest = self.make_payload()
        (payload / host.DLL).write_bytes(b"tampered")
        with patch.object(host, "check_storage", return_value=(self.mount, self.root)):
            with self.assertRaisesRegex(RuntimeError, "differ"):
                host.stage(payload, manifest, self.mount, self.root)
        self.assertFalse(self.root.exists())

    def test_release_pins_source_files_helper_and_never_overwrites(self):
        payload, manifest = self.make_payload()
        with patch.object(host, "check_storage", return_value=(self.mount, self.root)), patch.object(
                Path, "chmod"):
            release = host.stage(payload, manifest, self.mount, self.root)
            receipt = json.loads((release / "release.json").read_text())
            self.assertEqual(host.payload_files(release / "payload"), receipt["files"])
            self.assertEqual(host.sha256(release / "linux_web_host.py"), receipt["helperSha256"])
            self.assertEqual("a" * 40, receipt["mqttyCommit"])
            with self.assertRaises(FileExistsError):
                host.stage(payload, manifest, self.mount, self.root)

    @unittest.skipIf(os.name == "nt", "Windows symlink privileges vary")
    def test_published_symlink_cannot_escape_payload(self):
        payload, _ = self.make_payload()
        (payload / "escape").symlink_to(self.mount)
        with self.assertRaisesRegex(RuntimeError, "Symlinks"):
            host.payload_files(payload)

    @unittest.skipIf(os.name == "nt", "Windows symlink privileges vary")
    def test_existing_release_parent_symlink_cannot_redirect_install(self):
        payload, manifest = self.make_payload()
        self.root.mkdir()
        (self.root / "releases").symlink_to(self.base)
        with patch.object(host, "check_storage", return_value=(self.mount, self.root)):
            with self.assertRaisesRegex(RuntimeError, "escapes"):
                host.stage(payload, manifest, self.mount, self.root)

    def test_output_flood_is_bounded_and_latest_output_retained(self):
        path = self.mount / "log"
        logger = host.BoundedLog(path, max_bytes=17, backups=2)
        logger.write(b"a" * 1000 + b"tail")
        self.assertLessEqual(sum(p.stat().st_size for p in self.mount.glob("log*")), 51)
        retained = b"".join(p.read_bytes() for p in
                            (Path(str(path) + ".2"), Path(str(path) + ".1"), path) if p.exists())
        self.assertTrue(retained.endswith(b"tail"))

    def test_health_refuses_external_addresses(self):
        with patch.object(host.urllib.request, "urlopen") as opener:
            with self.assertRaises(ValueError):
                host.http_health("http://192.168.5.1:5088/healthz")
            opener.assert_not_called()

    def test_unit_rejects_systemd_specifier_injection(self):
        with self.assertRaises(ValueError):
            host.unit_text("/disk/%h", self.mount, self.root)


if __name__ == "__main__":
    unittest.main()
