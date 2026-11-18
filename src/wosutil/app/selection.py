"""Instance selection persisted between sessions."""

from wosutil.config import INSTANCE_SELECTION_FILE
from wosutil.utils import load_json_file, save_json_file


def load_instance_selection() -> dict:
    """Load the persisted instance selection.

    Returns:
        dict: Mapping of instance index (as a string) to its
        ``{"checked": bool, "profile": str}`` entry, plus ``max_emulators``.
    """
    selection = load_json_file(INSTANCE_SELECTION_FILE, default_value={})
    return selection if isinstance(selection, dict) else {}


def save_instance_selection(selection: dict) -> bool:
    """Persist the instance selection.

    Args:
        selection: Selection mapping to write.

    Returns:
        bool: True when the file was written successfully.
    """
    return save_json_file(INSTANCE_SELECTION_FILE, selection)
