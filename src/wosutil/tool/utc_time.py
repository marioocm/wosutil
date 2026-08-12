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
"""

import time
from typing import Dict, Optional, Tuple

from wosutil.config import COORDINATES, ROI
from wosutil.emulator.emulator_manager import click_on_coordinates, press_android_back_button
from wosutil.emulator.image_utils import read_screen_utc_time
from wosutil.tool.tasks.task_helpers import ensure_world_screen
from wosutil.utils import log_message

# Per-instance cache of the last read game clock, with the wall-clock
# timestamp (epoch seconds) when it was read.
_UTC_CACHE: Dict[int, Tuple[Tuple[int, int, int, int, int], float]] = {}

UTC_SECONDS_PER_DAY = 24 * 60 * 60


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


def clear_cached_utc_times() -> None:
    """Clear the whole UTC cache (used by tests)."""
    _UTC_CACHE.clear()


def sync_utc_time(instance_index: int) -> bool:
    """Reads the game clock (UTC date and time) from the world map schedule panel.

    The schedule panel is opened with the 'world_schedule' click, the clock is
    read from the 'world_schedule_utc' ROI with OCR and the panel is closed
    again. The result is cached per instance so the tasks can compute exact
    UTC-based reschedules.

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
