"""Unit tests for pet adventure chest helpers."""

import unittest
from unittest.mock import call, patch

from wosutil.tool.tasks.task_automation import (
    PET_ADVENTURE_CHESTS_DAILY_LIMIT_RESCHEDULE_SECONDS,
    PET_ADVENTURE_CHESTS_RESCHEDULE_SECONDS,
    PET_ADVENTURE_CHESTS_RETRY_SECONDS,
    send_pet_adventure_chests,
)
from wosutil.tool.tasks.task_helpers import (
    PET_ADVENTURE_CHEST_FILLING_TEMPLATES,
    PET_ADVENTURE_CHEST_TEMPLATES,
    PET_ADVENTURE_CHEST_THRESHOLD,
    detect_pet_adventure_chests,
    ensure_pet_adventure_screen,
    merge_pet_adventure_chest_matches,
    open_pet_adventure_chest,
    start_pet_adventure_chest,
    start_pet_adventure_chests,
)


class TestMergePetAdventureChestMatches(unittest.TestCase):
    """Test cases for the chest match merging logic."""

    def test_merges_same_position_into_one_chest(self):
        """Two matches at the same position are counted as a single chest."""
        chests = {}
        merge_pet_adventure_chest_matches(chests, [(100, 200, 90, 80)], 3, "start")
        merge_pet_adventure_chest_matches(chests, [(104, 202, 90, 80)], 3, "start")
        self.assertEqual(len(chests), 1)
        chest = next(iter(chests.values()))
        self.assertEqual(chest["type"], 3)
        self.assertEqual(chest["state"], "start")

    def test_start_state_wins_over_ready_on_tie(self):
        """A chest matched by both start and ready templates is not ready.

        A chest in the ready state only matches the "_ready" template, so when
        the same physical chest also matches its start template the matches tie
        and the start state must win.
        """
        chests = {}
        merge_pet_adventure_chest_matches(chests, [(100, 200, 90, 80)], 3, "start")
        merge_pet_adventure_chest_matches(chests, [(102, 201, 74, 79)], 3, "ready")
        chest = next(iter(chests.values()))
        self.assertEqual(chest["state"], "start")

    def test_start_state_not_downgraded_by_ready(self):
        """A later ready match must not downgrade an existing start match."""
        chests = {}
        merge_pet_adventure_chest_matches(chests, [(100, 200, 90, 80)], 3, "start")
        merge_pet_adventure_chest_matches(chests, [(102, 201, 74, 79)], 3, "ready")
        chest = next(iter(chests.values()))
        self.assertEqual(chest["state"], "start")

    def test_ready_state_wins_when_no_start_match(self):
        """A chest that only matches the ready template stays ready."""
        chests = {}
        merge_pet_adventure_chest_matches(chests, [(100, 200, 74, 79)], 3, "ready")
        chest = next(iter(chests.values()))
        self.assertEqual(chest["state"], "ready")

    def test_merges_overlapping_matches_from_different_buckets(self):
        """A start and a ready match of the same chest in different buckets merge.

        Regression test for the 2026-08-20 pet adventure failure: the same
        normal chest matched both the start (98x80) and the ready (74x79)
        templates with top-left corners far enough to land in different
        (x//10, y//10) buckets, producing a duplicated chest that was wrongly
        treated as ready to open.
        """
        chests = {}
        merge_pet_adventure_chest_matches(chests, [(579, 792, 98, 80)], 2, "start")
        merge_pet_adventure_chest_matches(chests, [(612, 780, 74, 79)], 2, "ready")
        self.assertEqual(len(chests), 1)
        chest = next(iter(chests.values()))
        self.assertEqual(chest["type"], 2)
        self.assertEqual(chest["state"], "start")

    def test_distinct_positions_stay_separate(self):
        """Chests at distant positions are kept as separate entries."""
        chests = {}
        merge_pet_adventure_chest_matches(chests, [(100, 200, 90, 80)], 3, "start")
        merge_pet_adventure_chest_matches(chests, [(500, 700, 90, 80)], 1, "ready")
        self.assertEqual(len(chests), 2)

    def test_filling_template_adds_missing_chest(self):
        """A filling match at a new position is added as a filling chest."""
        chests = {}
        merge_pet_adventure_chest_matches(chests, [(100, 200, 90, 80)], 3, "start")
        merge_pet_adventure_chest_matches(chests, [(500, 700, 90, 80)], 2, "filling")
        chest = next(c for c in chests.values() if c["x"] == 500)
        self.assertEqual(chest["state"], "filling")
        self.assertEqual(chest["type"], 2)


