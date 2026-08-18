"""Emulator management module.

Handles MuMu Player emulator control, ADB commands, and game launching.
"""

import contextlib
import logging
import os
import tempfile
import time

from wosutil.config import (
    BACK_BUTTON_DELAY,
    CLICK_DELAY,
    WHITEOUT_ACTIVITY,
    WHITEOUT_PACKAGE,
)
from wosutil.emulator.backends import MuMuBackend
from wosutil.stop import ToolStopped, stop_signal
from wosutil.utils import get_coordinates, log_message, run_process_robust

logger = logging.getLogger(__name__)

# Successful ADB verifications per serial, cached for a short window
# so per-screenshot checks don't re-run subprocesses or spam the log.
_adb_verified_cache: dict = {}

# The emulator backend selected at startup; all ADB calls below delegate to it.
_active_backend = None


def set_active_backend(backend):
    """Register the emulator backend used for every ADB command.

    Args:
        backend: The EmulatorBackend instance to use.
    """
    global _active_backend
    _active_backend = backend


def get_active_backend():
    """Return the active emulator backend.

    Defaults to the MuMu backend when none was explicitly registered, keeping
    the pre-refactor behavior for callers that never set one.

    Returns:
        EmulatorBackend: The active backend.
    """
    global _active_backend
    if _active_backend is None:
        _active_backend = MuMuBackend()
    return _active_backend


def execute_adb_command(command_parts, instance_index, timeout=10, log_errors_as_info=None):
    """Executes an ADB command on the active emulator backend.

    Args:
        command_parts (list): Command parts to execute.
        timeout (int): Command timeout in seconds (reduced from 15 to 10).
        log_errors_as_info (str, optional): If provided, log errors as info with this prefix.
        instance_index (int): Emulator instance index.

    Returns:
        subprocess.CompletedProcess: Command execution result.
    """
    stop_signal.check()
    backend = get_active_backend()
    full_command = backend.build_adb_command(command_parts, instance_index)
    short_command = " ".join(["adb", "-s", backend.get_serial(instance_index)] + command_parts)

    log_message(f"Executing: {short_command}", level="adb")

    result = run_process_robust(full_command, timeout=timeout)
    if result is None:
        log_message(f"ADB command timed out after {timeout} seconds: {short_command}", level="error")
        if log_errors_as_info:
            log_message("Error executing ADB command: command timed out", level="error")
        return None

    # adb pull/push writes normal progress ("1 file pulled...") to stderr:
    # only treat stderr as a warning when the command actually failed.
    if result.stderr and not log_errors_as_info and result.returncode != 0:
        log_message(f"ADB stderr: {result.stderr}", level="warning")
    return result


def get_adb_serial(instance_index):
    """Returns the ADB serial (host:port) for the given emulator instance.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        str: ADB serial, e.g. "127.0.0.1:16416" for instance 1.
    """
    return get_active_backend().get_serial(instance_index)


def _list_adb_devices():
    """Runs 'adb devices' and parses the output into a {serial: state} dict.

    Returns:
        dict: Mapping of device serial to connection state ("device", "offline", ...).
    """
    return get_active_backend().list_devices()


def _connect_adb_device(serial):
    """Explicitly connects the ADB server to the emulator endpoint."""
    get_active_backend().connect(serial)


