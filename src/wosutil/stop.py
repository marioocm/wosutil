"""Cooperative cancellation mechanism for the automation tool.

A single module-level ``stop_signal`` is checked at key points: ADB operations,
screenshots, clicks and long loops. When the tool is stopped, ``check()``
raises ``ToolStopped`` so in-flight work aborts promptly no matter how deep
in a task it is, without threading a stop event through every function
signature.
"""

import threading
from typing import Optional


class ToolStopped(Exception):
    """Raised when the tool is stopped while an operation is in progress."""


class StopSignal:
    """Thread-safe cooperative stop signal."""

    def __init__(self) -> None:
        """Initializes the stop signal."""
        self._event = threading.Event()

    def set(self) -> None:
        """Request the tool to stop."""
        self._event.set()

    def clear(self) -> None:
        """Clear the stop request (e.g. when starting the tool)."""
        self._event.clear()

    def is_set(self) -> bool:
        """Returns True if a stop has been requested."""
        return self._event.is_set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Wait until a stop is requested or the timeout elapses.

        Returns True if a stop was requested while waiting.
        """
        return self._event.wait(timeout)

    def check(self) -> None:
        """Raises ToolStopped if the tool has been asked to stop."""
        if self._event.is_set():
            raise ToolStopped()


# Module-level signal shared by the GUI, the controller, and the emulator/task primitives.
stop_signal = StopSignal()