class TestPetAdventureChestConstants(unittest.TestCase):
    """Test cases for the chest template configuration."""

    def test_main_templates_covers_all_chests_and_states(self):
        """There must be a start and a ready template for each chest type."""
        states = {}
        for _name, chest_type, state in PET_ADVENTURE_CHEST_TEMPLATES:
            states[(chest_type, state)] = True
        for chest_type in (1, 2, 3):
            self.assertIn((chest_type, "start"), states)
            self.assertIn((chest_type, "ready"), states)

    def test_filling_templates_covers_all_chests(self):
        """There must be a 'b' (filling) template for each chest type."""
        types = {chest_type for _name, chest_type in PET_ADVENTURE_CHEST_FILLING_TEMPLATES}
        self.assertEqual(types, {1, 2, 3})

    def test_threshold_for_chests_is_0_9(self):
        """Chest templates must match with at least 0.9 confidence."""
        self.assertEqual(PET_ADVENTURE_CHEST_THRESHOLD, 0.9)


class TestDetectPetAdventureChests(unittest.TestCase):
    """Test cases for the chest detection flow."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.take_screenshot"),
            patch("wosutil.tool.tasks.task_helpers.find_multiple_templates"),
            patch("wosutil.tool.tasks.task_helpers.time.sleep"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.take_screenshot, self.find_multiple_templates, self.time_sleep = self.mocks
        self.take_screenshot.side_effect = ["/tmp/shot1.png", "/tmp/shot2.png"]
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_merges_matches_from_both_screenshots(self):
        """Chests found in either screenshot are merged into the detection."""
        self.find_multiple_templates.side_effect = [
            [(100, 200, 90, 80)],  # chest3 in shot1
            [],  # chest3_ready in shot1
            [],  # chest2 in shot1
            [(60, 650, 74, 79)],  # chest2_ready in shot1
            [],  # chest1 in shot1
            [(500, 700, 81, 78)],  # chest1_ready in shot1
            [(102, 202, 90, 80)],  # chest3 in shot2 (same chest, vibrated)
            [],  # ...
            [],  # chest2 in shot2
            [],  # chest2_ready in shot2
            [],  # chest1 in shot2
            [],  # chest1_ready in shot2
        ]
        chests = detect_pet_adventure_chests(0)
        self.assertIsNotNone(chests)
        # chest3 (start), chest2 (ready) and chest1 (ready) merged from both shots
        self.assertEqual(len(chests), 3)

    def test_uses_filling_templates_when_fewer_than_3(self):
        """The 'b' templates fill in the missing chest positions."""
        template_names = []  # record the order of find_multiple_templates calls

        def fake_find(template_path, _screenshot_path, threshold):
            template_names.append(template_path)
            if "chest3" in template_path:
                return [(100, 200, 90, 80)]
            if "chest1" in template_path and "chest1b" not in template_path:
                return [(500, 700, 81, 78)]
            if "chest1b" in template_path:
                return [(300, 450, 90, 80)]
            return []

        self.find_multiple_templates.side_effect = fake_find
        chests = detect_pet_adventure_chests(0)
        self.assertIsNotNone(chests)
        self.assertEqual(len(chests), 3)
        filling = [c for c in chests if c["state"] == "filling"]
        self.assertEqual(len(filling), 1)
        self.assertEqual(filling[0]["type"], 1)
        self.assertTrue(any("pet_adventure_chest1b" in name for name in template_names))

    def test_returns_none_when_no_screenshot(self):
        """Detection returns None if a screenshot cannot be taken."""
        self.take_screenshot.side_effect = ["/tmp/shot1.png", None]
        self.assertIsNone(detect_pet_adventure_chests(0))


class TestStartPetAdventureChest(unittest.TestCase):
    """Test cases for starting a single pet adventure chest."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_helpers.press_android_back_button"),
            patch("wosutil.tool.tasks.task_helpers.click_on_template"),
            patch("wosutil.tool.tasks.task_helpers.ensure_pet_adventure_screen"),
            patch("wosutil.tool.tasks.task_helpers.time.sleep"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.click_on_coordinates, self.press_back, self.click_template, self.ensure, self.time_sleep = self.mocks
        self.ensure.return_value = True
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_starts_chest_when_both_buttons_found(self):
        """The chest is started by clicking chest, select pet and start buttons."""
        self.click_template.side_effect = [True, True]
        result = start_pet_adventure_chest(0, 150, 250)
        self.assertTrue(result)
        self.click_on_coordinates.assert_called_once_with(150, 250, 0, delay=1.0)
        self.click_template.assert_has_calls(
            [
                call("pet_adventure_select_pet_button", 0, delay=1.0),
                call("pet_adventure_start_button", 0, delay=1.5),
            ]
        )

    def test_back_button_pressed_after_starting(self):
        """An Android back press follows the start click to return to the chests."""
        self.click_template.side_effect = [True, True]
        result = start_pet_adventure_chest(0, 150, 250)
        self.assertTrue(result)
        self.press_back.assert_called_once()

    def test_false_when_not_on_pet_adventure_screen(self):
        """Starting is aborted if we are not on the pet adventure screen."""
        self.ensure.return_value = False
        result = start_pet_adventure_chest(0, 150, 250)
        self.assertFalse(result)
        self.click_on_coordinates.assert_not_called()
        self.click_template.assert_not_called()

    def test_false_when_cannot_return_after_start(self):
        """Starting fails if the back press after start does not return to the menu."""
        self.click_template.side_effect = [True, True]
        self.ensure.side_effect = [True, False]
        result = start_pet_adventure_chest(0, 150, 250)
        self.assertFalse(result)

    def test_retries_select_pet_search_before_giving_up(self):
        """The select pet search is retried once before returning already_active."""
        self.click_template.side_effect = [False, True, True]
        result = start_pet_adventure_chest(0, 150, 250)
        self.assertTrue(result)
        self.assertEqual(self.click_template.call_count, 3)
        self.time_sleep.assert_called()

    def test_already_active_when_select_pet_missing(self):
        """Returning already_active when the select pet panel never appears.

        Only one back press is sent so the pet adventure screen is not exited by
        a second blind press; the screen is verified afterwards instead.
        """
        self.click_template.side_effect = [False, False]
        result = start_pet_adventure_chest(0, 150, 250)
        self.assertEqual(result, "already_active")
        self.press_back.assert_called_once()
        self.ensure.assert_called_with(0)

    def test_no_attempts_when_start_button_missing(self):
        """Returning no_attempts when the start button is not found."""
        self.click_template.side_effect = [True, False]
        result = start_pet_adventure_chest(0, 150, 250)
        self.assertEqual(result, "no_attempts")
        self.assertEqual(self.press_back.call_count, 2)


class TestStartPetAdventureChests(unittest.TestCase):
    """Test cases for the multi-chest start helper."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.detect_pet_adventure_chests"),
            patch("wosutil.tool.tasks.task_helpers.start_pet_adventure_chest"),
            patch("wosutil.tool.tasks.task_helpers.time.sleep"),
            patch("wosutil.tool.tasks.task_helpers.ensure_pet_adventure_screen", return_value=True),
            patch("wosutil.tool.tasks.task_helpers.is_game_on_pet_adventure_screen", return_value=True),
            patch("wosutil.tool.tasks.task_helpers.go_pet_adventure", return_value=True),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.detect, self.start_chest, self.time_sleep = self.mocks[:3]
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def _chest(self, x, y, chest_type, state):
        """Build a chest detection dict."""
        return {"x": x, "y": y, "w": 90, "h": 80, "type": chest_type, "state": state}

    def test_starts_all_startable_chests_prioritizing_chest3(self):
        """All startable chests are started with fresh detections between starts."""
        self.detect.side_effect = [
            [self._chest(100, 200, 3, "start"), self._chest(300, 500, 2, "start"), self._chest(500, 800, 1, "start")],
            [self._chest(100, 200, 3, "filling"), self._chest(300, 500, 2, "start"), self._chest(500, 800, 1, "start")],
            [self._chest(100, 200, 3, "filling"), self._chest(300, 500, 2, "filling"), self._chest(500, 800, 1, "start")],
            [self._chest(100, 200, 3, "filling"), self._chest(300, 500, 2, "filling"), self._chest(500, 800, 1, "filling")],
        ]
        self.start_chest.return_value = True
        result = start_pet_adventure_chests(0)
        self.assertEqual(result, "done")
        centers = [tuple(c.args[1:3]) for c in self.start_chest.call_args_list]
        # chest 3 first, then type 2, then type 1
        self.assertEqual(centers, [(145, 240), (345, 540), (545, 840)])

    def test_skips_already_active_chest_without_retrying_it(self):
        """An already_active chest is skipped and not attempted again."""
        self.detect.side_effect = [
            [self._chest(100, 200, 3, "start"), self._chest(300, 500, 2, "start"), self._chest(500, 800, 1, "start")],
            [self._chest(100, 200, 3, "start"), self._chest(300, 500, 2, "start"), self._chest(500, 800, 1, "start")],
            [self._chest(100, 200, 3, "start"), self._chest(300, 500, 2, "start"), self._chest(500, 800, 1, "start")],
            [self._chest(100, 200, 3, "start"), self._chest(300, 500, 2, "start"), self._chest(500, 800, 1, "start")],
        ]
        self.start_chest.side_effect = ["already_active", True, True]
        result = start_pet_adventure_chests(0)
        self.assertEqual(result, "done")
        centers = [tuple(c.args[1:3]) for c in self.start_chest.call_args_list]
        # each chest attempted exactly once, chest 3 first (skipped), then 2 and 1
        self.assertEqual(centers, [(145, 240), (345, 540), (545, 840)])

    def test_does_not_reattempt_chest_shifted_between_detections(self):
        """A chest that shifted a few pixels between detections is not retried.

        Regression test for the 2026-08-20 failure: after starting a chest the
        same physical chest shifted 1px (579,792 -> 580,791), changing its
        (x//10, y//10) bucket key, so the old logic re-clicked it and found no
        select pet panel. Centers within proximity must count as the same chest.
        """
        self.detect.side_effect = [
            [self._chest(579, 792, 2, "start"), self._chest(100, 200, 1, "start"), self._chest(300, 500, 1, "start")],
            [self._chest(580, 791, 2, "start"), self._chest(100, 200, 1, "start"), self._chest(300, 500, 1, "start")],
            [self._chest(100, 200, 1, "start"), self._chest(300, 500, 1, "start"), self._chest(500, 800, 1, "start")],
            [self._chest(100, 200, 1, "start"), self._chest(300, 500, 1, "start"), self._chest(500, 800, 1, "start")],
            [self._chest(100, 200, 1, "start"), self._chest(300, 500, 1, "start"), self._chest(500, 800, 1, "start")],
        ]
        self.start_chest.return_value = True
        result = start_pet_adventure_chests(0)
        self.assertEqual(result, "done")
        centers = [tuple(c.args[1:3]) for c in self.start_chest.call_args_list]
        self.assertEqual(centers, [(624, 832), (145, 240), (345, 540), (545, 840)])
        self.assertNotIn((625, 831), centers)

    def test_propagates_no_attempts(self):
        """The no_attempts result is returned to the caller."""
        self.detect.side_effect = [[self._chest(100, 200, 3, "start"), self._chest(300, 500, 2, "start"), self._chest(500, 800, 1, "start")]]
        self.start_chest.return_value = "no_attempts"
        result = start_pet_adventure_chests(0)
        self.assertEqual(result, "no_attempts")

    def test_failed_when_no_chests_detected(self):
        """Detection never finding the 3 chests makes the helper return failed."""
        self.detect.return_value = None
        result = start_pet_adventure_chests(0)
        self.assertEqual(result, "failed")


class TestEnsurePetAdventureScreen(unittest.TestCase):
    """Test cases for verifying we are on the pet adventure screen."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.is_game_on_pet_adventure_screen"),
            patch("wosutil.tool.tasks.task_helpers.press_android_back_button"),
            patch("wosutil.tool.tasks.task_helpers.time.sleep"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.is_on_screen, self.press_back, self.time_sleep = self.mocks
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_returns_true_when_already_on_screen(self):
        """No back press is needed when already on the pet adventure screen."""
        self.is_on_screen.return_value = True
        self.assertTrue(ensure_pet_adventure_screen(0))
        self.press_back.assert_not_called()

    def test_presses_back_to_close_panel_then_succeeds(self):
        """A missing screen detection triggers a back press, then succeeds."""
        self.is_on_screen.side_effect = [False, True]
        self.assertTrue(ensure_pet_adventure_screen(0))
        self.press_back.assert_called_once()

    def test_returns_false_when_never_on_screen(self):
        """Returns False when the screen is never detected after retries."""
        self.is_on_screen.return_value = False
        self.assertFalse(ensure_pet_adventure_screen(0))


class TestOpenPetAdventureChest(unittest.TestCase):
    """Test cases for opening a ready pet adventure chest."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_helpers.click_on_coordinates"),
            patch("wosutil.tool.tasks.task_helpers.press_android_back_button"),
            patch("wosutil.tool.tasks.task_helpers.ensure_pet_adventure_screen"),
            patch("wosutil.tool.tasks.task_helpers.time.sleep"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.click_on_coordinates, self.press_back, self.ensure, self.time_sleep = self.mocks
        self.ensure.return_value = True
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_opens_chest_when_on_pet_adventure_screen(self):
        """The ready chest is opened by clicking it and the claim position."""
        result = open_pet_adventure_chest(0, 150, 250)
        self.assertTrue(result)
        calls = [c.args[:3] for c in self.click_on_coordinates.call_args_list]
        self.assertIn((150, 250, 0), calls)
        self.assertIn((371, 810, 0), calls)
        self.assertEqual(self.press_back.call_count, 2)

    def test_returns_false_when_not_on_pet_adventure_screen(self):
        """Opening is aborted if we are not on the pet adventure screen."""
        self.ensure.return_value = False
        result = open_pet_adventure_chest(0, 150, 250)
        self.assertFalse(result)
        self.click_on_coordinates.assert_not_called()


class TestSendPetAdventureChestsReschedule(unittest.TestCase):
    """Test cases for the reschedule logic of the send chests task."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_automation.go_pet_adventure"),
            patch("wosutil.tool.tasks.task_automation.get_seconds_until_utc_midnight"),
            patch("wosutil.tool.tasks.task_automation.is_game_on_pet_adventure_screen"),
        ]
        self.mocks = [p.start() for p in self.patchers]
        self.go_pet_adventure, self.midnight, self.is_on_screen = self.mocks
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_reschedules_to_midnight_when_side_menu_button_missing(self):
        """A missing side menu button means no daily attempts: reschedule to 00:00 UTC."""
        self.go_pet_adventure.return_value = False
        self.midnight.return_value = 12345.0
        result = send_pet_adventure_chests(0)
        self.assertEqual(result, (True, 12345.0))
        self.midnight.assert_called_once_with(0, fallback=PET_ADVENTURE_CHESTS_DAILY_LIMIT_RESCHEDULE_SECONDS)

    def test_uses_daily_limit_fallback_when_no_utc_clock(self):
        """Without a UTC clock the task falls back to the daily limit reschedule."""
        self.go_pet_adventure.return_value = False
        self.midnight.return_value = PET_ADVENTURE_CHESTS_DAILY_LIMIT_RESCHEDULE_SECONDS
        result = send_pet_adventure_chests(0)
        self.assertEqual(result, (True, PET_ADVENTURE_CHESTS_DAILY_LIMIT_RESCHEDULE_SECONDS))

    def test_retries_soon_when_not_on_screen(self):
        """Missing the pet adventure screen after navigating retries in 2h."""
        self.go_pet_adventure.return_value = True
        self.is_on_screen.return_value = False
        result = send_pet_adventure_chests(0)
        self.assertEqual(result, (False, PET_ADVENTURE_CHESTS_RETRY_SECONDS))


