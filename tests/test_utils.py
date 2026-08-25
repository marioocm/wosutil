"""Unit tests for utility functions."""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from wosutil.stop import StopSignal, ToolStopped
from wosutil.utils import (
    ensure_directory_exists,
    load_json_file,
    retry_operation,
    safe_int,
    save_json_file,
)


class TestUtils(unittest.TestCase):
    """Test cases for utility functions."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.temp_dir, "test.json")

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ensure_directory_exists(self):
        """Test directory creation."""
        test_dir = os.path.join(self.temp_dir, "new_dir", "sub_dir")

        # Test creating new directory
        result = ensure_directory_exists(test_dir)
        self.assertTrue(result)
        self.assertTrue(os.path.exists(test_dir))

        # Test existing directory
        result = ensure_directory_exists(test_dir)
        self.assertTrue(result)

    def test_load_json_file_success(self):
        """Test successful JSON file loading."""
        test_data = {"key": "value", "number": 42}
        with open(self.test_file, "w") as f:
            json.dump(test_data, f)

        result = load_json_file(self.test_file)
        self.assertEqual(result, test_data)

    def test_load_json_file_not_found(self):
        """Test loading non-existent JSON file."""
        result = load_json_file("nonexistent.json")
        self.assertIsNone(result)

    def test_load_json_file_invalid_json(self):
        """Test loading invalid JSON file."""
        with open(self.test_file, "w") as f:
            f.write("invalid json content")

        result = load_json_file(self.test_file)
        self.assertIsNone(result)

    def test_load_json_file_with_default(self):
        """Test loading JSON file with default value."""
        default_value = {"default": "value"}
        result = load_json_file("nonexistent.json", default_value)
        self.assertEqual(result, default_value)

    def test_save_json_file_success(self):
        """Test successful JSON file saving."""
        test_data = {"key": "value", "number": 42}

        result = save_json_file(self.test_file, test_data)
        self.assertTrue(result)

        # Verify file was created and contains correct data
        with open(self.test_file) as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data, test_data)

    def test_save_json_file_creates_directory(self):
        """Test that save_json_file creates parent directory."""
        nested_file = os.path.join(self.temp_dir, "nested", "dir", "test.json")
        test_data = {"key": "value"}

        result = save_json_file(nested_file, test_data)
        self.assertTrue(result)
        self.assertTrue(os.path.exists(nested_file))

    def test_save_json_file_preserves_previous_data_on_serialization_failure(self):
        """A failed save must not truncate the existing JSON file."""
        original_data = {"existing": True}
        with open(self.test_file, "w", encoding="utf-8") as f:
            json.dump(original_data, f)

        self.assertFalse(save_json_file(self.test_file, {"invalid": object()}))

        self.assertEqual(load_json_file(self.test_file), original_data)
        self.assertEqual(os.listdir(self.temp_dir), ["test.json"])

    def test_safe_int_valid(self):
        """Test safe_int with valid values."""
        self.assertEqual(safe_int("42"), 42)
        self.assertEqual(safe_int(42), 42)
        self.assertEqual(safe_int(42.0), 42)

    def test_safe_int_invalid(self):
        """Test safe_int with invalid values."""
        self.assertEqual(safe_int("invalid"), 0)
        self.assertEqual(safe_int(None), 0)
        self.assertEqual(safe_int("invalid", 10), 10)

    def test_retry_operation_success(self):
        """Test retry_operation with successful operation."""
        mock_operation = MagicMock(return_value="success")

        result = retry_operation(mock_operation, max_attempts=3)
        self.assertEqual(result, "success")
        self.assertEqual(mock_operation.call_count, 1)

    def test_retry_operation_failure_then_success(self):
        """Test retry_operation with failure then success."""
        mock_operation = MagicMock(side_effect=[Exception("fail"), Exception("fail"), "success"])

        result = retry_operation(mock_operation, max_attempts=3, delay=0.1)
        self.assertEqual(result, "success")
        self.assertEqual(mock_operation.call_count, 3)

    def test_retry_operation_can_retry_false_results(self):
        """Boolean failures are retried when explicitly requested."""
        mock_operation = MagicMock(side_effect=[False, False, "success"])

        result = retry_operation(mock_operation, max_attempts=3, delay=0, retry_on_false=True)

        self.assertEqual(result, "success")
        self.assertEqual(mock_operation.call_count, 3)

    def test_retry_operation_returns_false_after_false_results_are_exhausted(self):
        """The final boolean failure is returned instead of raised."""
        mock_operation = MagicMock(return_value=False)

        result = retry_operation(mock_operation, max_attempts=3, delay=0, retry_on_false=True)

        self.assertFalse(result)
        self.assertEqual(mock_operation.call_count, 3)

    def test_retry_operation_rejects_invalid_configuration(self):
        """Invalid retry settings fail early with a useful error."""
        with self.assertRaises(ValueError):
            retry_operation(lambda: True, max_attempts=0)
        with self.assertRaises(ValueError):
            retry_operation(lambda: True, delay=-1)

    def test_retry_operation_all_failures(self):
        """Test retry_operation with all failures."""
        mock_operation = MagicMock(side_effect=Exception("fail"))

        with self.assertRaises(Exception):  # noqa: B017
            retry_operation(mock_operation, max_attempts=2, delay=0.1)

        self.assertEqual(mock_operation.call_count, 2)

    def test_retry_operation_tool_stopped_not_retried(self):
        """Test that retry_operation does not retry when ToolStopped is raised."""
        mock_operation = MagicMock(side_effect=ToolStopped)

        with self.assertRaises(ToolStopped):
            retry_operation(mock_operation, max_attempts=3, delay=0.1)

        self.assertEqual(mock_operation.call_count, 1)


class TestStopSignal(unittest.TestCase):
    """Test cases for the StopSignal cooperative cancellation mechanism."""

    def test_check_raises_when_set(self):
        """Test that check() raises ToolStopped once the signal is set."""
        signal = StopSignal()
        signal.set()
        with self.assertRaises(ToolStopped):
            signal.check()

    def test_check_ok_when_clear(self):
        """Test that check() does nothing while the signal is clear."""
        signal = StopSignal()
        self.assertIsNone(signal.check())

    def test_is_set_and_clear(self):
        """Test is_set() and clear() round trip."""
        signal = StopSignal()
        self.assertFalse(signal.is_set())
        signal.set()
        self.assertTrue(signal.is_set())
        signal.clear()
        self.assertFalse(signal.is_set())

    def test_wait_returns_true_when_set(self):
        """Test that wait() returns True when the signal is set."""
        signal = StopSignal()
        signal.set()
        self.assertTrue(signal.wait(timeout=0.1))

    def test_wait_timeout_returns_false(self):
        """Test that wait() returns False on timeout while the signal is clear."""
        signal = StopSignal()
        self.assertFalse(signal.wait(timeout=0.05))


if __name__ == "__main__":
    unittest.main()
