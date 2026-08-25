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
    BLUESTACKS_CONF,
    BLUESTACKS_HD_PLAYER_PATH,
    LDPLAYER_ADB_PATH,
    LDPLAYER_ADB_PORT,
    LDPLAYER_ADB_PORT_STEP,
    LDPLAYER_CONSOLE_PATH,
    LDPLAYER_INSTANCE_CONFIG_DIR,
    LDPLAYER_PLAYER_PATH,
    MUMU_ADB_PATH,
    MUMU_MULTI_PLAYER_PATH,
)
from wosutil.emulator.instances_controller import MultiInstanceManager, save_instance_cache
from wosutil.utils import run_process_robust

logger = logging.getLogger(__name__)

# Backend identifiers used in preferences and the GUI.
EMULATOR_MUMU = "mumu"
EMULATOR_BLUESTACKS = "bluestacks"
EMULATOR_LDPLAYER = "ldplayer"

_BLUESTACKS_INSTANCE_PORT_RE = re.compile(r"^bst\.instance\.([^.]+)\.status\.adb_port$")
_LDPLAYER_CONFIG_RE = re.compile(r"^leidian(\d+)\.config$")


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
            return json.load(f)
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


def _iter_processes():
    """Yield (process, name, argv) for every inspectable process."""
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            yield proc, proc.info["name"] or "", proc.info["cmdline"] or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _is_process_named(name, expected_name):
    """Compare process basenames without accepting similarly named programs."""
    return os.path.splitext(os.path.basename(name))[0].casefold() == expected_name.casefold()


def _has_process_option(cmdline, option, expected_value):
    """Return whether an option has the exact expected value in an argv list.

    Supports the forms used by emulator processes: ``--option value``,
    ``--option=value`` and the equivalent form without leading dashes.
    Exact token comparison prevents instance ``1`` from matching ``10``.
    """
    option = option.lstrip("-").casefold()
    expected_value = str(expected_value).casefold()
    for position, argument in enumerate(cmdline):
        normalized = str(argument).strip('"').lstrip("-")
        key, separator, value = normalized.partition("=")
        if separator and key.casefold() == option and value.casefold() == expected_value:
            return True
        if not separator and normalized.casefold() == option and position + 1 < len(cmdline):
            next_value = str(cmdline[position + 1]).strip('"').casefold()
            if next_value == expected_value:
                return True
    return False


class EmulatorBackend(ABC):
    """Uniform interface implemented by every supported emulator."""

    name = ""

    @abstractmethod
    def get_instances(self):
        """Return the list of emulator instances with 'index' and 'name'."""
        raise NotImplementedError

    @abstractmethod
    def _is_instance_running(self, instance_index):
        """Return True if the given instance is currently running."""
        raise NotImplementedError

    @abstractmethod
    def start_instance(self, instance_index):
        """Start the instance and wait until it is up. Return True on success."""
        raise NotImplementedError

    @abstractmethod
    def stop_instance(self, instance_index):
        """Stop the instance and wait until it is down. Return True on success."""
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
    def list_devices(self):
        """Return {serial: state} from the backend ADB devices command."""
        raise NotImplementedError

    @abstractmethod
    def connect(self, serial):
        """Ask the backend ADB server to connect to ``serial``."""
        raise NotImplementedError

    @abstractmethod
    def kill_server(self):
        """Stop the backend ADB server."""
        raise NotImplementedError

    @abstractmethod
    def restart_server(self):
        """Restart the backend ADB server."""
        raise NotImplementedError

    @abstractmethod
    def check_adb_access(self):
        """Return non-empty user-facing warnings about ADB access."""
        raise NotImplementedError


