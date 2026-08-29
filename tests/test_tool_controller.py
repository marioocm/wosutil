"""Unit tests for the multi-instance tool controller."""

import queue
import time
import unittest
from unittest.mock import MagicMock, patch

from wosutil.stop import stop_signal
from wosutil.tool.tool_instances_controller import MultiInstanceToolController, pick_scheduled_task


class FakeManager:
    """Backend stub that reports blocked ADB access."""

    def check_adb_access(self):
        """Return a blocking warning."""
        return ["ADB access is blocked."]


class FakeManagerOpen(FakeManager):
    """Backend stub with unrestricted ADB access."""

    def check_adb_access(self):
        """Return no warnings."""
        return []


class FakeManagerRunning(FakeManagerOpen):
    """Backend stub for a running, controllable instance."""

    def __init__(self):
        """Initialize the call counters."""
        self.stop_calls = 0
        self.start_calls = 0

    def _is_instance_running(self, index):
        """The instance is always up."""
        return True

    def start_instance(self, index):
        """Record a start call."""
        self.start_calls += 1

    def stop_instance(self, index):
        """Record a stop call."""
        self.stop_calls += 1


class TestStartToolAbort(unittest.TestCase):
    """Tests for the start-time ADB access guard."""

    def setUp(self):
        """Ensure the global stop signal is clear between tests."""
        stop_signal.clear()

    def tearDown(self):
        """Do not leak the stop signal into other tests."""
        stop_signal.clear()

    def _make_controller(self, manager):
        """Build a controller with mock dependencies and a log capture."""
        self.logs = []
        return MultiInstanceToolController(
            log_message=lambda msg, level="info": self.logs.append((msg, level)),
            TASK_DEFINITIONS={},
            multi_instance_manager=manager,
            profile_manager=MagicMock(),
            instances_profile_managers={},
            instance_queue=[],
            active_instances=set(),
            instance_widgets=[],
            save_instance_selection=lambda selection: None,
            load_instance_selection=lambda: {},
        )

    def test_start_tool_aborts_when_adb_blocked(self):
        """start_tool refuses to run and logs the warning when ADB is blocked."""
        controller = self._make_controller(FakeManager())
        self.assertFalse(controller.start_tool())
        self.assertFalse(controller.tool_running)
        self.assertTrue(any("ADB access is blocked" in msg for msg, _level in self.logs))

    def test_start_tool_runs_when_adb_ok(self):
        """start_tool runs normally when the backend has no warnings."""
        controller = self._make_controller(FakeManagerOpen())
        self.assertTrue(controller.start_tool())
        self.assertTrue(controller.tool_running)

    def test_start_tool_refuses_to_overlap_a_running_session(self):
        """A second start cannot reset the state of the current session."""
        controller = self._make_controller(FakeManagerOpen())
        controller.tool_running = True

        self.assertFalse(controller.start_tool())
        self.assertTrue(controller.tool_running)
        self.assertTrue(any("already running" in msg for msg, _level in self.logs))

    def test_old_worker_cannot_remove_a_newer_thread_reference(self):
        """A stale worker must not delete a replacement thread entry."""
        controller = self._make_controller(FakeManagerOpen())
        old_thread = object()
        current_thread = object()
        controller.instance_threads[0] = current_thread

        controller._remove_thread_reference(0, old_thread)

        self.assertIs(controller.instance_threads[0], current_thread)

    def test_stop_tool_keeps_reference_to_worker_that_is_still_alive(self):
        """Stopping does not forget a thread that exceeded the join timeout."""
        manager = FakeManagerRunning()
        controller = self._make_controller(manager)
        worker = MagicMock()
        worker.is_alive.return_value = True
        controller.tool_running = True
        controller.active_instances.add(0)
        controller.instance_queue.append((0, "profile_x"))
        controller.instance_threads[0] = worker

        controller.stop_tool()

        self.assertIs(controller.instance_threads[0], worker)
        worker.join.assert_called_once_with(timeout=5.0)
        self.assertEqual(manager.stop_calls, 1)
        self.assertEqual(controller.active_instances, set())
        self.assertEqual(controller.instance_queue, [])


