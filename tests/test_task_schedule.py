"""Unit tests for the task schedule persistence module."""

import math
import os
import tempfile
import unittest
from unittest.mock import patch

from wosutil.tool.tasks.task_schedule import (
    build_task_state,
    load_saved_tasks,
    load_task_schedule,
    save_task_schedule,
    snapshot_instance_schedule,
)


def _task(task_id, reschedule_seconds=3600, priority=5):
    """Build a minimal task definition dict."""
    return {"id": task_id, "name": task_id, "reschedule_seconds": reschedule_seconds, "priority": priority}


class TestLoadSavedTasks(unittest.TestCase):
    """load_saved_tasks extracts the saved state of one instance."""

    def test_returns_empty_when_schedule_is_empty(self):
        """An empty schedule has no saved tasks."""
        self.assertEqual(load_saved_tasks({}, 0), {})

    def test_returns_empty_when_instance_missing(self):
        """A schedule without the instance index has no saved tasks."""
        self.assertEqual(load_saved_tasks({"1": {"claim_idle": {}}}, 0), {})

    def test_returns_entries_for_matching_instance(self):
        """Entries of the matching instance are returned."""
        schedule = {"0": {"claim_idle": {"next_run_time": 100.0, "reschedule_seconds": 400.0}}}
        self.assertEqual(load_saved_tasks(schedule, 0), {"claim_idle": {"next_run_time": 100.0, "reschedule_seconds": 400.0}})

    def test_ignores_entries_without_valid_next_run_time(self):
        """Entries without a numeric next_run_time are ignored."""
        schedule = {
            "0": {
                "ok": {"next_run_time": 100.0},
                "bad": {},
                "text": {"next_run_time": "x"},
                "nan": {"next_run_time": math.nan},
                "infinite": {"next_run_time": math.inf},
                "boolean": {"next_run_time": True},
            }
        }
        self.assertEqual(load_saved_tasks(schedule, 0), {"ok": {"next_run_time": 100.0}})

    def test_ignores_malformed_structure(self):
        """Malformed schedule structures yield no saved tasks."""
        self.assertEqual(load_saved_tasks({"0": "nope"}, 0), {})
        self.assertEqual(load_saved_tasks({"0": [1, 2]}, 0), {})
        self.assertEqual(load_saved_tasks({"0": {"x": 5}}, 0), {})


