"""Preferences validation and persistence shared by every view."""

from dataclasses import dataclass
from typing import Optional

from wosutil.emulator.backends import EMULATOR_BLUESTACKS, EMULATOR_LDPLAYER, EMULATOR_MUMU
from wosutil.preferences import (
    BEAR_RALLY_CALL_MARCH_MAX,
    BEAR_RALLY_CALL_MARCH_MIN,
    BEAR_TRAP_MARCH_MAX,
    BEAR_TRAP_MARCH_MIN,
    GATHER_RESOURCES,
    INTEL_MODE_CLASSIC,
    INTEL_MODE_EXECUTE_ALL,
    INTEL_MODES,
    KILL_BEAST_MARCH_MAX,
    KILL_BEAST_MARCH_MIN,
    MYSTERY_SHOP_LEVEL_FREE,
    MYSTERY_SHOP_LEVEL_WIDGETS_20,
    MYSTERY_SHOP_LEVEL_WIDGETS_50,
    MYSTERY_SHOP_LEVELS,
    get_bear_rally_call_march,
    get_bear_trap_marches,
    get_debug_mode,
    get_emulator,
    get_emulator_paths,
    get_gather_resource,
    get_intel_mode,
    get_kill_beast_march,
    get_kill_beast_march_assignment,
    get_mystery_shop_level,
    get_remember_schedule,
    get_start_minimized,
    load_preferences,
    save_preferences,
)
from wosutil.utils import safe_int

EMULATOR_LABELS = {EMULATOR_MUMU: "MuMu Player", EMULATOR_BLUESTACKS: "BlueStacks", EMULATOR_LDPLAYER: "LDPlayer"}

EMULATOR_PATH_FIELDS = {
    EMULATOR_MUMU: (
        ("base_path", "Program folder (MuMuManager.exe)", "directory"),
        ("instance_base_path", "Instances folder (vms)", "directory"),
    ),
    EMULATOR_BLUESTACKS: (
        ("base_path", "Installation folder", "directory"),
        ("config_path", "Configuration file (bluestacks.conf)", "file"),
    ),
    EMULATOR_LDPLAYER: (
        ("base_path", "Installation folder", "directory"),
        ("instance_config_dir", "Instances config folder (vms\\config)", "directory"),
    ),
}

MYSTERY_SHOP_LEVEL_LABELS = {
    MYSTERY_SHOP_LEVEL_FREE: "Free items only",
    MYSTERY_SHOP_LEVEL_WIDGETS_50: "Free items + widgets 50%",
    MYSTERY_SHOP_LEVEL_WIDGETS_20: "Free items + widgets 50% + widgets 20%",
}

GATHER_RESOURCE_LABELS = {resource: resource.title() for resource in GATHER_RESOURCES}

INTEL_MODE_LABELS = {
    INTEL_MODE_CLASSIC: "Intel Classic (use Gina for beast)",
    INTEL_MODE_EXECUTE_ALL: "Intel Execute All",
}


@dataclass
class PreferencesResult:
    """Outcome of saving the preferences form.

    Attributes:
        ok: True when the preferences were persisted.
        error: User-facing error text when ``ok`` is False.
        emulator: Emulator code stored by the form.
        emulator_paths: Normalized paths stored by the form.
        emulator_changed: True when the emulator or its paths changed and the
            active backend must be replaced.
    """

    ok: bool
    error: Optional[str] = None
    emulator: Optional[str] = None
    emulator_paths: Optional[dict] = None
    emulator_changed: bool = False


def preferences_view(task_definitions: dict) -> dict:
    """Return the current preference values in a view-friendly shape.

    Args:
        task_definitions: Task definitions used to order the priority list.
    """
    preferences = load_preferences()
    ordered_tasks = sorted(task_definitions.values(), key=lambda task: task["priority"])
    return {
        "emulator": get_emulator(preferences),
        "emulator_paths": get_emulator_paths(preferences),
        "task_order": [task["id"] for task in ordered_tasks],
        "kill_beast_march": get_kill_beast_march(preferences),
        "kill_beast_march_set": get_kill_beast_march_assignment(preferences) is not None,
        "bear_trap_marches": get_bear_trap_marches(preferences),
        "bear_rally_call_march": get_bear_rally_call_march(preferences),
        "bear_rally_call_march_set": "bear_rally_call_march" in preferences,
        "gather_resource": get_gather_resource(preferences),
        "intel_mode": get_intel_mode(preferences),
        "mystery_shop_level": get_mystery_shop_level(preferences),
        "debug_mode": get_debug_mode(preferences),
        "remember_schedule": get_remember_schedule(preferences),
        "start_minimized": get_start_minimized(preferences),
    }