class TestGameNotInstalledAbort(unittest.TestCase):
    """The instance aborts without retries when the game is not installed."""

    def setUp(self):
        """Ensure the global stop signal is clear between tests."""
        stop_signal.clear()

    def _make_controller(self, manager):
        """Build a controller with mock dependencies and a log capture."""
        self.logs = []
        return MultiInstanceToolController(
            log_message=lambda msg, level="info": self.logs.append((msg, level)),
            TASK_DEFINITIONS={},
            multi_instance_manager=manager,
            profile_manager=MagicMock(),
            instances_profile_managers={},
            instance_queue=[],
            active_instances=set(),
            instance_widgets=[],
            save_instance_selection=lambda selection: None,
            load_instance_selection=lambda: {},
            dialog_queue=None,
        )

    def test_worker_aborts_without_requeue_when_game_missing(self):
        """Missing game: clear error, one stop, no relaunch and no requeue.

        Regression guard: previously each failed game launch closed the
        emulator and re-queued the instance, restarting it in a loop.
        """
        manager = FakeManagerRunning()
        controller = self._make_controller(manager)
        with patch("wosutil.emulator.emulator_manager.verify_adb_connected", return_value=True), patch("wosutil.emulator.emulator_manager.is_wos_installed", return_value=False), patch(
            "wosutil.tool.tasks.task_helpers.launch_and_reach_city_screen"
        ) as mock_launch:
            controller.run_profile_on_instance_with_slot(0, "profile_x")
        mock_launch.assert_not_called()
        self.assertEqual(manager.stop_calls, 1)
        self.assertEqual(manager.start_calls, 0)
        self.assertNotIn(0, controller.active_instances)
        self.assertEqual(controller.instance_queue, [])
        self.assertTrue(any("not installed" in msg for msg, _level in self.logs))

    def test_worker_queues_dialog_when_game_missing(self):
        """A (title, text) dialog request is queued for the GUI main thread."""
        manager = FakeManagerRunning()
        dialogs = queue.Queue()
        controller = self._make_controller(manager)
        controller.dialog_queue = dialogs
        with patch("wosutil.emulator.emulator_manager.verify_adb_connected", return_value=True), patch("wosutil.emulator.emulator_manager.is_wos_installed", return_value=False), patch(
            "wosutil.tool.tasks.task_helpers.launch_and_reach_city_screen"
        ) as mock_launch:
            controller.run_profile_on_instance_with_slot(0, "profile_x")
        mock_launch.assert_not_called()
        title, text = dialogs.get_nowait()
        self.assertEqual(title, "Game not installed")
        self.assertIn("com.gof.global", text)

    def test_worker_never_requeues_when_game_missing(self):
        """The launch-attempt counter stays untouched for a missing game."""
        manager = FakeManagerRunning()
        controller = self._make_controller(manager)
        with patch("wosutil.emulator.emulator_manager.verify_adb_connected", return_value=True), patch("wosutil.emulator.emulator_manager.is_wos_installed", return_value=False), patch(
            "wosutil.tool.tasks.task_helpers.launch_and_reach_city_screen"
        ) as mock_launch:
            controller.run_profile_on_instance_with_slot(0, "profile_x")
            controller.run_profile_on_instance_with_slot(0, "profile_x")
        mock_launch.assert_not_called()
        self.assertEqual(manager.stop_calls, 2)
        self.assertEqual(controller.instance_launch_attempts, {})


