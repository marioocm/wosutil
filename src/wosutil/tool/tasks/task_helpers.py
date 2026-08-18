"""Task helper functions module.

Helper functions for navigation, screen detection, and common automation patterns.
"""

import time

# Import configuration and utility functions
from wosutil.config import (
    CLICK_DELAY,
    INTEL_BEAST_MARCH_SENT_WAIT_SECONDS,
    INTEL_BEAST_MAX_RETRIES,
    INTEL_BEAST_MAX_WAIT_SECONDS,
    INTEL_BEAST_TIMER_MAX_SECONDS,
    MAIN_SCREEN_MAX_ATTEMPTS,
    ROI,
    SCREEN_CHECK_THRESHOLD,
)
from wosutil.context import get_multi_instance_manager
from wosutil.emulator.emulator_manager import (
    click_on,
    click_on_coordinates,
    delete_temp_screenshot,
    is_wos_installed,
    is_wos_running,
    launch_and_verify_game,
    press_android_back_button,
    scroll_screen,
    take_screenshot,
)
from wosutil.emulator.image_utils import (
    find_first_non_zero_digit_position,
    find_gray_template_center_on_screen,
    find_gray_template_on_screen,
    find_multiple_templates,
    find_template_center_on_screen,
    find_template_on_screen,
    find_text_center_on_screen,
    read_screen_time,
)
from wosutil.preferences import get_kill_beast_march_assignment
from wosutil.stop import ToolStopped, stop_signal
from wosutil.utils import get_roi, get_template_path, log_message, retry_operation


def is_game_on_city_screen(instance_index):
    """Checks if the main city screen icon is present.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on city screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "city_icon", "city")


def ensure_city_screen(instance_index):
    """Ensures the game is on the main city screen. If the game is not open, it launches it.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if city screen is reached, False otherwise.
    """
    multi_instance_manager = get_multi_instance_manager()
    # Step 1: Check if the game is running
    if not is_wos_running(instance_index):
        if not is_wos_installed(instance_index):
            log_message(f"Whiteout Survival is not installed on instance {instance_index}. Install it and try again.", "error")
            return False
        log_message(f"Game not running on instance {instance_index}. Attempting to launch...", "info")
        if not retry_operation(
            lambda: launch_and_verify_game(instance_index),
            max_attempts=3,
            delay=2.0,
        ):
            log_message(f"Failed to launch game after 3 attempts on instance {instance_index}. Restarting emulator...", "error")
            if multi_instance_manager:
                try:
                    multi_instance_manager.stop_instance(instance_index)
                    if stop_signal.wait(timeout=5):
                        raise ToolStopped()
                    multi_instance_manager.start_instance(instance_index)
                    if stop_signal.wait(timeout=30):
                        raise ToolStopped()
                    log_message(f"Emulator {instance_index} restarted. Attempting to launch game again...", "info")
                    if not launch_and_verify_game(instance_index):
                        log_message(f"Failed to launch game after emulator restart on instance {instance_index}.", "error")
                        return False
                except Exception as e:
                    log_message(f"Error restarting emulator {instance_index}: {e}", "error")
                    return False
            else:
                log_message(f"No multi_instance_manager provided, cannot restart emulator {instance_index}.", "error")
                return False
        else:
            log_message(f"Game successfully launched on instance {instance_index}.", "success")
    else:
        log_message(f"Game already running on instance {instance_index}.", "info")
    # Step 2: Check if on main city screen
    for attempt in range(1, MAIN_SCREEN_MAX_ATTEMPTS + 1):
        stop_signal.check()
        if is_game_on_city_screen(instance_index):
            log_message("Successfully on main screen ('city')!", level="success")
            return True
        if is_game_on_world_screen(instance_index):
            log_message("Game is on world screen.", level="info")
            go_cityworld(instance_index)
        else:
            log_message(f"Not on main screen. Pressing back (Attempt {attempt}/{MAIN_SCREEN_MAX_ATTEMPTS}).", level="info")
            press_android_back_button(instance_index)

    # If we reach here, we failed to get to the city screen. Restart the game once and retry.
    log_message("Could not reach city screen after all attempts. Restarting game and retrying...", level="warning")

    stop_signal.check()

    if not launch_and_verify_game(instance_index):
        log_message("Game restart failed. Cannot reach city screen.", level="error")
        return False

    # Retry once more after restart
    for attempt in range(1, MAIN_SCREEN_MAX_ATTEMPTS + 1):
        stop_signal.check()
        if is_game_on_city_screen(instance_index):
            log_message("Successfully on main screen ('city')!", level="success")
            return True
        if is_game_on_world_screen(instance_index):
            log_message("Game is on world screen.", level="info")
            go_cityworld(instance_index)
        else:
            log_message(f"Not on main screen. Pressing back (Retry {attempt}/{MAIN_SCREEN_MAX_ATTEMPTS}).", level="info")
            press_android_back_button(instance_index)

    log_message("Failed to reach city screen even after restarting the game.", level="error")
    return False


def is_game_on_world_screen(instance_index):
    """Checks if the game is on the world screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on world screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "world", "world")


