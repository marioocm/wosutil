"""UI-agnostic application layer.

The modules in this package own the application state, validation and use
cases; views (the current Tkinter shell and the planned web front-end) only
render snapshots and forward commands. Nothing here imports a GUI toolkit.
"""
