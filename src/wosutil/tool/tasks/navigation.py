"""Game navigation and screen interaction helpers.

Screen detection (is_game_on_*), navigation (go_*, ensure_*, launch_*) and
the generic click/OCR interaction primitives they are built on.
"""

import time

# Import configuration and utility functions
from wosutil.config import (
    CLICK_DELAY,
    GAME_BOOT_GRACE_SECONDS,
    GAME_PROCESS_RECHECK_SECONDS,
    MAIN_SCREEN_MAX_ATTEMPTS,
    SCREEN_CHECK_THRESHOLD,
)
from wosutil.context import get_multi_instance_manager
from wosutil.emulator.emulator_manager import (
    click_on,
    click_on_coordinates,
    delete_temp_screenshot,
    force_stop_game,
    is_wos_installed,
    is_wos_running,
    launch_game_activity,
    press_android_back_button,
    scroll_screen,
    take_screenshot,
)
from wosutil.emulator.image_utils import (
    find_multiple_templates,
    find_template_center_on_screen,
    find_text_center_on_screen,
)
from wosutil.stop import ToolStopped, stop_signal
from wosutil.utils import get_roi, get_template_path, log_message, retry_operation

WORLD_MAP_SEARCH_SCROLL_START = (636, 912)

WORLD_MAP_SEARCH_SCROLL_END = (86, 912)

WORLD_MAP_SEARCH_SCROLL_DURATION_MS = 200


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
    log_message(f"Checking if on '{template_name}' screen on instance {instance_index}...", level="info")
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
        found, _ = find_template_center_on_screen(template_path, captured_screenshot_path, threshold=threshold, roi=roi)
        return found
    finally:
        if owned_screenshot:
            delete_temp_screenshot(captured_screenshot_path)


def is_game_on_city_screen(instance_index):
    """Checks if the main city screen icon is present.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on city screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "city_icon", "city")


def is_game_on_hero_recruit_screen(instance_index):
    """Checks if the game is on the hero recruit screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on the hero recruit screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "hero_recruit_screen", "hero_recruit_screen")


def is_game_on_intel_screen(instance_index):
    """Checks if the game is on the intel screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on the intel screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "intel_screen", "intel_screen")


def is_game_on_pet_adventure_screen(instance_index):
    """Checks if the game is on the pet adventure screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on the pet adventure screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "pet_adventure_screen", "pet_adventure_screen")


