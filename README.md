# WoS Util

Python automation tool for **Whiteout Survival** that controls Android emulators (MuMu, BlueStacks, LDPlayer) to run a queue of daily in-game tasks automatically.

> **Warning:** Use at your own risk. Automating game actions may violate the game's terms of service and could lead to account restrictions. This project is not affiliated with or endorsed by the game developers.

## Features

- Multi-emulator support (MuMu, BlueStacks 5, LDPlayer) with per-instance ADB control
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

- Windows 10/11
- One emulator installed in its default location:
  - MuMu (`C:\Program Files\Netease\MuMuPlayer\`)
  - BlueStacks 5 (`C:\Program Files\BlueStacks_nxt\`)
  - LDPlayer (`C:\LDPlayer\LDPlayer14\`)
- ADB enabled on the emulator
- 720p portrait resolution (`720x1280`) in every instance
- Whiteout Survival installed and set to English (`com.gof.global`)

The distributed `WosUtil.exe` bundles Tesseract OCR, so nothing else needs to be installed.

## Installation

Download the latest `WosUtil.exe` from the [Releases](https://github.com/marioocm/WosUtil/releases) page and run it. No installation required.

For non-default emulator installations, open the Preferences tab, select the required folders or configuration file with the `Browse...` buttons, and click `Save Preferences`.

## Usage

1. Select your emulator type in the Preferences tab.
2. Create task profiles and choose which tasks each profile runs.
3. Assign profiles to emulator instances and start the automation.

## License

MIT — see [LICENSE](LICENSE).
