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
    _read_time_native,
    clear_template_cache,
    find_multiple_templates,
    find_template_center_on_screen,
    find_template_on_screen,
    find_text_center_on_screen,
    find_text_on_image,
    find_text_on_screen,
    get_box_center,
    load_template,
    non_max_suppression,
    read_text_lines_on_image,
    resolve_tesseract_cmd,
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

    def test_find_template_on_screen_success(self):
        """Test successful template finding."""
        found, position = find_template_on_screen(self.template_path, self.screenshot_path, threshold=0.8)

        self.assertTrue(found)
        self.assertIsNotNone(position)
        if position is not None:  # Type guard for linter
            x, y, w, h = position
            self.assertEqual(w, 10)
            self.assertEqual(h, 10)

    def test_find_template_on_screen_not_found(self):
        """Test template not found."""
        # Create screenshot without template (different pattern)
        screenshot = np.ones((50, 50, 3), dtype=np.uint8) * 128
        # Add some random noise to make it different
        screenshot[10:20, 10:20] = np.random.randint(0, 255, (10, 10, 3), dtype=np.uint8)
        cv2.imwrite(self.screenshot_path, screenshot)

        found, position = find_template_on_screen(self.template_path, self.screenshot_path, threshold=0.9)

        self.assertFalse(found)
        self.assertIsNone(position)

    def test_find_template_on_screen_with_roi(self):
        """Test template finding with ROI."""
        found, position = find_template_on_screen(
            self.template_path,
            self.screenshot_path,
            threshold=0.8,
            roi=(15, 15, 20, 20),  # ROI that includes the template
        )

        self.assertTrue(found)
        self.assertIsNotNone(position)

    def test_find_template_on_screen_roi_outside(self):
        """Test template finding with ROI outside template location."""
        found, position = find_template_on_screen(
            self.template_path,
            self.screenshot_path,
            threshold=0.8,
            roi=(0, 0, 15, 15),  # ROI that doesn't include the template at (20,20)
        )

        self.assertFalse(found)
        self.assertIsNone(position)

    def test_get_box_center(self):
        """Test computing the center of a box."""
        self.assertEqual(get_box_center((20, 30, 10, 20)), (25, 40))
        self.assertEqual(get_box_center((0, 0, 1, 1)), (0, 0))

    def test_find_template_center_on_screen(self):
        """Test template finding returning the center point."""
        found, center = find_template_center_on_screen(self.template_path, self.screenshot_path, threshold=0.8)

        self.assertTrue(found)
        self.assertIsNotNone(center)
        if center is not None:  # Type guard for linter
            # Template is at (20, 20) with size 10x10 -> center (25, 25)
            self.assertEqual(center, (25, 25))

    def test_find_template_center_on_screen_not_found(self):
        """Test center search when the template is not present."""
        screenshot = np.ones((50, 50, 3), dtype=np.uint8) * 128
        screenshot[10:20, 10:20] = np.random.randint(0, 255, (10, 10, 3), dtype=np.uint8)
        cv2.imwrite(self.screenshot_path, screenshot)

        found, center = find_template_center_on_screen(self.template_path, self.screenshot_path, threshold=0.9)

        self.assertFalse(found)
        self.assertIsNone(center)

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
        filtered_boxes = non_max_suppression(boxes, overlapThresh=0.5)

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
        found, position = find_template_on_screen("nonexistent_template.png", "nonexistent_screenshot.png")

        self.assertFalse(found)
        self.assertIsNone(position)

    @patch("cv2.imread")
    def test_find_template_cv2_error(self, mock_imread):
        """Test template finding with CV2 error."""
        mock_imread.return_value = None

        found, position = find_template_on_screen(self.template_path, self.screenshot_path)

        self.assertFalse(found)
        self.assertIsNone(position)


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


