"""Tests for installing and verifying the desktop UI bundle."""

import json
import tempfile
import unittest
from pathlib import Path

from wosutil.ui.bundle import sha256_bytes
from wosutil.ui.install import (
    InstallError,
    install_from_directory,
    install_from_manifest,
    installed_manifest_sha256,
    verify_installed,
)


class InstallTests(unittest.TestCase):
    """Cover both the local and the pinned-manifest install paths."""

    def setUp(self):
        """Create a source bundle with a manifest."""
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)
        self.source = self.base / "dist-ui"
        (self.source / "_astro").mkdir(parents=True)
        (self.source / "index.html").write_text("<html>app</html>", encoding="utf-8")
        (self.source / "_astro" / "app.css").write_text("body{}", encoding="utf-8")
        self.files = {
            "index.html": sha256_bytes((self.source / "index.html").read_bytes()),
            "_astro/app.css": sha256_bytes((self.source / "_astro" / "app.css").read_bytes()),
        }
        manifest = {"apiVersion": 1, "uiVersion": "test-1", "entry": "index.html", "files": self.files}
        self.manifest_bytes = (json.dumps(manifest) + "\n").encode()
        (self.source / "manifest.json").write_bytes(self.manifest_bytes)
        self.out = self.base / "installed"

    def tearDown(self):
        """Remove the temporary tree."""
        self._temp.cleanup()

    def test_install_from_directory_writes_and_verifies(self):
        """A local build is copied with a marker that detects tampering."""
        marker = install_from_directory(self.source, self.out)
        self.assertEqual(marker["uiVersion"], "test-1")
        self.assertTrue((self.out / "index.html").is_file())
        self.assertTrue((self.out / "_astro" / "app.css").is_file())
        self.assertEqual(verify_installed(self.out)["uiVersion"], "test-1")

        (self.out / "_astro" / "app.css").write_text("changed", encoding="utf-8")
        with self.assertRaises(InstallError):
            verify_installed(self.out)

    def test_install_from_manifest_verifies_the_pin_and_files(self):
        """The pinned manifest hash is enforced before installing."""
        manifest_url = (self.source / "manifest.json").as_uri()
        expected = sha256_bytes(self.manifest_bytes)

        marker = install_from_manifest(manifest_url, expected, self.out)
        self.assertEqual(marker["manifestSha256"], expected)
        self.assertEqual(installed_manifest_sha256(self.out), expected)
        self.assertEqual(verify_installed(self.out)["files"]["index.html"], self.files["index.html"])

    def test_install_from_manifest_rejects_a_changed_manifest(self):
        """A manifest that no longer matches the pin is refused."""
        manifest_url = (self.source / "manifest.json").as_uri()
        with self.assertRaises(InstallError):
            install_from_manifest(manifest_url, "0" * 64, self.out)
        self.assertFalse(self.out.exists())

    def test_manifest_with_an_unsafe_path_is_rejected(self):
        """A manifest cannot write outside the installed directory."""
        manifest = {"apiVersion": 1, "uiVersion": "bad", "entry": "index.html", "files": {"../evil.txt": "0" * 64}}
        raw = json.dumps(manifest).encode()
        (self.source / "manifest.json").write_bytes(raw)
        with self.assertRaises(InstallError):
            install_from_manifest((self.source / "manifest.json").as_uri(), sha256_bytes(raw), self.out)
