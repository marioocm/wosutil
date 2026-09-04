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
    that is already due, waiting for a higher-priority task scheduled within
    the grouping window (5 seconds) if one is close, otherwise the earliest
    future task.
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

    def test_higher_priority_task_within_window_is_waited_for(self):
        """A higher-priority task due within 5s beats the due lower-priority one.

        Timer readings of tasks that belong to the same window (e.g. 00:00 UTC)
        can differ by a second or two; the priority order must win instead of
        the task that was read earlier.
        """
        pm = _FakePM(
            [
                self._task("a", "Due low priority", 10, 990),
                self._task("b", "Due urgent in 2s", 1, 1002),
            ]
        )
        name, next_time = get_next_task_info(pm, 1000)
        self.assertEqual(name, "Due urgent in 2s")
        self.assertEqual(next_time, 1002)

    def test_higher_priority_task_outside_window_does_not_block(self):
        """A higher-priority task more than 5s away does not delay the due one."""
        pm = _FakePM(
            [
                self._task("a", "Due low priority", 10, 1000),
                self._task("b", "Urgent much later", 1, 3000),
            ]
        )
        name, next_time = get_next_task_info(pm, 1000)
        self.assertEqual(name, "Due low priority")
        self.assertIsNone(next_time)

    def test_lower_priority_task_within_window_does_not_block(self):
        """A due task is not delayed by a lower-priority task that is close."""
        pm = _FakePM(
            [
                self._task("a", "Due urgent", 1, 1000),
                self._task("b", "Relaxed close", 10, 1002),
            ]
        )
        name, next_time = get_next_task_info(pm, 1000)
        self.assertEqual(name, "Due urgent")
        self.assertIsNone(next_time)

    def test_closed_instance_shows_due_time_not_ready(self):
        """A closed instance with an early-runnable task shows its due time.

        The worker would run it now if the instance were open, but the
        launcher only opens at the due time: showing "ready" would be a lie.
        """
        pm = _FakePM(
            [
                {
                    "id": "claim_mail",
                    "name": "Claim Mail Rewards",
                    "priority": 15,
                    "next_run_time": 3400.0,
                    "nominal_due": 3400.0,
                    "early_seconds": 7200,
                    "late_seconds": 3600,
                    "last_result": "success",
                },
            ]
        )
        name, next_time = get_next_task_info(pm, 1000.0, is_open=False)
        self.assertEqual(name, "Claim Mail Rewards")
        self.assertEqual(next_time, 3400.0)

    def test_open_instance_runs_early_task_now(self):
        """An open instance really executes the early-runnable task at once."""
        pm = _FakePM(
            [
                {
                    "id": "claim_mail",
                    "name": "Claim Mail Rewards",
                    "priority": 15,
                    "next_run_time": 3400.0,
                    "nominal_due": 3400.0,
                    "early_seconds": 7200,
                    "late_seconds": 3600,
                    "last_result": "success",
                },
            ]
        )
        name, next_time = get_next_task_info(pm, 1000.0, is_open=True)
        self.assertEqual(name, "Claim Mail Rewards")
        self.assertIsNone(next_time)

    def test_closed_instance_shows_joint_batch_time(self):
        """A closed instance shows when the batch opens, not each due time."""
        pm = _FakePM(
            [
                {
                    "id": "claim_idle",
                    "name": "Claim Idle Income",
                    "priority": 4,
                    "next_run_time": 4600.0,
                    "nominal_due": 4600.0,
                    "early_seconds": 7200,
                    "late_seconds": 3600,
                    "last_result": "success",
                },
                {
                    "id": "train_troops",
                    "name": "Train Troops",
                    "priority": 12,
                    "next_run_time": 8200.0,
                    "nominal_due": 8200.0,
                    "early_seconds": 0,
                    "late_seconds": 600,
                    "last_result": "success",
                },
            ]
        )
        name, next_time = get_next_task_info(pm, 1000.0, is_open=False)
        self.assertEqual(name, "Claim Idle Income")
        self.assertEqual(next_time, 8200.0)

    def test_label_shows_first_executed_task_not_first_timer(self):
        """The label is the first task executed, not the first timer reading.

        Real 00:00 UTC batch: Stamina is read later (5.48s) but has better
        priority than the chests/intel tasks read at 2.5s; the worker waits
        for it within the 5s window, so it executes first and the preview
        must say so.
        """
        pm = _FakePM(
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
