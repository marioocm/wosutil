"""Tests for the preference validation shared by every view."""

import unittest
from unittest.mock import patch

from wosutil.app.preferences import apply_preferences

TASKS = {
    "a": {"id": "a", "name": "Task A", "priority": 2},
    "b": {"id": "b", "name": "Task B", "priority": 1},
}


class ApplyPreferencesTests(unittest.TestCase):
    """Cover the rules a view cannot enforce on its own."""

    def _apply(self, values, saved=None):
        """Run apply_preferences with an in-memory preferences file."""
        stored = {} if saved is None else dict(saved)
        captured = {}
        logs = []

        def fake_save(preferences):
            captured.update(preferences)
            return True

        with (
            patch("wosutil.app.preferences.load_preferences", return_value=stored),
            patch("wosutil.app.preferences.save_preferences", side_effect=fake_save),
        ):
            result = apply_preferences(values, {key: dict(value) for key, value in TASKS.items()}, lambda message, level="info": logs.append((message, level)))
        return result, captured, logs

    def test_marches_stay_unassigned_until_set(self):
        """An untouched march must not be persisted as an explicit value."""
        result, saved, _logs = self._apply({"emulator": "mumu"})
        self.assertTrue(result.ok)
        self.assertNotIn("kill_beast_march", saved)
        self.assertNotIn("bear_rally_call_march", saved)

    def test_invalid_marches_keep_the_saved_values(self):
        """Out-of-range input falls back instead of dropping the stored value."""
        result, saved, logs = self._apply(
            {"kill_beast_march": 99, "kill_beast_march_set": True, "bear_trap_marches": [0, 1, 2, 3, 4, 5]},
            saved={"bear_trap_marches": [1, 2, 3, 4, 5, 6]},
        )
        self.assertTrue(result.ok)
        self.assertEqual(saved["kill_beast_march"], 1)
        self.assertEqual(saved["bear_trap_marches"], [1, 2, 3, 4, 5, 6])
        self.assertTrue(any("between" in message for message, _level in logs))

    def test_task_order_updates_the_definitions(self):
        """Saving the order rewrites both the definitions and the preferences."""
        tasks = {key: dict(value) for key, value in TASKS.items()}
        captured = {}
        with (
            patch("wosutil.app.preferences.load_preferences", return_value={}),
            patch("wosutil.app.preferences.save_preferences", side_effect=lambda preferences: captured.update(preferences) or True),
        ):
            result = apply_preferences({"task_order": ["b", "a"]}, tasks, lambda *_: None)
        self.assertTrue(result.ok)
        self.assertEqual(tasks["b"]["priority"], 1)
        self.assertEqual(tasks["a"]["priority"], 2)
        self.assertEqual(captured["task_priorities"], {"b": 1, "a": 2})

    def test_emulator_change_is_reported(self):
        """A different emulator marks the backend for replacement."""
        result, saved, _logs = self._apply({"emulator": "ldplayer"}, saved={"emulator": "mumu"})
        self.assertTrue(result.ok)
        self.assertTrue(result.emulator_changed)
        self.assertEqual(result.emulator, "ldplayer")
        self.assertIn("ldplayer", saved["emulator_paths"])
