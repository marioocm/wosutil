"""Unit tests for task automation flows."""

import unittest
from unittest.mock import patch

from wosutil.tool.tasks.task_automation import PET_SKILL_RESCHEDULE_SECONDS, activate_daily_pet_skills


class TestActivateDailyPetSkills(unittest.TestCase):
    """Test the active ox gathering branch of the pet skills task."""

    def test_active_ox_gathers_then_returns_to_pet_skills(self):
        """An active ox starts gathering before the task continues with pet skills."""
        with patch("wosutil.tool.tasks.task_automation.go_pet_skill", return_value=True) as go_pet_skill, \
            patch("wosutil.tool.tasks.task_automation.ensure_pet_skill_screen", return_value=True), \
            patch("wosutil.tool.tasks.task_automation.is_game_on_screen", return_value=True) as is_on_screen, \
            patch("wosutil.tool.tasks.task_automation.click_first_found_template", return_value=None) as click_first_found, \
            patch("wosutil.tool.tasks.task_automation.click_on_template"), \
            patch("wosutil.tool.tasks.task_automation.get_roi", return_value=(0, 0, 1, 1)), \
            patch("wosutil.tool.tasks.task_automation.read_screen_time", return_value=None) as read_screen_time, \
            patch("wosutil.tool.tasks.task_automation.press_android_back_button") as back_button, \
            patch("wosutil.tool.tasks.task_automation.gather_tile", return_value=180) as gather, \
            patch("wosutil.tool.tasks.task_automation.get_gather_resource", return_value="wood"):
            result = activate_daily_pet_skills(0)

        self.assertEqual(result, (True, PET_SKILL_RESCHEDULE_SECONDS))
        gather.assert_called_once_with(0, "wood")
        self.assertEqual(go_pet_skill.call_count, 2)
        self.assertEqual(read_screen_time.call_count, 3)
        self.assertFalse(any("pet_skill_ox_timer" in c.kwargs.get("debug_label", "") for c in read_screen_time.call_args_list))
        is_on_screen.assert_called_once_with(0, "pet_skill_ox_active", "pet_skill_ox_timer")
        back_button.assert_called_with(0)
        self.assertEqual(back_button.call_count, 2)
        click_first_found.assert_called_once_with(
            0,
            ["pet_skill_wolf", "pet_skill_tapir", "pet_skill_elk"],
            roi=(0, 0, 1, 1),
            delay=0.8,
        )

    def test_gather_failure_continues_with_remaining_pet_skills(self):
        """A failed ox gathering attempt does not abort the pet skills task."""
        with patch("wosutil.tool.tasks.task_automation.go_pet_skill", return_value=True) as go_pet_skill, \
            patch("wosutil.tool.tasks.task_automation.ensure_pet_skill_screen", return_value=True), \
            patch("wosutil.tool.tasks.task_automation.is_game_on_screen", return_value=True), \
            patch(
                "wosutil.tool.tasks.task_automation.click_first_found_template",
                side_effect=["pet_skill_wolf", None],
            ) as click_first_found, \
            patch("wosutil.tool.tasks.task_automation.click_on_template", return_value=True) as click_use, \
            patch("wosutil.tool.tasks.task_automation.get_roi", return_value=(0, 0, 1, 1)), \
            patch("wosutil.tool.tasks.task_automation.read_screen_time", return_value=None), \
            patch("wosutil.tool.tasks.task_automation.press_android_back_button"), \
            patch("wosutil.tool.tasks.task_automation.gather_tile", return_value=None) as gather, \
            patch("wosutil.tool.tasks.task_automation.get_gather_resource", return_value="wood"):
            result = activate_daily_pet_skills(0)

        self.assertEqual(result, (True, PET_SKILL_RESCHEDULE_SECONDS))
        gather.assert_called_once_with(0, "wood")
        self.assertEqual(go_pet_skill.call_count, 2)
        self.assertEqual(click_first_found.call_count, 2)
        click_use.assert_called_once()


if __name__ == "__main__":
    unittest.main()
