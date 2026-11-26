"""Emulator backend abstraction.

Each backend owns the emulator-specific details (instance discovery,
start/stop and ADB addressing) behind one small interface, so the automation
pipeline can run against MuMu Player, BlueStacks 5 or LDPlayer unchanged.

An instance is always addressed by an integer ``instance_index`` that is
stable for the selected emulator.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod

import psutil

from wosutil.adb import AdbClient
from wosutil.config import (
    ADB_PORT,
    ADB_PORT_STEP,
    BLUESTACKS_ADB_PATH,
    BLUESTACKS_BASE_PATH,
    BLUESTACKS_CONF,
    BLUESTACKS_HD_PLAYER_PATH,
    LDPLAYER_ADB_PATH,
    LDPLAYER_ADB_PORT,
    LDPLAYER_ADB_PORT_STEP,
    LDPLAYER_BASE_PATH,
    LDPLAYER_CONSOLE_PATH,
    LDPLAYER_INSTANCE_CONFIG_DIR,
    LDPLAYER_PLAYER_PATH,
    LOG_DIR,
    MUMU_ADB_PATH,
    MUMU_BASE_PATH,
    MUMU_INSTANCE_BASE_PATH,
    MUMU_MULTI_PLAYER_PATH,
    WAYDROID_ADB_PATH,
    WAYDROID_ADB_PORT,
    WAYDROID_ANDROID_HEIGHT,
    WAYDROID_ANDROID_WIDTH,
    WAYDROID_BASE_DIR,
    WAYDROID_BOOT_TIMEOUT_SECONDS,
    WAYDROID_LEASE_DIR,
    WAYDROID_NAMES_FILE,
    WAYDROID_WINDOW_FILE,
    WAYDROID_WINDOW_HEIGHT,
    WAYDROID_WINDOW_STEP,
    WAYDROID_WINDOW_TOP,
    WAYDROID_WINDOW_WIDTH,
)
from wosutil.emulator.window_utils import minimize_process_windows, minimize_windows_by_title
from wosutil.utils import (
    _detached_creation_flags,
    _has_process_option,
    _is_process_named,
    _iter_processes,
    _minimized_startupinfo,
    ensure_directory_exists,
    load_json_file,
    run_process_robust,
    save_json_file,
)

logger = logging.getLogger(__name__)

# Backend identifiers used in preferences and the GUI.
EMULATOR_MUMU = "mumu"
EMULATOR_BLUESTACKS = "bluestacks"
EMULATOR_LDPLAYER = "ldplayer"
EMULATOR_WAYDROID = "waydroid"

# Instance lifecycle polling. Emulators boot/close asynchronously, so both
# operations confirm the result by polling their processes.
POLL_SECONDS = 2
START_CONFIRM_ATTEMPTS = 30
START_MINIMIZE_GRACE_SWEEPS = 2
STOP_CONFIRM_ATTEMPTS = 30
STOP_RETRY_EVERY = 5
KILL_CONFIRM_ATTEMPTS = 5

_BLUESTACKS_INSTANCE_PORT_RE = re.compile(r"^bst\.instance\.([^.]+)\.status\.adb_port$")
_LDPLAYER_CONFIG_RE = re.compile(r"^leidian(\d+)\.config$")
_MUMU_INSTANCE_FOLDER_RE = re.compile(r"^MuMuPlayerGlobal-[\d.]+-(\d+)$")
_MUMU_INSTANCE_SERIES_RE = re.compile(r"^MuMuPlayerGlobal-([\d.]+)-\d+$")
_WAYDROID_SECONDARY_RE = re.compile(r"^waydroid\.([A-Za-z][A-Za-z0-9_]*)$")

# Executable names whose windows belong to each emulator family (basename,
# extensionless, case-insensitive). Used to minimize windows that would
# otherwise steal the foreground focus.
MUMU_WINDOW_PROCESS_NAMES = (
    "MuMuNxDevice",
    "MuMuNxMain",
    "MuMuNxService",
    "MuMuNxLauncher",
    "MuMuPlayer",
    "NemuPlayer",
    "MuMuManager",
    "MuMuPlayerHomepage",
)
BLUESTACKS_WINDOW_PROCESS_NAMES = ("HD-Player",)
LDPLAYER_WINDOW_PROCESS_NAMES = ("dnplayer",)


def start_minimized_enabled():
    """Return whether emulator windows must be minimized on start/stop.

    Lazily imports the preference to avoid a circular import (preferences
    imports the backend identifiers from this module).
    """
    from wosutil.preferences import get_start_minimized

    return get_start_minimized()


def parse_bluestacks_conf(conf_path=BLUESTACKS_CONF):
    """Parse a ``bluestacks.conf`` file into a flat {key: value} dict.

    Args:
        conf_path: Path to the BlueStacks configuration file.

    Returns:
        dict: Flattened configuration, or an empty dict if the file is missing.
    """
    values = {}
    try:
        with open(conf_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                value = value.strip()
                if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                    value = value[1:-1]
                values[key.strip()] = value
    except OSError:
        return {}
    return values


def list_bluestacks_instances(conf_values=None):
    """Return the BlueStacks virtual devices described in a parsed conf.

    Args:
        conf_values (dict, optional): Parsed ``bluestacks.conf`` content. If
            None, the real config file is parsed.

    Returns:
        list: Sorted list of dicts with 'name', 'display_name' and 'adb_port'.
    """
    if conf_values is None:
        conf_values = parse_bluestacks_conf()
    instances = {}
    for key, value in conf_values.items():
        match = _BLUESTACKS_INSTANCE_PORT_RE.match(key)
        if not match:
            continue
        name = match.group(1)
        adb_port = conf_values.get(f"bst.instance.{name}.adb_port", value)
        if not adb_port.isdigit():
            adb_port = "5555"
        instances[name] = {
            "name": name,
            "display_name": conf_values.get(f"bst.instance.{name}.display_name", name),
            "adb_port": adb_port,
        }
    return sorted(instances.values(), key=lambda i: (int(i["adb_port"]), i["name"]))


def get_bluestacks_adb_access(conf_values=None):
    """Return True when BlueStacks exposes ADB to the outside world.

    Args:
        conf_values (dict, optional): Parsed ``bluestacks.conf``.

    Returns:
        bool: True if ``bst.enable_adb_access`` is "1", False otherwise.
    """
    if conf_values is None:
        conf_values = parse_bluestacks_conf()
    return conf_values.get("bst.enable_adb_access") == "1"


def parse_ldplayer_config(config_path):
    """Parse a LDPlayer instance config file (``leidianN.config``, JSON).

    Args:
        config_path (str): Path to the per-instance JSON config.

    Returns:
        dict: Raw config content, or an empty dict if the file is missing or
        not valid JSON.
    """
    try:
        with open(config_path, encoding="utf-8") as f:
            values = json.load(f)
            return values if isinstance(values, dict) else {}
    except (OSError, ValueError):
        return {}


def list_ldplayer_instances(config_dir=LDPLAYER_INSTANCE_CONFIG_DIR):
    """Return the LDPlayer instances described by their config files.

    Each instance has a ``leidianN.config`` file whose number N is its stable
    instance index (the one used by ``ldconsole launch/quit --index N``).

    Args:
        config_dir (str, optional): Directory holding the instance configs.

    Returns:
        list: Sorted list of dicts with 'key', 'index', 'display_name' and
        'adb_debug' (bool).
    """
    instances = []
    try:
        entries = os.listdir(config_dir)
    except OSError:
        return []
    for entry in entries:
        match = _LDPLAYER_CONFIG_RE.match(entry)
        if not match:
            continue
        values = parse_ldplayer_config(os.path.join(config_dir, entry))
        index = int(match.group(1))
        instances.append(
            {
                "key": f"leidian{index}",
                "index": index,
                "display_name": values.get("statusSettings.playerName", f"leidian{index}"),
                "adb_debug": values.get("basicSettings.adbDebug", 0) == 1,
            }
        )
    return sorted(instances, key=lambda inst: inst["index"])


def _read_waydroid_names(names_path):
    """Return the {instance_id: display_name} map stored by the Waydroid launchers.

    The file (``waydroid-instances.json``) is maintained by the local
    ``waydroid-install-launchers`` helper and maps the internal instance IDs
    (``default``, ``alt``, ...) to the visible names (``Mario``, ``Tokyo``).
    Both ``{"id": {"name": "Mario"}}`` and a flat ``{"id": "Mario"}`` shape are
    accepted so an older or hand-written file never breaks discovery.

    Args:
        names_path (str): Path to the JSON names file.

    Returns:
        dict: Mapping of instance ID to display name.
    """
    try:
        with open(names_path, encoding="utf-8") as names_file:
            values = json.load(names_file)
    except (OSError, ValueError):
        return {}
    if not isinstance(values, dict):
        return {}
    names = {}
    for instance_id, entry in values.items():
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip():
            names[str(instance_id)] = name.strip()
    return names


def list_waydroid_instances(base_dir=WAYDROID_BASE_DIR, names_path=WAYDROID_NAMES_FILE):
    """Return the Waydroid instances found under a data directory.

    A real instance always has a ``waydroid.cfg`` file; unrelated folders such
    as the ``waydroid-nvidia`` staging directory are ignored. The default
    instance (``/var/lib/waydroid``) is always listed first, followed by the
    additional ones sorted by their internal ID.

    Args:
        base_dir (str): Directory holding ``waydroid`` and ``waydroid.<id>``.
        names_path (str): Path to the visible-names JSON file.

    Returns:
        list: Instance dicts with 'id', 'path' and 'name' keys.
    """
    names = _read_waydroid_names(names_path)
    instances = []

    def add(instance_id, path):
        default_name = "Waydroid" if instance_id == "default" else f"Waydroid {instance_id}"
        instances.append({"id": instance_id, "path": path, "name": names.get(instance_id, default_name)})

    default_path = os.path.join(base_dir, "waydroid")
    if os.path.isfile(os.path.join(default_path, "waydroid.cfg")):
        add("default", default_path)
    try:
        entries = sorted(os.listdir(base_dir))
    except OSError:
        entries = []
    for entry in entries:
        match = _WAYDROID_SECONDARY_RE.fullmatch(entry)
        if not match:
            continue
        path = os.path.join(base_dir, entry)
        if os.path.isfile(os.path.join(path, "waydroid.cfg")):
            add(match.group(1), path)
    return instances


def waydroid_bridge(instance_id):
    """Return the network bridge name Waydroid uses for an instance.

    Args:
        instance_id (str): Internal instance ID ("default", "alt", ...).

    Returns:
        str: ``waydroid0`` for the default instance, ``waydroid-<id>`` otherwise.
    """
    return "waydroid0" if instance_id == "default" else f"waydroid-{instance_id}"


def parse_waydroid_leases(leases_path):
    """Return the most recent IPv4 address from a dnsmasq lease file.

    Waydroid's dnsmasq appends one line per lease:
    ``expiry mac ip hostname client-id``. When several leases are present, the
    one with the greatest expiry is the current address.

    Args:
        leases_path (str): Path to the ``dnsmasq.waydroid*.leases`` file.

    Returns:
        str: The IPv4 address, or an empty string when the file is missing or
        holds no usable lease.
    """
    best = None
    try:
        with open(leases_path, encoding="utf-8") as leases_file:
            for line in leases_file:
                parts = line.split()
                if len(parts) < 3:
                    continue
                try:
                    expiry = int(parts[0])
                except ValueError:
                    continue
                address = parts[2]
                if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", address):
                    continue
                if best is None or expiry > best[0]:
                    best = (expiry, address)
    except OSError:
        return ""
    return best[1] if best else ""


def platform_emulator_codes():
    """Return the emulator backends usable on the current platform.

    MuMu, BlueStacks and LDPlayer are Windows-only; Waydroid is the Linux
    backend. Other platforms have no working backend yet.

    Returns:
        tuple: Supported emulator codes, in preference order.
    """
    if sys.platform.startswith("win"):
        return (EMULATOR_MUMU, EMULATOR_BLUESTACKS, EMULATOR_LDPLAYER)
    if sys.platform.startswith("linux"):
        return (EMULATOR_WAYDROID,)
    return ()


def default_emulator_code():
    """Return the emulator used when none is installed or selected.

    Returns:
        str: The platform's first backend, or MuMu as a last-resort fallback.
    """
    codes = platform_emulator_codes()
    return codes[0] if codes else EMULATOR_MUMU


def _signal_processes(processes, action):
    """Terminate or kill each process, ignoring ones that already exited.

    Args:
        processes (iterable): psutil process objects.
        action (str): Method to call on each process ("terminate" or "kill").
    """
    for proc in processes:
        try:
            getattr(proc, action)()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _resolve_instance(instances, instance_index, emulator_name):
    """Return the instance dict of an emulator slot.

    Args:
        instances (list): Instance dicts with an 'index' key.
        instance_index (int): Instance slot to resolve.
        emulator_name (str): Backend name used in the error message.

    Returns:
        dict: The matching instance.

    Raises:
        ValueError: If the index does not belong to any instance.
    """
    for instance in instances:
        if instance["index"] == int(instance_index):
            return instance
    raise ValueError(f"Unknown {emulator_name} instance index: {instance_index}")


class EmulatorBackend(ABC):
    """Uniform interface implemented by every supported emulator."""

    name = ""
    window_process_names = ()
    # Whether a running instance can also be detected as an ADB device when
    # its launcher process is not visible (headless instances).
    adb_running_fallback = False

    def __init__(self, log_func=None, adb_path=None):
        """Initialize the backend with a logging function and ADB binary."""
        self.log = log_func or logger.info
        self.adb = AdbClient(adb_path)

    @property
    def adb_path(self):
        """Return the adb executable owned by this backend."""
        return self.adb.adb_path

    # --- Instance discovery ---

    @abstractmethod
    def list_instances(self):
        """Return the emulator instances as dicts with 'index' and 'name'."""

    def get_instances(self):
        """Return the emulator instances as dicts with 'index' and 'name'."""
        return self.list_instances()

    # --- Instance details (emulator specific) ---

    @abstractmethod
    def _matching_processes(self, instance_index):
        """Return the running processes that belong to the instance."""

    @abstractmethod
    def _display_name(self, instance_index):
        """Return the instance display name (its window title)."""

    @abstractmethod
    def _launch_argv(self, instance_index):
        """Return the argv that starts the instance (a long-lived process)."""

    @abstractmethod
    def get_serial(self, instance_index):
        """Return the ADB serial (host:port) of the given instance."""

    @abstractmethod
    def check_adb_access(self):
        """Return non-empty user-facing warnings about ADB access."""

    def _killable_processes(self, instance_index):
        """Return the processes the stop fallback may terminate directly."""
        return self._matching_processes(instance_index)

    def _is_instance_running(self, instance_index):
        """Return True when the instance processes or its ADB device are up."""
        if self._matching_processes(instance_index):
            return True
        if not self.adb_running_fallback:
            return False
        return self.adb.devices().get(self.get_serial(instance_index)) == "device"

    # --- Instance lifecycle ---

    def _graceful_stop(self, instance_index):
        """Ask the emulator to close the instance gracefully (optional hook)."""
        return False

    def _minimize_windows(self, enabled, title=None):
        """Minimize the emulator windows so they never steal the foreground.

        The window owner process can be elevated and unreadable, so the
        instance window title is matched as well when it is known.
        ``enabled`` is read once per lifecycle call so a polling loop never
        re-reads the preference file.
        """
        if not enabled:
            return
        minimize_process_windows(self.window_process_names, self.log)
        if title:
            minimize_windows_by_title((title,), self.log)

    def start_instance(self, instance_index):
        """Start an instance and wait for it to boot. Return True on success."""
        name = self._display_name(instance_index)
        minimize = start_minimized_enabled()
        self.log(f"Starting instance {name}...", "info")
        if self._is_instance_running(instance_index):
            self.log(f"Instance {name} is already running.", "info")
            self._minimize_windows(minimize, name)
            return True
        try:
            subprocess.Popen(
                self._launch_argv(instance_index),
                creationflags=_detached_creation_flags(),
                startupinfo=_minimized_startupinfo(),
            )
        except OSError as e:
            self.log(f"Could not launch instance {name}: {e}", "error")
            return False
        # Minimize right after launch and while the window appears, so it never
        # keeps the foreground focus.
        self._minimize_windows(minimize, name)
        for _ in range(START_CONFIRM_ATTEMPTS):
            if self._is_instance_running(instance_index):
                for _ in range(START_MINIMIZE_GRACE_SWEEPS):
                    self._minimize_windows(minimize, name)
                    time.sleep(POLL_SECONDS)
                self.log(f"Instance {name} started.", "success")
                return True
            self._minimize_windows(minimize, name)
            time.sleep(POLL_SECONDS)
        self.log(f"Could not confirm instance {name} started.", "error")
        return False

    def stop_instance(self, instance_index):
        """Stop an instance: graceful request first, then process termination.

        The graceful request (when the emulator provides one) is given time to
        close the VM cleanly before the instance processes are terminated
        directly. Killing a legacy MuMu VM process crashes its hypervisor
        service, so backends may exclude those processes from
        :meth:`_killable_processes`.

        Returns:
            bool: True when the instance is no longer running.
        """
        name = self._display_name(instance_index)
        minimize = start_minimized_enabled()
        self.log(f"Stopping instance {name}...", "info")
        stopped = True
        if self._is_instance_running(instance_index):
            graceful = self._graceful_stop(instance_index)
            if not (graceful and self._wait_until_stopped(instance_index, retry_graceful=True)):
                if graceful:
                    self.log(f"Graceful shutdown of instance {name} did not complete. Terminating processes...", "warning")
                stopped = self._terminate_matching_processes(instance_index)
                if not stopped:
                    self.log(f"Could not stop instance {name}.", "error")
            if stopped:
                self.log(f"Instance {name} stopped.", "success")
        else:
            self.log(f"Instance {name} is not running.", "info")
        # The emulator may leave helper windows behind when an instance closes.
        self._minimize_windows(minimize)
        return stopped

    def _wait_until_stopped(self, instance_index, attempts=STOP_CONFIRM_ATTEMPTS, retry_graceful=False):
        """Poll until the instance processes are gone or the attempts run out.

        Args:
            instance_index (int): Instance slot.
            attempts (int): Number of polls.
            retry_graceful (bool): Re-issue the graceful shutdown request every
                few polls for a guest that only answers a later request.

        Returns:
            bool: True when the instance processes are gone.
        """
        for attempt in range(attempts):
            if not self._matching_processes(instance_index):
                return True
            if retry_graceful and attempt and attempt % STOP_RETRY_EVERY == 0:
                self._graceful_stop(instance_index)
            time.sleep(POLL_SECONDS)
        return not self._matching_processes(instance_index)

    def _terminate_matching_processes(self, instance_index):
        """Terminate the instance processes, then kill any survivor."""
        _signal_processes(self._killable_processes(instance_index), "terminate")
        if self._wait_until_stopped(instance_index, KILL_CONFIRM_ATTEMPTS):
            return True
        _signal_processes(self._killable_processes(instance_index), "kill")
        return self._wait_until_stopped(instance_index, KILL_CONFIRM_ATTEMPTS)


class _ConfigBackend(EmulatorBackend):
    """Backend whose instances come from a configuration source on disk.

    Subclasses provide :meth:`_read_instances`; this class keeps the parsed
    list and resolves integer slots against it.
    """

    def __init__(self, log_func=None, adb_path=None):
        """Initialize the backend and load its instance list."""
        super().__init__(log_func=log_func, adb_path=adb_path)
        self._instances = []
        self.refresh()

    def refresh(self):
        """Re-read the configuration and rebuild the instance list."""
        self._instances = self._read_instances()

    @abstractmethod
    def _read_instances(self):
        """Return instance dicts with 'index', 'name' and 'display_name'."""

    def list_instances(self):
        """Return the config-backed instances with their stable indices."""
        self.refresh()
        return [{"index": inst["index"], "name": inst["display_name"] or inst["name"]} for inst in self._instances]

    def _get_instance(self, instance_index):
        """Resolve an integer slot to its instance dict.

        Raises:
            ValueError: If the index is out of range.
        """
        return _resolve_instance(self._instances, instance_index, self.name)


class MuMuBackend(EmulatorBackend):
    """MuMu Player backend.

    Instances are discovered by scanning the ``vms`` folder (the folder name
    carries the instance index) and matched to their shell/hypervisor
    processes, so no manager process or window is needed.
    """

    name = EMULATOR_MUMU
    window_process_names = MUMU_WINDOW_PROCESS_NAMES

    def __init__(self, log_func=None, adb_path=MUMU_ADB_PATH, instance_base_path=MUMU_INSTANCE_BASE_PATH, manager_path=MUMU_MULTI_PLAYER_PATH):
        """Initialize MuMu with the configured adb binary and instance path.

        Args:
            log_func (callable, optional): Logging function.
            adb_path (str): Path to the MuMu adb.exe.
            instance_base_path (str): Directory containing the instance VMs.
            manager_path (str): Path to MuMuManager.exe used for graceful
                instance shutdowns.
        """
        super().__init__(log_func=log_func, adb_path=adb_path)
        self.instance_base_path = instance_base_path
        self.manager_path = manager_path

    def _vm_name(self, idx):
        """Return the instance folder name, e.g. ``MuMuPlayerGlobal-15.0-0``.

        MuMu names the per-instance folder after the emulator series it was
        created with, so the series must not be hardcoded. A best-effort guess
        is returned when no folder matches so a missing instance still
        surfaces as a warning.
        """
        if not os.path.isdir(self.instance_base_path):
            return f"MuMuPlayerGlobal-12.0-{idx}"
        try:
            return next(folder for folder in os.listdir(self.instance_base_path) if re.fullmatch(rf"MuMuPlayerGlobal-[\d.]+-{idx}", folder))
        except StopIteration:
            return f"MuMuPlayerGlobal-12.0-{idx}"

    def _instance_config_path(self, idx):
        """Return the extra_config.json path of an instance."""
        return os.path.join(self.instance_base_path, self._vm_name(idx), "configs", "extra_config.json")

    def _instance_name(self, idx):
        """Return the display name of an instance, or ``Instance {idx}``."""
        try:
            with open(self._instance_config_path(idx), encoding="utf-8") as f:
                return json.load(f).get("playerName", f"Instance {idx}")
        except (OSError, ValueError):
            return f"Instance {idx}"

    def _device_executable(self, idx):
        """Return the MuMuNxDevice.exe shell for the instance's series."""
        series = _MUMU_INSTANCE_SERIES_RE.match(self._vm_name(idx)).group(1)
        root = os.path.dirname(self.instance_base_path)
        return os.path.normpath(os.path.join(root, "nx_device", series, "shell", "MuMuNxDevice.exe"))

    def _device_argv(self, idx):
        """Return the argv that launches the instance VM directly."""
        return [self._device_executable(idx), "-v", str(idx), "--vm", self._vm_name(idx)]

    def list_instances(self):
        """Return the instances found in the ``vms`` folder."""
        self.log("Fetching emulator instances...", "info")
        if not os.path.isdir(self.instance_base_path):
            return []
        indices = set()
        for folder in os.listdir(self.instance_base_path):
            match = _MUMU_INSTANCE_FOLDER_RE.fullmatch(folder)
            if match:
                indices.add(int(match.group(1)))
        return [{"index": idx, "name": self._instance_name(idx)} for idx in sorted(indices)]

    def _matching_processes(self, instance_index):
        """Return the shell and hypervisor processes of an instance."""
        vm_name = self._vm_name(instance_index)
        matches = []
        for proc, name, cmdline in _iter_processes():
            if (
                _is_process_named(name, "MuMuNxDevice")
                and _has_process_option(cmdline, "vm", vm_name)
                or _is_process_named(name, "MuMuVMMHeadless")
                and _has_process_option(cmdline, "comment", vm_name)
            ):
                matches.append(proc)
        return matches

    def _killable_processes(self, instance_index):
        """Return the instance shell processes, never the VM process.

        Terminating MuMuVMMHeadless.exe directly crashes the MuMuVMMSVC
        hypervisor service, so only the MuMuNxDevice shell may be killed.
        """
        vm_name = self._vm_name(instance_index)
        return [proc for proc, name, cmdline in _iter_processes() if _is_process_named(name, "MuMuNxDevice") and _has_process_option(cmdline, "vm", vm_name)]

    def _launch_argv(self, instance_index):
        """Launch the instance VM directly with its device shell."""
        return self._device_argv(instance_index)

    def _display_name(self, instance_index):
        """Return the instance display name (the window title)."""
        return self._instance_name(instance_index)

    def get_serial(self, instance_index):
        """Return the MuMu ADB serial, e.g. "127.0.0.1:16416" for instance 1."""
        port = int(ADB_PORT) + ADB_PORT_STEP * int(instance_index)
        return f"127.0.0.1:{port}"

    def check_adb_access(self):
        """MuMu does not restrict out-of-the-box ADB usage."""
        return []

    def _graceful_stop(self, instance_index):
        """Shut the instance down through MuMuManager instead of killing it.

        The request is retried a few times: a busy manager (e.g. still
        answering another instance) can reject the first one.
        """
        if not os.path.exists(self.manager_path):
            self.log(f"MuMuManager.exe not found at {self.manager_path}, falling back to process termination.", "warning")
            return False
        for attempt in range(3):
            result = run_process_robust([self.manager_path, "control", "--vmindex", str(instance_index), "shutdown"], timeout=30)
            if result and result.returncode == 0:
                return True
            if attempt < 2:
                time.sleep(POLL_SECONDS)
        self.log(f"MuMuManager shutdown request failed for instance {instance_index}.", "warning")
        return False


