"""Unit tests for the UTC clock synchronization (world map schedule panel)."""

import unittest
from unittest.mock import patch

from wosutil.emulator.image_utils import parse_utc_text
from wosutil.tool.tasks.task_automation import (
    _finish_pet_adventure_starts,
    claim_pet_adventure_ally_treasure,
    claim_vip_daily_rewards,
)
from wosutil.tool.utc_time import (
    clear_cached_utc_times,
    get_cached_utc_time,
    get_seconds_until_utc_hour,
    get_seconds_until_utc_midnight,
    set_cached_utc_time,
    sync_utc_time,
)


class TestParseUtcText(unittest.TestCase):
    """Test cases for the UTC clock OCR text parser."""

    def test_parses_clean_clock(self):
        """A clean 'UTC MM-DD HH:MM:SS' string is parsed."""
        self.assertEqual(parse_utc_text("4 UTC 08-10 11:12:26"), (8, 10, 11, 12, 26))

    def test_parses_clock_without_spaces(self):
        """Tesseract often merges the fields; the parser must handle that."""
        self.assertEqual(parse_utc_text("44UTC08-1011:12:26"), (8, 10, 11, 12, 26))

    def test_parses_single_digit_fields(self):
        """Single-digit months, days and hours are accepted."""
        self.assertEqual(parse_utc_text("UTC 1-2 3:04:05"), (1, 2, 3, 4, 5))

    def test_parses_lowercase_utc(self):
        """The 'UTC' keyword is case insensitive."""
        self.assertEqual(parse_utc_text("utc 08-10 11:12:26"), (8, 10, 11, 12, 26))

    def test_rejects_invalid_hour(self):
        """An hour of 24 or more cannot be a valid UTC clock."""
        self.assertIsNone(parse_utc_text("UTC 08-10 24:12:26"))

    def test_rejects_invalid_month_and_day(self):
        """Months over 12 and days over 31 are rejected."""
        self.assertIsNone(parse_utc_text("UTC 13-10 11:12:26"))
        self.assertIsNone(parse_utc_text("UTC 08-32 11:12:26"))

    def test_rejects_invalid_minute_or_second(self):
        """Minutes and seconds over 59 are rejected."""
        self.assertIsNone(parse_utc_text("UTC 08-10 11:60:26"))
        self.assertIsNone(parse_utc_text("UTC 08-10 11:12:60"))

    def test_rejects_text_without_clock(self):
        """Text without the UTC date/time pattern returns None."""
        self.assertIsNone(parse_utc_text("No clock here"))
        self.assertIsNone(parse_utc_text("08-10"))


class TestUtcCacheAndReschedule(unittest.TestCase):
    """Test cases for the cached clock and its reschedule helpers."""

    def setUp(self):
        """Start from an empty cache."""
        clear_cached_utc_times()

    def tearDown(self):
        """Leave no cached state behind."""
        clear_cached_utc_times()

    def test_cache_set_and_get(self):
        """The cached clock survives a set/get round trip."""
        utc = (8, 10, 11, 12, 26)
        set_cached_utc_time(0, utc)
        self.assertEqual(get_cached_utc_time(0), utc)
        self.assertIsNone(get_cached_utc_time(1))

    def test_clear_removes_instance_entry(self):
        """Storing None removes the cached clock of only that instance."""
        set_cached_utc_time(0, (8, 10, 11, 12, 26))
        set_cached_utc_time(0, None)
        self.assertIsNone(get_cached_utc_time(0))

    def test_seconds_until_midnight(self):
        """At 11:12:26 the wait until 00:00 UTC is 86400 - 11:12:26."""
        set_cached_utc_time(0, (8, 10, 11, 12, 26))
        self.assertEqual(get_seconds_until_utc_midnight(0), 86400 - (11 * 3600 + 12 * 60 + 26))

    def test_seconds_until_midnight_at_exact_midnight(self):
        """At exactly 00:00:00 the task waits a full day."""
        set_cached_utc_time(0, (8, 10, 0, 0, 0))
        self.assertEqual(get_seconds_until_utc_midnight(0), 86400)

    def test_seconds_until_midnight_fallback(self):
        """Without a cached clock the fallback is returned."""
        self.assertEqual(get_seconds_until_utc_midnight(0, fallback=12345.0), 12345.0)
        self.assertIsNone(get_seconds_until_utc_midnight(0))

    def test_seconds_until_utc_hour_later_today(self):
        """At 11:12:26 the wait until 12:00 UTC is 47m 34s."""
        set_cached_utc_time(0, (8, 10, 11, 12, 26))
        self.assertEqual(get_seconds_until_utc_hour(0, 12), 47 * 60 + 34)

    def test_seconds_until_utc_hour_passed_today(self):
        """A target hour already passed today waits until tomorrow."""
        set_cached_utc_time(0, (8, 10, 11, 12, 26))
        self.assertEqual(get_seconds_until_utc_hour(0, 10), 86400 - (1 * 3600 + 12 * 60 + 26))

    def test_seconds_until_utc_hour_fallback(self):
        """Without a cached clock the fallback is returned."""
        self.assertEqual(get_seconds_until_utc_hour(0, 0, fallback=60.0), 60.0)
        self.assertIsNone(get_seconds_until_utc_hour(0, 0))

    def test_seconds_until_midnight_stale_cache_after_midnight(self):
        """A clock read at 23:59:11 crossing midnight still waits for the NEXT midnight.

        This reproduces the bug where a cache from the previous day (e.g. read
        48 minutes ago through 00:00 UTC) rescheduled the tasks to ~49 seconds
        instead of ~23 hours, causing a busy loop.
        """
        with patch("wosutil.tool.utc_time.time.time", side_effect=[1000.0, 1000.0 + 48 * 60]):
            set_cached_utc_time(0, (8, 10, 23, 59, 11))
            self.assertEqual(get_seconds_until_utc_midnight(0), 86400 - (48 * 60 - 49))

    def test_seconds_until_utc_hour_stale_cache_after_midnight(self):
        """A stale cache is extrapolated when the target hour comes after midnight."""
        with patch("wosutil.tool.utc_time.time.time", side_effect=[1000.0, 1000.0 + 48 * 60]):
            set_cached_utc_time(0, (8, 10, 23, 59, 11))
            self.assertEqual(get_seconds_until_utc_hour(0, 1), 3600 - (48 * 60 - 49))


