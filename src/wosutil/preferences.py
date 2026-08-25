"""User preferences module.

Persists user-level settings such as task priorities and the march used to
kill beasts, so they can be changed from the Preferences tab instead of
editing task_definitions.py.
"""

import os

from wosutil.config import (
    BLUESTACKS_BASE_PATH,
    BLUESTACKS_CONF,
    LDPLAYER_BASE_PATH,
    LDPLAYER_INSTANCE_CONFIG_DIR,
    MUMU_BASE_PATH,
    MUMU_INSTANCE_BASE_PATH,
    PREFERENCES_FILE,
)
from wosutil.emulator.backends import EMULATOR_BLUESTACKS, EMULATOR_LDPLAYER, EMULATOR_MUMU
from wosutil.utils import load_json_file, safe_int, save_json_file

KILL_BEAST_MARCH_MIN = 1
KILL_BEAST_MARCH_MAX = 12
DEFAULT_KILL_BEAST_MARCH = 1

MYSTERY_SHOP_LEVEL_FREE = "free"
MYSTERY_SHOP_LEVEL_WIDGETS_50 = "widgets_50"
MYSTERY_SHOP_LEVEL_WIDGETS_20 = "widgets_20"
MYSTERY_SHOP_LEVELS = (MYSTERY_SHOP_LEVEL_FREE, MYSTERY_SHOP_LEVEL_WIDGETS_50, MYSTERY_SHOP_LEVEL_WIDGETS_20)
DEFAULT_MYSTERY_SHOP_LEVEL = MYSTERY_SHOP_LEVEL_FREE

GATHER_RESOURCES = ("meat", "wood", "coal", "iron")
DEFAULT_GATHER_RESOURCE = "meat"

DEBUG_MODE_ENV = "WOSUTIL_DEBUG"

DEFAULT_EMULATOR_PATHS = {
    EMULATOR_MUMU: {
        "base_path": MUMU_BASE_PATH,
        "instance_base_path": MUMU_INSTANCE_BASE_PATH,
    },
    EMULATOR_BLUESTACKS: {
        "base_path": BLUESTACKS_BASE_PATH,
        "config_path": BLUESTACKS_CONF,
    },
    EMULATOR_LDPLAYER: {
        "base_path": LDPLAYER_BASE_PATH,
        "instance_config_dir": LDPLAYER_INSTANCE_CONFIG_DIR,
    },
}


def get_remember_schedule(preferences=None):
    """Return whether the task schedule is remembered between sessions.

    When enabled (default) the scheduler restores the pending tasks of every
    instance when the tool starts again: tasks keep their remaining wait and
    overdue tasks run immediately. When disabled the tool behaves like before:
    every task is scheduled to run at startup.

    Args:
        preferences (dict, optional): Preferences data. If None, loads from disk.

    Returns:
        bool: True when the schedule should be remembered.
    """
    if preferences is None:
        preferences = load_preferences()
    return preferences.get("remember_schedule", True) is not False


def get_debug_mode(preferences=None):
    """Return whether debug (verbose) mode is enabled.

    When enabled, debug-level log messages are written to the log file and
    shown in the GUI, and OCR debug captures are saved on failures.

    Priority: the ``WOSUTIL_DEBUG`` environment variable (``1``/``true``/
    ``yes``/``on`` enables it, ``0``/``false``/``no``/``off`` disables it),
    then the persisted preference.

    Args:
        preferences (dict, optional): Preferences data. If None, loads from disk.

    Returns:
        bool: True when debug mode is enabled.
    """
    env_value = os.environ.get(DEBUG_MODE_ENV, "").strip().lower()
    if env_value:
        if env_value in ("1", "true", "yes", "on"):
            return True
        if env_value in ("0", "false", "no", "off"):
            return False
    if preferences is None:
        preferences = load_preferences()
    return preferences.get("debug_mode", False) is True


def set_debug_mode(enabled):
    """Persist the debug (verbose) mode preference.

    Args:
        enabled (bool): New debug mode state.

    Returns:
        bool: True if saved successfully.
    """
    preferences = load_preferences() or {}
    preferences["debug_mode"] = bool(enabled)
    return save_preferences(preferences)


def load_preferences():
    """Load preferences from the JSON file.

    Returns:
        dict: Preferences data or an empty dict if missing/invalid.
    """
    preferences = load_json_file(PREFERENCES_FILE, default_value={})
    if not isinstance(preferences, dict):
        return {}
    return preferences


def save_preferences(preferences):
    """Save preferences to the JSON file.

    Args:
        preferences (dict): Preferences data to save.

    Returns:
        bool: True if saved successfully.
    """
    return save_json_file(PREFERENCES_FILE, preferences)


def get_task_priorities(preferences=None):
    """Get the user-defined task priorities.

    Args:
        preferences (dict, optional): Preferences data. If None, loads from disk.

    Returns:
        dict: Mapping of task_id to integer priority (lower = higher priority).
    """
    if preferences is None:
        preferences = load_preferences()
    priorities = preferences.get("task_priorities", {})
    if not isinstance(priorities, dict):
        return {}
    clean_priorities = {}
    for task_id, value in priorities.items():
        priority = safe_int(value, 0)
        if priority > 0:
            clean_priorities[task_id] = priority
    return clean_priorities