class BlueStacksBackend(_ConfigBackend):
    """BlueStacks 5 backend (``bluestacks.conf``-driven).

    Instances are ordered by ADB port and addressed by their position in that
    order. ADB always runs with BlueStacks' own ``HD-Adb.exe``.
    """

    name = EMULATOR_BLUESTACKS
    window_process_names = BLUESTACKS_WINDOW_PROCESS_NAMES
    adb_running_fallback = True

    def __init__(self, log_func=None, conf_path=BLUESTACKS_CONF, adb_path=BLUESTACKS_ADB_PATH, player_path=BLUESTACKS_HD_PLAYER_PATH):
        """Initialize the BlueStacks backend.

        Args:
            log_func (callable, optional): Logging function.
            conf_path (str): Path to bluestacks.conf.
            adb_path (str): Path to HD-Adb.exe.
            player_path (str): Path to HD-Player.exe.
        """
        self.conf_path = conf_path
        self.player_path = player_path
        super().__init__(log_func=log_func, adb_path=adb_path)

    def _read_instances(self):
        """Read bluestacks.conf and assign stable slots by ADB port."""
        return [{"index": index, **instance} for index, instance in enumerate(list_bluestacks_instances(parse_bluestacks_conf(self.conf_path)))]

    def _launch_argv(self, instance_index):
        """Launch the instance through HD-Player.exe."""
        return [self.player_path, "--instance", self._get_instance(instance_index)["name"]]

    def get_serial(self, instance_index):
        """Return the ADB serial (host:port) of the instance, using its port."""
        return f"127.0.0.1:{self._get_instance(instance_index)['adb_port']}"

    def _matching_processes(self, instance_index):
        """Return the HD-Player processes launched for an instance."""
        instance_name = self._get_instance(instance_index)["name"]
        return [proc for proc, name, cmdline in _iter_processes() if _is_process_named(name, "HD-Player") and _has_process_option(cmdline, "instance", instance_name)]

    def _display_name(self, instance_index):
        """Return the instance display name (the window title)."""
        instance = self._get_instance(instance_index)
        return instance["display_name"] or instance["name"]

    def check_adb_access(self):
        """Warn when BlueStacks blocks out-of-band ADB shell access."""
        if get_bluestacks_adb_access(parse_bluestacks_conf(self.conf_path)):
            return []
        return [
            "BlueStacks has 'Android Debug Bridge' disabled. Enable it in "
            "BlueStacks Settings > Advanced > Android Debug Bridge and restart "
            "BlueStacks, or the tool cannot control the emulator (adb errors: "
            "'error: closed')."
        ]


