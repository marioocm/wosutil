"""UTC clock synchronization for the game.

The daily game resets follow the UTC calendar: several tasks cannot be done
again until 00:00 UTC. The world map schedule panel shows the current UTC
date and time, so it is read once every time an instance is opened and cached
here. Tasks can then be rescheduled to the exact UTC instant they need
(currently, to the next 00:00 UTC).

The cache also stores the real wall-clock instant when the game clock was
read. The current UTC time of day is extrapolated from that instant, so a
clock read hours ago (even from the previous day, e.g. 23:59 before midnight
during a long session) still schedules tasks to the *next* 00:00 UTC instead
of the one that already happened.

While the schedule panel is open, its task list (the upcoming events) is also
read: the display is switched to UTC time when needed and every 'Bear Hunt'
entry is cached with the UTC date and time at which it takes place, so tasks
can be scheduled to those instants.
"""

import re
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image

from wosutil.config import COORDINATES, ROI
from wosutil.emulator.emulator_manager import (
    click_on_coordinates,
    delete_temp_screenshot,
    press_android_back_button,
    scroll_screen,
    take_screenshot,
)
from wosutil.emulator.image_utils import read_screen_utc_time, read_words_on_image
from wosutil.stop import stop_signal
from wosutil.tool.tasks.task_helpers import click_on_template, ensure_world_screen
from wosutil.utils import log_message

# Per-instance cache of the last read game clock, with the wall-clock
# timestamp (epoch seconds) when it was read.
_UTC_CACHE: Dict[int, Tuple[Tuple[int, int, int, int, int], float]] = {}

# Per-instance cache of the Bear Hunt events read from the task list, as
# (year, month, day, hour, minute) UTC tuples.
_BEAR_HUNT_CACHE: Dict[int, List[Tuple[int, int, int, int, int]]] = {}

UTC_SECONDS_PER_DAY = 24 * 60 * 60

# Task list OCR settings. The panel draws the event names in dark blue over
# light blue rows (read with a plain upscale pass), while the date banners
# ('Today', '2026/08/26') are slightly off-white text over saturated
# orange/blue banners (read with a loose brightness mask).
_TASK_LIST_SCALE = 3
_TASK_LIST_BRIGHT_VAL_MIN = 200
_TASK_LIST_BRIGHT_SAT_MAX = 140
_TASK_LIST_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_TASK_LIST_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_TASK_LIST_WORD_RE = re.compile(r"[^a-z0-9]")
_TASK_LIST_TODAY_MIN_SIMILARITY = 0.8  # the banner font makes Tesseract read e.g. 'ioday'
_TASK_LIST_MODE_STRIP_HEIGHT = 65  # px: the 'UTC/Local Time' label sits at the top of the panel
_TASK_LIST_MODE_MAX_ATTEMPTS = 3  # mode label reads + toggle clicks before giving up
_TASK_LIST_HEADER_MERGE_Y = 12  # px: the same banner read by both OCR passes lands this close
_TASK_LIST_WORD_ROW_Y = 12  # px: words of the same event name share their vertical center
_TASK_LIST_TIME_MAX_Y_DELTA = 45  # px: an event time sits at most this far from its name
_TASK_LIST_TIME_MIN_X = 500  # px: event times are right-aligned past this x
_TASK_LIST_MAX_SCROLLS = 5  # extra list pages to read when Bear Hunt entries keep appearing
_TASK_LIST_SCROLL_START = (400, 1100)
_TASK_LIST_SCROLL_END = (400, 650)
_TASK_LIST_SCROLL_DURATION_MS = 300
_TASK_LIST_SCROLL_HOLD_MS = 100  # hold the finger so the list stops without momentum
_TASK_LIST_SCROLL_SETTLE_SECONDS = 1.0


def _normalize_task_list_word(word: str) -> str:
    """Lowercase a word and strip every non-alphanumeric character."""
    return _TASK_LIST_WORD_RE.sub("", word.lower())