class TestSendPetAdventureChestsRetryDetection(unittest.TestCase):
    """Test cases for retrying the chest detection after opening a chest."""

    def setUp(self):
        """Set up shared mocks."""
        self.patchers = [
            patch("wosutil.tool.tasks.task_automation.go_pet_adventure", return_value=True),
            patch("wosutil.tool.tasks.task_automation.is_game_on_pet_adventure_screen", return_value=True),
            patch("wosutil.tool.tasks.task_automation.detect_pet_adventure_chests"),
            patch("wosutil.tool.tasks.task_automation.ensure_pet_adventure_screen"),
            patch("wosutil.tool.tasks.task_automation.start_pet_adventure_chests", return_value="done"),
            patch("wosutil.tool.tasks.task_automation.press_android_back_button"),
            patch("wosutil.tool.tasks.task_automation.time.sleep"),
            patch("wosutil.tool.tasks.task_automation.get_seconds_until_utc_midnight", return_value=None),
        ]
        self.mocks = [p.start() for p in self.patchers]
        (
            self.go_pet_adventure,
            self.is_on_screen,
            self.detect,
            self.ensure,
            self.start_chests,
            self.press_back,
            self.time_sleep,
            self.midnight,
        ) = self.mocks
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def _chest(self, x, y, chest_type, state):
        """Build a chest detection dict."""
        return {"x": x, "y": y, "w": 90, "h": 80, "type": chest_type, "state": state}

    def test_retries_when_fewer_than_3_chests_detected_after_opening(self):
        """A transient <3 detection after opening a chest is retried, not aborted.

        Regression test for the 2026-08-20 failure: after opening a ready chest a
        new one spawns with an animation during which only 2 chests are detected.
        The task must retry until the 3 chests are visible instead of aborting.
        """
        self.detect.side_effect = [
            [self._chest(100, 200, 2, "start"), self._chest(300, 500, 1, "start")],
            [self._chest(100, 200, 2, "start"), self._chest(300, 500, 1, "start"), self._chest(500, 800, 1, "start")],
        ]
        result = send_pet_adventure_chests(0)
        self.assertEqual(result, (True, PET_ADVENTURE_CHESTS_RESCHEDULE_SECONDS))
        self.start_chests.assert_called_once_with(0)

    def test_aborts_after_retries_when_chests_never_appear(self):
        """The task aborts when fewer than 3 chests never become visible."""
        self.detect.return_value = [self._chest(100, 200, 2, "start"), self._chest(300, 500, 1, "start")]
        result = send_pet_adventure_chests(0)
        self.assertEqual(result, (False, PET_ADVENTURE_CHESTS_RETRY_SECONDS))
        self.start_chests.assert_not_called()


if __name__ == "__main__":
    unittest.main()
