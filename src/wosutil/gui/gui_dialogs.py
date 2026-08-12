"""Shared GUI dialog helpers."""

import tkinter as tk
from tkinter import ttk


def center_window_on_screen(window):
    """Center the given Tk window on the screen.

    Args:
        window: The Tk/Toplevel window to reposition.
    """
    window.update_idletasks()
    width = window.winfo_reqwidth()
    height = window.winfo_reqheight()
    x = max((window.winfo_screenwidth() - width) // 2, 0)
    y = max((window.winfo_screenheight() - height) // 2, 0)
    window.geometry(f"+{x}+{y}")


def show_centered_dialog(parent, title, text):
    """Show a modal, screen-centered dialog with a single OK button.

    Args:
        parent: The parent Tk window.
        title (str): Dialog title.
        text (str): Message shown in the dialog.
    """
    top = tk.Toplevel(parent)
    top.title(title)
    top.transient(parent)
    top.grab_set()
    top.configure(bg="#2C3E50")

    ttk.Label(top, text=text, wraplength=480, justify="center").pack(padx=24, pady=(20, 12))
    ttk.Button(top, text="OK", command=top.destroy).pack(pady=(0, 16))

    center_window_on_screen(top)
    top.wait_window()
