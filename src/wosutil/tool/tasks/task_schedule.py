"""Task schedule persistence module.

Persists the per-instance task schedule (next run time and last measured
reschedule) so pending tasks can be resumed after the tool or the computer
restarts, instead of re-scheduling every task to run at startup.

The schedule is keyed by instance and task id (not by profile): when the user
switches the profile of an instance, the tasks that exist in both profiles
keep their remembered schedule and only the new ones start from scratch.
"""

import time

from wosutil.config import TASK_SCHEDULE_FILE
from wosutil.utils import load_json_file, save_json_file


def load_task_schedule():
    """Load the persisted per-instance task schedule.

    Returns:
        dict: Schedule keyed by instance index -> task id.
    """
    schedule = load_json_file(TASK_SCHEDULE_FILE, default_value={})
    if not isinstance(schedule, dict):
        return {}
    return schedule


def save_task_schedule(schedule):
    """Persist the task schedule to the JSON file.

    Args:
        schedule (dict): Schedule keyed by instance index -> task id.

    Returns:
        bool: True if saved successfully.
    """
    return save_json_file(TASK_SCHEDULE_FILE, schedule)


def load_saved_tasks(schedule, instance_index):
    """Return the saved tasks of one instance.

    Entries without a valid ``next_run_time`` are ignored so corrupted or
    outdated data never produces an unexpected schedule.

    Args:
        schedule (dict): The full task schedule.
        instance_index (int): Emulator instance index.

    Returns:
        dict: Mapping of task id -> {"next_run_time", "reschedule_seconds"}.
    """
    instance_entry = schedule.get(str(instance_index))
    if not isinstance(instance_entry, dict):
        return {}
    saved_tasks = {}
    for task_id, entry in instance_entry.items():
        if isinstance(entry, dict) and isinstance(entry.get("next_run_time"), (int, float)):
            saved_tasks[task_id] = entry
    return saved_tasks


def build_task_state(sorted_task_defs, saved_tasks, now=None):
    """Build the running task state applying the saved schedule.

    Tasks with a saved ``next_run_time`` keep it: a past time means the task
    is due immediately at startup, a future time keeps the remaining wait.
    Tasks without a saved entry fall back to the fresh-start behavior and run
    at startup.

    Args:
        sorted_task_defs (list): Task definitions sorted by priority.
        saved_tasks (dict): Saved state from load_saved_tasks.
        now (float, optional): Current timestamp. If None, uses time.time().

    Returns:
        list: Task dicts (copies of the definitions) with 'next_run_time' set.
    """
    now = now or time.time()
    tasks = []
    for task_def in sorted_task_defs:
        task = task_def.copy()
        saved = saved_tasks.get(task["id"])
        if saved and isinstance(saved.get("next_run_time"), (int, float)):
            task["next_run_time"] = float(saved["next_run_time"])
            saved_reschedule = saved.get("reschedule_seconds")
            if isinstance(saved_reschedule, (int, float)) and saved_reschedule > 0:
                task["reschedule_seconds"] = float(saved_reschedule)
        else:
            task["next_run_time"] = now
        tasks.append(task)
    return tasks


def snapshot_instance_schedule(tasks):
    """Serialize the running task state to the persisted format.

    Args:
        tasks (list): Running task state dicts.

    Returns:
        dict: Mapping of task id -> {"next_run_time", "reschedule_seconds"}.
    """
    return {
        task["id"]: {
            "next_run_time": task.get("next_run_time", time.time()),
            "reschedule_seconds": task.get("reschedule_seconds", 3600),
        }
        for task in tasks
    }
