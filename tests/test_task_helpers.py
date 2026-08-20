"""Unit tests for task helper functions."""

import unittest
from unittest.mock import call, patch

from wosutil.config import (
    CLICK_DELAY,
    INTEL_BEAST_MARCH_SENT_WAIT_SECONDS,
    INTEL_BEAST_MAX_RETRIES,
    INTEL_BEAST_MAX_WAIT_SECONDS,
    SCREEN_CHECK_THRESHOLD,
)
from wosutil.tool.tasks.task_helpers import (
    KILL_BEAST_MARCH_POSITIONS,
    KILL_BEAST_MARCH_SCROLL_END,
    KILL_BEAST_MARCH_SCROLL_START,
    WORLD_MAP_SEARCH_SCROLL_DURATION_MS,
    WORLD_MAP_SEARCH_SCROLL_END,
    WORLD_MAP_SEARCH_SCROLL_START,
    _click_template_repeatedly,
    click_first_found_template,
    click_on_template,
    click_on_text,
    ensure_hero_recruit_screen,
    gather_tile,
    go_hero_recruit_screen,
    go_pet_adventure,
    go_sidemenu_city,
    go_sidemenu_daily,
    go_tundra_trek,
    go_worldmap_search,
    is_game_on_hero_recruit_screen,
    is_game_on_screen,
    kill_beast,
    kill_intel_beast,
)


