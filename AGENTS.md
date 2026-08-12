# AGENTS.md

## Project
Python automation tool for Whiteout Survival mobile game using Android emulators (MuMu, BlueStacks, LDPlayer). Windows-only.

## Structure
- `main.py` → entry point, launches Tkinter GUI
- `src/wosutil/` → pip-installable package (gui/, emulator/, tool/, template_creator.py)
- `templates/` → PNG images for OpenCV template matching
- `data/` → JSON config (instance_cache.json, profiles.json, etc.), auto-created at runtime
- `tests/` → pytest-based tests
- `wosutil.spec` + `scripts/build.ps1` → single-file exe build (bundles Tesseract)

## Key quirks
- Pip-installable: `pip install -e .` (see `pyproject.toml`).
- `data/*` is gitignored; the `data/` directory and its JSON files are auto-created at runtime (`save_json_file` in `src/wosutil/utils.py`).
- Linting/formatter: ruff, typechecking: mypy (see `pyproject.toml`).
- Agents **must run** full check after code changes.
- Emulator paths hardcoded to default installs (MuMu at `C:\Program Files\Netease\MuMuPlayer\`, BlueStacks at `C:\Program Files\BlueStacks_nxt\`, LDPlayer at `C:\LDPlayer\LDPlayer14\`).
- Dev mode requires system-installed Tesseract OCR (pytesseract wrapper); the built exe bundles its own copy (resolved in `src/wosutil/emulator/image_utils.py:resolve_tesseract_cmd`).
- In frozen (PyInstaller) mode `config.py` resolves templates from `sys._MEIPASS` and writes data/logs/debug to `%LOCALAPPDATA%\WosUtil`.
- Virtual env at `.venv/`.
- Templates and ROIs are defined in `src/wosutil/config.py` (TEMPLATE_PATHS, COORDINATES, ROI).

## Commands
- Install: `.venv/Scripts/pip install -e ".[dev]"`
- Run app: `python main.py` (or `wosutil` after install)
- Lint: `.venv/Scripts/ruff check .`
- Format: `.venv/Scripts/ruff format .`
- Typecheck: `.venv/Scripts/mypy src/wosutil/`
- Tests: `.venv/Scripts/python -m pytest` (all) or `.venv/Scripts/python -m pytest tests/test_utils.py` (specific)
- Full check (required after changes): run all three above
- Build: `python -m build`
- Build exe: `.\scripts\build.ps1` (needs 7-Zip; produces `dist\WosUtil.exe` with bundled Tesseract)