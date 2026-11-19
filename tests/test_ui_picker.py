"""Tests for the native path picker."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from wosutil.ui.picker import pick_path


class PickerTests(unittest.TestCase):
    """The picker picks the right platform command and handles cancellation."""

    def test_rejects_unknown_kinds(self):
        """Only directory and file are valid."""
        with self.assertRaises(ValueError):
            pick_path("whatever")

    def test_linux_uses_zenity_directory_mode(self):
        """Zenity receives the directory flag."""
        completed = SimpleNamespace(returncode=0, stdout="/tmp/games\n")
        with (
            patch("wosutil.ui.picker.sys.platform", "linux"),
            patch("wosutil.ui.picker.shutil.which", side_effect=lambda name: "/usr/bin/zenity" if name == "zenity" else None),
            patch("wosutil.ui.picker.subprocess.run", return_value=completed) as run,
        ):
            self.assertEqual(pick_path("directory", "Pick", "/tmp"), "/tmp/games")

        arguments = run.call_args.args[0]
        self.assertEqual(arguments[0], "/usr/bin/zenity")
        self.assertIn("--directory", arguments)

    def test_linux_returns_none_when_cancelled(self):
        """A non-zero exit code means the dialog was cancelled."""
        completed = SimpleNamespace(returncode=1, stdout="")
        with (
            patch("wosutil.ui.picker.sys.platform", "linux"),
            patch("wosutil.ui.picker.shutil.which", side_effect=lambda name: "/usr/bin/zenity" if name == "zenity" else None),
            patch("wosutil.ui.picker.subprocess.run", return_value=completed),
        ):
            self.assertIsNone(pick_path("file"))