class LDPlayerBackend(_ConfigBackend):
    """LDPlayer backend (``vms/config/leidianN.config``-driven).

    The ``leidianN`` file number N is the instance index used by
    ``ldconsole.exe``. ADB always runs with LDPlayer's own ``adb.exe``.
    """

    name = EMULATOR_LDPLAYER
    window_process_names = LDPLAYER_WINDOW_PROCESS_NAMES
    adb_running_fallback = True

    def __init__(self, log_func=None, config_dir=LDPLAYER_INSTANCE_CONFIG_DIR, console_path=LDPLAYER_CONSOLE_PATH, adb_path=LDPLAYER_ADB_PATH, player_path=LDPLAYER_PLAYER_PATH):
        """Initialize the LDPlayer backend.

        Args:
            log_func (callable, optional): Logging function.
            config_dir (str): Directory holding the leidianN.config files.
            console_path (str): Path to ldconsole.exe.
            adb_path (str): Path to LDPlayer's bundled adb.exe.
            player_path (str): Path to dnplayer.exe.
        """
        self.config_dir = config_dir
        self.console_path = console_path
        self.player_path = player_path
        super().__init__(log_func=log_func, adb_path=adb_path)

    def _read_instances(self):
        """Read the per-instance config files."""
        return list_ldplayer_instances(self.config_dir)

    def _launch_argv(self, instance_index):
        """Launch the instance through ldconsole (asynchronous)."""
        self._get_instance(instance_index)
        return [self.console_path, "launch", "--index", str(instance_index)]

    def get_serial(self, instance_index):
        """Return the ADB serial (``emulator-<port - 1>``) of the instance."""
        self._get_instance(instance_index)
        port = LDPLAYER_ADB_PORT + LDPLAYER_ADB_PORT_STEP * int(instance_index)
        return f"emulator-{port - 1}"

    def _matching_processes(self, instance_index):
        """Return the dnplayer processes launched for an instance."""
        return [proc for proc, name, cmdline in _iter_processes() if _is_process_named(name, "dnplayer") and _has_process_option(cmdline, "index", instance_index)]

    def _display_name(self, instance_index):
        """Return the instance display name (the window title)."""
        return self._get_instance(instance_index)["display_name"]

    def check_adb_access(self):
        """Warn when LDPlayer instances block out-of-band ADB usage."""
        disabled = [inst["display_name"] for inst in self._instances if not inst["adb_debug"]]
        if not disabled:
            return []
        return [
            "LDPlayer has 'ADB debugging' disabled for instance(s): " + ", ".join(disabled) + ". Enable it in LDPlayer settings (Customize > Other settings > ADB "
            "debugging) and restart the instances, or the tool cannot control "
            "the emulator."
        ]

    def _graceful_stop(self, instance_index):
        """Ask ldconsole to close the instance gracefully."""
        result = run_process_robust([self.console_path, "quit", "--index", str(instance_index)], timeout=15)
        return bool(result and result.returncode == 0)