def click_on_template(template_name, instance_index, roi=None, delay=CLICK_DELAY, gray=False, screenshot_path=None, threshold=SCREEN_CHECK_THRESHOLD):
    """Takes a screenshot and clicks the center of the given template if found.

    Generic helper that replaces the repeated "screenshot -> find template ->
    click its center" pattern.

    Args:
        template_name (str): Template name in TEMPLATE_PATHS.
        instance_index (int): Emulator instance index.
        roi (tuple, optional): Region of interest (x, y, w, h).
        delay (float): Delay after the click.
        gray (bool): Use gray-scale matching when True.
        screenshot_path (str, optional): Reuse an already taken screenshot
            instead of capturing a new one. Only valid when the caller can
            guarantee no screen change has happened since the capture (that is,
            no click between captures).
        threshold (float): Minimum confidence threshold for a match.

    Returns:
        bool: True if the template was found and clicked, False otherwise.
    """
    owned_screenshot = screenshot_path is None
    if screenshot_path is None:
        screenshot_path = take_screenshot(instance_index)
    template_path = get_template_path(template_name)
    if not screenshot_path or not template_path:
        log_message(f"Could not get screenshot or template for '{template_name}'.", level="error")
        return False

    try:
        if gray:
            found, center = find_gray_template_center_on_screen(template_path, screenshot_path, threshold=threshold, roi=roi)
        else:
            found, center = find_template_center_on_screen(template_path, screenshot_path, threshold=threshold, roi=roi)
        if not found or not center:
            return False

        cx, cy = center
        click_on_coordinates(cx, cy, instance_index, delay=delay)
        log_message(f"Template '{template_name}' found, clicking at ({cx}, {cy}).", level="success")
        return True
    finally:
        if owned_screenshot:
            delete_temp_screenshot(screenshot_path)


def click_first_found_template(instance_index, templates, roi=None, delay=CLICK_DELAY, screenshot_path=None):
    """Tries to find and click the first template in the list that matches.

    Takes a single screenshot and searches every template against it, clicking
    the first match. This avoids capturing one screenshot per template when the
    screen does not change between checks.

    Each entry can be a template name or a (template_name, gray) tuple when
    the template needs gray-scale matching.

    Args:
        instance_index (int): Emulator instance index.
        templates (list): Template names or (template_name, gray) tuples.
        roi (tuple, optional): Region of interest (x, y, w, h).
        delay (float): Delay after the click.
        screenshot_path (str, optional): Reuse an already taken screenshot
            instead of capturing a new one. Only valid when the caller can
            guarantee no screen change has happened since the capture (that is,
            no click between captures).

    Returns:
        str or None: The name of the clicked template, or None if none was found.
    """
    owned_screenshot = screenshot_path is None
    if screenshot_path is None:
        screenshot_path = take_screenshot(instance_index)
    if not screenshot_path:
        log_message("Could not take a screenshot to search the templates.", level="error")
        return None
    try:
        for entry in templates:
            if isinstance(entry, tuple):
                template_name, gray = entry
            else:
                template_name, gray = entry, False
            if click_on_template(template_name, instance_index, roi=roi, delay=delay, gray=gray, screenshot_path=screenshot_path):
                return template_name
        return None
    finally:
        if owned_screenshot:
            delete_temp_screenshot(screenshot_path)


def click_on_text(text, instance_index, roi=None, delay=CLICK_DELAY, screenshot_path=None, last=False):
    """Takes a screenshot and clicks the center of the given text if found.

    Text-based counterpart of :func:`click_on_template` for menus whose
    entries kept their labels but moved around, e.g. the side menu.

    Args:
        text (str): Text to search for and click, e.g. 'Tundra Trek'.
        instance_index (int): Emulator instance index.
        roi (tuple, optional): Region of interest (x, y, w, h).
        delay (float): Delay after the click.
        screenshot_path (str, optional): Reuse an already taken screenshot
            instead of capturing a new one. Only valid when the caller can
            guarantee no screen change has happened since the capture (that is,
            no click between captures).
        last (bool): When True click the lowest occurrence of the text instead
            of the first one.

    Returns:
        bool: True if the text was found and clicked, False otherwise.
    """
    owned_screenshot = screenshot_path is None
    if screenshot_path is None:
        screenshot_path = take_screenshot(instance_index)
    if not screenshot_path:
        log_message(f"Could not get a screenshot to click on '{text}'.", level="error")
        return False

    try:
        found, center = find_text_center_on_screen(screenshot_path, text, roi=roi, instance_index=instance_index, debug_label=f"click_text_{text}", last=last)
        if not found or not center:
            return False

        cx, cy = center
        click_on_coordinates(cx, cy, instance_index, delay=delay)
        log_message(f"Text '{text}' found, clicking at ({cx}, {cy}).", level="success")
        return True
    finally:
        if owned_screenshot:
            delete_temp_screenshot(screenshot_path)


