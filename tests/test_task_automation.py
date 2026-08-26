"""Unit tests for task automation flows."""

import calendar
import unittest
from unittest.mock import patch

from wosutil.config import (
    BEAR_TRAP_DURATION_SECONDS,
    BEAR_TRAP_PREP_SECONDS,
    BEAR_TRAP_SCHEDULE_RETRY_SECONDS,
)
from wosutil.tool.tasks.task_automation import (
    PET_SKILL_RESCHEDULE_SECONDS,
    _bear_trap_prepare_and_join,
    _next_bear_hunt_start,
    activate_daily_pet_skills,
    play_bear_trap,
)

NEXT_HUNT = (2026, 8, 26, 12, 0)  # 2026-08-26 12:00 UTC
NEXT_HUNT_START = calendar.timegm((2026, 8, 26, 12, 0, 0, 0, 0))


class TestActivateDailyPetSkills(unittest.TestCase):
    """Test the active ox gathering branch of the pet skills task."""

    def test_active_ox_gather_reschedules_for_the_march_time(self):
        """A sent ox gathering march reschedules the task for its round trip."""
        with patch("wosutil.tool.tasks.task_automation.go_pet_skill", return_value=True) as go_pet_skill, patch("wosutil.tool.tasks.task_automation.ensure_pet_skill_screen", return_value=True), patch(
            "wosutil.tool.tasks.task_automation.is_game_on_screen", return_value=True
        ) as is_on_screen, patch("wosutil.tool.tasks.task_automation.click_first_found_template", return_value=None) as click_first_found, patch(
            "wosutil.tool.tasks.task_automation.click_on_template"
        ), patch("wosutil.tool.tasks.task_automation.get_roi", return_value=(0, 0, 1, 1)), patch("wosutil.tool.tasks.task_automation.read_screen_time", return_value=None) as read_screen_time, patch(
            "wosutil.tool.tasks.task_automation.press_android_back_button"
        ) as back_button, patch("wosutil.tool.tasks.task_automation.gather_tile", return_value=130) as gather, patch("wosutil.tool.tasks.task_automation.get_gather_resource", return_value="wood"):
            result = activate_daily_pet_skills(0)

        self.assertEqual(result, (True, 130))
        gather.assert_called_once_with(0, "wood")
        self.assertEqual(go_pet_skill.call_count, 1)
        is_on_screen.assert_called_once_with(0, "pet_skill_ox_active", "pet_skill_ox_timer")
        back_button.assert_called_once_with(0)
        click_first_found.assert_not_called()
        read_screen_time.assert_not_called()

    def test_gather_failure_continues_with_remaining_pet_skills(self):
        """A failed ox gathering attempt does not abort the pet skills task."""
        with patch("wosutil.tool.tasks.task_automation.go_pet_skill", return_value=True) as go_pet_skill, patch("wosutil.tool.tasks.task_automation.ensure_pet_skill_screen", return_value=True), patch(
            "wosutil.tool.tasks.task_automation.is_game_on_screen", return_value=True
        ), patch(
            "wosutil.tool.tasks.task_automation.click_first_found_template",
            side_effect=["pet_skill_wolf", None],
        ) as click_first_found, patch("wosutil.tool.tasks.task_automation.click_on_template", return_value=True) as click_use, patch(
            "wosutil.tool.tasks.task_automation.get_roi", return_value=(0, 0, 1, 1)
        ), patch("wosutil.tool.tasks.task_automation.read_screen_time", return_value=None), patch("wosutil.tool.tasks.task_automation.press_android_back_button"), patch(
            "wosutil.tool.tasks.task_automation.gather_tile", return_value=None
        ) as gather, patch("wosutil.tool.tasks.task_automation.get_gather_resource", return_value="wood"):
            result = activate_daily_pet_skills(0)

        self.assertEqual(result, (True, PET_SKILL_RESCHEDULE_SECONDS))
        gather.assert_called_once_with(0, "wood")
        self.assertEqual(go_pet_skill.call_count, 2)
        self.assertEqual(click_first_found.call_count, 2)
        click_use.assert_called_once()


