"""Tests for UI bundle discovery and path safety."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wosutil.ui.bundle import load_ui_bundle, resolve_ui_bundle, safe_join
from wosutil.ui.contract import API_VERSION


class SafeJoinTests(unittest.TestCase):
    """A request path can never escape the bundle directory."""

    def setUp(self):
        """Create a bundle and a secret file next to it."""
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "bundle"
        (self.root / "_astro").mkdir(parents=True)
        (self.root / "index.html").write_text("<html></html>", encoding="utf-8")
        (self.root / "_astro" / "app.css").write_text("body{}", encoding="utf-8")
        (Path(self._temp.name) / "secret.txt").write_text("secret", encoding="utf-8")

    def tearDown(self):
        """Remove the temporary tree."""
        self._temp.cleanup()

    def test_serves_files_inside_the_bundle(self):
        """Normal assets resolve to their file."""
        self.assertEqual(safe_join(self.root, "/_astro/app.css"), (self.root / "_astro" / "app.css").resolve())
        self.assertEqual(safe_join(self.root, "/"), (self.root / "index.html").resolve())

    def test_rejects_escapes_and_missing_files(self):
        """Traversal, encoded traversal, drive paths and missing files are rejected."""
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/C:/Windows/win.ini", "/missing.css", "/_astro"):
            with self.subTest(path=path):
                self.assertIsNone(safe_join(self.root, path))


class LoadBundleTests(unittest.TestCase):
    """Only compatible bundles are served."""

    def setUp(self):
        """Create a temporary bundle directory."""
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        (self.root / "index.html").write_text("<html></html>", encoding="utf-8")

    def tearDown(self):
        """Remove the temporary tree."""
        self._temp.cleanup()

    def test_accepts_a_manifestless_dev_build(self):
        """A local build without a manifest is usable as a dev bundle."""
        bundle = load_ui_bundle(self.root)
        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(bundle.ui_version, "dev")

    def test_rejects_an_incompatible_manifest(self):
        """A manifest for another API version is not served."""
        (self.root / "manifest.json").write_text(json.dumps({"apiVersion": API_VERSION + 1}), encoding="utf-8")
        self.assertIsNone(load_ui_bundle(self.root))

    def test_resolve_prefers_the_environment_override(self):
        """WOSUTIL_UI_DIR points the shell at a local build."""
        with patch.dict("os.environ", {"WOSUTIL_UI_DIR": str(self.root)}):
            bundle = resolve_ui_bundle()
        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(bundle.path, self.root)
