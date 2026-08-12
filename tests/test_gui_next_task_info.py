"""Unit tests for the GUI next-task preview helper."""

import unittest

from wosutil.gui.gui_instances import get_next_task_info


class _FakePM:
    """Minimal profile manager stub exposing the tasks state."""

    def __init__(self, tasks):
        self.running_tasks_state = tasks


class TestGetNextTaskInfo(unittest.TestCase):
    """Test cases for the next-task preview shown in the instances tab.

    The preview must mirror the worker selection: the highest-priority task
    that is already due, otherwise the earliest future task.
    """

    def _task(self, task_id, name, priority, next_run_time):
        """Build a running task state dict."""
        return {"id": task_id, "name": name, "priority": priority, "next_run_time": next_run_time}

    def test_empty_state_returns_nothing(self):
        """No tasks means no next task."""
        self.assertEqual(get_next_task_info(_FakePM([]), 1000), (None, None))

    def test_none_state_returns_nothing(self):
        """A missing state means no next task."""
        self.assertEqual(get_next_task_info(None, 1000), (None, None))

    def test_picks_highest_priority_among_due_tasks(self):
        """The due task with the smallest priority number is the next one."""
        pm = _FakePM(
            [
                self._task("a", "Low urgency due", 50, 0),
                self._task("b", "High urgency due", 1, 900),
                self._task("c", "Future task", 2, 5000),
            ]
        )
        name, next_time = get_next_task_info(pm, 1000)
        self.assertEqual(name, "High urgency due")
        self.assertIsNone(next_time)

    def test_due_task_wins_over_earlier_future_task(self):
        """A due task with lower priority beats a not-yet-due urgent one."""
        pm = _FakePM(
            [
                self._task("a", "Due but low priority", 10, 1),
                self._task("b", "Not due yet urgent", 1, 2000),
            ]
        )
        name, next_time = get_next_task_info(pm, 1000)
        self.assertEqual(name, "Due but low priority")
        self.assertIsNone(next_time)

    def test_waiting_returns_earliest_future_task_with_time(self):
        """With nothing due, the earliest future task is returned with its time."""
        pm = _FakePM(
            [
                self._task("a", "Rescheduled sooner", 50, 1500),
                self._task("b", "Next in future", 1, 2000),
                self._task("c", "Later", 2, 4000),
            ]
        )
        name, next_time = get_next_task_info(pm, 1000)
        self.assertEqual(name, "Rescheduled sooner")
        self.assertEqual(next_time, 1500)


if __name__ == "__main__":
    unittest.main()
