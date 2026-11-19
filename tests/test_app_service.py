"""Tests for the headless application service."""

import unittest
from unittest.mock import patch

from wosutil.app.service import WosUtilService
from wosutil.emulator.emulator_manager import set_active_backend

TASKS = {"a": {"id": "a", "name": "Task A", "priority": 1, "description": ""}}


class FakeBackend:
    """Minimal emulator backend stub."""

    def __init__(self, warnings=None):
        """Initialize the stub."""
        self.warnings = list(warnings or [])
        self.started = 0

    def check_adb_access(self):
        """Return the configured ADB warnings."""
        return list(self.warnings)

    def get_instances(self):
        """Return one fake instance."""
        return [{"index": 0, "name": "MuMu-0"}]

    def start_instance(self, index):
        """Record a start."""
        self.started += 1
        return True

    def stop_instance(self, index):
        """Record a stop."""
        return True

    def get_serial(self, index):
        """Return a stable serial."""
        return f"serial-{index}"


class FakeProfileManager:
    """Minimal profile manager stub."""

    def __init__(self):
        """Create the default profile."""
        self.profiles = {"All": ["a"]}

    def save_profiles(self):
        """Pretend the profiles were persisted."""
        return True


class ServiceTestCase(unittest.TestCase):
    """Base case building a service with an in-memory state."""

    def setUp(self):
        """Clear the global backend between tests."""
        set_active_backend(None)
        self.service = None

    def tearDown(self):
        """Release the service observers."""
        if self.service is not None:
            self.service.close()
        set_active_backend(None)

    def _make_service(self, backend=None):
        """Build a service with persisted selection and one cached instance."""
        for target, value in (
            ("wosutil.app.service.load_instance_selection", {"0": {"checked": True, "profile": "All"}, "max_emulators": 3}),
            ("wosutil.app.service.load_instance_cache", [{"index": 0, "name": "MuMu-0"}]),
            ("wosutil.app.service.detect_installed_emulators", ["mumu"]),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = WosUtilService(
            profile_manager=FakeProfileManager(),
            task_definitions={key: dict(value) for key, value in TASKS.items()},
            backend=backend or FakeBackend(),
            emulator="mumu",
            log_message=lambda message, level="info": None,
        )
        return self.service

    def test_snapshot_exposes_selection_and_limit(self):
        """The snapshot carries everything a view needs to paint the rows."""
        snapshot = self._make_service().snapshot()
        self.assertEqual(snapshot["max_instances"], 3)
        self.assertEqual(snapshot["instances"][0]["profile"], "All")
        self.assertTrue(snapshot["instances"][0]["checked"])
        self.assertFalse(snapshot["tool_running"])

    def test_start_tool_returns_the_adb_error_without_starting(self):
        """A blocked ADB connection is reported to the view."""
        service = self._make_service(FakeBackend(warnings=["ADB blocked"]))
        result = service.start_tool()
        self.assertFalse(result.ok)
        self.assertIn("ADB blocked", result.error or "")
        self.assertFalse(service.snapshot()["tool_running"])

    def test_selection_update_keeps_the_emulator_limit(self):
        """Saving a row must not wipe the configured emulator limit."""
        service = self._make_service()
        with patch("wosutil.app.service.save_instance_selection", return_value=True) as save:
            service.update_instance_selection(0, False, "All")
        saved = save.call_args.args[0]
        self.assertEqual(saved["max_emulators"], 3)
        self.assertEqual(saved["0"], {"checked": False, "profile": "All"})

    def test_deleted_profiles_fall_back_to_the_first_one(self):
        """A removed profile must not leave a dangling name in the rows or the run."""
        service = self._make_service()
        service._selection["0"]["profile"] = "Missing"
        self.assertEqual(service.snapshot()["instances"][0]["profile"], "All")
        self.assertEqual(service.selected_instances(), [(0, "All")])

    def test_emulator_change_is_refused_while_running(self):
        """Swapping the backend during a run would break the old workers."""
        service = self._make_service()
        service._controller.tool_running = True
        with patch("wosutil.app.service.create_backend") as create_backend:
            result = service.set_emulator("ldplayer")
        self.assertFalse(result.ok)
        create_backend.assert_not_called()
