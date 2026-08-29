"""Multi-instance tool controller.

Manages running automation tasks across multiple emulator instances with threading and memory optimization.
"""

import gc
import threading
import time
from typing import Dict

from wosutil.config import INSTANCE_SELECTION_FILE, WHITEOUT_PACKAGE
from wosutil.emulator.emulator_manager import check_emulator_health, force_restart_emulator
from wosutil.preferences import get_remember_schedule
from wosutil.stop import ToolStopped, stop_signal
from wosutil.tool.profiles.profile_utils import build_running_tasks_state
from wosutil.tool.tasks.task_schedule import (
    build_task_state,
    load_saved_tasks,
    load_task_schedule,
    save_task_schedule,
    snapshot_instance_schedule,
)
from wosutil.tool.utc_time import sync_utc_time
from wosutil.utils import load_json_file, retry_operation, safe_int, save_json_file

# Tasks whose run times are closer than this are considered the same time
# window: when both are due (or become due within the window), the priority
# order wins instead of the task that was read a second earlier.
TASK_GROUPING_WINDOW_SECONDS = 5.0


def pick_scheduled_task(task_state, now):
    """Choose the task to run or wait for, grouping close run times.

    Tasks aimed at the same instant (e.g. several events scheduled for 00:00
    UTC) can end up with run times a second or two apart because of timer
    reading noise. Without grouping, the task whose time was read earlier
    runs first even when another, higher-priority task belongs to the same
    window. This helper prefers the higher-priority task: when a task is
    already due, the highest-priority task due within
    TASK_GROUPING_WINDOW_SECONDS ahead is returned instead, and the caller
    waits (at most the window) for it. Ties on priority keep the earliest
    time order.

    Args:
        task_state (list): Running task state dicts with 'priority' and
            'next_run_time' keys.
        now (float): Current timestamp.

    Returns:
        tuple: (task, wait_until). When the task must run right now,
            ``wait_until`` is None. When the caller must wait, ``wait_until``
            is the timestamp the returned task becomes due. (None, None) when
            no task is scheduled.
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
    """Loads the instance selection from the JSON file using utility functions.

    Returns:
        dict: Dictionary with instance selection data.
    """
    return load_json_file(INSTANCE_SELECTION_FILE, default_value={})


def save_instance_selection(selection):
    """Saves the instance selection to the JSON file using utility functions.

    Args:
        selection (dict): Dictionary with instance selection data.

    Returns:
        bool: True if saved successfully.
    """
    return save_json_file(INSTANCE_SELECTION_FILE, selection)


class MultiInstanceToolController:
    """Controls the automation tool for managing multiple emulator instances and their profiles.

    Handles starting, stopping, and scheduling tasks for each instance with memory optimization.
    """

    def __init__(
        self,
        log_message,
        TASK_DEFINITIONS,
        multi_instance_manager,
        profile_manager,
        instances_profile_managers,
        instance_queue,
        active_instances,
        instance_widgets,
        save_instance_selection,
        load_instance_selection,
        dialog_queue=None,
    ):
        """Initialize the multi-instance tool controller.

        Args:
            log_message: Logging function.
            TASK_DEFINITIONS: Task definitions.
            multi_instance_manager: Multi-instance manager.
            profile_manager: Profile manager.
            instances_profile_managers: Dict of instance to profile managers.
            instance_queue: Queue for instances.
            active_instances: Set of active instances.
            instance_widgets: Dict of instance widgets.
            save_instance_selection: Function to save selection.
            load_instance_selection: Function to load selection.
            dialog_queue (queue.Queue, optional): Queue where the worker threads
                push (title, text) pairs the GUI must show as dialogs. None
                disables dialogs.
        """
        self.log_message = log_message
        self.TASK_DEFINITIONS = TASK_DEFINITIONS
        self.multi_instance_manager = multi_instance_manager
        self.profile_manager = profile_manager
        self.instances_profile_managers = instances_profile_managers
        self.instance_queue = instance_queue
        self.active_instances = active_instances
        self.instance_widgets = instance_widgets
        self.save_instance_selection = save_instance_selection
        self.load_instance_selection = load_instance_selection
        self.dialog_queue = dialog_queue
        self.max_instances_var = None
        self.tool_should_stop = stop_signal
        self.tool_running = False
        self.max_launch_attempts = 3
        self.instance_launch_attempts: Dict[int, int] = {}

        # Memory management
        self._state_lock = threading.RLock()
        self.instance_threads: Dict[int, threading.Thread] = {}
        self.last_memory_cleanup = time.time()
        self.memory_cleanup_interval = 300  # 5 minutes

        # Task schedule memory (persisted between sessions)
        self._schedule_lock = threading.Lock()
        self._task_schedule = None
        self._selected_instances: set = set()

    def _discard_active_instance(self, index):
        """Remove an instance from the active set while holding the state lock."""
        with self._state_lock:
            self.active_instances.discard(index)

    def _enqueue_instance(self, index, profile_name):
        """Queue an instance once so concurrent retries cannot duplicate it."""
        with self._state_lock:
            if not any(queued_index == index for queued_index, _ in self.instance_queue):
                self.instance_queue.append((index, profile_name))

    def _remove_thread_reference(self, index, thread):
        """Remove a worker reference only when it still belongs to that worker."""
        with self._state_lock:
            if self.instance_threads.get(index) is thread:
                del self.instance_threads[index]

    def set_max_instances_var(self, max_instances_var):
        """Sets the variable that controls the maximum number of simultaneous instances.

        Args:
            max_instances_var: Variable (e.g., tkinter.IntVar) for max instances.
        """
        self.max_instances_var = max_instances_var

    def on_checkbox_or_profile_change(self):
        """Saves the current selection of instances and profiles when a checkbox or profile is changed."""
        selection = {}
        for inst in self.instance_widgets:
            idx = str(inst["index"])
            selection[idx] = {"checked": inst["checked"].get(), "profile": inst["profile_var"].get()}
        self.save_instance_selection(selection)

    def cleanup_memory(self):
        """Performs memory cleanup operations to prevent memory leaks."""
        current_time = time.time()
        if current_time - self.last_memory_cleanup > self.memory_cleanup_interval:
            # Force garbage collection
            gc.collect()

            # Clean up completed threads
            with self._state_lock:
                completed_threads = [(instance_id, thread) for instance_id, thread in self.instance_threads.items() if not thread.is_alive()]
                for instance_id, thread in completed_threads:
                    if self.instance_threads.get(instance_id) is thread:
                        del self.instance_threads[instance_id]

            self.last_memory_cleanup = current_time
            self.log_message("Memory cleanup completed", level="debug")

    def start_tool(self):
        """Starts the automation tool.

        Initializes the queue and active instances, and schedules the first batch of
        tasks for each selected instance. Refuses to start when the emulator
        backend cannot control ADB (e.g. BlueStacks has ADB access disabled), to
        avoid an endless start/retry loop.

        Returns:
            bool: True if the tool started, False if it was aborted.
        """
        with self._state_lock:
            if self.tool_running:
                self.log_message("The tool is already running.", level="warning")
                return False
            live_threads = [thread for thread in self.instance_threads.values() if thread.is_alive()]
            if live_threads:
                self.log_message("The previous tool session is still stopping. Try again in a moment.", level="warning")
                return False
            self.tool_running = True
            self.instance_queue.clear()
            self.active_instances.clear()
            self.instance_launch_attempts.clear()
            self.instance_threads.clear()

        self.tool_should_stop.clear()

        # Refuse to start when the backend cannot control the emulator.
        adb_warnings = self.multi_instance_manager.check_adb_access()
        if adb_warnings:
            for warning in adb_warnings:
                self.log_message(warning, level="error")
            self.log_message(
                "Tool start aborted: ADB access to the emulator is blocked. Fix the error above and press Start again.",
                level="error",
            )
            with self._state_lock:
                self.tool_running = False
            return False

        # Load the persisted schedule when the schedule memory is enabled;
        # otherwise keep the legacy behavior (every task scheduled at startup).
        self._task_schedule = load_task_schedule() if get_remember_schedule() else None
        self._selected_instances.clear()

        for inst in self.instance_widgets:
            if inst["checked"].get():
                idx = inst["index"]
                profile_name = inst["profile_var"].get()

                # Initialize the task state for the GUI
                from wosutil.tool.profiles.profile_manager import ProfileManager

                pm = ProfileManager(self.log_message)
                task_names = pm.profiles.get(profile_name, [])
                running_tasks_state = []
                now = time.time()

                # Sort tasks by priority (lower number = higher priority)
                sorted_task_defs = sorted([self.TASK_DEFINITIONS[tname] for tname in task_names if tname in self.TASK_DEFINITIONS], key=lambda t: t["priority"])

                self._selected_instances.add(idx)
                if self._task_schedule is not None:
                    # Resume the saved schedule: future times keep the remaining
                    # wait, overdue times make the task run immediately. The
                    # schedule is keyed by task, so switching profiles keeps
                    # the state of the tasks shared between both profiles.
                    saved_tasks = load_saved_tasks(self._task_schedule, idx)
                    running_tasks_state = build_task_state(sorted_task_defs, saved_tasks, now)
                else:
                    for i, task_def in enumerate(sorted_task_defs):
                        t = task_def.copy()
                        # Only the highest priority task is scheduled for now, the rest for the future
                        if i == 0:
                            t["next_run_time"] = now
                        else:
                            t["next_run_time"] = now + t["reschedule_seconds"]
                        running_tasks_state.append(t)

                self.instances_profile_managers[idx] = pm
                if self._task_schedule is not None:
                    pm.running_tasks_state = running_tasks_state
                else:
                    pm.running_tasks_state = build_running_tasks_state(profile_name, pm.profiles, self.TASK_DEFINITIONS)
                if running_tasks_state:
                    pm.next_run_time = min(t["next_run_time"] for t in running_tasks_state)
                else:
                    pm.next_run_time = None
                pm.current_task_name = None
                self._enqueue_instance(idx, profile_name)

        if self._task_schedule is not None and self._selected_instances:
            self._persist_schedules()
        self.launch_next_instances()
        return True

    def _persist_schedules(self):
        """Persist the running task schedule of every instance (thread-safe).

        Only active when the schedule memory is enabled and at least one
        instance is selected (an empty selection must never wipe the saved
        schedule): worker threads call this after every task run so a crash
        or shutdown never loses state.

        Only the tasks of the selected instances are updated; entries of tasks
        that are not in the current profile (e.g. from a previous profile of
        the same instance) are kept so they can be resumed later.
        """
        if self._task_schedule is None or not self._selected_instances:
            return
        with self._schedule_lock:
            for idx in self._selected_instances:
                pm = self.instances_profile_managers.get(idx)
                if pm is None:
                    continue
                instance_entry = self._task_schedule.setdefault(str(idx), {})
                instance_entry.update(snapshot_instance_schedule(getattr(pm, "running_tasks_state", []) or []))
            if not save_task_schedule(self._task_schedule):
                self.log_message("Error saving the task schedule.", level="error")

    def _queue_sort_key(self, item):
        """Key to order the queue by the instance's next task time (earlier = first).

        The queue does not follow arrival order: the instance whose next task is
        closest goes first. Python's sort is stable, so ties keep arrival order.
        """
        idx, _profile_name = item
        pm = self.instances_profile_managers.get(idx)
        now = time.time()
        if pm and hasattr(pm, "running_tasks_state") and pm.running_tasks_state:
            return min(t.get("next_run_time", now) for t in pm.running_tasks_state)
        return now

    def launch_next_instances(self):
        """Launches the next set of emulator instances up to the maximum allowed."""
        max_simul = safe_int(self.max_instances_var.get() if self.max_instances_var else 2, 2)

        while not self.tool_should_stop.is_set():
            with self._state_lock:
                if len(self.active_instances) >= max_simul or not self.instance_queue:
                    return
                # Prioritize the instance whose next task is soonest, not the one
                # that arrived first to the queue. Ties keep the arrival order.
                self.instance_queue.sort(key=self._queue_sort_key)
                idx, profile_name = self.instance_queue.pop(0)
                if idx in self.active_instances:
                    continue
                pm = self.instances_profile_managers.get(idx)
                next_run_time = None
                now = time.time()
                if pm and hasattr(pm, "running_tasks_state") and pm.running_tasks_state:
                    next_run_time = min(t.get("next_run_time", now) for t in pm.running_tasks_state)
                if next_run_time is not None and next_run_time - now > 120:
                    self.instance_queue.insert(0, (idx, profile_name))
                    return
                self.active_instances.add(idx)
                thread = threading.Thread(
                    target=self.run_profile_on_instance_with_slot,
                    args=(idx, profile_name),
                    daemon=True,
                    name=f"Instance-{idx}",
                )
                self.instance_threads[idx] = thread
            thread.start()
            self.cleanup_memory()

    def _requeue_with_limit(self, index, profile_name):
        """Re-queues an instance for retry, giving up after max_launch_attempts.

        Prevents the infinite loop of starting/closing the emulator when the
        game or ADB keeps failing to come up.
        """
        with self._state_lock:
            attempts = self.instance_launch_attempts.get(index, 0) + 1
            self.instance_launch_attempts[index] = attempts
            self.active_instances.discard(index)
        if attempts >= self.max_launch_attempts:
            self.log_message(
                f"Instance {index} has failed {attempts} consecutive startup attempts. Stopping it to avoid an infinite loop.",
                level="error",
            )
            # The failed instance already released its slot above. Wake the
            # queue so another selected instance can use that slot.
            self.launch_next_instances()
            return
        self._enqueue_instance(index, profile_name)
        self.launch_next_instances()

    def run_profile_on_instance_with_slot(self, index, profile_name):
        """Runs the profile automation for a specific emulator instance in a separate thread.

        Handles emulator startup, ADB connection, game launch, and task scheduling.

        Args:
            index (int): Emulator instance index.
            profile_name (str): Name of the profile to run.
        """
        from wosutil.emulator.emulator_manager import (
            is_wos_installed,
            verify_adb_connected,
        )
        from wosutil.tool.profiles.profile_manager import ProfileManager
        from wosutil.tool.tasks.task_helpers import launch_and_reach_city_screen

        def instance_worker():
            try:
                pm = self.instances_profile_managers.get(index)
                if pm is None:
                    pm = ProfileManager(self.log_message)
                    self.instances_profile_managers[index] = pm
                # Opening phase (emulator start, ADB, game launch): the GUI shows
                # "Opening..." until the game is ready to run tasks.
                pm.opening_state = True

                # Use retry operation for emulator startup
                def start_emulator():
                    running = self.multi_instance_manager._is_instance_running(index)
                    if not running:
                        self.multi_instance_manager.start_instance(index)
                    else:
                        self.log_message(f"Emulator on instance {index} is already running.", "info")
                    # start_instance already waits for state=start_finished;
                    # verify_adb_connected below waits until the device is reachable.
                    return True

                # Retry emulator startup with exponential backoff
                if not retry_operation(start_emulator, max_attempts=3, delay=5.0, retry_on_false=True):
                    self.log_message(f"Failed to start emulator on instance {index}. Closing any partial startup and trying next instance.", "error")
                    # Try to close the emulator if it was partially started
                    try:
                        self.multi_instance_manager.stop_instance(index)
                        self.log_message(f"Emulator instance {index} closed due to startup failure.", "info")
                    except Exception as e:
                        self.log_message(f"Error closing emulator instance {index}: {e}", "error")
                    self._discard_active_instance(index)
                    self.launch_next_instances()
                    return

                # Retry ADB connection with reduced attempts and better error handling
                def connect_adb():
                    return verify_adb_connected(instance_index=index)

                # Add timeout protection for ADB connection phase
                adb_start_time = time.time()
                adb_timeout = 60  # 60 seconds max for ADB connection phase

                # Reduce max attempts from 10 to 5 to avoid long hangs
                if not retry_operation(connect_adb, max_attempts=5, delay=3.0, retry_on_false=True):
                    # Check if we've exceeded the timeout for ADB connection phase
                    if time.time() - adb_start_time > adb_timeout:
                        self.log_message(
                            f"ADB connection phase timed out after {adb_timeout} seconds for instance {index}. Instance appears stuck, re-queuing for retry.",
                            "error",
                        )
                        try:
                            self.multi_instance_manager.stop_instance(index)
                            self.log_message(f"Emulator instance {index} closed due to ADB timeout.", "info")
                        except Exception as e:
                            self.log_message(f"Error closing emulator instance {index}: {e}", "error")
                        self._requeue_with_limit(index, profile_name)
                        return

                    self.log_message(f"Could not connect ADB to instance {index} after 5 attempts. Checking emulator health...", "error")

                    # Check if emulator is hanging
                    if not check_emulator_health(index):
                        self.log_message(f"Emulator instance {index} is hanging. Attempting force restart...", "warning")

                        # Try force restart
                        if force_restart_emulator(index, self.multi_instance_manager):
                            # Try ADB connection again after restart
                            if retry_operation(connect_adb, max_attempts=3, delay=2.0, retry_on_false=True):
                                self.log_message(f"ADB connection successful after force restart for instance {index}.", "success")
                            else:
                                self.log_message(f"ADB connection still failed after force restart for instance {index}. Re-queuing instance for retry.", "error")
                                self._requeue_with_limit(index, profile_name)
                                return
                        else:
                            self.log_message(f"Force restart failed for instance {index}. Re-queuing instance for retry.", "error")
                            self._requeue_with_limit(index, profile_name)
                            return
                    else:
                        # Emulator seems healthy but ADB still fails, try manual restart
                        self.log_message(f"Emulator instance {index} appears healthy but ADB connection fails. Attempting manual restart...", "warning")
                        try:
                            self.multi_instance_manager.stop_instance(index)
                            time.sleep(3)  # Wait for emulator to close
                            self.multi_instance_manager.start_instance(index)
                            time.sleep(5)  # Wait for emulator to start

                            # Try ADB connection again after restart
                            if retry_operation(connect_adb, max_attempts=3, delay=2.0, retry_on_false=True):
                                self.log_message(f"ADB connection successful after manual restart for instance {index}.", "success")
                            else:
                                self.log_message(f"ADB connection still failed after manual restart for instance {index}. Re-queuing instance for retry.", "error")
                                self._requeue_with_limit(index, profile_name)
                                return
                        except Exception as e:
                            self.log_message(f"Error during manual emulator restart for instance {index}: {e}", "error")
                            self._requeue_with_limit(index, profile_name)
                            return

                if self.tool_should_stop.is_set():
                    self._discard_active_instance(index)
                    return

                # The game missing is a permanent condition: closing/restarting
                # the emulator can never fix it, so warn the user and abort this
                # instance instead of entering the start/stop retry loop.
                if not is_wos_installed(index):
                    self.log_message(
                        f"Whiteout Survival ({WHITEOUT_PACKAGE}) is not installed on instance {index}. "
                        "Install the game on this instance (e.g. from the emulator's app store or Google Play) "
                        "and press Start again. The instance will not be retried automatically.",
                        "error",
                    )
                    if self.dialog_queue is not None:
                        self.dialog_queue.put(
                            (
                                "Game not installed",
                                f"Whiteout Survival ({WHITEOUT_PACKAGE}) is not installed on instance {index}.\n\n"
                                "Install the game on this instance (e.g. from the emulator's app store or Google Play) "
                                "and press Start again. The instance will not be retried automatically.",
                            )
                        )
                    try:
                        self.multi_instance_manager.stop_instance(index)
                        self.log_message(f"Emulator instance {index} closed because the game is not installed.", "info")
                    except Exception as e:
                        self.log_message(f"Error closing emulator instance {index}: {e}", "error")
                    self._discard_active_instance(index)
                    return

                # Retry game launch usando retry_operation
                def try_launch_game():
                    return launch_and_reach_city_screen(instance_index=index)

                # Add timeout protection for game launch phase
                game_launch_start_time = time.time()
                game_launch_timeout = 120  # 120 seconds max for game launch phase

                if not retry_operation(try_launch_game, max_attempts=3, delay=5.0, retry_on_false=True):
                    # Check if we've exceeded the timeout for game launch phase
                    if time.time() - game_launch_start_time > game_launch_timeout:
                        self.log_message(
                            f"Game launch phase timed out after {game_launch_timeout} seconds for instance {index}. Instance appears stuck, re-queuing for retry.",
                            "error",
                        )
                        try:
                            self.multi_instance_manager.stop_instance(index)
                            self.log_message(f"Emulator instance {index} closed due to game launch timeout.", "info")
                        except Exception as e:
                            self.log_message(f"Error closing emulator instance {index}: {e}", "error")
                        self._requeue_with_limit(index, profile_name)
                        return

                    self.log_message(f"Critical failure launching the game on instance {index}. Closing and re-queuing.", "error")

                    def requeue_instance():
                        try:
                            self.multi_instance_manager.stop_instance(index)
                            self.log_message(f"Emulator instance {index} closed due to game launch failure.", "info")
                        except Exception as e:
                            self.log_message(f"Error closing emulator instance {index}: {e}", "error")
                        self._requeue_with_limit(index, profile_name)

                    requeue_instance()
                    return

                # Game launched successfully: reset consecutive launch failure counter
                with self._state_lock:
                    self.instance_launch_attempts[index] = 0
                pm.opening_state = False

                # The game clock (UTC date and time) is the first thing to sync
                # every time an instance is opened: several tasks reschedule to
                # 00:00 UTC and need the clock to compute the exact delay.
                if self.tool_should_stop.is_set():
                    self._discard_active_instance(index)
                    return
                try:
                    sync_utc_time(index)
                except Exception as e:
                    self.log_message(f"Error syncing the game UTC clock on instance {index}: {e}", "error")

                # Continue with task execution...
                pm = self.instances_profile_managers.get(index)
                if not pm:
                    pm = ProfileManager(self.log_message)
                    self.instances_profile_managers[index] = pm
                    # Only initialize tasks if this is the first time for this instance
                    pm.running_tasks_state = build_running_tasks_state(profile_name, pm.profiles, self.TASK_DEFINITIONS)
                # If pm already exists, keep the existing task state to preserve scheduled times

                # Main task loop
                last_health_check = time.time()
                health_check_interval = 60  # Check emulator health every 60 seconds

                while not self.tool_should_stop.is_set():
                    now = time.time()

                    # Periodic health check
                    if now - last_health_check > health_check_interval:
                        if not check_emulator_health(index):
                            self.log_message(f"Emulator instance {index} appears to be hanging during operation. Attempting restart...", "warning")
                            if force_restart_emulator(index, self.multi_instance_manager):
                                self.log_message(f"Emulator instance {index} restarted successfully during operation.", "success")
                                # Re-verify ADB connection after restart
                                if not retry_operation(connect_adb, max_attempts=3, delay=2.0, retry_on_false=True):
                                    self.log_message(f"ADB connection failed after restart during operation for instance {index}. Closing instance.", "error")
                                    self._discard_active_instance(index)
                                    self.launch_next_instances()
                                    break
                            else:
                                self.log_message(f"Force restart failed during operation for instance {index}. Closing instance.", "error")
                                self._discard_active_instance(index)
                                self.launch_next_instances()
                                break
                        last_health_check = now

                    # Choose the task to run or wait for, grouping close run
                    # times so the priority order wins over timer-reading noise.
                    next_task, wait_until = pick_scheduled_task(getattr(pm, "running_tasks_state", []), now)
                    if next_task is None:
                        # No scheduled tasks, exit
                        self.log_message(f"No tasks scheduled for profile '{profile_name}' on instance {index}.", "info")
                        self._discard_active_instance(index)
                        self.launch_next_instances()
                        break
                    if wait_until is not None:
                        sleep_time = max(1, int(wait_until - now))
                        # If the next task is more than 120 seconds away, close the emulator, free the slot and requeue
                        if wait_until - now > 120:
                            self.log_message(
                                f"The next task for instance {index} is more than 120 seconds away. Closing the emulator, freeing the slot and requeuing.",
                                "info",
                            )
                            self.multi_instance_manager.stop_instance(index)
                            self._discard_active_instance(index)
                            self._enqueue_instance(index, profile_name)
                            self.launch_next_instances()
                            break
                        pm.current_task_name = None
                        # Wait reactive to stop
                        self.tool_should_stop.wait(timeout=min(sleep_time, 10))
                        continue
                    # Run the pending task with the highest priority
                    task = next_task
                    pm.current_task_name = task.get("name", "?")
                    self.log_message(f"Executing task '{pm.current_task_name}' on instance {index}...", "info")
                    try:
                        # If stop was pressed, do not run more tasks
                        if self.tool_should_stop.is_set():
                            break
                        result = task["function"](instance_index=index)
                        # Support for functions returning (result, reschedule_seconds)
                        if isinstance(result, tuple) and len(result) == 2:
                            result, new_reschedule = result
                            if isinstance(new_reschedule, (int, float)) and new_reschedule > 0:
                                task["reschedule_seconds"] = new_reschedule
                        if result:
                            self.log_message(f"Task '{pm.current_task_name}' completed successfully on instance {index}.", "success")
                        else:
                            self.log_message(f"Task '{pm.current_task_name}' failed on instance {index}.", "warning")
                    except Exception as e:
                        self.log_message(f"Exception while running task '{pm.current_task_name}' on instance {index}: {e}", "error")
                    # Reschedule the task
                    task["next_run_time"] = time.time() + task.get("reschedule_seconds", 60)
                    # Reschedule tasks that must run right after this one (the loop is serial per instance)
                    for other in pm.running_tasks_state:
                        if other.get("run_after") == task.get("id"):
                            other["next_run_time"] = time.time()
                    pm.next_run_time = min(t["next_run_time"] for t in pm.running_tasks_state)
                    pm.current_task_name = None
                    self._persist_schedules()
                    # Short wait before the next iteration, reactive to stop
                    self.tool_should_stop.wait(timeout=2)
                self.log_message(f"Task cycle finished for instance {index}.", "info")

            except ToolStopped:
                self.log_message(f"Tool stopped while working on instance {index}.", "info")
                self._discard_active_instance(index)

            except Exception as e:
                self.log_message(f"Error in instance {index}: {e}", "error")
                # Close the emulator on any error
                try:
                    self.multi_instance_manager.stop_instance(index)
                    self.log_message(f"Emulator instance {index} closed due to error.", "info")
                except Exception as close_error:
                    self.log_message(f"Error closing emulator instance {index}: {close_error}", "error")
                self._discard_active_instance(index)
                self.launch_next_instances()
            finally:
                self._discard_active_instance(index)
                self._remove_thread_reference(index, threading.current_thread())

        instance_worker()

    def stop_tool(self):
        """Stops the automation tool and cleans up resources."""
        with self._state_lock:
            self.tool_running = False
            self.tool_should_stop.set()
            active_instances = list(self.active_instances)
            worker_threads = list(self.instance_threads.items())

        # Close all active emulators before cleaning up
        for idx in active_instances:
            try:
                self.log_message(f"Closing emulator of instance {idx} while stopping the tool...", "info")
                self.multi_instance_manager.stop_instance(idx)
            except Exception as e:
                self.log_message(f"Error closing emulator of instance {idx}: {e}", "error")

        # Wait for threads to finish
        for _index, thread in worker_threads:
            if thread.is_alive():
                thread.join(timeout=5.0)

        with self._state_lock:
            live_threads = [thread for thread in self.instance_threads.values() if thread.is_alive()]
            self.active_instances.clear()
            self.instance_queue.clear()
            self.instance_launch_attempts.clear()
            if not live_threads:
                self.instance_threads.clear()

        # Force garbage collection
        gc.collect()

        if live_threads:
            self.log_message(
                f"Tool stop requested, but {len(live_threads)} worker thread(s) are still finishing.",
                level="warning",
            )
        self.log_message("Tool stopped and resources cleaned up", level="info")