def is_game_on_screen(instance_index, template_name, roi_name=None, screenshot_path=None, threshold=SCREEN_CHECK_THRESHOLD):
    """Checks if the game is on the screen identified by a template and ROI.

    Args:
        instance_index (int): Emulator instance index.
        template_name (str): Template name in TEMPLATE_PATHS.
        roi_name (str, optional): ROI name in the ROI dict. When omitted the
            template is searched on the full screen.
        screenshot_path (str, optional): Reuse an already taken screenshot
            instead of capturing a new one. Only valid when the caller can
            guarantee no screen change has happened since the capture (that is,
            no click, tap, or navigation between captures).
        threshold (float): Minimum confidence threshold for a match.

    Returns:
        bool: True if on the screen, False otherwise.
    """
    log_message(f"Checking if on '{template_name}' screen...", level="info")
    owned_screenshot = screenshot_path is None
    captured_screenshot_path = take_screenshot(instance_index) if screenshot_path is None else screenshot_path
    if not captured_screenshot_path:
        return False

    template_path = get_template_path(template_name)
    if not template_path:
        if owned_screenshot:
            delete_temp_screenshot(captured_screenshot_path)
        return False

    roi = get_roi(roi_name) if roi_name else None

    try:
        found, _ = find_template_on_screen(template_path, captured_screenshot_path, threshold=threshold, roi=roi)
        return found
    finally:
        if owned_screenshot:
            delete_temp_screenshot(captured_screenshot_path)


def ensure_screen_with_back(instance_index, is_on_screen_fn, max_attempts=3, back_delay=1.0):
    """Ensures the game is on a screen, pressing the Android back button to close overlays.

    Generic version of the "press back until detected" pattern used by the
    pet adventure and pet skill screens.

    Args:
        instance_index (int): Emulator instance index.
        is_on_screen_fn (callable): Function that checks if the screen is detected.
        max_attempts (int): Maximum back button presses.
        back_delay (float): Delay after each back press.

    Returns:
        bool: True if on the screen, False otherwise.
    """
    for _ in range(max_attempts):
        stop_signal.check()
        if is_on_screen_fn(instance_index):
            return True
        # The screen template is not detected while an overlay is open, so
        # close it before trying again.
        press_android_back_button(instance_index, delay=back_delay)
    return is_on_screen_fn(instance_index)


def ensure_screen_with_navigation(instance_index, is_on_screen_fn, navigate_fn, max_attempts=3, retry_delay=0.8):
    """Ensures the game is on a screen, navigating until it is confirmed.

    Generic version of the "navigate until detected" pattern used by the
    hero recruit and intel screens.

    Args:
        instance_index (int): Emulator instance index.
        is_on_screen_fn (callable): Function that checks if the screen is detected.
        navigate_fn (callable): Function that navigates to the screen.
        max_attempts (int): Maximum navigation attempts.
        retry_delay (float): Seconds to wait between navigation attempts.

    Returns:
        bool: True if the screen was reached, False otherwise.
    """
    for attempt in range(1, max_attempts + 1):
        stop_signal.check()
        if is_on_screen_fn(instance_index):
            return True
        log_message(f"Not on the screen, navigating (Attempt {attempt}/{max_attempts}).", level="info")
        navigate_fn(instance_index)
        time.sleep(retry_delay)
    return False


def ensure_world_screen(instance_index):
    """Ensures the game is on the world screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on world screen, False otherwise.
    """
    if is_game_on_world_screen(instance_index):
        log_message("Already on world screen.", level="info")
        return True
    if not ensure_city_screen(instance_index):
        log_message("Failed to reach city screen before going to world screen.", level="error")
        return False
    go_cityworld(instance_index)
    if is_game_on_world_screen(instance_index):
        log_message("Successfully reached world screen.", level="success")
        return True
    log_message("Failed to reach world screen after navigation.", level="error")
    return False


def go_alliance_tab(instance_index):
    """Navigates to the alliance tab, ensuring the city screen first.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the alliance tab was reached, False otherwise.
    """
    if not ensure_city_screen(instance_index):
        return False
    click_on("alliance", instance_index, delay=1.5)
    return True


def go_sidemenu(instance_index):
    """Navigates to the side menu, ensuring the city screen first.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the side menu was opened, False otherwise.
    """
    if not ensure_city_screen(instance_index):
        return False
    click_on("sidemenu", instance_index, delay=0.6)
    return True


def go_sidemenu_city(instance_index):
    """Opens the side menu and selects the City tab.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the side menu opened and the City tab was clicked, False otherwise.
    """
    if not go_sidemenu(instance_index):
        return False
    if not click_on_text("City", instance_index, roi=get_roi("sidemenu"), delay=1.0):
        log_message("City tab NOT found in side menu. Aborting.", level="warning")
        return False
    return True


def go_sidemenu_daily(instance_index):
    """Opens the side menu and selects the Daily tab.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the side menu opened and the Daily tab was clicked, False otherwise.
    """
    if not go_sidemenu(instance_index):
        return False
    if not click_on_text("Daily", instance_index, roi=get_roi("sidemenu"), delay=1.0):
        log_message("Daily tab NOT found in side menu. Aborting.", level="warning")
        return False
    return True


def go_exploration_tab(instance_index):
    """Navigates to the exploration tab, ensuring the city screen first.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the exploration tab was opened, False otherwise.
    """
    if not ensure_city_screen(instance_index):
        return False
    click_on("exploration", instance_index)
    return True


def go_cityworld(instance_index):
    """Navigates to the city world by clicking on the world button and waiting for it to open.

    Args:
        instance_index (int): Emulator instance index.
    """
    click_on("world", instance_index, delay=2.0)


