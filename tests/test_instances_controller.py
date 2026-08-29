"""Unit tests for the emulator instance cache boundary."""

import unittest
from unittest.mock import patch

from wosutil.emulator.instances_controller import MultiInstanceManager, load_instance_cache


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


class TestStartInstanceOnLaunchHook(unittest.TestCase):
    """The on_launch callback fires right after the launch command."""

    def test_on_launch_runs_after_the_launch_command(self):
        """The callback is invoked after launch_player and before polling."""
        manager = MultiInstanceManager(log_func=lambda *a, **k: None)
        order = []
        with patch.object(manager, "_execute_mumu_cli", side_effect=lambda args: order.append(("cli", args)) or ""), patch.object(manager, "_is_instance_running", return_value=True):
            result = manager.start_instance(0, on_launch=lambda: order.append(("on_launch", None)))

        self.assertTrue(result)
        self.assertEqual([step for step, _ in order], ["cli", "on_launch"])

    def test_start_without_callback_still_works(self):
        """The callback parameter is optional and backward compatible."""
        manager = MultiInstanceManager(log_func=lambda *a, **k: None)
        with patch.object(manager, "_execute_mumu_cli", return_value=""), patch.object(manager, "_is_instance_running", return_value=True):
            self.assertTrue(manager.start_instance(0))


if __name__ == "__main__":
    unittest.main()