class WaydroidBackend(_ConfigBackend):
    """Waydroid backend for Linux.

    Instances are discovered from ``/var/lib/waydroid`` (the default one) and
    ``/var/lib/waydroid.<id>`` (the additional ones). ADB is reached over the
    instance's DHCP address, read from the Waydroid dnsmasq lease file. Each
    interactive instance runs inside its own gamescope window so Android keeps
    its 720x1280 resolution while the window fits on screen.
    """

    name = EMULATOR_WAYDROID
    window_process_names = ("gamescope",)
    adb_running_fallback = True

    def __init__(
        self,
        log_func=None,
        adb_path=WAYDROID_ADB_PATH,
        base_dir=WAYDROID_BASE_DIR,
        lease_dir=WAYDROID_LEASE_DIR,
        names_path=WAYDROID_NAMES_FILE,
        window_file=WAYDROID_WINDOW_FILE,
    ):
        """Initialize the Waydroid backend.

        Args:
            log_func (callable, optional): Logging function.
            adb_path (str): Host ``adb`` executable (resolved through PATH
                when it is not an absolute path).
            base_dir (str): Directory holding the instance data directories.
            lease_dir (str): Directory holding the dnsmasq lease files.
            names_path (str): Waydroid instance names file.
            window_file (str): JSON file remembering each instance's window
                position between runs.
        """
        self.base_dir = base_dir
        self.lease_dir = lease_dir
        self.names_path = names_path
        self.window_file = window_file
        super().__init__(log_func=log_func, adb_path=adb_path)

    def _read_instances(self):
        """Discover the installed instances and expose them as stable slots."""
        instances = list_waydroid_instances(self.base_dir, self.names_path)
        return [
            {
                "index": index,
                "id": instance["id"],
                "name": instance["name"],
                "display_name": instance["name"],
            }
            for index, instance in enumerate(instances)
        ]

    def _display_name(self, instance_index):
        """Return the visible instance name (Mario, Tokyo, ...)."""
        return self._get_instance(instance_index)["display_name"]

    def _session_command(self, instance_index):
        """Return the Waydroid command that addresses one instance.

        The default instance uses the system ``waydroid`` command; additional
        instances need the multi-instance wrapper, which is looked up on PATH.

        Returns:
            list or None: Command prefix, or None when the wrapper is missing.
        """
        instance_id = self._get_instance(instance_index)["id"]
        if instance_id == "default":
            return ["waydroid"]
        multi = shutil.which("waydroid-multi")
        if multi is None:
            return None
        return [multi, "--instance", instance_id]

    def _launch_argv(self, instance_index):
        """Return the argv that starts an instance inside gamescope.

        gamescope is optional: without it Waydroid opens its own window at the
        Android resolution, which works but does not fit a 1080p monitor.
        """
        command = self._session_command(instance_index)
        if command is None:
            return []
        gamescope = shutil.which("gamescope")
        if gamescope is None:
            return [*command, "session", "start"]
        return [
            gamescope,
            "--expose-wayland",
            "-W",
            str(WAYDROID_WINDOW_WIDTH),
            "-H",
            str(WAYDROID_WINDOW_HEIGHT),
            "-w",
            str(WAYDROID_ANDROID_WIDTH),
            "-h",
            str(WAYDROID_ANDROID_HEIGHT),
            "--",
            *command,
            "session",
            "start",
        ]

    def _matching_processes(self, instance_index):
        """Return the gamescope processes that belong to an instance."""
        instance_id = self._get_instance(instance_index)["id"]
        matches = []
        for proc, _name, cmdline in _iter_processes():
            if not any(_is_process_named(argument, "gamescope") for argument in cmdline):
                continue
            if "session" not in cmdline or "start" not in cmdline:
                continue
            has_multi = any(_is_process_named(argument, "waydroid-multi") for argument in cmdline)
            if instance_id == "default":
                if has_multi or not any(_is_process_named(argument, "waydroid") for argument in cmdline):
                    continue
            elif not has_multi or not _has_process_option(cmdline, "instance", instance_id):
                continue
            matches.append(proc)
        return matches

    def get_serial(self, instance_index):
        """Return the ADB serial of an instance, read from its DHCP lease."""
        instance_id = self._get_instance(instance_index)["id"]
        leases_path = os.path.join(self.lease_dir, f"dnsmasq.{waydroid_bridge(instance_id)}.leases")
        address = parse_waydroid_leases(leases_path)
        return f"{address}:{WAYDROID_ADB_PORT}" if address else ""

    def _status(self, instance_index):
        """Return the ``waydroid status`` output of an instance ('' on failure)."""
        command = self._session_command(instance_index)
        if command is None:
            return ""
        result = run_process_robust([*command, "status"], timeout=15)
        if result is None or result.returncode != 0:
            return ""
        return result.stdout or ""

    def _session_running(self, instance_index):
        """Return True when Android reports a running session for the instance."""
        return bool(re.search(r"^Session:\s*RUNNING", self._status(instance_index), re.MULTILINE))

    def _ensure_container_service(self, instance_index):
        """Return True when the instance's container service is running.

        Additional instances are backed by a ``waydroid-container@<id>``
        systemd unit. Starting it needs root, so the tool reports the exact
        command instead of asking for a password in the middle of a run.
        """
        if shutil.which("systemctl") is None:
            return True
        instance_id = self._get_instance(instance_index)["id"]
        service = "waydroid-container.service" if instance_id == "default" else f"waydroid-container@{instance_id}.service"
        result = run_process_robust(["systemctl", "is-active", "--quiet", service], timeout=10)
        if result is not None and result.returncode == 0:
            return True
        if re.search(r"^Container:\s*RUNNING", self._status(instance_index), re.MULTILINE):
            return True
        self.log(f"Waydroid container service '{service}' is not running. Start it with: sudo systemctl start {service}", "error")
        return False

    def check_adb_access(self):
        """Return user-facing warnings that block the automation run."""
        warnings = []
        if not self._instances:
            warnings.append(f"No Waydroid instances found under {self.base_dir}. Expected {os.path.join(self.base_dir, 'waydroid')} or {os.path.join(self.base_dir, 'waydroid.<id>')}.")
        if not self._adb_available():
            warnings.append(f"ADB executable '{self.adb_path}' was not found. Install android-tools/platform-tools or set the ADB binary path in Preferences.")
        if warnings:
            return warnings

        devices = self.adb.devices()
        unauthorized = []
        for instance in self._instances:
            serial = self.get_serial(instance["index"])
            if serial and devices.get(serial) == "unauthorized":
                unauthorized.append(instance["display_name"])
        if unauthorized:
            warnings.append(
                "Waydroid denied ADB access for instance(s): "
                + ", ".join(unauthorized)
                + ". Authorize the host key (write ~/.android/adbkey.pub to /data/misc/adb/adb_keys in the instance) and restart it."
            )
        return warnings

    def _adb_available(self):
        """Return True when the configured adb executable can be executed."""
        if os.path.isabs(self.adb_path):
            return os.path.exists(self.adb_path)
        return shutil.which(self.adb_path) is not None

    def _boot_completed(self, serial):
        """Connect to an instance and return whether Android finished booting."""
        self.adb.connect(serial)
        result = self.adb.run(serial, ["shell", "getprop", "sys.boot_completed"], timeout=10)
        return bool(result is not None and result.returncode == 0 and result.stdout.strip() == "1")

    def _wait_until_booted(self, instance_index, process=None, timeout=WAYDROID_BOOT_TIMEOUT_SECONDS):
        """Poll until Android reports boot_completed or the session process dies.

        Args:
            instance_index (int): Instance slot.
            process (subprocess.Popen, optional): Session process, used to fail
                fast when gamescope or Waydroid exits before booting.
            timeout (int): Maximum seconds to wait.

        Returns:
            bool: True when Android booted and ADB answered.
        """
        deadline = time.time() + timeout
        next_notice = time.time() + 15
        while time.time() < deadline:
            if process is not None and process.poll() is not None:
                self.log(f"The Waydroid session process of instance {instance_index} exited with code {process.returncode}.", "error")
                return False
            serial = self.get_serial(instance_index)
            if serial and self._boot_completed(serial):
                return True
            if time.time() >= next_notice:
                self.log(f"Still waiting for instance {self._display_name(instance_index)} to boot...", "debug")
                next_notice = time.time() + 15
            time.sleep(POLL_SECONDS)
        return False

    def _show_ui(self, instance_index):
        """Show the Android UI of an instance (the game launches over ADB)."""
        command = self._session_command(instance_index)
        if command is None:
            return
        result = run_process_robust([*command, "show-full-ui"], timeout=20)
        if result is None or result.returncode != 0:
            self.log(
                f"Could not show the Android UI of instance {self._display_name(instance_index)}; the game will still launch over ADB.",
                "warning",
            )

    def _instance_window_ids(self, pid):
        """Return the gamescope window IDs owned by ``pid``.

        ``kdotool search --pid`` is not reliable (this kdotool version ignores
        the filter and returns every window), so the class is matched first and
        the owner PID is checked window by window.
        """
        kdotool = shutil.which("kdotool")
        if kdotool is None:
            return []
        result = run_process_robust([kdotool, "search", "--class", "gamescope"], timeout=10)
        if result is None or result.returncode != 0:
            return []
        windows = []
        for window_id in result.stdout.split():
            pid_result = run_process_robust([kdotool, "getwindowpid", window_id], timeout=10)
            if pid_result is not None and pid_result.returncode == 0 and pid_result.stdout.strip() == str(pid):
                windows.append(window_id)
        return windows

    def _window_positions(self):
        """Return the remembered ``{instance_id: (x, y)}`` window positions."""
        if not self.window_file:
            return {}
        values = load_json_file(self.window_file, default_value={})
        if not isinstance(values, dict):
            return {}
        positions = {}
        for instance_id, entry in values.items():
            if not isinstance(entry, dict):
                continue
            x, y = entry.get("x"), entry.get("y")
            if isinstance(x, int) and not isinstance(x, bool) and isinstance(y, int) and not isinstance(y, bool):
                positions[str(instance_id)] = (x, y)
        return positions

    def _window_position(self, pid):
        """Return the current ``(x, y)`` of the instance's gamescope window."""
        windows = self._instance_window_ids(pid)
        if not windows:
            return None
        kdotool = shutil.which("kdotool")
        if kdotool is None:
            return None
        result = run_process_robust([kdotool, "getwindowgeometry", windows[0]], timeout=10)
        if result is None or result.returncode != 0:
            return None
        match = re.search(r"Position:\s*(-?\d+),\s*(-?\d+)", result.stdout or "")
        return (int(match.group(1)), int(match.group(2))) if match else None

    def _remember_window_position(self, instance_index):
        """Persist the window position of an instance before it is closed."""
        if not self.window_file:
            return False
        instance = self._get_instance(instance_index)
        processes = self._matching_processes(instance_index)
        if not processes:
            return False
        position = self._window_position(processes[0].pid)
        if position is None:
            return False
        positions = self._window_positions()
        positions[instance["id"]] = {"x": position[0], "y": position[1]}
        return save_json_file(self.window_file, positions)

    def _minimize_window(self, pid):
        """Minimize the gamescope window owned by ``pid`` (KDE, best effort)."""
        kdotool = shutil.which("kdotool")
        if kdotool is None:
            return False
        windows = self._instance_window_ids(pid)
        for window_id in windows:
            run_process_robust([kdotool, "windowminimize", window_id], timeout=10)
        return bool(windows)

    def _place_window(self, instance_index, pid):
        """Move the instance's gamescope window to its remembered slot (best effort).

        The last position saved when the instance was closed wins; the first
        run (or an instance without a saved position) falls back to the
        left-to-right layout. Windows manipulate windows natively in
        :mod:`window_utils`; on KDE the ``kdotool`` helper does the same job.
        When it is not installed the windows keep the position the compositor
        assigns.
        """
        kdotool = shutil.which("kdotool")
        if kdotool is None:
            return False
        instance_id = self._get_instance(instance_index)["id"]
        saved_position = self._window_positions().get(instance_id)
        for _attempt in range(5):
            windows = self._instance_window_ids(pid)
            if windows:
                if saved_position is None:
                    x = int(instance_index) * WAYDROID_WINDOW_STEP
                    y = WAYDROID_WINDOW_TOP
                else:
                    x, y = saved_position
                run_process_robust([kdotool, "windowmove", windows[0], str(x), str(y)], timeout=10)
                return True
            time.sleep(1)
        return False

    def _wait_for_session_end(self, instance_index, attempts=15):
        """Wait until both the session and its gamescope window are gone."""
        for _attempt in range(attempts):
            if not self._session_running(instance_index) and not self._matching_processes(instance_index):
                return True
            time.sleep(POLL_SECONDS)
        return False

    def start_instance(self, instance_index):
        """Start Waydroid inside gamescope and wait for Android to boot.

        A session can outlive its window (gamescope was closed but Android kept
        running headless). That case is restarted so the instance always has a
        visible window again.
        """
        name = self._display_name(instance_index)
        processes = self._matching_processes(instance_index)
        if processes:
            self.log(f"Instance {name} is already running.", "info")
            if start_minimized_enabled():
                for process in processes:
                    self._minimize_window(process.pid)
            return True
        if self._session_running(instance_index):
            self.log(f"Instance {name} is running without a window; restarting the session...", "warning")
            self._graceful_stop(instance_index)
            if not self._wait_for_session_end(instance_index):
                self.log(f"Could not stop the headless session of instance {name}.", "error")
                return False
        if not self._ensure_container_service(instance_index):
            return False

        argv = self._launch_argv(instance_index)
        if not argv:
            self.log(f"Cannot start instance {name}: the waydroid-multi wrapper was not found on PATH.", "error")
            return False

        instance_id = self._get_instance(instance_index)["id"]
        log_path = os.path.join(LOG_DIR, f"waydroid-{instance_id}-gamescope.log")
        ensure_directory_exists(LOG_DIR)
        self.log(f"Starting instance {name}...", "info")
        try:
            with open(log_path, "ab") as log_file:
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=_detached_creation_flags(),
                    startupinfo=_minimized_startupinfo(),
                    start_new_session=os.name != "nt",
                )
        except OSError as error:
            self.log(f"Could not launch instance {name}: {error}", "error")
            return False

        # Reap the session process when it exits: the tool stays alive for
        # hours and must not leave a zombie behind.
        threading.Thread(target=process.wait, name=f"Waydroid-{instance_id}-reaper", daemon=True).start()

        self.log(f"Waiting for Android to boot on instance {name}...", "info")
        if not self._wait_until_booted(instance_index, process=process):
            self.log(f"Instance {name} did not boot within {WAYDROID_BOOT_TIMEOUT_SECONDS} seconds. Check {log_path}.", "error")
            return False

        self._show_ui(instance_index)
        self._place_window(instance_index, process.pid)
        if start_minimized_enabled():
            self._minimize_window(process.pid)
        self.log(f"Instance {name} started.", "success")
        return True

    def _graceful_stop(self, instance_index):
        """Remember the window position and stop the Android session."""
        command = self._session_command(instance_index)
        if command is None:
            return False
        serial = self.get_serial(instance_index)
        self._remember_window_position(instance_index)
        result = run_process_robust([*command, "session", "stop"], timeout=30)
        if serial:
            self.adb.disconnect(serial)
        return bool(result is not None and result.returncode == 0)


