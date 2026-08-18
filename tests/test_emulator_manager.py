"""Unit tests for the emulator manager scroll gesture helpers."""

import subprocess
import unittest
from unittest.mock import patch

from wosutil.emulator.emulator_manager import _scroll_with_hold, scroll_screen

SHELL = "shell"
INPUT = "input"


def _ok():
    """A CompletedProcess that reports success."""
    return subprocess.CompletedProcess(args=[], returncode=0)


def _fail():
    """A CompletedProcess that reports failure (command unsupported)."""
    return subprocess.CompletedProcess(args=[], returncode=1)


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


if __name__ == "__main__":
    unittest.main()
