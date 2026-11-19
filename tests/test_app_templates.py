"""Tests for template loading, cropping and saving."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from wosutil.app.templates import TemplateError, TemplateService


class TemplateServiceTests(unittest.TestCase):
    """Cover the image and path rules behind the Templates screen."""

    def setUp(self):
        """Create a writable templates directory and a source image."""
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.templates_dir = self.root / "templates"
        self.templates_dir.mkdir()
        self._patch = patch("wosutil.app.templates.TEMPLATES_WRITE_DIR", str(self.templates_dir))
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.source = self.root / "source.png"
        Image.new("RGB", (100, 80), (200, 40, 40)).save(self.source)
        self.service = TemplateService()

    def tearDown(self):
        """Remove the temporary tree."""
        self._temp.cleanup()

    def test_loads_and_encodes_the_image(self):
        """Loading reports the size and the PNG endpoint bytes are valid."""
        info = self.service.load(str(self.source))
        self.assertEqual((info["width"], info["height"]), (100, 80))
        self.assertEqual(info["rev"], 1)
        self.assertTrue(self.service.png().startswith(b"\x89PNG"))

    def test_saves_a_crop_under_the_templates_directory(self):
        """A relative path is contained and the crop has the requested size."""
        self.service.load(str(self.source))
        saved = Path(self.service.save_crop([10, 10, 20, 15], "nested/test"))
        self.assertEqual(saved, (self.templates_dir / "nested" / "test.png").resolve())
        with Image.open(saved) as image:
            self.assertEqual(image.size, (20, 15))

    def test_saves_the_full_image_with_a_default_name(self):
        """An empty name saves under screenshots/ with the source size."""
        self.service.load(str(self.source))
        saved = Path(self.service.save_full(""))
        self.assertTrue(saved.is_file())
        with Image.open(saved) as image:
            self.assertEqual(image.size, (100, 80))

    def test_rejects_unsafe_paths_and_invalid_regions(self):
        """The save path cannot escape the templates directory."""
        self.service.load(str(self.source))
        for name in ("../escape.png", "C:/escape.png", "/escape.png"):
            with self.subTest(name=name), self.assertRaises(TemplateError):
                self.service.save_full(name)
        for roi in ([0, 0, 0, 5], [90, 70, 20, 20], [1, 2, 3]):
            with self.subTest(roi=roi), self.assertRaises(TemplateError):
                self.service.save_crop(roi, "bad")

    def test_capture_reads_and_removes_the_temporary_screenshot(self):
        """Capture reads the temp file before deleting it."""
        capture = self.root / "capture.png"
        Image.new("RGB", (30, 20), (0, 0, 0)).save(capture)
        with patch("wosutil.app.templates.take_screenshot", return_value=str(capture)):
            info = TemplateService().capture(2)
        self.assertEqual((info["width"], info["height"]), (30, 20))
        self.assertEqual(info["source"], "Instance 2")
        self.assertFalse(capture.exists())
