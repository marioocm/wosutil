"""Win32 window helpers to keep emulator windows out of the foreground.

Emulators steal the foreground focus when their window is created, and some
(notably MuMu) re-show their window when the Android boot finishes or the game
opens, grabbing the focus again. The helpers here minimize emulator windows
without activating them (``SW_SHOWMINNOACTIVE``), which makes Windows move the
focus back to whatever the user was doing before; the emulator keeps rendering
while minimized (that is how multi-instance farming setups are used).

To make the window effectively appear already minimized, a short-lived watcher
thread polls the foreground window while the instance is opening and minimizes
it as soon as it belongs to the emulator (by exact window handle or process
name).

Everything is best-effort: a failure to enumerate or minimize a window never
raises, so automation continues even if the window API behaves unexpectedly.
On non-Windows platforms (e.g. CI) every function is a no-op.
"""

import contextlib
import ctypes
import os
import time

from wosutil.utils import log_message

# ShowWindow flags (winuser.h).
SW_MINIMIZE = 6
SW_SHOWMINNOACTIVE = 7

# GetWindowLongW ex-style flags.
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080

# DwmGetWindowAttribute attribute for cloaked (virtual-desktop hidden) windows.
DWMWA_CLOAKED = 14

# How often the watcher refreshes the exact window handles of an emulator that
# can re-create its windows during boot.
_WATCHER_HANDLE_REFRESH_SECONDS = 15

_user32 = None
_dwmapi = None
_wnd_enum_proc = None


