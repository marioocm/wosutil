# AGENTS.md

## Project
Python automation tool for Whiteout Survival mobile game using Android emulators (MuMu, BlueStacks, LDPlayer on Windows; Waydroid on Linux).

## Structure
- `main.py` → entry point, launches the native desktop shell
- `src/wosutil/` → pip-installable package (app/, emulator/, tool/, ui/)
- `src/wosutil/ui/` → local HTTP server, pywebview window, browser fallback and UI bundle resolution/installation
- `src/wosutil/app/templates.py` → template capture/crop/save use cases for the Templates screen
- `ui.lock.json` → pinned UI manifest URL and SHA-256 consumed by `scripts/fetch_ui.py`
- `templates/` → PNG images for OpenCV template matching
- `data/` → JSON config (instance_cache.json, profiles.json, etc.), auto-created at runtime
- `tests/` → pytest-based tests
- `wosutil.spec` + `scripts/build.ps1` → single-file exe build (bundles pywebview/WebView2, RapidOCR + ONNX Runtime and the UI)

## Key quirks
- Pip-installable: `pip install -e .` (see `pyproject.toml`). Requires Python >= 3.11 (ONNX Runtime).
- `wosutil` and the exe launch the **native pywebview window** (WebView2 on Windows, WebKitGTK/PyGObject on Linux, WKWebView on macOS). It is not a browser tab: no browser chrome, no browser context menu and no find/save/print shortcuts. The old Edge/Chrome `--app` launcher is only a fallback when pywebview is unavailable or cannot start. `WOSUTIL_UI_DEBUG=1` opens the webview devtools.
- `template-creator` (or `WosUtil.exe --template`) opens the **standalone Template Creator window**. It is a development tool, not part of the user-facing shell: its own page (`templates/index.html`) and window, no navigation in the main app.
- Linux needs the WebKitGTK/PyGObject stack for the native window; otherwise the shell falls back to the default browser. Python 3.15 is expected to work because pythonnet publishes cp315 wheels, but validate it with the release interpreter before switching.
- The UI bundle is built and published by `wosutil-site` on Cloudflare Pages at `https://wosutil.tech/ui/`; `ui.lock.json` pins the `wosutil-site.pages.dev` alias because the zone's bot protection challenges HTML downloads from CI. `scripts/fetch_ui.py` downloads the pinned manifest into `src/wosutil/webui/static/` (gitignored). `WOSUTIL_UI_DIR` overrides the bundle directory for development. The manifest's `apiVersion` must match `wosutil.ui.contract.API_VERSION`; incompatible bundles fall back to a built-in page.
- The Templates screen (standalone window) loads/captures an image, selects an ROI and saves PNG crops under `TEMPLATES_WRITE_DIR` (repo `templates/` in source mode, `%LOCALAPPDATA%\WosUtil\templates` in the frozen exe). It uses the same active backend as the automation tool.
- The preferences screen reads `GET /api/preferences` (values, task catalog, labels and bounds come from the Python constants) and asks `POST /api/pick-path` for native folder/file dialogs. Clients that edit one emulator's paths keep the others untouched.
- The shell is portable (Windows/macOS/Linux). On Windows the active backends are MuMu, BlueStacks and LDPlayer; on Linux the active backend is Waydroid (Android 13+, 720x1280, ADB over the instance's dnsmasq lease, optional gamescope 576x1024 window). macOS has no backend yet.
- Waydroid instances are discovered from `/var/lib/waydroid` (default) and `/var/lib/waydroid.<id>`; a real instance must contain `waydroid.cfg`. Visible names come from `~/.config/waydroid-instances.json`, and additional instances need the local `waydroid-multi` wrapper on `PATH`.
- `src/wosutil/emulator/backends.py` holds `WaydroidBackend` too; it starts sessions inside gamescope, waits for `sys.boot_completed`, shows the Android UI and places/minimizes windows with `kdotool` when available. Secondary container services (`waydroid-container@<id>.service`) must already be running; the tool never asks for root.
- Waydroid window positions are persisted per instance in `WAYDROID_WINDOW_FILE` (`data/waydroid_windows.json`) when the session is stopped; `_place_window` restores them and falls back to the `WAYDROID_WINDOW_STEP` side-by-side slots when there is no saved position. `kdotool search --pid` is unreliable in the installed version, so windows are matched by the `gamescope` class and then filtered by owner PID.
- PyInstaller bundles the fetched UI as `webui`, the native webview backend (WebView2 on Windows, WebKitGTK on Linux through PyGObject) and RapidOCR/ONNX Runtime, and excludes Tkinter (`wosutil.spec` is cross-platform). `scripts/build.ps1` builds the Windows exe; `scripts/build.sh` builds the Linux binary `dist/WosUtil` (it still needs the system GTK/WebKitGTK libraries at runtime).
- `data/*` is gitignored; the `data/` directory and its JSON files are auto-created at runtime (`save_json_file` in `src/wosutil/utils.py`).
- Linting/formatter: ruff, typechecking: mypy (see `pyproject.toml`).
- Agents **must run** full check after code changes.
- `src/wosutil/adb.py` is the pure ADB transport (`AdbClient`); it knows nothing about emulators. Each emulator backend owns one client and only supplies the ADB serial per instance.
- All emulator-specific code (instance discovery, start/stop, ADB serial and command shape) lives in `src/wosutil/emulator/backends.py`; add a new emulator by implementing `EmulatorBackend` and registering it in `create_backend`. `src/wosutil/emulator/emulator_manager.py` only exposes game/ADB actions on the active backend.
- Scrolling with a held finger uses `input touchscreen motionevent` on every backend; the explicit `touchscreen` source is required on Waydroid (the bare `input motionevent` form raises). The swipe+hold fallback stays for Android builds without `motionevent`.
- `src/wosutil/tool/tool_instances_controller.py` schedules due instances: an instance is opened when its next task is within `TASK_STARTUP_WINDOW_SECONDS` and closed when the next one is farther than `INSTANCE_KEEPALIVE_WINDOW_SECONDS`. There is no shared queue to maintain; add scheduling behavior there. The controller receives a plain `(instance_index, profile_name)` selection and does not depend on a UI toolkit.
- `ProfileManager` only owns persisted profiles; per-instance runtime state is `InstanceState` wrapped by `InstanceRuntime` in the controller.
- Instance status and tooltip formatting lives in `src/wosutil/app/presentation.py` (no GUI toolkit), so it can be tested without a window.
- Emulator paths hardcoded to default installs (MuMu at `C:\Program Files\Netease\MuMuPlayer\`, BlueStacks at `C:\Program Files\BlueStacks_nxt\`, LDPlayer at `C:\LDPlayer\LDPlayer14\`).
- OCR runs in-process through RapidOCR (`src/wosutil/emulator/ocr.py`) and ONNX Runtime; no system OCR installation is required. The models and YAML routing tables are package data bundled by `wosutil.spec`.
- In frozen (PyInstaller) mode `config.py` resolves templates from `sys._MEIPASS` and writes data/logs/debug to `%LOCALAPPDATA%\WosUtil` on Windows and `$XDG_DATA_HOME/WosUtil` on Linux.
- Virtual env at `.venv/`.
- Every change PR bumps `version` in `pyproject.toml` (in the same branch). The Windows exe is built automatically by the Release workflow when a `v*` tag is pushed (see `.github/workflows/release.yml`); the Linux binary is built with `scripts/build.sh` until a Linux release job is added.
- Templates and ROIs are defined in `src/wosutil/config.py` (TEMPLATE_PATHS, COORDINATES, ROI).

## Commands
- Install: `.venv/bin/pip install -e ".[dev]"` (Windows: `.venv/Scripts/pip install -e ".[dev]"`)
- Run app: `python main.py` (or `wosutil` after install)
- Run Template Creator: `template-creator` (or `WosUtil.exe --template`)
- Run desktop shell: `wosutil-web` (same as `wosutil`; kept as an explicit alias)
- UI dev overrides: `WOSUTIL_UI_PORT`, `WOSUTIL_UI_TOKEN`, `WOSUTIL_UI_URL` and `WOSUTIL_UI_NO_WINDOW` let the shell work against the Astro dev server (see `wosutil-site/AGENTS.md`).
- Fetch the pinned UI: `python scripts/fetch_ui.py` (`--from-dir ..\wosutil-site\dist\ui` for a local build)
- Lint: `.venv/bin/ruff check .`
- Format: `.venv/bin/ruff format .`
- Typecheck: `.venv/bin/mypy src/wosutil/`
- Tests: `.venv/bin/python -m pytest` (all) or `.venv/bin/python -m pytest tests/test_utils.py` (specific)
- Full check (required after changes): run all three above
- Build wheel: `python -m build`
- Build Windows exe: `.\scripts\build.ps1`
- Build Linux binary: `./scripts/build.sh` (`dist/WosUtil`)

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