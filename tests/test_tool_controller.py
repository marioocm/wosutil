"""Unit tests for the multi-instance tool controller."""

import queue
import time
import unittest
from unittest.mock import MagicMock, patch

from wosutil.emulator.emulator_manager import AdbCommandError, get_active_backend, set_active_backend
from wosutil.stop import stop_signal
from wosutil.tool.tool_instances_controller import InstanceRuntime, InstanceState, MultiInstanceToolController, pick_scheduled_task

TASK_A = {"id": "a", "name": "Task A", "function": lambda instance_index: True, "priority": 1, "reschedule_seconds": 1000}
TASK_B = {"id": "b", "name": "Task B", "function": lambda instance_index: True, "priority": 2, "reschedule_seconds": 2000}
TASK_C = {"id": "c", "name": "Task C", "function": lambda instance_index: True, "priority": 3, "reschedule_seconds": 3000}
TASK_DEFINITIONS = {"a": TASK_A, "b": TASK_B, "c": TASK_C}


class FakeBackend:
    """Minimal emulator backend stub."""

    def __init__(self, warnings=None, start_result=True):
        """Initialize the stub."""
        self.warnings = list(warnings or [])
        self.start_result = start_result
        self.start_calls = 0
        self.stop_calls = 0

    def check_adb_access(self):
        """Return the configured ADB warnings."""
        return list(self.warnings)

    def start_instance(self, index):
        """Record a start and return the configured result."""
        self.start_calls += 1
        return self.start_result

    def stop_instance(self, index):
        """Record a stop."""
        self.stop_calls += 1
        return True

    def get_serial(self, index):
        """Return a stable serial for the stub."""
        return f"serial-{index}"


class ControllerTestCase(unittest.TestCase):
    """Base test case with a controller factory and clean stop signal."""

    def setUp(self):
        """Ensure the global stop signal is clear between tests."""
        stop_signal.clear()

    def tearDown(self):
        """Do not leak the stop signal or the active backend into other tests."""
        stop_signal.clear()
        set_active_backend(None)

    def _make_controller(self, backend=None, dialogs=None):
        """Build a controller with mock dependencies and a log capture."""
        self.logs = []
        return MultiInstanceToolController(
            log_message=lambda msg, level="info": self.logs.append((msg, level)),
            task_definitions=dict(TASK_DEFINITIONS),
            backend=backend or FakeBackend(),
            profiles={"All": ["a", "b"], "All2": ["a", "c"]},
            dialog_queue=dialogs,
        )

    def _state(self, tasks=None, profile_name="All"):
        """Build an instance state with the given running tasks."""
        return InstanceState(profile_name=profile_name, tasks=list([dict(TASK_A)] if tasks is None else tasks))

    def _add_instance(self, controller, index=0, tasks=None, profile_name="All"):
        """Register a selected instance in the controller."""
        runtime = InstanceRuntime(state=self._state(tasks, profile_name))
        controller.instances[index] = runtime
        return runtime


