"""Unit tests for the pure ADB transport."""

import subprocess
import unittest
from unittest.mock import patch

from wosutil.adb import AdbClient, parse_devices_output


class TestParseDevicesOutput(unittest.TestCase):
    """Parse the output of ``adb devices``."""

    def test_parses_devices_and_states(self):
        """Every device line becomes a serial -> state entry."""
        stdout = "List of devices attached\n127.0.0.1:5555\tdevice\n127.0.0.1:5552\toffline\n"
        self.assertEqual(parse_devices_output(stdout), {"127.0.0.1:5555": "device", "127.0.0.1:5552": "offline"})

    def test_ignores_headers_and_empty_output(self):
        """The header and daemon banners are not devices."""
        self.assertEqual(parse_devices_output(""), {})
        self.assertEqual(parse_devices_output("List of devices attached\n* daemon started successfully *\n"), {})


class TestAdbClient(unittest.TestCase):
    """The client only builds and runs ADB commands."""

    def test_build_command_targets_the_serial(self):
        """The serial is inserted before the command parts."""
        client = AdbClient("adb")
        self.assertEqual(client.build_command("127.0.0.1:5555", ["shell", "echo", "hi"]), ["adb", "-s", "127.0.0.1:5555", "shell", "echo", "hi"])

    def test_devices_parses_the_server_output(self):
        """The devices call parses the adb output."""
        client = AdbClient("adb")
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="List of devices attached\nserial-1\tdevice\n", stderr="")
        with patch("wosutil.adb.run_process_robust", return_value=result) as run:
            self.assertEqual(client.devices(), {"serial-1": "device"})
        run.assert_called_once_with(["adb", "devices"], timeout=15)

    def test_run_and_run_bytes_target_the_serial(self):
        """Both runners build the command with the requested serial."""
        client = AdbClient("adb")
        text_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        bytes_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"ok", stderr=b"")
        with (
            patch("wosutil.adb.run_process_robust", return_value=text_result) as run_text,
            patch("wosutil.adb.run_process_robust_bytes", return_value=bytes_result) as run_bytes,
        ):
            self.assertIs(client.run("serial-1", ["shell", "echo", "hi"], timeout=5), text_result)
            self.assertIs(client.run_bytes("serial-1", ["exec-out", "screencap", "-p"], timeout=5), bytes_result)
        run_text.assert_called_once_with(["adb", "-s", "serial-1", "shell", "echo", "hi"], timeout=5)
        run_bytes.assert_called_once_with(["adb", "-s", "serial-1", "exec-out", "screencap", "-p"], timeout=5)

    def test_connect_and_restart_use_the_adb_binary(self):
        """Connect and restart issue the expected server commands."""
        client = AdbClient("adb")
        with patch("wosutil.adb.run_process_robust", return_value=subprocess.CompletedProcess(args=[], returncode=0)) as run:
            client.connect("serial-1")
            client.restart_server()
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [["adb", "connect", "serial-1"], ["adb", "kill-server"], ["adb", "start-server"]],
        )


if __name__ == "__main__":
    unittest.main()
