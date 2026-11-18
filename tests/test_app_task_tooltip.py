"""Unit tests for the per-task countdown tooltip helpers."""

import unittest

from wosutil.app.presentation import (
    format_instance_status,
    format_task_tooltip,
    format_time_remaining,
    get_all_tasks_info,
)


class _FakeState:
    """Minimal instance state stub exposing the task list and runtime flags."""

    def __init__(self, tasks, opening=False, current_task_name=None):
        self.tasks = tasks
        self.opening = opening
        self.current_task_name = current_task_name


class TestFormatTimeRemaining(unittest.TestCase):
    """Test cases for the HH:MM:SS formatter."""

    def test_zero_seconds(self):
        """Zero seconds renders as all zeros."""
        self.assertEqual(format_time_remaining(0), "00:00:00")

    def test_padded_values(self):
        """Hours, minutes and seconds are zero padded."""
        self.assertEqual(format_time_remaining(3661), "01:01:01")

    def test_long_duration_keeps_hours_wide(self):
        """A long remaining time keeps the original hours width."""
        self.assertEqual(format_time_remaining(100000), "27:46:40")


class TestGetAllTasksInfo(unittest.TestCase):
    """Test cases for the per-task remaining time helper."""

    def _task(self, task_id, name, next_run_time, priority=99):
        """Build a running task state dict."""
        return {"id": task_id, "name": name, "priority": priority, "next_run_time": next_run_time}

    def test_no_or_empty_state_returns_empty(self):
        """A missing or empty state yields no tasks."""
        self.assertEqual(get_all_tasks_info(None, 1000), [])
        self.assertEqual(get_all_tasks_info(_FakeState([]), 1000), [])

    def test_returns_all_tasks_with_remaining_time(self):
        """Every scheduled task is listed with its remaining seconds."""
        pm = _FakeState([self._task("a", "Claim supplies", 1500), self._task("b", "Train troops", 2500)])
        self.assertEqual(get_all_tasks_info(pm, 1000), [("Claim supplies", 500), ("Train troops", 1500)])

    def test_dues_tasks_show_zero(self):
        """A due task is reported with 0 remaining seconds."""
        pm = _FakeState([self._task("a", "Due task", 900)])
        self.assertEqual(get_all_tasks_info(pm, 1000), [("Due task", 0)])

    def test_tasks_sorted_by_remaining_time(self):
        """Tasks are listed soonest first, due tasks on top."""
        pm = _FakeState(
            [
                self._task("a", "Far away", 4000),
                self._task("b", "Due task", 900),
                self._task("c", "Soon", 1500),
            ]
        )
        self.assertEqual(get_all_tasks_info(pm, 1000), [("Due task", 0), ("Soon", 500), ("Far away", 3000)])

    def test_same_window_tasks_run_by_priority_not_time(self):
        """In-window tasks follow the worker priority order, not the timer.

        v1.1.5 orders a batch read a second apart by priority; the list must
        show the higher-priority in-window task first even when its timer was
        read later.
        """
        pm = _FakeState(
            [
                self._task("a", "Low priority read early", 1000, priority=50),
                self._task("b", "High priority read later", 1002, priority=1),
            ]
        )
        self.assertEqual(get_all_tasks_info(pm, 1000), [("High priority read later", 2), ("Low priority read early", 0)])

    def test_out_of_window_task_keeps_chronological_order(self):
        """A high-priority task beyond the 5s window does not jump ahead."""
        pm = _FakeState(
            [
                self._task("a", "Due low priority", 1000, priority=50),
                self._task("b", "Urgent much later", 1015, priority=1),
            ]
        )
        self.assertEqual(get_all_tasks_info(pm, 1000), [("Due low priority", 0), ("Urgent much later", 15)])

    def test_same_priority_stays_chronological(self):
        """Tied priorities keep the timer order, as the worker does."""
        pm = _FakeState(
            [
                self._task("a", "First read", 1005, priority=5),
                self._task("b", "Second read", 1008, priority=5),
            ]
        )
        self.assertEqual(get_all_tasks_info(pm, 1000), [("First read", 5), ("Second read", 8)])


class TestFormatTaskTooltip(unittest.TestCase):
    """Test cases for the hover tooltip text."""

    def test_no_state_places_message(self):
        """No programmed tasks yields a placeholder message."""
        self.assertEqual(format_task_tooltip(None, 1000), "No programmed tasks")

    def test_each_task_is_one_line(self):
        """Each task occupies a numbered line with its countdown."""
        pm = _FakeState([{"name": "Claim supplies", "next_run_time": 1500}])
        self.assertEqual(format_task_tooltip(pm, 1000), "1. Claim supplies: 00:08:20")

    def test_due_tasks_are_marked_ready(self):
        """A due task is shown as ready instead of a zero countdown."""
        pm = _FakeState([{"name": "Due task", "next_run_time": 900}])
        self.assertEqual(format_task_tooltip(pm, 1000), "1. Due task: ready")

    def test_multi_line_priority_order(self):
        """The order shown follows the worker, not the timer reading."""
        pm = _FakeState(
            [
                {"name": "Low priority", "priority": 50, "next_run_time": 1000},
                {"name": "High priority", "priority": 1, "next_run_time": 1002},
            ]
        )
        self.assertEqual(format_task_tooltip(pm, 1000), "1. High priority: 00:00:02\n2. Low priority: ready")


class TestFormatInstanceStatus(unittest.TestCase):
    """Test cases for the status text shown next to an instance."""

    def _status(self, state, **kwargs):
        """Build a status with the defaults used by the GUI."""
        values = {"now": 1000, "active": False, "aborted": False, "queue_position": None, "retry_seconds": 0}
        values.update(kwargs)
        return format_instance_status(state, **values)

    def test_active_opening_shows_the_first_task(self):
        """The opening phase previews the first task."""
        state = _FakeState([{"name": "Claim supplies", "next_run_time": 1100}], opening=True)
        self.assertEqual(self._status(state, active=True), "Opening... First task: Claim supplies")

    def test_active_current_task_is_shown(self):
        """The task being executed is shown while the worker runs it."""
        state = _FakeState([], current_task_name="Train troops")
        self.assertEqual(self._status(state, active=True), "Executing: Train troops")

    def test_active_next_task_shows_the_countdown(self):
        """A future task of an active instance shows its countdown."""
        state = _FakeState([{"name": "Claim supplies", "next_run_time": 1100}])
        self.assertEqual(self._status(state, active=True), "Next: Claim supplies in 00:01:40")

    def test_active_without_tasks(self):
        """An active instance with an empty profile says so."""
        self.assertEqual(self._status(_FakeState([]), active=True), "No programmed tasks")

    def test_inactive_states(self):
        """The waiting, queued, paused and aborted labels keep their wording."""
        due_task = _FakeState([{"name": "Claim supplies", "next_run_time": 900}])
        future_task = _FakeState([{"name": "Claim supplies", "next_run_time": 1100}])
        cases = [
            (due_task, {"aborted": True}, "Game not installed - install it and press Start"),
            (future_task, {"queue_position": 2}, "In queue (2) - Next: Claim supplies in 00:01:40"),
            (due_task, {"queue_position": 2}, "In queue (2) - Waiting to execute: Claim supplies"),
            (future_task, {"retry_seconds": 90}, "Retry paused - 00:01:30"),
            (future_task, {}, "Standby - Next: Claim supplies in 00:01:40"),
            (_FakeState([]), {}, "Standby"),
        ]
        for state, kwargs, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self._status(state, **kwargs), expected)


if __name__ == "__main__":
    unittest.main()
