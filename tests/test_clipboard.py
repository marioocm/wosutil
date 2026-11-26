"""Tests for the host-to-Waydroid clipboard helper."""

import unittest
from unittest.mock import Mock, patch

from wosutil.emulator.clipboard import find_instance_for_pid, normalize_text, paste_text, read_host_clipboard, running_instances


class FakeAdb:
    """Minimal ADB client stub."""

    adb_path = "/usr/bin/adb"


class FakeBackend:
    """Minimal Waydroid backend stub with one running instance."""

    def __init__(self):
        """Expose two instances; only index 1 has a live window."""
        self.adb = FakeAdb()
        self._instances = [
            {"index": 0, "id": "default", "name": "Mario"},
            {"index": 1, "id": "alt", "name": "Tokyo"},
        ]

    def _matching_processes(self, index):
        """Return a fake gamescope process for Tokyo."""
        return [Mock(pid=101)] if index == 1 else []

    def get_serial(self, index):
        """Return a stable serial for the test instance."""
        return f"serial-{index}"


class ClipboardTests(unittest.TestCase):
    """The paste helper must reach the right instance and type safely."""

    def test_normalize_text_collapses_line_breaks(self):
        """Android input text cannot type newlines; they become spaces."""
        self.assertEqual(normalize_text("hello\r\nworld\nagain"), "hello world again")
        self.assertEqual(normalize_text("  spaced  "), "spaced")

    def test_read_host_clipboard_uses_klipper_first(self):
        """Klipper exposes the current clipboard even without a clipboard owner."""

        def which(name):
            return "/usr/bin/qdbus6" if name == "qdbus6" else None

        with (
            patch("wosutil.emulator.clipboard.shutil.which", side_effect=which),
            patch("wosutil.emulator.clipboard.run_process_robust", return_value=Mock(returncode=0, stdout="klipper text\n")) as run,
        ):
            self.assertEqual(read_host_clipboard(), "klipper text")
        run.assert_called_once_with(["/usr/bin/qdbus6", "org.kde.klipper", "/klipper", "getClipboardContents"], timeout=5)

    def test_find_instance_uses_the_window_owner_pid(self):
        """The focused gamescope window selects its Waydroid instance."""
        backend = FakeBackend()
        self.assertEqual(find_instance_for_pid(backend, 101), 1)
        self.assertIsNone(find_instance_for_pid(backend, 999))
        self.assertIsNone(find_instance_for_pid(backend, None))

    def test_running_instances_lists_only_live_windows(self):
        """Instances without a gamescope process are not paste targets."""
        self.assertEqual(running_instances(FakeBackend()), [1])

    def test_paste_text_pushes_and_types_the_clipboard(self):
        """The text is pushed to a file and expanded by the remote shell."""
        backend = FakeBackend()
        with patch("wosutil.emulator.clipboard.run_process_robust", return_value=Mock(returncode=0)) as run:
            self.assertTrue(paste_text(backend, 1, "hello world"))

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][:4], ["/usr/bin/adb", "-s", "serial-1", "push"])
        self.assertEqual(commands[1], ["/usr/bin/adb", "-s", "serial-1", "shell", 'input text "$(cat /data/local/tmp/wosutil_paste.txt)"'])
        self.assertIn(["/usr/bin/adb", "-s", "serial-1", "shell", "rm", "-f", "/data/local/tmp/wosutil_paste.txt"], commands)

    def test_paste_text_rejects_empty_text(self):
        """An empty clipboard does not run any ADB command."""
        with patch("wosutil.emulator.clipboard.run_process_robust") as run:
            self.assertFalse(paste_text(FakeBackend(), 1, " \n "))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
