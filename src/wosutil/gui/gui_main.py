"""Main GUI module for WoS Util.

Provides the Tkinter-based graphical interface for the automation tool.
"""

import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from wosutil.context import set_multi_instance_manager
from wosutil.emulator.backends import (
    EMULATOR_BLUESTACKS,
    EMULATOR_LDPLAYER,
    EMULATOR_MUMU,
    create_backend,
    detect_installed_emulators,
)
from wosutil.emulator.emulator_manager import set_active_backend
from wosutil.gui.gui_dialogs import center_window_on_screen, show_centered_dialog
from wosutil.preferences import (
    get_emulator,
    get_emulator_paths,
    get_requirements_reminder_seen,
    load_preferences,
    mark_requirements_reminder_seen,
    save_emulator,
)
from wosutil.tool.profiles.profile_manager import ProfileManager
from wosutil.tool.tasks.task_definitions import get_task_definitions
from wosutil.utils import log_message, set_gui_log_widget

# Global variables
log_text_widget = None
profile_manager = None
multi_instance_manager = None
instances_profile_managers: dict = {}
instance_queue: list = []
active_instances: set = set()
max_instances_var = None

# Logging configuration
LOG_COLORS = {"info": "white", "error": "red", "warning": "orange", "success": "light green", "adb": "light blue", "debug": "gray"}


def setup_gui_style():
    """Configure the GUI style and theme."""
    style = ttk.Style()
    style.theme_use("clam")

    # Configure notebook style
    style.configure("TNotebook", background="#2C3E50", borderwidth=0)
    style.configure("TNotebook.Tab", background="#34495E", foreground="#ECF0F1", font=("Arial", 10, "bold"), padding=[10, 5])
    style.map("TNotebook.Tab", background=[("selected", "#1ABC9C")], foreground=[("selected", "white")])

    # Configure frame and label styles
    style.configure("TFrame", background="#34495E")
    style.configure("TLabel", background="#34495E", foreground="#ECF0F1", font=("Arial", 10))
    style.configure("TLabelframe", background="#34495E", bordercolor="#ECF0F1")
    style.configure("TLabelframe.Label", background="#34495E", foreground="#1ABC9C", font=("Arial", 11, "bold"))

    # Configure button styles
    style.configure("TButton", background="#1ABC9C", foreground="white", font=("Arial", 10, "bold"), borderwidth=0)
    style.map("TButton", background=[("active", "#16A085")])
    style.configure("Stop.TButton", background="#E74C3C")
    style.map("Stop.TButton", background=[("active", "#C0392B")])


_EMULATOR_LABELS = {EMULATOR_MUMU: "MuMu Player", EMULATOR_BLUESTACKS: "BlueStacks", EMULATOR_LDPLAYER: "LDPlayer"}


def _ask_emulator(window, installed, log_message):
    """Pick the default emulator once (first run with several candidates).

    Args:
        window: The main Tk window (parent for the dialog).
        installed (list): Detected emulators, e.g. ["mumu", "bluestacks"].
        log_message: Logging function.

    Returns:
        str: The chosen emulator code.
    """
    if not installed:
        log_message("No emulator detected; falling back to MuMu Player.", "warning")
        return EMULATOR_MUMU
    if len(installed) == 1:
        return installed[0]

    chosen = {"value": None}
    top = tk.Toplevel(window)
    top.title("Select default emulator")
    top.transient(window)
    top.grab_set()
    top.configure(bg="#2C3E50")

    ttk.Label(top, text="Several emulators are installed. Choose the default one:").pack(padx=20, pady=(15, 5))
    options = [_EMULATOR_LABELS.get(code, code) for code in installed]
    var = tk.StringVar(value=options[0])
    combo = ttk.Combobox(top, textvariable=var, values=options, state="readonly", width=28)
    combo.pack(padx=20, pady=10)

    def on_ok():
        label = var.get()
        chosen["value"] = next((code for code in installed if _EMULATOR_LABELS.get(code, code) == label), installed[0])
        top.destroy()

    ttk.Button(top, text="OK", command=on_ok).pack(pady=(0, 15))
    center_window_on_screen(top)
    top.wait_window()

    selected = chosen["value"] or installed[0]
    log_message(f"Emulator selected: {_EMULATOR_LABELS.get(selected, selected)}", "info")
    return selected


def create_log_tab(notebook):
    """Create and configure the log tab."""
    log_tab = ttk.Frame(notebook)
    notebook.add(log_tab, text="Log")

    global log_text_widget
    log_text_widget = ScrolledText(log_tab, width=70, height=15, font=("Consolas", 9), wrap=tk.WORD, bg="#1F2E3A", fg="#ECF0F1", insertbackground="white")
    log_text_widget.pack(expand=True, fill="both", padx=10, pady=10)
    log_text_widget.configure(state="disabled")

    # Configure color tags
    for level, color in LOG_COLORS.items():
        log_text_widget.tag_config(level, foreground=color)

    # Set the widget in utils for logging
    set_gui_log_widget(log_text_widget)


