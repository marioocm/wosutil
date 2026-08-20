"""Unit tests for the preferences module."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from wosutil.preferences import (
    GATHER_RESOURCES,
    MYSTERY_SHOP_LEVEL_FREE,
    MYSTERY_SHOP_LEVELS,
    get_gather_resource,
    get_kill_beast_march,
    get_kill_beast_march_assignment,
    get_mystery_shop_level,
    get_remember_schedule,
    get_requirements_reminder_seen,
    get_task_priorities,
    load_preferences,
    mark_requirements_reminder_seen,
    save_preferences,
)
from wosutil.tool.tasks.task_definitions import get_task_definitions


class TestPreferences(unittest.TestCase):
    """Test cases for the preferences module."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.prefs_file = os.path.join(self.temp_dir, "preferences.json")

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_task_priorities_empty(self):
        """Test that missing preferences yield no overrides."""
        self.assertEqual(get_task_priorities({}), {})
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertEqual(get_task_priorities(None), {})

    def test_get_task_priorities_valid(self):
        """Test that valid priorities are returned as integers."""
        preferences = {"task_priorities": {"claim_idle": 3, "donate_tech": 7}}
        self.assertEqual(get_task_priorities(preferences), {"claim_idle": 3, "donate_tech": 7})

    def test_get_task_priorities_invalid_values_skipped(self):
        """Test that non-positive or unparsable priorities are skipped."""
        preferences = {"task_priorities": {"claim_idle": 0, "donate_tech": -5, "autojoin": "invalid", "claim_mail": 4}}
        self.assertEqual(get_task_priorities(preferences), {"claim_mail": 4})

    def test_get_task_priorities_non_dict(self):
        """Test that a non-dict task_priorities value yields no overrides."""
        self.assertEqual(get_task_priorities({"task_priorities": ["claim_idle"]}), {})

    def test_get_kill_beast_march_default(self):
        """Test the default kill beasts march value."""
        self.assertEqual(get_kill_beast_march({}), 1)
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertEqual(get_kill_beast_march(None), 1)

    def test_get_gather_resource_default(self):
        """Test that missing resource preferences default to meat."""
        self.assertEqual(get_gather_resource({}), "meat")
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertEqual(get_gather_resource(None), "meat")

    def test_get_gather_resource_accepts_all_resources(self):
        """Test that each supported gathering resource can be selected."""
        for resource in GATHER_RESOURCES:
            self.assertEqual(get_gather_resource({"gather_resource": resource}), resource)

    def test_get_gather_resource_invalid_falls_back(self):
        """Test that invalid resource preferences fall back to meat."""
        self.assertEqual(get_gather_resource({"gather_resource": "gold"}), "meat")
        self.assertEqual(get_gather_resource({"gather_resource": None}), "meat")

    def test_get_kill_beast_march_valid(self):
        """Test that a valid march number is returned."""
        self.assertEqual(get_kill_beast_march({"kill_beast_march": 8}), 8)

    def test_get_kill_beast_march_clamped(self):
        """Test that out-of-range march numbers are clamped to 1-12."""
        self.assertEqual(get_kill_beast_march({"kill_beast_march": 0}), 1)
        self.assertEqual(get_kill_beast_march({"kill_beast_march": -3}), 1)
        self.assertEqual(get_kill_beast_march({"kill_beast_march": 20}), 12)
        self.assertEqual(get_kill_beast_march({"kill_beast_march": "invalid"}), 1)

    def test_get_kill_beast_march_assignment_missing(self):
        """Test that a missing march assignment yields None."""
        self.assertIsNone(get_kill_beast_march_assignment({}))
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertIsNone(get_kill_beast_march_assignment(None))

    def test_get_kill_beast_march_assignment_valid(self):
        """Test that an assigned march is returned."""
        self.assertEqual(get_kill_beast_march_assignment({"kill_beast_march": 8}), 8)
        self.assertEqual(get_kill_beast_march_assignment({"kill_beast_march": 1}), 1)

    def test_get_kill_beast_march_assignment_invalid(self):
        """Test that out-of-range or unparsable assignments yield None."""
        self.assertIsNone(get_kill_beast_march_assignment({"kill_beast_march": 0}))
        self.assertIsNone(get_kill_beast_march_assignment({"kill_beast_march": 13}))
        self.assertIsNone(get_kill_beast_march_assignment({"kill_beast_march": -2}))
        self.assertIsNone(get_kill_beast_march_assignment({"kill_beast_march": "invalid"}))

    def test_get_mystery_shop_level_default(self):
        """Test the default mystery shop level."""
        self.assertEqual(get_mystery_shop_level({}), MYSTERY_SHOP_LEVEL_FREE)
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertEqual(get_mystery_shop_level(None), MYSTERY_SHOP_LEVEL_FREE)

    def test_get_mystery_shop_level_valid(self):
        """Test that a valid level is returned."""
        for level in MYSTERY_SHOP_LEVELS:
            self.assertEqual(get_mystery_shop_level({"mystery_shop_level": level}), level)

    def test_get_mystery_shop_level_invalid_falls_back(self):
        """Test that invalid levels fall back to the default."""
        self.assertEqual(get_mystery_shop_level({"mystery_shop_level": "widgets_99"}), MYSTERY_SHOP_LEVEL_FREE)
        self.assertEqual(get_mystery_shop_level({"mystery_shop_level": 50}), MYSTERY_SHOP_LEVEL_FREE)
        self.assertEqual(get_mystery_shop_level({"mystery_shop_level": None}), MYSTERY_SHOP_LEVEL_FREE)

    def test_save_and_load_roundtrip(self):
        """Test saving preferences and loading them back."""
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            preferences = {"task_priorities": {"claim_idle": 2}, "kill_beast_march": 5}

            self.assertTrue(save_preferences(preferences))
            self.assertEqual(load_preferences(), preferences)

        with open(self.prefs_file, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual(raw, preferences)

    def test_requirements_reminder_defaults_to_not_seen(self):
        """Test that the reminder flag defaults to not seen."""
        self.assertFalse(get_requirements_reminder_seen({}))
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertFalse(get_requirements_reminder_seen(None))

    def test_requirements_reminder_seen_when_flagged(self):
        """Test that the reminder flag is honored when set."""
        self.assertTrue(get_requirements_reminder_seen({"requirements_reminder_seen": True}))

    def test_requirements_reminder_mark_persists(self):
        """Test that marking the reminder seen persists and is reloaded."""
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertTrue(mark_requirements_reminder_seen())
            self.assertTrue(get_requirements_reminder_seen(None))

    def test_remember_schedule_defaults_to_enabled(self):
        """Test that the schedule memory defaults to enabled."""
        self.assertTrue(get_remember_schedule({}))
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertTrue(get_remember_schedule(None))

    def test_remember_schedule_disabled_when_false(self):
        """Test that an explicit False disables the schedule memory."""
        self.assertFalse(get_remember_schedule({"remember_schedule": False}))

    def test_remember_schedule_ignores_garbage_values(self):
        """Test that garbage values fall back to the default (enabled)."""
        self.assertTrue(get_remember_schedule({"remember_schedule": "no"}))
        self.assertTrue(get_remember_schedule({"remember_schedule": 0}))
        self.assertTrue(get_remember_schedule({"remember_schedule": None}))


class TestTaskDefinitionsPriorities(unittest.TestCase):
    """Test cases for user priorities applied to task definitions."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.prefs_file = os.path.join(self.temp_dir, "preferences.json")

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_preferences(self, preferences):
        with open(self.prefs_file, "w", encoding="utf-8") as f:
            json.dump(preferences, f)

    def test_user_priorities_override_defaults(self):
        """Test that user-defined priorities override the defaults."""
        preferences = {"task_priorities": {"claim_triumph": 1, "claim_idle": 15}, "kill_beast_march": 4}
        self._write_preferences(preferences)

        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            task_defs = get_task_definitions(None)
        self.assertEqual(task_defs["claim_triumph"]["priority"], 1)
        self.assertEqual(task_defs["claim_idle"]["priority"], 15)

    def test_defaults_when_no_preferences(self):
        """Test that default priorities remain when no preferences exist."""
        self.assertFalse(os.path.exists(self.prefs_file))

        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            task_defs = get_task_definitions(None)
        self.assertEqual(task_defs["claim_idle"]["priority"], 4)
        self.assertEqual(task_defs["claim_triumph"]["priority"], 17)

    def test_stale_priority_ids_ignored(self):
        """Test that priorities for unknown task IDs are ignored."""
        self._write_preferences({"task_priorities": {"nonexistent_task": 1}})

        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            task_defs = get_task_definitions(None)
        self.assertEqual(task_defs["claim_idle"]["priority"], 4)

    def test_mystery_shop_task_registered(self):
        """Test that the mystery shop task is registered with a function."""
        task_defs = get_task_definitions(None)
        self.assertIn("claim_mystery_shop", task_defs)
        self.assertEqual(task_defs["claim_mystery_shop"]["category"], "shop")


if __name__ == "__main__":
    unittest.main()