class TestSyncUtcTime(unittest.TestCase):
    """Test cases for the world map UTC clock sync flow."""

    def setUp(self):
        """Start from an empty cache."""
        clear_cached_utc_times()
        self.patchers = [
            patch("wosutil.tool.utc_time.ensure_world_screen"),
            patch("wosutil.tool.utc_time.click_on_coordinates"),
            patch("wosutil.tool.utc_time.read_screen_utc_time"),
            patch("wosutil.tool.utc_time.press_android_back_button"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.ensure_world_screen, self.click_on_coordinates, self.read_screen_utc_time, self.press_back = self.mocks
        self.ensure_world_screen.return_value = True
        self.read_screen_utc_time.return_value = (8, 10, 11, 12, 26)
        self.addCleanup(lambda: [p.stop() for p in self.patchers])
        self.addCleanup(clear_cached_utc_times)

    def test_success_reads_and_caches_clock(self):
        """The schedule panel is opened, read, closed and the clock cached."""
        self.assertTrue(sync_utc_time(0))
        self.click_on_coordinates.assert_called_once_with(98, 24, 0, delay=1.0)
        self.read_screen_utc_time.assert_called_once()
        self.press_back.assert_called_once()
        self.assertEqual(get_cached_utc_time(0), (8, 10, 11, 12, 26))

    def test_failure_when_not_on_world_screen(self):
        """The sync aborts without clicking when the world screen is unreachable."""
        self.ensure_world_screen.return_value = False
        self.assertFalse(sync_utc_time(0))
        self.click_on_coordinates.assert_not_called()
        self.read_screen_utc_time.assert_not_called()
        self.assertIsNone(get_cached_utc_time(0))

    def test_failure_when_clock_not_readable(self):
        """A failed read returns False and leaves the cache empty."""
        self.read_screen_utc_time.return_value = None
        self.assertFalse(sync_utc_time(0))
        self.press_back.assert_called_once()

    def test_failed_read_clears_previous_cache(self):
        """An unreadable clock clears any previously cached value."""
        set_cached_utc_time(0, (8, 9, 22, 0, 0))
        self.read_screen_utc_time.return_value = None
        self.assertFalse(sync_utc_time(0))
        self.assertIsNone(get_cached_utc_time(0))


class TestTasksRescheduleToUtcMidnight(unittest.TestCase):
    """Test that the daily tasks reschedule to 00:00 UTC through the clock helpers."""

    def test_claim_pet_adventure_ally_treasure_returns_utc_midnight(self):
        """The ally treasure task reschedules to the next 00:00 UTC on success."""
        with patch("wosutil.tool.tasks.task_automation.go_pet_adventure", return_value=True), patch("wosutil.tool.tasks.task_automation.click_on_coordinates"), patch(
            "wosutil.tool.tasks.task_automation.press_android_back_button"
        ), patch("wosutil.tool.tasks.task_automation.get_seconds_until_utc_midnight", return_value=43200.0):
            result = claim_pet_adventure_ally_treasure(0)
        self.assertEqual(result, (True, 43200.0))

    def test_claim_vip_daily_rewards_returns_utc_midnight(self):
        """The VIP daily rewards task reschedules to the next 00:00 UTC on success."""
        with patch("wosutil.tool.tasks.task_automation.ensure_city_screen", return_value=True), patch("wosutil.tool.tasks.task_automation.click_on"), patch(
            "wosutil.tool.tasks.task_automation.click_on_coordinates"
        ), patch("wosutil.tool.tasks.task_automation.press_android_back_button"), patch("wosutil.tool.tasks.task_automation.get_seconds_until_utc_midnight", return_value=3600.0):
            result = claim_vip_daily_rewards(0)
        self.assertEqual(result, (True, 3600.0))

    def test_pet_adventure_no_attempts_returns_utc_midnight(self):
        """Exhausted daily chest attempts reschedule to the next 00:00 UTC."""
        with patch("wosutil.tool.tasks.task_automation.press_android_back_button"), patch("wosutil.tool.tasks.task_automation.get_seconds_until_utc_midnight", return_value=7777.0):
            result = _finish_pet_adventure_starts(0, "no_attempts")
        self.assertEqual(result, (True, 7777.0))

    def test_pet_adventure_failed_keeps_default_reschedule(self):
        """A failed chest start keeps the default 5h reschedule."""
        with patch("wosutil.tool.tasks.task_automation.press_android_back_button"):
            result = _finish_pet_adventure_starts(0, "failed")
        self.assertEqual(result, (False, 5 * 60 * 60))

    def test_pet_adventure_done_keeps_default_reschedule(self):
        """A clean start keeps the default 5h reschedule (filling chests)."""
        with patch("wosutil.tool.tasks.task_automation.press_android_back_button"):
            result = _finish_pet_adventure_starts(0, "done")
        self.assertEqual(result, (True, 5 * 60 * 60))


if __name__ == "__main__":
    unittest.main()
