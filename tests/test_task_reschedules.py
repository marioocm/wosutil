"""Unit tests for per-task success/error reschedules (SPEC-retry-flex.md)."""

import unittest
from unittest.mock import patch

from wosutil.tool.tasks.task_automation import (
    activate_daily_pet_skills,
    claim_pet_adventure_ally_treasure,
    claim_recruit_hero_free_chest,
    claim_storehouse_stamina,
    claim_tundra_trek_supplies,
    claim_vip_daily_rewards,
    do_intel_missions,
)

RETRY = 2 * 60 * 60


class TestRecruitHeroChestReschedule(unittest.TestCase):
    """The recruit task uses its timer, 5h without one, 2h on error."""

    def setUp(self):
        """Set up shared mocks on the hero recruit screen without chests."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_automation.ensure_city_screen", return_value=True),
            patch("wosutil.tool.tasks.task_automation.click_on"),
            patch("wosutil.tool.tasks.task_automation.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_automation.ensure_hero_recruit_screen", return_value=True),
            patch("wosutil.tool.tasks.task_automation.take_screenshot", return_value="/tmp/shot.png"),
            patch("wosutil.tool.tasks.task_automation.delete_temp_screenshot"),
            patch("wosutil.tool.tasks.task_automation.get_template_path", return_value="/tmp/t.png"),
            patch("wosutil.tool.tasks.task_automation.get_roi", return_value=(0, 0, 10, 10)),
            patch("wosutil.tool.tasks.task_automation.find_multiple_templates", return_value=[]),
            patch("wosutil.tool.tasks.task_automation.press_android_back_button"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.read_timer = patch("wosutil.tool.tasks.task_automation.read_screen_time").start()
        self.addCleanup(lambda: [p.stop() for p in self.patchers])
        self.addCleanup(self.read_timer.stop)

    def test_uses_timer_when_readable(self):
        """A readable chest timer drives the reschedule."""
        self.read_timer.return_value = 7000
        self.assertEqual(claim_recruit_hero_free_chest(0), (True, 7000))

    def test_uses_five_hours_without_timer(self):
        """A completed run without a timer reschedules in 5h."""
        self.read_timer.return_value = None
        self.assertEqual(claim_recruit_hero_free_chest(0), (True, 5 * 60 * 60))

    def test_retries_when_city_unreachable(self):
        """Missing the city screen retries in 2h."""
        with patch("wosutil.tool.tasks.task_automation.ensure_city_screen", return_value=False):
            self.assertEqual(claim_recruit_hero_free_chest(0), (False, RETRY))


class TestStorehouseStaminaReschedule(unittest.TestCase):
    """The storehouse task uses its timer, 12h without one, error on failure."""

    def setUp(self):
        """Set up shared mocks on the profile screen."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_automation.ensure_city_screen", return_value=True),
            patch("wosutil.tool.tasks.task_automation.click_on"),
            patch("wosutil.tool.tasks.task_automation.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_automation.get_roi", return_value=(0, 0, 10, 10)),
            patch("wosutil.tool.tasks.task_automation.press_android_back_button"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.click_template = patch("wosutil.tool.tasks.task_automation.click_on_template").start()
        self.read_timer = patch("wosutil.tool.tasks.task_automation.read_screen_time").start()
        self.addCleanup(lambda: [p.stop() for p in self.patchers])
        self.addCleanup(self.click_template.stop)
        self.addCleanup(self.read_timer.stop)

    def test_uses_timer_when_readable(self):
        """A readable stamina timer drives the reschedule."""
        self.click_template.return_value = True
        self.read_timer.return_value = 8000
        self.assertEqual(claim_storehouse_stamina(0), (True, 8000))

    def test_uses_twelve_hours_without_timer(self):
        """A completed run without a timer reschedules in 12h."""
        self.click_template.return_value = False
        self.read_timer.return_value = None
        self.assertEqual(claim_storehouse_stamina(0), (True, 12 * 60 * 60))

    def test_fails_when_city_unreachable(self):
        """Missing the city screen fails so the controller retries in 2h."""
        with patch("wosutil.tool.tasks.task_automation.ensure_city_screen", return_value=False):
            self.assertFalse(claim_storehouse_stamina(0))


class TestIntelMissionsReschedule(unittest.TestCase):
    """The intel task uses its timers, 6h without one, error when blocked."""

    def setUp(self):
        """Set up shared mocks with no missions available."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_automation.kill_intel_beast", return_value=None),
            patch("wosutil.tool.tasks.task_automation.rescue_intel_survivor", return_value=False),
            patch("wosutil.tool.tasks.task_automation.do_intel_exploration", return_value=False),
            patch("wosutil.tool.tasks.task_automation.get_roi", return_value=(0, 0, 10, 10)),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.on_intel = patch("wosutil.tool.tasks.task_automation.is_game_on_intel_screen").start()
        self.read_timer = patch("wosutil.tool.tasks.task_automation.read_screen_time").start()
        self.addCleanup(lambda: [p.stop() for p in self.patchers])
        self.addCleanup(self.on_intel.stop)
        self.addCleanup(self.read_timer.stop)

    def test_uses_intel_timer_when_readable(self):
        """A readable intel timer drives the reschedule."""
        self.on_intel.return_value = True
        self.read_timer.return_value = 3700
        self.assertEqual(do_intel_missions(0), (True, 3700))

    def test_uses_six_hours_without_timer(self):
        """A completed run without a timer reschedules in 6h."""
        self.on_intel.return_value = True
        self.read_timer.return_value = None
        self.assertEqual(do_intel_missions(0), (True, 6 * 60 * 60))

    def test_fails_when_intel_screen_unreachable(self):
        """No work done and no intel screen means the game is blocked."""
        self.on_intel.return_value = False
        self.assertFalse(do_intel_missions(0))


class TestErrorRetries(unittest.TestCase):
    """Navigation failures retry in 2h across tasks."""

    def test_vip_retries_when_city_unreachable(self):
        """The VIP task retries when the city screen is missing."""
        with patch("wosutil.tool.tasks.task_automation.ensure_city_screen", return_value=False):
            self.assertEqual(claim_vip_daily_rewards(0), (False, RETRY))

    def test_ally_treasure_retries_when_navigation_fails(self):
        """The ally treasure task retries when pet adventure is unreachable."""
        with patch("wosutil.tool.tasks.task_automation.go_pet_adventure", return_value=False):
            self.assertEqual(claim_pet_adventure_ally_treasure(0), (False, RETRY))

    def test_tundra_supplies_retries_when_navigation_fails(self):
        """The tundra supplies task retries when the screen is unreachable."""
        with patch("wosutil.tool.tasks.task_automation.go_tundra_trek", return_value=False):
            self.assertEqual(claim_tundra_trek_supplies(0), (False, RETRY))

    def test_pet_skills_retries_when_navigation_fails(self):
        """The pet skills task retries when the screen is unreachable."""
        with patch("wosutil.tool.tasks.task_automation.go_pet_skill", return_value=False):
            self.assertEqual(activate_daily_pet_skills(0), (False, RETRY))


if __name__ == "__main__":
    unittest.main()
