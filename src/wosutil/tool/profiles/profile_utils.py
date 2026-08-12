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
    now = now or time.time()
    task_names = profiles.get(profile_name, [])
    return [{**task_definitions[t], "next_run_time": now} for t in task_names if t in task_definitions]