def run_gui():
    """Main GUI initialization and execution."""
    global profile_manager, multi_instance_manager, instances_profile_managers, max_instances_var

    # Configure console + file logging once (entry point of the `wosutil` command).
    from wosutil.utils import setup_logging

    setup_logging()

    # Remove temporary screenshots left behind by crashed sessions.
    from wosutil.emulator.emulator_manager import cleanup_stale_temp_screenshots

    cleanup_stale_temp_screenshots()

    log_message("Starting GUI initialization...", "info")

    # Initialize managers
    log_message("Initializing profile manager...", "info")
    profile_manager = ProfileManager(log_message)

    log_message("Loading task definitions...", "info")
    TASK_DEFINITIONS = get_task_definitions()

    log_message("Creating main window...", "info")
    # Create main window
    window = tk.Tk()
    window.title("WoS Util")

    # Set window size (height > width for vertical layout)
    window_width = 800
    window_height = 1000

    # Get screen dimensions
    screen_width = window.winfo_screenwidth()

    # Calculate position to place window in the top-right corner
    x_position = screen_width - window_width - 10  # 10 pixels from right edge
    y_position = 10  # 10 pixels from top

    # Set window geometry (width x height + x + y)
    window.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
    window.configure(bg="#2C3E50")

    # Ensure window is visible and on top
    window.lift()
    window.attributes("-topmost", True)
    window.after_idle(window.attributes, "-topmost", False)
    window.focus_force()

    log_message("Main window created and configured", "info")

    # --- First-run setup requirements reminder ---
    if not get_requirements_reminder_seen():
        show_centered_dialog(
            window,
            "Setup requirements",
            "Make sure every emulator instance is ready before starting:\n\n"
            "- ADB enabled in the emulator settings\n"
            "- Mobile resolution 720p (720x1280 portrait)\n"
            "- Whiteout Survival installed and set to English\n\n",
        )
        mark_requirements_reminder_seen()

    # --- Emulator selection (first run) ---
    preferences = load_preferences()
    emulator_paths = get_emulator_paths(preferences)
    emulator = get_emulator(preferences)
    if emulator is None:
        emulator = _ask_emulator(window, detect_installed_emulators(emulator_paths), log_message)
        save_emulator(emulator)

    log_message(f"Initializing emulator backend ({emulator})...", "info")
    multi_instance_manager = create_backend(emulator, log_message, emulator_paths=emulator_paths)
    set_active_backend(multi_instance_manager)
    set_multi_instance_manager(multi_instance_manager)

    # Shared state between the tabs: the active backend, the refresh callback
    # and the tool running flag allow an emulator change made in Preferences to
    # re-create the backend and re-enumerate instances without a restart.
    emulator_state = {"backend": multi_instance_manager}

    def switch_emulator(new_emulator, new_emulator_paths=None):
        """Apply an emulator change made in the Preferences tab.

        Refuses while the tool is running (active instances/queue run against
        the old backend); otherwise it re-creates the backend, replaces the
        active manager and forces an instance refresh.

        Args:
            new_emulator (str): Emulator code, "mumu", "bluestacks" or
                "ldplayer".
            new_emulator_paths (dict, optional): Configured executable and
                instance paths. Defaults to the persisted preferences.
        """
        global multi_instance_manager

        if emulator_state.get("tool_running", {}).get("value"):
            log_message("Emulator change ignored while the tool is running. It will apply on the next run.", "warning")
            return
        label = _EMULATOR_LABELS.get(new_emulator, new_emulator)
        log_message(f"Switching emulator to {label}...", "info")
        save_emulator(new_emulator)
        emulator_paths = new_emulator_paths or get_emulator_paths()
        multi_instance_manager = create_backend(new_emulator, log_message, emulator_paths=emulator_paths)
        set_active_backend(multi_instance_manager)
        set_multi_instance_manager(multi_instance_manager)
        emulator_state["backend"] = multi_instance_manager
        controller = emulator_state.get("controller")
        if controller is not None:
            controller.multi_instance_manager = multi_instance_manager
        refresh_instances = emulator_state.get("refresh_instances")
        if refresh_instances is not None:
            refresh_instances.force_update = True
            refresh_instances()

    # Setup GUI style
    log_message("Setting up GUI style...", "info")
    setup_gui_style()

    # Create notebook
    log_message("Creating notebook...", "info")
    notebook = ttk.Notebook(window)
    notebook.pack(pady=10, padx=20, expand=True, fill="both")

    # Setup tabs
    log_message("Setting up instances tab...", "info")
    from wosutil.gui.gui_instances import setup_instances_tab

    update_profile_comboboxes = setup_instances_tab(
        notebook,
        profile_manager,
        multi_instance_manager,
        log_message,
        TASK_DEFINITIONS,
        instances_profile_managers,
        instance_queue,
        active_instances,
        emulator_state=emulator_state,
    )

    log_message("Setting up profiles tab...", "info")
    from wosutil.gui.gui_profile import setup_profiles_tab

    setup_profiles_tab(
        notebook,
        profile_manager,
        log_message,
        list(TASK_DEFINITIONS.values()),
        on_profiles_changed=update_profile_comboboxes,
    )

    log_message("Setting up preferences tab...", "info")
    from wosutil.gui.gui_preferences import setup_preferences_tab

    setup_preferences_tab(notebook, TASK_DEFINITIONS, log_message, on_emulator_changed=switch_emulator)

    # Create log tab
    log_message("Creating log tab...", "info")
    create_log_tab(notebook)

    log_message("Starting GUI mainloop...", "info")
    # Start GUI
    window.mainloop()