class TestLaunchRetryQueue(unittest.TestCase):
    """The instance queue keeps progressing after a launch is abandoned."""

    def setUp(self):
        """Ensure the global stop signal is clear between tests."""
        stop_signal.clear()

    def tearDown(self):
        """Do not leak the stop signal into other tests."""
        stop_signal.clear()

    def test_exhausted_retries_wake_the_next_instance(self):
        """A failed instance must not leave other queued instances stranded."""
        controller = MultiInstanceToolController(
            log_message=lambda _msg, level="info": None,
            TASK_DEFINITIONS={},
            multi_instance_manager=FakeManagerOpen(),
            profile_manager=MagicMock(),
            instances_profile_managers={},
            instance_queue=[(1, "profile_y")],
            active_instances={0},
            instance_widgets=[],
            save_instance_selection=lambda _selection: None,
            load_instance_selection=lambda: {},
        )
        controller.max_launch_attempts = 1

        with patch.object(controller, "launch_next_instances") as launch_next:
            controller._requeue_with_limit(0, "profile_x")

        self.assertNotIn(0, controller.active_instances)
        self.assertEqual(controller.instance_queue, [(1, "profile_y")])
        launch_next.assert_called_once_with()


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
        """Among in-window tasks, the highest priority is waited for, not the nearest.

        A task whose timer was read earlier but with less priority must not
        jump ahead of a more urgent one read a bit later: the whole batch is
        grouped by priority.
        """
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
        state = [
            self._task("a", "Due", 6, 990),
            self._task("b", "Same priority later", 6, 1003),
        ]
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


TASK_A = {"id": "a", "name": "Task A", "function": lambda instance_index: True, "priority": 1, "reschedule_seconds": 1000}
TASK_B = {"id": "b", "name": "Task B", "function": lambda instance_index: True, "priority": 2, "reschedule_seconds": 2000}
TASK_C = {"id": "c", "name": "Task C", "function": lambda instance_index: True, "priority": 3, "reschedule_seconds": 3000}


