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
import subprocess
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
    MUMU_ADB_PATH,
    MUMU_BASE_PATH,
    MUMU_INSTANCE_BASE_PATH,
    MUMU_MULTI_PLAYER_PATH,
)
from wosutil.emulator.window_utils import minimize_process_windows, minimize_windows_by_title
from wosutil.utils import (
    _detached_creation_flags,
    _has_process_option,
    _is_process_named,
    _iter_processes,
    _minimized_startupinfo,
    run_process_robust,
)

logger = logging.getLogger(__name__)

# Backend identifiers used in preferences and the GUI.
EMULATOR_MUMU = "mumu"
EMULATOR_BLUESTACKS = "bluestacks"
EMULATOR_LDPLAYER = "ldplayer"

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
        list: e.g. ["mumu", "bluestacks", "ldplayer"]; empty if none is detected.
    """
    mumu_base_path = _configured_path(emulator_paths, EMULATOR_MUMU, "base_path", MUMU_BASE_PATH)
    bluestacks_conf = _configured_path(emulator_paths, EMULATOR_BLUESTACKS, "config_path", BLUESTACKS_CONF)
    ldplayer_base_path = _configured_path(emulator_paths, EMULATOR_LDPLAYER, "base_path", LDPLAYER_BASE_PATH)
    installed = []
    if os.path.exists(os.path.join(mumu_base_path, "MuMuNxMain.exe")):
        installed.append(EMULATOR_MUMU)
    if os.path.exists(bluestacks_conf):
        installed.append(EMULATOR_BLUESTACKS)
    if os.path.exists(os.path.join(ldplayer_base_path, "ldconsole.exe")):
        installed.append(EMULATOR_LDPLAYER)
    return installed


def create_backend(emulator=None, log_func=None, emulator_paths=None):
    """Create an emulator backend by name (defaults to MuMu).

    Args:
        emulator (str, optional): "mumu", "bluestacks", "ldplayer" or None.
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
    return MuMuBackend(
        log_func=log_func,
        adb_path=os.path.join(mumu_base_path, "adb.exe"),
        instance_base_path=mumu_instance_base_path,
        manager_path=os.path.join(mumu_base_path, "MuMuManager.exe"),
    )
