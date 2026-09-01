"""Unit tests for the per-task countdown tooltip helpers."""

import unittest

from wosutil.gui.gui_instances import (
    format_task_tooltip,
    format_time_remaining,
    get_all_tasks_info,
)


class _FakePM:
    """Minimal profile manager stub exposing the tasks state."""

    def __init__(self, tasks):
        self.running_tasks_state = tasks


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

    def test_no_state_returns_empty(self):
        """A missing state yields no tasks."""
        self.assertEqual(get_all_tasks_info(None, 1000), [])

    def test_empty_state_returns_empty(self):
        """No tasks means nothing to preview."""
        self.assertEqual(get_all_tasks_info(_FakePM([]), 1000), [])

    def test_returns_all_tasks_with_remaining_time(self):
        """Every scheduled task is listed with its remaining seconds."""
        pm = _FakePM([self._task("a", "Claim supplies", 1500), self._task("b", "Train troops", 2500)])
        self.assertEqual(get_all_tasks_info(pm, 1000), [("Claim supplies", 500), ("Train troops", 1500)])

    def test_dues_tasks_show_zero(self):
        """A due task is reported with 0 remaining seconds."""
        pm = _FakePM([self._task("a", "Due task", 900)])
        self.assertEqual(get_all_tasks_info(pm, 1000), [("Due task", 0)])

    def test_tasks_sorted_by_remaining_time(self):
        """Tasks are listed soonest first, due tasks on top."""
        pm = _FakePM(
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
        pm = _FakePM(
            [
                self._task("a", "Low priority read early", 1000, priority=50),
                self._task("b", "High priority read later", 1002, priority=1),
            ]
        )
        self.assertEqual(get_all_tasks_info(pm, 1000), [("High priority read later", 2), ("Low priority read early", 0)])

    def test_out_of_window_task_keeps_chronological_order(self):
        """A high-priority task beyond the 5s window does not jump ahead."""
        pm = _FakePM(
            [
                self._task("a", "Due low priority", 1000, priority=50),
                self._task("b", "Urgent much later", 1015, priority=1),
            ]
        )
        self.assertEqual(get_all_tasks_info(pm, 1000), [("Due low priority", 0), ("Urgent much later", 15)])

    def test_same_priority_stays_chronological(self):
        """Tied priorities keep the timer order, as the worker does."""
        pm = _FakePM(
            [
                self._task("a", "First read", 1005, priority=5),
                self._task("b", "Second read", 1008, priority=5),
            ]
        )
        self.assertEqual(get_all_tasks_info(pm, 1000), [("First read", 5), ("Second read", 8)])

    def test_real_00utc_batch_is_prioritized_before_it_due(self):
        """The 00:00 UTC batch keeps priority order even before tasks are due.

        Real schedule of the user: all four tasks fall within the 5s window
        (0000 UTC readings a couple of seconds apart) and the preview must
        show the most urgent first, not the timer reading order.
        """
        pm = _FakePM(
            [
                self._task("send_pet_adventure_chests", "Send Pet Adventure Chests", 1787616002.5451596, priority=9),
                self._task("claim_pet_adventure_ally_treasure", "Claim Pet Adventure Ally Treasure", 1787616002.6933224, priority=10),
                self._task("do_intel_missions", "Do Intel Missions", 1787616002.9505522, priority=11),
                self._task("claim_storehouse_stamina", "Claim Storehouse Stamina", 1787616005.4818602, priority=6),
            ]
        )
        self.assertEqual(
            get_all_tasks_info(pm, 1787616000.0),
            [
                ("Claim Storehouse Stamina", 5),
                ("Send Pet Adventure Chests", 2),
                ("Claim Pet Adventure Ally Treasure", 2),
                ("Do Intel Missions", 2),
            ],
        )


class TestFormatTaskTooltip(unittest.TestCase):
    """Test cases for the hover tooltip text."""

    def test_no_state_places_message(self):
        """No programmed tasks yields a placeholder message."""
        self.assertEqual(format_task_tooltip(None, 1000), "No programmed tasks")

    def test_each_task_is_one_line(self):
        """Each task occupies a numbered line with its countdown."""
        pm = _FakePM([{"name": "Claim supplies", "next_run_time": 1500}])
        self.assertEqual(format_task_tooltip(pm, 1000), "1. Claim supplies: 00:08:20")

    def test_due_tasks_are_marked_ready(self):
        """A due task is shown as ready instead of a zero countdown."""
        pm = _FakePM([{"name": "Due task", "next_run_time": 900}])
        self.assertEqual(format_task_tooltip(pm, 1000), "1. Due task: ready")

    def test_multi_line_priority_order(self):
        """The order shown follows the worker, not the timer reading."""
        pm = _FakePM(
            [
                {"name": "Low priority", "priority": 50, "next_run_time": 1000},
                {"name": "High priority", "priority": 1, "next_run_time": 1002},
            ]
        )
        self.assertEqual(format_task_tooltip(pm, 1000), "1. High priority: 00:00:02\n2. Low priority: ready")


if __name__ == "__main__":
    unittest.main()
