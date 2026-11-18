"""Open the desktop UI in a Chromium app window."""

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from wosutil.ui.bundle import cache_dir

WINDOW_SIZE = "1024,900"


def _candidate_browsers() -> list[str]:
    """Return the browser executables worth trying, in preference order."""
    override = os.environ.get("WOSUTIL_BROWSER")
    if override:
        return [override]

    candidates: list[str] = []
    if sys.platform.startswith("win"):
        roots = [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")]
        for root in roots:
            if not root:
                continue
            candidates.extend(
                [
                    os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"),
                    os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"),
                ]
            )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            ]
        )
    else:
        for name in ("microsoft-edge", "microsoft-edge-stable", "chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
    return candidates


def open_app_window(url: str) -> bool:
    """Open ``url`` in a Chromium app window.

    Uses a dedicated browser profile so the window has no tabs or address
    bar and never touches the user's browsing profile. Falls back to the
    default browser when no Chromium executable is available.

    Args:
        url: Local app URL including the access token.

    Returns:
        bool: False when no browser could be launched.
    """
    profile_dir = cache_dir() / "browser"
    profile_dir.mkdir(parents=True, exist_ok=True)
    arguments = [
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-background-mode",
        f"--window-size={WINDOW_SIZE}",
    ]

    for candidate in _candidate_browsers():
        executable = candidate if Path(candidate).is_file() else shutil.which(candidate)
        if not executable:
            continue
        try:
            subprocess.Popen([executable, *arguments], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        except OSError:
            continue
        return True

    try:
        return bool(webbrowser.open(url))
    except OSError:
        return False
