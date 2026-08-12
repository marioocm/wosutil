"""GUI module for user preferences.

Allows ordering tasks by priority and selecting the march used to kill beasts,
persisting the choices to the preferences file.
"""

import tkinter as tk
from tkinter import ttk

from wosutil.emulator.backends import (
    EMULATOR_BLUESTACKS,
    EMULATOR_LDPLAYER,
    EMULATOR_MUMU,
    detect_installed_emulators,
)
from wosutil.preferences import (
    KILL_BEAST_MARCH_MAX,
    KILL_BEAST_MARCH_MIN,
    MYSTERY_SHOP_LEVEL_FREE,
    MYSTERY_SHOP_LEVEL_WIDGETS_20,
    MYSTERY_SHOP_LEVEL_WIDGETS_50,
    MYSTERY_SHOP_LEVELS,
    get_debug_mode,
    get_emulator,
    get_kill_beast_march,
    get_mystery_shop_level,
    get_remember_schedule,
    load_preferences,
    save_preferences,
)

_EMULATOR_LABELS = {EMULATOR_MUMU: "MuMu Player", EMULATOR_BLUESTACKS: "BlueStacks", EMULATOR_LDPLAYER: "LDPlayer"}

_MYSTERY_SHOP_LEVEL_LABELS = {
    MYSTERY_SHOP_LEVEL_FREE: "Free items only",
    MYSTERY_SHOP_LEVEL_WIDGETS_50: "Free items + widgets 50%",
    MYSTERY_SHOP_LEVEL_WIDGETS_20: "Free items + widgets 50% + widgets 20%",
}


def _ordered_tasks(TASK_DEFINITIONS):
    """Return task definitions sorted by priority (lower = higher priority)."""
    return sorted(TASK_DEFINITIONS.values(), key=lambda t: t["priority"])


