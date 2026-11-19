"""Entry point for the desktop web shell."""

import os

from wosutil.app.bootstrap import build_app_context
from wosutil.ui.bundle import resolve_ui_bundle
from wosutil.ui.launcher import open_app_window
from wosutil.ui.server import AppServer
from wosutil.utils import log_message, setup_logging


def _env_port() -> int:
    """Return ``WOSUTIL_UI_PORT``, or 0 when unset or invalid."""
    try:
        return int(os.environ.get("WOSUTIL_UI_PORT", "0"))
    except ValueError:
        return 0


def run() -> int:
    """Start the service, the local server and the app window.

    Returns:
        int: Process exit code.
    """
    setup_logging()
    log_message("Starting desktop shell...", "info")
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
        window_url = os.environ.get("WOSUTIL_UI_URL") or server.url
        log_message(f"Desktop UI listening on {server.host}:{server.port}", "info")
        if os.environ.get("WOSUTIL_UI_NO_WINDOW") == "1":
            log_message("Desktop UI running without a window (WOSUTIL_UI_NO_WINDOW=1).", "info")
        elif not open_app_window(window_url):
            log_message("Could not open a browser window. Open the printed URL manually.", "error")
            print(window_url)
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
