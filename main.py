"""Main entry point for WoS Util application.

Launches the GUI for Whiteout Survival automation tool.
"""

from wosutil.gui.gui_main import run_gui
from wosutil.utils import setup_logging

# Configure console and file logging once, before anything emits records.
setup_logging()

if __name__ == "__main__":
    run_gui()
