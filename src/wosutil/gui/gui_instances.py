"""GUI module for managing multi-instance emulator setup and controls."""

import queue
import time
import tkinter as tk
from tkinter import ttk

from wosutil.emulator.instance_cache import load_instance_cache
from wosutil.gui.gui_dialogs import show_centered_dialog
from wosutil.gui.gui_tooltip import Tooltip
from wosutil.tool.tool_instances_controller import (
    TASK_GROUPING_WINDOW_SECONDS,
    MultiInstanceToolController,
    load_instance_selection,
    pick_scheduled_task,
    save_instance_selection,
)


def get_next_task_info(state, now):
    """Get the task that will actually run next, mirroring the worker.

    The controller executes the highest-priority task that is already due,
    waiting for a higher-priority task scheduled within the grouping window
    (TASK_GROUPING_WINDOW_SECONDS) first, and waits for the earliest future
    task otherwise. While a picked task is not due yet, the worker keeps
    waiting and re-picking, so a higher-priority in-window task can jump
    ahead. This helper walks that wait chain to find the task the worker will
    actually execute first.

    Args:
        state: The instance state (or None).
        now: Current epoch time.

    Returns:
        tuple: (task name, execution timestamp) or (None, None).
    """
    tasks = getattr(state, "tasks", None) if state else None
    if not tasks:
        return None, None

    sim_now = now
    while True:
        task, wait_until = pick_scheduled_task(tasks, sim_now)
        if task is None:
            return None, None
        if wait_until is None:
            task_name = task.get("name", "?")
            # The task is due now (or at the end of the wait chain)
            return task_name, (None if sim_now == now else sim_now)
        sim_now = wait_until


def format_time_remaining(seconds):
    """Format remaining time in HH:MM:SS format."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def get_all_tasks_info(state, now):
    """Get every scheduled task of an instance in the real execution order.

    Tasks whose run times fall within the same window
    (TASK_GROUPING_WINDOW_SECONDS) run grouped by priority, since the worker
    waits for the highest-priority in-window task instead of the one whose
    timer was read earlier. This helper groups the tasks the same way and
    lists each window in priority order, so the preview matches the order in
    which the tasks will actually run; tasks in different windows keep their
    chronological order.

    Args:
        state: The instance state (or None).
        now: Current epoch time.

    Returns:
        List of (task name, remaining seconds) pairs in execution order;
        tasks that are already due have a remaining time of 0.
    """
    tasks = getattr(state, "tasks", None) if state else None
    if not tasks:
        return []

    ordered = sorted(tasks, key=lambda task: (task.get("next_run_time", 0), task.get("priority", 99)))

    clusters = []
    cluster = []
    anchor = None
    for task in ordered:
        task_time = task.get("next_run_time", 0)
        if cluster and task_time > anchor + TASK_GROUPING_WINDOW_SECONDS:
            clusters.append(cluster)
            cluster = []
            anchor = None
        if not cluster:
            anchor = task_time
        cluster.append(task)
    if cluster:
        clusters.append(cluster)

    infos = []
    for cluster in clusters:
        cluster.sort(key=lambda task: (task.get("priority", 99), task.get("next_run_time", 0)))
        for task in cluster:
            remaining = max(0, int(task.get("next_run_time", 0) - now))
            infos.append((task.get("name", "?"), remaining))
    return infos


def format_task_tooltip(state, now):
    """Build the tooltip text with the countdown of every scheduled task.

    Args:
        state: The instance state (or None).
        now: Current epoch time.

    Returns:
        One numbered line per task in execution order, "Name: HH:MM:SS" form;
        tasks that are ready to run are shown as ready.
    """
    infos = get_all_tasks_info(state, now)
    if not infos:
        return "No programmed tasks"
    lines = []
    for position, (name, remaining) in enumerate(infos, start=1):
        if remaining <= 0:
            lines.append(f"{position}. {name}: ready")
        else:
            lines.append(f"{position}. {name}: {format_time_remaining(remaining)}")
    return "\n".join(lines)


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
            for inst in instances:
                row = ttk.Frame(instance_list_frame)
                row.pack(fill="x", pady=2)
                checked_var = tk.BooleanVar()
                idx_str = str(inst["index"])
                checked_val = instance_selection.get(idx_str, {}).get("checked", False)
                profile_val = instance_selection.get(idx_str, {}).get("profile", profiles[0] if profiles else "")
                checked_var.set(checked_val)
                cb = ttk.Checkbutton(row, variable=checked_var, command=controller.on_checkbox_or_profile_change)
                cb.pack(side="left")
                ttk.Label(row, text=f"Instance {inst['index']} ({inst['name']})").pack(side="left", padx=5)
                profile_var = tk.StringVar(value=profile_val)
                combo = ttk.Combobox(row, values=profiles, textvariable=profile_var, state="readonly", width=20)
                combo.pack(side="left", padx=5)
                combo.bind("<<ComboboxSelected>>", lambda e: controller.on_checkbox_or_profile_change())
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
                    info_icon = ttk.Label(row, text="\u24d8", cursor="hand2")
                    info_icon.pack(side="left", padx=(0, 2))
                    Tooltip(
                        info_icon,
                        lambda idx=inst["index"]: format_task_tooltip(controller.state_for(idx), time.time()),
                    )
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

        if not controller.start_tool():
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
        # Refresh the shown instance rows to the running state
        refresh_instances()

    def on_stop_tool():
        tool_running["value"] = False
        # Show controls and hide stop
        stop_btn.pack_forget()
        refresh_btn.pack(side="left", padx=5)
        emu_limit_spin.pack(side="left", padx=5)
        for child in button_frame.winfo_children():
            if isinstance(child, ttk.Label):
                child.pack(side="left", padx=5)
        start_btn.pack(side="left", padx=10, pady=10)
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

            if controller.is_active(idx):
                if state is not None and state.opening:
                    task_name, _ = get_next_task_info(state, now)
                    label.config(text=f"Opening... First task: {task_name}" if task_name else "Opening...")
                elif state is not None and state.current_task_name:
                    label.config(text=f"Executing: {state.current_task_name}")
                else:
                    task_name, next_time = get_next_task_info(state, now)
                    if task_name and next_time and next_time > now:
                        label.config(text=f"Next: {task_name} in {format_time_remaining(int(next_time - now))}")
                    elif task_name:
                        label.config(text=f"Executing: {task_name}")
                    else:
                        label.config(text="No programmed tasks")
            else:
                task_name, next_time = get_next_task_info(state, now)
                queue_pos = controller.pending_position(idx)
                retry_remaining = controller.retry_seconds_remaining(idx, now)

                if controller.is_aborted(idx):
                    label.config(text="Game not installed - install it and press Start")
                elif queue_pos:
                    if task_name:
                        if next_time:
                            label.config(text=f"In queue ({queue_pos}) - Next: {task_name} in {format_time_remaining(int(next_time - now))}")
                        else:
                            label.config(text=f"In queue ({queue_pos}) - Waiting to execute: {task_name}")
                    else:
                        label.config(text=f"In queue ({queue_pos})")
                elif retry_remaining > 0:
                    label.config(text=f"Retry paused - {format_time_remaining(int(retry_remaining))}")
                elif task_name:
                    if next_time:
                        label.config(text=f"Standby - Next: {task_name} in {format_time_remaining(int(next_time - now))}")
                    else:
                        label.config(text=f"Standby - Waiting to execute: {task_name}")
                else:
                    label.config(text="Standby")

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
