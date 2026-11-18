"""Profile use cases shared by every view."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProfileResult:
    """Outcome of a profile command."""

    ok: bool
    error: Optional[str] = None


def save_profile(profile_manager, name: str, task_ids: list, known_task_ids=None) -> ProfileResult:
    """Create or replace a profile.

    Args:
        profile_manager: Manager owning the profiles and their persistence.
        name: Profile name typed by the user.
        task_ids: Task ids selected for the profile.
        known_task_ids: Optional set used to drop ids of removed tasks.

    Returns:
        ProfileResult: Whether the profile was saved.
    """
    name = (name or "").strip()
    if not name:
        return ProfileResult(False, "Profile name is required.")
    if known_task_ids is not None:
        task_ids = [task_id for task_id in task_ids if task_id in known_task_ids]
    profile_manager.profiles[name] = list(task_ids)
    if not profile_manager.save_profiles():
        return ProfileResult(False, "Could not save the profile.")
    return ProfileResult(True)


def delete_profile(profile_manager, name: str) -> ProfileResult:
    """Delete a profile.

    Args:
        profile_manager: Manager owning the profiles and their persistence.
        name: Profile name to delete.

    Returns:
        ProfileResult: Whether the profile was deleted.
    """
    if not name or name not in profile_manager.profiles:
        return ProfileResult(False, "No profile selected to be deleted.")
    del profile_manager.profiles[name]
    if not profile_manager.save_profiles():
        return ProfileResult(False, "Could not delete the profile.")
    return ProfileResult(True)
