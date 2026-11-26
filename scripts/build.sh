#!/usr/bin/env bash
# Build the single-file Linux executable.
#
# Usage:
#   ./scripts/build.sh
#
# The resulting binary is dist/WosUtil. It uses the system GTK/WebKitGTK
# libraries through PyGObject, so the target machine needs python-gobject and
# webkit2gtk (the same dependencies the source checkout needs for the native
# window).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
PYINSTALLER="${PYINSTALLER:-$ROOT/.venv/bin/pyinstaller}"

if [[ ! -x "$PYTHON" ]]; then
    PYTHON="$(command -v python3)"
fi
if [[ ! -x "$PYINSTALLER" ]]; then
    PYINSTALLER="$(command -v pyinstaller)"
fi

echo "Fetching the pinned UI bundle..."
"$PYTHON" "$ROOT/scripts/fetch_ui.py"

echo "Running PyInstaller ($PYINSTALLER)..."
"$PYINSTALLER" --noconfirm --clean "$ROOT/wosutil.spec"

echo "Done: $ROOT/dist/WosUtil"
