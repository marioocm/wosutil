"""Task definitions module.

Provides metadata and configuration for all available automation tasks.
"""

from wosutil.preferences import get_task_priorities
from wosutil.tool.tasks.task_automation import (
    activate_daily_pet_skills,
    claim_alliance_chests,
    claim_idle_income,
    claim_island_idle,
    claim_mail,
    claim_mystery_shop,
    claim_nomadic_shop_rss_and_vip,
    claim_pet_adventure_ally_treasure,
    claim_recruit_hero_free_chest,
    claim_storehouse_stamina,
    claim_triumph,
    claim_tundra_trek_supplies,
    claim_vip_daily_rewards,
    do_intel_missions,
    donate_to_alliance_tech,
    send_pet_adventure_chests,
    start_tundra_trek_idle,
    train_troops,
    turn_on_autojoin,
)


def get_task_definitions(log_message):
    """Get task definitions with optimized structure.

    User-defined priorities from the preferences are applied on top of the
    default ones, so tasks can be reordered without touching this file.

    Returns:
        dict: Dictionary of task definitions with metadata and functions.
    """
    task_definitions = {
        "claim_idle": {
            "id": "claim_idle",
            "name": "Claim Idle Income",
            "description": "Claims idle income from exploration area",
            "function": lambda instance_index: claim_idle_income(instance_index),
            "priority": 4,
            "reschedule_seconds": 7 * 60 * 60,  # 7 hours
            "category": "exploration",
        },
        "donate_tech": {
            "id": "donate_tech",
            "name": "Donate to Alliance Tech",
            "description": "Donates resources to alliance technology",
            "function": lambda instance_index: donate_to_alliance_tech(instance_index),
            "priority": 13,
            "reschedule_seconds": 4 * 60 * 60,  # 4 hours
            "category": "alliance",
        },
        "autojoin": {
            "id": "autojoin",
            "name": "Activate Auto-Join",
            "description": "Turns on auto-join for alliance rallies",
            "function": lambda instance_index: turn_on_autojoin(instance_index),
            "priority": 14,
            "reschedule_seconds": 7 * 60 * 60,  # 7 hours
            "category": "alliance",
        },
        "claim_island": {
            "id": "claim_island",
            "name": "Claim Island Life Essence",
            "description": "Claims life essence from the island",
            "function": lambda instance_index: claim_island_idle(instance_index),
            "priority": 5,
            "reschedule_seconds": 7 * 60 * 60,  # 7 hours
            "category": "island",
        },
        "claim_mail": {
            "id": "claim_mail",
            "name": "Claim Mail Rewards",
            "description": "Claims rewards from mail inbox",
            "function": lambda instance_index: claim_mail(instance_index),
            "priority": 15,
            "reschedule_seconds": 8 * 60 * 60,  # 8 hours
            "category": "mail",
        },
        "claim_alliance_chests": {
            "id": "claim_alliance_chests",
            "name": "Claim Alliance Chests",
            "description": "Claims alliance chests from the alliance menu",
            "function": lambda instance_index: claim_alliance_chests(instance_index),
            "priority": 16,
            "reschedule_seconds": 10 * 60 * 60,  # 10 hours
            "category": "alliance",
        },
        "claim_triumph": {
            "id": "claim_triumph",
            "name": "Claim Triumph Rewards",
            "description": "Claims triumph rewards from the alliance menu",
            "function": lambda instance_index: claim_triumph(instance_index),
            "priority": 17,
            "reschedule_seconds": 12 * 60 * 60,  # 12 hours
            "category": "alliance",
        },
        "claim_recruit_hero_free_chest": {
            "id": "claim_recruit_hero_free_chest",
            "name": "Claim Recruit Hero Free Chest",
            "description": "Claims the free hero chests on the recruit hero chest screen.",
            "function": lambda instance_index: claim_recruit_hero_free_chest(instance_index),
            "priority": 1,
            "reschedule_seconds": 12 * 60 * 60,  # 12 hours
            "category": "heroes",
        },
        "claim_storehouse_stamina": {
            "id": "claim_storehouse_stamina",
            "name": "Claim Storehouse Stamina",
            "description": "Claims stamina from the storehouse by opening the profile and searching for the stamina icon and timer.",
            "function": lambda instance_index: claim_storehouse_stamina(instance_index),
            "priority": 6,
            "reschedule_seconds": 4 * 60 * 60,  # 4 hours
            "category": "profile",
        },
        "do_intel_missions": {
            "id": "do_intel_missions",
            "name": "Hunt All Intel Beasts",
            "description": "Hunts all available beasts in the intel screen, waiting the time returned by each kill, and reschedules based on the intel timer or 4 hours by default.",
            "function": lambda instance_index: do_intel_missions(instance_index),
            "priority": 11,
            "reschedule_seconds": 4 * 60 * 60,  # 4 hours
            "category": "intel",
        },
        "claim_nomadic_shop_rss_and_vip": {
            "id": "claim_nomadic_shop_rss_and_vip",
            "name": "Claim Nomadic Shop Resources and VIP",
            "description": "Claims resources (iron, coal, wood, meat) and VIP from the nomadic shop, using free refresh when available, and reschedules to 00:00 UTC when everything is claimed.",
            "function": lambda instance_index: claim_nomadic_shop_rss_and_vip(instance_index),
            "priority": 18,
            "reschedule_seconds": 4 * 60 * 60,  # 4 hours
            "category": "shop",
        },
        "claim_mystery_shop": {
            "id": "claim_mystery_shop",
            "name": "Claim Mystery Shop Items",
            "description": "Claims redeemable items from the mystery shop (free items always, widgets depending on the user preference), rescheduling to 00:00 UTC when everything is claimed.",
            "function": lambda instance_index: claim_mystery_shop(instance_index),
            "priority": 19,
            "reschedule_seconds": 4 * 60 * 60,  # 4 hours
            "category": "shop",
        },
        "claim_vip_daily_rewards": {
            "id": "claim_vip_daily_rewards",
            "name": "Claim VIP Daily Rewards",
            "description": "Claims the daily rewards from the VIP menu, rescheduling to the next 00:00 UTC once claimed.",
            "function": lambda instance_index: claim_vip_daily_rewards(instance_index),
            "priority": 8,
            "reschedule_seconds": 12 * 60 * 60,  # 12 hours
            "category": "vip",
        },
        "claim_tundra_trek_supplies": {
            "id": "claim_tundra_trek_supplies",
            "name": "Claim Tundra Trek Supplies",
            "description": "Claims the free supplies from the tundra trek, using the on-screen timer to reschedule or 6 hours by default.",
            "function": lambda instance_index: claim_tundra_trek_supplies(instance_index),
            "priority": 2,
            "reschedule_seconds": 6 * 60 * 60,  # 6 hours
            "category": "tundra_trek",
        },
        "start_tundra_trek_idle": {
            "id": "start_tundra_trek_idle",
            "name": "Start Tundra Trek Idle",
            "description": "Clicks the idle button on the tundra trek screen to start idle hunting, always right after claim tundra trek supplies.",
            "function": lambda instance_index: start_tundra_trek_idle(instance_index),
            "priority": 3,
            "reschedule_seconds": 12 * 60 * 60,  # 12 hours fallback, normally pulled by claim_tundra_trek_supplies
            "category": "tundra_trek",
            "run_after": "claim_tundra_trek_supplies",
        },
        "claim_pet_adventure_ally_treasure": {
            "id": "claim_pet_adventure_ally_treasure",
            "name": "Claim Pet Adventure Ally Treasure",
            "description": "Claims the ally treasure from the pet adventure screen, rescheduling to the next 00:00 UTC once claimed.",
            "function": lambda instance_index: claim_pet_adventure_ally_treasure(instance_index),
            "priority": 10,
            "reschedule_seconds": 12 * 60 * 60,  # 12 hours
            "category": "pet_adventure",
        },
        "send_pet_adventure_chests": {
            "id": "send_pet_adventure_chests",
            "name": "Send Pet Adventure Chests",
            "description": "Opens ready chests and starts available chests in pet adventure, prioritizing chest 3. When the daily attempts are exhausted it reschedules to the next 00:00 UTC.",
            "function": lambda instance_index: send_pet_adventure_chests(instance_index),
            "priority": 9,
            "reschedule_seconds": 5 * 60 * 60,  # 5 hours
            "category": "pet_adventure",
        },
        "activate_daily_pet_skills": {
            "id": "activate_daily_pet_skills",
            "name": "Activate Daily Pet Skills",
            "description": "Activates the daily pet skills (wolf, ox, tapir, elk), rescheduling with the shortest on-screen timer or 6 hours by default.",
            "function": lambda instance_index: activate_daily_pet_skills(instance_index),
            "priority": 7,
            "reschedule_seconds": 6 * 60 * 60,  # 6 hours
            "category": "pet_adventure",
        },
        "train_troops": {
            "id": "train_troops",
            "name": "Train Troops",
            "description": "Trains and promotes troops in the 3 camps, rescheduling with the shortest training timer or 6 hours by default.",
            "function": lambda instance_index: train_troops(instance_index),
            "priority": 12,
            "reschedule_seconds": 6 * 60 * 60,  # 6 hours
            "category": "troops",
        },
    }

    # Apply user-defined priorities on top of the defaults
    for task_id, priority in get_task_priorities().items():
        if task_id in task_definitions:
            task_definitions[task_id]["priority"] = priority

    return task_definitions


def get_all_task_ids(log_message=None):
    """Return the IDs of all available tasks, in definition order.

    This is the canonical "all tasks" list, derived from the task
    definitions so newly added tasks are included automatically.

    Returns:
        list: List of all task IDs.
    """
    return list(get_task_definitions(log_message).keys())