def go_tundra_trek(instance_index):
    """Navigates to the tundra trek screen by opening the side menu on the Daily tab and clicking the tundra trek entry by text.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the tundra trek entry was found and clicked, False otherwise.
    """
    if not go_sidemenu_daily(instance_index):
        return False

    if not click_on_text("Tundra Trek", instance_index, roi=get_roi("sidemenu"), delay=1.0):
        log_message("Tundra trek entry NOT found in side menu. Aborting.", level="warning")
        return False
    return True


def go_pet_adventure(instance_index):
    """Navigates to the pet adventure screen by opening the side menu on the Daily tab and clicking the lowest Pet Adventure entry by text.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the Pet Adventure entry was found and clicked, False otherwise.
    """
    if not go_sidemenu_daily(instance_index):
        return False

    if not click_on_text("Pet Adventure", instance_index, roi=get_roi("sidemenu"), delay=1.0, last=True):
        log_message("Pet Adventure entry NOT found in side menu. Aborting.", level="warning")
        return False
    return True


def go_hero_recruit_screen(instance_index):
    """Navigates to the hero recruit screen by clicking on the heroes button and the recruit tab.

    If the game is already on the hero recruit screen, skips the navigation.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the hero recruit screen was reached, False otherwise.
    """
    if is_game_on_hero_recruit_screen(instance_index):
        log_message("Already on the hero recruit screen.", level="info")
        return True
    if not ensure_city_screen(instance_index):
        return False
    click_on("heroes", instance_index)
    click_on_coordinates(535, 1215, instance_index, delay=0.7)
    return True


def is_game_on_hero_recruit_screen(instance_index):
    """Checks if the game is on the hero recruit screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on the hero recruit screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "hero_recruit_screen", "hero_recruit_screen")


def ensure_hero_recruit_screen(instance_index, max_attempts=3):
    """Ensures the game is on the hero recruit screen, navigating until it is confirmed.

    Args:
        instance_index (int): Emulator instance index.
        max_attempts (int): Maximum navigation attempts.

    Returns:
        bool: True if the hero recruit screen was reached, False otherwise.
    """
    return ensure_screen_with_navigation(
        instance_index,
        is_game_on_hero_recruit_screen,
        go_hero_recruit_screen,
        max_attempts=max_attempts,
    )


# --- Pet adventure chests ---
PET_ADVENTURE_CHEST_THRESHOLD = 0.9  # Minimum confidence for chest templates
PET_ADVENTURE_CHEST_DETECTION_DELAY = 1.5  # Seconds between the two detection screenshots
PET_ADVENTURE_CHEST_MAX_STARTS = 5  # Max start attempts per run (4 chests per day + guard)
PET_ADVENTURE_CHEST_SELECT_RETRY_SECONDS = 1.0  # Wait before retrying the select pet search
PET_ADVENTURE_CHEST_RETRY_SECONDS = 2.0  # Wait before re-detecting after a failed detection
# Main templates: identify the chest type and state (available to start / ready to open)
PET_ADVENTURE_CHEST_TEMPLATES = [
    ("pet_adventure_chest1", 1, "start"),
    ("pet_adventure_chest1_ready", 1, "ready"),
    ("pet_adventure_chest2", 2, "start"),
    ("pet_adventure_chest2_ready", 2, "ready"),
    ("pet_adventure_chest3", 3, "start"),
    ("pet_adventure_chest3_ready", 3, "ready"),
]
# "b" templates match any chest (filling, ready to start or ready to open);
# they are only used as a fallback to reach 3 detected chests.
PET_ADVENTURE_CHEST_FILLING_TEMPLATES = [
    ("pet_adventure_chest1b", 1),
    ("pet_adventure_chest2b", 2),
    ("pet_adventure_chest3b", 3),
]


def is_game_on_pet_adventure_screen(instance_index):
    """Checks if the game is on the pet adventure screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on the pet adventure screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "pet_adventure_screen", "pet_adventure_screen")


def ensure_pet_adventure_screen(instance_index, max_attempts=2):
    """Ensures the game is on the pet adventure screen before acting on chests.

    If a previous panel (e.g. the start button overlay) is still open the
    screen template is not detected, so the Android back button is pressed to
    close it before trying again.

    Args:
        instance_index (int): Emulator instance index.
        max_attempts (int): Maximum back button presses.

    Returns:
        bool: True if on the pet adventure screen, False otherwise.
    """
    return ensure_screen_with_back(
        instance_index,
        is_game_on_pet_adventure_screen,
        max_attempts=max_attempts,
    )


