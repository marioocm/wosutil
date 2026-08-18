"""Task automation module for Whiteout Survival."""

import time

from wosutil.config import INTEL_TIMER_MIN_SECONDS

# Import functions from emulator_manager
from wosutil.emulator.emulator_manager import (
    click_on,
    click_on_coordinates,
    delete_temp_screenshot,
    long_press_on_coordinates,
    press_android_back_button,
    scroll_screen,
    take_screenshot,
)
from wosutil.emulator.image_utils import (
    find_multiple_templates,
    find_template_center_on_screen,
    read_screen_time,
)
from wosutil.preferences import (
    MYSTERY_SHOP_LEVEL_WIDGETS_20,
    MYSTERY_SHOP_LEVEL_WIDGETS_50,
    get_mystery_shop_level,
)
from wosutil.stop import stop_signal
from wosutil.tool.tasks.task_helpers import (
    _train_troop_camp,
    click_first_found_template,
    click_on_template,
    click_on_text,
    detect_pet_adventure_chests,
    do_intel_exploration,
    end_tundra_trek_idle_if_active,
    ensure_city_screen,
    ensure_hero_recruit_screen,
    ensure_pet_skill_screen,
    go_alliance_tab,
    go_cityworld,
    go_exploration_tab,
    go_pet_adventure,
    go_pet_skill,
    go_shop,
    go_sidemenu_city,
    go_sidemenu_daily,
    go_tundra_trek,
    is_game_on_intel_screen,
    is_game_on_pet_adventure_screen,
    kill_intel_beast,
    open_pet_adventure_chest,
    rescue_intel_survivor,
    start_pet_adventure_chests,
)
from wosutil.tool.utc_time import get_seconds_until_utc_midnight
from wosutil.utils import get_roi, get_template_path, log_message

# --- TASKS ---
# Each task must return True on success and False on failure.


def claim_idle_income(instance_index):
    """Claims idle income from the exploration area.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if successful, False otherwise.
    """
    log_message("Attempting to claim idle income...", level="info")
    if not go_exploration_tab(instance_index):
        return False

    click_on_coordinates(617, 866, instance_index)
    click_on_coordinates(360, 925, instance_index, delay=0.6)

    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    return True


def donate_to_alliance_tech(instance_index):
    """Donates to alliance technology by finding the tech thumbnail and performing a long press.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if successful, False otherwise.
    """
    log_message("Attempting to donate to alliance technology...", level="info")
    if not go_alliance_tab(instance_index):
        return False

    click_on_coordinates(535, 935, instance_index)

    captured_screenshot_path = take_screenshot(instance_index)
    if not captured_screenshot_path:
        return False

    template_path = get_template_path("tech_thumb")
    roi = get_roi("tech_thumb")

    if not template_path or not roi:
        delete_temp_screenshot(captured_screenshot_path)
        press_android_back_button(instance_index)
        press_android_back_button(instance_index)
        return False

    found, center = find_template_center_on_screen(template_path, captured_screenshot_path, roi=roi)
    delete_temp_screenshot(captured_screenshot_path)
    if not found or center is None:
        log_message("Tech thumbnail NOT found. Aborting task.", level="warning")
        press_android_back_button(instance_index)
        press_android_back_button(instance_index)
        return False
    cx, cy = center

    click_on_coordinates(cx + 50, cy + 50, instance_index)
    long_press_on_coordinates(513, 1032, 5000, instance_index)  # 5 seconds

    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    return True


def turn_on_autojoin(instance_index):
    """Turns on the auto-join option for the alliance.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if successful, False otherwise.
    """
    log_message("Attempting to turn on auto-join for alliance...", level="info")
    if not go_alliance_tab(instance_index):
        return False

    click_on_coordinates(196, 665, instance_index, delay=1.5)
    click_on_coordinates(130, 130, instance_index)
    click_on_coordinates(360, 1225, instance_index)
    click_on_coordinates(434, 600, instance_index)
    click_on_coordinates(500, 1095, instance_index)

    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    return True


