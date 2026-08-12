"""Main entry point for WoS Util application.

Launches the GUI for Whiteout Survival automation tool.
"""

import logging

from wosutil.gui.gui_main import run_gui

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

if __name__ == "__main__":
    run_gui()