def is_game_on_pet_skill_screen(instance_index):
    """Checks if the game is on the pet skill screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on the pet skill screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "pet_skill_screen", "pet_skill_screen")


def is_game_on_world_screen(instance_index):
    """Checks if the game is on the world screen.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if on world screen, False otherwise.
    """
    return is_game_on_screen(instance_index, "world", "world")


def go_alliance_tab(instance_index):
    """Navigates to the alliance tab from the city or world screen.

    The alliance button is reachable with the same click from both the
    city and the world screen, so only when on neither screen it ensures
    the city screen first.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the alliance tab was reached, False otherwise.
    """
    if not is_game_on_city_screen(instance_index) and not is_game_on_world_screen(instance_index) and not ensure_city_screen(instance_index):
        return False
    click_on("alliance", instance_index, delay=1.5)
    return True


def go_cityworld(instance_index):
    """Navigates to the city world by clicking on the world button and waiting for it to open.

    Args:
        instance_index (int): Emulator instance index.
    """
    click_on("world", instance_index, delay=2.0)


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
    click_on_template("intel_button", instance_index, roi="bottom_right_side_icons", delay=0.8)
    click_on_coordinates(58, 210, instance_index)
    return True


def go_island(instance_index):
    """Navigates to the island screen by opening the side menu on the Daily tab and clicking the Tree entry by text.

    Scrolls the side menu to reveal the Tree entry, then dismisses the hand
    tutorial overlay once the island is open.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the island was reached, False otherwise.
    """
    if not go_sidemenu_daily(instance_index):
        return False

    scroll_screen(13, 500, 13, 0, 500, instance_index, hold_end_ms=500, delay=1.0)

    if not click_on_text("Tree", instance_index, roi="sidemenu", delay=4):
        log_message("Tree entry NOT found in side menu. Aborting.", level="warning")
        return False

    # Removing hand tutorial from screen
    click_on_coordinates(100, 70, instance_index)
    click_on_coordinates(100, 70, instance_index, delay=0.8)
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

    if not click_on_text("Pet Adventure", instance_index, roi="sidemenu", delay=1.0, last=True):
        log_message("Pet Adventure entry NOT found in side menu. Aborting.", level="warning")
        return False
    return True


def go_pet_skill(instance_index):
    """Navigates to the pet skill screen, ensuring the city screen first, by clicking the pet skill button.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the pet skill button was found and clicked, False otherwise.
    """
    if not ensure_city_screen(instance_index):
        return False
    if not click_on_template("pet_skill_button", instance_index, roi="bottom_right_side_icons", delay=1.0):
        log_message("Pet skill button NOT found. Aborting.", level="warning")
        return False
    return True


def go_rally_tab(instance_index):
    """Navigates to the alliance rally tab.

    Opens the alliance tab and then the rally tab with the two fixed
    clicks used e.g. by the autojoin flow.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the rally tab was reached, False otherwise.
    """
    if not go_alliance_tab(instance_index):
        return False
    click_on_coordinates(196, 665, instance_index)
    click_on_coordinates(130, 130, instance_index)
    return True


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
    if not click_on_text("City", instance_index, roi="sidemenu", delay=1.0):
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
    if not click_on_text("Daily", instance_index, roi="sidemenu", delay=1.0):
        log_message("Daily tab NOT found in side menu. Aborting.", level="warning")
        return False
    # Uncheck "Hide completed mission" if it is checked (no-op when unchecked).
    click_on_template("sidemenu_daily_hide_completed_mission", instance_index, roi="sidemenu")
    return True


def go_tundra_trek(instance_index):
    """Navigates to the tundra trek screen by opening the side menu on the Daily tab and clicking the tundra trek entry by text.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if the tundra trek entry was found and clicked, False otherwise.
    """
    if not go_sidemenu_daily(instance_index):
        return False

    if not click_on_text("Tundra Trek", instance_index, roi="sidemenu", delay=1.0):
        log_message("Tundra trek entry NOT found in side menu. Aborting.", level="warning")
        return False
    return True


def go_worldmap_search(instance_index, scroll=True):
    """Open the world-map resource search panel.

    Args:
        instance_index (int): Emulator instance index.
        scroll (bool): Whether to scroll the resource selector from right to
            left after opening it.

    Returns:
        bool: True when the world map was reached and the search was opened.
    """
    if not ensure_world_screen(instance_index):
        return False

    click_on_coordinates(44, 878, instance_index)
    if scroll:
        scroll_screen(
            WORLD_MAP_SEARCH_SCROLL_START[0],
            WORLD_MAP_SEARCH_SCROLL_START[1],
            WORLD_MAP_SEARCH_SCROLL_END[0],
            WORLD_MAP_SEARCH_SCROLL_END[1],
            WORLD_MAP_SEARCH_SCROLL_DURATION_MS,
            instance_index,
            delay=1.0,
        )
    return True


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
            lambda: launch_and_reach_city_screen(instance_index),
            max_attempts=3,
            delay=2.0,
            retry_on_false=True,
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
                    if not launch_and_reach_city_screen(instance_index):
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
        if _back_until_reach_city_screen(instance_index, "Attempt"):
            return True
        # The game is running but its screen is stuck: restart it once.
        log_message(f"Could not reach city screen after all attempts on instance {instance_index}. Restarting game and retrying...", level="warning")
        stop_signal.check()
        if not launch_and_reach_city_screen(instance_index):
            log_message(f"Game restart failed on instance {instance_index}. Cannot reach city screen.", level="error")
            return False
    return True


def _back_until_reach_city_screen(instance_index, attempt_label):
    """Press back / navigate until the main city screen is detected.

    Args:
        instance_index (int): Emulator instance index.
        attempt_label (str): Label for the attempt counter in the log, e.g.
            "Attempt" or "Retry".

    Returns:
        bool: True if the city screen was detected.
    """
    for attempt in range(1, MAIN_SCREEN_MAX_ATTEMPTS + 1):
        stop_signal.check()
        if is_game_on_city_screen(instance_index):
            log_message(f"Successfully on main screen ('city') on instance {instance_index}!", level="success")
            return True
        if is_game_on_world_screen(instance_index):
            log_message(f"Game is on world screen on instance {instance_index}.", level="info")
            go_cityworld(instance_index)
        else:
            log_message(f"Not on main screen on instance {instance_index}. Pressing back ({attempt_label} {attempt}/{MAIN_SCREEN_MAX_ATTEMPTS}).", level="info")
            press_android_back_button(instance_index)
    return False


def launch_and_reach_city_screen(instance_index):
    """Launch the game and wait until the main city screen is reached.

    After relaunching, the game process is polled (without screenshots or
    navigation) until it appears, so loading screens are never hammered with
    back presses while the game is still cold-booting. Each navigation check
    then re-verifies a missing process once before giving up, since a single
    empty pidof is often a transient ADB hiccup (e.g. right after an ADB
    server restart) rather than a dead game.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True when the city screen was reached, False otherwise.
    """
    log_message(f"Closing and relaunching the game on instance {instance_index}...", "info")

    # Close the game
    force_stop_game(instance_index)

    # Wait a bit before relaunching
    if stop_signal.wait(timeout=2):
        raise ToolStopped()

    # Relaunch the game
    launch_game_activity(instance_index)

    # Let the game cold-boot: poll the process without screenshots or back
    # presses, so navigation only starts once the game is actually up.
    boot_polls = max(1, int(GAME_BOOT_GRACE_SECONDS / 3))
    for _ in range(boot_polls):
        if is_wos_running(instance_index, verbose=False):
            break
        if stop_signal.wait(timeout=3):
            raise ToolStopped()
    else:
        log_message(f"Game process not detected during boot on instance {instance_index}.", "warning")
        return False

    # Verify the process stays active and navigate to the main screen
    for check in range(1, 11):
        if stop_signal.wait(timeout=3):
            raise ToolStopped()
        if not is_wos_running(instance_index, verbose=False):
            # A single missing process reading can be a transient ADB hiccup
            # rather than a dead game: re-check once before failing.
            if stop_signal.wait(timeout=GAME_PROCESS_RECHECK_SECONDS):
                raise ToolStopped()
            if not is_wos_running(instance_index, verbose=False):
                log_message(f"Game process not detected during check {check}/10 on instance {instance_index}.", "warning")
                return False
        if is_game_on_city_screen(instance_index):
            log_message(f"Game main screen reached on instance {instance_index}.", "success")
            return True
        if is_game_on_world_screen(instance_index):
            log_message(f"Game is on world screen on instance {instance_index}.", "info")
            go_cityworld(instance_index)
        else:
            log_message(f"Not on main screen on instance {instance_index}. Pressing back (check {check}/10).", "info")
            press_android_back_button(instance_index)

    log_message(f"Could not reach the main city screen on instance {instance_index}.", "error")
    return False


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


def _click_leftmost_template(instance_index, template_name, delay=CLICK_DELAY):
    """Click the left-most occurrence of a template on the current screen.

    Args:
        instance_index (int): Emulator instance index.
        template_name (str): Template name in ``TEMPLATE_PATHS``.
        delay (float): Delay after the click.

    Returns:
        bool: True when at least one template was found and clicked.
    """
    screenshot_path = take_screenshot(instance_index)
    if not screenshot_path:
        return False
    template_path = get_template_path(template_name)
    if not template_path:
        delete_temp_screenshot(screenshot_path)
        return False

    try:
        matches = find_multiple_templates(template_path, screenshot_path)
        if not matches:
            log_message(f"Template '{template_name}' NOT found on screen.", level="warning")
            return False
        x, y, width, height = min(matches, key=lambda box: box[0])
        click_on_coordinates(x + width // 2, y + height // 2, instance_index, delay=delay)
        log_message(f"Template '{template_name}' found, clicking its left-most match.", level="success")
        return True
    finally:
        delete_temp_screenshot(screenshot_path)


def _locate_template_center(template_name, screenshot_path, roi, gray, threshold):
    """Find a template on a screenshot and return its center.

    Args:
        template_name (str): Template name in TEMPLATE_PATHS.
        screenshot_path (str): Path to an existing screenshot.
        roi (tuple or None): Region of interest (x, y, w, h).
        gray (bool): Use gray-scale matching when True.
        threshold (float): Minimum confidence threshold for a match.

    Returns:
        tuple or None: (cx, cy) center of the match, or None when not found.
    """
    template_path = get_template_path(template_name)
    if not template_path:
        return None
    found, center = find_template_center_on_screen(template_path, screenshot_path, threshold=threshold, roi=roi, grayscale=gray)
    if not found or not center:
        return None
    return center[0], center[1]


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
        roi (tuple, str or None, optional): Region of interest as an (x, y, w, h)
            tuple, or an ROI name in the ROI dict (resolved via get_roi, fails
            when missing). When omitted the template is searched on the full
            screen.
        delay (float): Delay after the click.
        screenshot_path (str, optional): Reuse an already taken screenshot
            instead of capturing a new one. Only valid when the caller can
            guarantee no screen change has happened since the capture (that is,
            no click between captures).

    Returns:
        str or None: The name of the clicked template, or None if none was found.
    """
    if isinstance(roi, str):
        resolved_roi = get_roi(roi)
        if resolved_roi is None:
            log_message(f"ROI '{roi}' not found in ROI, cannot search the templates.", level="error")
            return None
    else:
        resolved_roi = roi

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
            if click_on_template(template_name, instance_index, roi=resolved_roi, delay=delay, gray=gray, screenshot_path=screenshot_path):
                return template_name
        return None
    finally:
        if owned_screenshot:
            delete_temp_screenshot(screenshot_path)