class TestNextBearHuntStart(unittest.TestCase):
    """Test selecting the next joinable bear hunt from the cached schedule."""

    def test_no_schedule_returns_none(self):
        """An empty schedule yields no hunt to join."""
        self.assertIsNone(_next_bear_hunt_start([], NEXT_HUNT_START))

    def test_picks_earliest_upcoming(self):
        """The earliest hunt that has not fully ended is selected."""
        hunts = [
            (2026, 8, 26, 8, 0),  # early morning, already ended
            (2026, 8, 26, 12, 0),
            (2026, 8, 27, 12, 0),
        ]
        self.assertEqual(_next_bear_hunt_start(hunts, calendar.timegm((2026, 8, 26, 9, 0, 0, 0, 0))), NEXT_HUNT_START)

    def test_in_progress_hunt_still_joinable(self):
        """A hunt that already started is returned while its window is open."""
        now = NEXT_HUNT_START + 15 * 60  # half-way into the attack window
        self.assertEqual(_next_bear_hunt_start([NEXT_HUNT], now), NEXT_HUNT_START)

    def test_ended_hunt_is_ignored(self):
        """A hunt whose window fully ended is not returned."""
        now = NEXT_HUNT_START + BEAR_TRAP_DURATION_SECONDS
        self.assertIsNone(_next_bear_hunt_start([NEXT_HUNT], now))


class TestPlayBearTrapScheduling(unittest.TestCase):
    """Test the bear trap scheduling around the Bear Hunt."""

    def test_schedules_preparation_before_next_hunt(self):
        """A task run long before the hunt reschedules itself for T-5 minutes."""
        with patch("wosutil.tool.tasks.task_automation.time.time", return_value=NEXT_HUNT_START - 24 * 60 * 60), patch(
            "wosutil.tool.tasks.task_automation.get_cached_bear_hunt_times", return_value=[NEXT_HUNT]
        ) as get_hunts, patch("wosutil.tool.tasks.task_automation._bear_trap_prepare_and_join") as prepare:
            result = play_bear_trap(0)

        self.assertEqual(result, (True, (NEXT_HUNT_START - BEAR_TRAP_PREP_SECONDS) - (NEXT_HUNT_START - 24 * 60 * 60)))
        prepare.assert_not_called()
        get_hunts.assert_called_once_with(0)

    def test_runs_preparation_window(self):
        """Within T-5 min and the hunt end the task prepares and joins."""
        run_at = NEXT_HUNT_START - BEAR_TRAP_PREP_SECONDS + 60
        with patch("wosutil.tool.tasks.task_automation.time.time", return_value=run_at), patch(
            "wosutil.tool.tasks.task_automation.get_cached_bear_hunt_times", return_value=[NEXT_HUNT]
        ), patch("wosutil.tool.tasks.task_automation._bear_trap_prepare_and_join", return_value=True) as prepare:
            result = play_bear_trap(0)

        self.assertTrue(result)
        prepare.assert_called_once_with(0, end=NEXT_HUNT_START + BEAR_TRAP_DURATION_SECONDS)

    def test_runs_immediately_without_schedule(self):
        """Without any cached hunts the task keeps the legacy immediate behavior."""
        with patch("wosutil.tool.tasks.task_automation.time.time", return_value=NEXT_HUNT_START), patch(
            "wosutil.tool.tasks.task_automation.get_cached_bear_hunt_times", return_value=[]
        ), patch("wosutil.tool.tasks.task_automation._bear_trap_prepare_and_join", return_value=True) as prepare:
            result = play_bear_trap(0)

        self.assertTrue(result)
        prepare.assert_called_once_with(0, end=NEXT_HUNT_START + BEAR_TRAP_DURATION_SECONDS)

    def test_retries_when_only_ended_hunts_known(self):
        """Known hunts that all ended reschedule for a later retry instead of running."""
        with patch("wosutil.tool.tasks.task_automation.time.time", return_value=NEXT_HUNT_START + BEAR_TRAP_DURATION_SECONDS), patch(
            "wosutil.tool.tasks.task_automation.get_cached_bear_hunt_times", return_value=[NEXT_HUNT]
        ), patch("wosutil.tool.tasks.task_automation._bear_trap_prepare_and_join") as prepare:
            result = play_bear_trap(0)

        self.assertEqual(result, (True, BEAR_TRAP_SCHEDULE_RETRY_SECONDS))
        prepare.assert_not_called()


