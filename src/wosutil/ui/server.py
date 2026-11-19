"""Local HTTP server that serves the desktop UI and its JSON API."""

import contextlib
import json
import mimetypes
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional, cast
from urllib.parse import parse_qs, urlsplit

from wosutil.app.actions import ActionResult
from wosutil.app.preferences import (
    EMULATOR_LABELS,
    EMULATOR_PATH_FIELDS,
    GATHER_RESOURCE_LABELS,
    INTEL_MODE_LABELS,
    MYSTERY_SHOP_LEVEL_LABELS,
)
from wosutil.app.templates import TemplateError, TemplateService
from wosutil.preferences import (
    BEAR_RALLY_CALL_MARCH_MAX,
    BEAR_RALLY_CALL_MARCH_MIN,
    BEAR_TRAP_MARCH_COUNT,
    BEAR_TRAP_MARCH_MAX,
    BEAR_TRAP_MARCH_MIN,
    GATHER_RESOURCES,
    INTEL_MODES,
    KILL_BEAST_MARCH_MAX,
    KILL_BEAST_MARCH_MIN,
    MYSTERY_SHOP_LEVELS,
)
from wosutil.ui.bundle import UiBundle, safe_join
from wosutil.ui.contract import API_VERSION
from wosutil.ui.picker import pick_path

mimetypes.add_type("font/woff2", ".woff2")

HEARTBEAT_TIMEOUT_SECONDS = 15.0
SUSPEND_GRACE_SECONDS = 4.0
WATCHDOG_INTERVAL_SECONDS = 2.0
SSE_POLL_SECONDS = 0.25
SSE_PING_SECONDS = 15.0
MAX_BODY_BYTES = 1_000_000

