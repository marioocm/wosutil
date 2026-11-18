"""GUI module for the multi-instance tab."""

import tkinter as tk
from tkinter import ttk

from wosutil.gui.gui_dialogs import show_centered_dialog
from wosutil.gui.gui_tooltip import Tooltip


def setup_instances_tab(notebook, service):
    """Set up the instances tab.

    Args:
        notebook: The parent notebook widget.
        service: Application service exposing instances, selection and tool
            commands.

    Returns:
        callable: Refresh the profile options of the instance rows.
    """
    tab = ttk.Frame(notebook)
    notebook.add(tab, text="Multi-Instance")

    list_frame = ttk.LabelFrame(tab, text="Instances and Profiles")
    list_frame.pack(pady=10, padx=10, fill="x")

    button_frame = ttk.Frame(tab)
    button_frame.pack(pady=10)

    refresh_btn = ttk.Button(button_frame, text="Refresh Instances")
    refresh_btn.pack(side="left", padx=5)

    limit_label = ttk.Label(button_frame, text="Emulators opened limit:")
    limit_label.pack(side="left", padx=5)

    snapshot = service.snapshot()
    max_instances_var = tk.IntVar(value=snapshot["max_instances"])
    emu_limit_spin = ttk.Spinbox(button_frame, from_=1, to=20, textvariable=max_instances_var, width=3)
    emu_limit_spin.pack(side="left", padx=5)

    start_btn = ttk.Button(button_frame, text="Start Tool")
    start_btn.pack(side="left", padx=10, pady=10)

    stop_btn = ttk.Button(button_frame, text="Stop Tool")
    stop_btn.pack(side="left", padx=10, pady=10)
    stop_btn.pack_forget()  # Hidden at first

    rows = {}
    row_key = None

    def on_selection_change(index):
        """Persist the checkbox and profile of one row."""
        row = rows.get(index)
        if row is None or row["checked"] is None:
            return
        service.update_instance_selection(index, row["checked"].get(), row["profile_var"].get())

    def rebuild_rows(current):
        """Rebuild the instance rows when the list or the run state changes."""
        nonlocal row_key
        for child in list_frame.winfo_children():
            child.destroy()
        rows.clear()
        profile_names = list(current["profiles"])

        for instance in current["instances"]:
            index = instance["index"]
            row = ttk.Frame(list_frame)
            row.pack(fill="x", pady=2)

            if current["tool_running"]:
                ttk.Label(row, text=f"Instance {index} ({instance['name']})").pack(side="left", padx=5)
                combo = ttk.Combobox(row, values=profile_names, state="disabled", width=20)
                combo.set(instance["profile"])
                combo.pack(side="left", padx=5)
                status_label = ttk.Label(row, text=instance["status"])
                status_label.pack(side="left", padx=10)
                info_icon = ttk.Label(row, text="\u24d8", cursor="hand2")
                info_icon.pack(side="left", padx=(0, 2))
                Tooltip(info_icon, lambda idx=index: rows.get(idx, {}).get("tooltip", ""))
                rows[index] = {"index": index, "checked": None, "profile_var": None, "combo": combo, "status_label": status_label, "tooltip": instance["tooltip"]}
                continue

            checked = tk.BooleanVar(value=instance["checked"])
            profile_var = tk.StringVar(value=instance["profile"])
            ttk.Checkbutton(row, variable=checked, command=lambda idx=index: on_selection_change(idx)).pack(side="left")
            ttk.Label(row, text=f"Instance {index} ({instance['name']})").pack(side="left", padx=5)
            combo = ttk.Combobox(row, values=profile_names, textvariable=profile_var, state="readonly", width=20)
            combo.pack(side="left", padx=5)
            combo.bind("<<ComboboxSelected>>", lambda _event, idx=index: on_selection_change(idx))
            status_label = ttk.Label(row, text=instance["status"])
            status_label.pack(side="left", padx=10)
            rows[index] = {"index": index, "checked": checked, "profile_var": profile_var, "combo": combo, "status_label": status_label, "tooltip": instance["tooltip"]}

        row_key = (tuple((instance["index"], instance["name"]) for instance in current["instances"]), current["tool_running"])

    def set_running_controls(running):
        """Show the controls of the running or the editable state."""
        if running:
            refresh_btn.pack_forget()
            start_btn.pack_forget()
            limit_label.pack_forget()
            emu_limit_spin.pack_forget()
            stop_btn.pack(side="left", padx=10, pady=10)
            return
        stop_btn.pack_forget()
        refresh_btn.pack(side="left", padx=5)
        limit_label.pack(side="left", padx=5)
        emu_limit_spin.pack(side="left", padx=5)
        start_btn.pack(side="left", padx=10, pady=10)

    def render(current):
        """Render one snapshot, rebuilding rows only when their shape changes."""
        key = (tuple((instance["index"], instance["name"]) for instance in current["instances"]), current["tool_running"])
        if key != row_key:
            rebuild_rows(current)
        else:
            for instance in current["instances"]:
                row = rows.get(instance["index"])
                if row is None:
                    continue
                row["status_label"].configure(text=instance["status"])
                row["tooltip"] = instance["tooltip"]
        set_running_controls(current["tool_running"])

    def on_refresh():
        """Query the emulator backend and repaint the list."""
        result = service.refresh_instances(force=True)
        if not result.ok and result.error:
            show_centered_dialog(notebook, "Refresh failed", result.error)
        render(service.snapshot())

    def on_start_tool():
        """Start the tool on the selected instances."""
        result = service.start_tool()
        if not result.ok and result.error:
            show_centered_dialog(notebook, "Tool not started", result.error)
        render(service.snapshot())

    def on_stop_tool():
        """Stop the tool and repaint the editable state."""
        service.stop_tool()
        render(service.snapshot())

    def on_max_instances_change(*_args):
        """Persist and apply the emulator limit."""
        try:
            value = max_instances_var.get()
        except tk.TclError:
            return
        service.set_max_instances(value)

    def update_profile_comboboxes():
        """Refresh the profile options of every instance row."""
        current = service.snapshot()
        profiles = list(current["profiles"])
        for index, row in rows.items():
            row["combo"]["values"] = profiles
            profile_var = row["profile_var"]
            current_name = profile_var.get() if profile_var is not None else row["combo"].get()
            if current_name not in profiles and profiles:
                if profile_var is None:
                    row["combo"].set(profiles[0])
                    continue
                profile_var.set(profiles[0])
                service.update_instance_selection(index, row["checked"].get(), profiles[0])

    refresh_btn.config(command=on_refresh)
    start_btn.config(command=on_start_tool)
    stop_btn.config(command=on_stop_tool)
    max_instances_var.trace_add("write", on_max_instances_change)

    service.refresh_instances(force=True)
    render(service.snapshot())

    subscription = service.subscribe()

    def poll_events():
        """Apply pending service events on the Tk main thread."""
        for event in subscription.drain():
            if event.type == "state":
                render(event.data)
            elif event.type == "dialog":
                show_centered_dialog(notebook, event.data.get("title", "WoS Util"), event.data.get("text", ""))
        tab.after(200, poll_events)

    poll_events()

    return update_profile_comboboxes