class TestScheduleMemory(unittest.TestCase):
    """The controller restores the saved schedule when memory is enabled."""

    def setUp(self):
        """Ensure the global stop signal is clear between tests."""
        stop_signal.clear()

    def _make_controller(self, manager):
        """Build a controller with mock dependencies and a log capture."""
        self.logs = []
        return MultiInstanceToolController(
            log_message=lambda msg, level="info": self.logs.append((msg, level)),
            TASK_DEFINITIONS={"a": TASK_A, "b": TASK_B, "c": TASK_C},
            multi_instance_manager=manager,
            profile_manager=MagicMock(),
            instances_profile_managers={},
            instance_queue=[],
            active_instances=set(),
            instance_widgets=[],
            save_instance_selection=lambda selection: None,
            load_instance_selection=lambda: {},
            dialog_queue=None,
        )

    def _checked_instance(self, profile_name="All"):
        """Build a checked instance widget for index 0 with the given profile."""
        checked = MagicMock()
        checked.get.return_value = True
        profile_var = MagicMock()
        profile_var.get.return_value = profile_name
        return {"index": 0, "checked": checked, "profile_var": profile_var}

    def _start_with_remember(self, controller, saved_schedule, profile_name="All", task_names=("a", "b")):
        """Run start_tool with the remember-schedule preference and a fake ProfileManager."""
        pm_mock = MagicMock()
        pm_mock.profiles = {profile_name: list(task_names)}
        with patch("wosutil.tool.profiles.profile_manager.ProfileManager", return_value=pm_mock), patch.object(controller, "launch_next_instances"), patch(
            "wosutil.tool.tool_instances_controller.get_remember_schedule", return_value=True
        ), patch("wosutil.tool.tool_instances_controller.load_task_schedule", return_value=saved_schedule), patch("wosutil.tool.tool_instances_controller.save_task_schedule"):
            controller.instance_widgets.append(self._checked_instance(profile_name))
            self.assertTrue(controller.start_tool())
        return controller.instances_profile_managers[0].running_tasks_state

    def test_start_tool_restores_saved_next_run_times(self):
        """A saved future time keeps the remaining wait of the task."""
        controller = self._make_controller(FakeManagerOpen())
        state = self._start_with_remember(
            controller,
            {"0": {"a": {"next_run_time": 5000.0, "reschedule_seconds": 400.0}}},
        )
        by_id = {t["id"]: t for t in state}
        self.assertEqual(by_id["a"]["next_run_time"], 5000.0)
        self.assertEqual(by_id["a"]["reschedule_seconds"], 400.0)
        # The task without a saved entry runs at startup (current behavior)
        self.assertLessEqual(by_id["b"]["next_run_time"], time.time())

    def test_start_tool_keeps_overdue_times_so_task_runs_immediately(self):
        """A saved time in the past stays due and runs immediately at startup."""
        controller = self._make_controller(FakeManagerOpen())
        state = self._start_with_remember(
            controller,
            {"0": {"a": {"next_run_time": 100.0}}},
        )
        by_id = {t["id"]: t for t in state}
        self.assertEqual(by_id["a"]["next_run_time"], 100.0)

    def test_start_tool_keeps_schedule_of_tasks_shared_between_profiles(self):
        """Switching profile keeps the schedule of the tasks present in both."""
        controller = self._make_controller(FakeManagerOpen())
        # The schedule was saved while the instance ran profile "All" (tasks a, b);
        # the user switches to profile "All2" (tasks a, c).
        saved = {
            "0": {
                "a": {"next_run_time": 5000.0, "reschedule_seconds": 400.0},
                "b": {"next_run_time": 6000.0},
            }
        }
        state = self._start_with_remember(controller, saved, profile_name="All2", task_names=("a", "c"))
        by_id = {t["id"]: t for t in state}
        # The shared task keeps its schedule; the new task runs at startup
        self.assertEqual(by_id["a"]["next_run_time"], 5000.0)
        self.assertEqual(by_id["a"]["reschedule_seconds"], 400.0)
        self.assertLessEqual(by_id["c"]["next_run_time"], time.time())

    def test_persist_keeps_entries_of_tasks_not_in_the_current_profile(self):
        """Persisting keeps the schedule of tasks that are not in the current profile."""
        controller = self._make_controller(FakeManagerOpen())
        saved = {"0": {"a": {"next_run_time": 5000.0}, "z": {"next_run_time": 7000.0}}}
        pm_mock = MagicMock()
        pm_mock.profiles = {"All": ["a", "b"]}
        with patch("wosutil.tool.profiles.profile_manager.ProfileManager", return_value=pm_mock), patch.object(controller, "launch_next_instances"), patch(
            "wosutil.tool.tool_instances_controller.get_remember_schedule", return_value=True
        ), patch("wosutil.tool.tool_instances_controller.load_task_schedule", return_value=saved), patch("wosutil.tool.tool_instances_controller.save_task_schedule") as mock_save:
            controller.instance_widgets.append(self._checked_instance())
            self.assertTrue(controller.start_tool())
        persisted = mock_save.call_args.args[0]
        # The profile only contains a and b, but z is kept for a future switch back
        self.assertIn("z", persisted["0"])
        self.assertIn("b", persisted["0"])

    def test_start_tool_does_not_wipe_schedule_without_selected_instances(self):
        """Starting with no instances selected never writes an empty schedule."""
        controller = self._make_controller(FakeManagerOpen())
        with patch("wosutil.tool.tool_instances_controller.get_remember_schedule", return_value=True), patch(
            "wosutil.tool.tool_instances_controller.load_task_schedule", return_value={"0": {"a": {"next_run_time": 1.0}}}
        ), patch("wosutil.tool.tool_instances_controller.save_task_schedule") as mock_save:
            self.assertTrue(controller.start_tool())
        mock_save.assert_not_called()

    def test_start_tool_keeps_current_behavior_when_memory_disabled(self):
        """Memory off: every task is scheduled to run at startup, as before."""
        controller = self._make_controller(FakeManagerOpen())
        pm_mock = MagicMock()
        pm_mock.profiles = {"All": ["a", "b"]}
        with patch("wosutil.tool.profiles.profile_manager.ProfileManager", return_value=pm_mock), patch.object(controller, "launch_next_instances"), patch(
            "wosutil.tool.tool_instances_controller.get_remember_schedule", return_value=False
        ), patch("wosutil.tool.tool_instances_controller.load_task_schedule") as mock_load:
            controller.instance_widgets.append(self._checked_instance())
            self.assertTrue(controller.start_tool())
        mock_load.assert_not_called()
        state = controller.instances_profile_managers[0].running_tasks_state
        self.assertEqual(len(state), 2)
        for task in state:
            self.assertLessEqual(task["next_run_time"], time.time())