def verify_adb_connected(instance_index, max_attempts=5, wait=3, cache_seconds=15):
    """Verifies that ADB is connected to the emulator.

    Checks the real 'adb devices' list for the instance's serial instead of
    blindly matching the word "device" in the output (which matches the header
    "List of devices attached" even when no device is present). If the device
    is missing or offline, explicitly reconnects to it and retries. If 'adb
    devices' itself returns nothing, the server is broken and the retries are
    skipped in favor of restarting it directly. Restarting severs the
    emulator's connection, so it is only used as a last resort otherwise.

    Successful verifications are cached for ``cache_seconds`` so repeated
    checks (e.g. before every screenshot) don't spam the log or re-run
    subprocesses unnecessarily.

    Args:
        instance_index (int): Emulator instance index.
        max_attempts (int): Attempts before restarting the ADB server.
        wait (int): Seconds to wait between attempts.
        cache_seconds (int): Seconds a successful verification stays valid.

    Returns:
        bool: True if ADB is connected, False otherwise.
    """
    serial = get_adb_serial(instance_index)

    last_verified = _adb_verified_cache.get(serial)
    if last_verified and time.time() - last_verified < cache_seconds:
        return True

    log_message(f"Attempting to connect ADB ({serial})...", level="adb")

    for attempt in range(max_attempts):
        stop_signal.check()
        devices = _list_adb_devices()
        if not devices:
            # The 'adb devices' command itself failed or returned nothing: the
            # ADB server is broken, so re-running the same command won't help.
            # Skip the remaining retries and restart the server directly.
            log_message("ADB devices command failed or returned no output.", level="warning")
            break
        state = devices.get(serial)
        if state == "device":
            log_message("ADB connected successfully.", level="success")
            _adb_verified_cache[serial] = time.time()
            return True
        if state == "offline":
            log_message(f"ADB device {serial} is offline. Reconnecting...", level="warning")
        else:
            log_message(f"ADB device {serial} not found in device list. Connecting...", level="warning")
        _connect_adb_device(serial)

        if attempt < max_attempts - 1:
            time.sleep(wait)

    # Last resort: restart the ADB server and reconnect explicitly
    restart_adb_server()
    stop_signal.check()
    time.sleep(3)
    _connect_adb_device(serial)
    devices = _list_adb_devices()
    if devices.get(serial) == "device":
        log_message("ADB connected successfully after server restart.", level="success")
        _adb_verified_cache[serial] = time.time()
        return True

    log_message("Could not establish or verify ADB connection with the emulator.", level="error")
    return False


def is_wos_running(instance_index, verbose=True):
    """Checks if Whiteout Survival is running on the emulator.

    Args:
        instance_index (int): Emulator instance index.
        verbose (bool): If True, log the check and its result.

    Returns:
        bool: True if the game is running, False otherwise.
    """
    if verbose:
        log_message("Checking if the game is already running...", level="info")
    result = execute_adb_command(["shell", "pidof", WHITEOUT_PACKAGE], instance_index)

    if result and result.stdout.strip():
        if verbose:
            log_message("Whiteout Survival is already running.", level="success")
        return True
    else:
        if verbose:
            log_message("Whiteout Survival is not running.", level="info")
        return False


def is_wos_installed(instance_index):
    """Checks if Whiteout Survival is installed on the emulator.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the game is installed, False otherwise.
    """
    log_message("Checking if the game is installed...", level="info")
    result = execute_adb_command(["shell", "pm", "list", "packages", WHITEOUT_PACKAGE], instance_index)

    if result and WHITEOUT_PACKAGE in result.stdout:
        log_message(f"Whiteout Survival ({WHITEOUT_PACKAGE}) is installed.", level="success")
        return True
    else:
        log_message(f"Whiteout Survival ({WHITEOUT_PACKAGE}) not installed.", level="info")
        return False


def take_screenshot(instance_index):
    """Takes a screenshot from the emulator and saves it to a temporary file.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        str or None: Path to the saved temporary screenshot, or None if failed.
    """
    stop_signal.check()
    if not verify_adb_connected(instance_index):
        log_message("No active ADB connection. Cannot take screenshot.", level="error")
        return None

    with tempfile.NamedTemporaryFile(suffix=".png", prefix="wosutil_", delete=False) as tmp:
        filename = os.path.basename(tmp.name)
        remote_path = f"/sdcard/{filename}"
        local_full_path = tmp.name

        log_message(f"Taking remote screenshot on: {remote_path}", level="debug")
        result_screencap = execute_adb_command(["shell", "screencap", "-p", remote_path], instance_index)

        if not result_screencap or result_screencap.returncode != 0:
            log_message("Failed to take screenshot on the emulator.", level="error")
            return None

        log_message(f"Downloading screenshot to temporary file: {local_full_path}", level="debug")
        result_pull = execute_adb_command(["pull", remote_path, local_full_path], instance_index)

        if not result_pull or result_pull.returncode != 0:
            log_message("Failed to download screenshot from the emulator.", level="error")
            execute_adb_command(["shell", "rm", remote_path], instance_index)
            return None

        # Clean up remote file
        log_message("Deleting screenshot from emulator...", level="debug")
        execute_adb_command(["shell", "rm", remote_path], instance_index)

        log_message(f"Screenshot saved to temporary file: {local_full_path}", level="debug")
        return local_full_path


