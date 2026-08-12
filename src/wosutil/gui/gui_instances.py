"""GUI module for managing multi-instance emulator setup and controls."""

import queue
import time
import tkinter as tk
from tkinter import ttk

from wosutil.emulator.instances_controller import load_instance_cache
from wosutil.gui.gui_dialogs import show_centered_dialog
from wosutil.tool.tool_instances_controller import (
    MultiInstanceToolController,
    load_instance_selection,
    save_instance_selection,
)


def get_next_task_info(pm, now):
    """Get the task that will actually run next, mirroring the worker.

    The controller executes the highest-priority task that is already due
    (next_run_time <= now) and waits for the earliest future task otherwise.
    """
    if not pm or not hasattr(pm, "running_tasks_state") or not pm.running_tasks_state:
        return None, None

    state = pm.running_tasks_state
    due_tasks = [t for t in state if t.get("next_run_time", 0) <= now]
    next_task = min(due_tasks, key=lambda t: t.get("priority", 99)) if due_tasks else min(state, key=lambda t: t.get("next_run_time", now + 99999))
    task_name = next_task.get("name", "?")
    next_time = next_task.get("next_run_time")

    # The task is ready to run: return the name without the wait time
    if next_time and next_time <= now:
        return task_name, None

    return task_name, next_time


