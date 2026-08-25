"""Image processing utilities for template matching and OCR.

Provides functions for finding templates on screen, reading text from screenshots,
and managing template caching.
"""

import os
import re
import sys
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple, cast

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageDraw, ImageFont

from wosutil.config import DEBUG_DIR, SCREEN_CHECK_THRESHOLD
from wosutil.utils import ensure_directory_exists, log_message


def resolve_tesseract_cmd() -> str:
    """Locate the tesseract executable used by pytesseract.

    Priority: the copy bundled inside a PyInstaller bundle (extracted under
    ``_MEIPASS/tesseract``), then the ``TESSERACT_CMD`` environment variable,
    then the default system install. When the bundled copy is used,
    ``TESSDATA_PREFIX`` is pointed at its ``tessdata`` folder so no system
    installation is required.
    """
    if getattr(sys, "frozen", False):
        tesseract_dir = os.path.join(cast(str, getattr(sys, "_MEIPASS", "")), "tesseract")
        bundled_exe = os.path.join(tesseract_dir, "tesseract.exe")
        if os.path.exists(bundled_exe):
            os.environ.setdefault("TESSDATA_PREFIX", os.path.join(tesseract_dir, "tessdata"))
            return bundled_exe
    return os.environ.get("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")


pytesseract.pytesseract.tesseract_cmd = resolve_tesseract_cmd()

# Template cache to avoid reloading images
_template_cache: Dict[str, np.ndarray] = {}

# Timer OCR settings
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2}):(\d{2})")
_DAY_RE = re.compile(r"(\d{1,2})\s*[dD]")
_OCR_PSMS = (7, 8, 13, 11)
_DIGIT_WHITELIST = "0123456789:dD"

# Red timer text settings: some timers (e.g. pet skills) draw red digits over
# warm-colored artwork, so grayscale cannot separate text from background; the
# digits are isolated with an HSV filter and the OCR pass is retried on them.
_TIMER_RED_HUE_MAX = 12
_TIMER_RED_HUE_MIN = 160
_TIMER_RED_SAT_MIN = 70
_TIMER_RED_VAL_MIN = 100

# Menu text OCR settings (side menu tabs/entries, shop labels)
_TEXT_SCALE = 3
_TEXT_VALUE_THRESHOLD = 220
_TEXT_SATURATION_THRESHOLD = 100
_TEXT_PSM = 6
_TEXT_FALLBACK_PSM = 11
# Fuzzy-search OCR settings (world-map search: resource labels over bright tiles)
_FUZZY_TEXT_VALUE_THRESHOLD = 220
_FUZZY_TEXT_SATURATION_THRESHOLD = 60
_FUZZY_TEXT_PSMS = (6, 11, 12)
_FUZZY_TEXT_MIN_SIMILARITY = 0.55

# UTC clock OCR settings (world map schedule panel: 'UTC MM-DD HH:MM:SS')
_UTC_TIME_RE = re.compile(r"UTC\s*(\d{1,2})-(\d{1,2})\s*(\d{1,2}):(\d{2}):(\d{2})", re.IGNORECASE)
_UTC_OCR_PSMS = (6, 7)
_NATIVE_TEMPLATE_FONTS = ("arialbd.ttf", "arial.ttf", "segoeuib.ttf", "segoeui.ttf", "courbd.ttf", "cour.ttf", "framd.ttf")
_NATIVE_TEMPLATE_SIZES = (28, 30, 32, 34, 36)

_native_templates_cache: Optional[Dict[str, List[np.ndarray]]] = None


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