def apply_preferences(values: dict, task_definitions: dict, log_message) -> PreferencesResult:
    """Validate and persist the preferences form.

    The expected keys are the ones returned by :func:`preferences_view`, except
    ``task_order`` (a list of task ids) and the ``*_set`` flags, which let a
    view keep a march unassigned instead of writing the effective default.

    Args:
        values: Raw values collected by a view.
        task_definitions: Task definitions whose priorities are updated.
        log_message: Logging function.

    Returns:
        PreferencesResult: Persisted values and whether the backend changed.
    """
    preferences = load_preferences()
    previous_emulator = get_emulator(preferences)
    previous_emulator_paths = get_emulator_paths(preferences)

    emulator = values.get("emulator")
    if emulator not in EMULATOR_LABELS:
        emulator = EMULATOR_MUMU
    preferences["emulator"] = emulator

    raw_paths = values.get("emulator_paths")
    if not isinstance(raw_paths, dict):
        raw_paths = {}
    existing_paths = get_emulator_paths(preferences)
    configured_paths: dict = {}
    for code, fields in EMULATOR_PATH_FIELDS.items():
        source = raw_paths.get(code)
        if not isinstance(source, dict):
            source = {}
        configured_paths[code] = {}
        for key, _label, _kind in fields:
            value = source.get(key)
            if isinstance(value, str):
                configured_paths[code][key] = value.strip()
            else:
                # A client that only edits one emulator must not wipe the
                # custom paths of the others.
                configured_paths[code][key] = existing_paths.get(code, {}).get(key, "")
    preferences["emulator_paths"] = configured_paths
    emulator_paths = get_emulator_paths(preferences)
    preferences["emulator_paths"] = emulator_paths

    preferences["task_priorities"] = {}
    position = 0
    task_order = values.get("task_order")
    if not isinstance(task_order, list):
        task_order = []
    for task_id in task_order:
        task = task_definitions.get(task_id)
        if task is None:
            continue
        position += 1
        task["priority"] = position
        preferences["task_priorities"][task_id] = position

    kill_beast_default = get_kill_beast_march(preferences)
    if values.get("kill_beast_march_set", "kill_beast_march" in preferences):
        preferences["kill_beast_march"] = _validated_march(
            safe_int(values.get("kill_beast_march"), kill_beast_default),
            kill_beast_default,
            KILL_BEAST_MARCH_MIN,
            KILL_BEAST_MARCH_MAX,
            "Killing beasts march",
            log_message,
        )

    bear_trap_marches = _valid_march_list(values.get("bear_trap_marches"), BEAR_TRAP_MARCH_MIN, BEAR_TRAP_MARCH_MAX)
    if bear_trap_marches is None:
        log_message(
            f"Bear trap marches must be between {BEAR_TRAP_MARCH_MIN} and {BEAR_TRAP_MARCH_MAX}, keeping the saved values.",
            "warning",
        )
    else:
        preferences["bear_trap_marches"] = bear_trap_marches

    bear_call_default = get_bear_rally_call_march(preferences)
    if values.get("bear_rally_call_march_set", "bear_rally_call_march" in preferences):
        preferences["bear_rally_call_march"] = _validated_march(
            safe_int(values.get("bear_rally_call_march"), bear_call_default),
            bear_call_default,
            BEAR_RALLY_CALL_MARCH_MIN,
            BEAR_RALLY_CALL_MARCH_MAX,
            "Bear rally call march",
            log_message,
        )

    preferences["gather_resource"] = _choice(values.get("gather_resource"), GATHER_RESOURCES, get_gather_resource(preferences))
    preferences["intel_mode"] = _choice(values.get("intel_mode"), INTEL_MODES, get_intel_mode(preferences))
    preferences["mystery_shop_level"] = _choice(values.get("mystery_shop_level"), MYSTERY_SHOP_LEVELS, get_mystery_shop_level(preferences))
    preferences["debug_mode"] = bool(values.get("debug_mode", get_debug_mode(preferences)))
    preferences["remember_schedule"] = bool(values.get("remember_schedule", get_remember_schedule(preferences)))
    preferences["start_minimized"] = bool(values.get("start_minimized", get_start_minimized(preferences)))

    if not save_preferences(preferences):
        log_message("Error saving preferences.", "error")
        return PreferencesResult(False, "Error saving preferences.")

    log_message("Preferences saved successfully.", "success")
    return PreferencesResult(
        True,
        None,
        emulator,
        emulator_paths,
        emulator != previous_emulator or emulator_paths != previous_emulator_paths,
    )


def _choice(value, allowed, default):
    """Return ``value`` when it is one of ``allowed``, else ``default``."""
    return value if value in allowed else default


def _validated_march(value, default, minimum, maximum, label, log_message):
    """Validate one march value, restoring the saved value when invalid."""
    if value < minimum or value > maximum:
        log_message(f"{label} must be between {minimum} and {maximum}, keeping {default}.", "warning")
        return default
    return value


def _valid_march_list(values, minimum, maximum):
    """Return the list of march values, or None when one is out of range."""
    if not isinstance(values, list) or not values:
        return None
    marches = []
    for value in values:
        march = safe_int(value, 0)
        if march < minimum or march > maximum:
            return None
        marches.append(march)
    return marches
