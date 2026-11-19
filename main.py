"""Main entry point for WoS Util.

Launches the native desktop shell (pywebview/WebView2).
"""

from wosutil.ui.main import run

if __name__ == "__main__":
    raise SystemExit(run())
