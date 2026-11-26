"""Main entry point for WoS Util.

Launches the native desktop shell (pywebview/WebView2); pass ``--template`` to
open the standalone Template Creator window and ``--paste`` to type the host
clipboard into the focused Waydroid instance.
"""

import sys

from wosutil.ui.main import run, run_template

if __name__ == "__main__":
    if "--paste" in sys.argv[1:]:
        from wosutil.emulator.clipboard import main as paste_main

        raise SystemExit(paste_main())
    raise SystemExit(run_template() if "--template" in sys.argv[1:] else run())