def setup_preferences_tab(notebook, TASK_DEFINITIONS, log_message, on_emulator_changed=None):
    """Setup the preferences tab in the GUI.

    Args:
        notebook: The parent notebook widget.
        TASK_DEFINITIONS: Dictionary of task definitions.
        log_message: Logging function.
        on_emulator_changed (callable, optional): Called with the new emulator
            code after a save that changed it, so the GUI can switch backends
            without restarting.
    """
    preferences_tab = ttk.Frame(notebook)
    notebook.add(preferences_tab, text="Preferences")

    # --- Emulator selection ---
    emulator_frame = ttk.LabelFrame(preferences_tab, text="Emulator")
    emulator_frame.pack(pady=10, padx=10, fill="x")

    emulator_row = ttk.Frame(emulator_frame)
    emulator_row.pack(anchor="w", padx=10, pady=10)

    installed = detect_installed_emulators()
    current_emulator = get_emulator()
    if current_emulator is None and installed:
        current_emulator = installed[0]
    if current_emulator is None:
        current_emulator = EMULATOR_MUMU

    emulator_var = tk.StringVar(value=_EMULATOR_LABELS.get(current_emulator, current_emulator))
    ttk.Label(emulator_row, text="Default emulator:").pack(side="left", padx=5)
    emulator_combo = ttk.Combobox(
        emulator_row,
        textvariable=emulator_var,
        values=[_EMULATOR_LABELS.get(code, code) for code in installed] or [EMULATOR_MUMU],
        state="readonly",
        width=20,
    )
    emulator_combo.pack(side="left", padx=5)

    # --- Task priorities ---
    priority_frame = ttk.LabelFrame(preferences_tab, text="Task Priorities")
    priority_frame.pack(pady=10, padx=10, fill="x")

    ttk.Label(
        priority_frame,
        text="Order the tasks by priority (top = highest priority). Click Save to apply.",
    ).pack(anchor="w", padx=10, pady=(5, 5))

    list_frame = ttk.Frame(priority_frame)
    list_frame.pack(fill="x", padx=10, pady=5)

    task_list = tk.Listbox(
        list_frame,
        height=10,
        selectmode=tk.SINGLE,
        bg="#1F2E3A",
        fg="#ECF0F1",
        selectbackground="#1ABC9C",
        selectforeground="white",
        highlightthickness=0,
        font=("Arial", 10),
    )
    task_list.pack(side="left", fill="both", expand=True)

    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=task_list.yview)
    scrollbar.pack(side="left", fill="y")
    task_list.configure(yscrollcommand=scrollbar.set)

    ordered_ids = []

    def refresh_list():
        ordered_ids.clear()
        task_list.delete(0, tk.END)
        for i, task in enumerate(_ordered_tasks(TASK_DEFINITIONS), start=1):
            ordered_ids.append(task["id"])
            task_list.insert(tk.END, f"{i}. {task['name']}")

    def move_selected(direction):
        index = task_list.curselection()
        if not index:
            return
        index = index[0]
        target = index + direction
        if target < 0 or target >= task_list.size():
            return
        current = task_list.get(index)
        task_list.delete(index)
        task_list.insert(target, current)
        task_list.selection_set(target)
        task_id = ordered_ids.pop(index)
        ordered_ids.insert(target, task_id)

    move_buttons = ttk.Frame(priority_frame)
    move_buttons.pack(fill="x", padx=10, pady=(0, 5))
    ttk.Button(move_buttons, text="Move Up", command=lambda: move_selected(-1)).pack(side="left", padx=5)
    ttk.Button(move_buttons, text="Move Down", command=lambda: move_selected(1)).pack(side="left", padx=5)

    # --- Kill beasts march selection ---
    kill_beast_frame = ttk.LabelFrame(preferences_tab, text="Kill Beasts")
    kill_beast_frame.pack(pady=10, padx=10, fill="x")

    march_row = ttk.Frame(kill_beast_frame)
    march_row.pack(anchor="w", padx=10, pady=10)

    march_var = tk.IntVar(value=get_kill_beast_march())
    march_touched = {"value": False}
    march_var.trace_add("write", lambda *_: march_touched.__setitem__("value", True))
    ttk.Label(march_row, text="Select our killing beasts march:").pack(side="left", padx=5)
    march_spinbox = ttk.Spinbox(
        march_row,
        from_=KILL_BEAST_MARCH_MIN,
        to=KILL_BEAST_MARCH_MAX,
        textvariable=march_var,
        width=4,
    )
    march_spinbox.pack(side="left", padx=5)

    # --- Mystery shop redemption level ---
    mystery_shop_frame = ttk.LabelFrame(preferences_tab, text="Mystery Shop")
    mystery_shop_frame.pack(pady=10, padx=10, fill="x")

    mystery_shop_row = ttk.Frame(mystery_shop_frame)
    mystery_shop_row.pack(anchor="w", padx=10, pady=10)

    mystery_shop_var = tk.StringVar(value=_MYSTERY_SHOP_LEVEL_LABELS[get_mystery_shop_level()])
    ttk.Label(mystery_shop_row, text="Redeem in the mystery shop:").pack(side="left", padx=5)
    ttk.Combobox(
        mystery_shop_row,
        textvariable=mystery_shop_var,
        values=[_MYSTERY_SHOP_LEVEL_LABELS[level] for level in MYSTERY_SHOP_LEVELS],
        state="readonly",
        width=35,
    ).pack(side="left", padx=5)

    # --- Schedule memory ---
    schedule_frame = ttk.LabelFrame(preferences_tab, text="Scheduling")
    schedule_frame.pack(pady=10, padx=10, fill="x")

    schedule_row = ttk.Frame(schedule_frame)
    schedule_row.pack(anchor="w", padx=10, pady=10, fill="x")

    remember_var = tk.BooleanVar(value=get_remember_schedule())
    ttk.Checkbutton(schedule_row, text="", variable=remember_var).pack(side="left", padx=5)
    ttk.Label(
        schedule_row,
        text="Remember the task schedule between sessions (pending tasks keep their wait after a restart)",
        wraplength=500,
        justify="left",
    ).pack(side="left", fill="x", expand=True)

    # --- Debug mode ---
    debug_frame = ttk.LabelFrame(preferences_tab, text="Debug")
    debug_frame.pack(pady=10, padx=10, fill="x")

    debug_row = ttk.Frame(debug_frame)
    debug_row.pack(anchor="w", padx=10, pady=10)

    debug_var = tk.BooleanVar(value=get_debug_mode())
    ttk.Checkbutton(
        debug_row,
        text="Debug mode (verbose logs and OCR debug captures)",
        variable=debug_var,
    ).pack(side="left", padx=5)

    # --- Save ---
    def save_preferences_action():
        prefs = load_preferences()
        previous_emulator = get_emulator(prefs)
        label = emulator_var.get()
        new_emulator = next((code for code, lab in _EMULATOR_LABELS.items() if lab == label), EMULATOR_MUMU)
        prefs["emulator"] = new_emulator
        prefs["task_priorities"] = {}
        for i, task_id in enumerate(ordered_ids, start=1):
            TASK_DEFINITIONS[task_id]["priority"] = i
            prefs["task_priorities"][task_id] = i
        march = march_var.get()
        if march < KILL_BEAST_MARCH_MIN or march > KILL_BEAST_MARCH_MAX:
            log_message(f"Killing beasts march must be between {KILL_BEAST_MARCH_MIN} and {KILL_BEAST_MARCH_MAX}, keeping {get_kill_beast_march(prefs)}.", "warning")
            march_var.set(get_kill_beast_march(prefs))
        elif march_touched["value"] or "kill_beast_march" in prefs:
            prefs["kill_beast_march"] = march
        level = next(
            (level for level, label in _MYSTERY_SHOP_LEVEL_LABELS.items() if label == mystery_shop_var.get()),
            get_mystery_shop_level(prefs),
        )
        prefs["mystery_shop_level"] = level
        prefs["debug_mode"] = bool(debug_var.get())
        prefs["remember_schedule"] = bool(remember_var.get())
        if save_preferences(prefs):
            log_message("Preferences saved successfully.", "success")
            if on_emulator_changed and new_emulator != previous_emulator:
                on_emulator_changed(new_emulator)
        else:
            log_message("Error saving preferences.", "error")

    ttk.Button(preferences_tab, text="Save Preferences", command=save_preferences_action).pack(pady=10)

    refresh_list()