class TestStartToolAbort(ControllerTestCase):
    """Tests for the start-time ADB access guard and session locking."""

    def test_start_tool_aborts_when_adb_blocked(self):
        """start_tool refuses to run and logs the warning when ADB is blocked."""
        controller = self._make_controller(FakeBackend(warnings=["ADB access is blocked."]))
        self.assertFalse(controller.start_tool([]))
        self.assertFalse(controller.tool_running)
        self.assertTrue(any("ADB access is blocked" in msg for msg, _level in self.logs))

    def test_controller_owns_the_active_backend(self):
        """Creating or replacing the backend updates the ADB target too."""
        first = FakeBackend()
        controller = self._make_controller(first)
        self.assertIs(get_active_backend(), first)

        second = FakeBackend()
        controller.set_backend(second)
        self.assertIs(get_active_backend(), second)

    def test_start_tool_runs_when_adb_ok(self):
        """start_tool runs normally when the backend has no warnings."""
        controller = self._make_controller()
        with patch("wosutil.tool.tool_instances_controller.get_remember_schedule", return_value=False):
            self.assertTrue(controller.start_tool([]))
        self.assertTrue(controller.tool_running)

    def test_start_tool_refuses_to_overlap_a_running_session(self):
        """A second start cannot reset the state of the current session."""
        controller = self._make_controller()
        controller.tool_running = True

        self.assertFalse(controller.start_tool([]))
        self.assertTrue(controller.tool_running)
        self.assertTrue(any("already running" in msg for msg, _level in self.logs))

    def test_start_tool_refuses_while_a_previous_session_is_stopping(self):
        """A live worker from the previous session blocks a new start."""
        controller = self._make_controller()
        worker = MagicMock()
        worker.is_alive.return_value = True
        controller.instances[0] = InstanceRuntime(state=self._state(), thread=worker)

        self.assertFalse(controller.start_tool([]))
        self.assertFalse(controller.tool_running)
        self.assertTrue(any("still stopping" in msg for msg, _level in self.logs))

    def test_stop_tool_keeps_reference_to_worker_that_is_still_alive(self):
        """Stopping does not forget a thread that exceeded the join timeout."""
        backend = FakeBackend()
        controller = self._make_controller(backend)
        worker = MagicMock()
        worker.is_alive.return_value = True
        controller.tool_running = True
        controller.instances[0] = InstanceRuntime(state=self._state(), thread=worker)

        controller.stop_tool()

        self.assertIs(controller.instances[0].thread, worker)
        worker.join.assert_called_once_with(timeout=5.0)
        self.assertEqual(backend.stop_calls, 1)
        self.assertTrue(controller.is_active(0))


class TestGameNotInstalledAbort(ControllerTestCase):
    """The instance aborts without retries when the game is not installed."""

    def test_worker_aborts_without_requeue_when_game_missing(self):
        """Missing game: clear error, one stop, no relaunch and no retry."""
        backend = FakeBackend()
        controller = self._make_controller(backend)
        runtime = self._add_instance(controller, tasks=[])
        with (
            patch("wosutil.tool.tool_instances_controller.verify_adb_connected", return_value=True),
            patch("wosutil.tool.tool_instances_controller.is_wos_installed", return_value=False),
            patch("wosutil.tool.tasks.navigation.launch_and_reach_city_screen") as mock_launch,
        ):
            controller._run_instance(0)
        mock_launch.assert_not_called()
        self.assertEqual(backend.stop_calls, 1)
        self.assertEqual(backend.start_calls, 1)
        self.assertFalse(controller.is_active(0))
        self.assertEqual(runtime.launch_attempts, 0)
        self.assertTrue(controller.is_aborted(0))
        self.assertTrue(any("not installed" in msg for msg, _level in self.logs))

    def test_worker_queues_dialog_when_game_missing(self):
        """A (title, text) dialog request is queued for the GUI main thread."""
        dialogs = queue.Queue()
        controller = self._make_controller(dialogs=dialogs)
        self._add_instance(controller, tasks=[])
        with (
            patch("wosutil.tool.tool_instances_controller.verify_adb_connected", return_value=True),
            patch("wosutil.tool.tool_instances_controller.is_wos_installed", return_value=False),
            patch("wosutil.tool.tasks.navigation.launch_and_reach_city_screen") as mock_launch,
        ):
            controller._run_instance(0)
        mock_launch.assert_not_called()
        title, text = dialogs.get_nowait()
        self.assertEqual(title, "Game not installed")
        self.assertIn("com.gof.global", text)


