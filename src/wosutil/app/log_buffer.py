"""In-memory log history shared by every view."""

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

from wosutil.app.events import EventBus


@dataclass(frozen=True)
class LogEntry:
    """One log line with a monotonically increasing sequence number."""

    seq: int
    time: float
    level: str
    message: str

    def as_dict(self) -> dict:
        """Return a JSON-serializable representation."""
        return {"seq": self.seq, "time": self.time, "level": self.level, "message": self.message}


class LogBuffer:
    """Keep the most recent log lines and stream new ones on the event bus.

    The buffer is registered as a ``wosutil.utils`` log observer, so every
    view receives the same lines regardless of which thread emitted them.
    """

    def __init__(self, bus: Optional[EventBus] = None, capacity: int = 2000):
        """Create the buffer.

        Args:
            bus: Event bus used to stream new lines to the views.
            capacity: Number of lines kept for late subscribers.
        """
        self._entries: deque[LogEntry] = deque(maxlen=capacity)
        self._bus = bus
        self._lock = threading.Lock()
        self._next_seq = 1

    def observe(self, message, level="info") -> None:
        """Record a line emitted by :func:`wosutil.utils.log_message`.

        Args:
            message: Log text.
            level: Log level name such as ``info`` or ``error``.
        """
        with self._lock:
            entry = LogEntry(seq=self._next_seq, time=time.time(), level=level, message=str(message))
            self._next_seq += 1
            self._entries.append(entry)
        if self._bus is not None:
            self._bus.publish("log", entry.as_dict())

    def tail(self, limit: int = 300) -> list[dict]:
        """Return the most recent entries, newest last.

        Args:
            limit: Maximum number of entries.
        """
        with self._lock:
            entries = list(self._entries)[-limit:]
        return [entry.as_dict() for entry in entries]

    def after(self, seq: int, limit: int = 500) -> list[dict]:
        """Return the entries recorded after ``seq``.

        Args:
            seq: Last sequence number the caller has already rendered.
            limit: Maximum number of entries returned.
        """
        with self._lock:
            entries = [entry for entry in self._entries if entry.seq > seq][-limit:]
        return [entry.as_dict() for entry in entries]