def _preprocess_task_list_raw(img: Image.Image) -> Image.Image:
    """Upscale without binarization: the event rows are dark text on a light background."""
    return img.resize((img.width * _TASK_LIST_SCALE, img.height * _TASK_LIST_SCALE), resample=Image.Resampling.LANCZOS)


def _preprocess_task_list_bright(img: Image.Image) -> Image.Image:
    """Isolate bright text as dark glyphs on a light background.

    The date banners ('Today', '2026/08/26') draw slightly off-white text over
    saturated orange/blue banners; a loose saturation threshold keeps those
    glyphs while dropping the banner colors.
    """
    arr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    arr = cv2.resize(arr, (arr.shape[1] * _TASK_LIST_SCALE, arr.shape[0] * _TASK_LIST_SCALE), interpolation=cv2.INTER_LANCZOS4)
    hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 2] >= _TASK_LIST_BRIGHT_VAL_MIN) & (hsv[:, :, 1] <= _TASK_LIST_BRIGHT_SAT_MAX)).astype(np.uint8) * 255
    return Image.fromarray(255 - mask)


def _is_today_banner_word(word: str) -> bool:
    """Return True when an OCR word is a (possibly misread) 'Today' banner."""
    normalized = _normalize_task_list_word(word)
    return len(normalized) >= 4 and SequenceMatcher(None, normalized, "today").ratio() >= _TASK_LIST_TODAY_MIN_SIMILARITY


def _merge_task_list_headers(headers: List[Tuple[int, Tuple[int, int, int]]]) -> List[Tuple[int, Tuple[int, int, int]]]:
    """Drop duplicate date banners read by both OCR passes (same date at nearly the same y)."""
    merged: List[Tuple[int, Tuple[int, int, int]]] = []
    for y, date in sorted(headers):
        if merged and merged[-1][1] == date and abs(y - merged[-1][0]) <= _TASK_LIST_HEADER_MERGE_Y:
            continue
        merged.append((y, date))
    return merged