def setup_instances_tab(
    notebook,
    profile_manager,
    multi_instance_manager,
    log_message,
    TASK_DEFINITIONS,
    instances_profile_managers,
    opened_by_app,
    instance_queue,
    active_instances,
    emulator_state=None,
):
    """Setup the instances tab in the GUI.

    Args:
        notebook: The parent notebook widget.
        profile_manager: Profile manager instance.
        multi_instance_manager: Multi-instance manager instance.
        log_message: Logging function.
        TASK_DEFINITIONS: Dictionary of task definitions.
        instances_profile_managers: Dictionary mapping instances to profile managers.
        opened_by_app: Set of instances opened by the app.
        instance_queue: Queue of instances to process.
        active_instances: Set of currently active instances.
        emulator_state (dict, optional): Shared emulator state. When provided,
            the backend, tool_running flag, controller and the refresh callback
            are tracked here so an emulator switch (Preferences) can re-create
            the backend and re-enumerate instances. If None, a private state is
            created and the passed backend is used.
    """
    if emulator_state is None:
        emulator_state = {}

    def get_backend():
        """Return the backend tracked in the shared state.

        Falls back to the one this tab was initialized with.
        """
        return emulator_state.get("backend") or multi_instance_manager

    multi_instance_tab = ttk.Frame(notebook)
    notebook.add(multi_instance_tab, text="Multi-Instance")

    instance_list_frame = ttk.LabelFrame(multi_instance_tab, text="Instances and Profiles")
    instance_list_frame.pack(pady=10, padx=10, fill="x")

    def get_profiles():
        return list(profile_manager.profiles.keys())

    instance_widgets = []
    instance_selection = load_instance_selection()
    max_emus_value = instance_selection.get("max_emulators", 2)
    max_instances_var = tk.IntVar(value=max_emus_value)

    # --- Botones y controles ---
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

    # --- Controlador ---
    # Shared queue used by the tool worker threads to request dialogs in the
    # main thread (e.g. "game not installed"); drained by periodic_update.
    dialog_queue = queue.Queue()
    controller = MultiInstanceToolController(
        multi_instance_manager=emulator_state.get("backend") or multi_instance_manager,
        profile_manager=profile_manager,
        log_message=log_message,
        TASK_DEFINITIONS=TASK_DEFINITIONS,
        instances_profile_managers=instances_profile_managers,
        opened_by_app=opened_by_app,
        instance_queue=instance_queue,
        active_instances=active_instances,
        instance_widgets=instance_widgets,
        get_profiles=get_profiles,
        save_instance_selection=save_instance_selection,
        load_instance_selection=load_instance_selection,
        refresh_instances_callback=lambda: refresh_instances(),
        dialog_queue=dialog_queue,
    )
    emulator_state["controller"] = controller

    def on_checkbox_or_profile_change():
        controller.on_checkbox_or_profile_change()

    # --- Tool state ---
    tool_running = emulator_state.get("tool_running") or {"value": False}
    emulator_state["tool_running"] = tool_running
    selected_indices_profiles = []

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
            for inst in instances:
                row = ttk.Frame(instance_list_frame)
                row.pack(fill="x", pady=2)
                checked_var = tk.BooleanVar()
                idx_str = str(inst["index"])
                checked_val = instance_selection.get(idx_str, {}).get("checked", False)
                profile_val = instance_selection.get(idx_str, {}).get("profile", profiles[0] if profiles else "")
                checked_var.set(checked_val)
                cb = ttk.Checkbutton(row, variable=checked_var, command=on_checkbox_or_profile_change)
                cb.pack(side="left")
                ttk.Label(row, text=f"Instance {inst['index']} ({inst['name']})").pack(side="left", padx=5)
                profile_var = tk.StringVar(value=profile_val)
                combo = ttk.Combobox(row, values=profiles, textvariable=profile_var, state="readonly", width=20)
                combo.pack(side="left", padx=5)
                combo.bind("<<ComboboxSelected>>", lambda e: on_checkbox_or_profile_change())
                task_status_label = ttk.Label(row, text="")
                task_status_label.pack(side="left", padx=10)
                instance_widgets.append({"index": inst["index"], "checked": checked_var, "profile_var": profile_var, "combo": combo, "task_status_label": task_status_label})
        else:
            for inst in instances:
                idx_str = str(inst["index"])
                sel = instance_selection.get(idx_str, {})
                if sel.get("checked"):
                    row = ttk.Frame(instance_list_frame)
                    row.pack(fill="x", pady=2)
                    ttk.Label(row, text=f"Instance {inst['index']} ({inst['name']})").pack(side="left", padx=5)
                    profile_val = sel.get("profile", profiles[0] if profiles else "")
                    profile_var = tk.StringVar(value=profile_val)
                    combo = ttk.Combobox(row, values=profiles, textvariable=profile_var, state="disabled", width=20)
                    combo.pack(side="left", padx=5)
                    task_status_label = ttk.Label(row, text="")
                    task_status_label.pack(side="left", padx=10)
                    instance_widgets.append(
                        {
                            "index": inst["index"],
                            "checked": tk.BooleanVar(value=True),
                            "profile_var": profile_var,
                            "combo": combo,
                            "task_status_label": task_status_label,
                        }
                    )

    def update_and_refresh():
        refresh_instances.force_update = True
        refresh_instances()

    refresh_btn.config(command=update_and_refresh)
    emulator_state["refresh_instances"] = refresh_instances
    controller.set_max_instances_var(max_instances_var)
    start_btn.config(command=lambda: on_start_tool())
    stop_btn.config(command=lambda: on_stop_tool())

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

        tool_running["value"] = True
        # Hide controls and show stop
        refresh_btn.pack_forget()
        start_btn.pack_forget()
        emu_limit_spin.pack_forget()
        for child in button_frame.winfo_children():
            if isinstance(child, ttk.Label):
                child.pack_forget()
        stop_btn.pack(side="left", padx=10, pady=10)
        # Save selected instances changes the showed instances
        instance_selection = load_instance_selection()
        selected_indices_profiles.clear()
        for idx_str, val in instance_selection.items():
            if idx_str == "max_emulators":
                continue
            if val.get("checked"):
                selected_indices_profiles.append((int(idx_str), val.get("profile")))
        refresh_instances()
        controller.start_tool()

    def on_stop_tool():
        tool_running["value"] = False
        # Show controls and hide stop
        stop_btn.pack_forget()
        refresh_btn.pack(side="left", padx=5)
        emu_limit_spin.pack(side="left", padx=5)
        # Shows emulator limit again
        for child in button_frame.winfo_children():
            if isinstance(child, ttk.Label):
                child.pack(side="left", padx=5)
        start_btn.pack(side="left", padx=10, pady=10)
        refresh_instances()
        controller.stop_tool()

    def format_time_remaining(seconds):
        """Format remaining time in HH:MM:SS format."""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def get_ordinal_number(n):
        """Convert a number to its ordinal form (1st, 2nd, 3rd, etc)."""
        if not isinstance(n, int):
            return str(n)

        suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")

        return f"{n}{suffix}"

    def get_queue_position(idx):
        """Get position of instance in queue."""
        try:
            pos = [ix for ix, (i, _) in enumerate(instance_queue) if i == idx][0] + 1
            return get_ordinal_number(pos)
        except (IndexError, ValueError):
            return None

    def update_task_status_labels():
        if not getattr(controller, "tool_running", False):
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

            pm = instances_profile_managers.get(idx)

            if idx in active_instances:
                # Instance is active
                if pm and getattr(pm, "opening_state", False):
                    # The emulator/game is still opening: show the opening phase
                    # and the first task it will run once inside.
                    task_name, _ = get_next_task_info(pm, now)
                    if task_name:
                        label.config(text=f"Opening... First task: {task_name}")
                    else:
                        label.config(text="Opening...")
                elif pm and hasattr(pm, "current_task_name") and pm.current_task_name:
                    label.config(text=f"Executing: {pm.current_task_name}")
                else:
                    task_name, next_time = get_next_task_info(pm, now)
                    if task_name and next_time and next_time > now:
                        remaining = int(next_time - now)
                        label.config(text=f"Next: {task_name} in {format_time_remaining(remaining)}")
                    elif task_name:
                        label.config(text=f"Executing: {task_name}")
                    else:
                        label.config(text="No programmed tasks")
            else:
                # Instance is in queue
                task_name, next_time = get_next_task_info(pm, now)
                queue_pos = get_queue_position(idx)

                if queue_pos:
                    if task_name:
                        if next_time:  # The task is on cooldown
                            remaining = int(next_time - now)
                            label.config(text=f"In queue ({queue_pos}) - Next: {task_name} in {format_time_remaining(remaining)}")
                        else:  # The task is ready to run
                            label.config(text=f"In queue ({queue_pos}) - Waiting to execute: {task_name}")
                    else:
                        label.config(text=f"In queue ({queue_pos})")
                else:
                    if task_name:
                        if next_time:  # The task is on cooldown
                            remaining = int(next_time - now)
                            label.config(text=f"In queue - Next: {task_name} in {format_time_remaining(remaining)}")
                        else:  # The task is ready to run
                            label.config(text=f"In queue - Waiting to execute: {task_name}")
                    else:
                        label.config(text="In queue")

    def update_profile_comboboxes():
        """Refresh the profile options of every instance combobox.

        Called after a profile is created, updated, or deleted so the lists stay
        in sync without requiring a manual refresh.
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
        # Retry launching queued instances whose next task is already within the
        # startup window (<= 120 seconds) or already due.
        if getattr(controller, "tool_running", False):
            controller.launch_next_instances()
        instance_list_frame.after(1000, periodic_update)

    periodic_update()

    def on_max_instances_change(*args):
        selection = load_instance_selection()
        selection["max_emulators"] = max_instances_var.get()
        save_instance_selection(selection)

    max_instances_var.trace_add("write", on_max_instances_change)

    update_and_refresh()

    return update_profile_comboboxes
