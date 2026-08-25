"""Profile manager module.

Manages loading, saving, and editing task profiles.
"""

import logging

from wosutil.config import DEFAULT_PROFILE_NAME, PROFILES_FILE
from wosutil.tool.tasks.task_definitions import get_all_task_ids
from wosutil.utils import load_json_file, save_json_file

logger = logging.getLogger(__name__)


class ProfileManager:
    """Manages loading and saving task profiles with improved error handling."""

    def __init__(self, log_func=None):
        """Initializes the profile manager.

        Args:
            log_func (callable, optional): Function to log messages to the UI.
        """
        self.log = log_func or logger.info
        self.profiles = self.load_profiles()
        if not self.profiles:
            self.log(
                f"No profiles found. Creating the default '{DEFAULT_PROFILE_NAME}' profile with all tasks.",
                "info",
            )
            self.profiles = {DEFAULT_PROFILE_NAME: get_all_task_ids()}
            self.save_profiles()
        self.running_tasks_state = []
        self.next_run_time = None
        self.current_task_name = None
        self.opening_state = False

    def load_profiles(self):
        """Loads profiles from the JSON file using utility functions.

        Returns:
            dict: Dictionary of profiles.
        """
        profiles = load_json_file(PROFILES_FILE, default_value={})
        if not profiles:
            self.log(f"Profiles file not found at {PROFILES_FILE}. A new one will be created.", "info")
        else:
            self.log("Profiles loaded successfully.", "info")
        return profiles

    def save_profiles(self):
        """Saves the current profiles to the JSON file using utility functions.

        Returns:
            bool: True if saved successfully.
        """
        if save_json_file(PROFILES_FILE, self.profiles):
            self.log("Profiles saved successfully.", "info")
            return True
        else:
            self.log("Error saving profiles.", "error")
            return False