def delete_temp_screenshot(screenshot_path):
    """Delete a temporary screenshot file created by take_screenshot.

    Callers must remove the file after they are done with it; failures are
    ignored (the file is cleaned up by cleanup_stale_temp_screenshots later).

    Args:
        screenshot_path (str or None): Path to the temporary screenshot.
    """
    if not screenshot_path:
        return
    with contextlib.suppress(OSError):
        os.remove(screenshot_path)


def cleanup_stale_temp_screenshots(max_age_seconds=24 * 60 * 60):
    """Remove leftover ``wosutil_*.png`` screenshot files from the system temp folder.

    Screenshots are deleted right after use; this only catches files left
    behind by crashed sessions. Call it once at application startup.

    Args:
        max_age_seconds (int): Files older than this are removed.
    """
    try:
        temp_dir = tempfile.gettempdir()
        now = time.time()
        for name in os.listdir(temp_dir):
            if not name.startswith("wosutil_") or not name.endswith(".png"):
                continue
            path = os.path.join(temp_dir, name)
            try:
                if now - os.path.getmtime(path) > max_age_seconds:
                    os.remove(path)
            except OSError:
                continue
    except OSError:
        pass


def click_on_coordinates(x, y, instance_index, delay=CLICK_DELAY):
    """Clicks on specific coordinates on the emulator screen.

    Args:
        x (int): X coordinate.
        y (int): Y coordinate.
        delay (float): Delay after clicking in seconds.
        instance_index (int): Emulator instance index.
    """
    log_message(f"Clicking on coordinates: ({x}, {y})", level="info")
    execute_adb_command(["shell", "input", "tap", str(x), str(y)], instance_index)
    stop_signal.check()
    time.sleep(delay)


def click_on(coordinate_name, instance_index, delay=CLICK_DELAY):
    """Clicks on a named coordinate from the COORDINATES dictionary.

    Args:
        coordinate_name (str): Name of the coordinate (e.g., "world", "alliance", "shop").
        instance_index (int): Emulator instance index.
        delay (float): Delay after clicking in seconds.

    Returns:
        bool: True if click was successful, False if coordinate not found.
    """
    coordinates = get_coordinates(coordinate_name)
    if coordinates:
        x, y = coordinates
        click_on_coordinates(x, y, instance_index, delay)
        return True
    else:
        log_message(f"Could not click on '{coordinate_name}': coordinate not found", level="error")
        return False


