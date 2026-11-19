"""Native desktop window backed by pywebview (WebView2 on Windows)."""

import contextlib
import threading
from typing import Callable

from wosutil.ui.bundle import cache_dir


def run_native_window(url: str, wait_for_stop: Callable[[], object], title: str = "WoS Util", debug: bool = False) -> bool:
    """Open the app in a native webview window.

    The window belongs to this process, so there is no browser chrome, no
    browser context menu, and closing it ends the app directly. On Windows
    pywebview uses the WebView2 runtime; Linux needs WebKitGTK/PyGObject and
    macOS uses WKWebView.

    Args:
        url: Local UI URL, including the access token.
        wait_for_stop: Blocks until the app server stops. It is used to close
            the window if the backend disappears.
        title: Window title.
        debug: Enable the webview developer tools.

    Returns:
        bool: True when the window was opened and later closed. False when
        pywebview is unavailable or cannot start; the caller should fall back
        to the browser launcher.
    """
    try:
        import webview
    except Exception:
        return False

    storage = cache_dir() / "webview"
    with contextlib.suppress(OSError):
        storage.mkdir(parents=True, exist_ok=True)

    try:
        window = webview.create_window(
            title,
            url,
            width=1024,
            height=900,
            min_size=(900, 600),
            focus=True,
            confirm_close=False,
            background_color="#151311",
            text_select=True,
            zoomable=False,
        )
        if window is None:
            return False

        def close_when_server_stops():
            with contextlib.suppress(Exception):
                wait_for_stop()
            with contextlib.suppress(Exception):
                window.destroy()

        threading.Thread(target=close_when_server_stops, name="WebviewWatch", daemon=True).start()
        webview.start(debug=debug, private_mode=False, storage_path=str(storage))
        return True
    except Exception:
        return False
