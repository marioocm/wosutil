"""Paste the host clipboard into the focused Waydroid instance.

Gamescope does not bridge the host Wayland clipboard to Waydroid, and Android
has its own clipboard, so Ctrl+V inside the instance sees nothing. This helper
bypasses both: it reads the host clipboard and types the text into the focused
Android field through ADB.

Usage:
    wosutil-paste
    WosUtil --paste
"""

import contextlib
import os
import shutil
import tempfile

from wosutil.emulator.backends import EMULATOR_WAYDROID, create_backend
from wosutil.utils import run_process_robust

REMOTE_PASTE_FILE = "/data/local/tmp/wosutil_paste.txt"


def read_host_clipboard():
    """Return the host clipboard text, or an empty string when unavailable.

    Klipper (KDE) exposes the current clipboard over DBus even when no app
    owns the selection; GTK and pyperclip are used as fallbacks on other
    desktops.
    """
    for name in ("qdbus6", "qdbus"):
        qdbus = shutil.which(name)
        if qdbus is None:
            continue
        result = run_process_robust([qdbus, "org.kde.klipper", "/klipper", "getClipboardContents"], timeout=5)
        if result is not None and result.returncode == 0 and result.stdout.strip():
            return result.stdout.rstrip("\r\n")

    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, Gtk

        Gtk.init([])
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        text = clipboard.wait_for_text()
        if text:
            return text
    except Exception:
        pass

    try:
        import pyperclip

        return pyperclip.paste() or ""
    except Exception:
        return ""


def normalize_text(text):
    """Return single-line text: ``input text`` cannot type line breaks."""
    return " ".join(line.strip() for line in str(text).replace("\r\n", "\n").split("\n")).strip()


def active_window_pid():
    """Return the PID of the focused window, or None when kdotool is missing."""
    kdotool = shutil.which("kdotool")
    if kdotool is None:
        return None
    window = run_process_robust([kdotool, "getactivewindow"], timeout=5)
    if window is None or window.returncode != 0 or not window.stdout.strip():
        return None
    pid = run_process_robust([kdotool, "getwindowpid", window.stdout.strip()], timeout=5)
    if pid is None or pid.returncode != 0:
        return None
    try:
        return int(pid.stdout.strip())
    except ValueError:
        return None


def find_instance_for_pid(backend, pid):
    """Return the Waydroid instance whose gamescope window owns ``pid``."""
    if pid is None:
        return None
    for instance in backend._instances:
        for process in backend._matching_processes(instance["index"]):
            if process.pid == pid:
                return instance["index"]
    return None


def running_instances(backend):
    """Return the indices of the instances with a live gamescope window."""
    return [instance["index"] for instance in backend._instances if backend._matching_processes(instance["index"])]


def paste_text(backend, instance_index, text):
    """Type ``text`` into the focused field of an instance through ADB.

    The text is pushed to a temporary file and expanded on the device, which
    keeps spaces and shell characters intact (the plain ``input text`` form
    splits or interprets them).

    Returns:
        bool: True when ADB accepted the text.
    """
    text = normalize_text(text)
    if not text:
        return False
    serial = backend.get_serial(instance_index)
    if not serial:
        return False

    local_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as paste_file:
            paste_file.write(text)
            local_path = paste_file.name
        adb = backend.adb.adb_path
        pushed = run_process_robust([adb, "-s", serial, "push", local_path, REMOTE_PASTE_FILE], timeout=20)
        if pushed is None or pushed.returncode != 0:
            return False
        # The remote shell expands $(cat ...) inside double quotes, so spaces
        # and punctuation reach `input text` as one literal argument.
        result = run_process_robust([adb, "-s", serial, "shell", f'input text "$(cat {REMOTE_PASTE_FILE})"'], timeout=20)
        return bool(result is not None and result.returncode == 0)
    finally:
        with contextlib.suppress(OSError):
            if local_path:
                os.remove(local_path)
        if serial:
            run_process_robust([backend.adb.adb_path, "-s", serial, "shell", "rm", "-f", REMOTE_PASTE_FILE], timeout=10)


def notify(title, message):
    """Show a desktop notification when notify-send is available."""
    notify_send = shutil.which("notify-send")
    if notify_send:
        run_process_robust([notify_send, "-a", title, title, message], timeout=5)


def main():
    """Paste the host clipboard into the active (or only running) instance."""
    text = normalize_text(read_host_clipboard())
    if not text:
        notify("WoS Util", "El portapapeles del host está vacío.")
        return 1

    backend = create_backend(EMULATOR_WAYDROID)
    pid = active_window_pid()
    instance_index = find_instance_for_pid(backend, pid)
    if instance_index is None:
        running = running_instances(backend)
        if len(running) != 1:
            notify("WoS Util", "Activa la ventana de la instancia de Waydroid antes de pegar.")
            return 1
        instance_index = running[0]

    name = next((instance["name"] for instance in backend._instances if instance["index"] == instance_index), str(instance_index))
    if paste_text(backend, instance_index, text):
        notify("WoS Util", f"Texto pegado en {name}.")
        return 0
    notify("WoS Util", f"No se pudo pegar el texto en {name}.")
    return 1
