"""Common utilities for the WoS Util application.

Centralized functions for common operations across the application.

Note: For image matching operations, it is recommended to use temporary
files for screenshots. The image_utils functions expect temporary file
paths and do not delete the files themselves.
"""

import json
import logging
import os
import subprocess
import tempfile
import time
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

from wosutil.config import LOG_FILE
from wosutil.stop import ToolStopped

# Global variable to store the GUI log widget
gui_log_widget = None
_logging_configured = False

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def rotation_namer(default_name: str) -> str:
    """Rename rotated backups so the index goes before the ``.log`` extension.

    The default handler names backups ``wosutil_...log.1``; this produces
    ``wosutil_...1.log`` so the ``.log`` extension always appears last.
    """
    root, ext = os.path.splitext(LOG_FILE)
    if default_name.startswith(LOG_FILE + "."):
        return root + "." + default_name[len(LOG_FILE) + 1 :] + ext
    return default_name


def setup_logging(log_to_file: bool = True) -> None:
    """Configure console and optional file logging.

    Writes to ``logs/wosutil_<timestamp>.log`` so agents and users can tail the
    same file without relying on the GUI or a specific terminal session.

    Args:
        log_to_file: If True, also write logs to the rotating file handler.
    """
    global _logging_configured
    if _logging_configured:
        return

    root_logger = logging.getLogger()
    # Handlers are DEBUG-level: debug messages are gated in log_message based
    # on the debug mode, so normal sessions never emit them.
    root_logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers if something else already configured logging
    if not root_logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root_logger.addHandler(console_handler)

    if log_to_file:
        from wosutil.config import LOG_DIR, LOG_FILE

        ensure_directory_exists(LOG_DIR)
        # Rotating file: 5 MB x 3 backups; flush every record for live tailing
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.namer = rotation_namer
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

        original_emit = file_handler.emit

        def emit_and_flush(record: logging.LogRecord) -> None:
            original_emit(record)
            file_handler.flush()

        file_handler.emit = emit_and_flush  # type: ignore[method-assign]
        root_logger.addHandler(file_handler)

    _logging_configured = True
    logging.getLogger("wosutil").info("--- Session started ---")


def set_gui_log_widget(widget):
    """Set the GUI log widget for displaying logs in the interface."""
    global gui_log_widget
    gui_log_widget = widget


def ensure_directory_exists(directory_path: str) -> bool:
    """Ensure a directory exists, creating it if necessary.

    Args:
        directory_path (str): Path to the directory.

    Returns:
        bool: True if directory exists or was created successfully.
    """
    try:
        Path(directory_path).mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logging.error(f"Failed to create directory {directory_path}: {e}")
        return False


def load_json_file(file_path: str, default_value: Any = None) -> Any:
    """Load data from a JSON file with error handling.

    Args:
        file_path (str): Path to the JSON file.
        default_value (Any): Default value to return if file doesn't exist or is invalid.

    Returns:
        Any: Loaded data or default value.
    """
    try:
        if not os.path.exists(file_path):
            return default_value

        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load JSON file {file_path}: {e}")
        return default_value


def save_json_file(file_path: str, data: Any, indent: int = 2) -> bool:
    """Save data to a JSON file with error handling.

    Args:
        file_path (str): Path to the JSON file.
        data (Any): Data to save.
        indent (int): JSON indentation.

    Returns:
        bool: True if saved successfully.
    """
    try:
        # Ensure directory exists
        directory = os.path.dirname(file_path)
        if directory and not ensure_directory_exists(directory):
            return False

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        return True
    except Exception as e:
        logging.error(f"Failed to save JSON file {file_path}: {e}")
        return False


def safe_int(value: Any, default: int = 0) -> int:
    """Safely convert a value to integer.

    Args:
        value (Any): Value to convert.
        default (int): Default value if conversion fails.

    Returns:
        int: Converted integer or default value.
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def retry_operation(operation, max_attempts: int = 3, delay: float = 1.0, exceptions: tuple = (Exception,)):
    """Retry an operation with exponential backoff.

    Args:
        operation: Function to retry.
        max_attempts (int): Maximum number of attempts.
        delay (float): Initial delay between attempts.
        exceptions (tuple): Exceptions to catch and retry.

    Returns:
        Any: Result of the operation.

    Raises:
        Exception: Last exception if all attempts fail.
    """
    last_exception = None

    for attempt in range(max_attempts):
        try:
            return operation()
        except ToolStopped:
            raise
        except exceptions as e:
            last_exception = e
            if attempt < max_attempts - 1:
                import time

                time.sleep(delay * (2**attempt))  # Exponential backoff

    if last_exception:
        raise last_exception
    else:
        raise Exception("Operation failed after all attempts")


def _terminate_process_tree(process):
    """Terminates a process and its children (best effort)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        else:
            process.kill()
    except Exception:
        import contextlib

        with contextlib.suppress(Exception):
            process.kill()


