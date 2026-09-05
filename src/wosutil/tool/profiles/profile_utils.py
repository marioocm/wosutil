"""Profile utilities module.

Helper functions for building and managing task profiles.
"""

import time


def build_running_tasks_state(profile_name, profiles, task_definitions, now=None):
    """Returns a list of initialized tasks for the given profile.

    Args:
        profile_name (str): Name of the profile.
        profiles (dict): Dictionary of profiles with task names.
        task_definitions (dict): Dictionary of task definitions.
        now (float, optional): Current timestamp. If None, uses time.time().

    Returns:
        list: List of task dictionaries with 'next_run_time' set.
    """
    now = now if now is not None else time.time()
    if not isinstance(profiles, dict) or not isinstance(task_definitions, dict) or not isinstance(profile_name, str):
        return []
    task_names = profiles.get(profile_name, [])
    if not isinstance(task_names, list):
        return []
    return [{**task_definitions[t], "next_run_time": now, "last_result": "success", "nominal_due": now, "consecutive_errors": 0} for t in task_names if isinstance(t, str) and t in task_definitions]
