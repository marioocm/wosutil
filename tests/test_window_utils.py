"""Unit tests for the Win32 window helpers used to keep emulators backgrounded.

The helpers are no-ops on non-Windows platforms (CI runs on Linux); the logic
is exercised by faking the user32/dwmapi objects.
"""

import unittest
from unittest.mock import patch

import psutil

import wosutil.emulator.window_utils as window_utils
from wosutil.emulator.window_utils import (
    SW_MINIMIZE,
    SW_SHOWMINNOACTIVE,
    WS_EX_TOOLWINDOW,
    _watch_foreground,
    minimize_foreground_watcher,
    minimize_hwnds,
    minimize_process_windows,
    minimize_windows_by_title,
)


def identity(func):
    """Dummy callback-type decorator used in place of WINFUNCTYPE."""
    return func


class FakeUser32:
    """Minimal user32 stand-in recording every ShowWindow call."""

    def __init__(self, windows):
        """Store the fake window table and the foreground window."""
        self.windows = windows
        self.minimized = []
        self.foreground = 0

    def EnumWindows(self, callback, _lparam):
        """Invoke the callback with every fake window handle."""
        for hwnd in self.windows:
            callback(hwnd, 0)
        return True

    def IsWindowVisible(self, hwnd):
        """Return the fake visibility flag (unknown handles are hidden)."""
        return self.windows.get(hwnd, {}).get("visible", False)

    def IsIconic(self, hwnd):
        """Return the fake minimized flag (unknown handles are not minimized)."""
        return self.windows.get(hwnd, {}).get("iconic", False)

    def GetWindowLongW(self, hwnd, _index):
        """Return the fake extended-style tool window flag."""
        return WS_EX_TOOLWINDOW if self.windows.get(hwnd, {}).get("tool") else 0

    def GetWindowThreadProcessId(self, hwnd, _out):
        """Return the fake owning PID (0 for unknown handles)."""
        return self.windows.get(hwnd, {}).get("pid", 0)

    def ShowWindow(self, hwnd, command):
        """Record the minimization and report success."""
        self.minimized.append((hwnd, command))
        return True

    def GetForegroundWindow(self):
        """Return the fake foreground window."""
        return self.foreground

    def GetWindowTextLengthW(self, hwnd):
        """Return the length of the fake window title."""
        return len(self.windows.get(hwnd, {}).get("title", ""))

    def GetWindowTextW(self, hwnd, buffer, maxlen):
        """Fill the buffer with the fake window title."""
        title = self.windows.get(hwnd, {}).get("title", "")
        buffer.value = title[: maxlen - 1]
        return len(buffer.value)


class FakeDwmapi:
    """dwmapi stand-in that never reports cloaked windows."""

    def DwmGetWindowAttribute(self, hwnd, _attribute, _value, _size):
        """Report that the window is not cloaked."""
        return 1


def enable_fake_win32(user32):
    """Make window_utils believe Windows is available and use the fake DLLs."""
    window_utils._user32 = user32
    window_utils._dwmapi = FakeDwmapi()
    window_utils._wnd_enum_proc = identity


