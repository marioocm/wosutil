"""Multi-instance emulator controller.

Manages launching, monitoring, and controlling multiple MuMu Player instances
without requiring the MuMu multi-instance manager: instances are discovered
from the ``vms`` folder, launched directly with MuMuNxDevice.exe and closed by
terminating their shell/hypervisor processes.
"""

import json
import logging
import os
import re
import subprocess
import time

import psutil

from wosutil.config import INSTANCE_CACHE_FILE, MUMU_INSTANCE_BASE_PATH
from wosutil.utils import (
    _detached_creation_flags,
    _has_process_option,
    _is_process_named,
    _iter_processes,
    _minimized_startupinfo,
    load_json_file,
    save_json_file,
)

logger = logging.getLogger(__name__)

_VM_FOLDER_RE = re.compile(r"^MuMuPlayerGlobal-[\d.]+-(\d+)$")


class MultiInstanceManager:
    """Manages multiple MuMu emulator instances: listing, starting, and stopping."""

    def __init__(self, log_func=None, instance_base_path=MUMU_INSTANCE_BASE_PATH):
        """Initializes the MultiInstanceManager.

        Args:
            log_func (callable): Optional logging function.
            instance_base_path (str): Directory containing MuMu instance folders.
        """
        self.log = log_func or logger.info
        self.instance_base_path = instance_base_path
        self.instances = []

    def _vm_name(self, idx):
        """Return the instance folder name, e.g. ``MuMuPlayerGlobal-15.0-0``.

        MuMu names the per-instance folder after the emulator series it was
        created with (e.g. ``MuMuPlayerGlobal-12.0-2`` or
        ``MuMuPlayerGlobal-15.0-2``), so the series must not be hardcoded.

        Args:
            idx (int): Instance index.

        Returns:
            str: The instance folder name. The best-effort guess
            (``MuMuPlayerGlobal-12.0-{idx}``) is returned when no folder
            matches, so a missing instance still surfaces as a warning.
        """
        if not os.path.isdir(self.instance_base_path):
            return f"MuMuPlayerGlobal-12.0-{idx}"
        try:
            return next(folder for folder in os.listdir(self.instance_base_path) if re.fullmatch(rf"MuMuPlayerGlobal-[\d.]+-{idx}", folder))
        except StopIteration:
            return f"MuMuPlayerGlobal-12.0-{idx}"

    def _instance_config_path(self, idx):
        """Locate the extra_config.json of an instance by its index.

        Args:
            idx (int): Instance index.

        Returns:
            str: Path to the instance's extra_config.json.
        """
        return os.path.join(self.instance_base_path, self._vm_name(idx), "configs", "extra_config.json")

    def _instance_name(self, idx):
        """Return the display name of an instance from its config file.

        Args:
            idx (int): Instance index.

        Returns:
            str: The instance display name (the window title), falling back
            to ``Instance {idx}`` when the config is missing or unreadable.
        """
        try:
            with open(self._instance_config_path(idx), encoding="utf-8") as f:
                return json.load(f).get("playerName", f"Instance {idx}")
        except (OSError, ValueError):
            return f"Instance {idx}"

    def _device_executable(self, idx):
        """Return the MuMuNxDevice.exe shell for the instance's series.

        Each instance series ships its own shell under
        ``nx_device/<series>/shell`` next to the ``vms`` folder that holds the
        instance folders.

        Args:
            idx (int): Instance index.

        Returns:
            str: Path to the instance's MuMuNxDevice.exe.
        """
        series = re.match(r"^MuMuPlayerGlobal-([\d.]+)-\d+$", self._vm_name(idx)).group(1)
        root = os.path.dirname(self.instance_base_path)
        return os.path.normpath(os.path.join(root, "nx_device", series, "shell", "MuMuNxDevice.exe"))

    def _device_argv(self, idx):
        """Return the argv that launches the instance VM directly.

        ``MuMuNxDevice.exe -v <index> --vm <folder>`` is exactly how the
        multi-instance manager opens an instance internally, so no manager
        process or window needs to be running first.

        Args:
            idx (int): Instance index.

        Returns:
            list: Full argv, e.g. [MuMuNxDevice.exe, "-v", "0", "--vm",
                "MuMuPlayerGlobal-15.0-0"].
        """
        return [self._device_executable(idx), "-v", str(idx), "--vm", self._vm_name(idx)]

    def _matching_instance_processes(self, idx):
        """Return the shell and hypervisor processes of an instance.

        The instance is the VM run by MuMuVMMHeadless (identified by its
        ``--comment <folder>``) hosted in the MuMuNxDevice.exe shell, so both
        processes must be gone for the instance to count as stopped.

        Args:
            idx (int): Instance index.

        Returns:
            list: The matching psutil process objects.
        """
        vm_name = self._vm_name(idx)
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

    def get_instances(self):
        """Retrieves the list of emulator instances and their names.

        Instances are discovered by scanning the ``vms`` folder (the folder
        name carries the instance index), so no manager process is needed.

        Returns:
            list: List of dictionaries with 'index' and 'name' for each instance.
        """
        self.log("Fetching emulator instances...", "info")
        self.instances = []
        if not os.path.isdir(self.instance_base_path):
            return []
        indices = set()
        for folder in os.listdir(self.instance_base_path):
            match = _VM_FOLDER_RE.fullmatch(folder)
            if match:
                indices.add(int(match.group(1)))
        for idx in sorted(indices):
            self.instances.append({"index": idx, "name": self._instance_name(idx)})
        save_instance_cache(self.instances)
        return self.instances

    def _is_instance_running(self, index):
        """Checks if a specific emulator instance is running.

        The shell process (MuMuNxDevice.exe) or its hypervisor process
        (MuMuVMMHeadless.exe) only exist while the instance is up, so their
        ``--vm``/``--comment`` arguments identify the instance without any
        manager.

        Args:
            index (int): Instance index.

        Returns:
            bool: True if running, False otherwise.
        """
        return bool(self._matching_instance_processes(index))

    def start_instance(self, index, on_launch=None):
        """Starts a MuMu emulator instance and waits until it is running.

        The instance VM is launched directly with MuMuNxDevice.exe (the same
        argv the multi-instance manager uses internally), so neither the
        manager window nor its process needs to be running first.

        Args:
            index (int): Instance index.
            on_launch (callable, optional): Called right after the launch
                command is issued, before waiting for the instance to boot
                (e.g. to minimize the emulator window as soon as it appears).

        Returns:
            bool: True if started successfully, False otherwise.
        """
        self.log(f"Starting MuMu instance {index}...", "info")
        if self._is_instance_running(index):
            self.log(f"Instance {index} is already running.", "info")
            if on_launch is not None:
                on_launch()
            return True
        try:
            subprocess.Popen(
                self._device_argv(index),
                creationflags=_detached_creation_flags(),
                startupinfo=_minimized_startupinfo(),
            )
        except OSError as e:
            self.log(f"Could not launch instance {index}: {e}", "error")
            return False
        if on_launch is not None:
            on_launch()
        # Wait until the instance is running
        for _ in range(30):
            running = self._is_instance_running(index)
            self.log(f"Instance {index} state: {'running' if running else 'not running'}", "debug")
            if running:
                self.log(f"Instance {index} started.", "success")
                return True
            time.sleep(2)
        self.log(f"Could not start instance {index}.", "error")
        return False

    def stop_instance(self, index):
        """Stops a MuMu emulator instance by terminating its processes.

        The shell and hypervisor processes are terminated directly (like the
        BlueStacks backend does), so no manager process or window is involved
        and a hung emulator is closed as fast as it can be killed. A graceful
        shutdown through the manager CLI is not attempted: the CLI may spawn
        the manager window when its process is not running.

        Args:
            index (int): Instance index.

        Returns:
            bool: True if stopped successfully, False otherwise.
        """
        self.log(f"Stopping MuMu instance {index}...", "info")
        processes = self._matching_instance_processes(index)
        if not processes:
            self.log(f"Instance {index} is not running.", "info")
            return True
        for proc in processes:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        # Wait until the instance is stopped
        for _ in range(5):
            if not self._matching_instance_processes(index):
                self.log(f"Instance {index} stopped.", "success")
                return True
            time.sleep(2)
        for proc in self._matching_instance_processes(index):
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for _ in range(5):
            if not self._matching_instance_processes(index):
                self.log(f"Instance {index} stopped.", "success")
                return True
            time.sleep(2)
        self.log(f"Could not stop instance {index}.", "error")
        return False


def save_instance_cache(instances):
    """Saves the list of emulator instances to a cache file.

    Args:
        instances (list): List of instance dictionaries.
    """
    save_json_file(INSTANCE_CACHE_FILE, instances)


def load_instance_cache():
    """Loads the list of emulator instances from the cache file.

    Returns:
        list: List of instance dictionaries, or empty list if not found.
    """
    instances = load_json_file(INSTANCE_CACHE_FILE, default_value=[])
    if not isinstance(instances, list):
        return []
    valid_instances = []
    seen_indices = set()
    for instance in instances:
        if not isinstance(instance, dict):
            continue
        index = instance.get("index")
        name = instance.get("name")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            continue
        if not isinstance(name, str) or not name.strip() or index in seen_indices:
            continue
        valid_instances.append({"index": index, "name": name})
        seen_indices.add(index)
    return valid_instances
