"""Emulator backend abstraction.

Introduces a single interface so the automation pipeline can run against
MuMu Player, BlueStacks 5 or LDPlayer without changing its own code. Each
backend owns the emulator-specific details: listing/starting/stopping
instances and resolving ADB commands/serials for a given integer
``instance_index``.

Callers that do not create a backend explicitly get the default MuMu backend,
preserving the pre-refactor behavior.
"""

import json
import logging
import os
import re
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Dict

import psutil

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
from wosutil.emulator.instances_controller import MultiInstanceManager, save_instance_cache
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

_BLUESTACKS_INSTANCE_PORT_RE = re.compile(r"^bst\.instance\.([^.]+)\.status\.adb_port$")
_LDPLAYER_CONFIG_RE = re.compile(r"^leidian(\d+)\.config$")

# Executable names whose windows belong to each emulator family (basename,
# extensionless, case-insensitive). Used to minimize windows that would
# otherwise steal the foreground focus. MuMu 12 runs its instances through
# MuMuNxDevice.exe (shells/renderers spawn short-lived helper processes that
# may be unreadable, so the stable names are listed too).
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


def parse_devices_output(stdout):
    """Parses the output of ``adb devices`` into a {serial: state} dict.

    Args:
        stdout: The raw output of the devices command.

    Returns:
        dict: Mapping of device serial to connection state ("device", "offline").
    """
    devices: Dict[str, str] = {}
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line or "List of devices" in line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices[parts[0]] = parts[1].lower()
    return devices


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