def _configured_path(emulator_paths, emulator, key, default):
    """Return one normalized path from persisted emulator settings."""
    if isinstance(emulator_paths, dict):
        emulator_config = emulator_paths.get(emulator, {})
        if isinstance(emulator_config, dict):
            value = emulator_config.get(key)
            if isinstance(value, str) and value.strip():
                return os.path.normpath(value.strip())
    return default


def detect_installed_emulators(emulator_paths=None):
    """Return the emulators installed, in a stable order.

    Args:
        emulator_paths (dict, optional): Configured paths by emulator. When
            omitted, the standard installation paths are used.

    Returns:
        list: e.g. ["mumu", "bluestacks", "ldplayer", "waydroid"]; empty if
        none is detected.
    """
    mumu_base_path = _configured_path(emulator_paths, EMULATOR_MUMU, "base_path", MUMU_BASE_PATH)
    bluestacks_conf = _configured_path(emulator_paths, EMULATOR_BLUESTACKS, "config_path", BLUESTACKS_CONF)
    ldplayer_base_path = _configured_path(emulator_paths, EMULATOR_LDPLAYER, "base_path", LDPLAYER_BASE_PATH)
    waydroid_base_path = _configured_path(emulator_paths, EMULATOR_WAYDROID, "instances_dir", WAYDROID_BASE_DIR)
    installed = []
    if os.path.exists(os.path.join(mumu_base_path, "MuMuNxMain.exe")):
        installed.append(EMULATOR_MUMU)
    if os.path.exists(bluestacks_conf):
        installed.append(EMULATOR_BLUESTACKS)
    if os.path.exists(os.path.join(ldplayer_base_path, "ldconsole.exe")):
        installed.append(EMULATOR_LDPLAYER)
    if os.path.exists(os.path.join(waydroid_base_path, "waydroid", "waydroid.cfg")):
        installed.append(EMULATOR_WAYDROID)
    return installed


