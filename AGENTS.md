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
- Every change PR bumps `version` in `pyproject.toml` (in the same branch) and builds the exe (`.\scripts\build.ps1`) before publishing.
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

## Git workflow
The project follows GitHub Flow: `main` is always stable and ready to use; all work happens on short-lived branches merged via Pull Requests (squash merge, branch deleted after merge).

### Branch naming
Prefix branches by change type:
- `feat/` — new functionality
- `fix/` — bug fixes
- `docs/` — documentation only (AGENTS.md, README, comments)
- `refactor/` — code changes that add no feature and fix no bug
- `test/` — tests only
- `ci/` — CI/CD, GitHub Actions, build tooling

### Commit message convention (Conventional Commits)
Format: `<type>(<scope optional>): <short description in imperative mood>` (first line ≤ 72 chars, no trailing period).

Types: `feat`, `fix`, `docs`, `refactor`, `style`, `test`, `chore`, `ci`.

Rules:
- Always write the description in English, in imperative mood ("add X", not "added X" or "X added").
- Keep commits atomic: one logical change per commit; split unrelated changes (e.g. `fix:` + `style:`) into separate commits.
- Add a body (blank line after header) only when the "why" matters; never restate the "how".
- Reference issues in the footer when applicable (`Closes #123`).

### Process
1. Before starting, update `main`: `git checkout main && git pull origin main`.
2. Create the branch: `git checkout -b <type>/<short-description>` (e.g. `feat/export-json`).
3. Commit in small increments using the conventions above.
4. Push the branch and open a Pull Request (even for solo work: it runs CI checks and forces a self-review of the diff).
5. Merge via squash, then delete the remote and local branches.

### CI
`.github/workflows/ci.yml` runs ruff, mypy and pytest on every push/PR. Agents must ensure checks pass before pushing; never merge a PR with failing checks.