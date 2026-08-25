"""Configuration constants for WoS Util.

Contains paths, thresholds, and application settings.
"""

import os
import sys
import time
from typing import cast


def _is_frozen() -> bool:
    """Return True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def _bundle_dir() -> str:
    """Return the PyInstaller bundle extraction directory ('' when not frozen)."""
    return cast(str, getattr(sys, "_MEIPASS", ""))


def _app_dir() -> str:
    r"""Return the writable base directory.

    In a bundle this is ``%LOCALAPPDATA%\WosUtil`` (data, logs and debug
    captures must persist per-user and never depend on the executable's
    location, which may be read-only); in a source checkout it is the
    project root.
    """
    if _is_frozen():
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return os.path.join(local_app_data, "WosUtil")
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _templates_dir() -> str:
    """Return the read-only templates directory.

    In a bundle templates are extracted into the PyInstaller temp folder;
    in a source checkout they live under the project root.
    """
    if _is_frozen():
        return os.path.join(_bundle_dir(), "templates")
    return os.path.join(_app_dir(), "templates")


# --- Paths Configuration ---
MUMU_BASE_PATH = os.path.join("C:", os.sep, "Program Files", "Netease", "MuMuPlayer", "nx_main")
MUMU_ADB_PATH = os.path.join(MUMU_BASE_PATH, "adb.exe")
MUMU_MULTI_PLAYER_PATH = os.path.join(MUMU_BASE_PATH, "MuMuManager.exe")
MUMU_INSTANCE_BASE_PATH = os.path.join("C:", os.sep, "Program Files", "Netease", "MuMuPlayer", "vms")

# --- BlueStacks Paths Configuration ---
# BlueStacks 5 (nxt) uses its own HD-Adb server on port 5037; MuMu's adb.exe
# must never be used against it.
BLUESTACKS_BASE_PATH = os.path.join("C:", os.sep, "Program Files", "BlueStacks_nxt")
BLUESTACKS_HD_PLAYER_PATH = os.path.join(BLUESTACKS_BASE_PATH, "HD-Player.exe")
BLUESTACKS_ADB_PATH = os.path.join(BLUESTACKS_BASE_PATH, "HD-Adb.exe")
BLUESTACKS_CONF = os.path.join("C:", os.sep, "ProgramData", "BlueStacks_nxt", "bluestacks.conf")

# --- LDPlayer Paths Configuration ---
# LDPlayer keeps its own adb server on port 5037 and registers instances as
# emulator-5554, emulator-5556, ... (one per instance, +2 ports per instance).
LDPLAYER_BASE_PATH = os.path.join("C:", os.sep, "LDPlayer", "LDPlayer14")
LDPLAYER_CONSOLE_PATH = os.path.join(LDPLAYER_BASE_PATH, "ldconsole.exe")
LDPLAYER_ADB_PATH = os.path.join(LDPLAYER_BASE_PATH, "adb.exe")
LDPLAYER_PLAYER_PATH = os.path.join(LDPLAYER_BASE_PATH, "dnplayer.exe")
LDPLAYER_INSTANCE_CONFIG_DIR = os.path.join(LDPLAYER_BASE_PATH, "vms", "config")

# --- Application Data Paths ---
PROJECT_ROOT = _app_dir()
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
TEMPLATES_DIR = _templates_dir()
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, time.strftime("wosutil_%Y%m%d_%H%M%S.log"))
DEBUG_DIR = os.path.join(PROJECT_ROOT, "debug")

# Data files
INSTANCE_CACHE_FILE = os.path.join(DATA_DIR, "instance_cache.json")
INSTANCE_SELECTION_FILE = os.path.join(DATA_DIR, "instance_selection.json")
PROFILES_FILE = os.path.join(DATA_DIR, "profiles.json")
PREFERENCES_FILE = os.path.join(DATA_DIR, "preferences.json")
TASK_SCHEDULE_FILE = os.path.join(DATA_DIR, "task_schedule.json")

# Default profile created for new users, containing every available task
DEFAULT_PROFILE_NAME = "All"

# --- ADB Configuration ---
ADB_PORT = "16384"  # Default ADB port for MuMu Global 12
ADB_PORT_STEP = 32  # Port increment between consecutive MuMu instances
LDPLAYER_ADB_PORT = 5555  # Default ADB port for LDPlayer instance 0
LDPLAYER_ADB_PORT_STEP = 2  # Port increment between consecutive LDPlayer instances

# --- Game Configuration ---
WHITEOUT_PACKAGE = "com.gof.global"
WHITEOUT_ACTIVITY = "com.unity3d.player.MyMainPlayerActivity"  # Main game activity

# --- Template Image Paths ---
# Templates are organized in subfolders by game screen (city / world).
TEMPLATE_PATHS = {
    # City main screen
    "world": os.path.join(TEMPLATES_DIR, "city", "world.png"),
    # World map
    "city_icon": os.path.join(TEMPLATES_DIR, "world", "cityicon.png"),
    # Alliance tech
    "tech_thumb": os.path.join(TEMPLATES_DIR, "city", "alliance", "alliance_tech", "techthumb.png"),
    # Heroes
    "hero_recruit_screen": os.path.join(TEMPLATES_DIR, "city", "heroes", "recruit", "hero_recruit_screen.png"),
    "free_hero_chest": os.path.join(TEMPLATES_DIR, "city", "heroes", "recruit", "free_hero_chest.png"),
    # Island
    "life_essence": os.path.join(TEMPLATES_DIR, "city", "sidemenu", "island", "life_essence.png"),
    # Daily
    "sidemenu_daily_hide_completed_mission": os.path.join(TEMPLATES_DIR, "city", "sidemenu", "sidemenu_daily_hide_completed_mission.png"),
    # Train camp
    "train_troop": os.path.join(TEMPLATES_DIR, "city", "sidemenu", "train_camp", "train_troop.png"),
    "troop_train_speed_up": os.path.join(TEMPLATES_DIR, "city", "sidemenu", "train_camp", "troop_train_speed_up.png"),
    "train_troop_promote": os.path.join(TEMPLATES_DIR, "city", "sidemenu", "train_camp", "train_troop_promote.png"),
    # Tundra trek
    "tundra_trek_free_supplies": os.path.join(TEMPLATES_DIR, "city", "sidemenu", "tundra_trek", "tundra_trek_free_supplies.png"),
    "tundra_trek_idle_end_button": os.path.join(TEMPLATES_DIR, "city", "sidemenu", "tundra_trek", "tundra_trek_idle_end_button.png"),
    # Profile
    "storehouse_claim_stamina": os.path.join(TEMPLATES_DIR, "city", "profile", "storehouse_claim_stamina.png"),
    # Nomadic shop
    "nomadic_shop_iron": os.path.join(TEMPLATES_DIR, "city", "shop", "nomadic_shop", "nomadic_shop_iron.png"),
    "nomadic_shop_coal": os.path.join(TEMPLATES_DIR, "city", "shop", "nomadic_shop", "nomadic_shop_coal.png"),
    "nomadic_shop_wood": os.path.join(TEMPLATES_DIR, "city", "shop", "nomadic_shop", "nomadic_shop_wood.png"),
    "nomadic_shop_meat": os.path.join(TEMPLATES_DIR, "city", "shop", "nomadic_shop", "nomadic_shop_meat.png"),
    "nomadic_shop_vip": os.path.join(TEMPLATES_DIR, "city", "shop", "nomadic_shop", "nomadic_shop_vip.png"),
    "nomadic_shop_free_refresh": os.path.join(TEMPLATES_DIR, "city", "shop", "nomadic_shop", "nomadic_shop_free_refresh.png"),
    # Mystery shop
    "mystery_shop_free": os.path.join(TEMPLATES_DIR, "city", "shop", "mistery_shop", "mystery_shop_free.png"),
    "mystery_shop_widget_50": os.path.join(TEMPLATES_DIR, "city", "shop", "mistery_shop", "mystery_shop_widget_50.png"),
    "mystery_shop_widget_20": os.path.join(TEMPLATES_DIR, "city", "shop", "mistery_shop", "mystery_shop_widget_20.png"),
    "mystery_shop_free_refresh": os.path.join(TEMPLATES_DIR, "city", "shop", "mistery_shop", "mystery_shop_free_refresh.png"),
    # Pets
    "pet_skill_button": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_skill_button.png"),
    "pet_skill_screen": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_skill_screen.png"),
    "pet_skill_wolf": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_skill_wolf.png"),
    "pet_skill_ox": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_skill_ox.png"),
    "pet_skill_ox_active": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_skill_ox_active.png"),
    "pet_skill_tapir": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_skill_tapir.png"),
    "pet_skill_elk": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_skill_elk.png"),
    "pet_skill_use": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_skill_use.png"),
    "pet_adventure_screen": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_screen.png"),
    "pet_adventure_chest1": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_chest1.png"),
    "pet_adventure_chest1_ready": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_chest1_ready.png"),
    "pet_adventure_chest1b": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_chest1b.png"),
    "pet_adventure_chest2": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_chest2.png"),
    "pet_adventure_chest2_ready": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_chest2_ready.png"),
    "pet_adventure_chest2b": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_chest2b.png"),
    "pet_adventure_chest3": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_chest3.png"),
    "pet_adventure_chest3_ready": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_chest3_ready.png"),
    "pet_adventure_chest3b": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_chest3b.png"),
    "pet_adventure_select_pet_button": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_select_pet_button.png"),
    "pet_adventure_start_button": os.path.join(TEMPLATES_DIR, "city", "pets", "pet_adventure_start_button.png"),
    # Intel
    "intel_button": os.path.join(TEMPLATES_DIR, "world", "intel", "intel_button.png"),
    "intel_screen": os.path.join(TEMPLATES_DIR, "world", "intel", "intel_screen.png"),
    "intel_beast": os.path.join(TEMPLATES_DIR, "world", "intel", "intel_beast.png"),
    "intel_survivor": os.path.join(TEMPLATES_DIR, "world", "intel", "intel_survivor.png"),
    "intel_exploration": os.path.join(TEMPLATES_DIR, "world", "intel", "intel_exploration.png"),
    "intel_fcbeast": os.path.join(TEMPLATES_DIR, "world", "intel", "intel_fcbeast.png"),
    "intel_firehunter": os.path.join(TEMPLATES_DIR, "world", "intel", "intel_firehunter.png"),
    "intel_fcsurvivor": os.path.join(TEMPLATES_DIR, "world", "intel", "intel_fcsurvivor.png"),
    "intel_fcexploration": os.path.join(TEMPLATES_DIR, "world", "intel", "intel_fcexploration.png"),
    "intel_claim_all": os.path.join(TEMPLATES_DIR, "world", "intel", "intel_claim_all.png"),
    "exploration_victory": os.path.join(TEMPLATES_DIR, "world", "intel", "exploration_victory.png"),
    # Task list (world map schedule panel)
    "task_list_time": os.path.join(TEMPLATES_DIR, "world", "task_list_time.png"),
    # March
    "no_troops_left": os.path.join(TEMPLATES_DIR, "world", "march", "no_troops_left.png"),
    "send_march_screen": os.path.join(TEMPLATES_DIR, "world", "march", "send_march_screen.png"),
    "gather_tile_increase_level": os.path.join(TEMPLATES_DIR, "world", "gather", "gather_tile_increase_level.png"),
    "remove_hero": os.path.join(TEMPLATES_DIR, "world", "march", "remove_hero.png"),
}

# --- Automation Parameters ---
SCREEN_CHECK_THRESHOLD = 0.8  # Confidence threshold for template matching (0.0 to 1.0)
MAIN_SCREEN_MAX_ATTEMPTS = 5  # Max attempts to reach main screen by pressing back
BACK_BUTTON_DELAY = 0.6  # Short delay after pressing back button
CLICK_DELAY = 0.6  # Delay between consecutive clicks in a sequence
INTEL_TIMER_MIN_SECONDS = 60  # Minimum reschedule time to trust the intel screen timer OCR
INTEL_BEAST_MAX_WAIT_SECONDS = 1000  # Max wait for a beast march (timer read * 2, capped at this)
INTEL_BEAST_TIMER_MAX_SECONDS = 30 * 60  # Max plausible beast march timer; longer reads are treated as OCR errors
INTEL_BEAST_MARCH_SENT_WAIT_SECONDS = 240  # Reschedule when the march was sent but the timer could not be read
INTEL_BEAST_MAX_RETRIES = 3  # Max attempts to confirm the send-march screen before giving up

# --- City Coordinates (main screen specific points) ---
COORDINATES = {
    "world": (654, 1218),
    "alliance": (535, 1218),
    "shop": (420, 1218),
    "heroes": (188, 1218),
    "exploration": (70, 1218),
    "vip": (500, 70),
    "profile": (50, 45),
    "sidemenu": (13, 550),
    "intel": (664, 864),
    "world_schedule": (98, 24),
}

# --- Region of Interest (ROI) for Template Matching ---
ROI = {
    "city": (614, 1178, 70, 53),
    "world": (627, 1175, 46, 55),
    "tech_thumb": (58, 322, 510, 958),
    "sidemenu": (0, 173, 484, 759),
    "shop_tabs": (0, 1195, 719, 85),
    "sidemenu_icons": (17, 342, 58, 517),
    "bottom_right_side_icons": (589, 673, 129, 431),
    "island_life_essence": (0, 63, 633, 1041),
    "recruit_free_chest": (112, 806, 162, 438),
    "recruit_free_chest_timer": (137, 771, 179, 33),
    "hero_recruit_screen": (583, 164, 134, 414),
    "intel": (12, 158, 695, 976),
    "intel_claim_all": (7, 997, 702, 282),
    "intel_screen": (0, 0, 324, 98),
    "intel_timer": (357, 105, 168, 42),
    "intel_timer2": (370, 585, 171, 49),
    "storehouse_claim_stamina": (491, 343, 177, 74),
    "storehouse_claim_stamina_timer": (502, 366, 156, 63),
    "walking_march_time": (501, 1138, 118, 29),
    "send_march_screen": (0, 0, 236, 91),
    "tundra_trek_supplies_timer": (520, 587, 112, 33),
    "tundra_trek_idle": (526, 1126, 194, 154),
    "pet_adventure_screen": (0, 0, 595, 80),
    "pet_skill_screen": (354, 0, 364, 231),
    "pet_skill_buttons": (0, 163, 717, 651),
    "pet_skill_use": (22, 855, 676, 401),
    "pet_skill_wolf_timer": (227, 293, 120, 27),
    "pet_skill_ox_timer": (372, 285, 123, 43),
    "pet_skill_tapir_timer": (520, 293, 120, 27),
    "pet_skill_elk_timer": (226, 430, 122, 46),
    "troop_train_speed_up": (306, 940, 412, 310),
    "troop_train_timer": (458, 920, 127, 38),
    "troop_promote_text": (3, 768, 717, 28),
    "train_troop_promote": (561, 394, 159, 274),
    "world_schedule_utc": (99, 0, 264, 42),
    "task_list": (0, 396, 720, 883),
    "worldmap_search": (0, 843, 718, 435),
    "worldmap": (0, 95, 718, 1008),
    "gathering_tile_info": (117, 200, 488, 563),
}
