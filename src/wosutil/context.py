"""Shared runtime context for the automation tool.

Holds cross-cutting services that are needed deep inside the task pipeline
(e.g. the multi-instance emulator manager). A module-level holder avoids
threading the same object through every function signature; the GUI registers
the manager once at startup and the task helpers read it when they need it.
"""

from typing import Optional

from wosutil.emulator.backends import EmulatorBackend

_manager: Optional[EmulatorBackend] = None


def set_multi_instance_manager(manager: Optional[EmulatorBackend]) -> None:
    """Registers the emulator backend for the whole process.

    Args:
        manager: The emulator backend to register.
    """
    global _manager
    _manager = manager


def get_multi_instance_manager() -> Optional[EmulatorBackend]:
    """Returns the registered emulator backend, or None if not set."""
    return _manager
