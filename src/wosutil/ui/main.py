"""Entry point for the desktop web shell."""

import os
import sys
import threading
from contextlib import suppress

from wosutil.app.bootstrap import build_app_context
from wosutil.ui.bundle import resolve_ui_bundle
from wosutil.ui.launcher import open_app_window
from wosutil.ui.native_window import run_native_window
from wosutil.ui.server import AppServer
from wosutil.ui.windows import watch_app_window
from wosutil.utils import log_message, setup_logging


def _env_port() -> int:
    """Return ``WOSUTIL_UI_PORT``, or 0 when unset or invalid."""
    try:
        return int(os.environ.get("WOSUTIL_UI_PORT", "0"))
    except ValueError:
        return 0


def run() -> int:
    """Start the main desktop shell."""
    return _run(template_mode=False)


def run_template() -> int:
    """Start the standalone Template Creator window."""
    return _run(template_mode=True)


def _run(template_mode: bool) -> int:
    """Start the service, the local server and the selected window.

    Args:
        template_mode: True for the standalone Template Creator page.

    Returns:
        int: Process exit code.
    """
    setup_logging()
    log_message("Starting template creator..." if template_mode else "Starting desktop shell...", "info")
    context = build_app_context(log_message)
    service = context.service
    if context.needs_emulator_choice:
        # Persist the provisional choice made at startup; the UI can change it
        # later through the emulator selector.
        service.set_emulator(service.snapshot()["emulator"])

    bundle = resolve_ui_bundle()
    if bundle is None:
        log_message("Desktop UI bundle not found; serving the fallback page. Run scripts/fetch_ui.py.", "warning")

    startup = {
        "requirements_reminder": context.requirements_reminder_pending,
        "needs_emulator_choice": context.needs_emulator_choice,
        "installed_emulators": context.installed_emulators,
    }
    server = AppServer(
        service,
        bundle=bundle,
        startup=startup,
        token=os.environ.get("WOSUTIL_UI_TOKEN") or None,
        port=_env_port(),
    )

    try:
        server.start()
        service.start()
        if template_mode:
            window_url = server.url_for("templates/index.html")
            window_title = "WoS Util · Template Creator"
        else:
            window_url = os.environ.get("WOSUTIL_UI_URL") or server.url
            window_title = "WoS Util"
        log_message(f"Desktop UI listening on {server.host}:{server.port}", "info")

        if os.environ.get("WOSUTIL_UI_NO_WINDOW") == "1":
            log_message("Desktop UI running without a window (WOSUTIL_UI_NO_WINDOW=1).", "info")
            server.wait()
        elif run_native_window(window_url, wait_for_stop=server.wait, title=window_title, debug=os.environ.get("WOSUTIL_UI_DEBUG") == "1"):
            log_message("Desktop UI window closed.", "info")
        else:
            log_message("Native window unavailable; falling back to the browser app window.", "warning")
            if not open_app_window(window_url):
                log_message("Could not open a browser window. Open the printed URL manually.", "error")
                print(window_url)
                server.wait()
            else:
                if sys.platform.startswith("win"):
                    # The fallback window belongs to another process: watch it
                    # so the app exits when the user closes it.
                    threading.Thread(target=watch_app_window, args=(window_title, server.stop), name="AppWindowWatch", daemon=True).start()
                server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        with suppress(Exception):
            if service.snapshot().get("tool_running"):
                service.stop_tool()
        server.stop()
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