class TestLaunchFailures(ControllerTestCase):
    """Launch failures are counted, then the instance backs off."""

    def test_worker_does_not_open_an_emulator_after_stop(self):
        """A worker scheduled just before Stop must not open the emulator."""
        backend = FakeBackend()
        controller = self._make_controller(backend)
        self._add_instance(controller)
        stop_signal.set()

        controller._run_instance(0)

        self.assertEqual(backend.start_calls, 0)
        self.assertEqual(backend.stop_calls, 0)
        self.assertFalse(controller.is_active(0))

    def test_start_failure_counts_toward_retry_attempts(self):
        """A failed emulator start releases the slot and counts an attempt."""
        backend = FakeBackend(start_result=False)
        controller = self._make_controller(backend)
        runtime = self._add_instance(controller)

        controller._run_instance(0)

        self.assertEqual(runtime.launch_attempts, 1)
        self.assertFalse(controller.is_active(0))
        self.assertEqual(backend.stop_calls, 1)

    def test_start_failure_pauses_after_max_attempts(self):
        """Consecutive start failures block the instance for the cooldown."""
        backend = FakeBackend(start_result=False)
        controller = self._make_controller(backend)
        controller.max_launch_attempts = 1
        runtime = self._add_instance(controller)

        controller._run_instance(0)

        self.assertGreater(runtime.retry_blocked_until, time.time())
        self.assertEqual(runtime.launch_attempts, 0)

    def test_adb_failure_restarts_the_emulator_before_giving_up(self):
        """A failed ADB connection triggers one emulator restart attempt."""
        backend = FakeBackend()
        controller = self._make_controller(backend)
        runtime = self._add_instance(controller)
        with (
            patch("wosutil.tool.tool_instances_controller.verify_adb_connected", return_value=False),
            patch("wosutil.tool.tool_instances_controller.force_restart_emulator", return_value=False) as mock_restart,
        ):
            controller._run_instance(0)
        mock_restart.assert_called_once_with(0, backend)
        self.assertEqual(runtime.launch_attempts, 1)
        self.assertEqual(backend.stop_calls, 1)

    def test_game_launch_failure_counts_toward_retry_attempts(self):
        """A failed game launch releases the slot and counts an attempt."""
        backend = FakeBackend()
        controller = self._make_controller(backend)
        runtime = self._add_instance(controller)
        with (
            patch("wosutil.tool.tool_instances_controller.verify_adb_connected", return_value=True),
            patch("wosutil.tool.tool_instances_controller.is_wos_installed", return_value=True),
            patch("wosutil.tool.tool_instances_controller.retry_operation", return_value=False),
        ):
            controller._run_instance(0)
        self.assertEqual(runtime.launch_attempts, 1)
        self.assertEqual(backend.stop_calls, 1)

    def test_startup_exception_counts_toward_retry_attempts(self):
        """An ADB crash during startup must count as a launch failure."""
        backend = FakeBackend()
        controller = self._make_controller(backend)
        runtime = self._add_instance(controller)

        def run_once(operation, **_kwargs):
            return operation()

        with (
            patch("wosutil.tool.tool_instances_controller.verify_adb_connected", return_value=True),
            patch("wosutil.tool.tool_instances_controller.is_wos_installed", return_value=True),
            patch("wosutil.tool.tool_instances_controller.retry_operation", side_effect=run_once),
            patch("wosutil.tool.tasks.navigation.launch_and_reach_city_screen", side_effect=AdbCommandError("boom")),
        ):
            controller._run_instance(0)

        self.assertEqual(runtime.launch_attempts, 1)
        self.assertFalse(controller.is_active(0))
        self.assertEqual(backend.stop_calls, 1)

    def test_startup_exception_backs_off_after_max_attempts(self):
        """Consecutive startup crashes pause the instance instead of spinning."""
        backend = FakeBackend()
        controller = self._make_controller(backend)
        controller.max_launch_attempts = 1
        runtime = self._add_instance(controller)

        def run_once(operation, **_kwargs):
            return operation()

        with (
            patch("wosutil.tool.tool_instances_controller.verify_adb_connected", return_value=True),
            patch("wosutil.tool.tool_instances_controller.is_wos_installed", return_value=True),
            patch("wosutil.tool.tool_instances_controller.retry_operation", side_effect=run_once),
            patch("wosutil.tool.tasks.navigation.launch_and_reach_city_screen", side_effect=AdbCommandError("boom")),
        ):
            controller._run_instance(0)

        self.assertGreater(runtime.retry_blocked_until, time.time())