class EmulatorBackend(ABC):
    """Uniform interface implemented by every supported emulator.

    The instance lifecycle (start/stop, window minimizing, ADB server helpers)
    is shared by every backend; subclasses only provide the emulator-specific
    details: how instances are discovered on disk, how one is launched and
    matched to its processes, and how ADB commands reach it.
    """

    name = ""
    window_process_names = ()

    def __init__(self, log_func=None, adb_path=None):
        """Initialize the backend with a logging function and ADB binary."""
        self.log = log_func or logger.info
        self.adb_path = adb_path

    @abstractmethod
    def get_instances(self):
        """Return the list of emulator instances with 'index' and 'name'."""
        raise NotImplementedError

    @abstractmethod
    def _is_instance_running(self, instance_index):
        """Return True if the given instance is currently running."""
        raise NotImplementedError

    @abstractmethod
    def get_serial(self, instance_index):
        """Return the ADB serial (host:port) of the given instance."""
        raise NotImplementedError

    @abstractmethod
    def build_adb_command(self, command_parts, instance_index):
        """Return the full ADB argv for ``command_parts`` on the instance."""
        raise NotImplementedError

    @abstractmethod
    def check_adb_access(self):
        """Return non-empty user-facing warnings about ADB access."""
        raise NotImplementedError

    @abstractmethod
    def _launch_argv(self, instance_index):
        """Return the argv that starts the instance (a long-lived process).

        The process must not be awaited: instances boot on their own and are
        monitored by polling, like the other emulator backends do.
        """
        raise NotImplementedError

    @abstractmethod
    def _matching_processes(self, instance_index):
        """Return the running processes that belong to the instance."""
        raise NotImplementedError

    @abstractmethod
    def _display_name(self, instance_index):
        """Return the instance display name (its window title)."""
        raise NotImplementedError

    def _graceful_stop(self, instance_index):
        """Ask the emulator to close the instance gracefully (optional hook)."""
        return None

    def _keep_windows_background(self, instance_index):
        """Minimize the emulator windows so they never steal the foreground.

        The window owner process can be elevated and unreadable, so the
        instance display name (the window title) is matched as well. Only runs
        when the instance is being opened by the tool: if the user restores
        the window afterwards it is left alone.
        """
        if not start_minimized_enabled():
            return
        titles = (self._display_name(instance_index),)
        minimize_process_windows(self.window_process_names, self.log)
        minimize_windows_by_title(titles, self.log)

    def start_instance(self, instance_index):
        """Start an instance and wait for it to boot. Return True on success.

        The window is launched minimized when the emulator honors the initial
        window state and is otherwise minimized as soon as it appears, so it
        never keeps the foreground focus.

        Return False after a generous (60s) window without confirmation.
        """
        name = self._display_name(instance_index)
        self.log(f"Starting instance {name}...", "info")
        if self._is_instance_running(instance_index):
            self.log(f"Instance {name} is already running.", "info")
            self._keep_windows_background(instance_index)
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
        # Minimize as soon as the window appears (and again once confirmed
        # running) so it never keeps the foreground focus.
        self._keep_windows_background(instance_index)
        for _ in range(30):
            if self._is_instance_running(instance_index):
                # The instance process may be confirmed before its window
                # exists (the emulator spawns it in the first seconds); keep
                # minimizing for a short grace period so the window never
                # steals the foreground when it appears.
                for _ in range(2):
                    self._keep_windows_background(instance_index)
                    time.sleep(2)
                self.log(f"Instance {name} started.", "success")
                return True
            self._keep_windows_background(instance_index)
            time.sleep(2)
        self.log(f"Could not confirm instance {name} started.", "error")
        return False

    def stop_instance(self, instance_index):
        """Stop an instance: graceful request first, then terminate processes.

        The graceful request (when the emulator provides one) is given time
        to close the VM cleanly before the instance processes are terminated
        directly: killing a legacy VM process (MuMuVMMHeadless.exe) crashes
        the MuMuVMMSVC hypervisor service, so direct termination is only the
        fallback for a hung emulator (and backends may forbid killing some
        processes entirely, see :meth:`_terminate_matching_processes`).

        Return False after a short window without confirmation.
        """
        name = self._display_name(instance_index)
        self.log(f"Stopping instance {name}...", "info")
        if not self._is_instance_running(instance_index):
            self.log(f"Instance {name} is not running.", "info")
        else:
            if self._graceful_stop(instance_index):
                # The manager accepted a graceful shutdown: keep the request
                # alive (a hung guest may only answer a later one) and wait
                # for the VM to close on its own before terminating anything.
                for attempt in range(30):
                    if not self._matching_processes(instance_index):
                        self.log(f"Instance {name} stopped.", "success")
                        break
                    if attempt and attempt % 5 == 0:
                        self._graceful_stop(instance_index)
                    time.sleep(2)
                if not self._matching_processes(instance_index):
                    if start_minimized_enabled():
                        minimize_process_windows(self.window_process_names, self.log)
                    return True
                self.log(f"Graceful shutdown of instance {name} did not complete. Terminating processes...", "warning")
            if not self._terminate_matching_processes(instance_index):
                self.log(f"Could not stop instance {name}.", "error")
                if start_minimized_enabled():
                    minimize_process_windows(self.window_process_names, self.log)
                return False
            self.log(f"Instance {name} stopped.", "success")
        # The emulator may leave windows behind when an instance closes (e.g.
        # the MuMu manager); minimize them so the close never steals focus.
        if start_minimized_enabled():
            minimize_process_windows(self.window_process_names, self.log)
        return True

    def _terminate_matching_processes(self, instance_index):
        """Terminate the instance processes directly, waiting between attempts.

        Terminates then kills the processes matched to the instance, giving
        them a short window to exit after each step. Backends whose processes
        must never be killed directly (MuMu legacy VMs crash the hypervisor
        service) override this to exclude them.

        Returns:
            bool: True when the instance processes are all gone afterwards.
        """
        processes = self._matching_processes(instance_index)
        for proc in processes:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for _ in range(5):
            if not self._matching_processes(instance_index):
                return True
            time.sleep(2)
        for proc in self._matching_processes(instance_index):
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for _ in range(5):
            if not self._matching_processes(instance_index):
                return True
            time.sleep(2)
        return False

    def list_devices(self):
        """List devices using the backend adb binary."""
        result = run_process_robust([self.adb_path, "devices"], timeout=15)
        if not result:
            return {}
        return parse_devices_output(result.stdout)

    def connect(self, serial):
        """Ask the backend ADB server to connect to ``serial``."""
        run_process_robust([self.adb_path, "connect", serial], timeout=10)

    def kill_server(self):
        """Stop the backend ADB server."""
        run_process_robust([self.adb_path, "kill-server"], timeout=10)

    def restart_server(self):
        """Restart the backend ADB server."""
        self.kill_server()
        run_process_robust([self.adb_path, "start-server"], timeout=10)


