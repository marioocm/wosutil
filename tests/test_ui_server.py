"""Tests for the local UI HTTP server."""

import http.client
import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image

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
        return {
            "instances": [],
            "profiles": {},
            "tool_running": False,
            "max_instances": 2,
            "emulator": "mumu",
            "emulator_options": [{"code": "mumu", "label": "MuMu Player", "installed": True}],
        }

    def preferences(self):
        """Return a preferences view."""
        return {"emulator": "mumu", "emulator_paths": {"mumu": {"base_path": "C:/MuMu"}}}

    def task_catalog(self):
        """Return one task."""
        return [{"id": "a", "name": "Task A", "description": "", "priority": 1}]

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
        self.templates_dir = self.base / "templates"
        self.templates_dir.mkdir()
        self._template_patch = patch("wosutil.app.templates.TEMPLATES_WRITE_DIR", str(self.templates_dir))
        self._template_patch.start()
        self.addCleanup(self._template_patch.stop)

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

    def test_url_for_builds_bundle_page_urls(self):
        """The template window can address a page inside the bundle."""
        self.assertEqual(self.server.url_for("/"), self.server.url)
        self.assertTrue(self.server.url_for("templates/index.html").endswith(f"/templates/index.html?t={self.TOKEN}"))

    def test_health_is_public(self):
        """Health probes do not need a token."""
        status, body, _headers = self._get("/api/health", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["apiVersion"], 1)

    def test_preferences_expose_labels_bounds_and_tasks(self):
        """The form gets every label and bound from the Python side."""
        status, body, _headers = self._get("/api/preferences")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["options"]["gather"][0], "meat")
        self.assertEqual(payload["bounds"]["bearTrapCount"], 6)
        self.assertIn("mumu", payload["labels"]["pathFields"])
        self.assertEqual(payload["tasks"][0]["id"], "a")
        self.assertEqual(payload["activeEmulator"], "mumu")

    def test_pick_path_returns_the_native_selection(self):
        """The native picker result is forwarded to the UI."""
        with patch("wosutil.ui.server.pick_path", return_value=r"C:\Games") as picker:
            status, body, _headers = self._post("/api/pick-path", {"kind": "directory", "title": "Folder"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True, "path": r"C:\Games"})
        picker.assert_called_once_with("directory", "Folder", "")

    def test_pick_path_rejects_unknown_kinds(self):
        """Only directory and file pickers are available."""
        status, body, _headers = self._post("/api/pick-path", {"kind": "whatever"})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["ok"])

    def test_template_loads_serves_and_saves_an_image(self):
        """The template endpoints load, stream and crop the current image."""
        source = self.base / "source.png"
        Image.new("RGB", (40, 30), (10, 20, 30)).save(source)

        status, body, _headers = self._post("/api/template/load", {"path": str(source)})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual((payload["image"]["width"], payload["image"]["height"]), (40, 30))

        status, body, headers = self._get(f"/api/template/image?rev=1&t={self.TOKEN}", token=False)
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x89PNG"))

        status, body, _headers = self._post("/api/template/save", {"path": "crop", "roi": [0, 0, 10, 10]})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        saved = Path(payload["path"])
        self.assertTrue(saved.is_file())
        with Image.open(saved) as image:
            self.assertEqual(image.size, (10, 10))

    def test_query_token_wins_over_a_stale_cookie(self):
        """Another server's cookie must not reject an image with its own token.

        The main shell and the Template Creator share the WebView2 profile, so
        the ``wosutil_token`` cookie can name a different server. The image
        request carries the correct token in the URL and must be served.
        """
        source = self.base / "source.png"
        Image.new("RGB", (40, 30), (10, 20, 30)).save(source)
        self._post("/api/template/load", {"path": str(source)})

        request = urllib.request.Request(
            self._url(f"/api/template/image?rev=1&t={self.TOKEN}"),
            headers={"Cookie": "wosutil_token=stale-token-from-the-main-shell"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(response.read().startswith(b"\x89PNG"))

    def test_cookie_token_authenticates_when_the_url_has_none(self):
        """The cookie remains the fallback for requests without an explicit token."""
        request = urllib.request.Request(self._url("/api/state"), headers={"Cookie": f"wosutil_token={self.TOKEN}"})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)

    def test_non_ascii_tokens_are_rejected(self):
        """A malformed non-ASCII token is refused instead of raising."""
        request = urllib.request.Request(self._url("/api/state"), headers={"X-WosUtil-Token": "tökén"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 403)

    def test_post_bodies_do_not_desync_keep_alive(self):
        """A POST body the endpoint ignores must be consumed before the next request."""
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            connection.request(
                "POST",
                f"/api/heartbeat?t={self.TOKEN}",
                body="{}",
                headers={"Content-Type": "application/json", "X-WosUtil-Token": self.TOKEN},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()

            connection.request("GET", "/api/preferences", headers={"X-WosUtil-Token": self.TOKEN})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        finally:
            connection.close()

    def test_unauthorized_post_bodies_are_drained(self):
        """An unauthorized body must not poison the next request either."""
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            connection.request("POST", "/api/heartbeat", body="{}", headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()

            connection.request("GET", "/api/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        finally:
            connection.close()

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

    def test_watchdog_stops_the_server_when_heartbeats_stop(self):
        """A page that never answers again is treated as gone."""
        self.server.touch()
        self.assertTrue(self.server.check_watchdog(now=time.monotonic() + 3600))
        self.assertTrue(self.server.wait(1))

    def test_watchdog_is_ignored_while_a_native_window_owns_the_app(self):
        """Minimized native windows must not be closed for missing heartbeats."""
        self.server.set_watchdog_enabled(False)
        self.server.touch()
        self.server.suspend()

        self.assertFalse(self.server.check_watchdog(now=time.monotonic() + 3600))
        self.assertFalse(self.server.wait(0.1))

        self.server.set_watchdog_enabled(True)
        self.assertTrue(self.server.check_watchdog(now=time.monotonic() + 3600))
