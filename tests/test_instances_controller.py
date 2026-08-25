"""Unit tests for the emulator instance cache boundary."""

import unittest
from unittest.mock import patch

from wosutil.emulator.instances_controller import load_instance_cache


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


if __name__ == "__main__":
    unittest.main()