class TestBearTrapPrepareAndJoin(unittest.TestCase):
    """Test the preparation + join window of the bear trap task."""

    def test_waits_inside_until_window_start(self):
        """Firing during preparation waits until the hunt starts before joining."""
        run_at = NEXT_HUNT_START - BEAR_TRAP_PREP_SECONDS + 60
        with patch(
            "wosutil.tool.tasks.task_automation.time.time",
            side_effect=[
                run_at,
                NEXT_HUNT_START,
                NEXT_HUNT_START + 1,
                NEXT_HUNT_START + 2,
                NEXT_HUNT_START + 3,
                NEXT_HUNT_START + 4,
                NEXT_HUNT_START + 5,
                NEXT_HUNT_START + 6,
                NEXT_HUNT_START + 5000,
            ],
        ), patch(
            "wosutil.tool.tasks.task_automation.ensure_world_screen", return_value=True
        ), patch("wosutil.tool.tasks.task_automation.take_screenshot", return_value="/tmp/shot.png"), patch(
            "wosutil.tool.tasks.task_automation.delete_temp_screenshot"
        ), patch("wosutil.tool.tasks.task_automation.find_text_center_on_screen", return_value=(False, None)), patch(
            "wosutil.tool.tasks.task_automation.activate_battle_pet_skills", return_value=True
        ) as activate, patch("wosutil.tool.tasks.task_automation.get_bear_trap_marches", return_value=[1]), patch(
            "wosutil.tool.tasks.task_automation.stop_signal.wait", return_value=False
        ) as stop_wait, patch("wosutil.tool.tasks.task_automation.join_bear_rally", return_value=None) as join:
            result = _bear_trap_prepare_and_join(0, NEXT_HUNT_START + BEAR_TRAP_DURATION_SECONDS)

        self.assertTrue(result)
        activate.assert_called_once_with(0)
        join.assert_called_once_with(0, 1)
        stop_wait.assert_called_once()

    def test_joins_immediately_after_window_start(self):
        """Firing when the window already started skips the wait and joins."""
        now = NEXT_HUNT_START + 60
        with patch(
            "wosutil.tool.tasks.task_automation.time.time",
            side_effect=[now, now + 1, now + 2, now + 3, now + 4, now + 5, now + 5000],
        ), patch(
            "wosutil.tool.tasks.task_automation.ensure_world_screen", return_value=True
        ), patch("wosutil.tool.tasks.task_automation.take_screenshot", return_value="/tmp/shot.png"), patch(
            "wosutil.tool.tasks.task_automation.delete_temp_screenshot"
        ), patch("wosutil.tool.tasks.task_automation.find_text_center_on_screen", return_value=(False, None)), patch(
            "wosutil.tool.tasks.task_automation.activate_battle_pet_skills", return_value=True
        ), patch("wosutil.tool.tasks.task_automation.get_bear_trap_marches", return_value=[1]), patch(
            "wosutil.tool.tasks.task_automation.stop_signal.wait", return_value=False
        ) as stop_wait, patch("wosutil.tool.tasks.task_automation.join_bear_rally", return_value=None) as join:
            result = _bear_trap_prepare_and_join(0, NEXT_HUNT_START + BEAR_TRAP_DURATION_SECONDS)

        self.assertTrue(result)
        join.assert_called_once_with(0, 1)
        stop_wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()


