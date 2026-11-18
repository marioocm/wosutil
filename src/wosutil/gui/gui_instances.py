"""GUI module for managing multi-instance emulator setup and controls."""

import queue
import time
import tkinter as tk
from tkinter import ttk

from wosutil.emulator.instance_cache import load_instance_cache
from wosutil.gui.gui_dialogs import show_centered_dialog
from wosutil.gui.gui_instance_status import format_instance_status, format_task_tooltip
from wosutil.gui.gui_tooltip import Tooltip
from wosutil.tool.tool_instances_controller import (
    MultiInstanceToolController,
    load_instance_selection,
    save_instance_selection,
)


def _build_editable_rows(parent, instances, profiles, selection, on_change):
    """Create the editable instance rows (checkbox, profile, status)."""
    widgets = []
    for inst in instances:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        index = inst["index"]
        idx_str = str(index)
        checked_var = tk.BooleanVar()
        checked_var.set(selection.get(idx_str, {}).get("checked", False))
        cb = ttk.Checkbutton(row, variable=checked_var, command=on_change)
        cb.pack(side="left")
        ttk.Label(row, text=f"Instance {index} ({inst['name']})").pack(side="left", padx=5)
        profile_var = tk.StringVar(value=selection.get(idx_str, {}).get("profile", profiles[0] if profiles else ""))
        combo = ttk.Combobox(row, values=profiles, textvariable=profile_var, state="readonly", width=20)
        combo.pack(side="left", padx=5)
        combo.bind("<<ComboboxSelected>>", lambda e: on_change())
        task_status_label = ttk.Label(row, text="")
        task_status_label.pack(side="left", padx=10)
        widgets.append({"index": index, "checked": checked_var, "profile_var": profile_var, "combo": combo, "task_status_label": task_status_label})
    return widgets


def _build_running_rows(parent, instances, profiles, selection, tooltip_text):
    """Create the read-only rows of the instances selected for the run."""
    widgets = []
    for inst in instances:
        index = inst["index"]
        selected = selection.get(str(index), {})
        if not selected.get("checked"):
            continue
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=f"Instance {index} ({inst['name']})").pack(side="left", padx=5)
        profile_var = tk.StringVar(value=selected.get("profile", profiles[0] if profiles else ""))
        combo = ttk.Combobox(row, values=profiles, textvariable=profile_var, state="disabled", width=20)
        combo.pack(side="left", padx=5)
        task_status_label = ttk.Label(row, text="")
        task_status_label.pack(side="left", padx=10)
        info_icon = ttk.Label(row, text="\u24d8", cursor="hand2")
        info_icon.pack(side="left", padx=(0, 2))
        Tooltip(info_icon, lambda idx=index: tooltip_text(idx))
        widgets.append(
            {
                "index": index,
                "checked": tk.BooleanVar(value=True),
                "profile_var": profile_var,
                "combo": combo,
                "task_status_label": task_status_label,
            }
        )
    return widgets


