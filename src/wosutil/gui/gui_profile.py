"""GUI module for managing task profiles."""

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk


def setup_profiles_tab(notebook, profile_manager, log_message, TASK_DEFINITIONS, on_profiles_changed=None):
    """Setup the profiles tab in the GUI.

    Args:
        notebook: The parent notebook widget.
        profile_manager: Profile manager instance.
        log_message: Logging function.
        TASK_DEFINITIONS: Dictionary of task definitions.
        on_profiles_changed: Optional callback invoked after a profile is
            saved or deleted, so other tabs can refresh their profile lists.
    """
    profiles_tab = ttk.Frame(notebook)
    notebook.add(profiles_tab, text="Profiles")

    profile_control_frame = ttk.LabelFrame(profiles_tab, text="Profile Management")
    profile_control_frame.pack(pady=10, padx=10, fill="x")

    ttk.Label(profile_control_frame, text="Profile:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
    profile_combobox = ttk.Combobox(profile_control_frame, values=list(profile_manager.profiles.keys()))
    profile_combobox.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

    task_defs_by_id = {task["id"]: task for task in TASK_DEFINITIONS}
    task_checkbox_vars = {}
    task_checkbox_widgets = {}
    for i, task in enumerate(TASK_DEFINITIONS):
        var = tk.BooleanVar()
        cb = ttk.Checkbutton(profile_control_frame, text=f"{task['name']} (Priority: {task['priority']})", variable=var)
        cb.grid(row=i + 1, column=0, columnspan=4, sticky="w", padx=10, pady=2)
        task_checkbox_vars[task["id"]] = var
        task_checkbox_widgets[task["id"]] = cb

    def refresh_priorities():
        """Update the priority shown next to each task to the current values."""
        for task_id, cb in task_checkbox_widgets.items():
            task = task_defs_by_id.get(task_id)
            if task is not None:
                cb.config(text=f"{task['name']} (Priority: {task['priority']})")

    def on_tab_changed(event):
        try:
            if notebook.index("current") == notebook.index(profiles_tab):
                refresh_priorities()
        except tk.TclError:
            pass

    notebook.bind("<<NotebookTabChanged>>", on_tab_changed)

    def load_profile_selection(*_):
        profile_name = profile_combobox.get()
        selected_tasks = profile_manager.profiles.get(profile_name, [])
        for task_id, var in task_checkbox_vars.items():
            var.set(task_id in selected_tasks)
        log_message(f"Profile '{profile_name}' loaded in selection.", level="info")

    def save_current_profile():
        profile_name = profile_combobox.get()
        if not profile_name:
            profile_name = simpledialog.askstring("Profile name", "Enter a name for the new profile:")
            if not profile_name:
                return
        selected_tasks = [task_id for task_id, var in task_checkbox_vars.items() if var.get()]
        profile_manager.profiles[profile_name] = selected_tasks
        profile_manager.save_profiles()
        profile_combobox["values"] = list(profile_manager.profiles.keys())
        profile_combobox.set(profile_name)
        if on_profiles_changed is not None:
            on_profiles_changed()
        messagebox.showinfo("Saved", f"Profile '{profile_name}' saved successfully.")

    def delete_current_profile():
        profile_name = profile_combobox.get()
        if not profile_name:
            messagebox.showerror("Error", "No profile selected to be deleted.")
            return
        if messagebox.askyesno("Confirm", f"Are you sure you want to delete the profile '{profile_name}'?") and profile_name in profile_manager.profiles:
            del profile_manager.profiles[profile_name]
            profile_manager.save_profiles()
            profile_combobox.set("")
            profile_combobox["values"] = list(profile_manager.profiles.keys())
            for var in task_checkbox_vars.values():
                var.set(False)
            if on_profiles_changed is not None:
                on_profiles_changed()
            messagebox.showinfo("Deleted", f"Profile '{profile_name}' deleted.")

    profile_combobox.bind("<<ComboboxSelected>>", load_profile_selection)
    ttk.Button(profile_control_frame, text="Save Profile", command=save_current_profile).grid(row=0, column=2, padx=5, pady=5)
    ttk.Button(profile_control_frame, text="Delete Profile", command=delete_current_profile).grid(row=0, column=3, padx=5, pady=5)