class TestWindowUtils(unittest.TestCase):
    """Tests for window enumeration, filtering and minimizing."""

    def setUp(self):
        """Reset the loaded-DLL state so tests control the platform."""
        window_utils._user32 = None
        window_utils._dwmapi = None
        window_utils._wnd_enum_proc = None

    def test_non_windows_platform_is_noop(self):
        """On non-Windows platforms nothing is minimized."""
        with patch.object(window_utils.os, "name", "posix"):
            self.assertEqual(minimize_hwnds([1, 2]), 0)
            self.assertEqual(minimize_process_windows(("MuMuPlayer",)), 0)

    def test_minimize_hwnds_minimizes_without_activating(self):
        """Windows are minimized with SW_SHOWMINNOACTIVE."""
        user32 = FakeUser32({})
        enable_fake_win32(user32)
        with patch.object(window_utils, "_load_win32", return_value=True):
            count = minimize_hwnds([0x1234, 0x5678])
        self.assertEqual(count, 2)
        self.assertEqual(user32.minimized, [(0x1234, SW_SHOWMINNOACTIVE), (0x5678, SW_SHOWMINNOACTIVE)])

    def test_minimize_active_window_uses_sw_minimize(self):
        """An active window is minimized with SW_MINIMIZE so the focus returns to the previous app."""
        windows = {1: {"visible": True, "iconic": False, "tool": False, "pid": 100}}
        user32 = FakeUser32(windows)
        enable_fake_win32(user32)
        user32.foreground = 1
        with patch.object(window_utils, "_load_win32", return_value=True):
            count = minimize_hwnds([1])
        self.assertEqual(count, 1)
        self.assertEqual(user32.minimized, [(1, SW_MINIMIZE)])

    def test_minimize_process_windows_filters_by_process_and_visibility(self):
        """Only visible, non-minimized windows of the requested process are minimized."""
        windows = {
            1: {"visible": True, "iconic": False, "tool": False, "pid": 100},
            2: {"visible": True, "iconic": True, "tool": False, "pid": 100},  # already minimized
            3: {"visible": False, "iconic": False, "tool": False, "pid": 100},  # hidden
            4: {"visible": True, "iconic": False, "tool": True, "pid": 100},  # tool window
            5: {"visible": True, "iconic": False, "tool": False, "pid": 200},  # other process
        }
        user32 = FakeUser32(windows)
        enable_fake_win32(user32)
        with patch.object(window_utils, "_load_win32", return_value=True), patch(
            "psutil.Process", side_effect=lambda pid: type("P", (), {"name": lambda self: "HD-Player.exe" if pid == 100 else "chrome.exe"})()
        ):
            count = minimize_process_windows(("HD-Player",))
        self.assertEqual(count, 1)
        self.assertEqual(user32.minimized, [(1, SW_SHOWMINNOACTIVE)])

    def test_process_name_matching_is_case_and_extension_insensitive(self):
        """Names match by basename without extension, case-insensitively."""
        windows = {1: {"visible": True, "iconic": False, "tool": False, "pid": 100}}
        user32 = FakeUser32(windows)
        enable_fake_win32(user32)
        with patch.object(window_utils, "_load_win32", return_value=True), patch("psutil.Process", side_effect=lambda pid: type("P", (), {"name": lambda self: "MUMUPLAYER.EXE"})()):
            count = minimize_process_windows(("mumuplayer",))
        self.assertEqual(count, 1)

    def test_unreadable_processes_are_skipped(self):
        """Access-denied processes never break the sweep."""
        windows = {1: {"visible": True, "iconic": False, "tool": False, "pid": 100}}
        user32 = FakeUser32(windows)
        enable_fake_win32(user32)
        with patch.object(window_utils, "_load_win32", return_value=True), patch("psutil.Process", side_effect=psutil.AccessDenied()):
            count = minimize_process_windows(("MuMuPlayer",))
        self.assertEqual(count, 0)

    def test_empty_process_names_are_ignored(self):
        """An empty process list sweeps nothing."""
        user32 = FakeUser32({})
        enable_fake_win32(user32)
        with patch.object(window_utils, "_load_win32", return_value=True):
            self.assertEqual(minimize_process_windows(("", None)), 0)
            self.assertEqual(user32.minimized, [])

    def test_minimize_windows_by_title(self):
        """Visible windows titled like the instance are minimized."""
        windows = {
            1: {"visible": True, "iconic": False, "tool": False, "pid": 100, "title": "Mario"},
            2: {"visible": True, "iconic": True, "tool": False, "pid": 100, "title": "Mario"},  # already minimized
            3: {"visible": False, "iconic": False, "tool": False, "pid": 100, "title": "Mario"},  # hidden
            4: {"visible": True, "iconic": False, "tool": False, "pid": 100, "title": "Other"},
        }
        user32 = FakeUser32(windows)
        enable_fake_win32(user32)
        with patch.object(window_utils, "_load_win32", return_value=True):
            count = minimize_windows_by_title(("Mario",))
        self.assertEqual(count, 1)
        self.assertEqual(user32.minimized, [(1, SW_SHOWMINNOACTIVE)])

    def test_enum_failure_is_swallowed(self):
        """A failing EnumWindows call must not raise."""

        class BrokenUser32(FakeUser32):
            def EnumWindows(self, _callback, _lparam):
                raise OSError("boom")

        enable_fake_win32(BrokenUser32({}))
        with patch.object(window_utils, "_load_win32", return_value=True):
            self.assertEqual(minimize_process_windows(("MuMuPlayer",)), 0)

    def test_cloaked_windows_are_skipped(self):
        """Windows on other virtual desktops are not minimized."""
        window_utils._user32 = FakeUser32({1: {"visible": True, "iconic": False, "tool": False, "pid": 100}})
        window_utils._dwmapi = FakeDwmapi()
        window_utils._wnd_enum_proc = identity
        with patch.object(window_utils, "_load_win32", return_value=True), patch.object(window_utils, "_is_cloaked", return_value=True), patch(
            "psutil.Process", side_effect=lambda pid: type("P", (), {"name": lambda self: "MuMuPlayer.exe"})()
        ):
            count = minimize_process_windows(("MuMuPlayer",))
        self.assertEqual(count, 0)

    def test_log_accepts_one_or_two_argument_callables(self):
        """Both log_message(message, level) and logger.info(message) work."""
        user32 = FakeUser32({})
        enable_fake_win32(user32)
        two_arg = []
        one_arg = []
        with patch.object(window_utils, "_load_win32", return_value=True):
            minimize_hwnds([1], log=lambda message, level="info": two_arg.append(message))
            minimize_hwnds([2], log=lambda message: one_arg.append(message))
        self.assertEqual(len(two_arg), 1)
        self.assertEqual(len(one_arg), 1)