def create_backend(emulator=None, log_func=None, emulator_paths=None):
    """Create an emulator backend by name (defaults to MuMu).

    Args:
        emulator (str, optional): "mumu", "bluestacks", "ldplayer",
            "waydroid" or None.
        log_func (callable, optional): Logging function.
        emulator_paths (dict, optional): Configured paths by emulator.

    Returns:
        EmulatorBackend: The chosen backend instance.
    """
    mumu_base_path = _configured_path(emulator_paths, EMULATOR_MUMU, "base_path", MUMU_BASE_PATH)
    mumu_instance_base_path = _configured_path(emulator_paths, EMULATOR_MUMU, "instance_base_path", MUMU_INSTANCE_BASE_PATH)
    bluestacks_base_path = _configured_path(emulator_paths, EMULATOR_BLUESTACKS, "base_path", BLUESTACKS_BASE_PATH)
    bluestacks_conf = _configured_path(emulator_paths, EMULATOR_BLUESTACKS, "config_path", BLUESTACKS_CONF)
    ldplayer_base_path = _configured_path(emulator_paths, EMULATOR_LDPLAYER, "base_path", LDPLAYER_BASE_PATH)
    ldplayer_config_dir = _configured_path(emulator_paths, EMULATOR_LDPLAYER, "instance_config_dir", LDPLAYER_INSTANCE_CONFIG_DIR)
    waydroid_base_path = _configured_path(emulator_paths, EMULATOR_WAYDROID, "instances_dir", WAYDROID_BASE_DIR)
    waydroid_adb_path = _configured_path(emulator_paths, EMULATOR_WAYDROID, "adb_path", WAYDROID_ADB_PATH)
    if emulator == EMULATOR_BLUESTACKS:
        return BlueStacksBackend(
            log_func=log_func,
            conf_path=bluestacks_conf,
            adb_path=os.path.join(bluestacks_base_path, "HD-Adb.exe"),
            player_path=os.path.join(bluestacks_base_path, "HD-Player.exe"),
        )
    if emulator == EMULATOR_LDPLAYER:
        return LDPlayerBackend(
            log_func=log_func,
            config_dir=ldplayer_config_dir,
            console_path=os.path.join(ldplayer_base_path, "ldconsole.exe"),
            adb_path=os.path.join(ldplayer_base_path, "adb.exe"),
            player_path=os.path.join(ldplayer_base_path, "dnplayer.exe"),
        )
    if emulator == EMULATOR_WAYDROID:
        return WaydroidBackend(
            log_func=log_func,
            adb_path=waydroid_adb_path,
            base_dir=waydroid_base_path,
        )
    return MuMuBackend(
        log_func=log_func,
        adb_path=os.path.join(mumu_base_path, "adb.exe"),
        instance_base_path=mumu_instance_base_path,
        manager_path=os.path.join(mumu_base_path, "MuMuManager.exe"),
    )