def parse_task_list_schedule(
    words: Sequence[Tuple[str, Tuple[int, int, int, int]]],
    today: Tuple[int, int, int],
    infer_leading_today: bool = False,
) -> Tuple[List[Tuple[int, int, int, int, int]], List[Tuple[int, int, int]]]:
    """Extract the UTC date and time of every 'Bear Hunt' entry in the task list.

    The list is walked top to bottom: date banners ('Today' or 'YYYY/M/D') set
    the date of the entries below them, and a 'Bear Hunt' name is paired with
    the HH:MM time drawn at its right on the same row. Entries sitting above
    every visible banner are skipped, unless ``infer_leading_today`` is set
    (only valid before any scrolling, where the list always starts with the
    'Today' banner, even when OCR could not read it). Events dated before
    ``today`` are dropped: the list only shows upcoming events, so a past date
    is a banner OCR misread.

    Args:
        words (list): (word, (x, y, w, h)) OCR pairs of the task list panel,
            in panel coordinates.
        today (tuple): (year, month, day) the 'Today' banner stands for.
        infer_leading_today (bool): Assign entries above the first visible
            banner to ``today``.

    Returns:
        tuple: (events, headers); events is a sorted, deduplicated list of
            (year, month, day, hour, minute) Bear Hunt times and headers the
            sorted (year, month, day) dates of the banners found.
    """
    headers: List[Tuple[int, Tuple[int, int, int]]] = []
    for word, (_x, y, _w, _h) in words:
        date_match = _TASK_LIST_DATE_RE.search(word)
        if date_match:
            headers.append((y, (int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))))
        elif _is_today_banner_word(word):
            headers.append((y, today))
    headers = _merge_task_list_headers(headers)

    events = set()
    for word, (x, y, _w, h) in words:
        if _normalize_task_list_word(word) != "bear":
            continue
        row_y = y + h // 2
        if not any(_normalize_task_list_word(other) == "hunt" and abs((oy + oh // 2) - row_y) <= _TASK_LIST_WORD_ROW_Y and 0 < ox - x <= 130 for other, (ox, oy, _ow, oh) in words):
            continue
        date = next((header_date for _hy, header_date in reversed(headers) if _hy < y), None)
        if date is None:
            if not infer_leading_today:
                continue
            date = today
        best: Optional[Tuple[int, int, int]] = None
        for other, (ox, oy, _ow, oh) in words:
            time_match = _TASK_LIST_TIME_RE.match(other)
            if not time_match or ox < _TASK_LIST_TIME_MIN_X:
                continue
            dy = abs((oy + oh // 2) - row_y)
            if dy > _TASK_LIST_TIME_MAX_Y_DELTA:
                continue
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
            if hour > 23 or minute > 59:
                continue
            if best is None or dy < best[0]:
                best = (dy, hour, minute)
        if best is not None:
            event = (date[0], date[1], date[2], best[1], best[2])
            # The task list only shows upcoming events; a date before today can
            # only be a banner OCR misread (e.g. '2026/08/27' read as
            # '2026/08/21'), which would otherwise schedule a task to a time
            # that already happened.
            if (event[0], event[1], event[2]) < today:
                continue
            events.add(event)
    return sorted(events), [date for _y, date in headers]


def _task_list_uses_utc_time(panel: Image.Image) -> Optional[bool]:
    """OCR the time mode label at the top of the task list panel.

    Args:
        panel (PIL.Image): Task list panel crop.

    Returns:
        bool or None: True for 'UTC Time', False for 'Local Time', None when
            the label could not be read.
    """
    strip = panel.crop((0, 0, panel.width, _TASK_LIST_MODE_STRIP_HEIGHT))
    words = read_words_on_image(strip, preprocess=_preprocess_task_list_raw)
    normalized = {_normalize_task_list_word(word) for word, _box in words}
    if "local" in normalized:
        return False
    if "utc" in normalized:
        return True
    return None


def _take_task_list_panel_image(instance_index: int) -> Optional[Image.Image]:
    """Take a screenshot and crop it to the task list panel ROI.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        PIL.Image or None: The task list panel crop, or None on failure.
    """
    screenshot_path = take_screenshot(instance_index)
    if not screenshot_path:
        return None
    try:
        with Image.open(screenshot_path) as opened_img:
            x, y, w, h = ROI["task_list"]
            return opened_img.crop((x, y, x + w, y + h))
    finally:
        delete_temp_screenshot(screenshot_path)


def _ensure_task_list_utc_time(instance_index: int) -> bool:
    """Make sure the open task list shows the event times in UTC.

    The label at the top of the panel reads 'UTC Time' or 'Local Time'; when
    it is in local mode the toggle button next to the label is clicked and the
    label is read again.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True when the panel shows UTC time, False otherwise.
    """
    for _ in range(_TASK_LIST_MODE_MAX_ATTEMPTS):
        stop_signal.check()
        panel = _take_task_list_panel_image(instance_index)
        if panel is None:
            log_message("Could not take a screenshot to read the task list time mode.", level="error")
            return False
        uses_utc = _task_list_uses_utc_time(panel)
        if uses_utc is True:
            return True
        if uses_utc is False:
            log_message("The task list shows local time; switching it to UTC time.", level="info")
            click_on_template("task_list_time", instance_index, roi=ROI["task_list"], delay=1.0)
            continue
        log_message("Could not read the task list time mode label.", level="warning")
        return False
    log_message("The task list did not switch to UTC time.", level="warning")
    return False


def _read_task_list_bear_hunt_times(instance_index: int, utc: Tuple[int, int, int, int, int]) -> List[Tuple[int, int, int, int, int]]:
    """Read every 'Bear Hunt' entry of the open task list, scrolling down if needed.

    The list is read page by page; a scroll is repeated while new content
    appears, up to a page cap. Entries whose date banner scrolled off the top
    are skipped (the overlapping scroll step already read them on the previous
    page).

    Args:
        instance_index (int): Emulator instance index.
        utc (tuple): Freshly read game clock (month, day, hour, minute,
            second), used to resolve the 'Today' banner.

    Returns:
        list: Sorted (year, month, day, hour, minute) UTC times of every Bear
            Hunt entry found.
    """
    # The game clock shows no year; the panel is read right after syncing it,
    # so the real UTC year matches it.
    today = (time.gmtime().tm_year, utc[0], utc[1])
    if not _ensure_task_list_utc_time(instance_index):
        log_message("Could not confirm the task list shows UTC time; skipping the Bear Hunt schedule read.", level="warning")
        return []

    events = set()
    last_signature = None
    for scroll_index in range(_TASK_LIST_MAX_SCROLLS + 1):
        stop_signal.check()
        panel = _take_task_list_panel_image(instance_index)
        if panel is None:
            break
        words = read_words_on_image(panel, preprocess=_preprocess_task_list_raw)
        words += read_words_on_image(panel, preprocess=_preprocess_task_list_bright)
        found, headers = parse_task_list_schedule(words, today, infer_leading_today=scroll_index == 0)
        events.update(found)
        signature = (tuple(headers), frozenset(found))
        if signature == last_signature:
            break  # the list did not move: the bottom was reached
        last_signature = signature
        if scroll_index < _TASK_LIST_MAX_SCROLLS:
            scroll_screen(
                _TASK_LIST_SCROLL_START[0],
                _TASK_LIST_SCROLL_START[1],
                _TASK_LIST_SCROLL_END[0],
                _TASK_LIST_SCROLL_END[1],
                _TASK_LIST_SCROLL_DURATION_MS,
                instance_index,
                hold_end_ms=_TASK_LIST_SCROLL_HOLD_MS,
            )
            time.sleep(_TASK_LIST_SCROLL_SETTLE_SECONDS)

    if events:
        schedule = ", ".join(f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d} UTC" for year, month, day, hour, minute in sorted(events))
        log_message(f"Bear Hunt schedule: {schedule}.", level="success")
    else:
        log_message("No Bear Hunt events found in the task list.", level="warning")
    return sorted(events)


def set_cached_utc_time(instance_index: int, utc: Optional[Tuple[int, int, int, int, int]]) -> None:
    """Store (or clear) the last read game clock for an instance.

    Args:
        instance_index (int): Emulator instance index.
        utc (tuple or None): (month, day, hour, minute, second) to cache, or
            None to remove the cached clock.
    """
    if utc is None:
        _UTC_CACHE.pop(instance_index, None)
    else:
        _UTC_CACHE[instance_index] = (utc, time.time())


def get_cached_utc_time(instance_index: int) -> Optional[Tuple[int, int, int, int, int]]:
    """Return the last read game clock for an instance, or None if not read yet.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        tuple or None: (month, day, hour, minute, second) or None.
    """
    cached = _UTC_CACHE.get(instance_index)
    if cached is None:
        return None
    return cached[0]


def set_cached_bear_hunt_times(instance_index: int, times: Sequence[Tuple[int, int, int, int, int]]) -> None:
    """Store the Bear Hunt event times read from the task list for an instance.

    Args:
        instance_index (int): Emulator instance index.
        times (list): (year, month, day, hour, minute) UTC tuples.
    """
    _BEAR_HUNT_CACHE[instance_index] = list(times)


def get_cached_bear_hunt_times(instance_index: int) -> List[Tuple[int, int, int, int, int]]:
    """Return the cached Bear Hunt event times for an instance.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        list: (year, month, day, hour, minute) UTC tuples, empty when the task
            list was never read (or no Bear Hunt was found).
    """
    return list(_BEAR_HUNT_CACHE.get(instance_index, []))


def clear_cached_utc_times() -> None:
    """Clear the whole UTC clock and Bear Hunt caches (used by tests)."""
    _UTC_CACHE.clear()
    _BEAR_HUNT_CACHE.clear()


def sync_utc_time(instance_index: int) -> bool:
    """Reads the game clock (UTC date and time) from the world map schedule panel.

    The schedule panel is opened with the 'world_schedule' click, the clock is
    read from the 'world_schedule_utc' ROI with OCR and the panel is closed
    again. The result is cached per instance so the tasks can compute exact
    UTC-based reschedules.

    While the panel is open, the task list is switched to UTC time when needed
    and every 'Bear Hunt' entry is read and cached with the UTC date and time
    at which it takes place (see :func:`get_cached_bear_hunt_times`).

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the clock was read and cached, False otherwise.
    """
    log_message(f"Attempting to sync the game UTC clock on instance {instance_index}...", level="info")
    if not ensure_world_screen(instance_index):
        log_message("Could not reach the world screen to read the UTC clock.", level="warning")
        return False

    click_on_coordinates(*COORDINATES["world_schedule"], instance_index, delay=1.0)

    utc = read_screen_utc_time(instance_index, roi=ROI["world_schedule_utc"], debug_label="world_schedule_utc")
    set_cached_utc_time(instance_index, utc)

    bear_hunts: List[Tuple[int, int, int, int, int]] = []
    if utc is not None:
        bear_hunts = _read_task_list_bear_hunt_times(instance_index, utc)
    set_cached_bear_hunt_times(instance_index, bear_hunts)

    press_android_back_button(instance_index)

    if utc is None:
        log_message("Could not read the UTC clock from the world map schedule panel.", level="warning")
        return False
    month, day, hour, minute, second = utc
    log_message(f"Game UTC clock synced: {month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}.", level="success")
    return True


def _current_utc_seconds_of_day(instance_index: int) -> Optional[float]:
    """Estimated current UTC time of day (seconds since 00:00:00) for an instance.

    The cached game clock is extrapolated with the real time elapsed since it
    was read: the game clock keeps ticking while the instance is open, so even
    a cache from the previous day yields the current UTC time of day. Returns
    None when no clock was read yet.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        float or None: Seconds since 00:00:00 UTC (0-86399), or None.
    """
    cached = _UTC_CACHE.get(instance_index)
    if cached is None:
        return None
    (_month, _day, hour, minute, second), read_at = cached
    elapsed = int(time.time() - read_at)
    return float(hour * 3600 + minute * 60 + second + elapsed) % UTC_SECONDS_PER_DAY


def get_seconds_until_utc_midnight(instance_index: int, fallback: Optional[float] = None) -> Optional[float]:
    """Seconds until the next 00:00 UTC according to the cached game clock.

    When the clock says exactly 00:00:00 the task waits a full day, since the
    daily reset will happen at the following midnight.

    Args:
        instance_index (int): Emulator instance index.
        fallback (float, optional): Value to return when no clock was read yet.

    Returns:
        float or None: Seconds until the next 00:00 UTC, or the fallback when
            the clock is unavailable (None when no fallback was given).
    """
    seconds_of_day = _current_utc_seconds_of_day(instance_index)
    if seconds_of_day is None:
        return fallback
    return float(UTC_SECONDS_PER_DAY - seconds_of_day)


def get_seconds_until_utc_hour(instance_index: int, hour: int, minute: int = 0, fallback: Optional[float] = None) -> Optional[float]:
    """Seconds until the given hour/minute in UTC according to the cached game clock.

    The target is the next occurrence of ``hour:minute`` UTC; when that instant
    already passed today the task waits until it comes back tomorrow.

    Args:
        instance_index (int): Emulator instance index.
        hour (int): Target UTC hour (0-23).
        minute (int, optional): Target UTC minute (0-59).
        fallback (float, optional): Value to return when no clock was read yet.

    Returns:
        float or None: Seconds until the target UTC time, or the fallback when
            the clock is unavailable (None when no fallback was given).
    """
    current = _current_utc_seconds_of_day(instance_index)
    if current is None:
        return fallback
    target = hour * 3600 + minute * 60
    delay = target - current
    if delay <= 0:
        delay += UTC_SECONDS_PER_DAY
    return float(delay)