class TestNativeTimerOcr(unittest.TestCase):
    """Test the Tesseract-free digit recognizer used as a fallback."""

    def render_timer(self, text, font_path=r"C:\Windows\Fonts\arialbd.ttf", size=30, pad=6):
        """Render a timer string as a white-on-black image using a TrueType font."""
        from PIL import Image, ImageDraw, ImageFont

        if not os.path.exists(font_path):
            self.skipTest(f"Font not available: {font_path}")
        font = ImageFont.truetype(font_path, size)
        img = Image.fromarray(np.zeros((60, 300), dtype=np.uint8))
        draw = ImageDraw.Draw(img)
        draw.text((pad, pad), text, fill=255, font=font)
        bbox = img.getbbox()
        return img.crop(bbox)

    def assert_reads(self, text):
        """Assert the native recognizer reads the rendered timer correctly."""
        img = self.render_timer(text)
        h, m, s = text.split(":")
        expected = int(h) * 3600 + int(m) * 60 + int(s)
        result = _read_time_native(img)
        self.assertEqual(result, expected, f"native OCR misread {text}")

    def test_native_reads_rendered_timers(self):
        """The native recognizer must read font-rendered timers without Tesseract."""
        for text in ("12:34:56", "00:00:05", "99:59:59", "4:00:00", "08:02:23"):
            self.assert_reads(text)


