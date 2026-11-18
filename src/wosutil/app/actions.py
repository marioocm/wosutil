"""Result type returned by application commands."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ActionResult:
    """Outcome of a command invoked from a view.

    Attributes:
        ok: True when the command completed successfully.
        error: User-facing error text when ``ok`` is False.
    """

    ok: bool
    error: Optional[str] = None
