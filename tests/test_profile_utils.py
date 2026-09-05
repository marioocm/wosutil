"""Unit tests for profile task-state construction."""

import unittest

from wosutil.tool.profiles.profile_utils import build_running_tasks_state


class TestBuildRunningTasksState(unittest.TestCase):
    """The profile helper accepts only safe, supported input shapes."""

    def test_preserves_zero_as_explicit_timestamp(self):
        """A zero timestamp is preserved for deterministic callers."""
        definitions = {"a": {"id": "a", "reschedule_seconds": 60}}
        state = build_running_tasks_state("All", {"All": ["a"]}, definitions, now=0.0)
        self.assertEqual(state[0]["next_run_time"], 0.0)

    def test_initializes_scheduling_metadata(self):
        """Fresh tasks start as successful cycles anchored to now."""
        definitions = {"a": {"id": "a", "reschedule_seconds": 60}}
        state = build_running_tasks_state("All", {"All": ["a"]}, definitions, now=100.0)
        self.assertEqual(state[0]["last_result"], "success")
        self.assertEqual(state[0]["nominal_due"], 100.0)
        self.assertEqual(state[0]["consecutive_errors"], 0)

    def test_ignores_non_string_task_ids(self):
        """Malformed task IDs cannot cause an unhashable-key error."""
        definitions = {"a": {"id": "a", "reschedule_seconds": 60}}
        state = build_running_tasks_state("All", {"All": ["a", [], 123]}, definitions, now=100.0)
        self.assertEqual([task["id"] for task in state], ["a"])

    def test_returns_empty_for_invalid_profile_data(self):
        """Invalid mapping arguments fail closed instead of raising."""
        definitions = {"a": {"id": "a", "reschedule_seconds": 60}}
        self.assertEqual(build_running_tasks_state("All", [], definitions, now=100.0), [])
        self.assertEqual(build_running_tasks_state("All", {"All": "a"}, definitions, now=100.0), [])
        self.assertEqual(build_running_tasks_state("All", {"All": ["a"]}, [], now=100.0), [])


if __name__ == "__main__":
    unittest.main()