class TestBuildTaskState(unittest.TestCase):
    """build_task_state applies the saved schedule on top of the task definitions."""

    def test_defaults_every_task_to_now_without_saved_schedule(self):
        """Without saved state every task runs at startup."""
        state = build_task_state([_task("a"), _task("b")], {}, now=1000.0)
        self.assertEqual([t["next_run_time"] for t in state], [1000.0, 1000.0])

    def test_preserves_zero_as_explicit_current_time(self):
        """A zero timestamp is valid in deterministic scheduling tests."""
        state = build_task_state([_task("a")], {}, now=0.0)
        self.assertEqual(state[0]["next_run_time"], 0.0)

    def test_restores_saved_next_run_time(self):
        """A saved future time keeps the remaining wait of the task."""
        saved = {"a": {"next_run_time": 5000.0}}
        state = build_task_state([_task("a"), _task("b")], saved, now=1000.0)
        self.assertEqual(state[0]["next_run_time"], 5000.0)
        # Tasks without a saved entry fall back to running at startup
        self.assertEqual(state[1]["next_run_time"], 1000.0)

    def test_keeps_overdue_time_so_task_is_due_immediately(self):
        """A saved time in the past stays due and runs immediately."""
        saved = {"a": {"next_run_time": 100.0}}
        state = build_task_state([_task("a")], saved, now=1000.0)
        self.assertEqual(state[0]["next_run_time"], 100.0)

    def test_restores_reschedule_seconds(self):
        """The last measured reschedule is restored with the task."""
        saved = {"a": {"next_run_time": 5000.0, "reschedule_seconds": 400.0}}
        state = build_task_state([_task("a", 3600)], saved, now=1000.0)
        self.assertEqual(state[0]["reschedule_seconds"], 400.0)

    def test_ignores_invalid_saved_reschedule(self):
        """An invalid saved reschedule keeps the definition default."""
        saved = {"a": {"next_run_time": 5000.0, "reschedule_seconds": "x"}}
        state = build_task_state([_task("a", 3600)], saved, now=1000.0)
        self.assertEqual(state[0]["reschedule_seconds"], 3600)

    def test_ignores_non_finite_saved_reschedule(self):
        """NaN and infinity cannot poison a restored task interval."""
        for value in (math.nan, math.inf, True):
            with self.subTest(value=value):
                saved = {"a": {"next_run_time": 5000.0, "reschedule_seconds": value}}
                state = build_task_state([_task("a", 3600)], saved, now=1000.0)
                self.assertEqual(state[0]["reschedule_seconds"], 3600)

    def test_does_not_mutate_the_definition_dict(self):
        """The returned tasks are copies, not the definition dicts."""
        definition = _task("a")
        state = build_task_state([definition], {}, now=1000.0)
        state[0]["next_run_time"] = 123.0
        self.assertNotIn("next_run_time", definition)

    def test_keeps_task_metadata_from_the_definition(self):
        """The task metadata (name, priority) survives the build."""
        state = build_task_state([_task("a", priority=3)], {}, now=1000.0)
        self.assertEqual(state[0]["priority"], 3)
        self.assertEqual(state[0]["name"], "a")

    def test_restores_saved_last_result(self):
        """A saved error result is restored with the task."""
        saved = {"a": {"next_run_time": 5000.0, "last_result": "error"}}
        state = build_task_state([_task("a")], saved, now=1000.0)
        self.assertEqual(state[0]["last_result"], "error")

    def test_restores_saved_nominal_due(self):
        """A postponed task keeps its anchor apart from its planned time."""
        saved = {"a": {"next_run_time": 5000.0, "nominal_due": 4000.0}}
        state = build_task_state([_task("a")], saved, now=1000.0)
        self.assertEqual(state[0]["next_run_time"], 5000.0)
        self.assertEqual(state[0]["nominal_due"], 4000.0)

    def test_defaults_nominal_due_to_next_run_time(self):
        """Entries without an anchor (old saves) anchor to their due time."""
        state = build_task_state([_task("a")], {}, now=1000.0)
        self.assertEqual(state[0]["nominal_due"], 1000.0)
        saved = {"a": {"next_run_time": 5000.0, "nominal_due": "x"}}
        state = build_task_state([_task("a")], saved, now=1000.0)
        self.assertEqual(state[0]["nominal_due"], 5000.0)

    def test_defaults_missing_last_result_to_success(self):
        """Tasks without a saved result behave as successful cycles."""
        state = build_task_state([_task("a")], {}, now=1000.0)
        self.assertEqual(state[0]["last_result"], "success")
        saved = {"a": {"next_run_time": 5000.0, "last_result": "bogus"}}
        state = build_task_state([_task("a")], saved, now=1000.0)
        self.assertEqual(state[0]["last_result"], "success")

    def test_restores_consecutive_errors(self):
        """A saved error streak is restored with the task."""
        saved = {"a": {"next_run_time": 5000.0, "consecutive_errors": 3}}
        state = build_task_state([_task("a")], saved, now=1000.0)
        self.assertEqual(state[0]["consecutive_errors"], 3)

    def test_defaults_consecutive_errors_to_zero(self):
        """Tasks without a saved streak start clean."""
        state = build_task_state([_task("a")], {}, now=1000.0)
        self.assertEqual(state[0]["consecutive_errors"], 0)
        saved = {"a": {"next_run_time": 5000.0, "consecutive_errors": -2}}
        state = build_task_state([_task("a")], saved, now=1000.0)
        self.assertEqual(state[0]["consecutive_errors"], 0)


