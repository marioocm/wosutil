"""Unit tests for the preferences module."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from wosutil.preferences import (
    BEAR_TRAP_MARCH_COUNT,
    BEAR_TRAP_MARCH_MAX,
    BEAR_TRAP_MARCH_MIN,
    DEFAULT_EMULATOR_PATHS,
    GATHER_RESOURCES,
    MYSTERY_SHOP_LEVEL_FREE,
    MYSTERY_SHOP_LEVELS,
    get_bear_rally_call_march,
    get_bear_trap_marches,
    get_emulator_paths,
    get_gather_resource,
    get_kill_beast_march,
    get_kill_beast_march_assignment,
    get_mystery_shop_level,
    get_remember_schedule,
    get_requirements_reminder_seen,
    get_start_minimized,
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

    def test_emulator_paths_default_to_standard_locations(self):
        """Missing emulator path settings use the standard locations."""
        self.assertEqual(get_emulator_paths({}), DEFAULT_EMULATOR_PATHS)

    def test_emulator_paths_normalize_valid_values_and_ignore_invalid_values(self):
        """Custom paths are normalized while malformed values use defaults."""
        preferences = {
            "emulator_paths": {
                "mumu": {"base_path": "D:/MuMu/../MuMuPlayer", "instance_base_path": 123},
                "bluestacks": {"base_path": "  E:/BlueStacks  ", "config_path": ""},
            }
        }
        paths = get_emulator_paths(preferences)
        self.assertEqual(paths["mumu"]["base_path"], os.path.normpath("D:/MuMuPlayer"))
        self.assertEqual(paths["mumu"]["instance_base_path"], DEFAULT_EMULATOR_PATHS["mumu"]["instance_base_path"])
        self.assertEqual(paths["bluestacks"]["base_path"], os.path.normpath("E:/BlueStacks"))
        self.assertEqual(paths["bluestacks"]["config_path"], DEFAULT_EMULATOR_PATHS["bluestacks"]["config_path"])

    def test_get_task_priorities_empty(self):
        """Test that missing preferences yield no overrides."""
        self.assertEqual(get_task_priorities({}), {})
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertEqual(get_task_priorities(None), {})

    def test_start_minimized_defaults_to_enabled(self):
        """Emulators are minimized by default so they never steal focus."""
        self.assertTrue(get_start_minimized({}))
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertTrue(get_start_minimized(None))

    def test_start_minimized_honors_the_saved_value(self):
        """The saved preference wins over the default."""
        self.assertFalse(get_start_minimized({"start_minimized": False}))
        self.assertTrue(get_start_minimized({"start_minimized": True}))

    def test_start_minimized_persists_across_sessions(self):
        """Disabling the preference is saved and reloaded."""
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            preferences = load_preferences() or {}
            preferences["start_minimized"] = False
            self.assertTrue(save_preferences(preferences))
            self.assertFalse(get_start_minimized(None))

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

    def test_get_bear_trap_marches_default(self):
        """Test the default bear trap marches."""
        self.assertEqual(get_bear_trap_marches({}), [1, 2, 3, 4, 5, 6])
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertEqual(get_bear_trap_marches(None), [1, 2, 3, 4, 5, 6])

    def test_get_bear_trap_marches_valid(self):
        """Test that valid marches are returned."""
        self.assertEqual(get_bear_trap_marches({"bear_trap_marches": [3, 9, 12, 1, 5, 7]}), [3, 9, 12, 1, 5, 7])

    def test_get_bear_trap_marches_invalid_values_skipped(self):
        """Test that out-of-range or unparsable marches are replaced."""
        marches = get_bear_trap_marches({"bear_trap_marches": [0, 13, "x", 4, 6, 8]})
        self.assertEqual(len(marches), BEAR_TRAP_MARCH_COUNT)
        self.assertIn(4, marches)
        self.assertIn(6, marches)
        self.assertIn(8, marches)
        for march in marches:
            self.assertTrue(BEAR_TRAP_MARCH_MIN <= march <= BEAR_TRAP_MARCH_MAX)

    def test_get_bear_trap_marches_padded_when_short(self):
        """Test that fewer than six marches are padded with unused numbers."""
        marches = get_bear_trap_marches({"bear_trap_marches": [12]})
        self.assertEqual(len(marches), BEAR_TRAP_MARCH_COUNT)
        self.assertEqual(marches[0], 12)
        self.assertEqual(len(set(marches)), BEAR_TRAP_MARCH_COUNT)

    def test_get_bear_trap_marches_truncated_when_long(self):
        """Test that more than six marches are truncated to six."""
        marches = get_bear_trap_marches({"bear_trap_marches": [1, 2, 3, 4, 5, 6, 7, 8]})
        self.assertEqual(marches, [1, 2, 3, 4, 5, 6])

    def test_get_bear_rally_call_march_default(self):
        """Test the default bear rally call march value."""
        self.assertEqual(get_bear_rally_call_march({}), 1)
        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            self.assertEqual(get_bear_rally_call_march(None), 1)

    def test_get_bear_rally_call_march_valid(self):
        """Test that a valid march is returned."""
        self.assertEqual(get_bear_rally_call_march({"bear_rally_call_march": 5}), 5)

    def test_get_bear_rally_call_march_below_min(self):
        """Test that a march below the minimum is clamped to the minimum."""
        self.assertEqual(get_bear_rally_call_march({"bear_rally_call_march": 0}), 1)

    def test_get_bear_rally_call_march_above_max(self):
        """Test that a march above the maximum is clamped to the maximum."""
        self.assertEqual(get_bear_rally_call_march({"bear_rally_call_march": 15}), 12)

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
            task_defs = get_task_definitions()
        self.assertEqual(task_defs["claim_triumph"]["priority"], 1)
        self.assertEqual(task_defs["claim_idle"]["priority"], 15)

    def test_defaults_when_no_preferences(self):
        """Test that default priorities remain when no preferences exist."""
        self.assertFalse(os.path.exists(self.prefs_file))

        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            task_defs = get_task_definitions()
        self.assertEqual(task_defs["claim_idle"]["priority"], 4)
        self.assertEqual(task_defs["claim_triumph"]["priority"], 17)

    def test_stale_priority_ids_ignored(self):
        """Test that priorities for unknown task IDs are ignored."""
        self._write_preferences({"task_priorities": {"nonexistent_task": 1}})

        with patch("wosutil.preferences.PREFERENCES_FILE", self.prefs_file):
            task_defs = get_task_definitions()
        self.assertEqual(task_defs["claim_idle"]["priority"], 4)

    def test_mystery_shop_task_registered(self):
        """Test that the mystery shop task is registered with a function."""
        task_defs = get_task_definitions()
        self.assertIn("claim_mystery_shop", task_defs)
        self.assertEqual(task_defs["claim_mystery_shop"]["category"], "shop")


if __name__ == "__main__":
    unittest.main()
