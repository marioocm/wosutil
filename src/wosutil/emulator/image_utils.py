"""Image processing utilities for template matching and OCR.

Provides functions for finding templates on screen, reading text from screenshots,
and managing template caching.
"""

import os
import re
import time
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image

from wosutil.config import DEBUG_DIR, SCREEN_CHECK_THRESHOLD
from wosutil.emulator import ocr
from wosutil.utils import ensure_directory_exists, log_message

# Template cache to avoid reloading images
_template_cache: Dict[str, np.ndarray] = {}

# Timer OCR settings
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2}):(\d{2})")
_DAY_RE = re.compile(r"(\d{1,2})\s*[dD]")
_DIGIT_CONFUSIONS = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "I": "1",
        "i": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "b": "8",
        "Z": "2",
        "z": "2",
        "G": "6",
        "g": "6",
    }
)

# Red timer text settings: some timers (e.g. pet skills) draw red digits over
# warm-colored artwork, so grayscale cannot separate text from background; the
# digits are isolated with an HSV filter and the OCR pass is retried on them.
_TIMER_RED_HUE_MAX = 12
_TIMER_RED_HUE_MIN = 160
_TIMER_RED_SAT_MIN = 70
_TIMER_RED_VAL_MIN = 100

# UTC clock OCR settings (world map schedule panel: 'UTC MM-DD HH:MM:SS')
_UTC_TIME_RE = re.compile(r"UTC\s*(\d{1,2})-(\d{1,2})\s*(\d{1,2}):(\d{2}):(\d{2})", re.IGNORECASE)


def load_template(template_path: str) -> Optional[np.ndarray]:
    """Load template image with caching for better performance.

    Args:
        template_path (str): Path to the template image file.

    Returns:
        np.ndarray or None: Loaded template image or None if failed.
    """
    if template_path in _template_cache:
        return _template_cache[template_path]

    template = cv2.imread(template_path)
    if template is not None:
        _template_cache[template_path] = template
    return template


def clear_template_cache():
    """Clear the template cache to free memory."""
    _template_cache.clear()