def claim_island_idle(instance_index):
    """Claims idle income from the island by navigating through the side menu and searching for the life essence icon.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if successful, False otherwise.
    """
    log_message("Attempting to claim island idle income...", level="info")
    if not go_sidemenu_daily(instance_index):
        return False

    scroll_screen(13, 500, 13, 0, 500, instance_index, hold_end_ms=500)
    time.sleep(1.0)

    if not click_on_text("Tree", instance_index, roi=get_roi("sidemenu"), delay=4):
        log_message("Tree entry NOT found in side menu. Aborting.", level="warning")
        return False

    click_on_coordinates(100, 70, instance_index)
    click_on_coordinates(100, 70, instance_index, delay=0.8)

    # Search for life essence
    found_any = False
    clicked_positions = set()  # Para evitar clicks duplicados

    essence_template_path = get_template_path("life_essence")
    essence_roi = get_roi("island_life_essence")

    if not essence_template_path or not essence_roi:
        go_cityworld(instance_index)
        return False

    for _ in range(2):  # Try to click it up to 2 times
        essence_path = take_screenshot(instance_index)
        if not essence_path:
            break

        essence_positions = find_multiple_templates(essence_template_path, essence_path, roi=essence_roi)
        delete_temp_screenshot(essence_path)

        if essence_positions:
            found_any = True
            new_clicks = 0  # Counter of new clicks in this iteration

            for ex, ey, ew, eh in essence_positions:
                # Create a unique key for this position (rounded to avoid duplicates from small differences)
                position_key = (ex // 10, ey // 10)  # Round to multiples of 10

                if position_key not in clicked_positions:
                    click_on_coordinates(ex + ew // 2, ey + eh // 2, instance_index, delay=1.0)
                    clicked_positions.add(position_key)
                    new_clicks += 1
                    log_message(f"Clicked on life essence at position ({ex + ew // 2}, {ey + eh // 2})", "info")

            # If no new clicks were made in this iteration, exit the loop
            if new_clicks == 0:
                log_message("No new life essences found to click, stopping search", "info")
                break
        else:
            log_message("No life essences found in this iteration", "info")
            break

    if not found_any:
        log_message("No life essence found on the island. Task failed.", level="warning")
        go_cityworld(instance_index)
        return False

    go_cityworld(instance_index)
    return True


def claim_mail(instance_index):
    """Claims mail rewards by navigating through the mail interface.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if successful, False otherwise.
    """
    log_message("Attempting to claim mail rewards...", level="info")
    if not ensure_city_screen(instance_index):
        return False

    # Click sequence to navigate and claim mail
    click_on_coordinates(665, 1050, instance_index)
    click_on_coordinates(88, 120, instance_index)
    for _ in range(3):
        click_on_coordinates(560, 1240, instance_index)
    click_on_coordinates(226, 120, instance_index)
    for _ in range(3):
        click_on_coordinates(560, 1240, instance_index)
    click_on_coordinates(360, 120, instance_index)
    for _ in range(3):
        click_on_coordinates(560, 1240, instance_index)
    click_on_coordinates(500, 120, instance_index)
    for _ in range(3):
        click_on_coordinates(560, 1240, instance_index)

    press_android_back_button(instance_index)
    return True


def claim_alliance_chests(instance_index):
    """Claims alliance chests by navigating through the alliance menu and opening chests.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if successful, False otherwise.
    """
    log_message("Attempting to claim alliance chests...", level="info")
    if not go_alliance_tab(instance_index):
        return False

    # Click on chest icon
    click_on_coordinates(535, 668, instance_index)
    # Click on chest tab
    click_on_coordinates(360, 206, instance_index)
    # Click on top tab 3 times
    for _ in range(3):
        click_on_coordinates(360, 54, instance_index)
    # Click on open chest
    click_on_coordinates(534, 400, instance_index)
    # Click on claim button 5 times
    for _ in range(5):
        click_on_coordinates(565, 1186, instance_index)
    # Click on left tab
    click_on_coordinates(190, 400, instance_index)
    # Click on claim button 5 times
    for _ in range(5):
        click_on_coordinates(360, 1208, instance_index)
    # Android back button 2 times
    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    return True


def claim_triumph(instance_index):
    """Claims triumph rewards from the alliance menu.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True if successful, False otherwise.
    """
    log_message("Attempting to claim triumph rewards...", level="info")
    if not go_alliance_tab(instance_index):
        return False

    click_on_coordinates(360, 1205, instance_index)
    click_on_coordinates(360, 460, instance_index)
    for _ in range(3):
        click_on_coordinates(360, 38, instance_index)
    click_on_coordinates(360, 870, instance_index)
    for _ in range(3):
        click_on_coordinates(360, 38, instance_index)
    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    return True


def claim_recruit_hero_free_chest(instance_index):
    """Claims free hero chests on the recruit hero chest screen.

    1. Ensures the main screen is active.
    2. Navigates to the recruit hero chest screen.
    3. Ensures the recruit hero chest screen is confirmed before searching for chests.
    4. Searches for all free chests using find_multiple_templates.
    5. For each chest found, clicks and waits 2 seconds.
    6. Ensures the recruit hero chest screen is confirmed before reading the timer.
    7. Reads countdown timer on screen using read_screen_time.
    8. Presses back twice to return to the main screen.

    Returns (True/False, seconds_until_reschedule).
    """
    log_message("Attempting to claim free hero chests on the recruit hero chest screen...", level="info")
    if not ensure_city_screen(instance_index):
        return False, 2 * 60 * 60

    # Navigate to the recruit hero chest screen
    click_on("heroes", instance_index)
    click_on_coordinates(535, 1215, instance_index, delay=0.7)

    # Make sure we are on the recruit hero chest screen before looking for chests
    if not ensure_hero_recruit_screen(instance_index):
        log_message("Could not reach the recruit hero chest screen, aborting task.", level="warning")
        press_android_back_button(instance_index)
        press_android_back_button(instance_index)
        return False, 2 * 60 * 60

    # Take screenshot and search for free chests
    screenshot_path = take_screenshot(instance_index)
    template_path = get_template_path("free_hero_chest")
    roi = get_roi("recruit_free_chest")
    if not screenshot_path or not template_path or not roi:
        delete_temp_screenshot(screenshot_path)
        log_message("Could not get screenshot, template, or ROI for free hero chests.", level="error")
        press_android_back_button(instance_index)
        press_android_back_button(instance_index)
        return False, 2 * 60 * 60

    chests = find_multiple_templates(template_path, screenshot_path, roi=roi)
    delete_temp_screenshot(screenshot_path)
    if chests:
        for x, y, w, h in chests:
            # Click on the center of the chest
            click_on_coordinates(x + w // 2, y + h // 2, instance_index, delay=2.0)
            # Return to the recruit hero chest screen
            click_on_coordinates(360, 125, instance_index, delay=2.0)
            click_on_coordinates(360, 125, instance_index)
    else:
        log_message("No free hero chests found.", level="info")

    # Make sure we are back on the recruit hero chest screen before reading the timer
    if not ensure_hero_recruit_screen(instance_index):
        log_message("Could not return to the recruit hero chest screen to read the timer, using default value (2 hours).", level="warning")
        press_android_back_button(instance_index)
        press_android_back_button(instance_index)
        return False, 2 * 60 * 60

    # Read the countdown timer on screen
    seconds = read_screen_time(instance_index, roi=get_roi("recruit_free_chest_timer"), debug_label="recruit_free_chest_timer")
    if seconds is not None and seconds > 0:
        log_message(f"Task will be rescheduled in {seconds} seconds according to on-screen timer.", level="info")
        reschedule = seconds
    else:
        log_message("No timer detected on screen, using default value (2 hours).", level="warning")
        reschedule = 2 * 60 * 60

    # Return to main screen
    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    return True, reschedule


def claim_storehouse_stamina(instance_index):
    """Claims stamina from the storehouse.

    1. Ensures the main menu is active.
    2. Opens the profile screen.
    3. Takes a screenshot and searches for the template.
    4. Searches for a timer using read_screen_time.
    5. Presses the back button to return to the main menu.

    Returns (True/False, reschedule_seconds).
    """
    log_message("Attempting to claim storehouse 120 stamina...", level="info")
    if not ensure_city_screen(instance_index):
        return False

    click_on("profile", instance_index, delay=0.7)
    click_on_coordinates(235, 1112, instance_index, delay=0.7)

    roi = get_roi("storehouse_claim_stamina")
    if not roi:
        log_message("Could not get ROI for storehouse_claim_stamina.", level="error")
        press_android_back_button(instance_index)
        return (False,)

    if not click_on_template("storehouse_claim_stamina", instance_index, roi=roi, delay=0.7):
        log_message("Stamina was already claimed, reescheduling task.", level="info")

    # Search for timer in the same ROI
    # Save the processed ROI for visual debugging (change the path as needed)
    seconds = read_screen_time(instance_index, roi=get_roi("storehouse_claim_stamina_timer"), debug_label="storehouse_claim_stamina_timer")
    if seconds is not None:
        log_message(f"Task will be rescheduled in {seconds} seconds according to on-screen timer.", level="info")
        reschedule = seconds
    else:
        log_message("No timer detected on screen, using default value (4 hours).", level="warning")
        return False

    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    return True, reschedule


def claim_nomadic_shop_rss_and_vip(instance_index):
    """Claims resources and VIP from the nomadic shop.

    1. Ensures the main city screen is active.
    2. Navigates to the shop using go_shop and clicks the Nomadic tab.
    3. Searches for nomadic shop resources and VIP.
    4. Clicks on found resources and VIP.
    5. Continues searching until no more resources are found.
    6. Searches for free refresh button.
    7. If refresh button is found, clicks it and restarts.
    8. If no refresh button is found, finishes the task.
    9. Reschedules to the next 00:00 UTC on the game clock (10 hours as a
       fallback when the UTC clock was not read yet).

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        tuple: (True/False, reschedule_seconds)
    """
    log_message("Attempting to claim nomadic shop resources and VIP...", level="info")
    if not go_shop(instance_index):
        return False, 10 * 60 * 60

    if not click_on_text("Nomadic", instance_index, roi=get_roi("shop_tabs"), delay=1.0):
        log_message("Nomadic shop tab NOT found. Aborting.", level="warning")
        return False, 10 * 60 * 60

    # Define the resources to search for
    resources = ["nomadic_shop_iron", "nomadic_shop_coal", "nomadic_shop_wood", "nomadic_shop_meat", "nomadic_shop_vip"]
    resources_roi = (34, 465, 648, 510)
    refresh_roi = (508, 233, 177, 59)

    while True:
        # Take a single screenshot per pass and search every resource against
        # it; clicking a resource changes the screen, so only the first match
        # is clicked and the outer loop re-scans for the remaining ones.
        screenshot_path = take_screenshot(instance_index)
        if not screenshot_path:
            log_message("Could not take screenshot for resource search.", level="error")
            return False, 10 * 60 * 60

        try:
            clicked_resource = None
            for resource in resources:
                template_path = get_template_path(resource)
                if not template_path:
                    log_message(f"Template path for {resource} not found.", level="warning")
                    continue

                found, center = find_template_center_on_screen(template_path, screenshot_path, roi=resources_roi)
                if found and center:
                    log_message(f"Found {resource}, clicking...", level="info")
                    cx, cy = center
                    # Special handling for VIP template
                    if resource == "nomadic_shop_vip":
                        click_on_coordinates(cx, cy + 100, instance_index)
                        click_on_coordinates(360, 830, instance_index)
                        click_on_coordinates(360, 790, instance_index)
                    else:
                        click_on_coordinates(cx, cy, instance_index)
                    clicked_resource = resource
                    break
        finally:
            delete_temp_screenshot(screenshot_path)

        if clicked_resource:
            continue  # screen changed, re-scan for any remaining resources

        log_message("No more resources found, checking for refresh button...", level="info")

        # Search for free refresh button
        if not click_on_template("nomadic_shop_free_refresh", instance_index, roi=refresh_roi, delay=1.0):
            log_message("No free refresh button found, finishing task.", level="info")

            reschedule = get_seconds_until_utc_midnight(instance_index, fallback=10 * 60 * 60)
            if reschedule is not None:
                log_message(f"Task will be rescheduled in {reschedule:.0f} seconds (until 00:00 UTC).", level="info")
            else:
                log_message("No UTC clock available, using default value (10 hours).", level="warning")

            press_android_back_button(instance_index)
            return True, reschedule
        else:
            # Continue searching for resources after refresh
            continue


def claim_mystery_shop(instance_index):
    """Claims redeemable items from the mystery shop.

    1. Ensures the main city screen is active.
    2. Navigates to the shop using go_shop and clicks the Mystery tab.
    3. Searches the items ROI for redeemable objects (free items always,
       widgets depending on the user preference) and clicks them.
    4. Continues searching until no more items are found.
    5. Searches for the free refresh button; if found, clicks it and restarts.
    6. If no refresh button is found, finishes the task and reschedules to the
       next 00:00 UTC on the game clock (10 hours as a fallback when the UTC
       clock was not read yet).

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        tuple: (True/False, reschedule_seconds)
    """
    log_message("Attempting to claim mystery shop items...", level="info")
    if not go_shop(instance_index):
        return False, 10 * 60 * 60

    if not click_on_text("Mystery", instance_index, roi=get_roi("shop_tabs"), delay=1.0):
        log_message("Mystery shop tab NOT found. Aborting.", level="warning")
        return False, 10 * 60 * 60

    items_roi = (0, 375, 710, 817)
    refresh_roi = (403, 146, 317, 343)

    mystery_shop_level = get_mystery_shop_level()
    items = ["mystery_shop_free"]
    if mystery_shop_level in (MYSTERY_SHOP_LEVEL_WIDGETS_50, MYSTERY_SHOP_LEVEL_WIDGETS_20):
        items.append("mystery_shop_widget_50")
    if mystery_shop_level == MYSTERY_SHOP_LEVEL_WIDGETS_20:
        items.append("mystery_shop_widget_20")
    log_message(f"Mystery shop level is '{mystery_shop_level}', searching for: {items}.", level="info")

    while True:
        # Take a single screenshot per pass and search every item template
        # against it; clicking an item changes the screen, so only the first
        # match is clicked and the outer loop re-scans for the remaining ones.
        screenshot_path = take_screenshot(instance_index)
        if not screenshot_path:
            log_message("Could not take screenshot for item search.", level="error")
            return False, 10 * 60 * 60

        try:
            clicked_item = None
            for item in items:
                template_path = get_template_path(item)
                if not template_path:
                    log_message(f"Template path for {item} not found.", level="warning")
                    continue

                found, center = find_template_center_on_screen(template_path, screenshot_path, roi=items_roi, threshold=0.97)
                if found and center:
                    log_message(f"Found {item}, clicking...", level="info")
                    cx, cy = center
                    click_on_coordinates(cx, cy, instance_index, delay=0.7)
                    click_on_coordinates(366, 829, instance_index, delay=0.7)
                    clicked_item = item
                    break
        finally:
            delete_temp_screenshot(screenshot_path)

        if clicked_item:
            continue  # screen changed, re-scan for any remaining items

        log_message("No more items found, checking for refresh button...", level="info")

        # Search for free refresh button
        if not click_on_template("mystery_shop_free_refresh", instance_index, roi=refresh_roi, delay=1.0):
            log_message("No free refresh button found, finishing task.", level="info")

            reschedule = get_seconds_until_utc_midnight(instance_index, fallback=10 * 60 * 60)
            if reschedule is not None:
                log_message(f"Task will be rescheduled in {reschedule:.0f} seconds (until 00:00 UTC).", level="info")
            else:
                log_message("No UTC clock available, using default value (10 hours).", level="warning")

            press_android_back_button(instance_index)
            return True, reschedule
        else:
            # Continue searching for items after refresh
            continue


def claim_vip_daily_rewards(instance_index):
    """Claims the daily VIP rewards from the VIP menu on the main screen.

    1. Ensures the main city screen is active.
    2. Clicks the 'vip' coordinate in the top bar.
    3. Clicks the daily rewards button and presses back.
    4. Clicks the VIP gift button and presses back.
    5. Reschedules to the next 00:00 UTC on the game clock (12 hours as a
       fallback when the UTC clock was not read yet).

    Returns (True/False, reschedule_seconds).
    """
    log_message("Attempting to claim VIP daily rewards...", level="info")
    if not ensure_city_screen(instance_index):
        return False, 12 * 60 * 60

    click_on("vip", instance_index, delay=0.7)
    click_on_coordinates(628, 282, instance_index, delay=0.7)
    click_on_coordinates(585, 826, instance_index, delay=0.7)
    click_on_coordinates(585, 826, instance_index, delay=0.7)
    click_on_coordinates(585, 826, instance_index, delay=0.7)

    reschedule = get_seconds_until_utc_midnight(instance_index, fallback=12 * 60 * 60)
    if reschedule is not None:
        log_message(f"Task will be rescheduled in {reschedule:.0f} seconds (until 00:00 UTC).", level="info")
    else:
        log_message("No UTC clock available, using default value (12 hours).", level="warning")

    press_android_back_button(instance_index)
    return True, reschedule


def claim_tundra_trek_supplies(instance_index):
    """Claims the free supplies from the tundra trek screen.

    1. Ensures the main city screen is active.
    2. Navigates to the tundra trek via the side menu.
    3. Searches for the 'tundra_trek_free_supplies' template and clicks it.
    4. Clicks the claim button.
    5. Reads the countdown timer to reschedule the task; defaults to 6 hours
       if no timer is found or if it is longer than 12 hours.
    6. Presses back twice.

    Returns (True/False, reschedule_seconds).
    """
    log_message("Attempting to claim tundra trek supplies...", level="info")
    if not go_tundra_trek(instance_index):
        return False, 6 * 60 * 60

    end_tundra_trek_idle_if_active(instance_index)

    if click_on_template("tundra_trek_free_supplies", instance_index, delay=0.7):
        click_on_coordinates(575, 594, instance_index, delay=2)
    else:
        log_message("Free supplies icon NOT found, skipping claim.", level="warning")

    seconds = read_screen_time(instance_index, roi=get_roi("tundra_trek_supplies_timer"), debug_label="tundra_trek_supplies_timer")
    if seconds is not None and seconds <= 16 * 60 * 60:
        log_message(f"Task will be rescheduled in {seconds} seconds according to on-screen timer.", level="info")
        reschedule = seconds
    else:
        log_message("No timer detected on screen or timer exceeds 16 hours, using default value (6 hours).", level="warning")
        reschedule = 6 * 60 * 60

    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    return True, reschedule


def start_tundra_trek_idle(instance_index):
    """Clicks the idle button on the tundra trek screen.

    1. Ensures the main city screen is active.
    2. Navigates to the tundra trek via the side menu.
    3. Searches for the 'Idle' text in the idle button ROI and clicks it.

    Returns:
        bool: True if the idle button was clicked, False otherwise.
    """
    log_message("Attempting to click tundra trek idle button...", level="info")
    if not go_tundra_trek(instance_index):
        return False

    end_tundra_trek_idle_if_active(instance_index)

    if not click_on_text("Idle", instance_index, roi=get_roi("tundra_trek_idle")):
        log_message("Tundra trek idle button NOT found.", level="warning")
        return False

    click_on_coordinates(362, 877, instance_index)
    return True


def claim_pet_adventure_ally_treasure(instance_index):
    """Claims the ally treasure from the pet adventure screen.

    1. Ensures the main city screen is active.
    2. Navigates to the pet adventure via the side menu.
    3. Clicks on the treasure entries and claims the rewards.
    4. Presses back to return.
    5. Reschedules to the next 00:00 UTC on the game clock (12 hours as a
       fallback when the UTC clock was not read yet).

    Returns:
        tuple: (True/False, reschedule_seconds)
    """
    log_message("Attempting to claim pet adventure ally treasure...", level="info")
    if not go_pet_adventure(instance_index):
        return False, 12 * 60 * 60

    click_on_coordinates(634, 1201, instance_index)
    click_on_coordinates(363, 1083, instance_index)
    for _ in range(3):
        click_on_coordinates(359, 1254, instance_index)
    press_android_back_button(instance_index)

    reschedule = get_seconds_until_utc_midnight(instance_index, fallback=12 * 60 * 60)
    if reschedule is not None:
        log_message(f"Task will be rescheduled in {reschedule:.0f} seconds (until 00:00 UTC).", level="info")
    else:
        log_message("No UTC clock available, using default value (12 hours).", level="warning")
    return True, reschedule


PET_ADVENTURE_CHESTS_RESCHEDULE_SECONDS = 5 * 60 * 60  # Default reschedule (chest 3 takes 5h)
PET_ADVENTURE_CHESTS_DAILY_LIMIT_RESCHEDULE_SECONDS = 6 * 60 * 60  # Reschedule when daily attempts are exhausted
PET_ADVENTURE_CHESTS_MAX_LOOP_ITERATIONS = 15  # Safety guard against infinite loops


def _finish_pet_adventure_starts(instance_index, result):
    """Closes the task after starting chests and returns the result tuple.

    Presses the two closing Android back buttons, and maps the start result
    ("done", "no_attempts" or "failed") to a (success, reschedule) tuple.

    Args:
        instance_index (int): Emulator instance index.
        result (str): Result of start_pet_adventure_chests.

    Returns:
        tuple: (bool, reschedule_seconds).
    """
    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    if result == "no_attempts":
        reschedule = get_seconds_until_utc_midnight(instance_index, fallback=PET_ADVENTURE_CHESTS_DAILY_LIMIT_RESCHEDULE_SECONDS)
        if reschedule is not None:
            log_message(f"Daily chest attempts exhausted, rescheduling in {reschedule:.0f} seconds (until 00:00 UTC).", level="info")
        else:
            log_message("Daily chest attempts exhausted and no UTC clock available, using default value (6 hours).", level="warning")
        return True, reschedule
    if result == "failed":
        log_message("Failed to start the pet adventure chests. Aborting task.", level="warning")
        return False, PET_ADVENTURE_CHESTS_RESCHEDULE_SECONDS
    return True, PET_ADVENTURE_CHESTS_RESCHEDULE_SECONDS


def send_pet_adventure_chests(instance_index):
    """Opens and starts the pet adventure chests, prioritizing chest 3.

    Once on the pet adventure screen the three chests are detected with the
    chest templates (0.9 confidence, two screenshots 1.5s apart). The task:

    1. Opens a chest 3 that is ready to be opened, then re-detects (opening a
       chest spawns a new one).
    2. When a chest 3 is on screen, starts it (and any other startable chest)
       and reschedules in 5 hours to wait until it is ready to open.
    3. When no chest 3 is on screen, opens the ready chests preferring type 2
       over type 1 and re-detects after each open to check whether a chest 3
       appeared, then starts the remaining startable chests.
    4. If fewer than 3 chests are detected, the task aborts.

    If the start button never appears it means the 4 daily attempts are
    exhausted: there is nothing left to do until the daily reset, so the task
    reschedules to the next 00:00 UTC (6 hours as a fallback when the UTC
    clock was not read yet). Once the attempts are exhausted the game also
    hides the pet adventure icon from the side menu, in which case the same
    midnight reschedule applies.

    Returns (True/False, reschedule_seconds).
    """
    log_message("Attempting to send pet adventure chests...", level="info")
    if not go_pet_adventure(instance_index):
        # When the daily attempts are exhausted the game hides the pet adventure
        # icon from the side menu: there is nothing left to do until the daily
        # reset, so reschedule to the next 00:00 UTC without checking inside.
        reschedule = get_seconds_until_utc_midnight(instance_index, fallback=PET_ADVENTURE_CHESTS_DAILY_LIMIT_RESCHEDULE_SECONDS)
        if reschedule is not None:
            log_message(
                f"Pet adventure icon NOT found in side menu (daily attempts exhausted), rescheduling in {reschedule:.0f} seconds (until 00:00 UTC).",
                level="info",
            )
        else:
            log_message("Pet adventure icon NOT found in side menu and no UTC clock available, using default value (6 hours).", level="warning")
        return True, reschedule

    if not is_game_on_pet_adventure_screen(instance_index):
        log_message("Not on the pet adventure screen after navigating. Aborting task.", level="warning")
        return False, PET_ADVENTURE_CHESTS_RESCHEDULE_SECONDS

    for _ in range(PET_ADVENTURE_CHESTS_MAX_LOOP_ITERATIONS):
        stop_signal.check()

        chests = detect_pet_adventure_chests(instance_index)
        if not chests or len(chests) < 3:
            log_message(f"Detected {len(chests) if chests else 0} pet adventure chests, expected 3. Aborting task.", level="warning")
            press_android_back_button(instance_index)
            press_android_back_button(instance_index)
            return False, PET_ADVENTURE_CHESTS_RESCHEDULE_SECONDS

        # Step 1: open a chest 3 that is ready, then re-detect (a new chest appeared)
        chest3_ready = next((c for c in chests if c["type"] == 3 and c["state"] == "ready"), None)
        if chest3_ready is not None:
            if not open_pet_adventure_chest(
                instance_index,
                chest3_ready["x"] + chest3_ready["w"] // 2,
                chest3_ready["y"] + chest3_ready["h"] // 2,
            ):
                log_message("Could not open the ready chest 3, aborting task.", level="warning")
                press_android_back_button(instance_index)
                press_android_back_button(instance_index)
                return False, PET_ADVENTURE_CHESTS_RESCHEDULE_SECONDS
            continue

        # Step 2: a chest 3 is on screen (starting or filling): start every startable chest
        if any(c["type"] == 3 for c in chests):
            result = start_pet_adventure_chests(instance_index)
            return _finish_pet_adventure_starts(instance_index, result)

        # Step 3: no chest 3 on screen: open the ready chests, type 2 before type 1
        ready_chests = [c for c in chests if c["state"] == "ready"]
        if ready_chests:
            chest_to_open = max(ready_chests, key=lambda c: c["type"])
            log_message(f"Opening ready pet adventure chest (type {chest_to_open['type']})...", level="info")
            if not open_pet_adventure_chest(
                instance_index,
                chest_to_open["x"] + chest_to_open["w"] // 2,
                chest_to_open["y"] + chest_to_open["h"] // 2,
            ):
                log_message("Could not open the ready pet adventure chest, aborting task.", level="warning")
                press_android_back_button(instance_index)
                press_android_back_button(instance_index)
                return False, PET_ADVENTURE_CHESTS_RESCHEDULE_SECONDS
            continue  # a chest 3 may have appeared, re-detect it

        # Step 4: no chest 3 and nothing ready: start the remaining startable chests
        if any(c["state"] == "start" for c in chests):
            result = start_pet_adventure_chests(instance_index)
            return _finish_pet_adventure_starts(instance_index, result)

        # Step 5: nothing to do, reschedule in 5 hours
        log_message("Nothing to do with the pet adventure chests, rescheduling in 5 hours.", level="info")
        press_android_back_button(instance_index)
        press_android_back_button(instance_index)
        return True, PET_ADVENTURE_CHESTS_RESCHEDULE_SECONDS

    log_message("Pet adventure chests task exceeded its safety bound and aborted.", level="warning")
    press_android_back_button(instance_index)
    press_android_back_button(instance_index)
    return False, PET_ADVENTURE_CHESTS_RESCHEDULE_SECONDS


PET_SKILL_RESCHEDULE_SECONDS = 6 * 60 * 60  # Default reschedule when no timer is detected
PET_SKILLS = [
    ("pet_skill_wolf", "pet_skill_wolf_timer"),
    ("pet_skill_ox", "pet_skill_ox_timer"),
    ("pet_skill_tapir", "pet_skill_tapir_timer"),
    ("pet_skill_elk", "pet_skill_elk_timer"),
]


def activate_daily_pet_skills(instance_index):
    """Activates the available daily pet skills on the pet skill screen.

    1. Ensures the main city screen is active.
    2. Navigates to the pet skill screen (go_pet_skill).
    3. Ensures the pet skill screen before activating a skill or reading a timer.
    4. Searches the four pet skills; the first one found is clicked and confirmed
       with the use button, then the search is repeated for the rest.
    5. When no skill is ready, reads the four timers and reschedules with the
       shortest one, or 6 hours when no timer is detected.

    Returns (True/False, reschedule_seconds).
    """
    log_message("Attempting to activate daily pet skills...", level="info")
    if not go_pet_skill(instance_index):
        return False, PET_SKILL_RESCHEDULE_SECONDS

    skills_roi = get_roi("pet_skill_buttons")
    use_roi = get_roi("pet_skill_use")

    while True:
        stop_signal.check()

        if not ensure_pet_skill_screen(instance_index):
            log_message("Not on the pet skill screen, aborting task.", level="warning")
            return False, PET_SKILL_RESCHEDULE_SECONDS

        # Try to activate a pet skill
        activated = False
        if skills_roi:
            clicked_skill = click_first_found_template(
                instance_index,
                [skill_name for skill_name, _ in PET_SKILLS],
                roi=skills_roi,
                delay=0.8,
            )
            if clicked_skill is not None:
                log_message(f"Found {clicked_skill}, clicking to activate it...", level="info")
                if click_on_template("pet_skill_use", instance_index, roi=use_roi, delay=1.0):
                    activated = True
                else:
                    log_message("Use button NOT found after clicking a pet skill.", level="warning")

        if activated:
            continue  # re-detect the remaining skills

        # No skill to activate: reschedule with the shortest on-screen timer
        timers = []
        for _skill_name, timer_roi in PET_SKILLS:
            seconds = read_screen_time(instance_index, roi=get_roi(timer_roi), debug_label=timer_roi)
            if seconds is not None:
                timers.append(seconds)

        if timers:
            reschedule = min(timers)
            log_message(f"Task will be rescheduled in {reschedule} seconds according to the shortest on-screen timer.", level="info")
        else:
            reschedule = PET_SKILL_RESCHEDULE_SECONDS
            log_message("No timers detected on screen, using default value (6 hours).", level="warning")

        press_android_back_button(instance_index)
        return True, reschedule


def train_troops(instance_index):
    """Trains and promotes troops in the 3 troop camps.

    1. Ensures the main city screen and opens the side menu.
    2. Selects the City tab and clicks the Infantry entry by text, then opens
       the train troop screen.
    3. Runs the camp flow for the infantry (default) camp, then switches to the
       other two camps using their bottom tabs.
    4. Reschedules with the shortest readable training timer, or 6 hours when
       no camp reports a timer.

    Returns (True/False, reschedule_seconds).
    """
    log_message("Attempting to promote or train troops...", level="info")
    if not go_sidemenu_city(instance_index):
        return False, 6 * 60 * 60

    if not click_on_text("Infantry", instance_index, roi=get_roi("sidemenu"), delay=3):
        log_message("Infantry camp entry NOT found in side menu. Aborting.", level="warning")
        return False, 6 * 60 * 60

    for _ in range(4):
        click_on_coordinates(359, 578, instance_index, delay=0.5)

    if not click_on_template("train_troop", instance_index, delay=1.0):
        log_message("Train troop button NOT found. Aborting.", level="warning")
        press_android_back_button(instance_index)
        return False, 6 * 60 * 60

    # Infantry camp is shown by default after opening the screen.
    timers = [_train_troop_camp(instance_index)]
    # Second and third troop camps, switched through their bottom tabs.
    for tab_x in (362, 586):
        click_on_coordinates(tab_x, 1238, instance_index, delay=1.0)
        timers.append(_train_troop_camp(instance_index))

    measured = [t for t in timers if t is not None]
    if measured:
        reschedule = min(measured)
        log_message(f"Task will be rescheduled in {reschedule} seconds according to the shortest troop training timer.", level="info")
    else:
        reschedule = 6 * 60 * 60
        log_message("No training timer detected, using default value (6 hours).", level="warning")

    press_android_back_button(instance_index)
    return True, reschedule


def do_intel_missions(instance_index):
    """Completes all missions in the intel tab: beast, survivors, and exploration.

    The beast is always prioritized: whenever it is available, its march is sent
    before anything else. While that march takes to go and come back,
    rescue_intel_survivor and do_intel_exploration are run to fill the gap, but
    the moment the march returns those tasks are skipped (even if they still have
    entries remaining) and the next beast march is sent to optimize the time.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        tuple: (True, reschedule_seconds)
    """
    while True:
        # Highest priority: kill the beast whenever it is available
        seconds = kill_intel_beast(instance_index)
        if seconds is False:
            # No troops left to send to the beast, stop trying.
            break
        if seconds is None:
            # The beast is not reachable right now (in cooldown or a march is
            # still on the way). Do one round of the fast intel tasks and then
            # immediately re-check the beast instead of ignoring it.
            did_any = False
            if rescue_intel_survivor(instance_index):
                did_any = True
            if do_intel_exploration(instance_index):
                did_any = True
            if not did_any:
                break
            continue

        # A march is in flight: fill its round-trip time with the other intel
        # tasks. When there is nothing left to fill, reschedule for the remaining
        # time so the scheduler can run other tasks while the march returns.
        end = time.time() + seconds
        while time.time() < end:
            did_any = False
            # Rescue survivors
            if rescue_intel_survivor(instance_index):
                did_any = True
            # Do exploration
            if do_intel_exploration(instance_index):
                did_any = True
            if not did_any:
                # Nothing fast left to do: reschedule for the march return
                # instead of blocking the instance until it comes back.
                remaining = end - time.time()
                if remaining <= 0:
                    break
                log_message(
                    f"Waiting for the beast march to return, rescheduling in {remaining:.0f}s to do other tasks.",
                    level="info",
                )
                return True, remaining
    # When there are no more beasts, try survivors and exploration until none are left
    while True:
        did_any = False
        if rescue_intel_survivor(instance_index):
            did_any = True
        if do_intel_exploration(instance_index):
            did_any = True
        if not did_any:
            break
    # Only trust the intel refresh timer when on the intel screen and the read is plausible
    if is_game_on_intel_screen(instance_index):
        # Try both timer locations without saving debug captures yet: the timer can
        # legitimately live in the second location, so a miss on the first is expected.
        timer = read_screen_time(instance_index, roi=get_roi("intel_timer"))
        if timer is None:
            timer = read_screen_time(instance_index, roi=get_roi("intel_timer2"))
        if timer is None:
            # Not found in any location: this is a real read failure, so save the
            # debug captures for both ROIs now.
            for roi_name in ("intel_timer", "intel_timer2"):
                read_screen_time(instance_index, roi=get_roi(roi_name), debug_label=roi_name)
        if timer is not None and timer >= INTEL_TIMER_MIN_SECONDS:
            return True, timer
    log_message("No reliable intel timer detected, using default value (4 hours).", level="warning")
    return True, 4 * 60 * 60
