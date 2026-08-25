# WoS Util

Python automation tool for **Whiteout Survival** that controls Android emulators (MuMu, BlueStacks, LDPlayer) to run a queue of daily in-game tasks automatically.

> **Warning:** Use at your own risk. Automating game actions may violate the game's terms of service and could lead to account restrictions. This project is not affiliated with or endorsed by the game developers.

## Features

- Multi-emulator support (MuMu, BlueStacks 5, LDPlayer) with per-instance ADB control
- Per-instance task queues with auto-rescheduling (fixed delays, in-game timers via OCR, or considering the current UTC time)
- Built-in tasks: idle income and alliance chests, alliance tech, island life essence, mail, triumph, free hero chest, storehouse stamina, intel hunts, nomadic/mystery shops, VIP rewards, tundra trek, pet adventure, pet skills, troop training and promotion

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
