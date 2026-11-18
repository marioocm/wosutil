"""Headless application service shared by every front-end.

The service owns the controller, the instance selection and the scheduling
loop. Views never talk to the emulator backend or the controller directly:
they read snapshots and forward commands.
"""

import queue
import threading
import time
from typing import Optional

from wosutil.app import preferences as preferences_module
from wosutil.app.actions import ActionResult
from wosutil.app.events import EventBus, Subscription
from wosutil.app.log_buffer import LogBuffer
from wosutil.app.presentation import format_instance_status, format_task_tooltip
from wosutil.app.profiles import delete_profile as delete_profile_action
from wosutil.app.profiles import save_profile as save_profile_action
from wosutil.app.selection import load_instance_selection, save_instance_selection
from wosutil.emulator.backends import create_backend, detect_installed_emulators
from wosutil.emulator.instance_cache import load_instance_cache, save_instance_cache
from wosutil.preferences import get_emulator_paths, mark_requirements_reminder_seen, save_emulator
from wosutil.tool.tool_instances_controller import DEFAULT_MAX_INSTANCES, MultiInstanceToolController
from wosutil.utils import add_log_observer, remove_log_observer, safe_int
from wosutil.utils import log_message as default_log_message

SCHEDULER_INTERVAL_SECONDS = 1.0


