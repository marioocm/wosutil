"""Tests for the in-memory log stream."""

import unittest

from wosutil.app.log_buffer import LogBuffer
from wosutil.utils import add_log_observer, log_message, remove_log_observer


class LogBufferTests(unittest.TestCase):
    """Cover the history kept for late views."""

    def test_ring_buffer_keeps_recent_entries_and_sequence(self):
        """Old lines are dropped and sequence numbers stay monotonic."""
        buffer = LogBuffer(capacity=2)
        buffer.observe("one")
        buffer.observe("two")
        buffer.observe("three")
        entries = buffer.tail(5)
        self.assertEqual([entry["message"] for entry in entries], ["two", "three"])
        self.assertEqual([entry["seq"] for entry in entries], [2, 3])
        self.assertEqual([entry["message"] for entry in buffer.after(2)], ["three"])

    def test_log_message_reaches_registered_observers(self):
        """The logging path notifies views without importing a GUI toolkit."""
        received = []

        def observer(message, level="info"):
            received.append((message, level))

        add_log_observer(observer)
        self.addCleanup(remove_log_observer, observer)

        log_message("hello", "warning")

        self.assertEqual(received[-1], ("hello", "warning"))