class MuMuBackend(MultiInstanceManager, EmulatorBackend):
    """MuMu Player backend.

    Reuses the existing :class:`MultiInstanceManager` for instance discovery
    and adds the ADB command/connection helpers MuMu requires (through its own
    ``adb.exe``, targeting the predictable 16384 + 32*index serials).
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
                instance shutdowns (falls back to terminating processes when
                missing).
        """
        super().__init__(log_func=log_func, instance_base_path=instance_base_path)
        self.adb_path = adb_path
        self.manager_path = manager_path

    def _graceful_stop(self, instance_index):
        """Shut the instance down through MuMuManager instead of killing it.

        Terminating the instance processes directly (MuMuVMMHeadless.exe for
        legacy Android 12 VMs) makes the MuMuVMMSVC hypervisor service crash
        with an R6025 "pure virtual function call"; asking the manager to
        shut the player down first lets the hypervisor release the VM
        cleanly. The request is retried a few times: a busy manager (e.g.
        still answering another instance) can reject the first one.

        Returns:
            bool: True when the manager accepted the shutdown request, False
            when it is missing or every attempt failed (callers then fall
            back to terminating the shell processes only, never the VM).
        """
        for _ in range(3):
            if not os.path.exists(self.manager_path):
                self.log(f"MuMuManager.exe not found at {self.manager_path}, falling back to process termination.", "warning")
                return False
            result = run_process_robust([self.manager_path, "control", "--vmindex", str(instance_index), "shutdown"], timeout=30)
            if result and result.returncode == 0:
                return True
            time.sleep(2)
        self.log(f"MuMuManager shutdown request failed for instance {instance_index}.", "warning")
        return False

    def _terminate_matching_processes(self, instance_index):
        """Kill the instance shell processes, never the VM process.

        Terminating MuMuVMMHeadless.exe (the VirtualBox VM process behind
        Android 12 instances) directly crashes the MuMuVMMSVC hypervisor
        service with an R6025 "pure virtual function call", so the VM is
        left running when the manager cannot close it: the stop is reported
        as failed and the instance is retried later instead of taking the
        ADB of every instance down with it.

        Returns:
            bool: True when the instance processes are all gone afterwards.
        """
        vm_name = self._vm_name(instance_index)
        shells = [proc for proc, name, cmdline in _iter_processes() if _is_process_named(name, "MuMuNxDevice") and _has_process_option(cmdline, "vm", vm_name)]
        for proc in shells:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for _ in range(5):
            if not self._matching_processes(instance_index):
                return True
            time.sleep(2)
        for proc in shells:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for _ in range(5):
            if not self._matching_processes(instance_index):
                return True
            time.sleep(2)
        return False

    def _launch_argv(self, instance_index):
        """Launch the instance VM directly with its device shell."""
        return self._device_argv(instance_index)

    def _matching_processes(self, instance_index):
        """Match the instance by its shell or hypervisor process."""
        return super()._matching_processes(instance_index)

    def _display_name(self, instance_index):
        """Return the instance display name (the window title)."""
        return self._instance_name(instance_index)

    def _is_instance_running(self, instance_index):
        """Check whether the instance is running via its processes."""
        return bool(self._matching_processes(instance_index))

    def get_serial(self, instance_index):
        """Return the MuMu ADB serial for the instance index.

        Args:
            instance_index (int): Emulator instance index.

        Returns:
            str: ADB serial, e.g. "127.0.0.1:16416" for instance 1.
        """
        port = int(ADB_PORT) + ADB_PORT_STEP * int(instance_index)
        return f"127.0.0.1:{port}"

    def build_adb_command(self, command_parts, instance_index):
        """Build the MuMu adb command line.

        Args:
            command_parts (list): ADB command parts.
            instance_index (int): Emulator instance index.

        Returns:
            list: Full argv, e.g. [adb.exe, "-s", "127.0.0.1:16416", ...].
        """
        return [self.adb_path, "-s", self.get_serial(instance_index)] + command_parts

    def check_adb_access(self):
        """MuMu does not restrict out-of-the-box ADB usage."""
        return []


