"""Unit tests for the shop tasks."""

import unittest
from unittest.mock import patch

from wosutil.tool.tasks.task_automation import claim_mystery_shop, claim_nomadic_shop_rss_and_vip

DEFAULT = 10 * 60 * 60
ROI_SHOP_TABS = (0, 1195, 719, 85)


class TestClaimNomadicShop(unittest.TestCase):
    """Test cases for the nomadic shop task."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_automation.go_shop"),
            patch("wosutil.tool.tasks.task_automation.get_roi"),
            patch("wosutil.tool.tasks.task_automation.click_on_text"),
            patch("wosutil.tool.tasks.task_automation.take_screenshot"),
            patch("wosutil.tool.tasks.task_automation.delete_temp_screenshot"),
            patch("wosutil.tool.tasks.task_automation.get_template_path"),
            patch("wosutil.tool.tasks.task_automation.find_template_center_on_screen"),
            patch("wosutil.tool.tasks.task_automation.click_on_template"),
            patch("wosutil.tool.tasks.task_automation.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_automation.get_seconds_until_utc_midnight"),
            patch("wosutil.tool.tasks.task_automation.press_android_back_button"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        (
            self.go_shop,
            self.get_roi,
            self.click_text,
            self.take_screenshot,
            self.delete_temp_screenshot,
            self.get_template_path,
            self.find_center,
            self.click_template,
            self.click_coords,
            self.utc_midnight,
            self.back_press,
        ) = self.mocks
        self.go_shop.return_value = True
        self.get_roi.return_value = ROI_SHOP_TABS
        self.click_text.return_value = True
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.get_template_path.return_value = "/tmp/template.png"
        self.find_center.return_value = (False, None)
        self.click_template.return_value = False
        self.utc_midnight.return_value = 3600
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_clicks_nomadic_tab_after_shop(self):
        """After opening the shop the Nomadic tab is clicked by text."""
        self.assertEqual(claim_nomadic_shop_rss_and_vip(0), (True, 3600))
        self.go_shop.assert_called_once_with(0)
        self.click_text.assert_called_once_with("Nomadic", 0, roi=ROI_SHOP_TABS, delay=1.0)

    def test_fails_when_nomadic_tab_missing(self):
        """The task fails when the Nomadic tab is not found."""
        self.click_text.return_value = False
        self.assertEqual(claim_nomadic_shop_rss_and_vip(0), (False, DEFAULT))
        self.take_screenshot.assert_not_called()


class TestClaimMysteryShop(unittest.TestCase):
    """Test cases for the mystery shop task."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_automation.go_shop"),
            patch("wosutil.tool.tasks.task_automation.get_roi"),
            patch("wosutil.tool.tasks.task_automation.click_on_text"),
            patch("wosutil.tool.tasks.task_automation.get_mystery_shop_level"),
            patch("wosutil.tool.tasks.task_automation.take_screenshot"),
            patch("wosutil.tool.tasks.task_automation.delete_temp_screenshot"),
            patch("wosutil.tool.tasks.task_automation.get_template_path"),
            patch("wosutil.tool.tasks.task_automation.find_template_center_on_screen"),
            patch("wosutil.tool.tasks.task_automation.click_on_template"),
            patch("wosutil.tool.tasks.task_automation.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_automation.get_seconds_until_utc_midnight"),
            patch("wosutil.tool.tasks.task_automation.press_android_back_button"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        (
            self.go_shop,
            self.get_roi,
            self.click_text,
            self.get_shop_level,
            self.take_screenshot,
            self.delete_temp_screenshot,
            self.get_template_path,
            self.find_center,
            self.click_template,
            self.click_coords,
            self.utc_midnight,
            self.back_press,
        ) = self.mocks
        self.go_shop.return_value = True
        self.get_roi.return_value = ROI_SHOP_TABS
        self.click_text.return_value = True
        self.get_shop_level.return_value = "free"
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.get_template_path.return_value = "/tmp/template.png"
        self.find_center.return_value = (False, None)
        self.click_template.return_value = False
        self.utc_midnight.return_value = 3600
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_clicks_mystery_tab_after_shop(self):
        """After opening the shop the Mystery tab is clicked by text."""
        self.assertEqual(claim_mystery_shop(0), (True, 3600))
        self.go_shop.assert_called_once_with(0)
        self.click_text.assert_called_once_with("Mystery", 0, roi=ROI_SHOP_TABS, delay=1.0)

    def test_fails_when_mystery_tab_missing(self):
        """The task fails when the Mystery tab is not found."""
        self.click_text.return_value = False
        self.assertEqual(claim_mystery_shop(0), (False, DEFAULT))
        self.take_screenshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
