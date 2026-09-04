"""Unit tests for task timing metadata (success / retry / early / late)."""

import unittest

from wosutil.tool.tasks.task_definitions import get_all_task_ids, get_task_definitions

HOUR = 60 * 60

# Expected (success, retry, early, late) in seconds per task id, from
# SPEC-retry-flex.md. retry=None means the task has no error retry
# (bear trap runs only on its read schedule).
EXPECTED = {
    "play_bear_trap": (6 * HOUR, None, 0, 0),
    "claim_idle": (8 * HOUR, 2 * HOUR, 2 * HOUR, 1 * HOUR),
    "donate_tech": (4 * HOUR, 2 * HOUR, 1 * HOUR, 0),
    "autojoin": (7 * HOUR, 2 * HOUR, 2 * HOUR, 30 * 60),
    "claim_island": (8 * HOUR, 2 * HOUR, 2 * HOUR, 1 * HOUR),
    "claim_mail": (8 * HOUR, 2 * HOUR, 2 * HOUR, 1 * HOUR),
    "claim_alliance_chests": (10 * HOUR, 2 * HOUR, 2 * HOUR, 2 * HOUR),
    "claim_triumph": (12 * HOUR, 2 * HOUR, 2 * HOUR, 0),
    "claim_recruit_hero_free_chest": (5 * HOUR, 2 * HOUR, 0, 0),
    "claim_storehouse_stamina": (12 * HOUR, 2 * HOUR, 0, 4 * HOUR),
    "do_intel_missions": (6 * HOUR, 2 * HOUR, 0, 2 * HOUR),
    "claim_nomadic_shop_rss_and_vip": (12 * HOUR, 2 * HOUR, 0, 4 * HOUR),
    "claim_mystery_shop": (12 * HOUR, 2 * HOUR, 0, 4 * HOUR),
    "claim_vip_daily_rewards": (12 * HOUR, 2 * HOUR, 0, 4 * HOUR),
    "claim_tundra_trek_supplies": (6 * HOUR, 2 * HOUR, 0, 2 * HOUR),
    "start_tundra_trek_idle": (12 * HOUR, 2 * HOUR, 0, 0),
    "claim_pet_adventure_ally_treasure": (12 * HOUR, 2 * HOUR, 0, 3 * HOUR),
    "send_pet_adventure_chests": (5 * HOUR, 2 * HOUR, 0, 0),
    "activate_daily_pet_skills": (6 * HOUR, 2 * HOUR, 0, 10 * 60),
    "train_troops": (6 * HOUR, 2 * HOUR, 0, 10 * 60),
}


class TestTaskTimingMetadata(unittest.TestCase):
    """Every task exposes success / retry / early / late timings."""

    def test_all_tasks_present(self):
        """The canonical id list matches the timing table."""
        self.assertEqual(set(get_all_task_ids()), set(EXPECTED))

    def test_timing_values_match_spec(self):
        """reschedule/retry/early/late hold the SPEC values."""
        task_definitions = get_task_definitions()
        for task_id, (success, retry, early, late) in EXPECTED.items():
            with self.subTest(task=task_id):
                task = task_definitions[task_id]
                self.assertEqual(task["reschedule_seconds"], success)
                self.assertEqual(task.get("retry_seconds"), retry)
                self.assertEqual(task.get("early_seconds"), early)
                self.assertEqual(task.get("late_seconds"), late)


if __name__ == "__main__":
    unittest.main()