def run_process_robust(command: list, timeout: int = 30):
    """Run a subprocess redirecting output to temporary files instead of pipes.

    ``subprocess.run`` with ``capture_output=True`` can hang forever on Windows
    when the child spawns a long-lived daemon (e.g. the ADB server) that
    inherits the stdout/stderr pipe handles: ``communicate()`` blocks waiting
    for EOF and the ``timeout`` is never honored. Redirecting to files keeps
    the timeout effective and avoids the deadlock.

    Args:
        command (list): Command parts to execute.
        timeout (int): Command timeout in seconds.

    Returns:
        subprocess.CompletedProcess or None if the command timed out.
    """
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
            process = subprocess.Popen(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=creation_flags,
            )
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _terminate_process_tree(process)
                return None
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout_bytes = stdout_file.read()
            stderr_bytes = stderr_file.read()
        return subprocess.CompletedProcess(
            args=command,
            returncode=process.returncode,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )
    except OSError as e:
        return subprocess.CompletedProcess(args=command, returncode=-1, stdout="", stderr=str(e))


def log_message(message, level="info"):
    """Centralized logging function for the application.

    Args:
        message (str): The message to log.
        level (str): The log level ("info", "warning", "error", "success", "debug", "adb").
    """
    # Debug messages are only emitted when debug (verbose) mode is enabled.
    if level == "debug":
        from wosutil.preferences import get_debug_mode

        if not get_debug_mode():
            return

    # Ensure console + file handlers exist even if main did not call setup_logging
    setup_logging()

    # Log to console / file
    logger = logging.getLogger("wosutil")
    if level == "info":
        logger.info(message)
    elif level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    elif level == "success":
        logger.info(f"SUCCESS: {message}")
    elif level == "debug":
        logger.debug(message)
    elif level == "adb":
        logger.info(f"ADB: {message}")
    else:
        logger.info(message)

    # Update GUI if available
    if gui_log_widget is not None:
        try:
            timestamp = time.strftime("[%H:%M:%S]")

            # Update GUI widget (thread-safe); the tag name matches the level so
            # the GUI can color it via its tag configuration.
            gui_log_widget.after(0, lambda: _update_gui_log(timestamp, level, message))
        except Exception as e:
            # If GUI update fails, just log the error to console
            logger.error(f"Failed to update GUI log: {e}")


def _update_gui_log(timestamp, level, message):
    """Update the GUI log widget (called from main thread)."""
    if gui_log_widget is None:
        return

    try:
        gui_log_widget.configure(state="normal")
        gui_log_widget.insert(tk.END, f"{timestamp} [{level.upper()}] {message}\n", level)
        gui_log_widget.see(tk.END)
        gui_log_widget.configure(state="disabled")
    except Exception:
        # If widget is destroyed or other error, just ignore
        pass


def get_template_path(template_name: str) -> Optional[str]:
    """Get template path from the TEMPLATE_PATHS dictionary.

    Args:
        template_name (str): Name of the template (e.g., "city_icon", "tech_thumb").

    Returns:
        str or None: Path to the template file or None if not found.
    """
    from wosutil.config import TEMPLATE_PATHS

    if template_name in TEMPLATE_PATHS:
        return TEMPLATE_PATHS[template_name]
    else:
        log_message(f"Template '{template_name}' not found in TEMPLATE_PATHS", level="error")
        return None


def get_coordinates(coordinate_name: str) -> Optional[tuple]:
    """Get coordinates from the COORDINATES dictionary.

    Args:
        coordinate_name (str): Name of the coordinate (e.g., "world", "alliance", "shop").

    Returns:
        tuple or None: (x, y) coordinates or None if not found.
    """
    from wosutil.config import COORDINATES

    if coordinate_name in COORDINATES:
        return COORDINATES[coordinate_name]
    else:
        log_message(f"Coordinate '{coordinate_name}' not found in COORDINATES", level="error")
        return None


def get_roi(roi_name: str) -> Optional[tuple]:
    """Get ROI (Region of Interest) from the ROI dictionary.

    Args:
        roi_name (str): Name of the ROI (e.g., "city", "tech_thumb", "sidemenu_icons").

    Returns:
        tuple or None: (x, y, width, height) ROI or None if not found.
    """
    from wosutil.config import ROI

    if roi_name in ROI:
        return ROI[roi_name]
    else:
        log_message(f"ROI '{roi_name}' not found in ROI", level="error")
        return None