def merge_pet_adventure_chest_matches(chests, positions, chest_type, state):
    """Merges template matches into a chest detection accumulator.

    Chests are keyed by their rounded position so the same chest found by
    several templates, or in the two screenshots, is only counted once.
    For the same position a "ready" state always wins over "start"/"filling".

    Args:
        chests (dict): Accumulator mapping position key -> chest dict.
        positions (list): List of (x, y, w, h) template matches.
        chest_type (int): Chest type (1, 2 or 3).
        state (str): Chest state ("start", "ready" or "filling").

    Returns:
        dict: The updated chest accumulator.
    """
    for x, y, w, h in positions:
        key = (x // 10, y // 10)
        chest = chests.get(key)
        if chest is None:
            chests[key] = {"x": x, "y": y, "w": w, "h": h, "type": chest_type, "state": state}
        elif state == "ready" and chest["state"] != "ready":
            chest["state"] = "ready"
    return chests


def detect_pet_adventure_chests(instance_index):
    """Detects the three pet adventure chests currently on screen.

    Takes two screenshots 1.5 seconds apart (chests vibrate a little) and
    matches every chest template with a 0.9 confidence threshold in both. The
    six main templates (chestN / chestN_ready) identify the chest type and
    state; the "b" templates are only used to fill in missing positions and
    identify them as filling chests (already activated, timer running).

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        list or None: List of chest dicts ({x, y, w, h, type, state}) or None
            if a screenshot could not be taken.
    """
    first_shot = take_screenshot(instance_index)
    time.sleep(PET_ADVENTURE_CHEST_DETECTION_DELAY)
    second_shot = take_screenshot(instance_index)
    if not first_shot or not second_shot:
        log_message("Could not take screenshots to detect pet adventure chests.", level="error")
        delete_temp_screenshot(first_shot)
        delete_temp_screenshot(second_shot)
        return None

    try:
        chests = {}
        for screenshot_path in (first_shot, second_shot):
            for template_name, chest_type, state in PET_ADVENTURE_CHEST_TEMPLATES:
                template_path = get_template_path(template_name)
                if not template_path:
                    continue
                positions = find_multiple_templates(template_path, screenshot_path, threshold=PET_ADVENTURE_CHEST_THRESHOLD)
                merge_pet_adventure_chest_matches(chests, positions, chest_type, state)

        if len(chests) < 3:
            for screenshot_path in (first_shot, second_shot):
                for template_name, chest_type in PET_ADVENTURE_CHEST_FILLING_TEMPLATES:
                    template_path = get_template_path(template_name)
                    if not template_path:
                        continue
                    positions = find_multiple_templates(template_path, screenshot_path, threshold=PET_ADVENTURE_CHEST_THRESHOLD)
                    merge_pet_adventure_chest_matches(chests, positions, chest_type, "filling")

        log_message(f"Detected {len(chests)} pet adventure chests.", level="info")
        return list(chests.values())
    finally:
        delete_temp_screenshot(first_shot)
        delete_temp_screenshot(second_shot)


def start_pet_adventure_chest(instance_index, x, y):
    """Starts a single pet adventure chest by clicking it and confirming with the select pet and start buttons.

    Args:
        instance_index (int): Emulator instance index.
        x (int): Chest center X coordinate.
        y (int): Chest center Y coordinate.

    Returns:
        bool or str: True if the chest was started, "no_attempts" if the start
            button is missing (daily attempts exhausted), "already_active" if
            the chest showed no select pet panel (it was already active), or
            False on unexpected failure.
    """
    log_message(f"Starting pet adventure chest at ({x}, {y})...", level="info")
    if not ensure_pet_adventure_screen(instance_index):
        log_message("Not on the pet adventure screen, cannot start the chest.", level="warning")
        return False

    click_on_coordinates(x, y, instance_index, delay=1.0)

    if not click_on_template("pet_adventure_select_pet_button", instance_index, delay=1.0):
        # The panel may still be animating in, retry once before giving up.
        time.sleep(PET_ADVENTURE_CHEST_SELECT_RETRY_SECONDS)
        if not click_on_template("pet_adventure_select_pet_button", instance_index, delay=1.0):
            log_message("Select pet button NOT found, the chest is probably already active. Returning to pet adventure screen.", level="warning")
            press_android_back_button(instance_index, delay=1.0)
            press_android_back_button(instance_index, delay=1.0)
            return "already_active"

    if not click_on_template("pet_adventure_start_button", instance_index, delay=1.5):
        log_message("Start button NOT found, daily chest attempts are probably exhausted.", level="warning")
        press_android_back_button(instance_index, delay=1.0)
        press_android_back_button(instance_index, delay=1.0)
        return "no_attempts"

    # The start button opens a confirmation panel; press back to return to the
    # pet adventure screen so the next chest can be started.
    press_android_back_button(instance_index, delay=1.0)
    if not ensure_pet_adventure_screen(instance_index):
        log_message("Could not return to the pet adventure screen after starting the chest.", level="warning")
        return False
    return True


def start_pet_adventure_chests(instance_index):
    """Starts every available pet adventure chest, one at a time.

    The screen is re-detected between each chest so fresh coordinates are used
    and the start animation of a previous chest cannot swallow the click on the
    next one (which caused starts to fail when reusing stale positions). Chest 3
    is always started before type 2, and type 2 before type 1; chests that are
    already active are skipped instead of failing the task.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        str: "done" if the starts finished cleanly, "no_attempts" if the start
            button was missing (all 4 daily attempts used), or "failed" if the
            3 chests could not be detected to keep going.
    """
    attempted_positions = set()
    for _ in range(PET_ADVENTURE_CHEST_MAX_STARTS):
        stop_signal.check()

        chests = detect_pet_adventure_chests(instance_index)
        if not chests or len(chests) < 3:
            time.sleep(PET_ADVENTURE_CHEST_RETRY_SECONDS)
            chests = detect_pet_adventure_chests(instance_index)
            if not chests or len(chests) < 3:
                log_message("Could not detect the 3 pet adventure chests while starting them.", level="warning")
                return "failed"

        candidates = sorted(
            (c for c in chests if c["state"] == "start"),
            key=lambda c: (c["type"] != 3, -c["type"]),  # chest 3 first, then type 2, then type 1
        )
        chest = next((c for c in candidates if (c["x"] // 10, c["y"] // 10) not in attempted_positions), None)
        if chest is None:
            return "done"
        attempted_positions.add((chest["x"] // 10, chest["y"] // 10))

        result = start_pet_adventure_chest(
            instance_index,
            chest["x"] + chest["w"] // 2,
            chest["y"] + chest["h"] // 2,
        )
        if result == "no_attempts":
            return "no_attempts"
        if result == "already_active":
            log_message("Skipping a pet adventure chest that is already active.", level="info")
            continue
        if result is not True:
            return "failed"

    return "done"


def go_pet_skill(instance_index):
    """Navigates to the pet skill screen, ensuring the city screen first, by clicking the pet skill button.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the pet skill button was found and clicked, False otherwise.
    """
    if not ensure_city_screen(instance_index):
        return False
    if not click_on_template("pet_skill_button", instance_index, roi=get_roi("bottom_right_side_icons"), delay=1.0):
        log_message("Pet skill button NOT found. Aborting.", level="warning")
        return False
    return True


def is_game_on_pet_skill_screen(instance_index):
    """Checks if the game is on the pet skill screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on the pet skill screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "pet_skill_screen", "pet_skill_screen")


def ensure_pet_skill_screen(instance_index, max_attempts=3):
    """Ensures the game is on the pet skill screen before activating a skill or reading a timer.

    If the screen template is not detected, a popup or overlay is probably
    open, so the Android back button is pressed to close it before retrying.

    Args:
        instance_index (int): Emulator instance index.
        max_attempts (int): Maximum back button presses.

    Returns:
        bool: True if on the pet skill screen, False otherwise.
    """
    return ensure_screen_with_back(
        instance_index,
        is_game_on_pet_skill_screen,
        max_attempts=max_attempts,
    )


def open_pet_adventure_chest(instance_index, x, y):
    """Opens a ready pet adventure chest.

    Args:
        instance_index (int): Emulator instance index.
        x (int): Chest center X coordinate.
        y (int): Chest center Y coordinate.

    Returns:
        bool: True if the chest was opened, False if not on the pet adventure screen.
    """
    log_message(f"Opening pet adventure chest at ({x}, {y})...", level="info")
    if not ensure_pet_adventure_screen(instance_index):
        log_message("Not on the pet adventure screen, cannot open the chest.", level="warning")
        return False
    click_on_coordinates(x, y, instance_index, delay=1.0)
    click_on_coordinates(371, 810, instance_index)
    time.sleep(2.0)
    press_android_back_button(instance_index, delay=1.0)
    press_android_back_button(instance_index, delay=1.0)
    return True


def end_tundra_trek_idle_if_active(instance_index):
    """Ends an active tundra trek idle hunt if the end button is on screen.

    Searches the full screen for the 'tundra_trek_idle_end_button' template.
    If found, clicks it and presses the Android back button.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the end button was found and clicked, False otherwise.
    """
    if click_on_template("tundra_trek_idle_end_button", instance_index, delay=0.7):
        press_android_back_button(instance_index)
        return True
    log_message("Tundra trek idle end button NOT found, continuing without ending idle.", level="info")
    return False


def go_shop(instance_index):
    """Navigates to the shop, ensuring the city screen first, by clicking on the shop icon.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the shop was opened, False otherwise.
    """
    if not ensure_city_screen(instance_index):
        return False
    click_on("shop", instance_index)
    return True


def go_intel(instance_index):
    """Navigates to the intel through the world screen, and uses Agnes skill.

    If the game is already on the intel screen, skips the navigation.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the intel screen was reached, False otherwise.
    """
    if is_game_on_intel_screen(instance_index):
        log_message("Already on intel screen.", level="info")
        return True
    if not ensure_world_screen(instance_index):
        return False
    click_on_template("intel_button", instance_index, roi=get_roi("bottom_right_side_icons"), delay=0.8)
    click_on_coordinates(58, 210, instance_index)
    return True


def is_game_on_intel_screen(instance_index):
    """Checks if the game is on the intel screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on the intel screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "intel_screen", "intel_screen")


def ensure_intel_screen(instance_index, max_attempts=3):
    """Ensures the game is on the intel screen, navigating until it is confirmed.

    Args:
        instance_index (int): Emulator instance index.
        max_attempts (int): Maximum navigation attempts.

    Returns:
        bool: True if the intel screen was reached, False otherwise.
    """
    return ensure_screen_with_navigation(
        instance_index,
        is_game_on_intel_screen,
        go_intel,
        max_attempts=max_attempts,
    )


KILL_BEAST_MARCH_POSITIONS = {
    1: (63, 122),
    2: (137, 122),
    3: (211, 122),
    4: (286, 122),
    5: (361, 122),
    6: (435, 122),
    7: (511, 122),
    8: (584, 122),
    9: (337, 122),
    10: (411, 122),
    11: (486, 122),
    12: (560, 122),
}
KILL_BEAST_MARCH_SCROLL_START = (511, 122)
KILL_BEAST_MARCH_SCROLL_END = (63, 122)


def kill_beast(instance_index):
    """Kills a beast with the default march if the beast is already clicked and centered on the screen.

    If the user assigned a march in the preferences, that formation is selected
    on the march row (scrolling horizontally first when needed) before attacking.

    When the march timer cannot be read, the send-march screen template is
    searched to confirm the flow is on the correct screen:

    - If the send-march screen is found, the march is sent and a short wait is
      returned since the timer is unknown.
    - If the send-march screen is NOT found, the flow is not on the correct
      screen: ``None`` is returned so the caller can go back to the intel and
      retry the attack.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        int: Time in seconds to wait (detected timer * 2, capped at INTEL_BEAST_MAX_WAIT_SECONDS,
            or INTEL_BEAST_MARCH_SENT_WAIT_SECONDS when the march was sent without a readable timer).
        False: If the attack was skipped because no troops are available to send.
        None: If the send-march screen could not be confirmed (not on the correct screen).
    """
    click_on_coordinates(360, 620, instance_index)

    march = get_kill_beast_march_assignment()
    if march is not None:
        log_message(f"Selecting march {march} for killing the beast...", level="info")
        if march > 8:
            scroll_screen(
                KILL_BEAST_MARCH_SCROLL_START[0],
                KILL_BEAST_MARCH_SCROLL_START[1],
                KILL_BEAST_MARCH_SCROLL_END[0],
                KILL_BEAST_MARCH_SCROLL_END[1],
                200,
                instance_index,
            )
        click_on_coordinates(*KILL_BEAST_MARCH_POSITIONS[march], instance_index, delay=0.3)

    timer = read_screen_time(
        instance_index,
        roi=ROI["kill_beast_timer"],
        debug_label="kill_beast_timer",
        max_seconds=INTEL_BEAST_TIMER_MAX_SECONDS,
    )
    if timer is None and is_game_on_screen(instance_index, "no_troops_left"):
        log_message("No troops left to send to the beast, skipping the attack.", level="warning")
        return False

    if timer is None:
        # The timer could not be read: verify the flow is really on the
        # send-march screen before assuming the march was sent.
        if is_game_on_screen(instance_index, "send_march_screen", "send_march_screen", threshold=0.90):
            log_message(
                "Timer unreadable but the send-march screen is confirmed, sending the march.",
                level="warning",
            )
            click_on_coordinates(552, 1216, instance_index)
            return INTEL_BEAST_MARCH_SENT_WAIT_SECONDS
        log_message(
            "Send-march screen NOT found, not on the correct screen; the beast attack will be retried.",
            level="warning",
        )
        return None

    click_on_coordinates(552, 1216, instance_index)
    return min(timer * 2, INTEL_BEAST_MAX_WAIT_SECONDS)


def _click_intel_template(instance_index, templates):
    """After navigating to the intel screen, claims the rewards and clicks the first matching template.

    Takes a single screenshot and reuses it across the intel screen check, the
    'intel_claim_all' search and the template search. A fresh screenshot is only
    captured right after an action that redraws the screen (navigating to the
    intel screen or pressing the Android back button after claiming).

    Args:
        instance_index (int): Emulator instance index.
        templates (list): Template names or (template_name, gray) tuples.

    Returns:
        str or None: The name of the clicked template, or None if none was found.
    """
    roi = get_roi("intel")
    if not roi:
        log_message("Could not get the ROI for 'intel'", level="error")
        return None

    screenshot_path = take_screenshot(instance_index)
    if not screenshot_path:
        log_message("Could not take a screenshot for the intel template search.", level="error")
        return None

    try:
        if not is_game_on_screen(instance_index, "intel_screen", "intel_screen", screenshot_path=screenshot_path):
            if not ensure_intel_screen(instance_index):
                log_message("Could not reach the intel screen, skipping the collection.", level="warning")
                return None
            delete_temp_screenshot(screenshot_path)
            screenshot_path = take_screenshot(instance_index)
            if not screenshot_path:
                return None

        claim_roi = get_roi("intel_claim_all")
        if claim_roi and click_on_template("intel_claim_all", instance_index, roi=claim_roi, screenshot_path=screenshot_path):
            press_android_back_button(instance_index)
            delete_temp_screenshot(screenshot_path)
            screenshot_path = take_screenshot(instance_index)
            if not screenshot_path:
                return None
        else:
            log_message("'intel_claim_all' not found on the screen.", level="info")

        return click_first_found_template(instance_index, templates, roi=roi, screenshot_path=screenshot_path)
    finally:
        delete_temp_screenshot(screenshot_path)


def kill_intel_beast(instance_index):
    """Kills the intel beast.

    Goes to the intel screen, searches for the 'intel_beast' template.
    If not found, searches for 'intel_fcbeast' in the same ROI.
    Clicks the center of the found template, then clicks (360, 935),
    then calls kill_beast and returns its result in seconds.
    Returns to the intel screen before returning.

    When kill_beast cannot confirm the send-march screen (the flow is not on
    the correct screen), the attempt is retried from the intel screen up to
    INTEL_BEAST_MAX_RETRIES times.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        int: The value in seconds returned by kill_beast, False when no troops
            are available to send, or None if no beast is found or the attack
            could not be confirmed after the retry limit.
    """
    for attempt in range(1, INTEL_BEAST_MAX_RETRIES + 1):
        clicked = _click_intel_template(
            instance_index,
            [("intel_beast", True), ("intel_fcbeast", True), ("intel_firehunter", False)],
        )
        if not clicked:
            return None  # No beast found
        click_on_coordinates(360, 908, instance_index)
        result = kill_beast(instance_index)
        if result is None:
            log_message(
                f"Send-march screen not confirmed on attempt {attempt}/{INTEL_BEAST_MAX_RETRIES}, retrying from the intel screen.",
                level="warning",
            )
            press_android_back_button(instance_index)
            continue
        click_on_template("intel_button", instance_index, roi=get_roi("bottom_right_side_icons"), delay=0.8)
        return result
    log_message("The beast attack could not be confirmed after several attempts, skipping for now.", level="warning")
    return None


def rescue_intel_survivor(instance_index):
    """Rescues intel survivors.

    Navigates to the intel screen, searches for the 'intel_survivor' template.
    If not found, searches for 'intel_fcsurvivor' in the same ROI.
    Clicks the center of the found template, then clicks (360, 908)(view),
    and finally on rescue(360, 620). Returns to the intel screen before returning.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if a survivor was rescued, False if none was found.
    """
    clicked = _click_intel_template(
        instance_index,
        [("intel_survivor", True), ("intel_fcsurvivor", True)],
    )
    if not clicked:
        return False  # No survivor found
    click_on_coordinates(360, 908, instance_index)
    click_on_coordinates(360, 620, instance_index)
    click_on_template("intel_button", instance_index, roi=get_roi("bottom_right_side_icons"), delay=0.8)
    return True


def do_intel_exploration(instance_index):
    """Completes an exploration mission from the intel tab if available.

    1. Navigates to the intel screen and claims all completed missions.
    2. Searches for 'intel_exploration' and 'intel_fcexploration' in gray mode.
    3. If found, clicks the center and follows the sequence.
    4. Searches for 'exploration_victory' for up to 30 seconds.
    5. If found or after 30 seconds, presses the Android back button and
       returns to the intel screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if an exploration was completed, False otherwise.
    """
    clicked = _click_intel_template(
        instance_index,
        [("intel_exploration", True), ("intel_fcexploration", True)],
    )
    if not clicked:
        return False
    click_on_coordinates(360, 908, instance_index)
    click_on_coordinates(360, 620, instance_index)
    click_on_coordinates(200, 1200, instance_index)
    click_on_coordinates(525, 1200, instance_index)
    # Wait up to 30s for exploration_victory, checking first at 4s
    # and then every 5s to detect the victory earlier.
    victory_template = get_template_path("exploration_victory")
    victory_roi = (230, 425, 256, 79)
    start = time.time()
    first_retry = True
    while time.time() - start < 30:
        stop_signal.check()
        screenshot_path = take_screenshot(instance_index)
        if screenshot_path and victory_template:
            found, _ = find_gray_template_on_screen(victory_template, screenshot_path, roi=victory_roi)
            delete_temp_screenshot(screenshot_path)
            if found:
                break
        elif screenshot_path:
            delete_temp_screenshot(screenshot_path)
        time.sleep(4 if first_retry else 5)
        first_retry = False
    press_android_back_button(instance_index)
    click_on_template("intel_button", instance_index, roi=get_roi("bottom_right_side_icons"), delay=0.8)
    return True


def _train_troop_camp(instance_index):
    """Promotes or trains the troops of the currently open troop camp.

    1. Taps the camp header 4 times (0.5s delay) to open the training queue.
    2. Up to 3 times:
       a. If the speed-up template is on screen, troops are being trained:
          the remaining timer is returned.
       b. Otherwise the camp is idle/completed: clicks 84px above the first
          non-zero troop count digit, searches the promote button and trains.
          On the next check the timer should be found.
    3. When the timer could not be read after all attempts, None is returned
       so the caller can fall back to its own default reschedule time.

    Returns:
        int or None: Remaining training time in seconds, or None if not detected.
    """
    for _ in range(4):
        click_on_coordinates(360, 40, instance_index, delay=0.5)

    for _ in range(3):
        stop_signal.check()

        if is_game_on_screen(instance_index, "troop_train_speed_up", "troop_train_speed_up"):
            seconds = read_screen_time(instance_index, roi=get_roi("troop_train_timer"), debug_label="troop_train_timer")
            return seconds

        # No troops being trained: try to promote the existing ones first.
        digit_position = find_first_non_zero_digit_position(instance_index, roi=get_roi("troop_promote_text"))
        if digit_position is not None:
            cx, cy = digit_position
            click_on_coordinates(cx, cy - 84, instance_index, delay=1.0)
            log_message(f"Clicked at ({cx}, {cy - 84}) above the first non-zero troop digit.", level="success")
        else:
            log_message("No non-zero troop digit found to promote from, skipping that step.", level="info")

        if click_on_template("train_troop_promote", instance_index, roi=get_roi("train_troop_promote"), delay=1.0):
            click_on_coordinates(521, 904, instance_index, delay=1.0)
        else:
            click_on_coordinates(531, 1119, instance_index, delay=1.0)

    log_message("Could not read a troop training timer after all attempts.", level="warning")
    return None
