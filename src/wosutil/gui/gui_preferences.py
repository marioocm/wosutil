"""GUI module for user preferences.

Renders the preferences form and forwards the collected values to the
application service, which validates and persists them.
"""

import os
import tkinter as tk
from tkinter import filedialog, ttk

from wosutil.app.preferences import (
    EMULATOR_LABELS,
    EMULATOR_PATH_FIELDS,
    GATHER_RESOURCE_LABELS,
    INTEL_MODE_LABELS,
    MYSTERY_SHOP_LEVEL_LABELS,
)
from wosutil.preferences import (
    BEAR_RALLY_CALL_MARCH_MAX,
    BEAR_RALLY_CALL_MARCH_MIN,
    BEAR_TRAP_MARCH_MAX,
    BEAR_TRAP_MARCH_MIN,
    GATHER_RESOURCES,
    INTEL_MODES,
    KILL_BEAST_MARCH_MAX,
    KILL_BEAST_MARCH_MIN,
    MYSTERY_SHOP_LEVELS,
)


def _make_scrollable(parent):
    """Create a vertically scrollable frame and return its content frame."""
    canvas = tk.Canvas(parent, highlightthickness=0, bg="#34495E")
    scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    content = ttk.Frame(canvas)
    content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    window = canvas.create_window((0, 0), window=content, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def stretch(event):
        canvas.itemconfigure(window, width=event.width)

    def on_mousewheel(event):
        canvas.yview_scroll(int(-event.delta / 120), "units")

    canvas.bind("<Configure>", stretch)
    canvas.bind("<MouseWheel>", on_mousewheel)
    content.bind("<MouseWheel>", on_mousewheel)
    return content


def _choice_value(variable, labels, default):
    """Return the internal value matching a label shown in a combobox."""
    label = variable.get()
    return next((value for value, text in labels.items() if text == label), default)


def _build_emulator_section(parent, path_parent, service, current):
    """Build the emulator selector and its path editor."""
    frame = ttk.LabelFrame(parent, text="Emulator")
    frame.pack(pady=10, padx=10, fill="x")

    row = ttk.Frame(frame)
    row.pack(anchor="w", padx=10, pady=10)

    snapshot = service.snapshot()
    options = snapshot["emulator_options"]
    emulator_var = tk.StringVar(value=next((option["label"] for option in options if option["code"] == current["emulator"]), options[0]["label"]))

    ttk.Label(row, text="Default emulator:").pack(side="left", padx=5)
    emulator_combo = ttk.Combobox(row, textvariable=emulator_var, values=[option["label"] for option in options], state="readonly", width=20)
    emulator_combo.pack(side="left", padx=5)

    path_vars = {code: {key: tk.StringVar(value=current["emulator_paths"][code][key]) for key, _, _ in fields} for code, fields in EMULATOR_PATH_FIELDS.items()}

    def browse_path(variable, path_type, title):
        """Select a directory or file and update its entry field."""
        current_path = variable.get().strip()
        if path_type == "file":
            initial_dir = os.path.dirname(current_path) if current_path else ""
            selected_path = filedialog.askopenfilename(parent=path_parent, initialdir=initial_dir, title=title)
        else:
            initial_dir = current_path if os.path.isdir(current_path) else ""
            selected_path = filedialog.askdirectory(parent=path_parent, initialdir=initial_dir, title=title)
        if selected_path:
            variable.set(os.path.normpath(selected_path))

    def selected_emulator_code():
        """Return the emulator code currently shown in the selector."""
        label = emulator_var.get()
        return next((option["code"] for option in options if option["label"] == label), "mumu")

    def open_emulator_paths():
        """Open the path editor for the emulator currently selected."""
        emulator = selected_emulator_code()
        dialog = tk.Toplevel(path_parent)
        dialog.title(f"Configure {EMULATOR_LABELS[emulator]} paths")
        dialog.transient(path_parent)
        dialog.grab_set()

        ttk.Label(dialog, text="Select the custom folders or configuration file, then click OK.", wraplength=600, justify="left").pack(anchor="w", padx=15, pady=(15, 10))
        emulator_path_frame = ttk.LabelFrame(dialog, text=EMULATOR_LABELS[emulator])
        emulator_path_frame.pack(fill="x", padx=15, pady=5)
        for key, label, path_type in EMULATOR_PATH_FIELDS[emulator]:
            path_row = ttk.Frame(emulator_path_frame)
            path_row.pack(fill="x", padx=5, pady=3)
            path_var = path_vars[emulator][key]
            ttk.Label(path_row, text=f"{label}:", width=38).pack(side="left", padx=(0, 5))
            ttk.Entry(path_row, textvariable=path_var, width=48).pack(side="left", fill="x", expand=True, padx=(0, 5))
            ttk.Button(path_row, text="Browse...", command=lambda var=path_var, kind=path_type, name=label: browse_path(var, kind, f"Select {name}")).pack(side="left")

        ttk.Button(dialog, text="OK", command=dialog.destroy).pack(pady=(10, 15))

    ttk.Button(row, text="Configure paths...", command=open_emulator_paths).pack(side="left", padx=5)
    return {"var": emulator_var, "path_vars": path_vars, "selected_code": selected_emulator_code}


def _build_priority_section(parent, task_catalog):
    """Build the task priority list and its move buttons."""
    frame = ttk.LabelFrame(parent, text="Task Priorities")
    frame.pack(pady=10, padx=10, fill="x")
    ttk.Label(frame, text="Order the tasks by priority (top = highest priority). Click Save to apply.").pack(anchor="w", padx=10, pady=(5, 5))

    list_frame = ttk.Frame(frame)
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
    for position, task in enumerate(task_catalog, start=1):
        ordered_ids.append(task["id"])
        task_list.insert(tk.END, f"{position}. {task['name']}")

    def move_selected(direction):
        selected = task_list.curselection()
        if not selected:
            return
        index = selected[0]
        target = index + direction
        if target < 0 or target >= task_list.size():
            return
        current = task_list.get(index)
        task_list.delete(index)
        task_list.insert(target, current)
        task_list.selection_set(target)
        task_id = ordered_ids.pop(index)
        ordered_ids.insert(target, task_id)

    move_buttons = ttk.Frame(frame)
    move_buttons.pack(fill="x", padx=10, pady=(0, 5))
    ttk.Button(move_buttons, text="Move Up", command=lambda: move_selected(-1)).pack(side="left", padx=5)
    ttk.Button(move_buttons, text="Move Down", command=lambda: move_selected(1)).pack(side="left", padx=5)

    return {"ordered_ids": ordered_ids}


def _build_kill_beast_section(parent, current):
    """Build the kill-beasts march selector."""
    frame = ttk.LabelFrame(parent, text="Kill Beasts")
    frame.pack(pady=10, padx=10, fill="x")
    row = ttk.Frame(frame)
    row.pack(anchor="w", padx=10, pady=10)
    march_var = tk.IntVar(value=current["kill_beast_march"])
    touched = {"value": False}
    march_var.trace_add("write", lambda *_: touched.__setitem__("value", True))
    ttk.Label(row, text="Select our killing beasts march:").pack(side="left", padx=5)
    ttk.Spinbox(row, from_=KILL_BEAST_MARCH_MIN, to=KILL_BEAST_MARCH_MAX, textvariable=march_var, width=4).pack(side="left", padx=5)
    return {"var": march_var, "touched": touched}


def _build_bear_trap_section(parent, current):
    """Build the bear trap march selectors."""
    frame = ttk.LabelFrame(parent, text="Bear Trap")
    frame.pack(pady=10, padx=10, fill="x")

    row = ttk.Frame(frame)
    row.pack(anchor="w", padx=10, pady=10)
    ttk.Label(row, text="Select squad to join during bear trap (one per march slot, 1-12):").pack(side="left", padx=5)

    march_vars = [tk.IntVar(value=march) for march in current["bear_trap_marches"]]
    for position, variable in enumerate(march_vars, start=1):
        slot_row = ttk.Frame(frame)
        slot_row.pack(anchor="w", padx=10, pady=(0, 5))
        ttk.Label(slot_row, text=f"March {position}:").pack(side="left", padx=5)
        ttk.Spinbox(slot_row, from_=BEAR_TRAP_MARCH_MIN, to=BEAR_TRAP_MARCH_MAX, textvariable=variable, width=4).pack(side="left", padx=5)

    call_row = ttk.Frame(frame)
    call_row.pack(anchor="w", padx=10, pady=(0, 10))
    call_var = tk.IntVar(value=current["bear_rally_call_march"])
    call_touched = {"value": False}
    call_var.trace_add("write", lambda *_: call_touched.__setitem__("value", True))
    ttk.Label(call_row, text="Select the squad to call your rallies during bear trap:").pack(side="left", padx=5)
    ttk.Spinbox(call_row, from_=BEAR_RALLY_CALL_MARCH_MIN, to=BEAR_RALLY_CALL_MARCH_MAX, textvariable=call_var, width=4).pack(side="left", padx=5)
    return {"marches": march_vars, "call_var": call_var, "call_touched": call_touched}


def _build_gather_section(parent, current):
    """Build the ox gathering resource selector."""
    frame = ttk.LabelFrame(parent, text="Ox Gathering")
    frame.pack(pady=10, padx=10, fill="x")
    row = ttk.Frame(frame)
    row.pack(anchor="w", padx=10, pady=10)
    variable = tk.StringVar(value=GATHER_RESOURCE_LABELS[current["gather_resource"]])
    ttk.Label(row, text="Gather resource with the ox skill:").pack(side="left", padx=5)
    ttk.Combobox(row, textvariable=variable, values=[GATHER_RESOURCE_LABELS[resource] for resource in GATHER_RESOURCES], state="readonly", width=12).pack(side="left", padx=5)
    return {"var": variable}


def _build_intel_section(parent, current):
    """Build the intel mission mode selector."""
    frame = ttk.LabelFrame(parent, text="Intel")
    frame.pack(pady=10, padx=10, fill="x")
    row = ttk.Frame(frame)
    row.pack(anchor="w", padx=10, pady=10)
    variable = tk.StringVar(value=INTEL_MODE_LABELS[current["intel_mode"]])
    ttk.Label(row, text="Complete intel missions with:").pack(side="left", padx=5)
    ttk.Combobox(row, textvariable=variable, values=[INTEL_MODE_LABELS[mode] for mode in INTEL_MODES], state="readonly", width=35).pack(side="left", padx=5)
    ttk.Label(
        frame,
        text="Execute All completes every available mission instantly using extra stamina; Classic sends marches for each mission.",
        wraplength=600,
        justify="left",
    ).pack(anchor="w", padx=15, pady=(0, 10))
    return {"var": variable}


def _build_mystery_shop_section(parent, current):
    """Build the mystery shop redemption level selector."""
    frame = ttk.LabelFrame(parent, text="Mystery Shop")
    frame.pack(pady=10, padx=10, fill="x")
    row = ttk.Frame(frame)
    row.pack(anchor="w", padx=10, pady=10)
    variable = tk.StringVar(value=MYSTERY_SHOP_LEVEL_LABELS[current["mystery_shop_level"]])
    ttk.Label(row, text="Redeem in the mystery shop:").pack(side="left", padx=5)
    ttk.Combobox(row, textvariable=variable, values=[MYSTERY_SHOP_LEVEL_LABELS[level] for level in MYSTERY_SHOP_LEVELS], state="readonly", width=35).pack(side="left", padx=5)
    return {"var": variable}


def _build_schedule_section(parent, current):
    """Build the schedule-memory toggle."""
    frame = ttk.LabelFrame(parent, text="Scheduling")
    frame.pack(pady=10, padx=10, fill="x")
    row = ttk.Frame(frame)
    row.pack(anchor="w", padx=10, pady=10, fill="x")
    variable = tk.BooleanVar(value=current["remember_schedule"])
    ttk.Checkbutton(row, text="", variable=variable).pack(side="left", padx=5)
    ttk.Label(
        row,
        text="Remember the task schedule between sessions (pending tasks keep their wait after a restart)",
        wraplength=500,
        justify="left",
    ).pack(side="left", fill="x", expand=True)
    return {"var": variable}


def _build_background_section(parent, current):
    """Build the start-minimized toggle."""
    frame = ttk.LabelFrame(parent, text="Emulator Windows")
    frame.pack(pady=10, padx=10, fill="x")
    row = ttk.Frame(frame)
    row.pack(anchor="w", padx=10, pady=10, fill="x")
    variable = tk.BooleanVar(value=current["start_minimized"])
    ttk.Checkbutton(row, text="", variable=variable).pack(side="left", padx=5)
    ttk.Label(
        row,
        text="Start emulators minimized (keep them out of the foreground while the tool runs)",
        wraplength=500,
        justify="left",
    ).pack(side="left", fill="x", expand=True)
    return {"var": variable}


def _build_debug_section(parent, current):
    """Build the debug-mode toggle."""
    frame = ttk.LabelFrame(parent, text="Debug")
    frame.pack(pady=10, padx=10, fill="x")
    row = ttk.Frame(frame)
    row.pack(anchor="w", padx=10, pady=10)
    variable = tk.BooleanVar(value=current["debug_mode"])
    ttk.Checkbutton(row, text="Debug mode (verbose logs and OCR debug captures)", variable=variable).pack(side="left", padx=5)
    return {"var": variable}


def setup_preferences_tab(notebook, service):
    """Set up the preferences tab.

    Args:
        notebook: The parent notebook widget.
        service: Application service exposing ``preferences`` and
            ``save_preferences``.
    """
    preferences_tab = ttk.Frame(notebook)
    notebook.add(preferences_tab, text="Preferences")

    content = _make_scrollable(preferences_tab)
    current = service.preferences()
    catalog = service.task_catalog()

    emulator = _build_emulator_section(content, notebook.winfo_toplevel(), service, current)
    priority = _build_priority_section(content, catalog)
    kill_beast = _build_kill_beast_section(content, current)
    bear_trap = _build_bear_trap_section(content, current)
    gather = _build_gather_section(content, current)
    intel = _build_intel_section(content, current)
    mystery_shop = _build_mystery_shop_section(content, current)
    schedule = _build_schedule_section(content, current)
    background = _build_background_section(content, current)
    debug = _build_debug_section(content, current)

    def save_preferences_action():
        """Collect the form values and let the service persist them."""
        values = {
            "emulator": emulator["selected_code"](),
            "emulator_paths": {code: {key: variable.get().strip() for key, variable in fields.items()} for code, fields in emulator["path_vars"].items()},
            "task_order": priority["ordered_ids"],
            "kill_beast_march": kill_beast["var"].get(),
            "kill_beast_march_set": kill_beast["touched"]["value"] or current["kill_beast_march_set"],
            "bear_trap_marches": [variable.get() for variable in bear_trap["marches"]],
            "bear_rally_call_march": bear_trap["call_var"].get(),
            "bear_rally_call_march_set": bear_trap["call_touched"]["value"] or current["bear_rally_call_march_set"],
            "gather_resource": _choice_value(gather["var"], GATHER_RESOURCE_LABELS, current["gather_resource"]),
            "intel_mode": _choice_value(intel["var"], INTEL_MODE_LABELS, current["intel_mode"]),
            "mystery_shop_level": _choice_value(mystery_shop["var"], MYSTERY_SHOP_LEVEL_LABELS, current["mystery_shop_level"]),
            "debug_mode": bool(debug["var"].get()),
            "remember_schedule": bool(schedule["var"].get()),
            "start_minimized": bool(background["var"].get()),
        }
        service.save_preferences(values)

    ttk.Button(content, text="Save Preferences", command=save_preferences_action).pack(pady=10)
