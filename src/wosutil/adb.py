"""Pure ADB transport.

This module knows how to talk to an ``adb`` binary; it does not know about
emulators, instance lifecycles or game logic. An emulator backend owns one
``AdbClient`` and supplies the serial of each instance.
"""

from wosutil.utils import run_process_robust, run_process_robust_bytes


def parse_devices_output(stdout):
    """Parse the output of ``adb devices`` into a {serial: state} dict.

    Args:
        stdout: The raw output of the devices command.

    Returns:
        dict: Mapping of device serial to connection state ("device",
        "offline").
    """
    devices: dict[str, str] = {}
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line or "List of devices" in line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices[parts[0]] = parts[1].lower()
    return devices


class AdbClient:
    """Run ADB commands against one or more device serials."""

    def __init__(self, adb_path):
        """Store the adb executable used by every command."""
        self.adb_path = adb_path

    def build_command(self, serial, command_parts):
        """Return the full argv for a command targeting ``serial``."""
        return [self.adb_path, "-s", serial, *command_parts]

    def run(self, serial, command_parts, timeout=10):
        """Run an ADB command and return its text result (None on timeout)."""
        return run_process_robust(self.build_command(serial, command_parts), timeout=timeout)

    def run_bytes(self, serial, command_parts, timeout=10):
        """Run an ADB command and return its raw-byte result (None on timeout)."""
        return run_process_robust_bytes(self.build_command(serial, command_parts), timeout=timeout)

    def devices(self):
        """Return the current ``{serial: state}`` map from the ADB server."""
        result = run_process_robust([self.adb_path, "devices"], timeout=15)
        return parse_devices_output(result.stdout) if result else {}

    def connect(self, serial):
        """Ask the ADB server to connect to ``serial``."""
        return run_process_robust([self.adb_path, "connect", serial], timeout=10)

    def disconnect(self, serial):
        """Ask the ADB server to disconnect from ``serial``."""
        return run_process_robust([self.adb_path, "disconnect", serial], timeout=10)

    def kill_server(self):
        """Stop the ADB server."""
        return run_process_robust([self.adb_path, "kill-server"], timeout=10)

    def start_server(self):
        """Start the ADB server."""
        return run_process_robust([self.adb_path, "start-server"], timeout=10)

    def restart_server(self):
        """Restart the ADB server."""
        self.kill_server()
        self.start_server()
