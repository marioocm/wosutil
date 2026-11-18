"""Locate, validate and hash the desktop UI bundle."""

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import unquote

from wosutil.ui.contract import API_VERSION


@dataclass(frozen=True)
class UiBundle:
    """A validated UI bundle ready to be served.

    Attributes:
        path: Directory containing ``index.html`` and its assets.
        ui_version: Version declared by the bundle manifest (``dev`` when the
            bundle has no manifest, e.g. a local ``dist-app`` build).
        api_version: Backend API version the bundle expects.
        manifest: Parsed manifest, or None for a manifest-less dev build.
    """

    path: Path
    ui_version: str
    api_version: int
    manifest: Optional[dict]


def cache_dir() -> Path:
    """Return the per-user cache directory used by the desktop shell."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "WosUtil"


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hexadecimal SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the lowercase hexadecimal SHA-256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_safe_relative_path(value: str) -> bool:
    """Return True when a manifest path stays inside the bundle directory.

    Args:
        value: POSIX-style relative path taken from a manifest.
    """
    if not value or "\\" in value or value.startswith("/"):
        return False
    if re.match(r"^[A-Za-z]:", value):
        return False
    parts = PurePosixPath(value).parts
    return bool(parts) and all(part not in ("", ".", "..") for part in parts)


def safe_join(root: Path, url_path: str) -> Optional[Path]:
    """Resolve a URL path inside ``root`` and return it only when it is a file.

    Args:
        root: Bundle directory.
        url_path: Request path such as ``/_astro/app.css``.

    Returns:
        The resolved file, or None for traversal attempts, directories and
        missing files.
    """
    relative = unquote(url_path).lstrip("/")
    if not relative:
        relative = "index.html"
    if "\x00" in relative:
        return None
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def load_ui_bundle(path: Optional[Path]) -> Optional[UiBundle]:
    """Validate a bundle directory.

    A manifest-less directory with an ``index.html`` is accepted as a dev
    build. A manifest with a different API version is rejected so a stale or
    incompatible front-end is never served.

    Args:
        path: Candidate bundle directory.
    """
    if path is None:
        return None
    if not (path / "index.html").is_file():
        return None

    manifest = None
    manifest_path = path / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(manifest, dict) or manifest.get("apiVersion") != API_VERSION:
            return None

    ui_version = str(manifest.get("uiVersion") or "dev") if manifest else "dev"
    return UiBundle(path=path, ui_version=ui_version, api_version=API_VERSION, manifest=manifest)


def resolve_ui_bundle() -> Optional[UiBundle]:
    """Find the UI bundle to serve.

    Order: the ``WOSUTIL_UI_DIR`` override, the downloaded cache, the bundle
    shipped with the executable, and finally the development build output.
    """
    override = os.environ.get("WOSUTIL_UI_DIR")
    if override:
        return load_ui_bundle(Path(override).expanduser())

    cached = load_ui_bundle(cache_dir() / "ui" / "current")
    if cached is not None:
        return cached

    if getattr(sys, "frozen", False):
        return load_ui_bundle(Path(getattr(sys, "_MEIPASS", "")) / "webui")

    return load_ui_bundle(Path(__file__).resolve().parents[1] / "webui" / "static")
