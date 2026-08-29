"""Unit tests for the emulator instance cache boundary."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from wosutil.emulator.instances_controller import MultiInstanceManager, load_instance_cache


def vm_dir_helper(folders, names=None):
    """Create a temp MuMu vms dir holding the given instance folders.

    Args:
        folders (list): Instance folder names, e.g. ["MuMuPlayerGlobal-15.0-0"].
        names (dict, optional): playerName by folder, e.g. {"MuMuPlayerGlobal-15.0-0": "Healer"}.
    """
    directory = tempfile.mkdtemp(suffix=".vms")
    for folder in folders:
        configs = os.path.join(directory, folder, "configs")
        os.makedirs(configs, exist_ok=True)
        name = (names or {}).get(folder, "Instance")
        with open(os.path.join(configs, "extra_config.json"), "w", encoding="utf-8") as f:
            json.dump({"playerName": name}, f)
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
        self.manager = MultiInstanceManager(log_func=lambda *a, **k: None, instance_base_path=self.vms)

    def test_vm_name_matches_the_instance_folder(self):
        """The folder of the instance series is used as the VM name."""
        self.assertEqual(self.manager._vm_name(0), "MuMuPlayerGlobal-15.0-0")
        self.assertEqual(self.manager._vm_name(1), "MuMuPlayerGlobal-12.0-1")

    def test_vm_name_falls_back_when_the_folder_is_missing(self):
        """A missing folder falls back to the 12.0 series guess."""
        manager = MultiInstanceManager(log_func=lambda *a, **k: None, instance_base_path="nonexistent_dir")
        self.assertEqual(manager._vm_name(2), "MuMuPlayerGlobal-12.0-2")

    def test_device_executable_uses_the_instance_series(self):
        """Each series resolves to its own nx_device shell next to vms."""
        expected = os.path.normpath(os.path.join(os.path.dirname(self.vms), "nx_device", "15.0", "shell", "MuMuNxDevice.exe"))
        self.assertEqual(self.manager._device_executable(0), expected)

    def test_device_argv_launches_the_vm_directly(self):
        """The launch argv targets the instance folder without the manager."""
        expected = [
            os.path.normpath(os.path.join(os.path.dirname(self.vms), "nx_device", "15.0", "shell", "MuMuNxDevice.exe")),
            "-v",
            "0",
            "--vm",
            "MuMuPlayerGlobal-15.0-0",
        ]
        self.assertEqual(self.manager._device_argv(0), expected)


class TestListInstances(unittest.TestCase):
    """Instances are discovered by scanning the vms folder."""

    def test_get_instances_reads_folders_and_names(self):
        """Indices come from the folder names, names from extra_config.json."""
        self.vms = vm_dir_helper(
            ["MuMuPlayerGlobal-15.0-0", "MuMuPlayerGlobal-12.0-1", "not-an-instance"],
            {"MuMuPlayerGlobal-15.0-0": "Healer", "MuMuPlayerGlobal-12.0-1": "Antnee"},
        )
        manager = MultiInstanceManager(log_func=lambda *a, **k: None, instance_base_path=self.vms)
        with patch("wosutil.emulator.instances_controller.save_instance_cache") as mock_save:
            instances = manager.get_instances()

        self.assertEqual(instances, [{"index": 0, "name": "Healer"}, {"index": 1, "name": "Antnee"}])
        mock_save.assert_called_once_with(instances)

    def test_get_instances_missing_dir_returns_empty(self):
        """A missing vms dir yields no instances."""
        manager = MultiInstanceManager(log_func=lambda *a, **k: None, instance_base_path="nonexistent_dir")
        self.assertEqual(manager.get_instances(), [])


class TestProcessMatching(unittest.TestCase):
    """The instance is matched to its shell and hypervisor processes."""

    def setUp(self):
        """Create a manager bound to a temp vms dir with one instance."""
        self.vms = vm_dir_helper(["MuMuPlayerGlobal-15.0-0"])
        self.manager = MultiInstanceManager(log_func=lambda *a, **k: None, instance_base_path=self.vms)

    def test_matches_device_and_hypervisor_processes(self):
        """The shell and the hypervisor both identify the instance."""
        with patch(
            "wosutil.emulator.instances_controller._iter_processes",
            return_value=[
                (object(), "MuMuNxDevice.exe", ["MuMuNxDevice.exe", "-v", "0", "--vm", "MuMuPlayerGlobal-15.0-0"]),
                (object(), "MuMuVMMHeadless.exe", ["MuMuVMMHeadless.exe", "--comment", "MuMuPlayerGlobal-15.0-0", "--startvm", "abc"]),
                (object(), "MuMuVMMHeadless.exe", ["MuMuVMMHeadless.exe", "--comment", "MuMuPlayerGlobal-12.0-10", "--startvm", "def"]),
            ],
        ):
            matches = self.manager._matching_processes(0)

        self.assertEqual(len(matches), 2)

    def test_other_instance_does_not_match(self):
        """Index 1 does not match a folder belonging to instance 10."""
        with patch(
            "wosutil.emulator.instances_controller._iter_processes",
            return_value=[
                (object(), "MuMuNxDevice.exe", ["MuMuNxDevice.exe", "-v", "10", "--vm", "MuMuPlayerGlobal-15.0-10"]),
            ],
        ):
            matches = self.manager._matching_processes(1)

        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
