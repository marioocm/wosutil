"""Entry point for the desktop web shell."""

from wosutil.app.bootstrap import build_app_context
from wosutil.ui.bundle import resolve_ui_bundle
from wosutil.ui.launcher import open_app_window
from wosutil.ui.server import AppServer
from wosutil.utils import log_message, setup_logging


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
    server = AppServer(service, bundle=bundle, startup=startup)

    try:
        server.start()
        service.start()
        log_message(f"Desktop UI listening on {server.host}:{server.port}", "info")
        if not open_app_window(server.url):
            log_message("Could not open a browser window. Open the printed URL manually.", "error")
            print(server.url)
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