FALLBACK_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WoS Util</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #151311; color: #f4f0e6;
         font-family: ui-sans-serif, system-ui, "Segoe UI", sans-serif; }
  main { max-width: 32rem; padding: 32px; border: 1px solid rgba(255, 252, 246, .12); border-radius: 8px; background: #1d1a17; }
  h1 { margin: 0 0 12px; font-size: 24px; font-weight: 500; letter-spacing: -.01em; }
  p { margin: 8px 0; color: #b8b1a7; font-size: 14px; line-height: 1.5; }
  code { font-family: ui-monospace, Consolas, monospace; color: #f4f0e6; }
  a { color: #f4f0e6; }
</style>
</head>
<body>
  <main>
    <h1>WoS Util</h1>
    <p>The desktop interface bundle is not installed.</p>
    <p>Run <code>python scripts/fetch_ui.py</code> and restart the app.</p>
    <p><a href="/api/health">API status</a></p>
  </main>
  <script>
    const token = new URLSearchParams(location.search).get('t') || ''
    setInterval(() => {
      void fetch('/api/heartbeat?t=' + encodeURIComponent(token), { method: 'POST' }).catch(() => {})
    }, 5000)
    window.addEventListener('pagehide', () => {
      navigator.sendBeacon('/api/suspend?t=' + encodeURIComponent(token))
    })
  </script>
</body>
</html>
"""

CommandHandler = Callable[[Any, dict], ActionResult]


def _require_int(args: dict, key: str) -> int:
    """Return an integer argument or raise a user-facing error."""
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"'{key}' must be an integer.")
    return value


def _command_set_emulator(service, args: dict) -> ActionResult:
    """Validate the emulator code before touching the saved preferences."""
    emulator = args.get("emulator")
    if emulator not in EMULATOR_LABELS:
        raise ValueError("Unknown emulator.")
    return service.set_emulator(emulator)


def _command_update_selection(service, args: dict) -> ActionResult:
    """Apply one instance row change."""
    checked = args.get("checked")
    if not isinstance(checked, bool):
        raise ValueError("'checked' must be a boolean.")
    return service.update_instance_selection(_require_int(args, "index"), checked, str(args.get("profile") or ""))


def _command_save_preferences(service, args: dict) -> ActionResult:
    """Persist a preferences form payload."""
    values = args.get("values")
    if not isinstance(values, dict):
        raise ValueError("'values' must be an object.")
    return service.save_preferences(values)


def _command_save_profile(service, args: dict) -> ActionResult:
    """Persist a profile from its name and selected task ids."""
    name = args.get("name")
    task_ids = args.get("task_ids")
    if not isinstance(name, str):
        raise ValueError("'name' must be a string.")
    if not isinstance(task_ids, list):
        raise ValueError("'task_ids' must be an array.")
    return service.save_profile(name, [str(task_id) for task_id in task_ids])


def _command_delete_profile(service, args: dict) -> ActionResult:
    """Delete a profile by name."""
    name = args.get("name")
    if not isinstance(name, str):
        raise ValueError("'name' must be a string.")
    return service.delete_profile(name)


_COMMANDS: dict[str, CommandHandler] = {
    "start_tool": lambda service, _args: service.start_tool(),
    "stop_tool": lambda service, _args: service.stop_tool(),
    "refresh_instances": lambda service, _args: service.refresh_instances(force=True),
    "set_max_instances": lambda service, args: service.set_max_instances(_require_int(args, "value")),
    "update_selection": _command_update_selection,
    "set_emulator": _command_set_emulator,
    "save_preferences": _command_save_preferences,
    "save_profile": _command_save_profile,
    "delete_profile": _command_delete_profile,
    "ack_requirements_reminder": lambda service, _args: service.ack_requirements_reminder(),
}


class AppServer:
    """Serve the UI bundle and expose the application service over HTTP."""

    def __init__(
        self,
        service: Any,
        bundle: Optional[UiBundle] = None,
        startup: Optional[dict] = None,
        token: Optional[str] = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ):
        """Create a server bound to the loopback interface.

        Args:
            service: Application service exposing snapshots and commands.
            bundle: Validated UI bundle. The built-in fallback page is served
                when omitted.
            startup: First-run metadata included in the state payload.
            token: Per-run access token. A random one is generated by default.
            host: Interface to bind.
            port: Port to bind (0 lets the OS pick a free one).
        """
        self.service = service
        self.bundle = bundle
        self.startup = dict(startup or {})
        self.templates = TemplateService()
        self.token = token or secrets.token_urlsafe(32)
        self.host = host
        self._requested_port = port
        self._port = port

        self._httpd: Optional[_UiHttpServer] = None
        self._thread: Optional[threading.Thread] = None
        self._watchdog: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._stopped = threading.Event()
        self._stop_lock = threading.Lock()

        self._activity_lock = threading.Lock()
        self._last_activity = 0.0
        self._seen_request = False
        self._suspend_deadline: Optional[float] = None

    @property
    def port(self) -> int:
        """Return the bound port."""
        return self._port

    @property
    def url(self) -> str:
        """Return the URL the desktop window must open."""
        return f"http://{self.host}:{self._port}/?t={self.token}"

    @property
    def ui_version(self) -> str:
        """Return the served bundle version (``missing`` for the fallback)."""
        return self.bundle.ui_version if self.bundle is not None else "missing"

    @property
    def stopping(self) -> bool:
        """Return True once shutdown has started."""
        return self._stopping.is_set()

    def start(self) -> None:
        """Bind, start serving and start the liveness watchdog."""
        if self._httpd is not None:
            return
        self._httpd = _UiHttpServer((self.host, self._requested_port), self)
        self._port = int(self._httpd.server_address[1])
        self._last_activity = time.monotonic()
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="UiServer", daemon=True)
        self._thread.start()
        self._watchdog = threading.Thread(target=self._watchdog_loop, name="UiWatchdog", daemon=True)
        self._watchdog.start()

    def stop(self) -> None:
        """Stop accepting connections and release the port."""
        with self._stop_lock:
            if self._stopping.is_set():
                self._stopped.set()
                return
            self._stopping.set()

        httpd = self._httpd
        if httpd is not None:
            if self._thread is not None and self._thread.is_alive():
                httpd.shutdown()
            httpd.server_close()
        self._stopped.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the server stops.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            bool: True when the server stopped before the timeout.
        """
        return self._stopped.wait(timeout)

    def touch(self) -> None:
        """Record activity from a request and arm the heartbeat watchdog."""
        with self._activity_lock:
            self._last_activity = time.monotonic()
            self._seen_request = True

    def suspend(self) -> None:
        """Start the short grace period used when the page unloads."""
        with self._activity_lock:
            self._last_activity = time.monotonic()
            self._suspend_deadline = time.monotonic() + SUSPEND_GRACE_SECONDS

    def resume(self) -> None:
        """Cancel a pending suspend (the page was reloaded)."""
        with self._activity_lock:
            self._suspend_deadline = None
            self._last_activity = time.monotonic()

    def state_payload(self) -> dict:
        """Return the snapshot plus the contract and startup metadata."""
        payload = self.service.snapshot()
        payload["apiVersion"] = API_VERSION
        payload["startup"] = dict(self.startup)
        payload["ui"] = {"version": self.ui_version, "apiVersion": API_VERSION}
        return payload

    def preferences_payload(self) -> dict:
        """Return the preferences form data: values, tasks, labels and bounds."""
        snapshot = self.service.snapshot()
        return {
            "preferences": self.service.preferences(),
            "tasks": self.service.task_catalog(),
            "emulators": snapshot["emulator_options"],
            "activeEmulator": snapshot["emulator"],
            "labels": {
                "gather": GATHER_RESOURCE_LABELS,
                "intel": INTEL_MODE_LABELS,
                "mysteryShop": MYSTERY_SHOP_LEVEL_LABELS,
                "pathFields": {code: [{"key": key, "label": label, "kind": kind} for key, label, kind in fields] for code, fields in EMULATOR_PATH_FIELDS.items()},
            },
            "options": {
                "gather": list(GATHER_RESOURCES),
                "intel": list(INTEL_MODES),
                "mysteryShop": list(MYSTERY_SHOP_LEVELS),
            },
            "bounds": {
                "killBeastMarch": [KILL_BEAST_MARCH_MIN, KILL_BEAST_MARCH_MAX],
                "bearTrapMarch": [BEAR_TRAP_MARCH_MIN, BEAR_TRAP_MARCH_MAX],
                "bearTrapCount": BEAR_TRAP_MARCH_COUNT,
                "bearRallyCallMarch": [BEAR_RALLY_CALL_MARCH_MIN, BEAR_RALLY_CALL_MARCH_MAX],
            },
        }

    def dispatch(self, payload: dict) -> dict:
        """Run one command from the UI.

        Args:
            payload: ``{"command": str, "args": dict}``.

        Returns:
            dict: ``{"ok": bool, "error": str | None}``.
        """
        command = payload.get("command")
        args = payload.get("args")
        if not isinstance(command, str) or not isinstance(args, dict):
            return {"ok": False, "error": "Invalid command payload."}
        handler = _COMMANDS.get(command)
        if handler is None:
            return {"ok": False, "error": f"Unknown command: {command}."}
        try:
            result = handler(self.service, args)
        except Exception as exc:  # The API boundary turns failures into a view-level error.
            return {"ok": False, "error": str(exc)}
        return {"ok": bool(result.ok), "error": result.error}

    def _watchdog_loop(self) -> None:
        """Close the app when the page is gone or was closed without a close call."""
        while not self._stopping.wait(WATCHDOG_INTERVAL_SECONDS):
            now = time.monotonic()
            with self._activity_lock:
                suspend_deadline = self._suspend_deadline
                last_activity = self._last_activity
                seen_request = self._seen_request
            if suspend_deadline is not None and now >= suspend_deadline:
                self.stop()
                return
            if seen_request and now - last_activity > HEARTBEAT_TIMEOUT_SECONDS:
                self.stop()
                return


class _UiHttpServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying the application server instance."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple, app: AppServer):
        """Store the app server and bind the socket."""
        self.app = app
        super().__init__(address, _UiRequestHandler)


class _UiRequestHandler(BaseHTTPRequestHandler):
    """Route static files, API calls and the SSE stream."""

    protocol_version = "HTTP/1.1"
    server_version = "WosUtil"
    sys_version = ""

    @property
    def app(self) -> AppServer:
        """Return the owning application server."""
        return cast("_UiHttpServer", self.server).app

    def log_message(self, format, *args):  # noqa: A002
        """Keep the console free of one line per static asset request."""

    def do_GET(self):  # noqa: N802
        """Serve the index, static assets and read-only API endpoints."""
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        path = parts.path

        if path == "/api/health":
            self._json({"ok": True, "apiVersion": API_VERSION, "uiVersion": self.app.ui_version})
            return

        if path == "/api/state":
            if not self._authorized(query):
                return
            self.app.touch()
            self._json(self.app.state_payload())
            return

        if path == "/api/logs":
            if not self._authorized(query):
                return
            self.app.touch()
            try:
                after = int((query.get("after") or ["0"])[0])
            except ValueError:
                after = 0
            self._json({"entries": self.app.service.logs_after(after)})
            return

        if path == "/api/preferences":
            if not self._authorized(query):
                return
            self.app.touch()
            self._json(self.app.preferences_payload())
            return

        if path == "/api/template/state":
            if not self._authorized(query):
                return
            self.app.touch()
            self._json({"ok": True, "image": self.app.templates.info()})
            return

        if path == "/api/template/image":
            if not self._authorized(query):
                return
            self.app.touch()
            try:
                data = self.app.templates.png()
            except TemplateError as exc:
                self._json({"ok": False, "error": str(exc)}, status=404)
                return
            self._send_bytes(data, "image/png", "no-store")
            return

        if path == "/api/events":
            if not self._authorized(query):
                return
            self.app.touch()
            self._serve_events()
            return

        if path == "/":
            if not self._authorized(query):
                return
            self.app.touch()
            self._serve_index()
            return

        self._serve_static(path)

    def do_POST(self):  # noqa: N802
        """Handle beacons and JSON commands."""
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        path = parts.path
        if not self._authorized(query):
            # Drain the body even on auth failures, otherwise the leftover
            # bytes desynchronize the next request on this keep-alive socket.
            self._discard_body()
            return
        self.app.touch()

        if path == "/api/command":
            payload = self._read_json()
            if payload is None:
                return
            self._json(self.app.dispatch(payload))
            return

        if path == "/api/pick-path":
            payload = self._read_json()
            if payload is None:
                return
            kind = payload.get("kind")
            if kind not in {"directory", "file"}:
                self._json({"ok": False, "error": "'kind' must be 'directory' or 'file'."})
                return
            try:
                selected = pick_path(kind, str(payload.get("title") or ""), str(payload.get("initial") or ""))
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)})
                return
            self._json({"ok": True, "path": selected})
            return

        if path == "/api/template/capture":
            payload = self._read_json()
            if payload is None:
                return
            try:
                info = self.app.templates.capture(_require_int(payload, "index"))
            except (TemplateError, ValueError) as exc:
                self._json({"ok": False, "error": str(exc)})
                return
            self._json({"ok": True, "image": info})
            return

        if path == "/api/template/load":
            payload = self._read_json()
            if payload is None:
                return
            try:
                info = self.app.templates.load(str(payload.get("path") or ""))
            except TemplateError as exc:
                self._json({"ok": False, "error": str(exc)})
                return
            self._json({"ok": True, "image": info})
            return

        if path == "/api/template/save":
            payload = self._read_json()
            if payload is None:
                return
            try:
                saved = self.app.templates.save_crop(payload.get("roi"), str(payload.get("path") or ""))
            except TemplateError as exc:
                self._json({"ok": False, "error": str(exc)})
                return
            self._json({"ok": True, "path": saved})
            return

        if path == "/api/template/save-full":
            payload = self._read_json()
            if payload is None:
                return
            try:
                saved = self.app.templates.save_full(str(payload.get("path") or ""))
            except TemplateError as exc:
                self._json({"ok": False, "error": str(exc)})
                return
            self._json({"ok": True, "path": saved})
            return

        if path == "/api/template/copy":
            payload = self._read_json()
            if payload is None:
                return
            try:
                self.app.templates.copy(str(payload.get("text") or ""))
            except TemplateError as exc:
                self._json({"ok": False, "error": str(exc)})
                return
            self._json({"ok": True})
            return

        self._discard_body()

        if path == "/api/heartbeat":
            self.app.resume()
            self._json({"ok": True})
            return

        if path == "/api/resume":
            self.app.resume()
            self._json({"ok": True})
            return

        if path == "/api/suspend":
            self.app.suspend()
            self._json({"ok": True})
            return

        if path == "/api/close":
            self._json({"ok": True})
            threading.Thread(target=self.app.stop, name="UiStop", daemon=True).start()
            return

        self._json({"ok": False, "error": "Not found."}, status=404)

    def _authorized(self, query: dict) -> bool:
        """Check the header, cookie or query token."""
        token = self.headers.get("X-WosUtil-Token") or self._cookie_token() or (query.get("t") or [""])[0]
        try:
            authorized = bool(token) and secrets.compare_digest(token, self.app.token)
        except TypeError:
            # compare_digest only accepts ASCII strings; a malformed token is
            # not authorized.
            authorized = False
        if authorized:
            return True
        self._json({"ok": False, "error": "Unauthorized."}, status=403)
        return False

    def _cookie_token(self) -> str:
        """Return the token stored in the request cookie, if any."""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "wosutil_token":
                return value
        return ""

    def _serve_index(self) -> None:
        """Serve the bundle index, or the built-in fallback page."""
        if self.app.bundle is None:
            self._send_bytes(FALLBACK_HTML.encode("utf-8"), "text/html; charset=utf-8", "no-store", set_cookie=True)
            return
        self._send_file(self.app.bundle.path / "index.html", set_cookie=True)

    def _serve_static(self, path: str) -> None:
        """Serve a file inside the bundle directory."""
        if self.app.bundle is None:
            self._json({"ok": False, "error": "Not found."}, status=404)
            return
        file_path = safe_join(self.app.bundle.path, path)
        if file_path is None:
            self._json({"ok": False, "error": "Not found."}, status=404)
            return
        self.app.touch()
        self._send_file(file_path)

    def _serve_events(self) -> None:
        """Stream state, log and dialog events until the client disconnects."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        subscription = self.app.service.subscribe()
        try:
            self._write_event("state", self.app.state_payload())
            self._write_event("logs", {"entries": self.app.service.logs(300)})
            last_ping = time.monotonic()
            while not self.app.stopping:
                events = subscription.drain()
                for event in events:
                    self._write_event(event.type, event.data)
                now = time.monotonic()
                if not events and now - last_ping >= SSE_PING_SECONDS:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = now
                if not events:
                    time.sleep(SSE_POLL_SECONDS)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.service.unsubscribe(subscription)

    def _write_event(self, event_type: str, data: dict) -> None:
        """Write one SSE frame."""
        payload = json.dumps(data, ensure_ascii=False)
        self.wfile.write(f"event: {event_type}\ndata: {payload}\n\n".encode())
        self.wfile.flush()

    def _read_json(self) -> Optional[dict]:
        """Read and decode the JSON request body."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json({"ok": False, "error": "Invalid Content-Length."}, status=400)
            return None
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            self._json({"ok": False, "error": "Request too large."}, status=413)
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._json({"ok": False, "error": "Invalid JSON body."}, status=400)
            return None
        if not isinstance(payload, dict):
            self._json({"ok": False, "error": "Expected a JSON object."}, status=400)
            return None
        return payload

    def _discard_body(self) -> None:
        """Consume an unread request body so keep-alive stays in sync."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            return
        with contextlib.suppress(OSError):
            self.rfile.read(length)

    def _json(self, payload: dict, status: int = 200) -> None:
        """Send a JSON response."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(data)

    def _send_file(self, path: Path, set_cookie: bool = False) -> None:
        """Send a file from the bundle."""
        try:
            data = path.read_bytes()
        except OSError:
            self._json({"ok": False, "error": "Not found."}, status=404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json", "image/svg+xml"}:
            content_type += "; charset=utf-8"
        cache = "public, max-age=31536000, immutable" if "_astro" in path.parts else "no-store"
        self._send_bytes(data, content_type, cache, set_cookie=set_cookie)

    def _send_bytes(self, data: bytes, content_type: str, cache_control: str, set_cookie: bool = False) -> None:
        """Send a byte response with the bundle headers."""
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if set_cookie:
            self.send_header("Set-Cookie", f"wosutil_token={self.app.token}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(data)