class TestSchedulePersistenceInWorker(unittest.TestCase):
    """The worker persists the schedule after every task run."""

    def setUp(self):
        """Ensure the global stop signal is clear between tests."""
        stop_signal.clear()

    def tearDown(self):
        """Do not leak the stop signal into other tests."""
        stop_signal.clear()

    def _make_controller(self, manager):
        """Build a controller with mock dependencies and a log capture."""
        self.logs = []
        return MultiInstanceToolController(
            log_message=lambda msg, level="info": self.logs.append((msg, level)),
            TASK_DEFINITIONS={"a": TASK_A},
            multi_instance_manager=manager,
            profile_manager=MagicMock(),
            instances_profile_managers={},
            instance_queue=[],
            active_instances=set(),
            instance_widgets=[],
            save_instance_selection=lambda selection: None,
            load_instance_selection=lambda: {},
            dialog_queue=None,
        )

    def _run_worker_once(self, controller, pm):
        """Run the instance worker synchronously with the emulator stack stubbed."""
        controller.instances_profile_managers[0] = pm
        controller._selected_instances = {0}
        with patch("wosutil.emulator.emulator_manager.verify_adb_connected", return_value=True), patch("wosutil.emulator.emulator_manager.is_wos_installed", return_value=True), patch(
            "wosutil.tool.tasks.task_helpers.launch_and_reach_city_screen", return_value=True
        ), patch("wosutil.tool.tool_instances_controller.sync_utc_time"):
            controller.run_profile_on_instance_with_slot(0, "All")

    def test_worker_persists_rescheduled_task_after_run(self):
        """After a task runs and reschedules, its new times are persisted."""
        manager = FakeManagerRunning()
        controller = self._make_controller(manager)
        controller._task_schedule = {}
        pm = MagicMock()
        pm.running_tasks_state = [dict(TASK_A)]
        pm.current_task_name = None

        def fake_task(instance_index):
            stop_signal.set()
            return (True, 500)

        pm.running_tasks_state[0]["function"] = fake_task
        with patch("wosutil.tool.tool_instances_controller.save_task_schedule") as mock_save:
            self._run_worker_once(controller, pm)
        mock_save.assert_called_once()
        entry = mock_save.call_args.args[0]["0"]["a"]
        self.assertAlmostEqual(entry["next_run_time"], time.time() + 500, delta=5)
        self.assertEqual(entry["reschedule_seconds"], 500)

    def test_worker_does_not_persist_when_memory_disabled(self):
        """Memory off: no schedule is written to disk."""
        manager = FakeManagerRunning()
        controller = self._make_controller(manager)
        controller._task_schedule = None
        pm = MagicMock()
        pm.running_tasks_state = [dict(TASK_A)]
        pm.current_task_name = None

        def fake_task(instance_index):
            stop_signal.set()
            return (True, 500)

        pm.running_tasks_state[0]["function"] = fake_task
        with patch("wosutil.tool.tool_instances_controller.save_task_schedule") as mock_save:
            self._run_worker_once(controller, pm)
        mock_save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
