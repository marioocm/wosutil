"""Tests for the native pywebview window wrapper."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from wosutil.ui.native_window import _preferred_gui, run_native_window


class FakeWindow:
    """Minimal pywebview window stub."""

    def __init__(self):
        """Start with the window open."""
        self.destroyed = False

    def destroy(self):
        """Record the close request."""
        self.destroyed = True


class NativeWindowTests(unittest.TestCase):
    """The wrapper must open WebView2 and fall back cleanly."""

    def setUp(self):
        """Create a temporary cache directory."""
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)

    def test_returns_false_without_pywebview(self):
        """A missing dependency tells the caller to use the browser fallback."""
        with patch.dict(sys.modules, {"webview": None}):
            self.assertFalse(run_native_window("http://127.0.0.1/", lambda: None))

    def test_opens_a_focused_native_window(self):
        """The window uses the URL, dark canvas and persistent storage."""
        fake_window = FakeWindow()
        fake = SimpleNamespace(create_window=MagicMock(return_value=fake_window), start=MagicMock())
        with (
            patch.dict(sys.modules, {"webview": fake}),
            patch("wosutil.ui.native_window.cache_dir", return_value=Path(self._temp.name)),
        ):
            result = run_native_window("http://127.0.0.1/?t=x", wait_for_stop=lambda: None, debug=True)

        self.assertTrue(result)
        args, kwargs = fake.create_window.call_args
        self.assertEqual(args[0], "WoS Util")
        self.assertEqual(args[1], "http://127.0.0.1/?t=x")
        self.assertTrue(kwargs["focus"])
        self.assertTrue(kwargs["text_select"])
        self.assertFalse(kwargs["zoomable"])
        self.assertEqual(kwargs["background_color"], "#151311")
        fake.start.assert_called_once()
        self.assertTrue(fake.start.call_args.kwargs["debug"])
        self.assertFalse(fake.start.call_args.kwargs["private_mode"])
        self.assertTrue(Path(fake.start.call_args.kwargs["storage_path"]).is_dir())

    def test_forces_gtk_when_available(self):
        """An available WebKitGTK backend is passed to pywebview explicitly."""
        fake = SimpleNamespace(create_window=MagicMock(return_value=FakeWindow()), start=MagicMock())
        with (
            patch.dict(sys.modules, {"webview": fake}),
            patch("wosutil.ui.native_window.cache_dir", return_value=Path(self._temp.name)),
            patch("wosutil.ui.native_window._preferred_gui", return_value="gtk"),
        ):
            self.assertTrue(run_native_window("http://127.0.0.1/", lambda: None))
        self.assertEqual(fake.start.call_args.kwargs["gui"], "gtk")

    def test_preferred_gui_is_none_off_linux(self):
        """Other platforms keep pywebview's automatic backend choice."""
        with patch("wosutil.ui.native_window.sys.platform", "win32"):
            self.assertIsNone(_preferred_gui())

    def test_create_window_failure_falls_back(self):
        """A failed window creation returns False without starting the loop."""
        fake = SimpleNamespace(create_window=MagicMock(return_value=None), start=MagicMock())
        with (
            patch.dict(sys.modules, {"webview": fake}),
            patch("wosutil.ui.native_window.cache_dir", return_value=Path(self._temp.name)),
        ):
            self.assertFalse(run_native_window("http://127.0.0.1/", lambda: None))
        fake.start.assert_not_called()

    def test_start_failure_falls_back(self):
        """A backend failure returns False so the caller can use the browser."""
        fake = SimpleNamespace(create_window=MagicMock(return_value=FakeWindow()), start=MagicMock(side_effect=RuntimeError("no WebView2")))
        with (
            patch.dict(sys.modules, {"webview": fake}),
            patch("wosutil.ui.native_window.cache_dir", return_value=Path(self._temp.name)),
        ):
            self.assertFalse(run_native_window("http://127.0.0.1/", lambda: None))