class MuMuBackend(MultiInstanceManager, EmulatorBackend):
    """MuMu Player backend.

    Reuses the existing :class:`MultiInstanceManager` for instance management
    and adds the ADB command/connection helpers MuMu requires (via
    ``MuMuManager.exe adb``).
    """

    name = EMULATOR_MUMU

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
            list: Full argv, e.g. [MuMuManager.exe, "adb", "-v", "0", ...].
        """
        return [MUMU_MULTI_PLAYER_PATH, "adb", "-v", str(instance_index)] + command_parts

    def list_devices(self):
        """List devices using MuMu's bundled adb binary."""
        result = run_process_robust([MUMU_ADB_PATH, "devices"], timeout=15)
        if not result:
            return {}
        return parse_devices_output(result.stdout)

    def connect(self, serial):
        """Connect the MuMu adb server to the given serial."""
        run_process_robust([MUMU_ADB_PATH, "connect", serial], timeout=10)

    def kill_server(self):
        """Stop the MuMu adb server."""
        run_process_robust([MUMU_ADB_PATH, "kill-server"], timeout=10)

    def restart_server(self):
        """Restart the MuMu adb server."""
        self.kill_server()
        run_process_robust([MUMU_ADB_PATH, "start-server"], timeout=10)

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

    def __init__(self, log_func=None, conf_path=BLUESTACKS_CONF, adb_path=BLUESTACKS_ADB_PATH, player_path=BLUESTACKS_HD_PLAYER_PATH):
        """Initialize the BlueStacks backend.

        Args:
            log_func (callable, optional): Logging function.
            conf_path (str): Path to bluestacks.conf.
            adb_path (str): Path to HD-Adb.exe.
            player_path (str): Path to HD-Player.exe.
        """
        self.log = log_func or logger.info
        self.conf_path = conf_path
        self.adb_path = adb_path
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

    def start_instance(self, instance_index):
        """Start a BlueStacks instance via HD-Player and wait for it to boot.

        Return False after a generous (60s) window without confirmation.
        """
        name = self._instance_name(instance_index)
        self.log(f"Starting BlueStacks instance {name}...", "info")
        if self._is_instance_running(instance_index):
            self.log(f"BlueStacks instance {name} is already running.", "info")
            return True
        # HD-Player.exe hosts the instance and never exits on its own, so it
        # must be launched detached and monitored by polling, not awaited.
        try:
            subprocess.Popen(
                [self.player_path, "--instance", name],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        except OSError as e:
            self.log(f"Could not launch BlueStacks instance {name}: {e}", "error")
            return False
        for _ in range(30):
            if self._is_instance_running(instance_index):
                self.log(f"BlueStacks instance {name} started.", "success")
                return True
            time.sleep(2)
        self.log(f"Could not confirm BlueStacks instance {name} started.", "error")
        return False

    def stop_instance(self, instance_index):
        """Terminate the BlueStacks processes of the instance."""
        name = self._instance_name(instance_index)
        self.log(f"Stopping BlueStacks instance {name}...", "info")
        processes = self._matching_processes(instance_index)
        if not processes:
            self.log(f"BlueStacks instance {name} is not running.", "info")
            return True
        for proc in processes:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for _ in range(30):
            remaining = self._matching_processes(instance_index)
            if not remaining:
                self.log(f"BlueStacks instance {name} stopped.", "success")
                return True
            time.sleep(2)
        self.log(f"Could not stop BlueStacks instance {name}.", "error")
        return False

    def list_devices(self):
        """List devices using BlueStacks' own HD-Adb."""
        result = run_process_robust([self.adb_path, "devices"], timeout=15)
        if not result:
            return {}
        return parse_devices_output(result.stdout)

    def connect(self, device):
        """Connect BlueStacks' HD-Adb server to the given serial."""
        run_process_robust([self.adb_path, "connect", device], timeout=10)

    def kill_server(self):
        """Stop BlueStacks' HD-Adb server."""
        run_process_robust([self.adb_path, "kill-server"], timeout=10)

    def restart_server(self):
        """Restart BlueStacks' HD-Adb server."""
        self.kill_server()
        run_process_robust([self.adb_path, "start-server"], timeout=10)

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
        self.log = log_func or logger.info
        self.config_dir = config_dir
        self.console_path = console_path
        self.adb_path = adb_path
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

    def _is_instance_running(self, instance_index):
        """Check whether the instance is running via its dnplayer process."""
        if self._matching_processes(instance_index):
            return True
        # Fallback: the instance may run headless, visible only as ADB device.
        devices = self.list_devices()
        return devices.get(self.get_serial(instance_index)) == "device"

    def start_instance(self, instance_index):
        """Start an LDPlayer instance via ldconsole and wait for it to boot.

        Return False after a generous (60s) window without confirmation.
        """
        instance = self._get_instance(instance_index)
        display_name = instance["display_name"]
        self.log(f"Starting LDPlayer instance {display_name}...", "info")
        if self._is_instance_running(instance_index):
            self.log(f"LDPlayer instance {display_name} is already running.", "info")
            return True
        # ldconsole launch is asynchronous: it returns immediately and the
        # emulator boots on its own, so it must be polled, not awaited.
        try:
            subprocess.Popen(
                [self.console_path, "launch", "--index", str(instance_index)],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        except OSError as e:
            self.log(f"Could not launch LDPlayer instance {display_name}: {e}", "error")
            return False
        for _ in range(30):
            if self._is_instance_running(instance_index):
                self.log(f"LDPlayer instance {display_name} started.", "success")
                return True
            time.sleep(2)
        self.log(f"Could not confirm LDPlayer instance {display_name} started.", "error")
        return False

    def stop_instance(self, instance_index):
        """Stop an LDPlayer instance gracefully via ldconsole quit."""
        instance = self._get_instance(instance_index)
        display_name = instance["display_name"]
        self.log(f"Stopping LDPlayer instance {display_name}...", "info")
        if not self._is_instance_running(instance_index):
            self.log(f"LDPlayer instance {display_name} is not running.", "info")
            return True
        run_process_robust([self.console_path, "quit", "--index", str(instance_index)], timeout=15)
        for _ in range(30):
            if not self._matching_processes(instance_index):
                self.log(f"LDPlayer instance {display_name} stopped.", "success")
                return True
            time.sleep(2)
        self.log(f"Could not stop LDPlayer instance {display_name}.", "error")
        return False

    def list_devices(self):
        """List devices using LDPlayer's own adb binary."""
        result = run_process_robust([self.adb_path, "devices"], timeout=15)
        if not result:
            return {}
        return parse_devices_output(result.stdout)

    def connect(self, device):
        """Connect LDPlayer's adb server to the given serial."""
        run_process_robust([self.adb_path, "connect", device], timeout=10)

    def kill_server(self):
        """Stop LDPlayer's adb server."""
        run_process_robust([self.adb_path, "kill-server"], timeout=10)

    def restart_server(self):
        """Restart LDPlayer's adb server."""
        self.kill_server()
        run_process_robust([self.adb_path, "start-server"], timeout=10)

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


def detect_installed_emulators():
    """Return the emulators installed, in a stable order.

    Returns:
        list: e.g. ["mumu", "bluestacks", "ldplayer"]; empty if none is detected.
    """
    installed = []
    if os.path.exists(MUMU_MULTI_PLAYER_PATH):
        installed.append(EMULATOR_MUMU)
    if os.path.exists(BLUESTACKS_CONF):
        installed.append(EMULATOR_BLUESTACKS)
    if os.path.exists(LDPLAYER_CONSOLE_PATH):
        installed.append(EMULATOR_LDPLAYER)
    return installed


def create_backend(emulator=None, log_func=None):
    """Create an emulator backend by name (defaults to MuMu).

    Args:
        emulator (str, optional): "mumu", "bluestacks", "ldplayer" or None.
        log_func (callable, optional): Logging function.

    Returns:
        EmulatorBackend: The chosen backend instance.
    """
    if emulator == EMULATOR_BLUESTACKS:
        return BlueStacksBackend(log_func=log_func)
    if emulator == EMULATOR_LDPLAYER:
        return LDPlayerBackend(log_func=log_func)
    return MuMuBackend(log_func=log_func)
