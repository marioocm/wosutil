"""Unit tests for the OCR backend helpers."""

import unittest

from wosutil.emulator.ocr import _fallback_chars, _fallback_words, _has_ascii_alnum, _quad_to_box


class TestOcrHelpers(unittest.TestCase):
    """Test the geometry and text filtering helpers of the OCR backend."""

    def test_quad_to_box_converts_a_rotated_quadrilateral(self):
        """A rotated OCR box is converted to its smallest axis-aligned rectangle."""
        quad = [[10, 20], [50, 22], [49, 40], [9, 38]]

        self.assertEqual(_quad_to_box(quad), (9, 20, 41, 20))

    def test_has_ascii_alnum(self):
        """Only text with an ASCII letter or digit is kept."""
        self.assertTrue(_has_ascii_alnum("Bear Hunt"))
        self.assertTrue(_has_ascii_alnum("19:00"))
        self.assertFalse(_has_ascii_alnum("千"))
        self.assertFalse(_has_ascii_alnum("-"))

    def test_fallback_words_divides_the_line_box_proportionally(self):
        """Words without backend boxes are spread over the line by character count."""
        words = _fallback_words("Bear Hunter -", (0, 0, 80, 10))

        self.assertEqual(words, [("Bear", (0, 0, 32, 10)), ("Hunter", (32, 0, 48, 10))])

    def test_fallback_chars_keeps_punctuation_positions(self):
        """Character fallback keeps every non-space character in order."""
        chars = _fallback_chars("1,234", (0, 0, 50, 10))

        self.assertEqual([char for char, _box in chars], ["1", ",", "2", "3", "4"])
        self.assertEqual(chars[0][1], (0, 0, 10, 10))
        self.assertEqual(chars[-1][1], (40, 0, 10, 10))


if __name__ == "__main__":
    unittest.main()
