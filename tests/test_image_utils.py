"""Unit tests for image utility functions."""

import os
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from wosutil.emulator.image_utils import (
    _TIME_RE,
    _find_first_non_zero_digit_on_image,
    _parse_timer_text,
    _preprocess_timer_red_text,
    clear_template_cache,
    find_all_text_on_image,
    find_multiple_templates,
    find_template_center_on_screen,
    find_text_on_image,
    get_box_center,
    load_template,
    non_max_suppression,
    read_screen_time,
)


class TestImageUtils(unittest.TestCase):
    """Test cases for image utility functions."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()

        # Create test images
        self.template_path = os.path.join(self.temp_dir, "template.png")
        self.screenshot_path = os.path.join(self.temp_dir, "screenshot.png")

        # Create a simple test image (10x10 white square with black border)
        test_image = np.ones((10, 10, 3), dtype=np.uint8) * 255
        test_image[0, :] = [0, 0, 0]  # Black top border
        test_image[-1, :] = [0, 0, 0]  # Black bottom border
        test_image[:, 0] = [0, 0, 0]  # Black left border
        test_image[:, -1] = [0, 0, 0]  # Black right border
        cv2.imwrite(self.template_path, test_image)

        # Create a larger screenshot with the template in it
        screenshot = np.ones((50, 50, 3), dtype=np.uint8) * 128
        # Place template at position (20, 20)
        screenshot[20:30, 20:30] = test_image
        cv2.imwrite(self.screenshot_path, screenshot)

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)
        clear_template_cache()

    def test_load_template_success(self):
        """Test successful template loading."""
        template = load_template(self.template_path)
        self.assertIsNotNone(template)
        if template is not None:  # Type guard for linter
            self.assertEqual(template.shape, (10, 10, 3))

    def test_load_template_caching(self):
        """Test template caching functionality."""
        # Load template twice
        template1 = load_template(self.template_path)
        template2 = load_template(self.template_path)

        # Both should be the same object (cached)
        self.assertIsNotNone(template1)
        self.assertIsNotNone(template2)
        if template1 is not None and template2 is not None:  # Type guard for linter
            self.assertIs(template1, template2)

    def test_load_template_nonexistent(self):
        """Test loading non-existent template."""
        template = load_template("nonexistent.png")
        self.assertIsNone(template)

    def test_clear_template_cache(self):
        """Test clearing template cache."""
        # Load a template
        template1 = load_template(self.template_path)

        # Clear cache
        clear_template_cache()

        # Load again (should be different object)
        template2 = load_template(self.template_path)
        self.assertIsNot(template1, template2)

    def test_find_template_center_success(self):
        """Color and gray-scale matching return the same center."""
        for grayscale in (False, True):
            with self.subTest(grayscale=grayscale):
                found, center = find_template_center_on_screen(self.template_path, self.screenshot_path, threshold=0.8, grayscale=grayscale)

                self.assertTrue(found)
                # Template is at (20, 20) with size 10x10 -> center (25, 25)
                self.assertEqual(center, (25, 25))

    def _read_timer(self, side_effect=None, return_value=None, max_seconds=None):
        """Call read_screen_time with the screenshot and OCR backend mocked."""
        with (
            patch("wosutil.emulator.emulator_manager.take_screenshot", return_value=self.screenshot_path),
            patch("wosutil.emulator.emulator_manager.delete_temp_screenshot"),
            patch(
                "wosutil.emulator.image_utils.ocr.read_line",
                side_effect=side_effect,
                return_value=return_value,
            ) as read_line,
        ):
            result = read_screen_time(0, max_seconds=max_seconds)
        return result, read_line

    def test_read_screen_time_reads_timer(self):
        """A clean timer read by the OCR backend is converted to seconds."""
        result, read_line = self._read_timer(return_value=("12:24:29", 0.99))

        self.assertEqual(result, 12 * 3600 + 24 * 60 + 29)
        self.assertEqual(read_line.call_count, 1)

    def test_read_screen_time_adds_days_to_timer(self):
        """A timer with a day prefix (e.g. 1d 06:29:08) adds 24h per day."""
        result, _read_line = self._read_timer(return_value=("1d 06:29:08", 0.99))

        self.assertEqual(result, 1 * 86400 + 6 * 3600 + 29 * 60 + 8)

    def test_read_screen_time_normalizes_ocr_letter_confusions(self):
        """Common OCR letter/digit confusions are corrected before parsing."""
        result, _read_line = self._read_timer(return_value=("O6:3I:I6", 0.99))

        self.assertEqual(result, 6 * 3600 + 31 * 60 + 16)

    def test_read_screen_time_retries_with_alternate_preprocessing(self):
        """A timer the raw crop cannot parse is retried with other passes."""
        result, read_line = self._read_timer(side_effect=[("garbage", 0.99), ("06:31:16", 0.99)])

        self.assertEqual(result, 6 * 3600 + 31 * 60 + 16)
        self.assertEqual(read_line.call_count, 2)

    def test_read_screen_time_retry_keeps_day_prefix(self):
        """An alternate pass with a day prefix still adds 24h per day."""
        result, read_line = self._read_timer(side_effect=[("", 0.0), ("1d 06:29:08", 0.99)])

        self.assertEqual(result, 1 * 86400 + 6 * 3600 + 29 * 60 + 8)
        self.assertEqual(read_line.call_count, 2)

    def test_read_screen_time_does_not_retry_when_raw_text_succeeds(self):
        """Alternate preprocessing only runs when the first pass fails."""
        result, read_line = self._read_timer(return_value=("12:24:29", 0.99))

        self.assertEqual(result, 12 * 3600 + 24 * 60 + 29)
        self.assertEqual(read_line.call_count, 1)

    def test_read_screen_time_rejects_low_confidence_reads(self):
        """A low-confidence OCR result is not accepted as a timer."""
        result, _read_line = self._read_timer(return_value=("12:24:29", 0.1))

        self.assertIsNone(result)

    def test_read_screen_time_rejects_implausible_max_seconds(self):
        """A parsed timer above max_seconds is treated as an OCR error."""
        result, _read_line = self._read_timer(return_value=("01:00:00", 0.99), max_seconds=60)

        self.assertIsNone(result)

    def test_preprocess_timer_red_text_isolates_red_digits(self):
        """The red-text mask keeps only saturated red (timer digits) pixels."""
        img = Image.fromarray(
            cv2.cvtColor(
                np.array(Image.merge("RGB", (Image.new("L", (40, 20), 120), Image.new("L", (40, 20), 40), Image.new("L", (40, 20), 120)))),
                cv2.COLOR_RGB2BGR,
            )
        )
        red = np.zeros((20, 40, 3), dtype=np.uint8)
        red[5:15, 5:15] = (200, 20, 20)  # red square (timer digits)
        red_img = Image.fromarray(red)
        mask = np.array(_preprocess_timer_red_text(red_img))
        self.assertEqual(mask.shape, (40, 80))
        self.assertGreater((mask > 0).sum(), 0)
        # A non-red image must yield an empty mask.
        mask_gray = np.array(_preprocess_timer_red_text(img))
        self.assertEqual(int((mask_gray > 0).sum()), 0)

    def test_find_template_center_not_found(self):
        """A missing template is not found in either mode."""
        # Create screenshot without template (different pattern)
        screenshot = np.ones((50, 50, 3), dtype=np.uint8) * 128
        # Add some random noise to make it different
        screenshot[10:20, 10:20] = np.random.randint(0, 255, (10, 10, 3), dtype=np.uint8)
        cv2.imwrite(self.screenshot_path, screenshot)

        for grayscale in (False, True):
            with self.subTest(grayscale=grayscale):
                found, center = find_template_center_on_screen(self.template_path, self.screenshot_path, threshold=0.9, grayscale=grayscale)

                self.assertFalse(found)
                self.assertIsNone(center)

    def test_find_template_center_rejects_desaturated_match(self):
        """A gray (disabled) copy is rejected when min_saturation is set."""
        template = np.zeros((20, 20, 3), dtype=np.uint8)
        template[:, :10] = (40, 180, 60)  # green (BGR)
        template[:, 10:] = (255, 255, 255)  # white label
        cv2.imwrite(self.template_path, template)

        gray_template = cv2.cvtColor(cv2.cvtColor(template, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        screenshot = np.full((60, 60, 3), 128, dtype=np.uint8)
        screenshot[20:40, 20:40] = gray_template
        cv2.imwrite(self.screenshot_path, screenshot)

        found, center = find_template_center_on_screen(self.template_path, self.screenshot_path, threshold=0.8)
        self.assertTrue(found)
        self.assertEqual(center, (30, 30))

        found, center = find_template_center_on_screen(self.template_path, self.screenshot_path, threshold=0.8, min_saturation=60)
        self.assertFalse(found)
        self.assertIsNone(center)

        screenshot[20:40, 20:40] = template
        cv2.imwrite(self.screenshot_path, screenshot)
        found, center = find_template_center_on_screen(self.template_path, self.screenshot_path, threshold=0.8, min_saturation=60)
        self.assertTrue(found)
        self.assertEqual(center, (30, 30))

    def test_find_template_center_with_roi(self):
        """Template finding with ROI returns the full-screen center."""
        for grayscale in (False, True):
            with self.subTest(grayscale=grayscale):
                found, center = find_template_center_on_screen(
                    self.template_path,
                    self.screenshot_path,
                    threshold=0.8,
                    roi=(15, 15, 20, 20),  # ROI that includes the template
                    grayscale=grayscale,
                )

                self.assertTrue(found)
                self.assertEqual(center, (25, 25))

    def test_find_template_center_roi_outside(self):
        """A ROI outside the template location finds nothing."""
        found, center = find_template_center_on_screen(
            self.template_path,
            self.screenshot_path,
            threshold=0.8,
            roi=(0, 0, 15, 15),  # ROI that doesn't include the template at (20,20)
        )

        self.assertFalse(found)
        self.assertIsNone(center)

    def test_get_box_center(self):
        """Test computing the center of a box."""
        self.assertEqual(get_box_center((20, 30, 10, 20)), (25, 40))
        self.assertEqual(get_box_center((0, 0, 1, 1)), (0, 0))

    def test_find_multiple_templates(self):
        """Test finding multiple template instances."""
        # Create screenshot with multiple templates
        screenshot = np.ones((100, 100, 3), dtype=np.uint8) * 128
        template = cv2.imread(self.template_path)

        # Place templates at different positions with some spacing
        screenshot[10:20, 10:20] = template
        screenshot[50:60, 50:60] = template
        cv2.imwrite(self.screenshot_path, screenshot)

        matches = find_multiple_templates(self.template_path, self.screenshot_path, threshold=0.8)

        # Should find exactly 2 instances
        self.assertEqual(len(matches), 2)

        # Check that the matches are at the expected positions
        positions = [(match[0], match[1]) for match in matches]
        expected_positions = [(10, 10), (50, 50)]

        for expected_pos in expected_positions:
            found = False
            for pos in positions:
                if abs(pos[0] - expected_pos[0]) <= 2 and abs(pos[1] - expected_pos[1]) <= 2:
                    found = True
                    break
            self.assertTrue(found, f"Expected position {expected_pos} not found in {positions}")

    def test_find_multiple_templates_with_overlap(self):
        """Test NMS reduces overlapping detections from template matching."""
        # Create screenshot with two well-separated template instances
        screenshot = np.ones((100, 100, 3), dtype=np.uint8) * 128
        template = cv2.imread(self.template_path)

        # Place two templates far apart
        screenshot[10:20, 10:20] = template
        screenshot[70:80, 70:80] = template
        cv2.imwrite(self.screenshot_path, screenshot)

        # With any NMS threshold, should find both distinct instances
        matches = find_multiple_templates(self.template_path, self.screenshot_path, threshold=0.8, nms_threshold=0.5)

        self.assertEqual(len(matches), 2)

        # Verify positions are approximately correct
        positions = [(match[0], match[1]) for match in matches]
        self.assertTrue(any(abs(p[0] - 10) <= 2 and abs(p[1] - 10) <= 2 for p in positions), f"Expected match near (10,10), got {positions}")
        self.assertTrue(any(abs(p[0] - 70) <= 2 and abs(p[1] - 70) <= 2 for p in positions), f"Expected match near (70,70), got {positions}")

    def test_non_max_suppression(self):
        """Test non-maximum suppression function directly."""
        # Create overlapping boxes
        boxes = [
            (10, 10, 20, 20),  # Base box
            (12, 12, 20, 20),  # Overlapping with first
            (15, 15, 20, 20),  # Overlapping with first
            (50, 50, 20, 20),  # Non-overlapping
            (55, 55, 20, 20),  # Overlapping with fourth
        ]

        # Apply NMS
        filtered_boxes = non_max_suppression(boxes, overlap_thresh=0.5)

        # Should reduce the number of boxes
        self.assertLess(len(filtered_boxes), len(boxes))
        self.assertGreaterEqual(len(filtered_boxes), 2)  # Should keep at least the distinct ones

    def test_non_max_suppression_empty_list(self):
        """Test NMS with empty list."""
        boxes = []
        filtered_boxes = non_max_suppression(boxes)
        self.assertEqual(filtered_boxes, [])

    def test_find_template_invalid_files(self):
        """Test template finding with invalid files."""
        found, center = find_template_center_on_screen("nonexistent_template.png", "nonexistent_screenshot.png")

        self.assertFalse(found)
        self.assertIsNone(center)

    @patch("cv2.imread")
    def test_find_template_cv2_error(self, mock_imread):
        """Test template finding with CV2 error."""
        mock_imread.return_value = None

        found, center = find_template_center_on_screen(self.template_path, self.screenshot_path)

        self.assertFalse(found)
        self.assertIsNone(center)


class TestTemplateConfidence(unittest.TestCase):
    """Template NMS must prefer the strongest overlapping match."""

    def test_nms_prefers_higher_confidence_box(self):
        """A stronger overlapping detection is kept over a weaker one."""
        boxes = [(10, 10, 20, 20), (12, 12, 20, 20)]

        matches = non_max_suppression(boxes, overlap_thresh=0.5, scores=[0.75, 0.95])

        self.assertEqual(matches, [boxes[1]])

    def test_find_multiple_templates_uses_match_confidence(self):
        """The best score survives when several response pixels overlap."""
        screenshot = np.zeros((12, 12, 3), dtype=np.uint8)
        template = np.zeros((10, 10, 3), dtype=np.uint8)
        response = np.array(
            [
                [0.95, 0.90, 0.80],
                [0.85, 0.70, 0.60],
                [0.55, 0.50, 0.40],
            ],
            dtype=np.float32,
        )
        with (
            patch("wosutil.emulator.image_utils.cv2.imread", return_value=screenshot),
            patch("wosutil.emulator.image_utils.load_template", return_value=template),
            patch("wosutil.emulator.image_utils.cv2.matchTemplate", return_value=response),
        ):
            matches = find_multiple_templates("template.png", "screenshot.png", threshold=0.5)

        self.assertEqual(matches, [(0, 0, 10, 10)])


class TestTimeRegex(unittest.TestCase):
    """Test cases for the timer regex used in OCR."""

    def test_matches_valid_timer(self):
        """A clean HH:MM:SS string must match."""
        m = _TIME_RE.search("08:02:23")
        self.assertIsNotNone(m)
        if m:
            self.assertEqual(m.groups(), ("08", "02", "23"))

    def test_rejects_prefix_digit(self):
        """Garbage like '106:02:23' (hours preceded by another digit) must not match."""
        self.assertIsNone(_TIME_RE.search("106:02:23"))

    def test_matches_single_digit_hour(self):
        """A single-digit hour must match."""
        m = _TIME_RE.search(" 4:00:00 ")
        self.assertIsNotNone(m)
        if m:
            self.assertEqual(m.groups(), ("4", "00", "00"))

    def test_no_match_without_colons(self):
        """A timer without colons must not match."""
        self.assertIsNone(_TIME_RE.search("08 02 23"))

    def test_parser_rejects_impossible_minutes_and_seconds(self):
        """OCR values outside the clock ranges are not accepted as timers."""
        self.assertIsNone(_parse_timer_text("08:60:00"))
        self.assertIsNone(_parse_timer_text("08:00:60"))

    def test_parser_keeps_day_prefix_for_valid_timer(self):
        """A valid timer with a day prefix is converted to total seconds."""
        self.assertEqual(_parse_timer_text("1d 06:29:08"), (6, 29, 8, 1 * 86400 + 6 * 3600 + 29 * 60 + 8))


class TestFirstNonZeroDigit(unittest.TestCase):
    """Test the OCR-based leftmost non-zero digit locator used to click on text."""

    @patch("wosutil.emulator.image_utils.ocr.read_chars")
    def test_finds_leftmost_non_zero_digit(self, read_chars):
        """The first digit from 1 to 9 is returned, ignoring zeros and punctuation."""
        image = Image.new("RGB", (100, 20), "black")
        read_chars.return_value = [
            ("0", (0, 0, 10, 10)),
            (",", (10, 0, 5, 10)),
            ("3", (20, 0, 10, 10)),
            ("6", (30, 0, 10, 10)),
        ]

        self.assertEqual(_find_first_non_zero_digit_on_image(image), (25, 5))

    @patch("wosutil.emulator.image_utils.ocr.read_chars")
    def test_returns_none_when_all_zeros(self, read_chars):
        """An image with only zeros must return None."""
        image = Image.new("RGB", (100, 20), "black")
        read_chars.return_value = [("0", (0, 0, 10, 10)), ("0", (20, 0, 10, 10))]

        self.assertIsNone(_find_first_non_zero_digit_on_image(image))

    @patch("wosutil.emulator.image_utils.ocr.read_chars")
    def test_returns_none_on_empty_ocr(self, read_chars):
        """No recognized characters must return None."""
        image = Image.new("RGB", (100, 20), "black")
        read_chars.return_value = []

        self.assertIsNone(_find_first_non_zero_digit_on_image(image))


class TestTextOcr(unittest.TestCase):
    """Test the OCR-based text search used for the side menu."""

    def render_gather_button(self):
        """Render a game-style blue button with a white label on the dialog panel."""
        from PIL import ImageDraw, ImageFont

        font_path = r"C:\Windows\Fonts\arialbd.ttf"
        if not os.path.exists(font_path):
            self.skipTest(f"Font not available: {font_path}")
        img = Image.new("RGB", (300, 160), (239, 249, 255))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([44, 58, 264, 118], radius=18, fill=(120, 150, 190))  # drop shadow
        for i, y in enumerate(range(50, 110)):  # button body with a lighter top gradient
            t = i / 60
            draw.line([(42, y), (258, y)], fill=(int(140 + (79 - 140) * t), int(205 + (165 - 205) * t), int(255 + (252 - 255) * t)))
        draw.rounded_rectangle([42, 50, 258, 110], radius=18, outline=(21, 80, 160), width=2)
        font = ImageFont.truetype(font_path, 26)
        draw.text((150, 80), "Gather", fill=(255, 255, 255), font=font, anchor="mm", stroke_width=3, stroke_fill=(35, 35, 35))
        return img

    def test_reads_white_label_on_blue_button(self):
        """The Gather button label must be found on its blue button."""
        img = self.render_gather_button()
        found, box = find_text_on_image(img, "Gather", last=True)
        self.assertTrue(found, "'Gather' should be readable on a blue button")
        self.assertIsNotNone(box)
        if box is not None:  # Type guard for linter
            self.assertGreater(box[2], 0)
            self.assertGreater(box[3], 0)

    @patch("wosutil.emulator.image_utils._ocr_word_lines")
    def test_finds_multi_word_label_in_one_line(self, word_lines):
        """Consecutive target words in one OCR line are matched as a phrase."""
        image = Image.new("RGB", (100, 40), "black")
        first_box = (10, 10, 40, 10)
        second_box = (55, 10, 30, 10)
        word_lines.return_value = [[("Tundra", first_box), ("Trek", second_box)]]

        found, result = find_text_on_image(image, "Tundra Trek")

        self.assertTrue(found)
        self.assertEqual(result, (10, 10, 75, 10))
        word_lines.assert_called_once_with(image)

    @patch("wosutil.emulator.image_utils._ocr_word_lines")
    def test_last_search_prefers_a_lower_alternative_match(self, word_lines):
        """The lower OCR match wins when the same label appears twice."""
        image = Image.new("RGB", (100, 80), "black")
        upper_box = (10, 5, 30, 10)
        lower_box = (50, 60, 30, 10)
        word_lines.return_value = [[("Search", upper_box)], [("Search", lower_box)]]

        found, result = find_text_on_image(image, "Search", last=True)

        self.assertTrue(found)
        self.assertEqual(result, lower_box)

    @patch("wosutil.emulator.image_utils._ocr_word_lines")
    def test_exact_gather_match_ignores_lower_noise(self, word_lines):
        """The Gather button wins over a lower unrelated spelling."""
        image = Image.new("RGB", (100, 100), "black")
        button_box = (40, 50, 30, 10)
        noise_box = (5, 85, 30, 10)
        word_lines.return_value = [[("Gather", button_box)], [("ashery", noise_box)]]

        found, result = find_text_on_image(image, "Gather", last=True)

        self.assertTrue(found)
        self.assertEqual(result, button_box)

    @patch("wosutil.emulator.image_utils._ocr_word_lines")
    def test_finds_every_exact_word_match(self, word_lines):
        """All the Share buttons are returned and the tab labels are ignored."""
        image = Image.new("RGB", (100, 200), "black")
        first_box = (70, 50, 25, 12)
        second_box = (70, 120, 25, 12)
        word_lines.return_value = [
            [("My", (20, 20, 15, 12)), ("Shares", (40, 20, 40, 12))],
            [("Share", first_box)],
            [("Alliance", (20, 90, 40, 12)), ("Shares", (65, 90, 40, 12))],
            [("Share", second_box)],
        ]

        matches = find_all_text_on_image(image, "Share")

        self.assertEqual(matches, [first_box, second_box])
        word_lines.assert_called_once_with(image)

    @patch("wosutil.emulator.image_utils._ocr_word_lines")
    def test_returns_empty_list_when_no_text_matches(self, word_lines):
        """An image without the target text returns no boxes."""
        word_lines.return_value = [[("View", (10, 10, 30, 10))]]

        matches = find_all_text_on_image(Image.new("RGB", (50, 50), "black"), "Share")

        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
