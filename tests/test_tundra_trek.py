"""Unit tests for the tundra trek tasks."""

import unittest
from unittest.mock import patch

from wosutil.tool.tasks.task_automation import start_tundra_trek_idle

ROI_IDLE = (526, 1126, 194, 154)


class TestStartTundraTrekIdle(unittest.TestCase):
    """Test cases for starting a tundra trek idle hunt."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_automation.go_tundra_trek"),
            patch("wosutil.tool.tasks.task_automation.end_tundra_trek_idle_if_active"),
            patch("wosutil.tool.tasks.task_automation.get_roi"),
            patch("wosutil.tool.tasks.task_automation.click_on_text"),
            patch("wosutil.tool.tasks.task_automation.click_on_coordinates"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.go_tundra_trek, self.end_idle, self.get_roi, self.click_text, self.click_coords = self.mocks
        self.go_tundra_trek.return_value = True
        self.get_roi.return_value = ROI_IDLE
        self.click_text.return_value = True
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_clicks_idle_text_in_roi(self):
        """The 'Idle' text is searched in the idle ROI and clicked."""
        self.assertTrue(start_tundra_trek_idle(0))
        self.click_text.assert_called_once_with("Idle", 0, roi=ROI_IDLE)
        self.click_coords.assert_called_once_with(362, 877, 0)

    def test_returns_false_when_tundra_trek_not_reached(self):
        """The task fails when the tundra trek screen cannot be reached."""
        self.go_tundra_trek.return_value = False
        self.assertFalse(start_tundra_trek_idle(0))
        self.click_text.assert_not_called()

    def test_returns_false_when_idle_text_missing(self):
        """The task fails when the 'Idle' text is not found."""
        self.click_text.return_value = False
        self.assertFalse(start_tundra_trek_idle(0))
        self.click_coords.assert_not_called()


if __name__ == "__main__":
    unittest.main()