class BlueStacksBackend(EmulatorBackend):
    """BlueStacks 5 backend (``bluestacks.conf``-driven).

    Instances are discovered from ``bluestacks.conf``; integer instance indices
    map to those instances ordered by ADB port. ADB is always run with
    BlueStacks' own ``HD-Adb.exe`` (its server owns port 5037).
    """

    name = EMULATOR_BLUESTACKS
    window_process_names = BLUESTACKS_WINDOW_PROCESS_NAMES

    def __init__(self, log_func=None, conf_path=BLUESTACKS_CONF, adb_path=BLUESTACKS_ADB_PATH, player_path=BLUESTACKS_HD_PLAYER_PATH):
        """Initialize the BlueStacks backend.

        Args:
            log_func (callable, optional): Logging function.
            conf_path (str): Path to bluestacks.conf.
            adb_path (str): Path to HD-Adb.exe.
            player_path (str): Path to HD-Player.exe.
        """
        super().__init__(log_func=log_func, adb_path=adb_path)
        self.conf_path = conf_path
        self.player_path = player_path
        self._instances = []
        self._index_map = {}
        self.refresh()

    def refresh(self):
        """Re-read the config file and rebuild the instance list."""
        values = parse_bluestacks_conf(self.conf_path)
        self._instances = list_bluestacks_instances(values)
        self._index_map = {index: instance for index, instance in enumerate(self._instances)}

    def _get_instance(self, instance_index):
        """Resolve an integer slot to its instance dict.

        Args:
            instance_index (int): Instance slot.

        Returns:
            dict: The instance info.

        Raises:
            ValueError: If the index is out of range.
        """
        instance = self._index_map.get(int(instance_index))
        if instance is None:
            raise ValueError(f"Unknown BlueStacks instance index: {instance_index}")
        return instance

    def _instance_name(self, instance_index):
        """Return the config key (e.g. "Pie64") of an instance slot."""
        return self._get_instance(instance_index)["name"]

    def _display_name(self, instance_index):
        """Return the instance display name (the window title)."""
        return self._get_instance(instance_index)["display_name"]

    def _launch_argv(self, instance_index):
        """Launch the instance through HD-Player.exe."""
        return [self.player_path, "--instance", self._instance_name(instance_index)]

    def get_instances(self):
        """List BlueStacks instances with stable integer indices (by ADB port).

        The result is persisted to the shared instance cache, mirroring the MuMu
        backend, so the GUI can render instances without re-reading the config
        file on every refresh.
        """
        self.refresh()
        instances = [{"index": i, "name": inst["display_name"] or inst["name"]} for i, inst in enumerate(self._instances)]
        save_instance_cache(instances)
        return instances

    def get_serial(self, instance_index):
        """Return the ADB serial (host:port) of the instance, using its port."""
        return f"127.0.0.1:{self._get_instance(instance_index)['adb_port']}"

    def build_adb_command(self, command_parts, instance_index):
        """Build a BlueStacks adb command using its own binary.

        Args:
            command_parts (list): ADB command parts.
            instance_index (int): Emulator instance slot.

        Returns:
            list: Full argv, e.g. [HD-Adb.exe, "-s", "127.0.0.1:5555", ...].
        """
        return [self.adb_path, "-s", self.get_serial(instance_index)] + command_parts

    def _matching_processes(self, instance_index):
        """Return the HD-Player processes launched for an instance."""
        instance_name = self._instance_name(instance_index)
        return [proc for proc, name, cmdline in _iter_processes() if _is_process_named(name, "HD-Player") and _has_process_option(cmdline, "instance", instance_name)]

    def _is_instance_running(self, instance_index):
        """Check whether the instance is booting/running via its processes."""
        if self._matching_processes(instance_index):
            return True
        # Fallback: the instance may run headless, visible only as ADB device.
        devices = self.list_devices()
        return devices.get(self.get_serial(instance_index)) == "device"

    def check_adb_access(self):
        """Warn when BlueStacks blocks out-of-band ADB shell access.

        Returns:
            list: Human-readable warnings (empty when ADB access is enabled).
        """
        if get_bluestacks_adb_access(parse_bluestacks_conf(self.conf_path)):
            return []
        warning = (
            "BlueStacks has 'Android Debug Bridge' disabled. Enable it in "
            "BlueStacks Settings > Advanced > Android Debug Bridge and restart "
            "BlueStacks, or the tool cannot control the emulator (adb errors: "
            "'error: closed')."
        )
        return [warning]


