"""Composition root for the desktop application.

Builds the profile manager, the task catalog, the emulator backend and the
headless service. Both the desktop shell and any future front-end start here.
"""

from dataclasses import dataclass
from typing import Optional

from wosutil.app.events import EventBus
from wosutil.app.log_buffer import LogBuffer
from wosutil.app.service import WosUtilService
from wosutil.emulator.backends import create_backend, default_emulator_code, detect_installed_emulators, platform_emulator_codes
from wosutil.emulator.emulator_manager import cleanup_stale_temp_screenshots
from wosutil.preferences import get_emulator, get_emulator_paths, get_requirements_reminder_seen, load_preferences
from wosutil.tool.profiles.profile_manager import ProfileManager
from wosutil.tool.tasks.task_definitions import get_task_definitions
from wosutil.utils import add_log_observer
from wosutil.utils import log_message as default_log_message


@dataclass
class AppContext:
    """Everything a view needs to start.

    Attributes:
        service: Headless application service.
        requirements_reminder_pending: True on the first run, before the setup
            requirements dialog was acknowledged.
        needs_emulator_choice: True when no emulator was saved yet and several
            (or none) were detected.
        installed_emulators: Detected emulator codes, in detection order.
    """

    service: WosUtilService
    log_buffer: LogBuffer
    requirements_reminder_pending: bool
    needs_emulator_choice: bool
    installed_emulators: list


def build_app_context(log_message=None) -> AppContext:
    """Build the application service and the first-run information.

    Args:
        log_message: Logging function. Defaults to ``wosutil.utils``.

    Returns:
        AppContext: Ready-to-start application context.
    """
    log = log_message or default_log_message
    bus = EventBus()
    log_buffer = LogBuffer(bus)
    # Register before the managers run so their startup lines are kept for the
    # log view; the service registers the same observer and deduplicates it.
    add_log_observer(log_buffer.observe)

    cleanup_stale_temp_screenshots()

    log("Initializing profile manager...", "info")
    profile_manager = ProfileManager(log)

    log("Loading task definitions...", "info")
    task_definitions = get_task_definitions()

    preferences = load_preferences()
    emulator_paths = get_emulator_paths(preferences)
    installed_emulators = detect_installed_emulators(emulator_paths)
    emulator: Optional[str] = get_emulator(preferences)
    needs_emulator_choice = emulator is None
    if emulator is not None and emulator not in platform_emulator_codes():
        fallback = installed_emulators[0] if installed_emulators else default_emulator_code()
        log(f"Emulator '{emulator}' is not available on this platform; using '{fallback}'.", "warning")
        emulator = fallback
        needs_emulator_choice = True
    elif emulator is None:
        emulator = installed_emulators[0] if installed_emulators else default_emulator_code()
        if not installed_emulators:
            log(f"No emulator detected; falling back to {emulator}.", "warning")

    log(f"Initializing emulator backend ({emulator})...", "info")
    backend = create_backend(emulator, log, emulator_paths=emulator_paths)

    service = WosUtilService(
        profile_manager=profile_manager,
        task_definitions=task_definitions,
        backend=backend,
        emulator=emulator,
        bus=bus,
        log_buffer=log_buffer,
        log_message=log,
    )
    return AppContext(
        service=service,
        log_buffer=log_buffer,
        requirements_reminder_pending=not get_requirements_reminder_seen(),
        needs_emulator_choice=needs_emulator_choice,
        installed_emulators=installed_emulators,
    )
