"""Multi-instance tool controller.

Runs the selected profiles across emulator instances. Each instance is a
worker thread; a lightweight scheduler starts the instances whose next task is
close and lets them close the emulator while they wait for a distant one, so
the emulator limit is never exceeded and no manual queue maintenance is
needed.
"""

import gc
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Optional

from wosutil.config import INSTANCE_SELECTION_FILE, WHITEOUT_PACKAGE
from wosutil.emulator.emulator_manager import check_emulator_health, force_restart_emulator, invalidate_adb_verification, is_wos_installed, verify_adb_connected
from wosutil.preferences import get_remember_schedule
from wosutil.stop import ToolStopped, stop_signal
from wosutil.tool.tasks.task_schedule import (
    build_task_state,
    load_saved_tasks,
    load_task_schedule,
    save_task_schedule,
    snapshot_instance_schedule,
)
from wosutil.tool.utc_time import sync_utc_time
from wosutil.utils import instance_log_context, load_json_file, retry_operation, safe_int, save_json_file

# Tasks whose run times are closer than this are considered the same time
# window: when both are due (or become due within the window), the priority
# order wins instead of the task that was read a second earlier.
TASK_GROUPING_WINDOW_SECONDS = 5.0

# Seconds before a scheduled task when its instance is launched so the
# emulator and the game are ready by the time the task runs.
TASK_STARTUP_WINDOW_SECONDS = 60

# A running instance keeps its emulator open when the next task is closer
# than this; otherwise the emulator is closed to free the slot.
INSTANCE_KEEPALIVE_WINDOW_SECONDS = 120

# Seconds an instance waits before being retried after exhausting its
# consecutive launch attempts. The retry is delayed instead of being dropped
# so transient emulator/hypervisor failures recover without a full restart.
RETRY_COOLDOWN_SECONDS = 600

# Default number of emulators opened at the same time.
DEFAULT_MAX_INSTANCES = 2


@dataclass
class InstanceState:
    """Runtime scheduling state of one instance."""

    profile_name: str
    tasks: list = field(default_factory=list)
    current_task_name: Optional[str] = None
    opening: bool = False


def pick_scheduled_task(task_state, now):
    """Choose the task to run or wait for, grouping close run times.

    Tasks aimed at the same instant (e.g. several events scheduled for 00:00
    UTC) can end up with run times a second or two apart because of timer
    reading noise. This helper prefers the higher-priority task of the
    window: when a task is already due, the highest-priority task due within
    TASK_GROUPING_WINDOW_SECONDS ahead is returned instead, and the caller
    waits (at most the window) for it. Ties on priority keep the earliest
    time order.

    Args:
        task_state (list): Running task state dicts with 'priority' and
            'next_run_time' keys.
        now (float): Current timestamp.

    Returns:
        tuple: (task, wait_until). When the task must run right now,
        ``wait_until`` is None. When the caller must wait, ``wait_until`` is
        the timestamp the returned task becomes due. (None, None) when no
        task is scheduled.
    """
    due = [t for t in task_state if t.get("next_run_time", 0) <= now]
    if not due:
        future = [t for t in task_state if t.get("next_run_time", 0) > now]
        if not future:
            return None, None
        earliest = min(future, key=lambda t: t["next_run_time"])
        return earliest, earliest["next_run_time"]
    best = min(due, key=lambda t: t.get("priority", 99))
    higher_future = [t for t in task_state if t.get("next_run_time", 0) > now and t.get("next_run_time", 0) <= now + TASK_GROUPING_WINDOW_SECONDS and t.get("priority", 99) < best.get("priority", 99)]
    if not higher_future:
        return best, None
    target = min(higher_future, key=lambda t: (t.get("priority", 99), t["next_run_time"]))
    return target, target["next_run_time"]


def load_instance_selection():
    """Load the instance selection from the JSON file.

    Returns:
        dict: Dictionary with instance selection data.
    """
    return load_json_file(INSTANCE_SELECTION_FILE, default_value={})


def save_instance_selection(selection):
    """Save the instance selection to the JSON file.

    Args:
        selection (dict): Dictionary with instance selection data.

    Returns:
        bool: True if saved successfully.
    """
    return save_json_file(INSTANCE_SELECTION_FILE, selection)