class LDPlayerBackend(EmulatorBackend):
    """LDPlayer backend (``vms/config/leidianN.config``-driven).

    Instances are discovered from the per-instance JSON config files; the
    ``leidianN`` file number N is the instance index used by ``ldconsole.exe``
    (``launch --index N`` / ``quit --index N``). ADB always runs with
    LDPlayer's own ``adb.exe`` (its server owns port 5037 and registers each
    instance as ``emulator-5554 + 2*index``).
    """

    name = EMULATOR_LDPLAYER
    window_process_names = LDPLAYER_WINDOW_PROCESS_NAMES

    def __init__(
        self,
        log_func=None,
        config_dir=LDPLAYER_INSTANCE_CONFIG_DIR,
        console_path=LDPLAYER_CONSOLE_PATH,
        adb_path=LDPLAYER_ADB_PATH,
        player_path=LDPLAYER_PLAYER_PATH,
    ):
        """Initialize the LDPlayer backend.

        Args:
            log_func (callable, optional): Logging function.
            config_dir (str): Directory holding the leidianN.config files.
            console_path (str): Path to ldconsole.exe.
            adb_path (str): Path to LDPlayer's bundled adb.exe.
            player_path (str): Path to dnplayer.exe.
        """
        super().__init__(log_func=log_func, adb_path=adb_path)
        self.config_dir = config_dir
        self.console_path = console_path
        self.player_path = player_path
        self._instances = []
        self._index_map = {}
        self.refresh()

    def refresh(self):
        """Re-read the config directory and rebuild the instance list."""
        self._instances = list_ldplayer_instances(self.config_dir)
        self._index_map = {inst["index"]: inst for inst in self._instances}

    def _get_instance(self, instance_index):
        """Resolve an integer slot to its instance dict.

        Args:
            instance_index (int): Instance index.

        Returns:
            dict: The instance info.

        Raises:
            ValueError: If the index is out of range.
        """
        instance = self._index_map.get(int(instance_index))
        if instance is None:
            raise ValueError(f"Unknown LDPlayer instance index: {instance_index}")
        return instance

    def get_instances(self):
        """List LDPlayer instances with their stable indexes.

        The result is persisted to the shared instance cache, mirroring the
        other backends, so the GUI can render instances without re-reading the
        config files on every refresh.
        """
        self.refresh()
        instances = [{"index": inst["index"], "name": inst["display_name"]} for inst in self._instances]
        save_instance_cache(instances)
        return instances

    def get_serial(self, instance_index):
        """Return the ADB serial shown by LDPlayer's adb server.

        LDPlayer registers each instance as ``emulator-<port - 1>`` with the
        port derived from the instance index (5555 for instance 0).
        """
        self._get_instance(instance_index)
        port = LDPLAYER_ADB_PORT + LDPLAYER_ADB_PORT_STEP * int(instance_index)
        return f"emulator-{port - 1}"

    def build_adb_command(self, command_parts, instance_index):
        """Build an LDPlayer adb command using its own binary.

        Args:
            command_parts (list): ADB command parts.
            instance_index (int): Emulator instance index.

        Returns:
            list: Full argv, e.g. [adb.exe, "-s", "emulator-5554", ...].
        """
        return [self.adb_path, "-s", self.get_serial(instance_index)] + command_parts

    def _matching_processes(self, instance_index):
        """Return the dnplayer processes launched for an instance."""
        return [proc for proc, name, cmdline in _iter_processes() if _is_process_named(name, "dnplayer") and _has_process_option(cmdline, "index", instance_index)]

    def _display_name(self, instance_index):
        """Return the instance display name (the window title)."""
        return self._get_instance(instance_index)["display_name"]

    def _launch_argv(self, instance_index):
        """Launch the instance through ldconsole (asynchronous)."""
        return [self.console_path, "launch", "--index", str(instance_index)]

    def _graceful_stop(self, instance_index):
        """Ask ldconsole to close the instance gracefully."""
        run_process_robust([self.console_path, "quit", "--index", str(instance_index)], timeout=15)

    def _is_instance_running(self, instance_index):
        """Check whether the instance is running via its dnplayer process."""
        if self._matching_processes(instance_index):
            return True
        # Fallback: the instance may run headless, visible only as ADB device.
        devices = self.list_devices()
        return devices.get(self.get_serial(instance_index)) == "device"

    def check_adb_access(self):
        """Warn when LDPlayer instances block out-of-band ADB usage.

        Returns:
            list: Human-readable warnings (empty when ADB debugging is enabled
                on every instance).
        """
        disabled = [inst["display_name"] for inst in self._instances if not inst["adb_debug"]]
        if not disabled:
            return []
        warning = (
            "LDPlayer has 'ADB debugging' disabled for instance(s): " + ", ".join(disabled) + ". Enable it in LDPlayer settings (Customize > Other settings > ADB "
            "debugging) and restart the instances, or the tool cannot control "
            "the emulator."
        )
        return [warning]


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