def get_kill_beast_march(preferences=None):
    """Get the user-selected march used to kill beasts (1-12).

    Args:
        preferences (dict, optional): Preferences data. If None, loads from disk.

    Returns:
        int: March number between KILL_BEAST_MARCH_MIN and KILL_BEAST_MARCH_MAX.
    """
    if preferences is None:
        preferences = load_preferences()
    march = safe_int(preferences.get("kill_beast_march"), DEFAULT_KILL_BEAST_MARCH)
    return max(KILL_BEAST_MARCH_MIN, min(KILL_BEAST_MARCH_MAX, march))


def get_kill_beast_march_assignment(preferences=None):
    """Get the march used to kill beasts only if explicitly assigned by the user.

    Unlike get_kill_beast_march, this returns None when the user has not
    assigned a march, so callers can keep their default behavior.

    Args:
        preferences (dict, optional): Preferences data. If None, loads from disk.

    Returns:
        int or None: March number between KILL_BEAST_MARCH_MIN and
            KILL_BEAST_MARCH_MAX if assigned and valid, None otherwise.
    """
    if preferences is None:
        preferences = load_preferences()
    march = safe_int(preferences.get("kill_beast_march"), 0)
    if march < KILL_BEAST_MARCH_MIN or march > KILL_BEAST_MARCH_MAX:
        return None
    return march


def get_emulator(preferences=None):
    """Get the stored emulator mode ("mumu", "bluestacks" or "ldplayer").

    Args:
        preferences (dict, optional): Preferences data. If None, loads from disk.

    Returns:
        str or None: The emulator mode, or None if unset or unknown.
    """
    if preferences is None:
        preferences = load_preferences()
    emulator = preferences.get("emulator")
    if emulator not in (EMULATOR_MUMU, EMULATOR_BLUESTACKS, EMULATOR_LDPLAYER):
        return None
    return emulator


def get_emulator_paths(preferences=None):
    """Return normalized executable and instance paths for every emulator.

    Missing or malformed persisted values fall back to the standard Windows
    installation paths, so older preference files remain fully compatible.

    Args:
        preferences (dict, optional): Preferences data. If None, loads from disk.

    Returns:
        dict: Mapping of emulator identifiers to their configured paths.
    """
    if preferences is None:
        preferences = load_preferences()
    configured_paths = preferences.get("emulator_paths", {})
    if not isinstance(configured_paths, dict):
        configured_paths = {}

    def normalized_path(emulator_config, key, default):
        value = emulator_config.get(key)
        if isinstance(value, str) and value.strip():
            return os.path.normpath(value.strip())
        return os.path.normpath(default)

    paths = {}
    for emulator, defaults in DEFAULT_EMULATOR_PATHS.items():
        emulator_config = configured_paths.get(emulator, {})
        if not isinstance(emulator_config, dict):
            emulator_config = {}
        paths[emulator] = {key: normalized_path(emulator_config, key, default) for key, default in defaults.items()}
    return paths


def get_mystery_shop_level(preferences=None):
    """Get the user-selected mystery shop redemption level.

    Level "free" redeems only free items, "widgets_50" also redeems 50% off
    widgets and "widgets_20" additionally redeems 20% off widgets.

    Args:
        preferences (dict, optional): Preferences data. If None, loads from disk.

    Returns:
        str: One of MYSTERY_SHOP_LEVELS.
    """
    if preferences is None:
        preferences = load_preferences()
    level = preferences.get("mystery_shop_level", DEFAULT_MYSTERY_SHOP_LEVEL)
    if level not in MYSTERY_SHOP_LEVELS:
        return DEFAULT_MYSTERY_SHOP_LEVEL
    return level


def get_gather_resource(preferences=None):
    """Get the resource selected for the ox gathering skill.

    Args:
        preferences (dict, optional): Preferences data. If None, loads from disk.

    Returns:
        str: One of ``GATHER_RESOURCES``.
    """
    if preferences is None:
        preferences = load_preferences()
    resource = preferences.get("gather_resource", DEFAULT_GATHER_RESOURCE)
    if resource not in GATHER_RESOURCES:
        return DEFAULT_GATHER_RESOURCE
    return resource


def save_emulator(emulator):
    """Persist the selected emulator mode.

    Args:
        emulator (str): Emulator mode, "mumu", "bluestacks" or "ldplayer".

    Returns:
        bool: True if saved successfully.
    """
    preferences = load_preferences() or {}
    preferences["emulator"] = emulator
    return save_preferences(preferences)


def get_requirements_reminder_seen(preferences=None):
    """Return True when the first-run setup requirements reminder was shown.

    Args:
        preferences (dict, optional): Preferences data. If None, loads from disk.

    Returns:
        bool: True if the reminder has already been shown.
    """
    if preferences is None:
        preferences = load_preferences()
    return preferences.get("requirements_reminder_seen", False) is True


def mark_requirements_reminder_seen():
    """Persist that the first-run setup requirements reminder was shown.

    Returns:
        bool: True if saved successfully.
    """
    preferences = load_preferences() or {}
    preferences["requirements_reminder_seen"] = True
    return save_preferences(preferences)
