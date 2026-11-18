"""Instance cache shared by the emulator backends and the GUI.

The backends write their discovered instances here so the GUI can render the
instance list without re-scanning the emulator on every refresh.
"""

from wosutil.config import INSTANCE_CACHE_FILE
from wosutil.utils import load_json_file, save_json_file


def save_instance_cache(instances):
    """Save the list of emulator instances to the cache file.

    Args:
        instances (list): List of instance dictionaries with 'index' and 'name'.
    """
    save_json_file(INSTANCE_CACHE_FILE, instances)


def load_instance_cache():
    """Load and validate the cached emulator instance list.

    Returns:
        list: Valid instance dictionaries, or an empty list when the cache is
        missing, malformed or contains no usable entry.
    """
    instances = load_json_file(INSTANCE_CACHE_FILE, default_value=[])
    if not isinstance(instances, list):
        return []
    valid_instances = []
    seen_indices = set()
    for instance in instances:
        if not isinstance(instance, dict):
            continue
        index = instance.get("index")
        name = instance.get("name")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            continue
        if not isinstance(name, str) or not name.strip() or index in seen_indices:
            continue
        valid_instances.append({"index": index, "name": name})
        seen_indices.add(index)
    return valid_instances