def _scroll_with_hold(start_x, start_y, end_x, end_y, duration_ms, hold_end_ms, instance_index, steps=8):
    """Scrolls while keeping the finger pressed at the end point.

    ``adb input swipe`` always lifts the finger at the end, so the list keeps
    gliding (fling) after release. This helper sends a continuous gesture with
    ``input motionevent`` (DOWN -> several MOVEs -> hold -> UP): the finger
    reaches the end, stays still for ``hold_end_ms`` and only then lifts, which
    makes the release velocity zero and stops the scroll exactly where expected.

    On Android versions without ``motionevent`` the command fails and a slow
    swipe followed by a press at the end point is used as a fallback.

    Args:
        start_x (int): Starting X coordinate.
        start_y (int): Starting Y coordinate.
        end_x (int): Ending X coordinate.
        end_y (int): Ending Y coordinate.
        duration_ms (int): Approximate duration of the scroll in milliseconds.
        hold_end_ms (int): Milliseconds the finger stays pressed at the end point.
        instance_index (int): Emulator instance index.
        steps (int): Number of intermediate movement events.
    """
    down = ["shell", "input", "motionevent", "DOWN", str(start_x), str(start_y)]
    result = execute_adb_command(down, instance_index)
    if result is None or result.returncode != 0:
        log_message("input motionevent not supported, falling back to swipe + press at the end point.", level="warning")
        execute_adb_command(["shell", "input", "swipe", str(start_x), str(start_y), str(end_x), str(end_y), str(duration_ms + hold_end_ms)], instance_index)
        execute_adb_command(["shell", "input", "swipe", str(end_x), str(end_y), str(end_x), str(end_y), str(hold_end_ms)], instance_index)
        return
    for i in range(1, steps + 1):
        mx = start_x + (end_x - start_x) * i // steps
        my = start_y + (end_y - start_y) * i // steps
        execute_adb_command(["shell", "input", "motionevent", "MOVE", str(mx), str(my)], instance_index)
        time.sleep(duration_ms / steps / 1000.0)
    time.sleep(hold_end_ms / 1000.0)
    execute_adb_command(["shell", "input", "motionevent", "UP", str(end_x), str(end_y)], instance_index)


def scroll_screen(start_x, start_y, end_x, end_y, duration_ms, instance_index, hold_end_ms=0):
    """Performs a scroll gesture on the emulator screen.

    When ``hold_end_ms`` is greater than zero the finger is kept still at the
    end point before lifting, which stops the scrolling momentum instead of
    letting the list keep gliding after the finger is released.

    Args:
        start_x (int): Starting X coordinate.
        start_y (int): Starting Y coordinate.
        end_x (int): Ending X coordinate.
        end_y (int): Ending Y coordinate.
        duration_ms (int): Duration of the scroll in milliseconds.
        instance_index (int): Emulator instance index.
        hold_end_ms (int): Extra milliseconds to hold the finger at the end point (0 to skip).
    """
    log_message(f"Performing scroll from ({start_x}, {start_y}) to ({end_x}, {end_y}) over {duration_ms}ms", level="info")
    if hold_end_ms > 0:
        _scroll_with_hold(start_x, start_y, end_x, end_y, duration_ms, hold_end_ms, instance_index)
        return
    execute_adb_command(["shell", "input", "swipe", str(start_x), str(start_y), str(end_x), str(end_y), str(duration_ms)], instance_index)


