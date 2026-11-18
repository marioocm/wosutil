"""In-process event bus shared by every view.

Each subscriber owns a bounded queue, so a slow or paused view (a window being
resized, a browser tab in the background) can never block the scheduler or the
instance workers that publish events.
"""

import contextlib
import queue
import threading
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Event:
    """One event published by the application core.

    Attributes:
        type: Event name such as ``state``, ``log`` or ``dialog``.
        data: JSON-serializable payload.
    """

    type: str
    data: dict


class Subscription:
    """Bounded event queue owned by one view."""

    def __init__(self, maxsize: int = 500):
        """Create an empty subscription.

        Args:
            maxsize: Maximum number of events kept while the view is not
                draining the queue. The oldest event is dropped when full.
        """
        self._queue: queue.Queue[Event] = queue.Queue(maxsize=maxsize)
        self._closed = False

    def offer(self, event: Event) -> None:
        """Queue an event, dropping the oldest one when the queue is full."""
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
            return
        except queue.Full:
            pass
        with contextlib.suppress(queue.Empty):
            self._queue.get_nowait()
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(event)

    def drain(self) -> list[Event]:
        """Return every pending event and empty the queue."""
        events = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                return events

    def close(self) -> None:
        """Stop accepting events."""
        self._closed = True


class EventBus:
    """Fan out events to every subscribed view."""

    def __init__(self):
        """Create a bus without subscribers."""
        self._subscriptions: list[Subscription] = []
        self._lock = threading.Lock()

    def subscribe(self) -> Subscription:
        """Register and return a new subscription."""
        subscription = Subscription()
        with self._lock:
            self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a subscription returned by :meth:`subscribe`."""
        with self._lock:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)
        subscription.close()

    def publish(self, event_type: str, data: Optional[dict] = None) -> None:
        """Publish an event to every subscriber.

        Args:
            event_type: Event name, e.g. ``state``.
            data: JSON-serializable payload.
        """
        event = Event(type=event_type, data=data or {})
        with self._lock:
            subscriptions = list(self._subscriptions)
        for subscription in subscriptions:
            subscription.offer(event)

    def close(self) -> None:
        """Close every subscription."""
        with self._lock:
            subscriptions = list(self._subscriptions)
            self._subscriptions.clear()
        for subscription in subscriptions:
            subscription.close()