class MultiInstanceToolController:
    """Run the selected profiles across emulator instances."""

    def __init__(self, log_message, task_definitions, backend, profiles, instance_widgets, dialog_queue=None):
        """Initialize the controller.

        Args:
            log_message: Logging function.
            task_definitions (dict): Task definitions.
            backend: The emulator backend used to start/stop instances.
            profiles (dict): Task profiles (name -> task ids).
            instance_widgets (list): Instance rows shown in the GUI.
            dialog_queue (queue.Queue, optional): Queue where worker threads
                push (title, text) pairs the GUI must show as dialogs.
        """
        self.log_message = log_message
        self.task_definitions = task_definitions
        self.backend = backend
        self.profiles = profiles
        self.instance_widgets = instance_widgets
        self.dialog_queue = dialog_queue

        self.max_launch_attempts = 3
        # Seconds between emulator health probes while tasks run.
        self.health_check_interval = 60
        self.tool_running = False
        self.tool_should_stop = stop_signal

        self._state_lock = threading.RLock()
        self._schedule_lock = threading.Lock()

        self.states: dict[int, InstanceState] = {}
        self._selected_instances: dict[int, str] = {}
        self._active_instances: set[int] = set()
        self.instance_threads: dict[int, threading.Thread] = {}
        self._launch_attempts: dict[int, int] = {}
        self._retry_blocked_until: dict[int, float] = {}
        self._aborted_instances: set[int] = set()
        self._max_instances = DEFAULT_MAX_INSTANCES
        self._task_schedule = None

    # --- Public state used by the GUI ---

    def set_backend(self, backend):
        """Replace the emulator backend (e.g. after a Preferences change)."""
        self.backend = backend

    def set_max_instances(self, value):
        """Set the maximum number of simultaneously open emulators."""
        with self._state_lock:
            self._max_instances = max(1, safe_int(value, DEFAULT_MAX_INSTANCES))

    def state_for(self, index):
        """Return the scheduling state of an instance (None when not selected)."""
        with self._state_lock:
            return self.states.get(index)

    def is_active(self, index):
        """Return True when the instance has a running worker."""
        with self._state_lock:
            return index in self._active_instances

    def is_aborted(self, index):
        """Return True when the instance was permanently aborted (game missing)."""
        with self._state_lock:
            return index in self._aborted_instances

    def retry_seconds_remaining(self, index, now=None):
        """Return the seconds the instance is paused before its next retry."""
        now = time.time() if now is None else now
        with self._state_lock:
            return max(0.0, self._retry_blocked_until.get(index, 0) - now)

    def pending_position(self, index):
        """Return the 1-based start order of a waiting instance (None if absent)."""
        with self._state_lock:
            pending = [(next_run, idx) for idx, next_run in self._pending_instances_locked() if next_run is not None]
        pending.sort()
        for position, (_next_run, idx) in enumerate(pending, start=1):
            if idx == index:
                return position
        return None

    def on_checkbox_or_profile_change(self):
        """Persist the instance selection and profile choices."""
        selection = {}
        for inst in self.instance_widgets:
            selection[str(inst["index"])] = {"checked": inst["checked"].get(), "profile": inst["profile_var"].get()}
        save_instance_selection(selection)

    # --- Tool lifecycle ---

    def start_tool(self):
        """Start the automation tool.

        Returns:
            bool: True if the tool started, False if it was rejected (already
            running, previous session still stopping, or ADB blocked).
        """
        with self._state_lock:
            if self.tool_running:
                self.log_message("The tool is already running.", level="warning")
                return False
            if any(thread.is_alive() for thread in self.instance_threads.values()):
                self.log_message("The previous tool session is still stopping. Try again in a moment.", level="warning")
                return False
            self.tool_running = True
            self.states.clear()
            self._selected_instances.clear()
            self._active_instances.clear()
            self.instance_threads.clear()
            self._launch_attempts.clear()
            self._retry_blocked_until.clear()
            self._aborted_instances.clear()

        self.tool_should_stop.clear()

        adb_warnings = self.backend.check_adb_access()
        if adb_warnings:
            for warning in adb_warnings:
                self.log_message(warning, level="error")
            self.log_message("Tool start aborted: ADB access to the emulator is blocked. Fix the error above and press Start again.", level="error")
            with self._state_lock:
                self.tool_running = False
            return False

        # The schedule memory keeps pending tasks between sessions; without it
        # every task starts due right away.
        self._task_schedule = load_task_schedule() if get_remember_schedule() else None
        now = time.time()
        with self._state_lock:
            for inst in self.instance_widgets:
                if not inst["checked"].get():
                    continue
                idx = inst["index"]
                profile_name = inst["profile_var"].get()
                saved_tasks = load_saved_tasks(self._task_schedule, idx) if self._task_schedule is not None else {}
                tasks = self._build_tasks(profile_name, saved_tasks, now)
                self.states[idx] = InstanceState(profile_name=profile_name, tasks=tasks)
                self._selected_instances[idx] = profile_name

        if self._task_schedule is not None and self._selected_instances:
            self._persist_schedules()
        self.launch_due_instances()
        return True

    def stop_tool(self):
        """Stop the tool, close the emulators and wait for the workers."""
        with self._state_lock:
            self.tool_running = False
            self.tool_should_stop.set()
            active_instances = list(self._active_instances)
            worker_threads = list(self.instance_threads.values())

        for idx in active_instances:
            self.log_message(f"Closing emulator of instance {idx} while stopping the tool...", "info")
            self._close_instance(idx)

        for thread in worker_threads:
            if thread.is_alive():
                thread.join(timeout=5.0)

        with self._state_lock:
            live_threads = [thread for thread in self.instance_threads.values() if thread.is_alive()]
            self._active_instances.clear()
            self._selected_instances.clear()
            self._launch_attempts.clear()
            self._retry_blocked_until.clear()
            self._aborted_instances.clear()
            self.states.clear()
            if not live_threads:
                self.instance_threads.clear()

        gc.collect()

        if live_threads:
            self.log_message(f"Tool stop requested, but {len(live_threads)} worker thread(s) are still finishing.", level="warning")
        self.log_message("Tool stopped and resources cleaned up", level="info")

    # --- Scheduler ---

    def launch_due_instances(self):
        """Start the selected instances whose next task is close, up to the limit.

        The emulator limit is honored by only starting as many workers as free
        slots exist. Callers invoke this periodically (the GUI calls it once per
        second); it does not block and returns immediately when nothing is due.
        """
        if not self.tool_running:
            return
        if self.tool_should_stop.is_set():
            return
        to_start = []
        with self._state_lock:
            free_slots = self._max_instances - len(self._active_instances)
            if free_slots <= 0:
                return
            now = time.time()
            due = []
            for idx in self._selected_instances:
                if idx in self._active_instances or idx in self._aborted_instances:
                    continue
                if now < self._retry_blocked_until.get(idx, 0):
                    continue
                next_run = self._next_run_time(idx)
                if next_run is None or next_run - now > TASK_STARTUP_WINDOW_SECONDS:
                    continue
                due.append((next_run, idx))
            due.sort()
            for _next_run, idx in due[:free_slots]:
                thread = threading.Thread(target=self._run_instance, args=(idx,), daemon=True, name=f"Instance-{idx}")
                self._active_instances.add(idx)
                self.instance_threads[idx] = thread
                to_start.append(thread)
        for thread in to_start:
            thread.start()

    def _pending_instances_locked(self):
        """Yield ``(index, next_run_time)`` of instances that can be started.

        The caller must hold ``_state_lock``.
        """
        now = time.time()
        for idx in self._selected_instances:
            if idx in self._active_instances or idx in self._aborted_instances:
                continue
            if now < self._retry_blocked_until.get(idx, 0):
                continue
            yield idx, self._next_run_time(idx)

    def _next_run_time(self, index):
        """Return the earliest scheduled time of an instance (None without tasks).

        The caller must hold ``_state_lock``.
        """
        state = self.states.get(index)
        tasks = state.tasks if state else []
        return min((task.get("next_run_time", 0) for task in tasks), default=None)

    def _build_tasks(self, profile_name, saved_tasks, now):
        """Build the runtime task list of a profile, applying a saved schedule."""
        task_names = self.profiles.get(profile_name, [])
        task_defs = sorted((self.task_definitions[name] for name in task_names if name in self.task_definitions), key=lambda task: task["priority"])
        return build_task_state(task_defs, saved_tasks or {}, now)

    # --- Worker ---

    def _run_instance(self, index):
        """Run one instance worker, always releasing its slot afterwards."""
        with instance_log_context(index):
            try:
                self._instance_worker(index)
            except ToolStopped:
                self.log_message(f"Tool stopped while working on instance {index}.", "info")
            except Exception as e:
                self.log_message(f"Error in instance {index}: {e}", "error")
                self._record_launch_failure(index)
            finally:
                state = self.states.get(index)
                if state is not None:
                    state.opening = False
                    state.current_task_name = None
                # Close the emulator before releasing the slot: the scheduler
                # must never start a new worker on the same instance while the
                # previous one is still shutting it down. A stop request is
                # handled by stop_tool().
                if not self.tool_should_stop.is_set():
                    self._close_instance(index)
                self._release_slot(index)
                self._remove_thread_reference(index, threading.current_thread())

    def _instance_worker(self, index):
        """Open the instance, launch the game and run its tasks."""
        if self.tool_should_stop.is_set():
            return
        state = self.states.get(index)
        if state is None:
            return
        state.opening = True
        state.current_task_name = None

        self._forget_adb_verification(index)
        if not self.backend.start_instance(index):
            self._record_launch_failure(index)
            return
        if self.tool_should_stop.is_set():
            return

        if not verify_adb_connected(index):
            self.log_message(f"Could not connect ADB to instance {index}. Restarting the emulator...", "error")
            if not force_restart_emulator(index, self.backend) or not verify_adb_connected(index):
                self.log_message(f"ADB connection still failed after restart for instance {index}.", "error")
                self._record_launch_failure(index)
                return

        # The game missing is a permanent condition: closing/restarting the
        # emulator can never fix it, so abort this instance instead of looping.
        if not is_wos_installed(index):
            self._abort_instance(index)
            return

        from wosutil.tool.tasks.navigation import launch_and_reach_city_screen

        if not retry_operation(lambda: launch_and_reach_city_screen(instance_index=index), max_attempts=3, delay=5.0, retry_on_false=True):
            self.log_message(f"Could not launch the game on instance {index}.", "error")
            self._record_launch_failure(index)
            return

        with self._state_lock:
            self._launch_attempts.pop(index, None)
        state.opening = False

        if self.tool_should_stop.is_set():
            return

        # The game clock is the first thing to sync every time an instance is
        # opened: several tasks reschedule to 00:00 UTC.
        try:
            sync_utc_time(index)
        except Exception as e:
            self.log_message(f"Error syncing the game UTC clock on instance {index}: {e}", "error")

        from wosutil.tool.tasks.task_automation import reconcile_bear_trap_schedule

        if state.tasks and reconcile_bear_trap_schedule(index, state.tasks):
            self._persist_schedules()

        last_health_check = time.time()
        while not self.tool_should_stop.is_set():
            now = time.time()

            if now - last_health_check > self.health_check_interval:
                if not check_emulator_health(index):
                    self.log_message(f"Emulator instance {index} appears to be hanging. Attempting restart...", "warning")
                    if force_restart_emulator(index, self.backend) and retry_operation(lambda: verify_adb_connected(index), max_attempts=3, delay=2.0, retry_on_false=True):
                        self.log_message(f"Emulator instance {index} restarted successfully during operation.", "success")
                    else:
                        self.log_message(f"Could not recover emulator instance {index}.", "error")
                        self._record_launch_failure(index)
                        return
                last_health_check = now

            next_task, wait_until = pick_scheduled_task(state.tasks, now)
            if next_task is None:
                self.log_message(f"No tasks scheduled for profile '{state.profile_name}' on instance {index}.", "info")
                return
            if wait_until is not None:
                if wait_until - now > INSTANCE_KEEPALIVE_WINDOW_SECONDS:
                    self.log_message(
                        f"The next task for instance {index} is more than {INSTANCE_KEEPALIVE_WINDOW_SECONDS} seconds away. Closing the emulator and freeing the slot.",
                        "info",
                    )
                    return
                state.current_task_name = None
                self.tool_should_stop.wait(timeout=min(max(1, int(wait_until - now)), 10))
                continue

            task = next_task
            state.current_task_name = task.get("name", "?")
            self.log_message(f"Executing task '{state.current_task_name}' on instance {index}...", "info")
            try:
                result = task["function"](instance_index=index)
                if isinstance(result, tuple) and len(result) == 2:
                    result, new_reschedule = result
                    if isinstance(new_reschedule, (int, float)) and new_reschedule > 0:
                        task["reschedule_seconds"] = new_reschedule
                if result:
                    self.log_message(f"Task '{state.current_task_name}' completed successfully on instance {index}.", "success")
                else:
                    self.log_message(f"Task '{state.current_task_name}' failed on instance {index}.", "warning")
            except ToolStopped:
                raise
            except Exception as e:
                self.log_message(f"Exception while running task '{state.current_task_name}' on instance {index}: {e}", "error")
            task["next_run_time"] = time.time() + task.get("reschedule_seconds", 60)
            # Tasks configured to run right after this one are due immediately
            # (the loop is serial per instance).
            for other in state.tasks:
                if other.get("run_after") == task.get("id"):
                    other["next_run_time"] = time.time()
            state.current_task_name = None
            self._persist_schedules()
            self.tool_should_stop.wait(timeout=2)

        self.log_message(f"Task cycle finished for instance {index}.", "info")

    def _abort_instance(self, index):
        """Permanently abort an instance whose game is not installed."""
        with self._state_lock:
            self._aborted_instances.add(index)
        message = (
            f"Whiteout Survival ({WHITEOUT_PACKAGE}) is not installed on instance {index}. "
            "Install the game on this instance (e.g. from the emulator's app store or Google Play) "
            "and press Start again. The instance will not be retried automatically."
        )
        self.log_message(message, "error")
        if self.dialog_queue is not None:
            self.dialog_queue.put(("Game not installed", message))

    def _record_launch_failure(self, index):
        """Count a launch failure and pause the instance after repeated ones."""
        paused = False
        with self._state_lock:
            attempts = self._launch_attempts.get(index, 0) + 1
            if attempts >= self.max_launch_attempts:
                self._retry_blocked_until[index] = time.time() + RETRY_COOLDOWN_SECONDS
                self._launch_attempts[index] = 0
                paused = True
            else:
                self._launch_attempts[index] = attempts
        if paused:
            self.log_message(
                f"Instance {index} has failed {self.max_launch_attempts} consecutive startup attempts. Pausing it for {RETRY_COOLDOWN_SECONDS // 60} minutes before retrying.",
                level="error",
            )

    def _forget_adb_verification(self, index):
        """Discard the cached ADB verification of an instance, if resolvable."""
        with suppress(Exception):
            invalidate_adb_verification(self.backend.get_serial(index))

    def _close_instance(self, index):
        """Close an emulator instance, logging (but not raising) failures."""
        self._forget_adb_verification(index)
        try:
            self.backend.stop_instance(index)
        except Exception as e:
            self.log_message(f"Error closing emulator instance {index}: {e}", "error")

    def _remove_thread_reference(self, index, thread):
        """Remove a worker reference only when it still belongs to that worker."""
        with self._state_lock:
            if self.instance_threads.get(index) is thread:
                del self.instance_threads[index]

    def _release_slot(self, index):
        """Free the instance slot so the scheduler can start it again."""
        with self._state_lock:
            self._active_instances.discard(index)

    # --- Schedule persistence ---

    def _persist_schedules(self):
        """Persist the running task schedule of every selected instance.

        Only active when the schedule memory is enabled and at least one
        instance is selected (an empty selection must never wipe the saved
        schedule). Only the tasks of the selected instances are updated;
        entries of tasks that are not in the current profile are kept so they
        can be resumed later.
        """
        if self._task_schedule is None:
            return
        with self._state_lock:
            selected = list(self._selected_instances)
            states = {idx: self.states.get(idx) for idx in selected}
        if not selected:
            return
        with self._schedule_lock:
            for idx in selected:
                state = states.get(idx)
                if state is None:
                    continue
                instance_entry = self._task_schedule.setdefault(str(idx), {})
                instance_entry.update(snapshot_instance_schedule(state.tasks))
            if not save_task_schedule(self._task_schedule):
                self.log_message("Error saving the task schedule.", level="error")