def long_press_on_coordinates(x, y, duration_ms, instance_index):
    """Performs a long press on specific coordinates.

    Args:
        x (int): X coordinate.
        y (int): Y coordinate.
        duration_ms (int): Duration of the press in milliseconds.
        instance_index (int): Emulator instance index.
    """
    log_message(f"Long pressing on coordinates: ({x}, {y}) for {duration_ms}ms", level="info")
    execute_adb_command(["shell", "input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms)], instance_index)


def press_android_back_button(instance_index, delay=BACK_BUTTON_DELAY):
    """Presses the Android back button.

    Args:
        delay (float): Delay after pressing in seconds.
        instance_index (int): Emulator instance index.
    """
    log_message("Pressing Android back button...", level="info")
    execute_adb_command(["shell", "input", "keyevent", "4"], instance_index)
    stop_signal.check()
    time.sleep(delay)


def force_stop_game(instance_index):
    """Forces the game to stop.

    Args:
        instance_index (int): Emulator instance index.
    """
    log_message(f"Forcing game '{WHITEOUT_PACKAGE}' to stop...", level="warning")
    execute_adb_command(["shell", "am", "force-stop", WHITEOUT_PACKAGE], instance_index)


def launch_game_activity(instance_index):
    """Launches the main activity of Whiteout Survival.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if successful, False otherwise.
    """
    log_message("Starting Whiteout Survival...", level="info")
    result = execute_adb_command(["shell", "am", "start", "-n", f"{WHITEOUT_PACKAGE}/{WHITEOUT_ACTIVITY}"], instance_index)
    if not result or result.returncode != 0:
        log_message(f"Could not start the main activity '{WHITEOUT_ACTIVITY}'. Attempting to start with the general launcher...", level="warning")
        # NOTE: avoid the -c flag: MuMuManager's argument parser swallows it,
        # producing "adb.exe: unknown command android.intent.category.LAUNCHER".
        result_alt = execute_adb_command(["shell", "monkey", "-p", WHITEOUT_PACKAGE, "1"], instance_index)
        return result_alt and result_alt.returncode == 0

    return True


def launch_and_verify_game(instance_index):
    """Close the game, relaunch it and verify that the process stays active for 20 seconds.

    Args:
        instance_index (int): Instance index.

    Returns:
        bool: True if the game is running for 20 seconds, False otherwise.
    """
    from wosutil.emulator.emulator_manager import force_stop_game, is_wos_running, launch_game_activity

    log_message(f"Closing and relaunching the game on instance {instance_index}...", "info")

    # Close the game
    force_stop_game(instance_index)

    # Wait a bit before relaunching
    if stop_signal.wait(timeout=2):
        raise ToolStopped()

    # Relaunch the game
    launch_game_activity(instance_index)

    # Verify the process stays active every 5 seconds for 35 seconds
    for check in range(7):  # 7 checks * 5 seconds = 35 seconds total
        if stop_signal.wait(timeout=5):
            raise ToolStopped()

        if not is_wos_running(instance_index, verbose=False):
            log_message(f"Game process not detected during check {check + 1}/7 on instance {instance_index}.", "warning")
            return False
        else:
            log_message(f"Game process verified active (check {check + 1}/7) on instance {instance_index}.", "info")

    log_message(f"Game successfully launched and verified active for 20 seconds on instance {instance_index}.", "success")
    return True


def restart_adb_server():
    """Restarts the ADB server using the active emulator's binary."""
    log_message("Restarting ADB server...", level="warning")
    try:
        get_active_backend().restart_server()
        log_message("ADB server restarted successfully.", level="success")
    except Exception as e:
        log_message(f"Error restarting ADB server: {e}", level="error")


def check_emulator_health(instance_index):
    """Checks if the emulator is healthy and responsive.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if emulator is healthy, False if hanging or unresponsive.
    """
    log_message(f"Checking health of emulator instance {instance_index}...", level="info")

    # Try a simple ADB command with short timeout
    result = execute_adb_command(["shell", "echo", "health_check"], instance_index, timeout=5)

    if result and result.returncode == 0:
        log_message(f"Emulator instance {instance_index} is healthy.", level="success")
        return True
    else:
        log_message(f"Emulator instance {instance_index} appears to be hanging or unresponsive.", level="warning")
        return False


def force_restart_emulator(instance_index, multi_instance_manager):
    """Forces a restart of the emulator instance when it's hanging.

    Args:
        instance_index (int): Emulator instance index.
        multi_instance_manager: The multi-instance manager object.

    Returns:
        bool: True if restart was successful, False otherwise.
    """
    log_message(f"Force restarting emulator instance {instance_index} due to hanging...", level="warning")

    try:
        # Stop the instance
        multi_instance_manager.stop_instance(instance_index)
        stop_signal.check()
        time.sleep(3)  # Wait for emulator to close

        # Start the instance again
        multi_instance_manager.start_instance(instance_index)
        stop_signal.check()
        time.sleep(5)  # Wait for emulator to start

        # Check if restart was successful
        if check_emulator_health(instance_index):
            log_message(f"Emulator instance {instance_index} restarted successfully.", level="success")
            return True
        else:
            log_message(f"Emulator instance {instance_index} still unresponsive after restart.", level="error")
            return False
    except Exception as e:
        log_message(f"Error during force restart of emulator instance {instance_index}: {e}", level="error")
        return False