class TestKillBeast(unittest.TestCase):
    """Test cases for the kill_beast function."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_helpers.scroll_screen"),
            patch("wosutil.tool.tasks.task_helpers.read_screen_time"),
            patch("wosutil.tool.tasks.task_helpers.is_game_on_screen"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.click_on_coordinates, self.scroll_screen, self.read_screen_time, self.no_troops_left = self.mocks
        self.no_troops_left.return_value = False
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def _run_kill_beast(self, march, timer=100):
        with patch("wosutil.tool.tasks.task_helpers.get_kill_beast_march_assignment", return_value=march):
            self.read_screen_time.return_value = timer
            return kill_beast(0)

    def test_no_assignment_keeps_default_behavior(self):
        """Test that without a march assignment only the two original clicks happen."""
        result = self._run_kill_beast(None, timer=100)

        self.assertEqual(result, min(100 * 2, INTEL_BEAST_MAX_WAIT_SECONDS))
        self.assertEqual(self.click_on_coordinates.call_count, 2)
        self.click_on_coordinates.assert_any_call(360, 620, 0)
        self.click_on_coordinates.assert_any_call(552, 1216, 0)
        self.scroll_screen.assert_not_called()

    def test_no_assignment_no_timer_returns_none(self):
        """Test that without a march assignment, no timer and no send-march screen None is returned."""
        result = self._run_kill_beast(None, timer=None)

        self.assertIsNone(result)

    def test_no_timer_with_no_troops_returns_false(self):
        """Test that when no timer is read and no troops remain the kill is skipped."""
        self.no_troops_left.return_value = True
        result = self._run_kill_beast(None, timer=None)

        self.assertFalse(result)
        self.no_troops_left.assert_called_once_with(0, "no_troops_left")

    def test_no_timer_with_send_march_screen_returns_short_wait(self):
        """Test that with no timer but the send-march screen confirmed the march is sent."""
        self.no_troops_left.side_effect = [False, True]
        result = self._run_kill_beast(None, timer=None)

        self.assertEqual(result, INTEL_BEAST_MARCH_SENT_WAIT_SECONDS)
        self.click_on_coordinates.assert_any_call(552, 1216, 0)
        self.no_troops_left.assert_called_with(0, "send_march_screen", "send_march_screen", threshold=0.90)

    def test_no_timer_without_send_march_screen_does_not_send(self):
        """Test that without timer and without the send-march screen the march is not sent."""
        result = self._run_kill_beast(None, timer=None)

        self.assertIsNone(result)
        calls = [c.args[:2] for c in self.click_on_coordinates.call_args_list]
        self.assertNotIn((552, 1216), calls)

    def test_timer_success_ignores_no_troops_check(self):
        """Test that a successful timer read never checks the no-troops template."""
        result = self._run_kill_beast(None, timer=100)

        self.assertEqual(result, min(100 * 2, INTEL_BEAST_MAX_WAIT_SECONDS))
        self.no_troops_left.assert_not_called()

    def test_assigned_march_clicked_before_timer_read(self):
        """Test that an assigned march is selected right after the first click."""
        for march in range(1, 9):
            with self.subTest(march=march):
                self.click_on_coordinates.reset_mock()
                self._run_kill_beast(march, timer=100)
                calls = [c.args[:2] for c in self.click_on_coordinates.call_args_list]
                self.assertIn(KILL_BEAST_MARCH_POSITIONS[march], calls)
                self.assertEqual(self.click_on_coordinates.call_count, 3)
                self.scroll_screen.assert_not_called()

    def test_march_above_8_scrolls_horizontally_first(self):
        """Test that marches above 8 scroll the formation row horizontally first."""
        for march in range(9, 13):
            with self.subTest(march=march):
                self._run_kill_beast(march, timer=100)
                self.scroll_screen.assert_called_once_with(
                    KILL_BEAST_MARCH_SCROLL_START[0],
                    KILL_BEAST_MARCH_SCROLL_START[1],
                    KILL_BEAST_MARCH_SCROLL_END[0],
                    KILL_BEAST_MARCH_SCROLL_END[1],
                    200,
                    0,
                )
                calls = [c.args[:2] for c in self.click_on_coordinates.call_args_list]
                self.assertIn(KILL_BEAST_MARCH_POSITIONS[march], calls)
                self.assertEqual(self.click_on_coordinates.call_count, 3)
                self.scroll_screen.reset_mock()
                self.click_on_coordinates.reset_mock()


class TestGoWorldMapSearch(unittest.TestCase):
    """Test cases for opening the world-map search panel."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.ensure_world_screen"),
            patch("wosutil.tool.tasks.task_helpers.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_helpers.scroll_screen"),
        ]
        self.ensure_world, self.click_coords, self.scroll_screen = [p.start() for p in self.patchers]
        self.ensure_world.return_value = True
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_opens_search_with_scroll_by_default(self):
        """The default navigation opens the search and scrolls the resources."""
        self.assertTrue(go_worldmap_search(0))

        self.ensure_world.assert_called_once_with(0)
        self.click_coords.assert_called_once_with(44, 878, 0)
        self.scroll_screen.assert_called_once_with(
            WORLD_MAP_SEARCH_SCROLL_START[0],
            WORLD_MAP_SEARCH_SCROLL_START[1],
            WORLD_MAP_SEARCH_SCROLL_END[0],
            WORLD_MAP_SEARCH_SCROLL_END[1],
            WORLD_MAP_SEARCH_SCROLL_DURATION_MS,
            0,
        )

    def test_opens_search_without_scroll_when_disabled(self):
        """The caller can open the search without changing the resource row."""
        self.assertTrue(go_worldmap_search(0, scroll=False))

        self.click_coords.assert_called_once_with(44, 878, 0)
        self.scroll_screen.assert_not_called()

    def test_returns_false_when_world_map_cannot_be_reached(self):
        """Navigation fails without opening the search when the map is unavailable."""
        self.ensure_world.return_value = False

        self.assertFalse(go_worldmap_search(0))
        self.click_coords.assert_not_called()
        self.scroll_screen.assert_not_called()


