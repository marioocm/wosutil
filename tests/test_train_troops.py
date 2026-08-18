"""Unit tests for the troop training task."""

import unittest
from unittest.mock import patch

from wosutil.tool.tasks.task_automation import train_troops
from wosutil.tool.tasks.task_helpers import _train_troop_camp

DEFAULT = 6 * 60 * 60
ROI_SPEED_UP = (306, 940, 412, 310)
ROI_TIMER = (458, 920, 127, 38)
ROI_PROMOTE_TEXT = (3, 768, 717, 28)
ROI_PROMOTE = (561, 394, 159, 274)


class TestTrainTroopCamp(unittest.TestCase):
    """Test cases for the single troop camp flow."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_helpers.is_game_on_screen"),
            patch("wosutil.tool.tasks.task_helpers.find_first_non_zero_digit_position"),
            patch("wosutil.tool.tasks.task_helpers.click_on_template"),
            patch("wosutil.tool.tasks.task_helpers.read_screen_time"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.click_coords, self.on_screen, self.digit_pos, self.click_template, self.read_timer = self.mocks
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def header_clicks(self):
        """Return the header taps done at (360, 40) with delay 0.5."""
        return [c for c in self.click_coords.call_args_list if c.args[:2] == (360, 40) and c.kwargs.get("delay") == 0.5]

    def test_taps_header_four_times(self):
        """The camp header is tapped 4 times before any check."""
        self.on_screen.return_value = True
        self.read_timer.return_value = 100
        _train_troop_camp(0)
        self.assertEqual(len(self.header_clicks()), 4)

    def test_returns_timer_when_training_in_progress(self):
        """When the speed-up icon is present the remaining timer is returned."""
        self.on_screen.return_value = True
        self.read_timer.return_value = 1234
        result = _train_troop_camp(0)
        self.assertEqual(result, 1234)
        self.on_screen.assert_called_once_with(0, "troop_train_speed_up", "troop_train_speed_up")
        self.read_timer.assert_called_once_with(0, roi=ROI_TIMER, debug_label="troop_train_timer")
        self.digit_pos.assert_not_called()

    def test_returns_default_when_timer_unreadable(self):
        """When training is active but the timer cannot be read, None is returned."""
        self.on_screen.return_value = True
        self.read_timer.return_value = None
        self.assertIsNone(_train_troop_camp(0))

    def test_promotes_then_finds_timer(self):
        """An idle/completed camp is promoted, then the timer is found on the next check."""
        self.on_screen.side_effect = [False, True]
        self.digit_pos.return_value = (100, 900)
        self.click_template.return_value = True
        self.read_timer.return_value = 200
        result = _train_troop_camp(0)
        self.assertEqual(result, 200)
        self.click_coords.assert_any_call(100, 900 - 84, 0, delay=1.0)
        self.click_template.assert_called_once_with("train_troop_promote", 0, roi=ROI_PROMOTE, delay=1.0)
        self.click_coords.assert_any_call(521, 904, 0, delay=1.0)

    def test_uses_train_button_when_promote_not_found(self):
        """A missing promote button falls back to the train button."""
        self.on_screen.side_effect = [False, True]
        self.digit_pos.return_value = None
        self.click_template.return_value = False
        self.read_timer.return_value = 300
        result = _train_troop_camp(0)
        self.assertEqual(result, 300)
        self.click_coords.assert_any_call(531, 1119, 0, delay=1.0)

    def test_returns_none_when_no_training_after_attempts(self):
        """After all attempts without a detected training, None is returned."""
        self.on_screen.return_value = False
        self.digit_pos.return_value = None
        self.click_template.return_value = False
        self.assertIsNone(_train_troop_camp(0))
        self.assertEqual(self.click_coords.call_count, 4 + 3)  # 4 header taps + 3 train fallback clicks


class TestTrainTroopsTask(unittest.TestCase):
    """Test cases for the full troop training task."""

    def setUp(self):
        """Set up shared mocks for the task-level tests."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_automation.go_sidemenu_city"),
            patch("wosutil.tool.tasks.task_automation.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_automation.click_on_template"),
            patch("wosutil.tool.tasks.task_automation.click_on_text"),
            patch("wosutil.tool.tasks.task_automation.press_android_back_button"),
            patch("wosutil.tool.tasks.task_automation._train_troop_camp"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.go_sidemenu_city, self.click_coords, self.click_template, self.click_text, self.back_press, self.train_camp = self.mocks
        self.go_sidemenu_city.return_value = True
        self.click_template.return_value = True
        self.click_text.return_value = True
        self.train_camp.return_value = None
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_uses_shortest_timer_for_reschedule(self):
        """The shortest readable training timer drives the reschedule, ignoring None."""
        self.train_camp.side_effect = [720, None, 360]
        self.assertEqual(train_troops(0), (True, 360))
        self.go_sidemenu_city.assert_called_once_with(0)
        self.click_text.assert_called_once_with("Infantry", 0, roi=(0, 173, 484, 759), delay=3)
        self.assertEqual(self.train_camp.call_count, 3)
        self.click_coords.assert_any_call(362, 1238, 0, delay=1.0)
        self.click_coords.assert_any_call(586, 1238, 0, delay=1.0)
        self.back_press.assert_called_once()

    def test_default_reschedule_when_no_timers(self):
        """Without any readable timer the task reschedules in 6 hours."""
        self.assertEqual(train_troops(0), (True, DEFAULT))

    def test_fails_when_side_menu_not_opened(self):
        """The task fails when the City tab cannot be reached."""
        self.go_sidemenu_city.return_value = False
        self.assertEqual(train_troops(0), (False, DEFAULT))
        self.click_text.assert_not_called()

    def test_fails_when_infantry_entry_missing(self):
        """The task fails when the Infantry entry is not found in the side menu."""
        self.click_text.return_value = False
        self.assertEqual(train_troops(0), (False, DEFAULT))
        self.train_camp.assert_not_called()

    def test_fails_when_train_button_missing(self):
        """The task fails when the train troop button is not on the screen."""
        self.click_template.return_value = False
        self.assertEqual(train_troops(0), (False, DEFAULT))
        self.back_press.assert_called_once()


if __name__ == "__main__":
    unittest.main()