class TestFirstNonZeroDigit(unittest.TestCase):
    """Test the leftmost non-zero digit locator used to click on text."""

    def render_digits(self, text, font_path=r"C:\Windows\Fonts\arialbd.ttf", size=30, pad=6):
        """Render a digit string as a white-on-black image using a TrueType font."""
        from PIL import Image, ImageDraw, ImageFont

        if not os.path.exists(font_path):
            self.skipTest(f"Font not available: {font_path}")
        font = ImageFont.truetype(font_path, size)
        img = Image.fromarray(np.zeros((60, 300), dtype=np.uint8))
        draw = ImageDraw.Draw(img)
        draw.text((pad, pad), text, fill=255, font=font)
        bbox = img.getbbox()
        return img.crop(bbox)

    def test_finds_leftmost_non_zero_digit(self):
        """'05' must locate the '5' on the right half of the image."""
        img = self.render_digits("05")
        pos = _find_first_non_zero_digit_on_image(img)
        self.assertIsNotNone(pos)
        if pos:
            cx, _ = pos
            self.assertGreater(cx, img.width // 2, "the '5' is the first non-zero digit and sits on the right")

    def test_finds_digit_on_left_when_first(self):
        """'50' must locate the '5' on the left half of the image."""
        img = self.render_digits("50")
        pos = _find_first_non_zero_digit_on_image(img)
        self.assertIsNotNone(pos)
        if pos:
            cx, _ = pos
            self.assertLess(cx, img.width // 2, "the '5' is the first non-zero digit and sits on the left")

    def test_returns_none_when_all_zeros(self):
        """An image with only zeros must return None."""
        img = self.render_digits("000")
        self.assertIsNone(_find_first_non_zero_digit_on_image(img))

    def test_returns_none_on_blank_image(self):
        """A blank image must return None."""
        from PIL import Image as PILImage

        img = PILImage.fromarray(np.zeros((40, 80), dtype=np.uint8))
        self.assertIsNone(_find_first_non_zero_digit_on_image(img))


class TestTextOcr(unittest.TestCase):
    """Test the OCR-based text search used for the side menu."""

    @classmethod
    def setUpClass(cls):
        """Load the side menu sample screenshots, skipping when Tesseract is missing."""
        if not os.path.exists(resolve_tesseract_cmd()):
            raise unittest.SkipTest("Tesseract is not installed.")
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        with Image.open(os.path.join(data_dir, "sidemenu_example.png")) as sample:
            cls.image = sample.copy()
        with Image.open(os.path.join(data_dir, "sidemenu_infantry_example.png")) as sample:
            cls.city_tab_image = sample.copy()

    def test_finds_side_menu_entries(self):
        """Every relevant side menu label must be found with a plausible box."""
        targets = ["City", "Wilderness", "Daily", "Arena", "Tundra Trek", "Trek Supplies", "Pet Adventure", "Land of Heroes"]
        for target in targets:
            with self.subTest(target=target):
                found, box = find_text_on_image(self.image, target)
                self.assertTrue(found, f"'{target}' should be found in the side menu sample")
                self.assertIsNotNone(box)
                if box is not None:  # Type guard for linter
                    x, y, w, h = box
                    self.assertGreater(w, 0)
                    self.assertGreater(h, 0)
                    self.assertTrue(0 <= x < self.image.width and 0 <= y < self.image.height)

    def test_finds_city_tab_entries(self):
        """The City tab capture labels must be found despite the selected tab highlight."""
        targets = ["City", "Training", "Infantry", "Lancer", "Marksman", "Tech Research", "War Academy Research", "Icefire Hunter"]
        for target in targets:
            with self.subTest(target=target):
                found, box = find_text_on_image(self.city_tab_image, target)
                self.assertTrue(found, f"'{target}' should be found in the City tab sample")
                self.assertIsNotNone(box)

    def test_text_not_found(self):
        """A label that is not on the screen must not be found."""
        found, box = find_text_on_image(self.image, "Nonexistent Entry")
        self.assertFalse(found)
        self.assertIsNone(box)

    def test_read_text_lines(self):
        """The line reader must return the side menu entries."""
        lines = read_text_lines_on_image(self.image.crop((0, 230, 450, 940)))
        self.assertGreater(len(lines), 5)
        joined = " ".join(text for text, _ in lines)
        self.assertIn("Tundra Trek", joined)
        self.assertIn("Land of Heroes", joined)

    def test_find_text_on_screen_with_roi(self):
        """Boxes returned with an ROI must be in full-screen coordinates."""
        with tempfile.TemporaryDirectory() as temp_dir:
            screenshot_path = os.path.join(temp_dir, "shot.png")
            self.image.save(screenshot_path)
            roi = (0, 230, 450, 710)
            found, box = find_text_on_screen(screenshot_path, "City", roi=roi)
            self.assertTrue(found)
            self.assertIsNotNone(box)
            if box is not None:  # Type guard for linter
                self.assertTrue(roi[1] <= box[1] < roi[1] + roi[3])
                found_center, center = find_text_center_on_screen(screenshot_path, "City", roi=roi)
                self.assertTrue(found_center)
                if center is not None:  # Type guard for linter
                    self.assertEqual(center, get_box_center(box))

    def test_find_text_on_screen_saves_debug_images_on_failure(self):
        """A missing text saves the OCR debug captures when a label and instance are given."""
        with tempfile.TemporaryDirectory() as temp_dir:
            screenshot_path = os.path.join(temp_dir, "shot.png")
            self.image.save(screenshot_path)
            with patch("wosutil.emulator.image_utils._save_ocr_debug_images") as save_debug:
                found, _ = find_text_on_screen(screenshot_path, "Nonexistent Entry", instance_index=1, debug_label="debug_text")
                self.assertFalse(found)
                save_debug.assert_called_once()
                args = save_debug.call_args.args
                self.assertEqual(args[:2], ("debug_text", 1))
                self.assertIsNotNone(args[2])
                self.assertIsNotNone(args[3])

    def test_find_text_on_screen_skips_debug_without_instance(self):
        """Without an instance index no debug captures are saved."""
        with tempfile.TemporaryDirectory() as temp_dir:
            screenshot_path = os.path.join(temp_dir, "shot.png")
            self.image.save(screenshot_path)
            with patch("wosutil.emulator.image_utils._save_ocr_debug_images") as save_debug:
                found, _ = find_text_on_screen(screenshot_path, "Nonexistent Entry", debug_label="debug_text")
                self.assertFalse(found)
                save_debug.assert_not_called()


if __name__ == "__main__":
    unittest.main()
