"""Main GUI module for WoS Util.

Tkinter shell over the headless application service: it creates the window,
renders snapshots and forwards user actions. Application logic lives in
``wosutil.app``, so this module has no business rules.
"""

import time
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from wosutil.app.bootstrap import build_app_context
from wosutil.app.preferences import EMULATOR_LABELS
from wosutil.emulator.backends import EMULATOR_MUMU
from wosutil.gui.gui_dialogs import center_window_on_screen, show_centered_dialog
from wosutil.utils import log_message

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
    options = [EMULATOR_LABELS.get(code, code) for code in installed]
    var = tk.StringVar(value=options[0])
    combo = ttk.Combobox(top, textvariable=var, values=options, state="readonly", width=28)
    combo.pack(padx=20, pady=10)

    def on_ok():
        label = var.get()
        chosen["value"] = next((code for code in installed if EMULATOR_LABELS.get(code, code) == label), installed[0])
        top.destroy()

    ttk.Button(top, text="OK", command=on_ok).pack(pady=(0, 15))
    center_window_on_screen(top)
    top.wait_window()

    selected = chosen["value"] or installed[0]
    log_message(f"Emulator selected: {EMULATOR_LABELS.get(selected, selected)}", "info")
    return selected


def create_log_tab(notebook, service):
    """Create the log tab and stream the service log history into it.

    Args:
        notebook: The parent notebook widget.
        service: Application service exposing ``logs_after``.
    """
    log_tab = ttk.Frame(notebook)
    notebook.add(log_tab, text="Log")

    log_text_widget = ScrolledText(log_tab, width=70, height=15, font=("Consolas", 9), wrap=tk.WORD, bg="#1F2E3A", fg="#ECF0F1", insertbackground="white")
    log_text_widget.pack(expand=True, fill="both", padx=10, pady=10)
    log_text_widget.configure(state="disabled")

    for level, color in LOG_COLORS.items():
        log_text_widget.tag_config(level, foreground=color)

    state = {"seq": 0}

    def pump():
        entries = service.logs_after(state["seq"])
        if entries:
            log_text_widget.configure(state="normal")
            for entry in entries:
                state["seq"] = entry["seq"]
                stamp = time.strftime("[%H:%M:%S]", time.localtime(entry["time"]))
                log_text_widget.insert(tk.END, f"{stamp} [{entry['level'].upper()}] {entry['message']}\n", entry["level"])
            log_text_widget.see(tk.END)
            log_text_widget.configure(state="disabled")
        log_text_widget.after(300, pump)

    pump()


def run_gui():
    """Main GUI initialization and execution."""
    # Configure console + file logging once (entry point of the `wosutil` command).
    from wosutil.utils import setup_logging

    setup_logging()

    log_message("Starting GUI initialization...", "info")
    context = build_app_context(log_message)
    service = context.service

    log_message("Creating main window...", "info")
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
    if context.requirements_reminder_pending:
        show_centered_dialog(
            window,
            "Setup requirements",
            "Make sure every emulator instance is ready before starting:\n\n"
            "- ADB enabled in the emulator settings\n"
            "- Mobile resolution 720p (720x1280 portrait)\n"
            "- Whiteout Survival installed and set to English\n\n",
        )
        service.ack_requirements_reminder()

    # --- Emulator selection (first run) ---
    if context.needs_emulator_choice:
        chosen = _ask_emulator(window, context.installed_emulators, log_message)
        service.set_emulator(chosen)

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

    update_profile_comboboxes = setup_instances_tab(notebook, service)

    log_message("Setting up profiles tab...", "info")
    from wosutil.gui.gui_profile import setup_profiles_tab

    setup_profiles_tab(notebook, service, log_message, on_profiles_changed=update_profile_comboboxes)

    log_message("Setting up preferences tab...", "info")
    from wosutil.gui.gui_preferences import setup_preferences_tab

    setup_preferences_tab(notebook, service)

    # Create log tab
    log_message("Creating log tab...", "info")
    create_log_tab(notebook, service)

    def on_close():
        """Stop the scheduler and close the window."""
        service.close()
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", on_close)

    log_message("Starting GUI mainloop...", "info")
    service.start()
    window.mainloop()