class TestInstanceWorker(ControllerTestCase):
    """The worker runs tasks and closes idle or unhealthy instances."""

    def _startup_patches(self, controller, launch_result=True):
        """Patch a successful startup up to the task loop."""
        return (
            patch("wosutil.tool.tool_instances_controller.verify_adb_connected", return_value=True),
            patch("wosutil.tool.tool_instances_controller.is_wos_installed", return_value=True),
            patch("wosutil.tool.tool_instances_controller.retry_operation", return_value=launch_result),
            patch("wosutil.tool.tool_instances_controller.sync_utc_time"),
            patch("wosutil.tool.tasks.task_automation.reconcile_bear_trap_schedule", return_value=False),
        )

    def test_idle_close_stops_the_emulator_without_requeue(self):
        """A far next task closes the emulator and the scheduler keeps the slot."""
        backend = FakeBackend()
        controller = self._make_controller(backend)
        self._add_instance(controller, tasks=[{"id": "x", "name": "X", "priority": 1, "next_run_time": time.time() + 1000}])
        patches = self._startup_patches(controller)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            controller._run_instance(0)
        self.assertEqual(backend.stop_calls, 1)
        self.assertFalse(controller.is_active(0))
        self.assertTrue(any("more than" in msg for msg, _level in self.logs))

    def test_health_check_failure_restarts_or_releases_the_slot(self):
        """An unhealthy emulator is restarted; a failed restart counts a failure."""
        backend = FakeBackend()
        controller = self._make_controller(backend)
        controller.health_check_interval = -1
        runtime = self._add_instance(controller, tasks=[{"id": "x", "name": "X", "priority": 1, "next_run_time": time.time() + 1000}])
        patches = self._startup_patches(controller)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patch("wosutil.tool.tool_instances_controller.check_emulator_health", return_value=False),
            patch("wosutil.tool.tool_instances_controller.force_restart_emulator", return_value=False) as mock_restart,
        ):
            controller._run_instance(0)
        mock_restart.assert_called_once_with(0, backend)
        self.assertEqual(runtime.launch_attempts, 1)
        self.assertEqual(backend.stop_calls, 1)

    def test_no_tasks_releases_the_slot(self):
        """An empty profile closes the instance and stops scheduling it."""
        backend = FakeBackend()
        controller = self._make_controller(backend)
        runtime = self._add_instance(controller, tasks=[])
        controller.tool_running = True
        patches = self._startup_patches(controller)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            controller._run_instance(0)
        self.assertEqual(backend.stop_calls, 1)
        self.assertEqual(runtime.state.tasks, [])


