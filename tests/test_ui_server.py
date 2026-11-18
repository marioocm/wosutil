"""Tests for the local UI HTTP server."""

import http.client
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from wosutil.app.actions import ActionResult
from wosutil.app.events import Subscription
from wosutil.ui.bundle import load_ui_bundle
from wosutil.ui.server import AppServer


class FakeService:
    """Minimal application service for the HTTP boundary."""

    def __init__(self):
        """Start with an empty call log."""
        self.calls = []

    def snapshot(self):
        """Return a state payload."""
        return {"instances": [], "profiles": {}, "tool_running": False, "max_instances": 2}

    def logs(self, limit=300):
        """Return no log history."""
        return []

    def logs_after(self, seq, limit=500):
        """Return no new log lines."""
        return []

    def subscribe(self):
        """Return a real subscription for the SSE stream."""
        return Subscription()

    def unsubscribe(self, subscription):
        """Close the subscription."""
        subscription.close()

    def start_tool(self):
        """Record the start command."""
        self.calls.append(("start_tool",))
        return ActionResult(True)

    def stop_tool(self):
        """Record the stop command."""
        self.calls.append(("stop_tool",))
        return ActionResult(True)

    def refresh_instances(self, force=False):
        """Record the refresh command."""
        self.calls.append(("refresh_instances", force))
        return ActionResult(True)

    def set_max_instances(self, value):
        """Record the emulator limit."""
        self.calls.append(("set_max_instances", value))
        return ActionResult(True)

    def update_instance_selection(self, index, checked, profile):
        """Record one row change."""
        self.calls.append(("update_instance_selection", index, checked, profile))
        return ActionResult(True)

    def set_emulator(self, emulator, emulator_paths=None):
        """Record an emulator change."""
        self.calls.append(("set_emulator", emulator))
        return ActionResult(True)

    def save_preferences(self, values):
        """Record a preferences save."""
        self.calls.append(("save_preferences", values))
        return ActionResult(True)

    def save_profile(self, name, task_ids):
        """Record a profile save."""
        self.calls.append(("save_profile", name, task_ids))
        return ActionResult(True)

    def delete_profile(self, name):
        """Record a profile deletion."""
        self.calls.append(("delete_profile", name))
        return ActionResult(True)

    def ack_requirements_reminder(self):
        """Record the first-run acknowledgement."""
        self.calls.append(("ack_requirements_reminder",))
        return ActionResult(True)


class ServerTestCase(unittest.TestCase):
    """Base case with a running server and a temporary bundle."""

    TOKEN = "test-token"

    def setUp(self):
        """Create a bundle and start the server on a free port."""
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)
        bundle_dir = self.base / "bundle"
        (bundle_dir / "_astro").mkdir(parents=True)
        (bundle_dir / "index.html").write_text('<html><template id="instance-template"></template></html>', encoding="utf-8")
        (bundle_dir / "_astro" / "app.css").write_text("body{}", encoding="utf-8")
        (self.base / "secret.txt").write_text("secret", encoding="utf-8")

        self.service = FakeService()
        self.server = AppServer(
            self.service,
            bundle=load_ui_bundle(bundle_dir),
            startup={"requirements_reminder": True},
            token=self.TOKEN,
        )
        self.server.start()

    def tearDown(self):
        """Stop the server and remove the bundle."""
        self.server.stop()
        self.server.wait(3)
        self._temp.cleanup()

    def _url(self, path):
        """Build an absolute URL for the running server."""
        return f"http://127.0.0.1:{self.server.port}{path}"

    def _get(self, path, token=True):
        """Perform a GET and return status, body and headers."""
        headers = {"X-WosUtil-Token": self.TOKEN} if token else {}
        request = urllib.request.Request(self._url(path), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def _post(self, path, payload, token=True):
        """Perform a JSON POST and return status, body and headers."""
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-WosUtil-Token"] = self.TOKEN
        request = urllib.request.Request(self._url(path), data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def test_index_requires_the_token_and_sets_the_cookie(self):
        """The token is required once and then kept in a cookie."""
        status, _body, _headers = self._get("/", token=False)
        self.assertEqual(status, 403)

        status, body, headers = self._get(f"/?t={self.TOKEN}", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"instance-template", body)
        self.assertIn("wosutil_token=", headers.get("Set-Cookie", ""))

    def test_static_assets_are_served(self):
        """Assets are served from the bundle without the API token."""
        status, body, _headers = self._get("/_astro/app.css", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"body{}")

    def test_traversal_is_rejected(self):
        """An encoded traversal cannot read a file next to the bundle."""
        status, _body, _headers = self._get("/%2e%2e/secret.txt")
        self.assertEqual(status, 404)

    def test_state_requires_the_token(self):
        """The snapshot is not exposed without the token."""
        status, _body, _headers = self._get("/api/state", token=False)
        self.assertEqual(status, 403)

        status, body, _headers = self._get("/api/state")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["apiVersion"], 1)
        self.assertTrue(payload["startup"]["requirements_reminder"])

    def test_commands_are_dispatched(self):
        """A valid command reaches the service and returns ok."""
        status, body, _headers = self._post("/api/command", {"command": "set_max_instances", "args": {"value": 4}})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertIn(("set_max_instances", 4), self.service.calls)

    def test_unknown_commands_return_an_error(self):
        """Unknown commands are rejected without touching the service."""
        status, body, _headers = self._post("/api/command", {"command": "drop_everything", "args": {}})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertIn("Unknown command", payload["error"])

    def test_health_is_public(self):
        """Health probes do not need a token."""
        status, body, _headers = self._get("/api/health", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["apiVersion"], 1)

    def test_non_ascii_tokens_are_rejected(self):
        """A malformed non-ASCII token is refused instead of raising."""
        request = urllib.request.Request(self._url("/api/state"), headers={"X-WosUtil-Token": "tökén"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 403)

    def test_events_stream_starts_with_state_and_history(self):
        """The SSE connection receives the current state before the log tail."""
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            connection.request("GET", f"/api/events?t={self.TOKEN}")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.fp.readline().decode().strip(), "event: state")
            data_line = response.fp.readline().decode().strip()
            self.assertTrue(data_line.startswith("data: "))
            self.assertEqual(json.loads(data_line[6:])["apiVersion"], 1)
            self.assertEqual(response.fp.readline().decode().strip(), "")
            self.assertEqual(response.fp.readline().decode().strip(), "event: logs")
        finally:
            connection.close()
