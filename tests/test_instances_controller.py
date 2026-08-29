"""Unit tests for the emulator instance cache boundary."""

import os
import tempfile
import unittest
from unittest.mock import patch

from wosutil.emulator.instances_controller import MultiInstanceManager, load_instance_cache


def vm_dir_helper(folders):
    """Create a temp MuMu vms dir holding the given instance folders."""
    directory = tempfile.mkdtemp(suffix=".vms")
    for folder in folders:
        os.makedirs(os.path.join(directory, folder, "configs"), exist_ok=True)
    return directory


class TestLoadInstanceCache(unittest.TestCase):
    """Malformed cache entries must not reach the GUI."""

    def test_keeps_valid_entries_and_deduplicates_indices(self):
        """Only the first valid entry for each instance index is kept."""
        cached = [
            {"index": 1, "name": "Second", "extra": "ignored"},
            {"index": 1, "name": "Duplicate"},
            {"index": 0, "name": "First"},
            {"index": True, "name": "Boolean index"},
            {"index": -1, "name": "Negative index"},
            {"index": 2, "name": ""},
            {"index": "3", "name": "String index"},
            "not an instance",
        ]
        with patch("wosutil.emulator.instances_controller.load_json_file", return_value=cached):
            result = load_instance_cache()

        self.assertEqual(result, [{"index": 1, "name": "Second"}, {"index": 0, "name": "First"}])

    def test_non_list_cache_returns_empty(self):
        """A valid JSON object is not a usable instance cache."""
        with patch("wosutil.emulator.instances_controller.load_json_file", return_value={"0": {}}):
            self.assertEqual(load_instance_cache(), [])


class TestVmResolution(unittest.TestCase):
    """The instance folder and its shell are resolved per series."""

    def setUp(self):
        """Create a manager bound to a temp vms dir with two series."""
        self.vms = vm_dir_helper(["MuMuPlayerGlobal-15.0-0", "MuMuPlayerGlobal-12.0-1"])
        self.manager = MultiInstanceManager(
            log_func=lambda *a, **k: None,
            multi_player_path="C:/MuMu/nx_main/MuMuManager.exe",
            instance_base_path=self.vms,
        )

    def test_vm_name_matches_the_instance_folder(self):
        """The folder of the instance series is used as the VM name."""
        self.assertEqual(self.manager._vm_name(0), "MuMuPlayerGlobal-15.0-0")
        self.assertEqual(self.manager._vm_name(1), "MuMuPlayerGlobal-12.0-1")

    def test_vm_name_falls_back_when_the_folder_is_missing(self):
        """A missing folder falls back to the 12.0 series guess."""
        manager = MultiInstanceManager(log_func=lambda *a, **k: None, instance_base_path="nonexistent_dir")
        self.assertEqual(manager._vm_name(2), "MuMuPlayerGlobal-12.0-2")

    def test_device_executable_uses_the_instance_series(self):
        """Each series resolves to its own nx_device shell."""
        self.assertEqual(
            self.manager._device_executable(0),
            os.path.normpath("C:/MuMu/nx_device/15.0/shell/MuMuNxDevice.exe"),
        )
        self.assertEqual(
            self.manager._device_executable(1),
            os.path.normpath("C:/MuMu/nx_device/12.0/shell/MuMuNxDevice.exe"),
        )

    def test_device_argv_launches_the_vm_directly(self):
        """The launch argv targets the instance folder without the manager."""
        self.assertEqual(
            self.manager._device_argv(0),
            [
                os.path.normpath("C:/MuMu/nx_device/15.0/shell/MuMuNxDevice.exe"),
                "-v",
                "0",
                "--vm",
                "MuMuPlayerGlobal-15.0-0",
            ],
        )


class TestStartInstanceOnLaunchHook(unittest.TestCase):
    """The on_launch callback fires right after the direct launch."""

    def setUp(self):
        """Create a manager bound to a temp vms dir with one instance."""
        self.vms = vm_dir_helper(["MuMuPlayerGlobal-15.0-0"])
        self.manager = MultiInstanceManager(
            log_func=lambda *a, **k: None,
            multi_player_path="C:/MuMu/nx_main/MuMuManager.exe",
            instance_base_path=self.vms,
        )

    def test_on_launch_runs_after_the_launch_command(self):
        """The callback is invoked after the device launch and before polling."""
        order = []
        with patch("subprocess.Popen") as mock_popen, patch.object(self.manager, "_is_instance_running", side_effect=[False, True]):
            result = self.manager.start_instance(0, on_launch=lambda: order.append("on_launch"))

        self.assertTrue(result)
        self.assertEqual(order, ["on_launch"])
        mock_popen.assert_called_once()
        self.assertEqual(
            mock_popen.call_args.args[0],
            [os.path.normpath("C:/MuMu/nx_device/15.0/shell/MuMuNxDevice.exe"), "-v", "0", "--vm", "MuMuPlayerGlobal-15.0-0"],
        )

    def test_start_without_callback_still_works(self):
        """The callback parameter is optional and backward compatible."""
        with patch("subprocess.Popen"), patch.object(self.manager, "_is_instance_running", side_effect=[False, True]):
            self.assertTrue(self.manager.start_instance(0))

    def test_already_running_skips_the_launch(self):
        """A running instance is not launched a second time."""
        with patch("subprocess.Popen") as mock_popen, patch.object(self.manager, "_is_instance_running", return_value=True):
            result = self.manager.start_instance(0, on_launch=lambda: None)
        self.assertTrue(result)
        mock_popen.assert_not_called()

    def test_is_running_matches_the_device_process(self):
        """Only the MuMuNxDevice process of this VM counts as running."""
        with patch(
            "wosutil.emulator.instances_controller._iter_processes",
            return_value=[
                (object(), "MuMuNxDevice.exe", ["MuMuNxDevice.exe", "-v", "0", "--vm", "MuMuPlayerGlobal-15.0-0"]),
                (object(), "MuMuNxDevice.exe", ["MuMuNxDevice.exe", "-v", "0", "--vm", "MuMuPlayerGlobal-15.0-00"]),
            ],
        ):
            self.assertTrue(self.manager._is_instance_running(0))


if __name__ == "__main__":
    unittest.main()
