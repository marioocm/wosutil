# WoS Util

Python automation tool for **Whiteout Survival** that controls Android emulators (MuMu, BlueStacks, LDPlayer on Windows; Waydroid on Linux) to run a queue of daily in-game tasks automatically.

> **Warning:** Use at your own risk. Automating game actions may violate the game's terms of service and could lead to account restrictions. This project is not affiliated with or endorsed by the game developers.

## Features

- Multi-emulator support (MuMu, BlueStacks 5, LDPlayer on Windows; Waydroid on Linux) with per-instance ADB control
- Per-instance task queues with auto-rescheduling (fixed delays, in-game timers via OCR, or considering the current UTC time)
- Built-in tasks:
  - Play Bear Trap
  - Intel Missions
  - Troop Training and Promotion
  - Nomadic and Mystery Shop
  - Pet Chests
  - Tundra Trek
  - And many more daily features (island, hero chests, VIP, triumph...)
- **Light on resources:** the emulators dont need to stay open. The program knows when each task is due and opens/closes the emulator instances in the background, so it doesn't disturb you and isn't wasting resources while idle

## About the interface

The UI is intentionally basic — it hasn't received much work yet and will keep improving over time. The current priority is adding more features, but a nicer interface is planned.

## Feedback

The project is open to any suggestion. Feel free to open an [issue](https://github.com/marioocm/WosUtil/issues) or a pull request with your ideas.

## Requirements

### Windows 10/11

- One emulator installed in its default location:
  - MuMu (`C:\Program Files\Netease\MuMuPlayer\`)
  - BlueStacks 5 (`C:\Program Files\BlueStacks_nxt\`)
  - LDPlayer (`C:\LDPlayer\LDPlayer14\`)
- ADB enabled on the emulator
- 720p portrait resolution (`720x1280`) in every instance
- Whiteout Survival installed and set to English (`com.gof.global`)

The distributed `WosUtil.exe` bundles RapidOCR (PP-OCR models and ONNX Runtime), so nothing else needs to be installed.

### Linux with Waydroid

- [Waydroid](https://waydro.id/) with Android 13 and Whiteout Survival installed (`com.gof.global`)
- Android set to **720x1280** portrait (the templates are captured at that size)
- `adb` on `PATH` (package `android-tools` on Arch/CachyOS, `adb` on Debian/Ubuntu)
- `gamescope` recommended: it scales the 720x1280 Android surface into a 576x1024 window that fits a 1080p monitor
- `kdotool` optional: the tool uses it to place each instance window side by side and to minimize it on KDE Plasma
- Python 3.11+ and a source checkout:

```bash
git clone https://github.com/marioocm/WosUtil.git
cd WosUtil
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python main.py
```

The native window uses WebKitGTK through PyGObject. When the virtual environment
cannot import `gi`, the shell falls back to a Chromium app window (or the default
browser). To keep the native window, create the environment with
`python -m venv --system-site-packages .venv` after installing the distribution's
`python-gobject` package.

Instances are auto-discovered from `/var/lib/waydroid` (the default instance) and
`/var/lib/waydroid.<id>` (additional instances). The visible names come from
`~/.config/waydroid-instances.json`; the ADB address is read from the instance's
dnsmasq lease. Additional instances need the local `waydroid-multi` wrapper on
`PATH`; the default one uses the regular `waydroid` command.

Each gamescope window is moved back to the position it had when the instance was
last stopped (stored in `data/waydroid_windows.json`, or the equivalent file
under `~/.local/share/WosUtil/data/` for the executable). Delete that file to
reset the layout to the default side-by-side positions.

## Installation

On Windows, download the latest `WosUtil.exe` from the [Releases](https://github.com/marioocm/WosUtil/releases) page and run it. No installation required.

For non-default emulator installations, open the Preferences tab, select the required folders or configuration file with the `Browse...` buttons, and click `Save Preferences`.

On Linux there is no prebuilt executable yet: install it from source with the commands above and run `.venv/bin/python main.py` (or `.venv/bin/wosutil` after the editable install).

### Build the Linux executable

With the development dependencies installed (`pip install -e ".[dev]"`), build the
single-file binary with:

```bash
./scripts/build.sh
# dist/WosUtil
```

The executable bundles Python, the UI, the templates and the OCR runtime. It
still uses the system GTK/WebKitGTK libraries (through PyGObject) for the
native window, so the target machine needs `python-gobject` and
`webkit2gtk-4.1` (plus `gamescope`, `adb` and the Waydroid setup described
above). When those libraries are missing, run the source checkout and let the
shell fall back to the browser.

## Usage

1. Select your emulator type in the Preferences tab.
2. Create task profiles and choose which tasks each profile runs.
3. Assign profiles to emulator instances and start the automation.

## License

MIT — see [LICENSE](LICENSE).