def click_on_template(template_name, instance_index, roi=None, delay=CLICK_DELAY, gray=False, screenshot_path=None, threshold=SCREEN_CHECK_THRESHOLD, clicks=1):
    """Takes a screenshot and clicks the center of the given template if found.

    Generic helper that replaces the repeated "screenshot -> find template ->
    click its center" pattern. The template is located once and its unchanged
    center is clicked ``clicks`` times.

    Args:
        template_name (str): Template name in TEMPLATE_PATHS.
        instance_index (int): Emulator instance index.
        roi (tuple, str or None, optional): Region of interest as an (x, y, w, h)
            tuple, or an ROI name in the ROI dict (resolved via get_roi, fails
            when missing). When omitted the template is searched on the full
            screen.
        delay (float): Delay after each click.
        gray (bool): Use gray-scale matching when True.
        screenshot_path (str, optional): Reuse an already taken screenshot
            instead of capturing a new one. Only valid when the caller can
            guarantee no screen change has happened since the capture (that is,
            no click between captures).
        threshold (float): Minimum confidence threshold for a match.
        clicks (int): Number of times to click the found center.

    Returns:
        bool: True if the template was found and clicked, False otherwise.
    """
    if isinstance(roi, str):
        resolved_roi = get_roi(roi)
        if resolved_roi is None:
            log_message(f"ROI '{roi}' not found in ROI, cannot click on '{template_name}'.", level="error")
            return False
    else:
        resolved_roi = roi

    owned_screenshot = screenshot_path is None
    if screenshot_path is None:
        screenshot_path = take_screenshot(instance_index)
    if not screenshot_path:
        log_message("Could not take a screenshot to search the templates.", level="error")
        return False

    try:
        center = _locate_template_center(template_name, screenshot_path, roi=resolved_roi, gray=gray, threshold=threshold)
        if center is None:
            return False
        for _ in range(clicks):
            click_on_coordinates(center[0], center[1], instance_index, delay=delay)
        if clicks > 1:
            log_message(f"Template '{template_name}' found; clicked it {clicks} times.", level="success")
        else:
            log_message(f"Template '{template_name}' found, clicking at ({center[0]}, {center[1]}).", level="success")
        return True
    finally:
        if owned_screenshot:
            delete_temp_screenshot(screenshot_path)