class TestScheduler(ControllerTestCase):
    """The due-time scheduler replaces the old shared queue."""

    def setUp(self):
        """Create a controller that can launch up to three instances."""
        super().setUp()
        self.controller = self._make_controller()
        self.controller.set_max_instances(3)
        self.controller.tool_running = True

    def _select(self, index, next_run_time, profile_name="All"):
        """Register a selected instance with a single scheduled task."""
        self._add_instance(self.controller, index=index, tasks=[{"id": "x", "name": "X", "priority": 1, "next_run_time": next_run_time}], profile_name=profile_name)

    def _launch(self):
        """Run one scheduler pass and return the patched Thread factory."""
        with patch("wosutil.tool.tool_instances_controller.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            self.controller.launch_due_instances()
        return mock_thread

    def test_due_instance_is_started(self):
        """An instance whose task is due starts immediately."""
        self._select(0, time.time() - 1)
        thread_factory = self._launch()
        thread_factory.return_value.start.assert_called_once()
        self.assertEqual(thread_factory.call_args.kwargs["args"], (0,))
        self.assertTrue(self.controller.is_active(0))

    def test_future_task_within_startup_window_is_started(self):
        """The instance opens shortly before its next task."""
        self._select(0, time.time() + 30)
        self._launch().return_value.start.assert_called_once()

    def test_future_task_outside_startup_window_is_not_started(self):
        """A distant task keeps the emulator closed."""
        self._select(0, time.time() + 61)
        self._launch().return_value.start.assert_not_called()
        self.assertFalse(self.controller.is_active(0))

    def test_instance_without_tasks_is_not_started(self):
        """A profile with no tasks never opens an emulator."""
        self._add_instance(self.controller, tasks=[])
        self._launch().return_value.start.assert_not_called()

    def test_aborted_instance_is_not_started(self):
        """A permanently aborted instance is never resurrected."""
        self._select(0, time.time() - 1)
        self.controller.instances[0].aborted = True
        self._launch().return_value.start.assert_not_called()

    def test_retry_blocked_instance_is_not_started_until_cooldown(self):
        """A paused instance is skipped while the retry cooldown runs."""
        self._select(0, time.time() - 1)
        self.controller.instances[0].retry_blocked_until = time.time() + 300
        self._launch().return_value.start.assert_not_called()

    def test_active_instance_is_not_started_twice(self):
        """An instance already running keeps its slot."""
        self._select(0, time.time() - 1)
        self.controller.instances[0].thread = MagicMock()
        self._launch().return_value.start.assert_not_called()

    def test_emulator_limit_is_honored(self):
        """No instance starts when every slot is busy."""
        self.controller.set_max_instances(1)
        self._select(0, time.time() - 1)
        self.controller.instances[1] = InstanceRuntime(state=self._state(tasks=[]), thread=MagicMock())
        self._launch().return_value.start.assert_not_called()

    def test_earliest_task_gets_the_free_slot_first(self):
        """The free slot goes to the instance whose task is closest."""
        now = time.time()
        self._select(0, now + 40)
        self._select(1, now - 5)
        self.controller.set_max_instances(1)
        thread_factory = self._launch()
        thread_factory.return_value.start.assert_called_once()
        self.assertEqual(thread_factory.call_args.kwargs["args"], (1,))

    def test_stale_thread_reference_does_not_block_takeover(self):
        """A dead thread entry from an old session does not block a launch."""
        self._select(0, time.time() - 1)
        dead = MagicMock()
        dead.is_alive.return_value = False
        self.controller.instances[0].thread = dead
        self._launch().return_value.start.assert_called_once()

    def test_pending_position_reflects_start_order(self):
        """Waiting instances are ordered by their next task time."""
        now = time.time()
        self._select(0, now + 200)
        self._select(1, now + 100)
        self.assertEqual(self.controller.pending_position(1), 1)
        self.assertEqual(self.controller.pending_position(0), 2)
        self.assertIsNone(self.controller.pending_position(2))

    def test_retry_seconds_remaining_zero_without_cooldown(self):
        """An instance without a cooldown reports no remaining pause."""
        self.assertEqual(self.controller.retry_seconds_remaining(0), 0.0)


class TestPickScheduledTask(unittest.TestCase):
    """The worker's task selection groups close run times by priority."""

    def _task(self, task_id, name, priority, next_run_time):
        """Build a running task state dict."""
        return {"id": task_id, "name": name, "priority": priority, "next_run_time": next_run_time}

    def test_empty_state_returns_nothing(self):
        """No scheduled tasks: nothing to run or wait for."""
        self.assertEqual(pick_scheduled_task([], 1000), (None, None))

    def test_due_task_runs_immediately_without_nearby_higher_priority(self):
        """A due task runs now unless a higher-priority one is within the window."""
        state = [self._task("a", "Due", 5, 990), self._task("b", "Later", 1, 2000)]
        task, wait_until = pick_scheduled_task(state, 1000)
        self.assertEqual(task["id"], "a")
        self.assertIsNone(wait_until)

    def test_higher_priority_task_within_window_is_wait_target(self):
        """The higher-priority task due within the window wins over the due one."""
        state = [self._task("a", "Due first", 6, 990), self._task("b", "Urgent", 1, 1003)]
        task, wait_until = pick_scheduled_task(state, 1000)
        self.assertEqual(task["id"], "b")
        self.assertEqual(wait_until, 1003)

    def test_highest_priority_of_window_is_the_wait_target(self):
        """Among in-window tasks, the highest priority is waited for, not the nearest."""
        state = [
            self._task("a", "Due first", 6, 990),
            self._task("b", "Most urgent later", 1, 1004),
            self._task("c", "Urgent sooner", 2, 1002),
        ]
        task, wait_until = pick_scheduled_task(state, 1000)
        self.assertEqual(task["id"], "b")
        self.assertEqual(wait_until, 1004)

    def test_tied_priorities_keep_earliest_time(self):
        """Tied priorities within the window keep the earliest time as target."""
        state = [
            self._task("a", "Due first", 6, 990),
            self._task("b", "Urgent read earlier", 4, 1002),
            self._task("c", "Urgent read later", 4, 1004),
        ]
        task, wait_until = pick_scheduled_task(state, 1000)
        self.assertEqual(task["id"], "b")
        self.assertEqual(wait_until, 1002)

    def test_same_priority_does_not_skip_ahead(self):
        """A same-priority task does not delay the due one."""
        state = [self._task("a", "Due", 6, 990), self._task("b", "Same priority later", 6, 1003)]
        task, wait_until = pick_scheduled_task(state, 1000)
        self.assertEqual(task["id"], "a")
        self.assertIsNone(wait_until)

    def test_nothing_due_returns_earliest_future_with_time(self):
        """With nothing due, the earliest future task is the wait target."""
        state = [self._task("a", "Sooner", 9, 1500), self._task("b", "Later", 1, 2500)]
        task, wait_until = pick_scheduled_task(state, 1000)
        self.assertEqual(task["id"], "a")
        self.assertEqual(wait_until, 1500)

    def test_highest_priority_wins_among_due_tasks(self):
        """Among due tasks, the smallest priority number runs."""
        state = [self._task("a", "Low", 10, 900), self._task("b", "High", 1, 1000)]
        task, wait_until = pick_scheduled_task(state, 1000)
        self.assertEqual(task["id"], "b")
        self.assertIsNone(wait_until)


class TestScheduleMemory(ControllerTestCase):
    """The controller restores the saved schedule when memory is enabled."""

    def _start_with_remember(self, controller, saved_schedule, profile_name="All", task_names=("a", "b")):
        """Run start_tool with the remember-schedule preference and a saved schedule."""
        controller.profiles[profile_name] = list(task_names)
        with (
            patch.object(controller, "launch_due_instances"),
            patch("wosutil.tool.tool_instances_controller.get_remember_schedule", return_value=True),
            patch("wosutil.tool.tool_instances_controller.load_task_schedule", return_value=saved_schedule),
            patch("wosutil.tool.tool_instances_controller.save_task_schedule"),
        ):
            self.assertTrue(controller.start_tool([(0, profile_name)]))
        return controller.instances[0].state.tasks

    def test_start_tool_restores_saved_next_run_times(self):
        """A saved future time keeps the remaining wait of the task."""
        controller = self._make_controller()
        future = time.time() + 5000
        tasks = self._start_with_remember(controller, {"0": {"a": {"next_run_time": future, "reschedule_seconds": 400.0}}})
        by_id = {task["id"]: task for task in tasks}
        self.assertEqual(by_id["a"]["next_run_time"], future)
        self.assertEqual(by_id["a"]["reschedule_seconds"], 400.0)
        # The task without a saved entry runs at startup (current behavior)
        self.assertLessEqual(by_id["b"]["next_run_time"], time.time())

    def test_start_tool_keeps_overdue_times_so_task_runs_immediately(self):
        """A saved time in the past stays due and runs immediately at startup."""
        controller = self._make_controller()
        tasks = self._start_with_remember(controller, {"0": {"a": {"next_run_time": 100.0}}})
        by_id = {task["id"]: task for task in tasks}
        self.assertEqual(by_id["a"]["next_run_time"], 100.0)

    def test_start_tool_keeps_schedule_of_tasks_shared_between_profiles(self):
        """Switching profile keeps the schedule of the tasks present in both."""
        controller = self._make_controller()
        saved = {
            "0": {
                "a": {"next_run_time": 5000.0, "reschedule_seconds": 400.0},
                "b": {"next_run_time": 6000.0},
            }
        }
        tasks = self._start_with_remember(controller, saved, profile_name="All2", task_names=("a", "c"))
        by_id = {task["id"]: task for task in tasks}
        # The shared task keeps its schedule; the new task runs at startup
        self.assertEqual(by_id["a"]["next_run_time"], 5000.0)
        self.assertEqual(by_id["a"]["reschedule_seconds"], 400.0)
        self.assertLessEqual(by_id["c"]["next_run_time"], time.time())

    def test_persist_keeps_entries_of_tasks_not_in_the_current_profile(self):
        """Persisting keeps the schedule of tasks that are not in the current profile."""
        controller = self._make_controller()
        saved = {"0": {"a": {"next_run_time": 5000.0}, "z": {"next_run_time": 7000.0}}}
        with (
            patch.object(controller, "launch_due_instances"),
            patch("wosutil.tool.tool_instances_controller.get_remember_schedule", return_value=True),
            patch("wosutil.tool.tool_instances_controller.load_task_schedule", return_value=saved),
            patch("wosutil.tool.tool_instances_controller.save_task_schedule") as mock_save,
        ):
            self.assertTrue(controller.start_tool([(0, "All")]))
        persisted = mock_save.call_args.args[0]
        # The profile only contains a and b, but z is kept for a future switch back
        self.assertIn("z", persisted["0"])
        self.assertIn("b", persisted["0"])

    def test_start_tool_does_not_wipe_schedule_without_selected_instances(self):
        """Starting with no instances selected never writes an empty schedule."""
        controller = self._make_controller()
        with (
            patch("wosutil.tool.tool_instances_controller.get_remember_schedule", return_value=True),
            patch("wosutil.tool.tool_instances_controller.load_task_schedule", return_value={"0": {"a": {"next_run_time": 1.0}}}),
            patch("wosutil.tool.tool_instances_controller.save_task_schedule") as mock_save,
        ):
            self.assertTrue(controller.start_tool([]))
        mock_save.assert_not_called()

    def test_start_tool_keeps_current_behavior_when_memory_disabled(self):
        """Memory off: every task is scheduled to run at startup, as before."""
        controller = self._make_controller()
        with (
            patch.object(controller, "launch_due_instances"),
            patch("wosutil.tool.tool_instances_controller.get_remember_schedule", return_value=False),
            patch("wosutil.tool.tool_instances_controller.load_task_schedule") as mock_load,
        ):
            self.assertTrue(controller.start_tool([(0, "All")]))
        mock_load.assert_not_called()
        tasks = controller.instances[0].state.tasks
        self.assertEqual(len(tasks), 2)
        for task in tasks:
            self.assertLessEqual(task["next_run_time"], time.time())


class TestSchedulePersistenceInWorker(ControllerTestCase):
    """The worker persists the schedule after every task run."""

    def _run_worker_once(self, controller, state):
        """Run the instance worker synchronously with the emulator stack stubbed."""
        controller.instances[0] = InstanceRuntime(state=state)
        with (
            patch("wosutil.tool.tool_instances_controller.verify_adb_connected", return_value=True),
            patch("wosutil.tool.tool_instances_controller.is_wos_installed", return_value=True),
            patch("wosutil.tool.tool_instances_controller.retry_operation", return_value=True),
            patch("wosutil.tool.tool_instances_controller.sync_utc_time"),
            patch("wosutil.tool.tasks.task_automation.reconcile_bear_trap_schedule", return_value=False),
        ):
            controller._run_instance(0)

    def test_worker_persists_rescheduled_task_after_run(self):
        """After a task runs and reschedules, its new times are persisted."""
        controller = self._make_controller()
        controller._task_schedule = {}
        task = dict(TASK_A)

        def fake_task(instance_index):
            stop_signal.set()
            return (True, 500)

        task["function"] = fake_task
        with patch("wosutil.tool.tool_instances_controller.save_task_schedule") as mock_save:
            self._run_worker_once(controller, self._state(tasks=[task]))
        mock_save.assert_called_once()
        entry = mock_save.call_args.args[0]["0"]["a"]
        self.assertAlmostEqual(entry["next_run_time"], time.time() + 500, delta=5)
        self.assertEqual(entry["reschedule_seconds"], 500)

    def test_worker_does_not_persist_when_memory_disabled(self):
        """Memory off: no schedule is written to disk."""
        controller = self._make_controller()
        controller._task_schedule = None
        task = dict(TASK_A)

        def fake_task(instance_index):
            stop_signal.set()
            return (True, 500)

        task["function"] = fake_task
        with patch("wosutil.tool.tool_instances_controller.save_task_schedule") as mock_save:
            self._run_worker_once(controller, self._state(tasks=[task]))
        mock_save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
