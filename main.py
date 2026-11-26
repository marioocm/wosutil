"""Main entry point for WoS Util.

Launches the native desktop shell (pywebview/WebView2); pass ``--template`` to
open the standalone Template Creator window.
"""

import sys

from wosutil.ui.main import run, run_template

if __name__ == "__main__":
    raise SystemExit(run_template() if "--template" in sys.argv[1:] else run())