def click_on_text(text, instance_index, roi=None, delay=CLICK_DELAY, screenshot_path=None, last=False, fuzzy=False):
    """Takes a screenshot and clicks the center of the given text if found.

    Text-based counterpart of :func:`click_on_template` for menus whose
    entries kept their labels but moved around, e.g. the side menu.

    Args:
        text (str): Text to search for and click, e.g. 'Tundra Trek'.
        instance_index (int): Emulator instance index.
        roi (tuple, str or None, optional): Region of interest as an (x, y, w, h)
            tuple, or an ROI name in the ROI dict (resolved via get_roi, fails
            when missing). When omitted the text is searched on the full screen.
        delay (float): Delay after the click.
        screenshot_path (str, optional): Reuse an already taken screenshot
            instead of capturing a new one. Only valid when the caller can
            guarantee no screen change has happened since the capture (that is,
            no click between captures).
        last (bool): When True click the lowest occurrence of the text instead
            of the first one.
        fuzzy (bool): Retry the OCR with a looser mask and small spelling
            differences for decorative single-word labels.

    Returns:
        bool: True if the text was found and clicked, False otherwise.
    """
    if isinstance(roi, str):
        resolved_roi = get_roi(roi)
        if resolved_roi is None:
            log_message(f"ROI '{roi}' not found in ROI, cannot click on '{text}'.", level="error")
            return False
    else:
        resolved_roi = roi

    owned_screenshot = screenshot_path is None
    if screenshot_path is None:
        screenshot_path = take_screenshot(instance_index)
    if not screenshot_path:
        log_message(f"Could not get a screenshot to click on '{text}'.", level="error")
        return False

    try:
        text_search_kwargs = {
            "roi": resolved_roi,
            "instance_index": instance_index,
            "debug_label": f"click_text_{text}",
            "last": last,
        }
        if fuzzy:
            text_search_kwargs["fuzzy"] = True
        found, center = find_text_center_on_screen(screenshot_path, text, **text_search_kwargs)
        if not found or not center:
            return False

        cx, cy = center
        click_on_coordinates(cx, cy, instance_index, delay=delay)
        log_message(f"Text '{text}' found, clicking at ({cx}, {cy}).", level="success")
        return True
    finally:
        if owned_screenshot:
            delete_temp_screenshot(screenshot_path)
