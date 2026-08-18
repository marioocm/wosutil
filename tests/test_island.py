"""Unit tests for the island idle income task."""

import unittest
from unittest.mock import patch

from wosutil.tool.tasks.task_automation import claim_island_idle

ROI_SIDEMENU = (0, 173, 484, 759)
ROI_ESSENCE = (0, 63, 633, 1041)


class TestClaimIslandIdle(unittest.TestCase):
    """Test cases for claiming island idle income."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_automation.go_sidemenu_daily"),
            patch("wosutil.tool.tasks.task_automation.scroll_screen"),
            patch("wosutil.tool.tasks.task_automation.click_on_text"),
            patch("wosutil.tool.tasks.task_automation.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_automation.get_template_path"),
            patch("wosutil.tool.tasks.task_automation.get_roi"),
            patch("wosutil.tool.tasks.task_automation.take_screenshot"),
            patch("wosutil.tool.tasks.task_automation.delete_temp_screenshot"),
            patch("wosutil.tool.tasks.task_automation.find_multiple_templates"),
            patch("wosutil.tool.tasks.task_automation.go_cityworld"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        (
            self.go_sidemenu_daily,
            self.scroll_screen,
            self.click_text,
            self.click_coords,
            self.get_template_path,
            self.get_roi,
            self.take_screenshot,
            self.delete_temp_screenshot,
            self.find_templates,
            self.go_cityworld,
        ) = self.mocks
        self.go_sidemenu_daily.return_value = True
        self.click_text.return_value = True
        self.get_template_path.return_value = "/tmp/life_essence.png"
        self.get_roi.side_effect = lambda name: ROI_SIDEMENU if name == "sidemenu" else ROI_ESSENCE
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_navigates_with_controlled_scroll_and_tree_click(self):
        """The Daily tab, a controlled 500px scroll and the Tree entry are used to reach the island."""
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.find_templates.return_value = []
        claim_island_idle(0)
        self.go_sidemenu_daily.assert_called_once_with(0)
        self.scroll_screen.assert_called_once_with(13, 500, 13, 0, 500, 0, hold_end_ms=500)
        self.click_text.assert_called_once_with("Tree", 0, roi=ROI_SIDEMENU, delay=4)

    def test_success_clicks_life_essence(self):
        """Found life essences are clicked and the task ends on the world map."""
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.find_templates.return_value = [(100, 200, 30, 20)]
        self.assertTrue(claim_island_idle(0))
        self.click_coords.assert_any_call(100 + 15, 200 + 10, 0, delay=1.0)
        self.go_cityworld.assert_called_once_with(0)

    def test_returns_false_when_daily_tab_not_reached(self):
        """The task fails when the Daily tab cannot be reached."""
        self.go_sidemenu_daily.return_value = False
        self.assertFalse(claim_island_idle(0))
        self.scroll_screen.assert_not_called()

    def test_returns_false_when_tree_missing(self):
        """The task fails when the Tree entry is not found."""
        self.click_text.return_value = False
        self.assertFalse(claim_island_idle(0))
        self.take_screenshot.assert_not_called()

    def test_returns_false_when_no_essence(self):
        """The task fails and returns to the world map when no life essence is found."""
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.find_templates.return_value = []
        self.assertFalse(claim_island_idle(0))
        self.go_cityworld.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