def _load_win32():
    """Load the Win32 DLLs once; return False on non-Windows platforms."""
    global _user32, _dwmapi, _wnd_enum_proc
    if _user32 is not None:
        return True
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    try:
        _wnd_enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32 = ctypes.windll.user32
        user32.EnumWindows.argtypes = [_wnd_enum_proc, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        _user32 = user32
        _dwmapi = dwmapi
    except (OSError, AttributeError):
        return False
    return True


def _emit(log, message):
    """Log a message with a one- or two-argument log callable."""
    if log is None:
        log = log_message
    try:
        log(message)
    except TypeError:
        with contextlib.suppress(TypeError):
            log(message, "info")


def _is_cloaked(hwnd):
    """Return True when the window is cloaked (hidden on another virtual desktop)."""
    import ctypes

    cloaked = ctypes.c_int()
    try:
        result = _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    except OSError:
        return False
    return result == 0 and bool(cloaked.value)


def _window_is_visible(hwnd):
    """Return True when the window is visible, not minimized and not a tool window."""
    if not _user32.IsWindowVisible(hwnd):
        return False
    if _user32.IsIconic(hwnd):
        return False
    if _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
        return False
    # Cloaked windows (e.g. on another virtual desktop) report as visible but
    # must not be touched.
    return not _is_cloaked(hwnd)


def _iter_top_level_windows():
    """Yield the handle of every top-level window, best effort."""
    if not _load_win32():
        return
    handles = []

    @_wnd_enum_proc
    def callback(hwnd, _lparam):
        handles.append(hwnd)
        return True

    try:
        _user32.EnumWindows(callback, 0)
    except OSError:
        return
    yield from handles


def _window_process_pid(hwnd):
    """Return the PID of the process that owns the window (0 on failure)."""
    try:
        return int(_user32.GetWindowThreadProcessId(hwnd, None))
    except (TypeError, ValueError):
        return 0


def _window_title(hwnd):
    """Return the window title text ('' on failure)."""
    try:
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value
    except (OSError, TypeError):
        return ""


def _window_process_matches(hwnd, wanted_names):
    """Return True when the window owner's basename is in the wanted names.

    The owner may be a protected/elevated process that cannot be inspected;
    those windows simply do not match.
    """
    pid = _window_process_pid(hwnd)
    if not pid:
        return False
    import psutil

    try:
        name = psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return os.path.splitext(os.path.basename(name))[0].casefold() in wanted_names


def minimize_hwnds(hwnds, log=None):
    """Minimize the given window handles without stealing the user's focus.

    When the window is currently the foreground (active) window it is
    minimized with ``SW_MINIMIZE``, which makes Windows automatically move the
    focus to the next window in the Z-order — the app the user was working
    with before the emulator appeared. Otherwise ``SW_SHOWMINNOACTIVE`` is
    used so the window is minimized without taking the focus.

    Args:
        hwnds (list): Window handles (ints).
        log (callable, optional): Logging function; defaults to log_message.

    Returns:
        int: Number of windows minimized.
    """
    if not _load_win32():
        return 0
    count = 0
    for hwnd in hwnds:
        try:
            if _user32.GetForegroundWindow() == hwnd and not _user32.IsIconic(hwnd):
                _user32.ShowWindow(hwnd, SW_MINIMIZE)
            else:
                _user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
            count += 1
        except OSError:
            continue
    if count:
        _emit(log, f"Minimized {count} emulator window(s) to keep them out of the foreground.")
    return count


def minimize_process_windows(process_names, log=None):
    """Minimize every visible top-level window owned by the given processes.

    Matches the process executable name (basename without extension, case
    insensitive) so it works for every emulator family without knowing exact
    window titles, which vary with the running game.

    Args:
        process_names (list/tuple): Executable names, e.g. ("HD-Player",).
        log (callable, optional): Logging function; defaults to log_message.

    Returns:
        int: Number of windows minimized.
    """
    if not _load_win32():
        return 0
    wanted = {name.strip().casefold() for name in process_names if name and name.strip()}
    if not wanted:
        return 0
    matching = []
    for hwnd in _iter_top_level_windows():
        if _window_is_visible(hwnd) and _window_process_matches(hwnd, wanted):
            matching.append(hwnd)
    return minimize_hwnds(matching, log=log)


def minimize_windows_by_title(titles, log=None):
    """Minimize every visible top-level window whose title matches.

    Emulator window owners can be elevated/protected processes whose name
    cannot be read (psutil and OpenProcess fail), so process-name matching
    misses them. Window titles, however, are readable on any window, and every
    supported emulator titles its instance window with the instance display
    name, which the backends know.

    Args:
        titles (list/tuple): Window titles to minimize (exact match).
        log (callable, optional): Logging function; defaults to log_message.

    Returns:
        int: Number of windows minimized.
    """
    if not _load_win32():
        return 0
    wanted = {title.strip() for title in titles if title and title.strip()}
    if not wanted:
        return 0
    matching = []
    for hwnd in _iter_top_level_windows():
        if _window_is_visible(hwnd) and _window_title(hwnd) in wanted:
            matching.append(hwnd)
    return minimize_hwnds(matching, log=log)


def minimize_foreground_watcher(handles, process_names=(), titles=(), seconds=180, interval=0.15, log=None, refresh_handles=None):
    """Minimize emulator windows that take the foreground while opening.

    Emulators can re-show their window (and grab the focus) some time after
    launch, e.g. when the Android boot finishes or the game opens. This
    watcher polls the foreground window and minimizes it as soon as it belongs
    to the emulator (by exact handle, process name or window title), so the
    window effectively appears already minimized. Runs on a daemon thread and
    stops after ``seconds``; the caller never waits for it.

    Args:
        handles (list): Window handles to minimize if they take the foreground.
        process_names (list): Executable names whose windows are minimized.
        titles (list): Window titles to minimize (exact match); covers
            emulators whose window owner process is unreadable.
        seconds (float): How long to keep watching.
        interval (float): Poll interval in seconds.
        log (callable, optional): Logging function.
        refresh_handles (callable, optional): Returns fresh window handles;
            polled periodically because some emulators re-create their windows
            while booting.
    """
    if not _load_win32():
        return
    import threading

    threading.Thread(
        target=_watch_foreground,
        args=(list(handles), tuple(process_names), tuple(titles), seconds, interval, log, refresh_handles),
        daemon=True,
        name="emulator-window-watcher",
    ).start()


def _watch_foreground(handles, process_names, titles, seconds, interval, log, refresh_handles):
    """Watcher body: poll the foreground window and minimize emulator windows."""
    if not _load_win32():
        return
    wanted = {name.strip().casefold() for name in process_names if name and name.strip()}
    wanted_titles = {title.strip() for title in titles if title and title.strip()}
    handle_set = set(handles)
    last_refresh = 0.0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            fg = _user32.GetForegroundWindow()
        except OSError:
            return
        if fg and not _user32.IsIconic(fg) and (fg in handle_set or (wanted and _window_process_matches(fg, wanted)) or (wanted_titles and _window_title(fg) in wanted_titles)):
            minimize_hwnds([fg], log=log)
        if refresh_handles is not None and time.monotonic() - last_refresh >= _WATCHER_HANDLE_REFRESH_SECONDS:
            last_refresh = time.monotonic()
            try:
                fresh = refresh_handles()
            except Exception:
                fresh = None
            if fresh:
                handle_set = set(fresh)
        time.sleep(interval)