class TestForegroundWatcher(unittest.TestCase):
    """The watcher minimizes emulator windows that take the foreground."""

    def setUp(self):
        """Reset the loaded-DLL state so tests control the platform."""
        window_utils._user32 = None
        window_utils._dwmapi = None
        window_utils._wnd_enum_proc = None

    def test_minimizes_matching_handle_when_it_takes_foreground(self):
        """A watched handle that becomes foreground is minimized repeatedly."""
        windows = {0x160680: {"visible": True, "iconic": False, "tool": False, "pid": 100}}
        user32 = FakeUser32(windows)
        enable_fake_win32(user32)
        user32.foreground = 0x160680
        with patch.object(window_utils, "_load_win32", return_value=True):
            _watch_foreground([0x160680], (), (), seconds=0.05, interval=0.01, log=None, refresh_handles=None)
        self.assertTrue(user32.minimized)
        for hwnd, command in user32.minimized:
            self.assertEqual((hwnd, command), (0x160680, SW_MINIMIZE))

    def test_ignores_foreign_foreground_windows(self):
        """Windows not owned by the emulator are never minimized."""
        windows = {1: {"visible": True, "iconic": False, "tool": False, "pid": 100, "title": "Mario"}}
        user32 = FakeUser32(windows)
        enable_fake_win32(user32)
        user32.foreground = 99
        with patch.object(window_utils, "_load_win32", return_value=True):
            _watch_foreground([], ("MuMuNxDevice",), ("Mario",), seconds=0.05, interval=0.01, log=None, refresh_handles=None)
        self.assertEqual(user32.minimized, [])

    def test_matches_foreground_window_by_process_name(self):
        """A foreground window owned by a wanted process is minimized."""
        windows = {1: {"visible": True, "iconic": False, "tool": False, "pid": 100}}
        user32 = FakeUser32(windows)
        enable_fake_win32(user32)
        user32.foreground = 1
        with patch.object(window_utils, "_load_win32", return_value=True), patch("psutil.Process", side_effect=lambda pid: type("P", (), {"name": lambda self: "MuMuNxDevice.exe"})()):
            _watch_foreground([], ("MuMuNxDevice",), (), seconds=0.05, interval=0.01, log=None, refresh_handles=None)
        self.assertTrue(user32.minimized)
        for hwnd, command in user32.minimized:
            self.assertEqual((hwnd, command), (1, SW_MINIMIZE))

    def test_matches_foreground_window_by_title(self):
        """A foreground window titled like the instance is minimized."""
        windows = {1: {"visible": True, "iconic": False, "tool": False, "pid": 100, "title": "Mario"}}
        user32 = FakeUser32(windows)
        enable_fake_win32(user32)
        user32.foreground = 1
        with patch.object(window_utils, "_load_win32", return_value=True):
            _watch_foreground([], (), ("Mario",), seconds=0.05, interval=0.01, log=None, refresh_handles=None)
        self.assertTrue(user32.minimized)
        for hwnd, command in user32.minimized:
            self.assertEqual((hwnd, command), (1, SW_MINIMIZE))

    def test_refresh_handles_updates_the_watched_set(self):
        """Re-created emulator windows are picked up via refresh_handles."""
        windows = {0x100: {"visible": True, "iconic": False, "tool": False, "pid": 1}, 0x200: {"visible": True, "iconic": False, "tool": False, "pid": 1}}
        user32 = FakeUser32(windows)
        enable_fake_win32(user32)
        user32.foreground = 0x200
        with patch.object(window_utils, "_load_win32", return_value=True), patch.object(window_utils, "_WATCHER_HANDLE_REFRESH_SECONDS", 0.01):
            _watch_foreground([0x100], (), (), seconds=0.12, interval=0.01, log=None, refresh_handles=lambda: [0x200])
        self.assertTrue(any(hwnd == 0x200 for hwnd, _ in user32.minimized))

    def test_watcher_is_a_daemon_thread(self):
        """minimize_foreground_watcher spawns a daemon thread on Windows."""
        user32 = FakeUser32({})
        enable_fake_win32(user32)
        with patch.object(window_utils, "_load_win32", return_value=True):
            minimize_foreground_watcher([], ("HD-Player",), seconds=0.01)
        self.assertEqual(len(user32.minimized), 0)  # no crash, thread started

    def test_watcher_is_noop_off_windows(self):
        """On non-Windows platforms no thread is spawned."""
        with patch.object(window_utils.os, "name", "posix"):
            minimize_foreground_watcher([1], ("HD-Player",))
            _watch_foreground([1], ("HD-Player",), (), 0.01, 0.01, None, None)


if __name__ == "__main__":
    unittest.main()