def find_template_on_screen(
    template_path: str, screenshot_path: str, threshold: float = SCREEN_CHECK_THRESHOLD, roi: Optional[Tuple[int, int, int, int]] = None
) -> Tuple[bool, Optional[Tuple[int, int, int, int]]]:
    """Searches for the template image within the screenshot.

    Returns (True, (x, y, w, h)) if found, (False, None) if not found.

    Args:
        template_path (str): Path to the template image file.
        screenshot_path (str): Path to the screenshot image file (should be a temporary file, not deleted by this function).
        threshold (float): Minimum confidence threshold for a match.
        roi (tuple, optional): Region of interest as (x, y, w, h) to search within the screenshot.

    Returns:
        tuple: (bool, (x, y, w, h) or None)
    """
    try:
        # Load screenshot
        img_rgb = cv2.imread(screenshot_path)
        if img_rgb is None:
            msg = f"Error loading screenshot: {screenshot_path}"
            log_message(msg, level="error")
            return False, None

        # Load template (with caching)
        template = load_template(template_path)
        if template is None:
            msg = f"Error loading template: {template_path}"
            log_message(msg, level="error")
            return False, None

        template_name = os.path.basename(template_path)

        # Apply ROI if specified
        if roi:
            x, y, w, h = roi
            img_rgb = img_rgb[y : y + h, x : x + w]

        # Perform template matching
        res = cv2.matchTemplate(img_rgb, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

        if max_val >= threshold:
            top_left = max_loc
            h, w = template.shape[:2]

            # Adjust coordinates if ROI was used
            if roi:
                top_left = (top_left[0] + roi[0], top_left[1] + roi[1])

            msg = f"Template '{template_name}' found at {top_left} with confidence {max_val:.2f}"
            log_message(msg, level="success")
            return True, (top_left[0], top_left[1], w, h)
        else:
            msg = f"Template '{template_name}' not found or confidence too low ({max_val:.2f} < {threshold})"
            log_message(msg, level="info")
            return False, None

    except Exception as e:
        msg = f"Error in template matching: {e}"
        log_message(msg, level="error")
        return False, None


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
        img_rgb = cv2.imread(screenshot_path)
        if img_rgb is None:
            return []

        template = load_template(template_path)
        if template is None:
            return []

        if roi:
            x, y, w, h = roi
            img_rgb = img_rgb[y : y + h, x : x + w]

        res = cv2.matchTemplate(img_rgb, template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(res >= threshold)

        matches = []
        h, w = template.shape[:2]

        for pt in zip(*locations[::-1]):
            if roi:
                pt = (pt[0] + roi[0], pt[1] + roi[1])
            matches.append((pt[0], pt[1], w, h))

        # Apply non-maximum suppression
        matches_nms = non_max_suppression(matches, overlap_thresh=nms_threshold)
        return matches_nms

    except Exception as e:
        msg = f"Error in multiple template matching: {e}"
        log_message(msg, level="error")
        return []


def non_max_suppression(boxes: List[Tuple[int, int, int, int]], overlap_thresh: float = 0.5) -> List[Tuple[int, int, int, int]]:
    """Applies non-maximum suppression to avoid overlapping boxes.

    Args:
        boxes (list): List of (x, y, w, h) tuples.
        overlap_thresh (float): Overlap threshold for suppression.

    Returns:
        list: Filtered list of boxes after NMS.
    """
    if len(boxes) == 0:
        return []
    boxes_np = np.array(boxes)
    x1 = boxes_np[:, 0]
    y1 = boxes_np[:, 1]
    x2 = x1 + boxes_np[:, 2]
    y2 = y1 + boxes_np[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    idxs = np.argsort(y2)
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
        processed_img (Image or None): Image after filters, as fed to Tesseract.
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
    artwork; the standard grayscale+Otsu pass merges them into the background.
    This retains only strongly red pixels (hue near 0/180, saturated enough and
    bright enough), producing the same binary layout the grayscale pass yields,
    and ``read_screen_time`` retries the OCR sequence on it.

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


def read_screen_time(
    instance_index: int,
    roi: Optional[Tuple[int, int, int, int]] = None,
    debug_label: Optional[str] = None,
    max_seconds: Optional[int] = None,
    ocr_psms: Optional[Tuple[int, ...]] = None,
    screenshot_path: Optional[str] = None,
) -> Optional[int]:
    """Read a timer in HH:MM:SS format from an emulator screenshot using OCR, returning the time in seconds.

    By default a fresh screenshot is captured; a caller can pass ``screenshot_path``
    to reuse one it already owns so the same capture can be shared with other OCR
    steps. The shared file is never deleted here.

    On failure (no timer matched, an unexpected error, or a detected value over
    ``max_seconds``) the original ROI image and the processed image are saved to
    the debug directory so the preprocessing can be reviewed.

    Args:
        instance_index (int): Emulator instance index to take the screenshot from.
        roi (tuple, optional): Region of interest (x, y, w, h) to search for the timer.
        debug_label (str, optional): Label used to name the debug images on failure.
        max_seconds (int, optional): Maximum plausible value in seconds. Reads above
            this are treated as OCR errors (logged and debug captured) and return ``None``.
        ocr_psms (tuple, optional): Tesseract page-segmentation modes to try. The
            default uses the standard timer modes.
        screenshot_path (str, optional): Reuse an already taken screenshot instead
            of capturing a new one. The file is not deleted by this function.

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
        # Preprocess: grayscale, upscale and Otsu binarization for a cleaner binary input.
        img = img.convert("L")
        img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
        img_np = np.array(img)
        _, img_np = cv2.threshold(img_np, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        img = Image.fromarray(img_np)
        processed_img = img
        # Try several Tesseract page-segmentation modes; some ROI sizes/fonts only
        # work with a specific mode (e.g. psm 7 returns empty while psm 8/13 succeed).
        text = ""
        for psm in _OCR_PSMS if ocr_psms is None else ocr_psms:
            try:
                text = pytesseract.image_to_string(
                    img,
                    config=f"--psm {psm} -c tessedit_char_whitelist={_DIGIT_WHITELIST}",
                )
            except Exception:
                continue
            parsed = _parse_timer_text(text)
            if parsed is None:
                continue
            h, m, s, total_seconds = parsed
            if max_seconds is not None and total_seconds > max_seconds:
                log_message(
                    f"Timer read {h:02}:{m:02}:{s:02} ({total_seconds}s) exceeds the maximum plausible value of {max_seconds}s, treating it as an invalid timer read.",
                    level="error",
                )
                if debug_label:
                    _save_ocr_debug_images(debug_label, instance_index, original_img, processed_img)
                return None
            log_message(f"Detected timer on screen: {h:02}:{m:02}:{s:02} ({total_seconds} seconds)", level="info")
            return total_seconds
        # Retry on red-text timers: some timers (e.g. pet skills) draw red
        # digits over warm-colored artwork; the grayscale/Otsu pass cannot
        # separate them, so the OCR sequence is repeated on a red-color mask.
        processed_img = _preprocess_timer_red_text(original_img)
        for psm in _OCR_PSMS if ocr_psms is None else ocr_psms:
            try:
                text = pytesseract.image_to_string(
                    processed_img,
                    config=f"--psm {psm} -c tessedit_char_whitelist={_DIGIT_WHITELIST}",
                )
            except Exception:
                continue
            parsed = _parse_timer_text(text)
            if parsed is None:
                continue
            h, m, s, total_seconds = parsed
            if max_seconds is not None and total_seconds > max_seconds:
                log_message(
                    f"Timer read {h:02}:{m:02}:{s:02} ({total_seconds}s) exceeds the maximum plausible value of {max_seconds}s, treating it as an invalid timer read.",
                    level="error",
                )
                if debug_label:
                    _save_ocr_debug_images(debug_label, instance_index, original_img, processed_img)
                return None
            log_message(f"Detected timer on screen (red text): {h:02}:{m:02}:{s:02} ({total_seconds} seconds)", level="info")
            return total_seconds
        # Fallback: digit recognition without Tesseract (segmentation + template matching).
        if original_img is not None:
            native_seconds = _read_time_native(original_img)
            if native_seconds is not None:
                log_message(f"Detected timer on screen (native OCR): {native_seconds} seconds", level="info")
                return native_seconds
        log_message(f"No timer in HH:MM:SS format detected in OCR text: '{text.strip() if text else ''}'", level="warning")
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
    the date in MM-DD format and the time in HH:MM:SS format; Tesseract can
    merge or drop the spaces, e.g. 'UTC 08-10 11:12:26' or 'UTC08-1011:12:26'.

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


def read_screen_utc_time(
    instance_index: int,
    roi: Optional[Tuple[int, int, int, int]] = None,
    debug_label: Optional[str] = None,
) -> Optional[Tuple[int, int, int, int, int]]:
    """Takes a screenshot and reads the game clock (UTC date and time) from the specified ROI using OCR.

    The schedule panel of the world map shows a small icon, the 'UTC' text,
    the date in MM-DD format and the time in HH:MM:SS format inside the ROI,
    e.g. 'UTC 08-10 11:12:26'. The ROI is grayscaled, upscaled and binarized
    before several page-segmentation modes are tried; the first plausible
    date/time wins.

    On failure the original ROI image and the processed image are saved to the
    debug directory so the preprocessing can be reviewed.

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
        # Preprocess: grayscale, upscale and Otsu binarization for a cleaner binary input.
        img = img.convert("L")
        img = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
        img_np = np.array(img)
        _, img_np = cv2.threshold(img_np, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        img = Image.fromarray(img_np)
        processed_img = img
        # Try several Tesseract page-segmentation modes; the layout of the clock
        # ('UTC' + date + time) is only parsed by the single-line modes.
        text = ""
        for psm in _UTC_OCR_PSMS:
            try:
                text = pytesseract.image_to_string(img, config=f"--psm {psm}")
            except Exception:
                continue
            parsed = parse_utc_text(text)
            if parsed is not None:
                month, day, hour, minute, second = parsed
                log_message(
                    f"Detected UTC clock on screen: {month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}",
                    level="info",
                )
                return parsed
        log_message(
            f"No UTC clock in 'UTC MM-DD HH:MM:SS' format detected in OCR text: '{text.strip() if text else ''}'",
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


def _normalize_glyph(cell_bin: np.ndarray, target_w: int = 30, target_h: int = 40) -> np.ndarray:
    """Crop a binary glyph to its bounding box and fit it into a (target_h, target_w) grid.

    Args:
        cell_bin (np.ndarray): Binary glyph patch (uint8, values 0 or 255).
        target_w (int): Normalized width.
        target_h (int): Normalized height.

    Returns:
        np.ndarray: Normalized binary glyph (values 0 or 255) centered in a target_h x target_w grid.
    """
    mask = (cell_bin > 128).astype(np.uint8)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return np.zeros((target_h, target_w), dtype=np.uint8)
    sub = mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    r_h, r_w = sub.shape
    ratio = min(target_w / r_w, target_h / r_h)
    nh, nw = max(1, int(r_h * ratio)), max(1, int(r_w * ratio))
    resized = cv2.resize((sub * 255).astype(np.uint8), (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((target_h, target_w), dtype=np.uint8)
    y_off, x_off = (target_h - nh) // 2, (target_w - nw) // 2
    out[y_off : y_off + nh, x_off : x_off + nw] = np.where(resized > 128, 255, 0)
    return out


def _load_native_templates() -> Dict[str, List[np.ndarray]]:
    """Render reference digit glyphs (0-9) from common Windows fonts.

    Cached on first call. Returns a mapping digit -> list of normalized template glyphs.

    Returns:
        Dict[str, List[np.ndarray]]: Digit label mapped to its rendered templates.
    """
    global _native_templates_cache
    if _native_templates_cache is not None:
        return _native_templates_cache
    ref: Dict[str, List[np.ndarray]] = {}
    font_dir = r"C:\Windows\Fonts"
    for font_name in _NATIVE_TEMPLATE_FONTS:
        font_path = os.path.join(font_dir, font_name)
        if not os.path.exists(font_path):
            continue
        for size in _NATIVE_TEMPLATE_SIZES:
            try:
                font = ImageFont.truetype(font_path, size)
            except OSError:
                continue
            for digit in "0123456789":
                canvas = Image.new("L", (64, 64), 0)
                draw = ImageDraw.Draw(canvas)
                bbox = font.getbbox(digit)
                draw.text((8 - bbox[0], 4 - bbox[1]), digit, fill=255, font=font)
                ref.setdefault(digit, []).append(_normalize_glyph(np.array(canvas)))
    _native_templates_cache = ref
    return ref


def _glyph_iou(a: np.ndarray, b: np.ndarray) -> float:
    """Intersection-over-union of two normalized binary glyphs."""
    inter = np.logical_and(a > 128, b > 128).sum()
    union = np.logical_or(a > 128, b > 128).sum()
    return inter / max(1, int(union))


def _middle_fill_ratio(cell: np.ndarray, region: str) -> float:
    """Foreground fraction of the middle band (35-65% height) on the left or right side (30% width).

    Used to disambiguate glyphs such as 3 (open left-middle) vs 8 (filled left-middle).
    """
    mask = cell > 128
    h, w = mask.shape
    band = mask[int(h * 0.35) : int(h * 0.65), :]
    total = band.sum()
    side = band[:, : max(1, int(w * 0.3))] if region == "left" else band[:, -max(1, int(w * 0.3)) :]
    return side.sum() / max(1, int(total))


def _classify_native_glyph(norm: np.ndarray) -> Optional[str]:
    """Match a normalized glyph against rendered digit templates.

    The candidate pair {'8', '3'} is disambiguated structurally because the two digits
    look very similar in the game font.

    Args:
        norm (np.ndarray): Normalized glyph patch.

    Returns:
        str or None: Best matching digit.
    """
    ref = _load_native_templates()
    scores = {}
    for digit, templates in ref.items():
        scores[digit] = max(_glyph_iou(norm, t) for t in templates)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_digit, best_score = ranked[0]
    if best_score < 0.5:
        return None
    second_digit = ranked[1][0]
    if (best_digit, second_digit) in (("8", "3"), ("3", "8"), ("9", "3"), ("3", "9")):
        # '3' has an open left-middle band; '8'/'9' are closed there.
        if _middle_fill_ratio(norm, "left") < 0.15:
            return "3"
        return best_digit
    if (best_digit, second_digit) in (("0", "6"), ("6", "0")):
        # '6' has an open right side in the middle band; '0' is closed on both sides.
        if _middle_fill_ratio(norm, "right") < 0.15:
            return "6"
        return "0"
    return best_digit


def _read_time_native(original_img: Image.Image) -> Optional[int]:
    """Read an HH:MM:SS timer without Tesseract.

    Segments digits via connected components and matches each glyph against rendered
    font templates using IoU, with structural rules to disambiguate similar digits.

    Args:
        original_img (PIL.Image): Original ROI crop (before preprocessing).

    Returns:
        int or None: Time in seconds if recognized, otherwise None.
    """
    try:
        img = original_img.convert("L")
        img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
        gray = np.array(img)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        n, _, stats, _ = cv2.connectedComponentsWithStats(binary)
        stats = np.asarray(stats)
        h_img, w_img = binary.shape
        min_h = 0.4 * h_img
        min_w = 0.06 * w_img
        min_area = 0.005 * h_img * w_img
        comps = [tuple(stats[i]) for i in range(1, n) if stats[i][2] >= min_w and stats[i][3] >= min_h and stats[i][4] >= min_area]
        comps.sort(key=lambda c: c[0])

        digits: List[str] = []
        for x, y, w, h, _area in comps:
            if w < min_w:
                continue
            cell = binary[y : y + h, x : x + w]
            digit = _classify_native_glyph(_normalize_glyph(cell))
            if digit is None:
                return None
            digits.append(digit)

        if len(digits) not in (5, 6):
            return None
        if len(digits) == 6:
            hh, mm, ss = "".join(digits[0:2]), "".join(digits[2:4]), "".join(digits[4:6])
        else:
            hh, mm, ss = digits[0], "".join(digits[1:3]), "".join(digits[3:5])
        if int(mm) >= 60 or int(ss) >= 60:
            return None
        return int(hh) * 3600 + int(mm) * 60 + int(ss)
    except Exception:
        return None


def _find_first_non_zero_digit_on_image(original_img: Image.Image) -> Optional[Tuple[int, int]]:
    """Locate the first non-zero digit in the given image.

    Uses the same segmentation and glyph classification as the native timer
    OCR, so it works without Tesseract. The found text is scanned at its
    glyph center from left to right. Its center is returned in original-image
    coordinates (no ROI offset applied).

    Args:
        original_img (PIL.Image): Original image to search for digits.

    Returns:
        tuple or None: (cx, cy) of the first non-zero digit, or None if none is found.
    """
    try:
        img = original_img.convert("L")
        img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
        gray = np.array(img)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # If the strip background is light, the text is dark: invert it so the
        # glyphs are the foreground (light) component like in the rest of the OCR.
        h, w = gray.shape[:2]
        corners = [gray[2, 2], gray[2, w - 3], gray[h - 3, 2], gray[h - 3, w - 3]]
        if sum(corners) / len(corners) > 200:
            binary = 255 - binary

        n, _, stats, _ = cv2.connectedComponentsWithStats(binary)
        stats = np.asarray(stats)
        h_img, w_img = binary.shape
        # Text glyphs fill most of the strip height; copy their height instead
        # of the ROI width for the width filter so thin digits like '1' pass.
        min_h = 0.4 * h_img
        min_w = max(4.0, 0.15 * min_h)
        min_area = 0.01 * min_h * min_h
        comps = [tuple(stats[i]) for i in range(1, n) if stats[i][3] >= min_h and stats[i][2] >= min_w and stats[i][4] >= min_area]
        comps.sort(key=lambda c: c[0])

        for x, y, w, h, _area in comps:
            if w < min_w:
                continue
            cell = binary[y : y + h, x : x + w]
            digit = _classify_native_glyph(_normalize_glyph(cell))
            if digit is None or digit == "0":
                continue
            # Components were computed on the 2x upscaled image, so divide by 2
            # to map back to the original image coordinates.
            return (x + w // 2) // 2, (y + h // 2) // 2
        return None
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


def find_gray_template_on_screen(
    template_path: str, screenshot_path: str, threshold: float = SCREEN_CHECK_THRESHOLD, roi: Optional[Tuple[int, int, int, int]] = None
) -> Tuple[bool, Optional[Tuple[int, int, int, int]]]:
    """Search for the template in grayscale.

    Converts both images to grayscale before comparing.
    Returns (True, (x, y, w, h)) if found, (False, None) otherwise.
    """
    try:
        # Load screenshot
        img_rgb = cv2.imread(screenshot_path)
        if img_rgb is None:
            msg = f"Error loading screenshot: {screenshot_path}"
            log_message(msg, level="error")
            return False, None

        # Load template (with cache)
        template = load_template(template_path)
        if template is None:
            msg = f"Error loading template: {template_path}"
            log_message(msg, level="error")
            return False, None

        template_name = os.path.basename(template_path)

        # Convert both images to grayscale
        img_gray = cv2.cvtColor(img_rgb, cv2.COLOR_BGR2GRAY)
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        # Apply ROI if specified
        if roi:
            x, y, w, h = roi
            img_gray = img_gray[y : y + h, x : x + w]

        # Matching
        res = cv2.matchTemplate(img_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

        if max_val >= threshold:
            top_left = max_loc
            h, w = template_gray.shape[:2]
            if roi:
                top_left = (top_left[0] + roi[0], top_left[1] + roi[1])
            msg = f"[GRAY] Template '{template_name}' found at {top_left} with confidence {max_val:.2f}"
            log_message(msg, level="success")
            return True, (top_left[0], top_left[1], w, h)
        else:
            msg = f"[GRAY] Template '{template_name}' not found or confidence too low ({max_val:.2f} < {threshold})"
            log_message(msg, level="info")
            return False, None
    except Exception as e:
        msg = f"Error in gray template matching: {e}"
        log_message(msg, level="error")
        return False, None


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
    template_path: str, screenshot_path: str, threshold: float = SCREEN_CHECK_THRESHOLD, roi: Optional[Tuple[int, int, int, int]] = None
) -> Tuple[bool, Optional[Tuple[int, int]]]:
    """Searches for the template and returns the center point of the match.

    Wraps :func:`find_template_on_screen` returning the center (cx, cy) of the
    template instead of the (x, y, w, h) box, so callers can click directly
    without computing it.

    Returns:
        tuple: (True, (cx, cy)) if found, (False, None) if not.
    """
    found, box = find_template_on_screen(template_path, screenshot_path, threshold=threshold, roi=roi)
    if not found or box is None:
        return False, None
    return True, get_box_center(box)


def find_gray_template_center_on_screen(
    template_path: str, screenshot_path: str, threshold: float = SCREEN_CHECK_THRESHOLD, roi: Optional[Tuple[int, int, int, int]] = None
) -> Tuple[bool, Optional[Tuple[int, int]]]:
    """Searches for the template in gray scale and returns the center point of the match.

    Wraps :func:`find_gray_template_on_screen` returning the center (cx, cy)
    of the template instead of the (x, y, w, h) box.

    Returns:
        tuple: (True, (cx, cy)) if found, (False, None) if not.
    """
    found, box = find_gray_template_on_screen(template_path, screenshot_path, threshold=threshold, roi=roi)
    if not found or box is None:
        return False, None
    return True, get_box_center(box)


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


def _preprocess_text_image(img: Image.Image) -> Image.Image:
    """Binarize a game UI image so bright text becomes dark glyphs on a light background.

    The game draws menu text in white/light blue over dark panels, so keeping
    the pixels with a high HSV value and a low-to-medium saturation isolates
    the text strokes while discarding colored backgrounds and highlights (e.g.
    the light blue of a selected tab, which is bright but saturated); the mask
    is inverted because Tesseract reads dark-on-light text better. The image
    is upscaled first so the thin game font survives the binarization.

    Args:
        img (PIL.Image): Source image containing text.

    Returns:
        PIL.Image: Upscaled and binarized image ready for Tesseract.
    """
    arr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    arr = cv2.resize(arr, (arr.shape[1] * _TEXT_SCALE, arr.shape[0] * _TEXT_SCALE), interpolation=cv2.INTER_LANCZOS4)
    hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 2] >= _TEXT_VALUE_THRESHOLD) & (hsv[:, :, 1] <= _TEXT_SATURATION_THRESHOLD)).astype(np.uint8) * 255
    return Image.fromarray(255 - mask)


def _preprocess_fuzzy_text_image(img: Image.Image) -> Image.Image:
    """Keep only neutral (white/gray) bright pixels for cluttered screens.

    Resource labels in the world-map search panel sit on bright, tinted
    artwork (light-cyan tile edges). A plain brightness mask merges the label
    with that artwork into one blob Tesseract cannot segment. This mask also
    demands near-zero saturation, which drops tinted highlights while keeping
    the pure white glyphs readable.

    Args:
        img (PIL.Image): Source image containing text and UI artwork.

    Returns:
        PIL.Image: Upscaled and binarized image ready for Tesseract.
    """
    arr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    arr = cv2.resize(arr, (arr.shape[1] * _TEXT_SCALE, arr.shape[0] * _TEXT_SCALE), interpolation=cv2.INTER_LANCZOS4)
    hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 2] >= _FUZZY_TEXT_VALUE_THRESHOLD) & (hsv[:, :, 1] <= _FUZZY_TEXT_SATURATION_THRESHOLD)).astype(np.uint8) * 255
    return Image.fromarray(255 - mask)


def _preprocess_raw_text_image(img: Image.Image) -> Image.Image:
    """Upscale the original image for OCR when binarization loses glyphs.

    Args:
        img (PIL.Image): Source image containing text.

    Returns:
        PIL.Image: Upscaled original image.
    """
    return img.resize((img.width * _TEXT_SCALE, img.height * _TEXT_SCALE), resample=Image.Resampling.LANCZOS)


def _preprocess_gray_inverted_text_image(img: Image.Image) -> Image.Image:
    """Invert the grayscale image so bright button labels turn dark-on-light.

    White text on saturated buttons (Gather/Search) survives binarization as
    dark glyphs trapped inside a bright button-shaped island, which Tesseract's
    layout analysis often refuses to segment. Plain inverted grayscale keeps
    the button shading readable instead.

    Args:
        img (PIL.Image): Source image containing text.

    Returns:
        PIL.Image: Upscaled, grayscale-inverted image ready for Tesseract.
    """
    arr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    arr = cv2.resize(arr, (arr.shape[1] * _TEXT_SCALE, arr.shape[0] * _TEXT_SCALE), interpolation=cv2.INTER_LANCZOS4)
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    return Image.fromarray(255 - gray)


def _ocr_lines(img: Image.Image, psm: int = _TEXT_PSM, preprocess=None) -> List[List[Tuple[str, Tuple[int, int, int, int]]]]:
    """Run OCR on an image and group the recognized words into text lines.

    Args:
        img (PIL.Image): Source image containing text.
        psm (int): Tesseract page segmentation mode.
        preprocess (callable, optional): Image preprocessing function. The
            standard text preprocessing is used when omitted.

    Returns:
        list: One entry per text line, each a list of (word, (x, y, w, h))
            pairs in original image coordinates. Punctuation-only words are
            dropped.
    """
    processed = _preprocess_text_image(img) if preprocess is None else preprocess(img)
    data = pytesseract.image_to_data(processed, output_type=pytesseract.Output.DICT, config=f"--psm {psm}")
    lines: Dict[Tuple[int, int, int], List[Tuple[str, Tuple[int, int, int, int]]]] = {}
    for i, word in enumerate(data["text"]):
        if not _normalize_word(word):
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        x1 = data["left"][i] // _TEXT_SCALE
        y1 = data["top"][i] // _TEXT_SCALE
        x2 = (data["left"][i] + data["width"][i]) // _TEXT_SCALE
        y2 = (data["top"][i] + data["height"][i]) // _TEXT_SCALE
        lines.setdefault(key, []).append((word, (x1, y1, x2 - x1, y2 - y1)))
    return list(lines.values())


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


def _find_fuzzy_text_matches(lines: List[List[Tuple[str, Tuple[int, int, int, int]]]], target_words: List[str]) -> List[Tuple[float, Tuple[int, int, int, int]]]:
    """Find close OCR spellings of a single target word.

    This is reserved for short, fixed UI labels whose decorative outline can
    cause Tesseract to drop or replace one or two characters. Multi-word text
    keeps the exact matching behavior used by the menus.

    Args:
        lines: OCR lines containing words and their boxes.
        target_words: Normalized target words.

    Returns:
        list: Similarity scores and boxes for close matches.
    """
    if len(target_words) != 1:
        return []

    target = target_words[0]
    matches = []
    for line in lines:
        for word, box in line:
            normalized = _normalize_word(word)
            if len(normalized) < 2:
                continue
            if normalized.startswith(target) and len(normalized) > len(target):
                continue
            similarity = SequenceMatcher(None, normalized, target).ratio()
            if similarity >= _FUZZY_TEXT_MIN_SIMILARITY:
                matches.append((similarity, box))
    return matches


def read_text_lines_on_image(img: Image.Image) -> List[Tuple[str, Tuple[int, int, int, int]]]:
    """Read the text lines of an image with OCR.

    Args:
        img (PIL.Image): Source image containing text.

    Returns:
        list: (line text, (x, y, w, h)) pairs for every recognized text line,
            in original image coordinates.
    """
    return [(" ".join(word for word, _ in line), _union_boxes([box for _, box in line])) for line in _ocr_lines(img)]


def find_text_on_image(img: Image.Image, target: str, last: bool = False, fuzzy: bool = False) -> Tuple[bool, Optional[Tuple[int, int, int, int]]]:
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
        fuzzy (bool): Retry a single-word search with a looser OCR mask and
            small spelling differences. Use only for decorative, fixed labels.

    Returns:
        tuple: (True, (x, y, w, h)) with the matched text position in original
            image coordinates, or (False, None) when not found.
    """
    target_words = [w for w in (_normalize_word(word) for word in target.split()) if w]
    if not target_words:
        return False, None
    matches = _find_text_matches(_ocr_lines(img), target_words)
    if not matches:
        matches = _find_text_matches(_ocr_lines(img, psm=_TEXT_FALLBACK_PSM), target_words)
    if fuzzy and (not matches or last):
        alternate_matches = []
        fuzzy_matches = []
        for preprocess in (_preprocess_fuzzy_text_image, _preprocess_raw_text_image, _preprocess_gray_inverted_text_image):
            for psm in _FUZZY_TEXT_PSMS:
                lines = _ocr_lines(img, psm=psm, preprocess=preprocess)
                alternate_matches.extend(_find_text_matches(lines, target_words))
                fuzzy_matches.extend(_find_fuzzy_text_matches(lines, target_words))
        if alternate_matches:
            if last:
                matches.extend(alternate_matches)
            elif not matches:
                matches = alternate_matches
        elif fuzzy_matches:
            if last:
                matches.extend(box for _similarity, box in fuzzy_matches)
            else:
                best_similarity = max(similarity for similarity, _ in fuzzy_matches)
                matches = [box for similarity, box in fuzzy_matches if similarity == best_similarity]
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
    fuzzy: bool = False,
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
        fuzzy (bool): Retry a single-word search with a looser OCR mask and
            small spelling differences.

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
        found, box = find_text_on_image(img, target, last=last, fuzzy=fuzzy)
        if not found and debug_label is not None and instance_index is not None:
            lines_text = [text for text, _ in read_text_lines_on_image(img)]
            log_message(f"OCR lines while looking for '{target}': {lines_text}", level="debug")
            _save_ocr_debug_images(debug_label, instance_index, img, _preprocess_text_image(img))
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
    fuzzy: bool = False,
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
        fuzzy=fuzzy,
    )
    if not found or box is None:
        return False, None
    return True, get_box_center(box)
