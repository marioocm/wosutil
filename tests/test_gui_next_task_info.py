"""Unit tests for the GUI next-task preview helper."""

import unittest

from wosutil.gui.gui_instance_status import get_next_task_info


class _FakeState:
    """Minimal instance state stub exposing the task list."""

    def __init__(self, tasks):
        self.tasks = tasks


class TestGetNextTaskInfo(unittest.TestCase):
    """Test cases for the next-task preview shown in the instances tab.

    The selection/grouping rules themselves are covered by the
    ``pick_scheduled_task`` tests; these cases cover the preview wrapper.
    """

    def _task(self, task_id, name, priority, next_run_time):
        """Build a running task state dict."""
        return {"id": task_id, "name": name, "priority": priority, "next_run_time": next_run_time}

    def test_empty_state_returns_nothing(self):
        """No tasks means no next task."""
        self.assertEqual(get_next_task_info(_FakeState([]), 1000), (None, None))

    def test_none_state_returns_nothing(self):
        """A missing state means no next task."""
        self.assertEqual(get_next_task_info(None, 1000), (None, None))

    def test_waiting_returns_earliest_future_task_with_time(self):
        """With nothing due, the earliest future task is returned with its time."""
        pm = _FakeState(
            [
                self._task("a", "Rescheduled sooner", 50, 1500),
                self._task("b", "Next in future", 1, 2000),
                self._task("c", "Later", 2, 4000),
            ]
        )
        name, next_time = get_next_task_info(pm, 1000)
        self.assertEqual(name, "Rescheduled sooner")
        self.assertEqual(next_time, 1500)

    def test_label_shows_first_executed_task_not_first_timer(self):
        """The label is the first task executed, not the first timer reading.

        Real 00:00 UTC batch: Stamina is read later (5.48s) but has better
        priority than the chests/intel tasks read at 2.5s; the worker waits
        for it within the 5s window, so it executes first and the preview
        must say so.
        """
        pm = _FakeState(
            [
                self._task("send_pet_adventure_chests", "Send Pet Adventure Chests", 9, 1787616002.5451596),
                self._task("claim_pet_adventure_ally_treasure", "Claim Pet Adventure Ally Treasure", 10, 1787616002.6933224),
                self._task("do_intel_missions", "Do Intel Missions", 11, 1787616002.9505522),
                self._task("claim_storehouse_stamina", "Claim Storehouse Stamina", 6, 1787616005.4818602),
            ]
        )
        name, next_time = get_next_task_info(pm, 1787616000.0)
        self.assertEqual(name, "Claim Storehouse Stamina")
        self.assertEqual(next_time, 1787616005.4818602)


if __name__ == "__main__":
    unittest.main()
