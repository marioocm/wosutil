"""Task helper functions module.

Common automation patterns for game tasks.
"""

import re
import time

# Import configuration and utility functions
from wosutil.config import (
    COORDINATES,
    INTEL_BEAST_MARCH_SENT_WAIT_SECONDS,
    INTEL_BEAST_MAX_RETRIES,
    INTEL_BEAST_MAX_WAIT_SECONDS,
    INTEL_BEAST_TIMER_MAX_SECONDS,
)
from wosutil.emulator.emulator_manager import (
    click_on_coordinates,
    delete_temp_screenshot,
    press_android_back_button,
    scroll_screen,
    take_screenshot,
)
from wosutil.emulator.image_utils import (
    find_first_non_zero_digit_position,
    find_multiple_templates,
    find_template_center_on_screen,
    find_text_center_on_screen,
    find_text_on_screen,
    read_screen_time,
    read_text_lines_on_screen,
)
from wosutil.preferences import GATHER_RESOURCES, get_bear_rally_call_march, get_kill_beast_march_assignment
from wosutil.stop import stop_signal
from wosutil.tool.tasks.navigation import (
    _click_leftmost_template,
    _click_template_repeatedly,
    click_first_found_template,
    click_on_template,
    click_on_text,
    ensure_intel_screen,
    ensure_pet_adventure_screen,
    ensure_world_screen,
    go_pet_adventure,
    go_pet_skill,
    go_rally_tab,
    go_worldmap_search,
    is_game_on_pet_adventure_screen,
    is_game_on_screen,
)
from wosutil.utils import get_roi, get_template_path, log_message

PET_ADVENTURE_CHEST_THRESHOLD = 0.9  # Minimum confidence for chest templates

PET_ADVENTURE_CHEST_DETECTION_DELAY = 1.5  # Seconds between the two detection screenshots

PET_ADVENTURE_CHEST_MAX_STARTS = 5  # Max start attempts per run (4 chests per day + guard)

PET_ADVENTURE_CHEST_SELECT_RETRY_SECONDS = 1.0  # Wait before retrying the select pet search

PET_ADVENTURE_CHEST_RETRY_SECONDS = 2.0  # Wait before re-detecting after a failed detection

PET_ADVENTURE_CHEST_START_RETRY_ATTEMPTS = 3  # Re-detect attempts while starting chests

PET_ADVENTURE_CHEST_START_RETRY_SECONDS = 2.0  # Wait between start re-detection attempts

PET_ADVENTURE_CHEST_ATTEMPT_PROXIMITY = 60  # px: chest centers closer than this are the same chest

PET_ADVENTURE_CHEST_TEMPLATES = [
    ("pet_adventure_chest1", 1, "start"),
    ("pet_adventure_chest1_ready", 1, "ready"),
    ("pet_adventure_chest2", 2, "start"),
    ("pet_adventure_chest2_ready", 2, "ready"),
    ("pet_adventure_chest3", 3, "start"),
    ("pet_adventure_chest3_ready", 3, "ready"),
]

PET_ADVENTURE_CHEST_FILLING_TEMPLATES = [
    ("pet_adventure_chest1b", 1),
    ("pet_adventure_chest2b", 2),
    ("pet_adventure_chest3b", 3),
]

PET_ADVENTURE_CHEST_STATE_PRECEDENCE = {"start": 3, "ready": 2, "filling": 1}


def _pet_adventure_boxes_overlap(box1, box2, iou_threshold=0.2):
    """Returns True when two template match boxes overlap enough to be the same chest.

    Args:
        box1 (tuple): (x, y, w, h) of the first match.
        box2 (tuple): (x, y, w, h) of the second match.
        iou_threshold (float): Minimum intersection over union to consider the
            boxes the same chest (default 0.2).

    Returns:
        bool: True if the boxes belong to the same physical chest.
    """
    ax, ay, aw, ah = box1
    bx, by, bw, bh = box2
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = ix2 - ix1, iy2 - iy1
    if iw <= 0 or ih <= 0:
        return False
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union >= iou_threshold