class WosUtilService:
    """Own the application state and expose use cases to any view."""

    def __init__(
        self,
        profile_manager,
        task_definitions: dict,
        backend,
        emulator: str,
        bus: Optional[EventBus] = None,
        log_buffer: Optional[LogBuffer] = None,
        log_message=None,
    ):
        """Wire the service around an already built backend.

        Args:
            profile_manager: Manager owning the task profiles.
            task_definitions: Task definition catalog.
            backend: Active emulator backend.
            emulator: Emulator code of ``backend``.
            bus: Event bus shared with the views. A new one is created when
                omitted.
            log_buffer: Log history shared with the views. A new one is created
                when omitted.
            log_message: Logging function. Defaults to ``wosutil.utils``.
        """
        self._log = log_message or default_log_message
        self._bus = bus or EventBus()
        self._log_buffer = log_buffer or LogBuffer(self._bus)
        add_log_observer(self._log_buffer.observe)

        self._profile_manager = profile_manager
        self._task_definitions = task_definitions
        self._backend = backend
        self._emulator = emulator

        self._lock = threading.RLock()
        self._selection = load_instance_selection()
        self._max_instances = max(1, safe_int(self._selection.get("max_emulators"), DEFAULT_MAX_INSTANCES))
        self._instances = load_instance_cache()
        self._installed_emulators = detect_installed_emulators(get_emulator_paths())

        self._dialog_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._controller = MultiInstanceToolController(
            log_message=self._log,
            task_definitions=task_definitions,
            backend=backend,
            profiles=profile_manager.profiles,
            dialog_queue=self._dialog_queue,
        )
        self._controller.set_max_instances(self._max_instances)

        self._scheduler_stop = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None

    # --- Views ---

    def subscribe(self) -> Subscription:
        """Return a subscription that receives ``state``, ``log`` and ``dialog`` events."""
        return self._bus.subscribe()

    def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a subscription returned by :meth:`subscribe`."""
        self._bus.unsubscribe(subscription)

    def logs(self, limit: int = 300) -> list[dict]:
        """Return the most recent log lines (newest last)."""
        return self._log_buffer.tail(limit)

    def logs_after(self, seq: int, limit: int = 500) -> list[dict]:
        """Return the log lines recorded after ``seq``."""
        return self._log_buffer.after(seq, limit)

    def snapshot(self) -> dict:
        """Return the full view state.

        The result only contains JSON-serializable values so the same snapshot
        can feed the Tkinter shell and the future web front-end.
        """
        now = time.time()
        with self._lock:
            selection = dict(self._selection)
            instances = list(self._instances)
            max_instances = self._max_instances
            emulator = self._emulator
            installed = list(self._installed_emulators)
            profiles = {name: list(task_ids) for name, task_ids in self._profile_manager.profiles.items()}

        running = self._controller.tool_running
        first_profile = next(iter(profiles), "")
        rows = []
        for instance in instances:
            index = instance["index"]
            entry = selection.get(str(index), {})
            checked = bool(entry.get("checked", False))
            status_text = ""
            tooltip = ""
            if running and checked:
                state = self._controller.state_for(index)
                status_text = format_instance_status(
                    state,
                    now=now,
                    active=self._controller.is_active(index),
                    aborted=self._controller.is_aborted(index),
                    queue_position=self._controller.pending_position(index),
                    retry_seconds=self._controller.retry_seconds_remaining(index, now),
                )
                tooltip = format_task_tooltip(state, now)
            rows.append(
                {
                    "index": index,
                    "name": instance["name"],
                    "checked": checked,
                    "profile": entry.get("profile") or first_profile,
                    "status": status_text,
                    "tooltip": tooltip,
                    "active": self._controller.is_active(index),
                }
            )

        option_codes = installed + [code for code in preferences_module.EMULATOR_LABELS if code not in installed]
        return {
            "emulator": emulator,
            "emulator_label": preferences_module.EMULATOR_LABELS.get(emulator, emulator),
            "emulator_options": [{"code": code, "label": preferences_module.EMULATOR_LABELS.get(code, code), "installed": code in installed} for code in option_codes],
            "tool_running": running,
            "max_instances": max_instances,
            "instances": rows,
            "profiles": profiles,
            "tasks": self.task_catalog(),
        }

    def task_catalog(self) -> list[dict]:
        """Return the task definitions in priority order."""
        tasks = [{"id": task_id, "name": task["name"], "description": task.get("description", ""), "priority": task["priority"]} for task_id, task in self._task_definitions.items()]
        tasks.sort(key=lambda task: task["priority"])
        return tasks

    def preferences(self) -> dict:
        """Return the current preference values for a preferences form."""
        return preferences_module.preferences_view(self._task_definitions)

    # --- Lifecycle ---

    def start(self) -> None:
        """Start the scheduling loop and publish the initial state."""
        if self._scheduler_thread is not None:
            return
        self._scheduler_stop.clear()
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="AppScheduler")
        self._scheduler_thread.start()
        self.publish_state()

    def close(self) -> None:
        """Stop the scheduling loop and release the log observer."""
        self._scheduler_stop.set()
        thread = self._scheduler_thread
        self._scheduler_thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        remove_log_observer(self._log_buffer.observe)
        self._bus.close()

    def publish_state(self) -> None:
        """Publish a state snapshot to every subscribed view."""
        self._bus.publish("state", self.snapshot())

    def tick(self) -> None:
        """Run one scheduler step: dialogs, due instances and a state snapshot."""
        try:
            self._drain_dialogs()
            if self._controller.tool_running:
                self._controller.launch_due_instances()
            self.publish_state()
        except Exception as exc:  # Keep the scheduler alive; a view bug must not stop the tool.
            self._log(f"Scheduler error: {exc}", "error")

    def _scheduler_loop(self) -> None:
        """Call :meth:`tick` once per second until the service is closed."""
        while not self._scheduler_stop.wait(SCHEDULER_INTERVAL_SECONDS):
            self.tick()

    def _drain_dialogs(self) -> None:
        """Forward dialogs requested by worker threads to the views."""
        while True:
            try:
                title, text = self._dialog_queue.get_nowait()
            except queue.Empty:
                return
            self._bus.publish("dialog", {"title": title, "text": text})

    # --- Instances ---

    def refresh_instances(self, force: bool = False) -> ActionResult:
        """Reload the instance list.

        Args:
            force: Query the emulator backend instead of reading the cache.

        Returns:
            ActionResult: Whether the list was refreshed.
        """
        try:
            if force:
                with self._lock:
                    backend = self._backend
                instances = backend.get_instances()
                save_instance_cache(instances)
                installed = detect_installed_emulators(get_emulator_paths())
                with self._lock:
                    self._instances = instances
                    self._installed_emulators = installed
            else:
                with self._lock:
                    self._instances = load_instance_cache()
        except Exception as exc:
            message = f"Could not refresh the emulator instances: {exc}"
            self._log(message, "error")
            return ActionResult(False, message)
        self.publish_state()
        return ActionResult(True)

    def update_instance_selection(self, index: int, checked: bool, profile: str) -> ActionResult:
        """Persist the checkbox and profile of one instance row."""
        with self._lock:
            self._selection[str(index)] = {"checked": bool(checked), "profile": profile}
            self._selection["max_emulators"] = self._max_instances
            save_instance_selection(self._selection)
        self.publish_state()
        return ActionResult(True)

    def set_max_instances(self, value: int) -> ActionResult:
        """Set how many emulators may stay open at the same time."""
        max_instances = max(1, safe_int(value, DEFAULT_MAX_INSTANCES))
        with self._lock:
            self._max_instances = max_instances
            self._selection["max_emulators"] = max_instances
            save_instance_selection(self._selection)
        self._controller.set_max_instances(max_instances)
        self.publish_state()
        return ActionResult(True)

    def selected_instances(self) -> list[tuple[int, str]]:
        """Return the ``(index, profile)`` pairs selected for a run."""
        with self._lock:
            selection = dict(self._selection)
            instances = list(self._instances)
            first_profile = next(iter(self._profile_manager.profiles), "")
        selected = []
        for instance in instances:
            entry = selection.get(str(instance["index"]), {})
            if entry.get("checked"):
                selected.append((instance["index"], entry.get("profile") or first_profile))
        return selected

    # --- Tool ---

    def start_tool(self) -> ActionResult:
        """Start the tool on the selected instances."""
        with self._lock:
            backend = self._backend
        warnings = backend.check_adb_access()
        if warnings:
            for warning in warnings:
                self._log(warning, "error")
            self._log("Tool not started: ADB access to the emulator is blocked. Enable ADB and press Start again.", "error")
            return ActionResult(False, "\n\n".join(warnings))
        started = self._controller.start_tool(self.selected_instances())
        self.publish_state()
        return ActionResult(started)

    def stop_tool(self) -> ActionResult:
        """Stop the tool and let the controller close its workers."""
        self._controller.stop_tool()
        self.publish_state()
        return ActionResult(True)

    def set_emulator(self, emulator: str, emulator_paths: Optional[dict] = None) -> ActionResult:
        """Replace the active backend without restarting the application.

        Args:
            emulator: Emulator code to activate.
            emulator_paths: Configured paths. Defaults to the persisted ones.

        Returns:
            ActionResult: False when a run is in progress.
        """
        if self._controller.tool_running:
            self._log("Emulator change ignored while the tool is running. It will apply on the next run.", "warning")
            return ActionResult(False, "The emulator cannot be changed while the tool is running.")

        if emulator == self._emulator and emulator_paths is None:
            save_emulator(emulator)
            return ActionResult(True)

        label = preferences_module.EMULATOR_LABELS.get(emulator, emulator)
        self._log(f"Switching emulator to {label}...", "info")
        save_emulator(emulator)
        paths = emulator_paths or get_emulator_paths()
        backend = create_backend(emulator, self._log, emulator_paths=paths)
        with self._lock:
            self._backend = backend
            self._emulator = emulator
        self._controller.set_backend(backend)
        self.refresh_instances(force=True)
        return ActionResult(True)

    # --- Preferences and profiles ---

    def save_preferences(self, values: dict) -> ActionResult:
        """Validate and persist the preferences form."""
        with self._lock:
            result = preferences_module.apply_preferences(values, self._task_definitions, self._log)
        if result.ok and result.emulator_changed and result.emulator:
            self.set_emulator(result.emulator, result.emulator_paths)
        self.publish_state()
        return ActionResult(result.ok, result.error)

    def save_profile(self, name: str, task_ids: list) -> ActionResult:
        """Create or replace a profile."""
        with self._lock:
            result = save_profile_action(self._profile_manager, name, task_ids, set(self._task_definitions))
        self.publish_state()
        return ActionResult(result.ok, result.error)

    def delete_profile(self, name: str) -> ActionResult:
        """Delete a profile."""
        with self._lock:
            result = delete_profile_action(self._profile_manager, name)
        self.publish_state()
        return ActionResult(result.ok, result.error)

    def ack_requirements_reminder(self) -> ActionResult:
        """Remember that the first-run requirements reminder was shown."""
        mark_requirements_reminder_seen()
        return ActionResult(True)
