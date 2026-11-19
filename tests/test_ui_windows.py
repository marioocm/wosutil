"""Tests for the app window lifecycle helpers."""

import unittest
from unittest.mock import patch

from wosutil.ui.launcher import _candidate_browsers
from wosutil.ui.windows import watch_app_window


class CandidateOrderTests(unittest.TestCase):
    """Edge must be preferred over any other Chromium browser."""

    def test_every_edge_path_is_tried_before_chrome(self):
        """The per-root interleave used to pick Chrome over Edge x86."""
        with (
            patch("sys.platform", "win32"),
            patch.dict("os.environ", {"PROGRAMFILES": r"C:\PF", "PROGRAMFILES(X86)": r"C:\PF86", "LOCALAPPDATA": r"C:\LAD"}),
        ):
            candidates = _candidate_browsers()

        edge_indices = [index for index, path in enumerate(candidates) if "Microsoft" in path]
        chrome_indices = [index for index, path in enumerate(candidates) if "Google" in path or "Chromium" in path]
        self.assertTrue(edge_indices)
        self.assertTrue(chrome_indices)
        self.assertLess(max(edge_indices), min(chrome_indices))


class WatchAppWindowTests(unittest.TestCase):
    """The watcher focuses once and reports a closed window."""

    def test_focuses_once_and_reports_the_close(self):
        """Two consecutive missing polls mean the window is gone."""
        sequence = iter([1, 1, None, None])
        focused = []
        closed = []

        watch_app_window(
            "WoS Util",
            on_closed=lambda: closed.append(True),
            find_window=lambda _title: next(sequence, None),
            focus=focused.append,
            poll_seconds=0,
        )

        self.assertEqual(focused, [1, 1])
        self.assertEqual(closed, [True])