class TestSnapshotInstanceSchedule(unittest.TestCase):
    """snapshot_instance_schedule serializes the running task state."""

    def test_serializes_next_run_time_and_reschedule(self):
        """Both scheduled fields are persisted per task."""
        tasks = [{"id": "a", "next_run_time": 100.0, "reschedule_seconds": 400.0, "last_result": "success", "nominal_due": 50.0, "consecutive_errors": 2}]
        self.assertEqual(
            snapshot_instance_schedule(tasks),
            {"a": {"next_run_time": 100.0, "reschedule_seconds": 400.0, "last_result": "success", "nominal_due": 50.0, "consecutive_errors": 2}},
        )

    def test_handles_missing_fields(self):
        """Missing fields fall back to safe defaults."""
        snapshot = snapshot_instance_schedule([{"id": "a"}])
        self.assertIsInstance(snapshot["a"]["next_run_time"], float)
        self.assertIsInstance(snapshot["a"]["reschedule_seconds"], (int, float))

    def test_only_keeps_scheduled_fields(self):
        """Runtime-only fields (functions, names) are not persisted."""
        tasks = [{"id": "a", "next_run_time": 100.0, "reschedule_seconds": 400.0, "last_result": "error", "nominal_due": 50.0, "consecutive_errors": 1, "function": lambda: None}]
        snapshot = snapshot_instance_schedule(tasks)
        self.assertEqual(set(snapshot["a"].keys()), {"next_run_time", "reschedule_seconds", "last_result", "nominal_due", "consecutive_errors"})

    def test_defaults_missing_streak_to_zero(self):
        """Entries without a streak persist zero errors."""
        snapshot = snapshot_instance_schedule([{"id": "a", "next_run_time": 100.0, "reschedule_seconds": 400.0}])
        self.assertEqual(snapshot["a"]["consecutive_errors"], 0)

    def test_defaults_missing_anchor_to_next_run_time(self):
        """Entries without an anchor persist their due time as the anchor."""
        snapshot = snapshot_instance_schedule([{"id": "a", "next_run_time": 100.0, "reschedule_seconds": 400.0}])
        self.assertEqual(snapshot["a"]["nominal_due"], 100.0)

    def test_persists_error_result(self):
        """An error cycle is remembered so flex never applies to its retry."""
        tasks = [{"id": "a", "next_run_time": 100.0, "reschedule_seconds": 400.0, "last_result": "error"}]
        self.assertEqual(snapshot_instance_schedule(tasks)["a"]["last_result"], "error")

    def test_defaults_missing_result_to_success(self):
        """Entries without a result (old saves) behave as successful cycles."""
        snapshot = snapshot_instance_schedule([{"id": "a", "next_run_time": 100.0, "reschedule_seconds": 400.0}])
        self.assertEqual(snapshot["a"]["last_result"], "success")

    def test_normalizes_invalid_result(self):
        """Unexpected result values never reach JSON storage."""
        snapshot = snapshot_instance_schedule([{"id": "a", "next_run_time": 100.0, "reschedule_seconds": 400.0, "last_result": "x"}])
        self.assertEqual(snapshot["a"]["last_result"], "success")

    def test_replaces_invalid_values_with_safe_defaults(self):
        """Runtime corruption is normalized before it reaches JSON storage."""
        snapshot = snapshot_instance_schedule(
            [
                {"id": "a", "next_run_time": math.nan, "reschedule_seconds": math.inf},
                {"id": "b", "next_run_time": True, "reschedule_seconds": 0},
                {"id": "invalid", "next_run_time": "x", "reschedule_seconds": "x"},
                {"next_run_time": 1.0, "reschedule_seconds": 2.0},
            ]
        )
        self.assertTrue(math.isfinite(snapshot["a"]["next_run_time"]))
        self.assertEqual(snapshot["a"]["reschedule_seconds"], 3600)
        self.assertTrue(math.isfinite(snapshot["b"]["next_run_time"]))
        self.assertEqual(snapshot["b"]["reschedule_seconds"], 3600)
        self.assertEqual(snapshot["invalid"]["reschedule_seconds"], 3600)
        self.assertNotIn("", snapshot)


class TestSaveAndLoadRoundTrip(unittest.TestCase):
    """save_task_schedule / load_task_schedule persist and restore the schedule."""

    def test_round_trip(self):
        """A saved schedule is restored exactly after a reload."""
        schedule = {"0": {"All": {"claim_idle": {"next_run_time": 100.0, "reschedule_seconds": 400.0}}}}
        with tempfile.TemporaryDirectory() as tmp, patch("wosutil.tool.tasks.task_schedule.TASK_SCHEDULE_FILE", os.path.join(tmp, "task_schedule.json")):
            self.assertTrue(save_task_schedule(schedule))
            self.assertEqual(load_task_schedule(), schedule)

    def test_load_returns_empty_dict_for_missing_file(self):
        """A missing schedule file yields an empty schedule."""
        with tempfile.TemporaryDirectory() as tmp, patch("wosutil.tool.tasks.task_schedule.TASK_SCHEDULE_FILE", os.path.join(tmp, "missing.json")):
            self.assertEqual(load_task_schedule(), {})


if __name__ == "__main__":
    unittest.main()
