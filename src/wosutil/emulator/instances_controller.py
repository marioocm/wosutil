"""Multi-instance emulator controller.

Manages launching, monitoring, and controlling multiple MuMu Player instances.
"""

import json
import logging
import os
import re
import time

from wosutil.config import INSTANCE_CACHE_FILE, MUMU_INSTANCE_BASE_PATH, MUMU_MULTI_PLAYER_PATH
from wosutil.utils import load_json_file, run_process_robust, save_json_file

logger = logging.getLogger(__name__)


class MultiInstanceManager:
    """Manages multiple MuMu emulator instances: listing, starting, and stopping."""

    def __init__(self, log_func=None, multi_player_path=MUMU_MULTI_PLAYER_PATH, instance_base_path=MUMU_INSTANCE_BASE_PATH):
        """Initializes the MultiInstanceManager.

        Args:
            log_func (callable): Optional logging function.
            multi_player_path (str): Path to MuMuManager.exe.
            instance_base_path (str): Directory containing MuMu instance folders.
        """
        self.log = log_func or logger.info
        self.multi_player_path = multi_player_path
        self.instance_base_path = instance_base_path
        self.instances = []

    def _execute_mumu_cli(self, args):
        """Executes a MuMuManager CLI command with the given arguments.

        Args:
            args (list): List of arguments for the CLI.

        Returns:
            str: The stdout output of the command.
        """
        if not os.path.exists(self.multi_player_path):
            self.log(f"MuMuManager.exe not found at {self.multi_player_path}", "error")
            return ""
        command = [self.multi_player_path, "api"] + args
        try:
            result = run_process_robust(command, timeout=30)
            if not result:
                self.log("MuMu CLI command timed out", "error")
                return ""
            if result.stderr:
                self.log(f"MuMu CLI stderr: {result.stderr}", "error")
            return result.stdout
        except Exception as e:
            self.log(f"Error executing MuMu CLI: {e}", "error")
            return ""

    def _instance_config_path(self, idx):
        """Locate the extra_config.json of an instance by its index.

        MuMu names the per-instance folder after the emulator series it was
        created with (e.g. ``MuMuPlayerGlobal-12.0-2`` or
        ``MuMuPlayerGlobal-15.0-2``), so the series must not be hardcoded.

        Args:
            idx (int): Instance index.

        Returns:
            str: Path to the instance's extra_config.json. The best-effort
            guess (``MuMuPlayerGlobal-12.0-{idx}``) is returned when no folder
            matches, so a missing file still surfaces as a warning.
        """
        if not os.path.isdir(self.instance_base_path):
            return os.path.join(self.instance_base_path, f"MuMuPlayerGlobal-12.0-{idx}", "configs", "extra_config.json")
        try:
            version_dir = next(folder for folder in os.listdir(self.instance_base_path) if re.fullmatch(rf"MuMuPlayerGlobal-[\d.]+-{idx}", folder))
        except StopIteration:
            version_dir = f"MuMuPlayerGlobal-12.0-{idx}"
        return os.path.join(self.instance_base_path, version_dir, "configs", "extra_config.json")

    def get_instances(self):
        """Retrieves the list of emulator instances and their names.

        Returns:
            list: List of dictionaries with 'index' and 'name' for each instance.
        """
        self.log("Fetching emulator instances...", "info")
        self.instances = []
        raw_list = self._execute_mumu_cli(["get_player_list"])
        if not raw_list:
            return []
        match = re.search(r"get player list: \[(.*?)\].*result: 0", raw_list)
        if not match:
            return []
        indices = [int(x.strip()) for x in match.group(1).split(",") if x.strip().isdigit()]
        for idx in indices:
            config_path = self._instance_config_path(idx)
            player_name = f"Instance {idx}"
            try:
                with open(config_path, encoding="utf-8") as f:
                    data = json.load(f)
                    player_name = data.get("playerName", player_name)
            except Exception as e:
                self.log(f"Could not read instance name {idx}: {e}", "warning")
            self.instances.append({"index": idx, "name": player_name})
        save_instance_cache(self.instances)
        return self.instances

    def _is_instance_running(self, index):
        """Checks if a specific emulator instance is running.

        Args:
            index (int): Instance index.

        Returns:
            bool: True if running, False otherwise.
        """
        out = self._execute_mumu_cli(["-v", str(index), "player_state"])
        self.log(f"player_state output for instance {index}: {out}", "debug")
        return "state=start_finished" in out

    def start_instance(self, index):
        """Starts a MuMu emulator instance and waits until it is running.

        Args:
            index (int): Instance index.

        Returns:
            bool: True if started successfully, False otherwise.
        """
        self.log(f"Starting MuMu instance {index}...", "info")
        out = self._execute_mumu_cli(["-v", str(index), "launch_player"])
        self.log(f"MuMu CLI output: {out}", "debug")
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
        """Stops a MuMu emulator instance and waits until it is stopped.

        Args:
            index (int): Instance index.

        Returns:
            bool: True if stopped successfully, False otherwise.
        """
        self.log(f"Stopping MuMu instance {index}...", "info")
        out = self._execute_mumu_cli(["-v", str(index), "shutdown_player"])
        self.log(f"MuMu CLI output: {out}", "debug")
        # Wait until the instance is stopped
        for _ in range(30):
            running = self._is_instance_running(index)
            if not running:
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