class TestGatherTile(unittest.TestCase):
    """Test cases for the world-map gathering helper."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.go_worldmap_search"),
            patch("wosutil.tool.tasks.task_helpers.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_helpers.get_roi"),
            patch("wosutil.tool.tasks.task_helpers.click_on_text"),
            patch("wosutil.tool.tasks.task_helpers._click_template_repeatedly"),
            patch("wosutil.tool.tasks.task_helpers.take_screenshot"),
            patch("wosutil.tool.tasks.task_helpers.delete_temp_screenshot"),
            patch("wosutil.tool.tasks.task_helpers.get_template_path"),
            patch("wosutil.tool.tasks.task_helpers.find_multiple_templates"),
            patch("wosutil.tool.tasks.task_helpers.find_text_on_screen"),
            patch("wosutil.tool.tasks.task_helpers.read_screen_time"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        (
            self.go_search,
            self.click_coords,
            self.get_roi,
            self.click_text,
            self.click_template_repeatedly,
            self.take_screenshot,
            self.delete_screenshot,
            self.get_template_path,
            self.find_multiple,
            self.find_text,
            self.read_time,
        ) = self.mocks
        self.go_search.return_value = True
        search_roi = (0, 843, 718, 435)
        self.get_roi.side_effect = lambda name: (
            search_roi
            if name == "worldmap_search"
            else (0, 95, 718, 1008)
            if name == "worldmap"
            else (501, 1138, 118, 29)
        )
        self.click_text.return_value = True
        self.click_template_repeatedly.return_value = True
        self.take_screenshot.return_value = "/tmp/march.png"
        self.get_template_path.return_value = "/tmp/remove_hero.png"
        self.find_multiple.return_value = [(400, 100, 30, 30), (200, 100, 30, 30)]
        self.find_text.return_value = (True, (190, 514, 150, 24))
        self.read_time.side_effect = [120, 30]
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_gathers_selected_resource_and_returns_round_trip_time(self):
        """The helper searches, deploys the march, and returns the full duration."""
        result = gather_tile(0, "wood")

        self.assertEqual(result, 180)
        self.go_search.assert_called_once_with(0)
        self.click_text.assert_any_call("Wood", 0, roi=(0, 843, 718, 435), fuzzy=True)
        self.click_text.assert_any_call("Search", 0, roi=(0, 843, 718, 435), delay=3.0, last=True, fuzzy=True)
        self.click_text.assert_any_call("Gather", 0, roi=(0, 95, 718, 1008), last=True, fuzzy=True)
        self.click_text.assert_any_call("Deploy", 0, last=True)
        self.click_template_repeatedly.assert_called_once_with(
            "gather_tile_increase_level",
            0,
            clicks=10,
            roi=(0, 843, 718, 435),
            gray=False,
            threshold=0.92,
        )
        self.find_text.assert_called_once_with(
            "/tmp/march.png",
            "Gathering Time",
            instance_index=0,
            debug_label="gathering_tile_time",
        )
        self.assertEqual(self.read_time.call_args_list[0].kwargs["ocr_psms"], (6, 7, 8, 11, 12, 13))
        self.assertEqual(self.read_time.call_args_list[1].kwargs["roi"], (501, 1138, 118, 29))
        self.click_coords.assert_any_call(215, 115, 0, delay=1.0)

    def test_meat_uses_the_resource_label_subregion(self):
        """Meat is searched in the narrow label area instead of the full ROI."""
        result = gather_tile(0, "meat")

        self.assertEqual(result, 180)
        self.click_text.assert_any_call("Meat", 0, roi=(0, 843, 718, 435), fuzzy=True)

    def test_invalid_resource_is_rejected_before_navigation(self):
        """An unsupported resource does not interact with the emulator."""
        self.assertIsNone(gather_tile(0, "gold"))
        self.go_search.assert_not_called()
        self.click_coords.assert_not_called()


class TestMarchPositions(unittest.TestCase):
    """Test cases for the march position constants."""

    def test_positions_exist_for_all_marches(self):
        """Test that all 12 marches have a position defined."""
        self.assertEqual(sorted(KILL_BEAST_MARCH_POSITIONS.keys()), list(range(1, 13)))

    def test_scroll_horizontal_at_march_height(self):
        """Test that the scroll is horizontal and at the march row height."""
        self.assertEqual(KILL_BEAST_MARCH_SCROLL_START[1], KILL_BEAST_MARCH_SCROLL_END[1])
        self.assertGreater(KILL_BEAST_MARCH_SCROLL_START[0], KILL_BEAST_MARCH_SCROLL_END[0])


class TestIsGameOnHeroRecruitScreen(unittest.TestCase):
    """Test cases for the hero recruit screen detection."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.take_screenshot"),
            patch("wosutil.tool.tasks.task_helpers.get_template_path"),
            patch("wosutil.tool.tasks.task_helpers.get_roi"),
            patch("wosutil.tool.tasks.task_helpers.find_template_on_screen"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.take_screenshot, self.get_template_path, self.get_roi, self.find_template = self.mocks
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_detects_screen_when_template_found_in_roi(self):
        """The screen is detected when the template matches inside the configured ROI."""
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.get_template_path.return_value = "/tmp/hero_recruit_screen.png"
        self.get_roi.return_value = (583, 164, 134, 414)
        self.find_template.return_value = (True, (600, 200, 46, 48))
        self.assertTrue(is_game_on_hero_recruit_screen(0))
        self.find_template.assert_called_once_with("/tmp/hero_recruit_screen.png", "/tmp/shot.png", threshold=SCREEN_CHECK_THRESHOLD, roi=(583, 164, 134, 414))

    def test_not_detected_when_template_does_not_match(self):
        """The screen is not detected when the template does not match."""
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.get_template_path.return_value = "/tmp/hero_recruit_screen.png"
        self.get_roi.return_value = (583, 164, 134, 414)
        self.find_template.return_value = (False, None)
        self.assertFalse(is_game_on_hero_recruit_screen(0))

    def test_false_without_screenshot(self):
        """The check fails when no screenshot can be taken."""
        self.take_screenshot.return_value = None
        self.assertFalse(is_game_on_hero_recruit_screen(0))
        self.find_template.assert_not_called()

    def test_false_without_template_path_or_roi(self):
        """The check fails when the template path or ROI is not configured."""
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.get_template_path.return_value = None
        self.get_roi.return_value = None
        self.assertFalse(is_game_on_hero_recruit_screen(0))
        self.find_template.assert_not_called()


class TestEnsureHeroRecruitScreen(unittest.TestCase):
    """Test cases for navigating to the hero recruit screen."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.is_game_on_hero_recruit_screen"),
            patch("wosutil.tool.tasks.task_helpers.go_hero_recruit_screen"),
            patch("wosutil.tool.tasks.task_helpers.time.sleep"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.is_on_screen, self.go, self.time_sleep = self.mocks
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_returns_true_when_already_on_screen(self):
        """No navigation is needed when already on the hero recruit screen."""
        self.is_on_screen.return_value = True
        self.assertTrue(ensure_hero_recruit_screen(0))
        self.go.assert_not_called()

    def test_navigates_then_succeeds(self):
        """A missing screen detection triggers navigation, then succeeds."""
        self.is_on_screen.side_effect = [False, True]
        self.assertTrue(ensure_hero_recruit_screen(0))
        self.go.assert_called_once_with(0)

    def test_returns_false_when_never_on_screen(self):
        """Returns False when the screen is never detected after retries."""
        self.is_on_screen.return_value = False
        self.assertFalse(ensure_hero_recruit_screen(0))
        self.assertEqual(self.go.call_count, 3)


class TestGoHeroRecruitScreen(unittest.TestCase):
    """Test cases for the navigation to the hero recruit screen."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.is_game_on_hero_recruit_screen"),
            patch("wosutil.tool.tasks.task_helpers.ensure_city_screen"),
            patch("wosutil.tool.tasks.task_helpers.click_on"),
            patch("wosutil.tool.tasks.task_helpers.click_on_coordinates"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.is_on_screen, self.ensure_city, self.click_on, self.click_coords = self.mocks
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_navigates_from_city_screen(self):
        """Navigation clicks the heroes button and the recruit tab."""
        self.is_on_screen.return_value = False
        self.ensure_city.return_value = True
        self.assertTrue(go_hero_recruit_screen(0))
        self.ensure_city.assert_called_once_with(0)
        self.click_on.assert_called_once_with("heroes", 0)
        self.click_coords.assert_called_once_with(535, 1215, 0, delay=0.7)

    def test_skips_navigation_when_already_on_screen(self):
        """Navigation is skipped when already on the hero recruit screen."""
        self.is_on_screen.return_value = True
        self.assertTrue(go_hero_recruit_screen(0))
        self.ensure_city.assert_not_called()
        self.click_on.assert_not_called()
        self.click_coords.assert_not_called()

    def test_returns_false_when_city_screen_not_reached(self):
        """Navigation fails when the city screen cannot be ensured."""
        self.is_on_screen.return_value = False
        self.ensure_city.return_value = False
        self.assertFalse(go_hero_recruit_screen(0))
        self.click_on.assert_not_called()


class TestGoSidemenuTab(unittest.TestCase):
    """Test cases for the side menu tab selectors."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.go_sidemenu"),
            patch("wosutil.tool.tasks.task_helpers.get_roi"),
            patch("wosutil.tool.tasks.task_helpers.click_on_text"),
            patch("wosutil.tool.tasks.task_helpers.click_on_template"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.go_sidemenu, self.get_roi, self.click_text, self.click_template = self.mocks
        self.go_sidemenu.return_value = True
        self.get_roi.return_value = (0, 173, 484, 759)
        self.click_text.return_value = True
        self.click_template.return_value = True
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_go_sidemenu_city_clicks_city_tab(self):
        """The City tab selector opens the side menu and clicks 'City'."""
        self.assertTrue(go_sidemenu_city(0))
        self.go_sidemenu.assert_called_once_with(0)
        self.click_text.assert_called_once_with("City", 0, roi=(0, 173, 484, 759), delay=1.0)

    def test_go_sidemenu_daily_clicks_daily_tab(self):
        """The Daily tab selector opens the side menu, clicks 'Daily', and unchecks the hide-completed-mission box."""
        self.assertTrue(go_sidemenu_daily(0))
        self.go_sidemenu.assert_called_once_with(0)
        self.click_text.assert_called_once_with("Daily", 0, roi=(0, 173, 484, 759), delay=1.0)
        self.click_template.assert_called_once_with("sidemenu_daily_hide_completed_mission", 0, roi=(0, 173, 484, 759))

    def test_returns_false_when_side_menu_not_opened(self):
        """The selectors fail when the side menu cannot be opened."""
        self.go_sidemenu.return_value = False
        self.assertFalse(go_sidemenu_city(0))
        self.assertFalse(go_sidemenu_daily(0))
        self.click_text.assert_not_called()
        self.click_template.assert_not_called()

    def test_returns_false_when_tab_missing(self):
        """The selectors fail when the tab text is not found."""
        self.click_text.return_value = False
        self.assertFalse(go_sidemenu_city(0))
        self.assertFalse(go_sidemenu_daily(0))
        self.click_template.assert_not_called()


class TestGoTundraTrek(unittest.TestCase):
    """Test cases for navigating to the tundra trek screen."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.go_sidemenu_daily"),
            patch("wosutil.tool.tasks.task_helpers.get_roi"),
            patch("wosutil.tool.tasks.task_helpers.click_on_text"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.go_sidemenu_daily, self.get_roi, self.click_text = self.mocks
        self.go_sidemenu_daily.return_value = True
        self.get_roi.return_value = (0, 173, 484, 759)
        self.click_text.return_value = True
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_navigates_through_daily_tab_and_entry(self):
        """The Daily tab is opened and the Tundra Trek entry is clicked by text."""
        self.assertTrue(go_tundra_trek(0))
        self.go_sidemenu_daily.assert_called_once_with(0)
        self.click_text.assert_called_once_with("Tundra Trek", 0, roi=(0, 173, 484, 759), delay=1.0)

    def test_returns_false_when_daily_tab_not_reached(self):
        """Navigation fails when the Daily tab cannot be reached."""
        self.go_sidemenu_daily.return_value = False
        self.assertFalse(go_tundra_trek(0))
        self.click_text.assert_not_called()

    def test_returns_false_when_entry_missing(self):
        """Navigation fails when the Tundra Trek entry is not found."""
        self.click_text.return_value = False
        self.assertFalse(go_tundra_trek(0))


class TestGoPetAdventure(unittest.TestCase):
    """Test cases for navigating to the pet adventure screen."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.go_sidemenu_daily"),
            patch("wosutil.tool.tasks.task_helpers.get_roi"),
            patch("wosutil.tool.tasks.task_helpers.click_on_text"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.go_sidemenu_daily, self.get_roi, self.click_text = self.mocks
        self.go_sidemenu_daily.return_value = True
        self.get_roi.return_value = (0, 173, 484, 759)
        self.click_text.return_value = True
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_navigates_through_daily_tab_and_pet_entry(self):
        """The Daily tab is opened and the lowest Pet Adventure entry is clicked by text."""
        self.assertTrue(go_pet_adventure(0))
        self.go_sidemenu_daily.assert_called_once_with(0)
        self.click_text.assert_called_once_with("Pet Adventure", 0, roi=(0, 173, 484, 759), delay=1.0, last=True)

    def test_returns_false_when_daily_tab_not_reached(self):
        """Navigation fails when the Daily tab cannot be reached."""
        self.go_sidemenu_daily.return_value = False
        self.assertFalse(go_pet_adventure(0))
        self.click_text.assert_not_called()

    def test_returns_false_when_pet_entry_missing(self):
        """Navigation fails when the Pet Adventure entry is not found."""
        self.click_text.return_value = False
        self.assertFalse(go_pet_adventure(0))


class TestClickOnTemplate(unittest.TestCase):
    """Test cases for the generic find and click template helper."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.take_screenshot"),
            patch("wosutil.tool.tasks.task_helpers.get_template_path"),
            patch("wosutil.tool.tasks.task_helpers.find_template_center_on_screen"),
            patch("wosutil.tool.tasks.task_helpers.find_gray_template_center_on_screen"),
            patch("wosutil.tool.tasks.task_helpers.click_on_coordinates"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.take_screenshot, self.get_template_path, self.find_center, self.find_gray_center, self.click_coords = self.mocks
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.get_template_path.return_value = "/tmp/template.png"
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_clicks_template_center(self):
        """The center of the found template is clicked."""
        self.find_center.return_value = (True, (50, 60))
        self.assertTrue(click_on_template("my_template", 0, delay=1.5))
        self.click_coords.assert_called_once_with(50, 60, 0, delay=1.5)

    def test_returns_false_when_not_found(self):
        """No click happens when the template is not found."""
        self.find_center.return_value = (False, None)
        self.assertFalse(click_on_template("my_template", 0))
        self.click_coords.assert_not_called()

    def test_returns_false_without_screenshot(self):
        """No click happens when a screenshot cannot be taken."""
        self.take_screenshot.return_value = None
        self.assertFalse(click_on_template("my_template", 0))
        self.find_center.assert_not_called()

    def test_reuses_provided_screenshot(self):
        """A provided screenshot is reused instead of capturing a new one."""
        self.find_center.return_value = (True, (50, 60))
        self.assertTrue(click_on_template("my_template", 0, screenshot_path="/tmp/existing.png"))
        self.take_screenshot.assert_not_called()
        self.find_center.assert_called_once()
        args, kwargs = self.find_center.call_args
        self.assertEqual(args[1], "/tmp/existing.png")
        self.click_coords.assert_called_once_with(50, 60, 0, delay=CLICK_DELAY)

    def test_gray_variant_uses_gray_matching(self):
        """The gray flag selects the gray-scale matching function."""
        self.find_gray_center.return_value = (True, (10, 20))
        self.assertTrue(click_on_template("my_template", 0, gray=True, delay=0.5))
        self.click_coords.assert_called_once_with(10, 20, 0, delay=0.5)
        self.find_center.assert_not_called()


class TestClickTemplateRepeatedly(unittest.TestCase):
    """Test the single-search repeated-click template helper."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.take_screenshot"),
            patch("wosutil.tool.tasks.task_helpers.get_template_path"),
            patch("wosutil.tool.tasks.task_helpers.find_template_center_on_screen"),
            patch("wosutil.tool.tasks.task_helpers.find_gray_template_center_on_screen"),
            patch("wosutil.tool.tasks.task_helpers.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_helpers.delete_temp_screenshot"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        (
            self.take_screenshot,
            self.get_template_path,
            self.find_center,
            self.find_gray_center,
            self.click_coords,
            self.delete_screenshot,
        ) = self.mocks
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.get_template_path.return_value = "/tmp/template.png"
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_finds_once_and_clicks_the_same_center_repeatedly(self):
        """Ten clicks reuse one template match and one screenshot."""
        self.find_center.return_value = (True, (50, 60))

        self.assertTrue(_click_template_repeatedly("my_template", 0, clicks=10, roi=(1, 2, 3, 4), delay=0.1))

        self.take_screenshot.assert_called_once_with(0)
        self.find_center.assert_called_once_with(
            "/tmp/template.png",
            "/tmp/shot.png",
            threshold=SCREEN_CHECK_THRESHOLD,
            roi=(1, 2, 3, 4),
        )
        self.find_gray_center.assert_not_called()
        self.assertEqual(self.click_coords.call_count, 10)
        self.assertTrue(all(call_args == call(50, 60, 0, delay=0.1) for call_args in self.click_coords.call_args_list))
        self.delete_screenshot.assert_called_once_with("/tmp/shot.png")


class TestClickFirstFoundTemplate(unittest.TestCase):
    """Test cases for the ordered template fallback helper."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.take_screenshot"),
            patch("wosutil.tool.tasks.task_helpers.click_on_template"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.take_screenshot, self.click_template = self.mocks
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_returns_first_clicked_template(self):
        """The first matching template (with its gray flag) is clicked."""
        self.click_template.side_effect = [False, True]
        result = click_first_found_template(0, [("first", True), "second"], roi=(1, 2, 3, 4), delay=2.0)
        self.assertEqual(result, "second")
        self.click_template.assert_has_calls(
            [
                call("first", 0, roi=(1, 2, 3, 4), delay=2.0, gray=True, screenshot_path="/tmp/shot.png"),
                call("second", 0, roi=(1, 2, 3, 4), delay=2.0, gray=False, screenshot_path="/tmp/shot.png"),
            ]
        )

    def test_takes_single_screenshot_for_all_templates(self):
        """Only one screenshot is captured for the whole template list."""
        self.click_template.side_effect = [False, False, True]
        click_first_found_template(0, ["a", "b", "c"])
        self.take_screenshot.assert_called_once_with(0)

    def test_returns_none_when_nothing_found(self):
        """None is returned when no template matches."""
        self.click_template.return_value = False
        self.assertIsNone(click_first_found_template(0, ["a", "b"]))
        self.take_screenshot.assert_called_once_with(0)

    def test_returns_none_when_screenshot_fails(self):
        """None is returned when the screenshot cannot be captured."""
        self.take_screenshot.return_value = None
        self.assertIsNone(click_first_found_template(0, ["a", "b"]))
        self.click_template.assert_not_called()


class TestClickOnText(unittest.TestCase):
    """Test cases for the text-based find and click helper."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.take_screenshot"),
            patch("wosutil.tool.tasks.task_helpers.delete_temp_screenshot"),
            patch("wosutil.tool.tasks.task_helpers.find_text_center_on_screen"),
            patch("wosutil.tool.tasks.task_helpers.click_on_coordinates"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.take_screenshot, self.delete_temp_screenshot, self.find_center, self.click_coords = self.mocks
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_clicks_text_center(self):
        """The center of the found text is clicked."""
        self.find_center.return_value = (True, (50, 60))
        self.assertTrue(click_on_text("Tundra Trek", 0, delay=1.5))
        self.find_center.assert_called_once_with("/tmp/shot.png", "Tundra Trek", roi=None, instance_index=0, debug_label="click_text_Tundra Trek", last=False)
        self.click_coords.assert_called_once_with(50, 60, 0, delay=1.5)

    def test_clicks_lowest_occurrence_when_last(self):
        """The last flag asks for the lowest occurrence of the text."""
        self.find_center.return_value = (True, (10, 20))
        self.assertTrue(click_on_text("Pet Adventure", 0, last=True))
        self.find_center.assert_called_once_with("/tmp/shot.png", "Pet Adventure", roi=None, instance_index=0, debug_label="click_text_Pet Adventure", last=True)
        self.click_coords.assert_called_once_with(10, 20, 0, delay=CLICK_DELAY)

    def test_returns_false_when_not_found(self):
        """No click happens when the text is not found."""
        self.find_center.return_value = (False, None)
        self.assertFalse(click_on_text("Missing", 0))
        self.click_coords.assert_not_called()

    def test_returns_false_without_screenshot(self):
        """No click happens when a screenshot cannot be taken."""
        self.take_screenshot.return_value = None
        self.assertFalse(click_on_text("City", 0))
        self.find_center.assert_not_called()

    def test_reuses_provided_screenshot(self):
        """A provided screenshot is reused and not deleted."""
        self.find_center.return_value = (True, (10, 20))
        self.assertTrue(click_on_text("City", 0, screenshot_path="/tmp/existing.png"))
        self.take_screenshot.assert_not_called()
        self.delete_temp_screenshot.assert_not_called()
        self.click_coords.assert_called_once_with(10, 20, 0, delay=CLICK_DELAY)

    def test_deletes_owned_screenshot(self):
        """The screenshot captured by the helper is deleted afterwards."""
        self.find_center.return_value = (True, (10, 20))
        self.assertTrue(click_on_text("City", 0))
        self.delete_temp_screenshot.assert_called_once_with("/tmp/shot.png")


class TestIsGameOnScreen(unittest.TestCase):
    """Test cases for the generic screen detection helper."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.take_screenshot"),
            patch("wosutil.tool.tasks.task_helpers.get_template_path"),
            patch("wosutil.tool.tasks.task_helpers.get_roi"),
            patch("wosutil.tool.tasks.task_helpers.find_template_on_screen"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.take_screenshot, self.get_template_path, self.get_roi, self.find_template = self.mocks
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_detects_screen_with_configured_template_and_roi(self):
        """The screen is detected with the given template and ROI."""
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.get_template_path.return_value = "/tmp/intel_screen.png"
        self.get_roi.return_value = (0, 0, 324, 98)
        self.find_template.return_value = (True, (100, 30, 46, 48))
        self.assertTrue(is_game_on_screen(0, "intel_screen", "intel_screen"))
        self.get_template_path.assert_called_once_with("intel_screen")
        self.get_roi.assert_called_once_with("intel_screen")
        self.find_template.assert_called_once_with("/tmp/intel_screen.png", "/tmp/shot.png", threshold=SCREEN_CHECK_THRESHOLD, roi=(0, 0, 324, 98))

    def test_false_when_template_missing(self):
        """The check fails when the template path is not configured."""
        self.take_screenshot.return_value = "/tmp/shot.png"
        self.get_template_path.return_value = None
        self.assertFalse(is_game_on_screen(0, "intel_screen", "intel_screen"))
        self.find_template.assert_not_called()


class TestKillIntelBeast(unittest.TestCase):
    """Test cases for the kill_intel_beast retry loop."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers._click_intel_template"),
            patch("wosutil.tool.tasks.task_helpers.kill_beast"),
            patch("wosutil.tool.tasks.task_helpers.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_helpers.click_on_template"),
            patch("wosutil.tool.tasks.task_helpers.get_roi"),
            patch("wosutil.tool.tasks.task_helpers.press_android_back_button"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        (
            self.click_intel_template,
            self.kill_beast,
            self.click_on_coordinates,
            self.click_on_template,
            self.get_roi,
            self.back_button,
        ) = self.mocks
        self.get_roi.return_value = (589, 673, 129, 431)
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_returns_result_when_beast_found(self):
        """The kill result is returned after returning to the intel screen."""
        self.click_intel_template.return_value = "intel_fcbeast"
        self.kill_beast.return_value = 40

        result = kill_intel_beast(0)

        self.assertEqual(result, 40)
        self.click_intel_template.assert_called_once()
        self.click_on_coordinates.assert_called_with(360, 908, 0)
        self.click_on_template.assert_called_once()
        self.back_button.assert_not_called()

    def test_returns_none_when_no_beast_found(self):
        """No beast found returns None without attacking."""
        self.click_intel_template.return_value = None

        result = kill_intel_beast(0)

        self.assertIsNone(result)
        self.kill_beast.assert_not_called()
        self.click_on_template.assert_not_called()

    def test_retries_when_march_screen_not_confirmed(self):
        """A None kill result retries from the intel and returns the next success."""
        self.click_intel_template.return_value = "intel_fcbeast"
        self.kill_beast.side_effect = [None, 60]

        result = kill_intel_beast(0)

        self.assertEqual(result, 60)
        self.assertEqual(self.kill_beast.call_count, 2)
        self.assertEqual(self.click_intel_template.call_count, 2)
        self.back_button.assert_called_once_with(0)

    def test_gives_up_after_max_retries(self):
        """The attack is skipped after INTEL_BEAST_MAX_RETRIES failed confirmations."""
        self.click_intel_template.return_value = "intel_fcbeast"
        self.kill_beast.return_value = None

        result = kill_intel_beast(0)

        self.assertIsNone(result)
        self.assertEqual(self.kill_beast.call_count, INTEL_BEAST_MAX_RETRIES)
        self.assertEqual(self.click_intel_template.call_count, INTEL_BEAST_MAX_RETRIES)
        self.assertEqual(self.back_button.call_count, INTEL_BEAST_MAX_RETRIES)
        self.click_on_template.assert_not_called()


class TestEnsureCityScreenNotInstalled(unittest.TestCase):
    """The city-screen flow aborts early when the game is not installed."""

    def test_aborts_without_launching_when_game_missing(self):
        """Missing game: clear error and no launch/restart of the emulator."""
        with patch("wosutil.tool.tasks.task_helpers.is_wos_running", return_value=False), patch("wosutil.tool.tasks.task_helpers.is_wos_installed", return_value=False), patch(
            "wosutil.tool.tasks.task_helpers.launch_and_verify_game"
        ) as mock_launch, patch("wosutil.tool.tasks.task_helpers.get_multi_instance_manager") as mock_manager:
            from wosutil.tool.tasks.task_helpers import ensure_city_screen

            result = ensure_city_screen(0)

        self.assertFalse(result)
        mock_launch.assert_not_called()
        mock_manager.return_value.stop_instance.assert_not_called()
        mock_manager.return_value.start_instance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
