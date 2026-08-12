"""Multi-instance emulator controller.

Manages launching, monitoring, and controlling multiple MuMu Player instances.
"""

import json
import logging
import os
import re
import time

from wosutil.config import INSTANCE_CACHE_FILE, MUMU_INSTANCE_BASE_PATH, MUMU_MULTI_PLAYER_PATH
from wosutil.utils import run_process_robust, save_json_file

logger = logging.getLogger(__name__)


class MultiInstanceManager:
    """Manages multiple MuMu emulator instances: listing, starting, and stopping."""

    def __init__(self, log_func=None):
        """Initializes the MultiInstanceManager.

        Args:
            log_func (callable): Optional logging function.
        """
        self.log = log_func or logger.info
        self.instances = []

    def _execute_mumu_cli(self, args):
        """Executes a MuMuManager CLI command with the given arguments.

        Args:
            args (list): List of arguments for the CLI.

        Returns:
            str: The stdout output of the command.
        """
        if not os.path.exists(MUMU_MULTI_PLAYER_PATH):
            self.log(f"MuMuManager.exe not found at {MUMU_MULTI_PLAYER_PATH}", "error")
            return ""
        command = [MUMU_MULTI_PLAYER_PATH, "api"] + args
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
        if not os.path.isdir(MUMU_INSTANCE_BASE_PATH):
            return os.path.join(MUMU_INSTANCE_BASE_PATH, f"MuMuPlayerGlobal-12.0-{idx}", "configs", "extra_config.json")
        try:
            version_dir = next(folder for folder in os.listdir(MUMU_INSTANCE_BASE_PATH) if re.fullmatch(rf"MuMuPlayerGlobal-[\d.]+-{idx}", folder))
        except StopIteration:
            version_dir = f"MuMuPlayerGlobal-12.0-{idx}"
        return os.path.join(MUMU_INSTANCE_BASE_PATH, version_dir, "configs", "extra_config.json")

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
    if os.path.exists(INSTANCE_CACHE_FILE):
        try:
            with open(INSTANCE_CACHE_FILE, encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return []
                return json.loads(content)
        except Exception:
            return []
    return []