def merge_pet_adventure_chest_matches(chests, positions, chest_type, state):
    """Merges template matches into a chest detection accumulator.

    Matches are grouped per physical chest by their rounded position or by box
    overlap, so the same chest found by several templates (e.g. the "start" and
    "_ready" templates of the same type) or in the two screenshots is only
    counted once. Every state occurrence is tallied and the winning state is the
    most frequent one, with "start" breaking ties over "ready" over "filling".

    Args:
        chests (dict): Accumulator mapping position key -> chest dict. Updated
            in place.
        positions (list): List of (x, y, w, h) template matches.
        chest_type (int): Chest type (1, 2 or 3).
        state (str): Chest state ("start", "ready" or "filling").
    """
    for x, y, w, h in positions:
        key = (x // 10, y // 10)
        chest = chests.get(key)
        if chest is None:
            for existing in chests.values():
                if existing["type"] == chest_type and _pet_adventure_boxes_overlap((existing["x"], existing["y"], existing["w"], existing["h"]), (x, y, w, h)):
                    chest = existing
                    break
        if chest is None:
            chests[key] = {
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "type": chest_type,
                "state": state,
                "state_counts": {state: 1},
            }
        else:
            chest["state_counts"][state] = chest["state_counts"].get(state, 0) + 1
            chest["state"] = max(
                chest["state_counts"],
                key=lambda s: (chest["state_counts"][s], PET_ADVENTURE_CHEST_STATE_PRECEDENCE[s]),
            )


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


def _chest_center_is_attempted(chest, attempted_centers):
    """Returns True when a chest center is within proximity of an attempted center.

    Chests vibrate and shift a few pixels between detections, so an exact
    position match would re-attempt the same physical chest (e.g. one that is
    mid-animation). Centers closer than PET_ADVENTURE_CHEST_ATTEMPT_PROXIMITY
    pixels are considered the same chest.

    Args:
        chest (dict): Chest detection dict.
        attempted_centers (list): List of (x, y) centers already attempted.

    Returns:
        bool: True if the chest was already attempted.
    """
    cx = chest["x"] + chest["w"] // 2
    cy = chest["y"] + chest["h"] // 2
    return any(abs(cx - ax) <= PET_ADVENTURE_CHEST_ATTEMPT_PROXIMITY and abs(cy - ay) <= PET_ADVENTURE_CHEST_ATTEMPT_PROXIMITY for ax, ay in attempted_centers)


def _detect_three_pet_adventure_chests(instance_index):
    """Detects the 3 pet adventure chests, retrying until they are visible.

    After a chest is started or opened the remaining chests can animate for a
    few seconds, during which fewer than 3 chests are detected. This retries the
    detection (closing leftover panels between attempts) instead of failing.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        list: Detected chests, or a list with fewer than 3 entries if they never
            became visible within the retry budget.
    """
    for _ in range(PET_ADVENTURE_CHEST_START_RETRY_ATTEMPTS):
        chests = detect_pet_adventure_chests(instance_index)
        if chests and len(chests) >= 3:
            return chests
        time.sleep(PET_ADVENTURE_CHEST_START_RETRY_SECONDS)
        ensure_pet_adventure_screen(instance_index)
    return chests


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
            # A single back closes the chest panel if it opened. Pressing a second
            # one could exit the pet adventure screen entirely, so verify the
            # screen instead of pressing blindly.
            press_android_back_button(instance_index, delay=1.0)
            ensure_pet_adventure_screen(instance_index)
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
    attempted_centers = []
    for _ in range(PET_ADVENTURE_CHEST_MAX_STARTS):
        stop_signal.check()

        chests = _detect_three_pet_adventure_chests(instance_index)
        if not chests or len(chests) < 3:
            # The chest clicks may have exited the pet adventure screen (e.g. a
            # misclassified chest opened no panel and a back press closed the
            # screen); re-enter it once before giving up.
            if not is_game_on_pet_adventure_screen(instance_index):
                log_message("Not on the pet adventure screen while starting chests, re-entering.", level="info")
                go_pet_adventure(instance_index)
                ensure_pet_adventure_screen(instance_index)
            chests = _detect_three_pet_adventure_chests(instance_index)
            if not chests or len(chests) < 3:
                log_message("Could not detect the 3 pet adventure chests while starting them.", level="warning")
                return "failed"

        candidates = sorted(
            (c for c in chests if c["state"] == "start" and not _chest_center_is_attempted(c, attempted_centers)),
            key=lambda c: (c["type"] != 3, -c["type"]),  # chest 3 first, then type 2, then type 1
        )
        if not candidates:
            return "done"
        chest = candidates[0]
        center = (chest["x"] + chest["w"] // 2, chest["y"] + chest["h"] // 2)
        attempted_centers.append(center)

        result = start_pet_adventure_chest(instance_index, center[0], center[1])
        if result == "no_attempts":
            return "no_attempts"
        if result == "already_active":
            log_message("Skipping a pet adventure chest that is already active.", level="info")
            continue
        if result is not True:
            return "failed"

    return "done"


def activate_battle_pet_skills(instance_index):
    """Activates the battle pet skills used before the bear trap attack.

    Navigates to the pet skill screen, clicks the three battle skill entries
    in sequence and closes the screen with an Android back press.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        bool: True when the pet skill screen was reached and the sequence was
            clicked, False otherwise.
    """
    if not go_pet_skill(instance_index):
        return False
    click_on_coordinates(140, 454, instance_index, delay=0.6)
    click_on_coordinates(196, 1089, instance_index, delay=0.6)
    click_on_coordinates(512, 819, instance_index, delay=0.6)
    press_android_back_button(instance_index)
    return True


def _read_gathering_tile_time(instance_index):
    """Read the gathering duration to the right of the ``Gathering Time`` label.

    Locating the label first avoids depending on a fixed modal position while
    still restricting the timer OCR to the area where the value is displayed.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        int or None: Gathering duration in seconds, or None when unreadable.
    """
    screenshot_path = take_screenshot(instance_index)
    if not screenshot_path:
        return None

    try:
        found, label_box = find_text_on_screen(
            screenshot_path,
            "Gathering Time",
            instance_index=instance_index,
            debug_label="gathering_tile_time",
        )
    finally:
        delete_temp_screenshot(screenshot_path)

    if not found or label_box is None:
        log_message("'Gathering Time' label NOT found on the resource tile.", level="warning")
        return None

    label_x, label_y, label_w, label_h = label_box
    timer_x = label_x + label_w
    search_roi = get_roi("worldmap_search")
    if not search_roi:
        log_message("Could not get the world-map search ROI.", level="error")
        return None
    screen_right = search_roi[0] + search_roi[2]
    # The timer sits centered between the label and the modal edge, so the empty
    # padding on both sides is reduced with fixed offsets.
    timer_roi = (
        timer_x + 20,
        max(0, label_y - 10),
        max(1, screen_right - timer_x - 45),
        max(50, label_h + 20),
    )
    return read_screen_time(
        instance_index,
        roi=timer_roi,
        debug_label="gathering_tile_timer",
        ocr_psms=(6, 7, 8, 11, 12, 13),
    )


def gather_tile(instance_index, resource):
    """Find and deploy a gathering march for the selected resource.

    The helper opens the world-map search, selects the resource, raises the
    search level up to ten times when the increase control is available, finds
    a tile, reads its gathering duration, removes the left-most hero and deploys
    the march with :func:`send_march`.

    Args:
        instance_index (int): Emulator instance index.
        resource (str): Resource to search: ``meat``, ``wood``, ``coal`` or
            ``iron``.

    Returns:
        int or None: The march round-trip time in seconds (the value returned
            by :func:`send_march`), or None when any required step cannot be
            completed.
    """
    if resource not in GATHER_RESOURCES:
        log_message(f"Unsupported gathering resource '{resource}'.", level="error")
        return None
    if not go_worldmap_search(instance_index):
        return None

    if not click_on_text(resource.title(), instance_index, roi="worldmap_search", fuzzy=True):
        log_message(f"Resource '{resource}' NOT found in the world-map search.", level="warning")
        return None

    _click_template_repeatedly("gather_tile_increase_level", instance_index, clicks=10, roi="worldmap_search", gray=False, threshold=0.92)

    if not click_on_text("Search", instance_index, roi="worldmap_search", delay=3.0, last=True, fuzzy=True):
        log_message("Search button NOT found in the world-map search.", level="warning")
        return None

    gathering_time = _read_gathering_tile_time(instance_index)
    if gathering_time is None:
        return None

    if not click_on_text("Gather", instance_index, roi="gathering_tile_info", last=True, fuzzy=True):
        log_message("Gather button NOT found on the resource tile.", level="warning")
        return None
    if not _click_leftmost_template(instance_index, "remove_hero", delay=1.0):
        log_message("No hero removal control found on the march screen.", level="warning")
        return None

    march_walking_time = send_march(instance_index)
    if march_walking_time is None or march_walking_time is False:
        log_message("Could not send the gathering march.", level="warning")
        return None

    log_message(f"Gathering march deployed; march round-trip is {march_walking_time} seconds.", level="success")
    return march_walking_time


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


def recall_march(instance_index):
    """Recalls every march currently away from the city on the world map.

    Takes a single screenshot and searches the world-map marching panel ROI
    (``worldmap_marching``) for the 'recall_march' template, clicking each
    occurrence and confirming the recall popup at the fixed coordinates
    (510, 792) before moving to the next one.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        int: Number of marches recalled (0 when there is nothing to recall).
    """
    screenshot_path = take_screenshot(instance_index)
    if not screenshot_path:
        return 0

    template_path = get_template_path("recall_march")
    roi = get_roi("worldmap_marching")
    if not template_path or not roi:
        log_message("Could not get the recall_march template or the worldmap_marching ROI.", level="error")
        delete_temp_screenshot(screenshot_path)
        return 0

    try:
        matches = find_multiple_templates(template_path, screenshot_path, roi=roi)
        if not matches:
            log_message("No marching units to recall on the world map.", level="info")
            return 0
        for x, y, w, h in matches:
            stop_signal.check()
            cx, cy = x + w // 2, y + h // 2
            click_on_coordinates(cx, cy, instance_index, delay=0.6)
            click_on_coordinates(510, 792, instance_index, delay=0.6)
            log_message(f"Recalled a march by clicking ({cx}, {cy}) and confirming at (510, 792).", level="success")
        return len(matches)
    finally:
        delete_temp_screenshot(screenshot_path)


def send_march(instance_index):
    """Read the march timer and deploy the march by clicking its Deploy button.

    The timer, the no-troops check and the Deploy button are all resolved from a
    single screenshot, so no extra captures are taken between those steps.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        int: Time in seconds to wait (detected timer * 2, capped at
            INTEL_BEAST_MAX_WAIT_SECONDS, or
            INTEL_BEAST_MARCH_SENT_WAIT_SECONDS when the march was sent without
            a readable timer).
        False: When no troops are available to send the march.
        None: When the send-march screen could not be confirmed (the Deploy
            button is not on screen).
    """
    screenshot_path = take_screenshot(instance_index)
    if not screenshot_path:
        log_message("Could not take a screenshot to send the march.", level="error")
        return None

    try:
        timer = read_screen_time(
            instance_index,
            roi=get_roi("walking_march_time"),
            debug_label="walking_march_time",
            max_seconds=INTEL_BEAST_TIMER_MAX_SECONDS,
            screenshot_path=screenshot_path,
        )
        if timer is None and is_game_on_screen(instance_index, "no_troops_left", screenshot_path=screenshot_path):
            log_message("No troops left to send to the march, skipping it.", level="warning")
            return False

        deploy_found, deploy_center = find_text_center_on_screen(
            screenshot_path,
            "Deploy",
            instance_index=instance_index,
            debug_label="click_text_Deploy",
            last=True,
        )

        if timer is None:
            if deploy_found and deploy_center:
                log_message("Timer unreadable but the Deploy button is on screen, sending the march.", level="warning")
                click_on_coordinates(deploy_center[0], deploy_center[1], instance_index)
                return INTEL_BEAST_MARCH_SENT_WAIT_SECONDS
            log_message("Deploy button NOT found, not on the correct screen; the march will be retried.", level="warning")
            return None

        if deploy_found and deploy_center:
            click_on_coordinates(deploy_center[0], deploy_center[1], instance_index)
        else:
            log_message("Deploy button NOT found, cannot send the march.", level="warning")
            return None
        return min(timer * 2, INTEL_BEAST_MAX_WAIT_SECONDS)
    finally:
        delete_temp_screenshot(screenshot_path)


def select_march(instance_index, march):
    """Selects a march formation on the send-march screen, scrolling when needed.

    Marches 9 to 12 live on a second row reached by scrolling the formation
    selector horizontally first.

    Args:
        instance_index (int): Emulator instance index.
        march (int): March number between 1 and 12.
    """
    if march > 8:
        scroll_screen(
            KILL_BEAST_MARCH_SCROLL_START[0],
            KILL_BEAST_MARCH_SCROLL_START[1],
            KILL_BEAST_MARCH_SCROLL_END[0],
            KILL_BEAST_MARCH_SCROLL_END[1],
            200,
            instance_index,
            delay=0.5,
        )
    click_on_coordinates(*KILL_BEAST_MARCH_POSITIONS[march], instance_index, delay=0.3)


def kill_beast(instance_index):
    """Kills a beast with the default march if the beast is already clicked and centered on the screen.

    If the user assigned a march in the preferences, that formation is selected
    on the march row (scrolling horizontally first when needed) before attacking.
    The march itself is deployed by :func:`send_march`.

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
        select_march(instance_index, march)

    return send_march(instance_index)


BEAR_RALLY_MIN_TIMER_SECONDS = 25  # Discard rallies that start in less than this

BEAR_RALLY_RETRY_SECONDS = 25  # Wait before retrying when no valid rally is on screen

BEAR_RALLY_MARGIN_SECONDS = 30  # March cooldown margin added to the read rally timer

BEAR_TRAP_OWN_RALLY_PREP_SECONDS = 5 * 60  # Time our own bear rally takes to prepare

_RALLY_COUNTDOWN_RE = re.compile(r"(\d+):(\d+):(\d+)")


def _read_rally_countdowns(screenshot_path):
    """Read the 'Rallying: HH:MM:SS' countdowns of the rallies on the panel.

    Args:
        screenshot_path (str): Path to the screenshot of the rallies panel.

    Returns:
        list: (timer_seconds, (x, y, w, h)) countdown text boxes in full-screen
            coordinates, in reading order.
    """
    roi = get_roi("rally_tab")
    if not roi:
        log_message("Could not get the rally_tab ROI.", level="error")
        return []
    countdowns = []
    for text, box in read_text_lines_on_screen(screenshot_path, roi=roi):
        normalized = re.sub(r"[^a-z0-9: ]", "", text.lower())
        if "rallying" not in normalized:
            continue
        match = _RALLY_COUNTDOWN_RE.search(text)
        if not match:
            continue
        hours, minutes, seconds = (int(group) for group in match.groups())
        countdowns.append((hours * 3600 + minutes * 60 + seconds, box))
    log_message(f"rally_tab: {len(countdowns)} countdown(s) found", level="debug")
    for seconds, box in countdowns:
        log_message(f"  countdown {seconds}s at {box}", level="debug")
    return countdowns


def _read_join_rally_buttons(screenshot_path):
    """Read the centers of every join_rally button on the rallies panel.

    The template is matched in color (BGR, TM_CCOEFF_NORMED) with a strict
    0.96 threshold so the green join button is not confused with visually
    similar elements.

    Args:
        screenshot_path (str): Path to the screenshot of the rallies panel.

    Returns:
        list: (cx, cy) join button centers, ordered top to bottom.
    """
    template_path = get_template_path("join_rally")
    roi = get_roi("rally_tab")
    if not template_path or not roi:
        log_message("Could not get the join_rally template or the rally_tab ROI.", level="error")
        return []
    matches = find_multiple_templates(template_path, screenshot_path, roi=roi, threshold=0.96)
    log_message(f"join_rally: {len(matches)} match(es) found at threshold 0.96", level="debug")
    for x, y, w, h in matches:
        log_message(f"  match at ({x}, {y}, {w}, {h})", level="debug")
    centers = [(x + w // 2, y + h // 2) for x, y, w, h in matches]
    return sorted(centers, key=lambda center: center[1])


def _pick_valid_rally(countdowns, join_buttons):
    """Pick the highest valid rally on the panel.

    Countdowns are considered top to bottom and the first one with at least
    BEAR_RALLY_MIN_TIMER_SECONDS remaining and a join button below its
    countdown text (never above it) is picked, so the rally higher up on the
    screen always wins over lower ones.

    Args:
        countdowns (list): (timer_seconds, (x, y, w, h)) countdown text boxes.
        join_buttons (list): (cx, cy) join button centers.

    Returns:
        tuple or None: (timer_seconds, (cx, cy)) of the rally to join, or None
            when no valid rally is on screen.
    """
    for seconds, box in sorted(countdowns, key=lambda item: item[1][1]):
        if seconds < BEAR_RALLY_MIN_TIMER_SECONDS:
            continue
        text_bottom = box[1] + box[3]
        below = [button for button in join_buttons if button[1] >= text_bottom]
        if not below:
            continue
        return seconds, min(below, key=lambda button: button[1])
    return None


def join_bear_rally(instance_index, march):
    """Joins an ally rally against the bear with the given march.

    Opens the alliance rally tab and joins the first rally whose
    'Rallying: HH:MM:SS' countdown still has enough time, clicking the
    join button right below that countdown and deploying the march with
    :func:`send_march`, closing the screens with an Android back press so the
    next attempt starts clean. When no valid rally is on
    screen it closes the panel the same way and retries after
    BEAR_RALLY_RETRY_SECONDS, looping until a rally is joined or the tool is
    stopped.

    Args:
        instance_index (int): Emulator instance index.
        march (int): March number to send, between 1 and 12.

    Returns:
        int or None: Seconds until the march can be launched again (the read
            rally timer plus BEAR_RALLY_MARGIN_SECONDS, counting from when the
            timer was read because it keeps running), or None when the rally
            could not be joined.
    """
    while True:
        stop_signal.check()
        if not go_rally_tab(instance_index):
            return None

        read_at = time.time()
        screenshot_path = take_screenshot(instance_index)
        if not screenshot_path:
            press_android_back_button(instance_index)
            press_android_back_button(instance_index)
            time.sleep(BEAR_RALLY_RETRY_SECONDS)
            continue
        try:
            countdowns = _read_rally_countdowns(screenshot_path)
            join_buttons = _read_join_rally_buttons(screenshot_path)
        finally:
            delete_temp_screenshot(screenshot_path)

        rally = _pick_valid_rally(countdowns, join_buttons)
        if rally is None:
            # Close the rallies panel before retrying.
            press_android_back_button(instance_index)
            press_android_back_button(instance_index)
            time.sleep(BEAR_RALLY_RETRY_SECONDS)
            continue

        timer_seconds, join_center = rally
        log_message(f"Joining a bear rally starting in {timer_seconds}s with march {march}.", level="info")
        click_on_coordinates(join_center[0], join_center[1], instance_index, delay=0.8)
        select_march(instance_index, march)
        result = send_march(instance_index)
        if result is False:
            log_message("No troops left to send to the bear rally, skipping it.", level="warning")
            press_android_back_button(instance_index)
            press_android_back_button(instance_index)
            press_android_back_button(instance_index)
            return None
        if result is None:
            # The join click did not open the send-march screen (e.g. the rally
            # already started or the button was stale): close the panel and
            # retry instead of counting the march as sent.
            log_message("Could not open the send-march screen, closing the panel and retrying.", level="warning")
            press_android_back_button(instance_index)
            press_android_back_button(instance_index)
            time.sleep(BEAR_RALLY_RETRY_SECONDS)
            continue
        # Back so the next rally attempt starts clean.
        press_android_back_button(instance_index)
        press_android_back_button(instance_index)
        elapsed = time.time() - read_at
        return max(0, timer_seconds + BEAR_RALLY_MARGIN_SECONDS - elapsed)


def call_bear_rally(instance_index):
    """Open the bear trap panel and call our own rally with the configured march.

    Clicks the bear trap icon on the world map, waits for the panel to open,
    clicks the rally button (matched in color, never in gray), confirms at the
    fixed coordinates and lands on the send-march screen, where the squad
    selected by the user is deployed with :func:`send_march`.

    Args:
        instance_index (int): Emulator instance index.

    Returns:
        int: Seconds to wait before calling another rally (the march time
            returned by send_march doubled, plus the rally preparation time).
        False: When there are no troops left to send.
        None: When the rally could not be called (a step of the flow failed).
    """
    if not ensure_world_screen(instance_index):
        return None

    if not click_on_template("bear_trap_icon", instance_index, delay=2.0):
        log_message("Bear trap icon not found on the world map, cannot call a rally.", level="warning")
        return None

    if not click_on_template("bear_trap_rally", instance_index, delay=0.8):
        log_message("Bear trap rally button not found, cannot call a rally.", level="warning")
        press_android_back_button(instance_index)
        return None

    click_on_coordinates(*COORDINATES["bear_trap_confirm"], instance_index, delay=0.8)
    select_march(instance_index, get_bear_rally_call_march())
    result = send_march(instance_index)
    if result is False:
        log_message("No troops left to call the bear rally, skipping it.", level="warning")
        press_android_back_button(instance_index)
        return False
    if result is None:
        log_message("Could not confirm the send-march screen for the bear rally.", level="warning")
        press_android_back_button(instance_index)
        return None
    wait = result * 2 + BEAR_TRAP_OWN_RALLY_PREP_SECONDS
    log_message(f"Bear rally called, waiting {wait} seconds before calling another one.", level="info")
    return wait


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

        if click_on_template("intel_claim_all", instance_index, roi="intel_claim_all", screenshot_path=screenshot_path):
            press_android_back_button(instance_index)
            delete_temp_screenshot(screenshot_path)
            screenshot_path = take_screenshot(instance_index)
            if not screenshot_path:
                return None
        else:
            log_message("'intel_claim_all' not found on the screen.", level="info")

        return click_first_found_template(instance_index, templates, roi="intel", screenshot_path=screenshot_path)
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
        click_on_template("intel_button", instance_index, roi="bottom_right_side_icons", delay=0.8)
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
    click_on_template("intel_button", instance_index, roi="bottom_right_side_icons", delay=0.8)
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
            found, _ = find_template_center_on_screen(victory_template, screenshot_path, roi=victory_roi, grayscale=True)
            delete_temp_screenshot(screenshot_path)
            if found:
                break
        elif screenshot_path:
            delete_temp_screenshot(screenshot_path)
        time.sleep(4 if first_retry else 5)
        first_retry = False
    press_android_back_button(instance_index)
    click_on_template("intel_button", instance_index, roi="bottom_right_side_icons", delay=0.8)
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

        if click_on_template("train_troop_promote", instance_index, roi="train_troop_promote", delay=1.0):
            click_on_coordinates(521, 904, instance_index, delay=1.0)
        else:
            click_on_coordinates(531, 1119, instance_index, delay=1.0)

    log_message("Could not read a troop training timer after all attempts.", level="warning")
    return None
