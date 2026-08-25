"""Unit tests for the profile manager default profile behavior."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from wosutil.config import DEFAULT_PROFILE_NAME
from wosutil.tool.profiles.profile_manager import ProfileManager
from wosutil.tool.tasks.task_definitions import get_all_task_ids


class TestProfileManagerDefaultProfile(unittest.TestCase):
    """Tests for automatic creation of the default 'All' profile."""

    def setUp(self):
        """Point the profiles file at a temporary location."""
        self.temp_dir = tempfile.mkdtemp()
        self.profiles_file = os.path.join(self.temp_dir, "profiles.json")
        patcher = patch("wosutil.tool.profiles.profile_manager.PROFILES_FILE", self.profiles_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        """Clean up the temporary directory."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_profile_created_when_file_missing(self):
        """A missing profiles file yields the default 'All' profile with all tasks."""
        pm = ProfileManager(log_func=lambda *args, **kwargs: None)
        self.assertIn(DEFAULT_PROFILE_NAME, pm.profiles)
        self.assertEqual(pm.profiles[DEFAULT_PROFILE_NAME], get_all_task_ids())
        self.assertTrue(os.path.exists(self.profiles_file))

    def test_default_profile_created_when_file_empty(self):
        """An empty profiles file yields the default 'All' profile."""
        with open(self.profiles_file, "w") as f:
            json.dump({}, f)
        pm = ProfileManager(log_func=lambda *args, **kwargs: None)
        self.assertEqual(set(pm.profiles), {DEFAULT_PROFILE_NAME})

    def test_existing_profiles_are_kept(self):
        """Existing profiles load untouched, with no default added."""
        existing = {"Custom": ["claim_idle"]}
        with open(self.profiles_file, "w") as f:
            json.dump(existing, f)
        pm = ProfileManager(log_func=lambda *args, **kwargs: None)
        self.assertEqual(pm.profiles, existing)

    def test_invalid_top_level_json_creates_a_default_profile(self):
        """A valid JSON list must not be treated as a profile mapping."""
        with patch(
            "wosutil.tool.profiles.profile_manager.load_json_file",
            return_value=["not", "profiles"],
        ), patch("wosutil.tool.profiles.profile_manager.save_json_file", return_value=True):
            pm = ProfileManager(log_func=lambda *args, **kwargs: None)

        self.assertEqual(pm.profiles, {DEFAULT_PROFILE_NAME: get_all_task_ids()})

    def test_invalid_profile_entries_are_ignored_but_valid_entries_are_kept(self):
        """Malformed profiles do not make the whole profile file unusable."""
        profiles = {
            "Good": ["claim_idle", 123, None],
            "Empty": [],
            "Wrong type": "claim_idle",
            "   ": ["claim_idle"],
        }
        with patch("wosutil.tool.profiles.profile_manager.load_json_file", return_value=profiles):
            pm = ProfileManager(log_func=lambda *args, **kwargs: None)

        self.assertEqual(pm.profiles, {"Good": ["claim_idle"], "Empty": []})


if __name__ == "__main__":
    unittest.main()
