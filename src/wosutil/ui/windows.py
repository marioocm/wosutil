"""Windows helpers to focus and watch the desktop app window."""

import ctypes
import sys
import time
from ctypes import wintypes
from typing import Callable, Optional

APP_WINDOW_TITLE = "WoS Util"


def _user32():
    """Return the Win32 user32 library, or None outside Windows."""
    if not sys.platform.startswith("win"):
        return None
    return ctypes.windll.user32


def find_app_window(title: str = APP_WINDOW_TITLE) -> Optional[int]:
    """Return the handle of the first visible top-level window with ``title``.

    Args:
        title: Exact window title to look for.
    """
    user32 = _user32()
    if user32 is None:
        return None

    found: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value == title:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return found[0] if found else None


def focus_window(hwnd: int) -> None:
    """Bring a window to the foreground (best effort).

    Args:
        hwnd: Window handle returned by :func:`find_app_window`.
    """
    user32 = _user32()
    if user32 is None:
        return
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)


def watch_app_window(
    title: str,
    on_closed: Callable[[], None],
    find_window: Optional[Callable[[str], Optional[int]]] = None,
    focus: Optional[Callable[[int], None]] = None,
    poll_seconds: float = 1.0,
) -> None:
    """Focus the app window when it appears and report when it disappears.

    The browser-created window is not ours, so this watcher is what makes the
    app exit when the user closes it, independently of the page unload events.

    Args:
        title: Exact app window title.
        on_closed: Called once after the window has been gone for two polls.
        find_window: Window lookup used in tests.
        focus: Focus function used in tests.
        poll_seconds: Delay between polls.
    """
    finder = find_window or find_app_window
    focuser = focus or focus_window
    seen = False
    missing = 0

    while True:
        hwnd = finder(title)
        if hwnd:
            if not seen:
                seen = True
                focuser(hwnd)
            missing = 0
        elif seen:
            missing += 1
            if missing >= 2:
                on_closed()
                return
        if poll_seconds:
            time.sleep(poll_seconds)
