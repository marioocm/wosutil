"""Unit tests for the emulator manager scroll gesture helpers."""

import subprocess
import unittest
from unittest.mock import patch

from wosutil.emulator.emulator_manager import (
    WHITEOUT_PACKAGE,
    AdbCommandError,
    _scroll_with_hold,
    click_on_coordinates,
    force_stop_game,
    is_wos_installed,
    scroll_screen,
    take_screenshot,
)

SHELL = "shell"
INPUT = "input"


def _ok():
    """A CompletedProcess that reports success."""
    return subprocess.CompletedProcess(args=[], returncode=0)


def _fail():
    """A CompletedProcess that reports failure (command unsupported)."""
    return subprocess.CompletedProcess(args=[], returncode=1)


class TestIsWosInstalled(unittest.TestCase):
    """Test cases for the resilient game-installed check."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.emulator.emulator_manager.execute_adb_command"),
            patch("wosutil.emulator.emulator_manager.verify_adb_connected"),
            patch("wosutil.emulator.emulator_manager.time.sleep"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.execute_adb_command, self.verify_adb_connected, self.time_sleep = self.mocks
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_installed_when_package_present(self):
        """A reachable device reporting the package is installed."""
        self.verify_adb_connected.return_value = True
        self.execute_adb_command.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=f"package:{WHITEOUT_PACKAGE}\n")
        self.assertTrue(is_wos_installed(0))

    def test_not_installed_when_absent_but_reachable(self):
        """A reachable device without the package is definitively not installed."""
        self.verify_adb_connected.return_value = True
        self.execute_adb_command.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="package:com.other.app\n")
        self.assertFalse(is_wos_installed(0))

    def test_assumes_installed_when_adb_unreachable(self):
        """An unreachable device must not produce a false 'not installed'."""
        self.verify_adb_connected.return_value = False
        self.assertTrue(is_wos_installed(0))
        # It should have retried the connection before giving up.
        self.assertGreaterEqual(self.verify_adb_connected.call_count, 2)

    def test_assumes_installed_when_package_query_fails(self):
        """A failing package query (e.g. Android still booting) is not 'not installed'."""
        self.verify_adb_connected.return_value = True
        # "cmd: Can't find service: package" — the package manager is not up yet.
        self.execute_adb_command.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="cmd: Can't find service: package\n")
        self.assertTrue(is_wos_installed(0))
        # The query should have been retried before giving up.
        self.assertGreaterEqual(self.execute_adb_command.call_count, 3)

    def test_not_installed_requires_a_successful_query(self):
        """Only a successful query without the package is definitive."""
        self.verify_adb_connected.return_value = True
        self.execute_adb_command.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=1, stdout=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="package:com.other.app\n"),
        ]
        self.assertFalse(is_wos_installed(0))
        self.assertEqual(self.execute_adb_command.call_count, 2)


class TestScrollScreen(unittest.TestCase):
    """Test cases for the scroll gesture helper."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.emulator.emulator_manager.execute_adb_command"),
            patch("wosutil.emulator.emulator_manager.time.sleep"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.execute_adb_command, self.time_sleep = self.mocks
        self.execute_adb_command.return_value = _ok()
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_plain_swipe_without_hold(self):
        """Without a hold the regular input swipe command is used."""
        scroll_screen(13, 500, 13, 0, 200, 0)
        self.execute_adb_command.assert_called_once_with([SHELL, INPUT, "swipe", "13", "500", "13", "0", "200"], 0)
        self.time_sleep.assert_not_called()

    def test_hold_uses_continuous_motionevent_gesture(self):
        """A held scroll sends DOWN, moves, waits and lifts with a single continuous gesture."""
        self.execute_adb_command.return_value = _ok()
        _scroll_with_hold(13, 500, 13, 0, 400, 150, 0, steps=4)
        commands = [c.args[0] for c in self.execute_adb_command.call_args_list]
        self.assertEqual(commands[0], [SHELL, INPUT, "motionevent", "DOWN", "13", "500"])
        self.assertEqual(commands[-1], [SHELL, INPUT, "motionevent", "UP", "13", "0"])
        moves = [c for c in commands if c[1] == INPUT and c[2] == "motionevent" and c[3] == "MOVE"]
        self.assertEqual(len(moves), 4)
        self.assertEqual(moves[0][4:], ["13", "375"])
        self.assertEqual(moves[-1][4:], ["13", "0"])
        self.time_sleep.assert_called()

    def test_hold_falls_back_when_motionevent_unsupported(self):
        """When motionevent fails a slow swipe plus a press at the end is used."""
        self.execute_adb_command.side_effect = [_fail(), _ok(), _ok()]
        _scroll_with_hold(13, 500, 13, 0, 400, 150, 0, steps=4)
        swipes = [c.args[0] for c in self.execute_adb_command.call_args_list if c.args[0][1] == INPUT and c.args[0][2] == "swipe"]
        self.assertEqual(
            swipes,
            [
                [SHELL, INPUT, "swipe", "13", "500", "13", "0", "550"],
                [SHELL, INPUT, "swipe", "13", "0", "13", "0", "150"],
            ],
        )

    def test_plain_swipe_failure_is_reported(self):
        """A failed regular swipe cannot be reported as successful."""
        self.execute_adb_command.return_value = _fail()

        with self.assertRaises(AdbCommandError):
            scroll_screen(13, 500, 13, 0, 200, 0)

    def test_fallback_failure_is_reported(self):
        """A failed fallback swipe is surfaced to the task runner."""
        self.execute_adb_command.side_effect = [_fail(), _fail()]

        with self.assertRaises(AdbCommandError):
            _scroll_with_hold(13, 500, 13, 0, 400, 150, 0, steps=4)


class TestInputAndScreenshotFailures(unittest.TestCase):
    """Input commands and screenshots fail safely instead of reporting success."""

    def test_click_raises_when_adb_fails(self):
        """A failed ADB tap is surfaced to the caller."""
        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="device offline")
        with patch("wosutil.emulator.emulator_manager.execute_adb_command", return_value=failed), patch("wosutil.emulator.emulator_manager.time.sleep") as sleep, self.assertRaisesRegex(
            AdbCommandError, "device offline"
        ):
            click_on_coordinates(10, 20, 0)
        sleep.assert_not_called()

    def test_force_stop_raises_when_adb_fails(self):
        """A failed force-stop is surfaced instead of being ignored."""
        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="permission denied")
        with patch("wosutil.emulator.emulator_manager.execute_adb_command", return_value=failed), self.assertRaisesRegex(AdbCommandError, "permission denied"):
            force_stop_game(0)

    def test_take_screenshot_removes_local_file_when_screencap_fails(self):
        """A failed remote capture removes the local placeholder and remote path."""
        local_path = "temporary/wosutil_test.png"
        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="capture failed")
        cleanup = subprocess.CompletedProcess(args=[], returncode=0)
        with patch("wosutil.emulator.emulator_manager.verify_adb_connected", return_value=True), patch("wosutil.emulator.emulator_manager.tempfile.mkstemp", return_value=(123, local_path)), patch(
            "wosutil.emulator.emulator_manager.os.close"
        ), patch("wosutil.emulator.emulator_manager.execute_adb_command", side_effect=[failed, cleanup]) as execute, patch("wosutil.emulator.emulator_manager.delete_temp_screenshot") as delete:
            self.assertIsNone(take_screenshot(0))

        delete.assert_called_once_with(local_path)
        self.assertEqual(execute.call_args_list[1].args[0], ["shell", "rm", "/sdcard/wosutil_test.png"])

    def test_take_screenshot_keeps_local_file_after_success(self):
        """A successful capture keeps the downloaded file for its caller."""
        local_path = "temporary/wosutil_test.png"
        success = subprocess.CompletedProcess(args=[], returncode=0)
        with patch("wosutil.emulator.emulator_manager.verify_adb_connected", return_value=True), patch("wosutil.emulator.emulator_manager.tempfile.mkstemp", return_value=(123, local_path)), patch(
            "wosutil.emulator.emulator_manager.os.close"
        ), patch("wosutil.emulator.emulator_manager.execute_adb_command", side_effect=[success, success, success]), patch("wosutil.emulator.emulator_manager.delete_temp_screenshot") as delete:
            self.assertEqual(take_screenshot(0), local_path)

        delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