def setup_instances_tab(notebook, profile_manager, log_message, task_definitions, emulator_state):
    """Set up the instances tab in the GUI.

    Args:
        notebook: The parent notebook widget.
        profile_manager: Profile manager instance.
        log_message: Logging function.
        task_definitions (dict): Task definitions.
        emulator_state (dict): Shared state with the active backend, the tool
            running flag, the controller and the refresh callback.
    """
    multi_instance_tab = ttk.Frame(notebook)
    notebook.add(multi_instance_tab, text="Multi-Instance")

    instance_list_frame = ttk.LabelFrame(multi_instance_tab, text="Instances and Profiles")
    instance_list_frame.pack(pady=10, padx=10, fill="x")

    def get_backend():
        return emulator_state["backend"]

    def get_profiles():
        return list(profile_manager.profiles.keys())

    instance_widgets = []
    instance_selection = load_instance_selection()
    max_emus_value = instance_selection.get("max_emulators", 2)
    max_instances_var = tk.IntVar(value=max_emus_value)

    # --- Buttons and controls ---
    button_frame = ttk.Frame(multi_instance_tab)
    button_frame.pack(pady=10)

    refresh_btn = ttk.Button(button_frame, text="Refresh Instances")
    refresh_btn.pack(side="left", padx=5)

    ttk.Label(button_frame, text="Emulators opened limit:").pack(side="left", padx=5)
    emu_limit_spin = ttk.Spinbox(button_frame, from_=1, to=20, textvariable=max_instances_var, width=3)
    emu_limit_spin.pack(side="left", padx=5)

    start_btn = ttk.Button(button_frame, text="Start Tool")
    start_btn.pack(side="left", padx=10, pady=10)

    stop_btn = ttk.Button(button_frame, text="Stop Tool")
    stop_btn.pack(side="left", padx=10, pady=10)
    stop_btn.pack_forget()  # Hidden at first

    # --- Controller ---
    # Worker threads ask for dialogs through this queue; periodic_update shows
    # them in the main thread.
    dialog_queue = queue.Queue()
    controller = MultiInstanceToolController(
        log_message=log_message,
        task_definitions=task_definitions,
        backend=get_backend(),
        profiles=profile_manager.profiles,
        instance_widgets=instance_widgets,
        dialog_queue=dialog_queue,
    )
    controller.set_max_instances(max_emus_value)
    emulator_state["controller"] = controller

    # --- Tool state ---
    tool_running = emulator_state.get("tool_running") or {"value": False}
    emulator_state["tool_running"] = tool_running

    def refresh_instances():
        for widget in instance_list_frame.winfo_children():
            widget.destroy()
        instance_widgets.clear()

        force_update = getattr(refresh_instances, "force_update", False)
        refresh_instances.force_update = False

        if force_update:
            instances = get_backend().get_instances()
            if not instances:
                instances = load_instance_cache()
        else:
            instances = load_instance_cache()

        profiles = get_profiles()
        instance_selection = load_instance_selection()

        if not tool_running["value"]:
            instance_widgets.extend(_build_editable_rows(instance_list_frame, instances, profiles, instance_selection, controller.on_checkbox_or_profile_change))
        else:
            instance_widgets.extend(_build_running_rows(instance_list_frame, instances, profiles, instance_selection, lambda idx: format_task_tooltip(controller.state_for(idx), time.time())))

    def update_and_refresh():
        refresh_instances.force_update = True
        refresh_instances()

    refresh_btn.config(command=update_and_refresh)
    emulator_state["refresh_instances"] = refresh_instances
    start_btn.config(command=lambda: on_start_tool())
    stop_btn.config(command=lambda: on_stop_tool())

    def set_running_controls(running):
        """Show the controls of the running or the editable state."""
        if running:
            refresh_btn.pack_forget()
            start_btn.pack_forget()
            emu_limit_spin.pack_forget()
            for child in button_frame.winfo_children():
                if isinstance(child, ttk.Label):
                    child.pack_forget()
            stop_btn.pack(side="left", padx=10, pady=10)
            return
        stop_btn.pack_forget()
        refresh_btn.pack(side="left", padx=5)
        emu_limit_spin.pack(side="left", padx=5)
        for child in button_frame.winfo_children():
            if isinstance(child, ttk.Label):
                child.pack(side="left", padx=5)
        start_btn.pack(side="left", padx=10, pady=10)

    def on_start_tool():
        # Abort before switching to the running UI if the backend cannot
        # control the emulator (e.g. BlueStacks has ADB access disabled).
        adb_warnings = get_backend().check_adb_access()
        if adb_warnings:
            for warning in adb_warnings:
                log_message(warning, level="error")
            log_message("Tool not started: ADB access to the emulator is blocked. Enable ADB and press Start again.", level="error")
            show_centered_dialog(notebook, "Tool not started", "\n\n".join(adb_warnings))
            return

        if not controller.start_tool():
            return

        tool_running["value"] = True
        set_running_controls(True)
        # Refresh the shown instance rows to the running state
        refresh_instances()

    def on_stop_tool():
        tool_running["value"] = False
        set_running_controls(False)
        refresh_instances()
        controller.stop_tool()

    def update_task_status_labels():
        if not controller.tool_running:
            for inst_widget in instance_widgets:
                inst_widget["task_status_label"].config(text="")
            return

        now = time.time()
        for inst_widget in instance_widgets:
            idx = inst_widget["index"]
            checked = inst_widget["checked"].get()
            label = inst_widget["task_status_label"]

            if not checked:
                label.config(text="")
                continue

            state = controller.state_for(idx)
            label.config(
                text=format_instance_status(
                    state,
                    now=now,
                    active=controller.is_active(idx),
                    aborted=controller.is_aborted(idx),
                    queue_position=controller.pending_position(idx),
                    retry_seconds=controller.retry_seconds_remaining(idx, now),
                )
            )

    def update_profile_comboboxes():
        """Refresh the profile options of every instance combobox.

        Called after a profile is created, updated, or deleted so the lists
        stay in sync without requiring a manual refresh.
        """
        profiles = get_profiles()
        for inst_widget in instance_widgets:
            combo = inst_widget.get("combo")
            if combo is None:
                continue
            current = inst_widget["profile_var"].get()
            combo["values"] = profiles
            if current not in profiles and profiles:
                inst_widget["profile_var"].set(profiles[0])

    def periodic_update():
        update_task_status_labels()
        # Show any dialog requested by the worker threads (main thread only).
        while True:
            try:
                title, text = dialog_queue.get_nowait()
            except queue.Empty:
                break
            show_centered_dialog(notebook, title, text)
        if controller.tool_running:
            controller.launch_due_instances()
        instance_list_frame.after(1000, periodic_update)

    periodic_update()

    def on_max_instances_change(*args):
        controller.set_max_instances(max_instances_var.get())
        selection = load_instance_selection()
        selection["max_emulators"] = max_instances_var.get()
        save_instance_selection(selection)

    max_instances_var.trace_add("write", on_max_instances_change)

    update_and_refresh()

    return update_profile_comboboxes
