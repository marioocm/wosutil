# AGENTS.md

## Project
Python automation tool for Whiteout Survival mobile game using Android emulators (MuMu, BlueStacks, LDPlayer). Windows-only.

## Structure
- `main.py` → entry point, launches Tkinter GUI
- `src/wosutil/` → pip-installable package (gui/, emulator/, tool/, template_creator.py)
- `src/wosutil/ui/` → local HTTP server, browser launcher and UI bundle resolution/installation for the desktop shell
- `ui.lock.json` → pinned UI manifest URL and SHA-256 consumed by `scripts/fetch_ui.py`
- `templates/` → PNG images for OpenCV template matching
- `data/` → JSON config (instance_cache.json, profiles.json, etc.), auto-created at runtime
- `tests/` → pytest-based tests
- `wosutil.spec` + `scripts/build.ps1` → single-file exe build (bundles RapidOCR + ONNX Runtime)

## Key quirks
- Pip-installable: `pip install -e .` (see `pyproject.toml`). Requires Python >= 3.11 (ONNX Runtime).
- `wosutil-web` is the opt-in desktop shell: it serves the static UI bundle from a loopback HTTP server (`/api/state`, `/api/events` over SSE, `/api/command`) and opens it in a Chromium app window (Edge/Chrome). The UI bundle is built and published by `wosutil-site`; `scripts/fetch_ui.py` downloads the manifest pinned in `ui.lock.json` into `src/wosutil/webui/static/` (gitignored). `WOSUTIL_UI_DIR` overrides the bundle directory for development. The manifest's `apiVersion` must match `wosutil.ui.contract.API_VERSION`; incompatible bundles fall back to a built-in page.
- On Windows the shell watches the app window (`wosutil/ui/windows.py`): it focuses it on launch and exits when it closes. The page heartbeat is the fallback on other platforms or if the watcher is unavailable.
- The preferences screen reads `GET /api/preferences` (values, task catalog, labels and bounds come from the Python constants) and asks `POST /api/pick-path` for native folder/file dialogs. Clients that edit one emulator's paths keep the others untouched.
- `wosutil` still launches the Tkinter shell. The web shell is portable (Windows/macOS/Linux), but the emulator backends and `adb.exe` are Windows-only; Linux would open the UI without being able to automate anything until a backend is ported.
- PyInstaller bundles the fetched UI as `webui` (`wosutil.spec`), and `scripts/build.ps1` runs the fetch step first.
- `data/*` is gitignored; the `data/` directory and its JSON files are auto-created at runtime (`save_json_file` in `src/wosutil/utils.py`).
- Linting/formatter: ruff, typechecking: mypy (see `pyproject.toml`).
- Agents **must run** full check after code changes.
- `src/wosutil/adb.py` is the pure ADB transport (`AdbClient`); it knows nothing about emulators. Each emulator backend owns one client and only supplies the ADB serial per instance.
- All emulator-specific code (instance discovery, start/stop, ADB serial and command shape) lives in `src/wosutil/emulator/backends.py`; add a new emulator by implementing `EmulatorBackend` and registering it in `create_backend`. `src/wosutil/emulator/emulator_manager.py` only exposes game/ADB actions on the active backend.
- `src/wosutil/tool/tool_instances_controller.py` schedules due instances: an instance is opened when its next task is within `TASK_STARTUP_WINDOW_SECONDS` and closed when the next one is farther than `INSTANCE_KEEPALIVE_WINDOW_SECONDS`. There is no shared queue to maintain; add scheduling behavior there. The controller receives a plain `(instance_index, profile_name)` selection and does not depend on Tkinter.
- `ProfileManager` only owns persisted profiles; per-instance runtime state is `InstanceState` wrapped by `InstanceRuntime` in the controller.
- Instance status and tooltip formatting lives in `src/wosutil/gui/gui_instance_status.py` (no Tkinter), so it can be tested without a GUI.
- Emulator paths hardcoded to default installs (MuMu at `C:\Program Files\Netease\MuMuPlayer\`, BlueStacks at `C:\Program Files\BlueStacks_nxt\`, LDPlayer at `C:\LDPlayer\LDPlayer14\`).
- OCR runs in-process through RapidOCR (`src/wosutil/emulator/ocr.py`) and ONNX Runtime; no system OCR installation is required. The models and YAML routing tables are package data bundled by `wosutil.spec`.
- In frozen (PyInstaller) mode `config.py` resolves templates from `sys._MEIPASS` and writes data/logs/debug to `%LOCALAPPDATA%\WosUtil`.
- Virtual env at `.venv/`.
- Every change PR bumps `version` in `pyproject.toml` (in the same branch). The exe is built automatically by the Release workflow when a `v*` tag is pushed (see `.github/workflows/release.yml`).
- Templates and ROIs are defined in `src/wosutil/config.py` (TEMPLATE_PATHS, COORDINATES, ROI).

## Commands
- Install: `.venv/Scripts/pip install -e ".[dev]"`
- Run app: `python main.py` (or `wosutil` after install)
- Run desktop shell: `wosutil-web` (or `python -m wosutil.ui.main`)
- UI dev overrides: `WOSUTIL_UI_PORT`, `WOSUTIL_UI_TOKEN`, `WOSUTIL_UI_URL` and `WOSUTIL_UI_NO_WINDOW` let the shell work against the Astro dev server (see `wosutil-site/AGENTS.md`).
- Fetch the pinned UI: `python scripts/fetch_ui.py` (`--from-dir ..\wosutil-site\dist\ui` for a local build)
- Lint: `.venv/Scripts/ruff check .`
- Format: `.venv/Scripts/ruff format .`
- Typecheck: `.venv/Scripts/mypy src/wosutil/`
- Tests: `.venv/Scripts/python -m pytest` (all) or `.venv/Scripts/python -m pytest tests/test_utils.py` (specific)
- Full check (required after changes): run all three above
- Build: `python -m build`

## Testing policy
- Add a test for observable behavior or for a regression that actually happened, not for every getter, default or private branch.
- Prefer one parameterized test over several near-duplicate tests.
- Mock only external boundaries (ADB, filesystem, clock, GUI); avoid asserting private call sequences unless order is the behavior.
- Never add a test just to raise coverage; if the existing tests already cover the behavior, leave them alone.
- Keep the suite fast: patch `time.sleep` in tests instead of waiting real seconds.

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