def _load_screenshot_and_template(screenshot_path: str, template_path: str, roi: Optional[Tuple[int, int, int, int]] = None) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Load the screenshot and template for a search, cropping the screenshot to the ROI.

    Each element is None when its own load failed, so callers keep their
    own error handling (logging vs silent skip).

    Args:
        screenshot_path (str): Path to the screenshot image file.
        template_path (str): Path to the template image file.
        roi (tuple, optional): Region of interest as (x, y, w, h) to crop
            the screenshot to.

    Returns:
        tuple: (screenshot, template) images, either None on load failure.
    """
    img = cv2.imread(screenshot_path)
    if img is None:
        return None, None
    template = load_template(template_path)
    if template is None:
        return img, None
    if roi:
        x, y, w, h = roi
        img = img[y : y + h, x : x + w]
    return img, template


def find_multiple_templates(
    template_path: str,
    screenshot_path: str,
    threshold: float = SCREEN_CHECK_THRESHOLD,
    roi: Optional[Tuple[int, int, int, int]] = None,
    nms_threshold: float = 0.5,
) -> list:
    """Find all instances of a template in the screenshot, applying non-maximum suppression to avoid overlapping matches.

    Args:
        template_path (str): Path to the template image file.
        screenshot_path (str): Path to the screenshot image file (should be a temporary file, not deleted by this function).
        threshold (float): Minimum confidence threshold for a match.
        roi (tuple, optional): Region of interest as (x, y, w, h).
        nms_threshold (float): Overlap threshold for non-maximum suppression (default 0.5).

    Returns:
        list: List of tuples (x, y, w, h) for each found instance (after NMS).
    """
    try:
        img_rgb, template = _load_screenshot_and_template(screenshot_path, template_path, roi)
        if img_rgb is None or template is None:
            return []

        res = cv2.matchTemplate(img_rgb, template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(res >= threshold)

        matches = []
        h, w = template.shape[:2]

        for pt in zip(*locations[::-1], strict=True):
            match_x, match_y = int(pt[0]), int(pt[1])
            if roi:
                match_x += roi[0]
                match_y += roi[1]
            score = float(res[int(pt[1]), int(pt[0])])
            matches.append(((match_x, match_y, w, h), score))

        # Apply non-maximum suppression, keeping the strongest match in each
        # overlapping group rather than whichever box happens to be last.
        matches_nms = non_max_suppression(
            [box for box, _score in matches],
            overlap_thresh=nms_threshold,
            scores=[score for _box, score in matches],
        )
        return sorted(matches_nms, key=lambda box: (box[1], box[0]))

    except Exception as e:
        msg = f"Error in multiple template matching: {e}"
        log_message(msg, level="error")
        return []


def non_max_suppression(boxes: List[Tuple[int, int, int, int]], overlap_thresh: float = 0.5, scores: Optional[Sequence[float]] = None) -> List[Tuple[int, int, int, int]]:
    """Applies non-maximum suppression to avoid overlapping boxes.

    Args:
        boxes (list): List of (x, y, w, h) tuples.
        overlap_thresh (float): Overlap threshold for suppression.
        scores (sequence, optional): Match confidence for each box. When
            provided, higher-confidence boxes are kept before lower-confidence
            overlapping boxes.

    Returns:
        list: Filtered list of boxes after NMS.
    """
    if len(boxes) == 0:
        return []
    if scores is not None and len(scores) != len(boxes):
        raise ValueError("scores must contain one value per box")
    boxes_np = np.array(boxes)
    x1 = boxes_np[:, 0]
    y1 = boxes_np[:, 1]
    x2 = x1 + boxes_np[:, 2]
    y2 = y1 + boxes_np[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    idxs = np.argsort(np.asarray(scores) if scores is not None else y2)
    pick = []
    while len(idxs) > 0:
        last = idxs[-1]
        pick.append(last)
        xx1 = np.maximum(x1[last], x1[idxs[:-1]])
        yy1 = np.maximum(y1[last], y1[idxs[:-1]])
        xx2 = np.minimum(x2[last], x2[idxs[:-1]])
        yy2 = np.minimum(y2[last], y2[idxs[:-1]])
        w = np.maximum(0, xx2 - xx1 + 1)
        h = np.maximum(0, yy2 - yy1 + 1)
        overlap = (w * h) / areas[idxs[:-1]]
        idxs = np.delete(idxs, np.concatenate(([len(idxs) - 1], np.where(overlap > overlap_thresh)[0])))
    return [tuple(boxes_np[i]) for i in pick]


def _save_ocr_debug_images(
    label: str,
    instance_index: int,
    original_img: Optional[Image.Image],
    processed_img: Optional[Image.Image],
) -> None:
    """Save the original and processed ROI images to the debug folder for OCR troubleshooting.

    Only runs when debug (verbose) mode is enabled; normal sessions skip the
    captures entirely.

    Args:
        label (str): Label used to name the files.
        instance_index (int): Emulator instance index, included in the file name.
        original_img (Image or None): Original ROI crop, before preprocessing.
        processed_img (Image or None): Image after preprocessing, as fed to
            the OCR engine.
    """
    from wosutil.preferences import get_debug_mode

    if not get_debug_mode():
        return
    ensure_directory_exists(DEBUG_DIR)
    timestamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    if original_img is not None:
        original_img.save(os.path.join(DEBUG_DIR, f"{label}_inst{instance_index}_{timestamp}_original.png"))
    if processed_img is not None:
        processed_img.save(os.path.join(DEBUG_DIR, f"{label}_inst{instance_index}_{timestamp}_processed.png"))
    log_message(
        f"Saved OCR debug captures to {DEBUG_DIR} for label '{label}' (instance {instance_index}) at {timestamp}.",
        level="warning",
    )


def _preprocess_timer_red_text(img: Image.Image) -> Image.Image:
    """Isolate red timer digits as white glyphs on a black background.

    Some game timers (e.g. the pet skills) draw red digits over warm-colored
    artwork; the raw crop can merge them into the background. This retains only
    strongly red pixels (hue near 0/180, saturated enough and bright enough),
    producing a binary image with the digits in white, and
    ``read_screen_time`` retries the OCR on it.

    Args:
        img (PIL.Image): Source ROI crop containing the red timer text.

    Returns:
        PIL.Image: Upscaled binary image with the red digits in white.
    """
    arr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    arr = cv2.resize(arr, (arr.shape[1] * 2, arr.shape[0] * 2), interpolation=cv2.INTER_LANCZOS4)
    hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)
    mask = (((hsv[:, :, 0] <= _TIMER_RED_HUE_MAX) | (hsv[:, :, 0] >= _TIMER_RED_HUE_MIN)) & (hsv[:, :, 1] >= _TIMER_RED_SAT_MIN) & (hsv[:, :, 2] >= _TIMER_RED_VAL_MIN)).astype(np.uint8) * 255
    return Image.fromarray(mask)


def _pil_to_bgr(img: Image.Image) -> np.ndarray:
    """Convert a PIL image to the BGR array expected by the OCR backend."""
    return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)


def _preprocess_gray_text(img: Image.Image) -> Image.Image:
    """Upscale and binarize a timer crop as a low-contrast fallback."""
    gray = img.convert("L")
    gray = gray.resize((gray.width * 2, gray.height * 2), Image.Resampling.LANCZOS)
    _, binary = cv2.threshold(np.array(gray), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return Image.fromarray(binary)


def _parse_timer_text(text: str) -> Optional[Tuple[int, int, int, int]]:
    """Parse a timer only when its hour, minute and second fields are valid."""
    match = _TIME_RE.search(text)
    if not match:
        return None
    hours, minutes, seconds = map(int, match.groups())
    if hours > 99 or minutes > 59 or seconds > 59:
        return None
    total_seconds = hours * 3600 + minutes * 60 + seconds
    day_match = _DAY_RE.search(text)
    if day_match:
        total_seconds += int(day_match.group(1)) * 86400
    return hours, minutes, seconds, total_seconds


def _parse_timer_candidate(text: str) -> Optional[Tuple[int, int, int, int]]:
    """Parse a timer, retrying with common OCR letter/digit confusions corrected."""
    parsed = _parse_timer_text(text)
    if parsed is not None:
        return parsed
    return _parse_timer_text(text.translate(_DIGIT_CONFUSIONS))


def read_screen_time(
    instance_index: int,
    roi: Optional[Tuple[int, int, int, int]] = None,
    debug_label: Optional[str] = None,
    max_seconds: Optional[int] = None,
    screenshot_path: Optional[str] = None,
    quiet: bool = False,
) -> Optional[int]:
    """Read a timer in HH:MM:SS format from an emulator screenshot, returning the time in seconds.

    By default a fresh screenshot is captured; a caller can pass ``screenshot_path``
    to reuse one it already owns so the same capture can be shared with other OCR
    steps. The shared file is never deleted here.

    The ROI is fed to the recognition-only OCR engine, which is much faster and
    more reliable than text detection for tight timer strips. When the raw crop
    cannot be parsed, a red-text mask and a grayscale/Otsu pass are tried before
    giving up.

    On failure (no timer matched, an unexpected error, or a detected value over
    ``max_seconds``) the original ROI image and the last processed image are
    saved to the debug directory so the failure can be reviewed.

    Args:
        instance_index (int): Emulator instance index to take the screenshot from.
        roi (tuple, optional): Region of interest (x, y, w, h) to search for the timer.
        debug_label (str, optional): Label used to name the debug images on failure.
        max_seconds (int, optional): Maximum plausible value in seconds. Reads above
            this are treated as OCR errors (logged and debug captured) and return ``None``.
        screenshot_path (str, optional): Reuse an already taken screenshot instead
            of capturing a new one. The file is not deleted by this function.
        quiet (bool): Log a missing timer at debug level instead of warning. Use
            it for optional probes where an empty ROI is expected.

    Returns:
        int or None: Time in seconds if detected, None if not found or implausible.
    """
    from wosutil.emulator.emulator_manager import delete_temp_screenshot, take_screenshot

    original_img: Optional[Image.Image] = None
    processed_img: Optional[Image.Image] = None
    owned_screenshot = screenshot_path is None

    try:
        if screenshot_path is None:
            screenshot_path = take_screenshot(instance_index)
        if not screenshot_path:
            log_message("Could not take screenshot for timer OCR.", level="error")
            return None
        try:
            with Image.open(screenshot_path) as opened_img:
                img = opened_img.copy()
        finally:
            if owned_screenshot:
                delete_temp_screenshot(screenshot_path)
        if roi:
            x, y, w, h = roi
            img = img.crop((x, y, x + w, y + h))
        original_img = img.copy()

        def candidates():
            yield "raw", original_img
            red_mask = _preprocess_timer_red_text(original_img)
            if np.any(np.array(red_mask)):
                yield "red text", red_mask
            yield "grayscale", _preprocess_gray_text(original_img)

        last_text = ""
        for label, candidate in candidates():
            processed_img = candidate
            text, score = ocr.read_line(_pil_to_bgr(candidate))
            if score < ocr.MIN_SCORE:
                continue
            last_text = text
            parsed = _parse_timer_candidate(text)
            if parsed is None:
                continue
            hours, minutes, seconds, total_seconds = parsed
            if max_seconds is not None and total_seconds > max_seconds:
                log_message(
                    f"Timer read {hours:02}:{minutes:02}:{seconds:02} ({total_seconds}s) exceeds the maximum plausible value of {max_seconds}s, treating it as an invalid timer read.",
                    level="error",
                )
                if debug_label:
                    _save_ocr_debug_images(debug_label, instance_index, original_img, processed_img)
                return None
            suffix = "" if label == "raw" else f" ({label})"
            log_message(f"Detected timer on screen{suffix}: {hours:02}:{minutes:02}:{seconds:02} ({total_seconds} seconds)", level="info")
            return total_seconds

        log_message(f"No timer in HH:MM:SS format detected in OCR text: '{last_text.strip()}'", level="debug" if quiet else "warning")
        if debug_label:
            _save_ocr_debug_images(debug_label, instance_index, original_img, processed_img)
        return None
    except Exception as e:
        log_message(f"Error reading timer from screen: {e}", level="error")
        if debug_label:
            _save_ocr_debug_images(debug_label, instance_index, original_img, processed_img)
        return None


def parse_utc_text(text: str) -> Optional[Tuple[int, int, int, int, int]]:
    """Parse the game clock (date and time in UTC) from OCR text.

    The schedule panel of the world map shows a small icon, the 'UTC' text,
    the date in MM-DD format and the time in HH:MM:SS format; OCR can merge or
    drop the spaces, e.g. 'UTC 08-10 11:12:26' or 'UTC08-1011:12:26'.

    Args:
        text (str): OCR text that should contain the UTC clock.

    Returns:
        tuple or None: (month, day, hour, minute, second) when the text
            contains a plausible date and time, None otherwise.
    """
    match = _UTC_TIME_RE.search(text)
    if not match:
        return None
    month, day, hour, minute, second = map(int, match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return month, day, hour, minute, second


def _parse_utc_candidate(text: str) -> Optional[Tuple[int, int, int, int, int]]:
    """Parse the UTC clock, retrying with common OCR letter/digit confusions corrected."""
    parsed = parse_utc_text(text)
    if parsed is not None:
        return parsed
    return parse_utc_text(text.translate(_DIGIT_CONFUSIONS))


def read_screen_utc_time(
    instance_index: int,
    roi: Optional[Tuple[int, int, int, int]] = None,
    debug_label: Optional[str] = None,
) -> Optional[Tuple[int, int, int, int, int]]:
    """Takes a screenshot and reads the game clock (UTC date and time) from the specified ROI using OCR.

    The schedule panel of the world map shows a small icon, the 'UTC' text,
    the date in MM-DD format and the time in HH:MM:SS format inside the ROI,
    e.g. 'UTC 08-10 11:12:26'. The ROI is read as a single line first; when
    that fails a full detection pass is tried over the same crop.

    On failure the original ROI image and the last processed image are saved to
    the debug directory so the failure can be reviewed.

    Args:
        instance_index (int): Emulator instance index to take the screenshot from.
        roi (tuple, optional): Region of interest (x, y, w, h) with the UTC clock.
        debug_label (str, optional): Label used to name the debug images on failure.

    Returns:
        tuple or None: (month, day, hour, minute, second) if detected, None otherwise.
    """
    from wosutil.emulator.emulator_manager import delete_temp_screenshot, take_screenshot

    original_img: Optional[Image.Image] = None
    processed_img: Optional[Image.Image] = None

    try:
        screenshot_path = take_screenshot(instance_index)
        if not screenshot_path:
            log_message("Could not take screenshot for UTC clock OCR.", level="error")
            return None
        try:
            with Image.open(screenshot_path) as opened_img:
                img = opened_img.copy()
        finally:
            delete_temp_screenshot(screenshot_path)
        if roi:
            x, y, w, h = roi
            img = img.crop((x, y, x + w, y + h))
        original_img = img.copy()

        last_text = ""
        text, score = ocr.read_line(_pil_to_bgr(original_img))
        if score >= ocr.MIN_SCORE:
            last_text = text
            parsed = _parse_utc_candidate(text)
            if parsed is not None:
                month, day, hour, minute, second = parsed
                log_message(
                    f"Detected UTC clock on screen: {month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}",
                    level="info",
                )
                return parsed

        lines = ocr.read_lines(_pil_to_bgr(original_img))
        if lines:
            last_text = " ".join(line_text for line_text, _box in lines)
            parsed = _parse_utc_candidate(last_text)
            if parsed is not None:
                month, day, hour, minute, second = parsed
                log_message(
                    f"Detected UTC clock on screen: {month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}",
                    level="info",
                )
                return parsed

        processed_img = _preprocess_gray_text(original_img)
        text, score = ocr.read_line(_pil_to_bgr(processed_img))
        if score >= ocr.MIN_SCORE:
            last_text = text
            parsed = _parse_utc_candidate(text)
            if parsed is not None:
                month, day, hour, minute, second = parsed
                log_message(
                    f"Detected UTC clock on screen (grayscale): {month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}",
                    level="info",
                )
                return parsed

        log_message(
            f"No UTC clock in 'UTC MM-DD HH:MM:SS' format detected in OCR text: '{last_text.strip()}'",
            level="warning",
        )
        if debug_label:
            _save_ocr_debug_images(debug_label, instance_index, original_img, processed_img)
        return None
    except Exception as e:
        log_message(f"Error reading UTC clock from screen: {e}", level="error")
        if debug_label:
            _save_ocr_debug_images(debug_label, instance_index, original_img, processed_img)
        return None


def _find_first_non_zero_digit_on_image(original_img: Image.Image) -> Optional[Tuple[int, int]]:
    """Locate the first non-zero digit in the given image.

    Uses the OCR backend's per-character boxes and returns the center of the
    leftmost character that is a digit from 1 to 9, in original-image
    coordinates (no ROI offset applied).

    Args:
        original_img (PIL.Image): Original image to search for digits.

    Returns:
        tuple or None: (cx, cy) of the first non-zero digit, or None if none is found.
    """
    try:
        best: Optional[Tuple[int, int, int, int]] = None
        for char, box in ocr.read_chars(_pil_to_bgr(original_img)):
            if char in "123456789" and (best is None or box[0] < best[0]):
                best = box
        if best is None:
            return None
        x, y, width, height = best
        return x + width // 2, y + height // 2
    except Exception as e:
        log_message(f"Error reading first non-zero digit on image: {e}", level="error")
        return None


def find_first_non_zero_digit_position(instance_index: int, roi: Optional[Tuple[int, int, int, int]] = None) -> Optional[Tuple[int, int]]:
    """Takes a screenshot and returns the screen position of the first non-zero digit in the ROI.

    Used to click on text (not on a template), e.g. the troop counts shown in
    the training screen: the leftmost legible digit that is not zero is
    located and returned so the caller can click above it.

    Args:
        instance_index (int): Emulator instance index to take the screenshot from.
        roi (tuple, optional): Region of interest (x, y, w, h) to search for digits.

    Returns:
        tuple or None: (x, y) absolute screen center of the first non-zero digit, or None.
    """
    from wosutil.emulator.emulator_manager import delete_temp_screenshot, take_screenshot

    screenshot_path = take_screenshot(instance_index)
    if not screenshot_path:
        log_message("Could not take screenshot to detect a non-zero digit.", "error")
        return None
    try:
        try:
            with Image.open(screenshot_path) as opened_img:
                img = opened_img.copy()
        finally:
            delete_temp_screenshot(screenshot_path)
        if roi:
            x, y, w, h = roi
            img = img.crop((x, y, x + w, y + h))
        position = _find_first_non_zero_digit_on_image(img)
        if position is None:
            return None
        cx, cy = position
        if roi:
            cx += roi[0]
            cy += roi[1]
        return int(cx), int(cy)
    except Exception as e:
        log_message(f"Error detecting a non-zero digit on screen: {e}", level="error")
        return None


def get_box_center(box: Tuple[int, int, int, int]) -> Tuple[int, int]:
    """Returns the center point of a (x, y, w, h) box.

    Args:
        box (tuple): (x, y, w, h) bounding box.

    Returns:
        tuple: (cx, cy) center point of the box.
    """
    x, y, w, h = box
    return x + w // 2, y + h // 2


def find_template_center_on_screen(
    template_path: str,
    screenshot_path: str,
    threshold: float = SCREEN_CHECK_THRESHOLD,
    roi: Optional[Tuple[int, int, int, int]] = None,
    grayscale: bool = False,
    min_saturation: Optional[float] = None,
) -> Tuple[bool, Optional[Tuple[int, int]]]:
    """Search for a template and return the center of the match.

    Unifies the former color / gray-scale and box / center variants: set
    ``grayscale`` to True for gray-scale matching (robust against color
    shifts), otherwise color matching is used. The center is always
    returned because every caller either clicks it or only checks ``found``.

    Args:
        template_path (str): Path to the template image file.
        screenshot_path (str): Path to the screenshot image file (should be a temporary file, not deleted by this function).
        threshold (float): Minimum confidence threshold for a match.
        roi (tuple, optional): Region of interest as (x, y, w, h) to search within the screenshot.
        grayscale (bool): Match in gray scale when True, in color otherwise.
        min_saturation (float, optional): Minimum mean HSV saturation (0-255)
            of the matched area. Used to reject disabled buttons that keep the
            same artwork but are drawn in gray. When None, saturation is not
            checked.

    Returns:
        tuple: (True, (cx, cy)) if found, (False, None) if not.
    """
    try:
        img, template = _load_screenshot_and_template(screenshot_path, template_path, roi)
        if img is None:
            msg = f"Error loading screenshot: {screenshot_path}"
            log_message(msg, level="error")
            return False, None

        if template is None:
            msg = f"Error loading template: {template_path}"
            log_message(msg, level="error")
            return False, None

        template_name = os.path.basename(template_path)
        prefix = "[GRAY] " if grayscale else ""
        color_img = img

        if grayscale:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(res)

        if max_val >= threshold:
            top_left = max_loc
            h, w = template.shape[:2]

            if min_saturation is not None:
                matched = color_img[top_left[1] : top_left[1] + h, top_left[0] : top_left[0] + w]
                saturation = float(cv2.cvtColor(matched, cv2.COLOR_BGR2HSV)[:, :, 1].mean())
                if saturation < min_saturation:
                    msg = f"{prefix}Template '{template_name}' matches but is too desaturated ({saturation:.0f} < {min_saturation:.0f}), treating it as disabled."
                    log_message(msg, level="info")
                    return False, None

            if roi:
                top_left = (top_left[0] + roi[0], top_left[1] + roi[1])
            msg = f"{prefix}Template '{template_name}' found at {top_left} with confidence {max_val:.2f}"
            log_message(msg, level="success")
            return True, get_box_center((top_left[0], top_left[1], w, h))

        msg = f"{prefix}Template '{template_name}' not found or confidence too low ({max_val:.2f} < {threshold})"
        log_message(msg, level="info")
        return False, None

    except Exception as e:
        msg = f"Error in template matching: {e}"
        log_message(msg, level="error")
        return False, None


def _normalize_word(word: str) -> str:
    """Lowercase a word and strip every non-alphanumeric character.

    Args:
        word (str): Raw OCR word.

    Returns:
        str: Normalized word, empty when the word is only punctuation.
    """
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _union_boxes(boxes: List[Tuple[int, int, int, int]]) -> Tuple[int, int, int, int]:
    """Merge several (x, y, w, h) boxes into the smallest box containing them all.

    Args:
        boxes (list): Non-empty list of (x, y, w, h) boxes.

    Returns:
        tuple: (x, y, w, h) union box.
    """
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[0] + b[2] for b in boxes)
    y2 = max(b[1] + b[3] for b in boxes)
    return x1, y1, x2 - x1, y2 - y1


def _ocr_word_lines(img: Image.Image) -> List[List[Tuple[str, Tuple[int, int, int, int]]]]:
    """Run OCR over a PIL image and return its words grouped by text line."""
    return ocr.read_word_lines(_pil_to_bgr(img))


def _find_text_matches(lines: List[List[Tuple[str, Tuple[int, int, int, int]]]], target_words: List[str]) -> List[Tuple[int, int, int, int]]:
    """Find consecutive target words in OCR lines and return their boxes."""
    matches: List[Tuple[int, int, int, int]] = []
    for line in lines:
        words = [(_normalize_word(word), box) for word, box in line]
        count = len(target_words)
        for i in range(len(words) - count + 1):
            if [w for w, _ in words[i : i + count]] == target_words:
                matches.append(_union_boxes([box for _, box in words[i : i + count]]))
    return matches


def read_words_on_image(img: Image.Image) -> List[Tuple[str, Tuple[int, int, int, int]]]:
    """Read every recognized word of an image with OCR, keeping its position.

    Unlike :func:`read_text_lines_on_screen`, which groups words into lines,
    this returns the individual words so the caller can relate them by
    position (e.g. a label and the value drawn to its right).

    Args:
        img (PIL.Image): Source image containing text.

    Returns:
        list: (word, (x, y, w, h)) pairs in original image coordinates.
            Punctuation-only words are dropped.
    """
    words = [word for line in _ocr_word_lines(img) for word in line]
    words.sort(key=lambda item: (item[1][1], item[1][0]))
    return words


def read_text_lines_on_screen(
    screenshot_path: str,
    roi: Optional[Tuple[int, int, int, int]] = None,
) -> List[Tuple[str, Tuple[int, int, int, int]]]:
    """Read the text lines of a screenshot, optionally within an ROI.

    Args:
        screenshot_path (str): Path to the screenshot image file.
        roi (tuple, optional): Region of interest as (x, y, w, h).

    Returns:
        list: (line text, (x, y, w, h)) pairs for every recognized text line,
            in full-screen coordinates.
    """
    img_bgr = cv2.imread(screenshot_path)
    if img_bgr is None:
        return []
    if roi:
        x, y, w, h = roi
        img_bgr = img_bgr[y : y + h, x : x + w]
    lines = ocr.read_lines(img_bgr)
    if roi:
        lines = [(text, (box[0] + x, box[1] + y, box[2], box[3])) for text, box in lines]
    return lines


def find_text_on_image(img: Image.Image, target: str, last: bool = False) -> Tuple[bool, Optional[Tuple[int, int, int, int]]]:
    """Search a piece of text (one or several words) inside an image with OCR.

    Words are normalized (lowercased, punctuation stripped) and the target is
    matched as a consecutive run of words within a single OCR line, so menu
    entries such as 'Tundra Trek' are found even when the line carries extra
    OCR noise around the words.

    When the target appears several times, the topmost occurrence is returned
    by default; set ``last`` to True to return the lowest one instead (e.g.
    when a section header and its clickable entry share the same label).

    Args:
        img (PIL.Image): Source image containing text.
        target (str): Text to search for, e.g. 'City' or 'Tundra Trek'.
        last (bool): When True return the lowest occurrence instead of the first.

    Returns:
        tuple: (True, (x, y, w, h)) with the matched text position in original
            image coordinates, or (False, None) when not found.
    """
    target_words = [w for w in (_normalize_word(word) for word in target.split()) if w]
    if not target_words:
        return False, None
    matches = _find_text_matches(_ocr_word_lines(img), target_words)
    if not matches:
        return False, None
    if last:
        return True, max(matches, key=lambda box: box[1])
    return True, matches[0]


def find_text_on_screen(
    screenshot_path: str,
    target: str,
    roi: Optional[Tuple[int, int, int, int]] = None,
    instance_index: Optional[int] = None,
    debug_label: Optional[str] = None,
    last: bool = False,
) -> Tuple[bool, Optional[Tuple[int, int, int, int]]]:
    """Search a piece of text inside a screenshot, optionally within an ROI.

    When the text is not found and both ``instance_index`` and ``debug_label``
    are given, the original ROI crop and the processed image are saved to the
    debug directory (only in debug mode) so the OCR failure can be reviewed.

    Args:
        screenshot_path (str): Path to the screenshot image file.
        target (str): Text to search for, e.g. 'City' or 'Tundra Trek'.
        roi (tuple, optional): Region of interest (x, y, w, h) to search within
            the screenshot.
        instance_index (int, optional): Emulator instance index, used to name
            the debug captures on failure.
        debug_label (str, optional): Label used to name the debug captures on
            failure.
        last (bool): When True return the lowest occurrence instead of the first.

    Returns:
        tuple: (True, (x, y, w, h)) in full-screen coordinates when found,
            (False, None) otherwise.
    """
    try:
        img_bgr = cv2.imread(screenshot_path)
        if img_bgr is None:
            msg = f"Error loading screenshot: {screenshot_path}"
            log_message(msg, level="error")
            return False, None
        if roi:
            x, y, w, h = roi
            img_bgr = img_bgr[y : y + h, x : x + w]
        img = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
        found, box = find_text_on_image(img, target, last=last)
        if not found and debug_label is not None and instance_index is not None:
            lines_text = [text for text, _ in ocr.read_lines(img_bgr)]
            log_message(f"OCR lines while looking for '{target}': {lines_text}", level="debug")
            processed = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
            _save_ocr_debug_images(debug_label, instance_index, img, processed)
        if found and box is not None and roi:
            box = (box[0] + roi[0], box[1] + roi[1], box[2], box[3])
        return found, box
    except Exception as e:
        msg = f"Error in text search: {e}"
        log_message(msg, level="error")
        return False, None


def find_text_center_on_screen(
    screenshot_path: str,
    target: str,
    roi: Optional[Tuple[int, int, int, int]] = None,
    instance_index: Optional[int] = None,
    debug_label: Optional[str] = None,
    last: bool = False,
) -> Tuple[bool, Optional[Tuple[int, int]]]:
    """Search a piece of text inside a screenshot and returns the center of the match.

    Wraps :func:`find_text_on_screen` returning the center (cx, cy) of the
    matched text instead of the (x, y, w, h) box, so callers can click directly
    without computing it.

    Returns:
        tuple: (True, (cx, cy)) if found, (False, None) if not.
    """
    found, box = find_text_on_screen(
        screenshot_path,
        target,
        roi=roi,
        instance_index=instance_index,
        debug_label=debug_label,
        last=last,
    )
    if not found or box is None:
        return False, None
    return True, get_box_center(box)
