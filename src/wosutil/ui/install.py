"""Install or verify the pinned desktop UI bundle."""

import hmac
import json
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from wosutil.ui.bundle import is_safe_relative_path, sha256_bytes
from wosutil.ui.contract import API_VERSION

MARKER_NAME = ".installed.json"
FETCH_TIMEOUT_SECONDS = 30.0
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class InstallError(RuntimeError):
    """Raised when a UI bundle cannot be installed or verified."""


def fetch_bytes(url: str) -> bytes:
    """Download a URL and return its raw bytes.

    Args:
        url: Absolute HTTP(S) or ``file://`` URL.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "WosUtil-UI/1"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            return response.read()
    except OSError as exc:
        raise InstallError(f"Could not fetch {url}: {exc}") from exc


def manifest_files(manifest: dict) -> dict[str, str]:
    """Validate a manifest and return its ``path -> sha256`` files map.

    Args:
        manifest: Parsed bundle manifest.

    Raises:
        InstallError: When the manifest is incompatible or malformed.
    """
    if manifest.get("apiVersion") != API_VERSION:
        raise InstallError(f"Bundle API version {manifest.get('apiVersion')!r} does not match {API_VERSION}.")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise InstallError("Bundle manifest has no files.")

    clean = {}
    for name, digest in files.items():
        if not isinstance(name, str) or not is_safe_relative_path(name):
            raise InstallError(f"Unsafe bundle path: {name!r}.")
        if not isinstance(digest, str) or not _SHA256_PATTERN.match(digest.lower()):
            raise InstallError(f"Invalid hash for {name!r}.")
        clean[name] = digest.lower()

    entry = manifest.get("entry", "index.html")
    if not isinstance(entry, str) or entry not in clean:
        raise InstallError("Bundle manifest does not list its entry file.")
    return clean


def install_from_manifest(manifest_url: str, expected_sha256: str, out: Path) -> dict:
    """Download and install the bundle described by a pinned manifest.

    Args:
        manifest_url: Published manifest URL.
        expected_sha256: SHA-256 of the exact manifest bytes pinned in
            ``ui.lock.json``.
        out: Destination directory.

    Returns:
        dict: Installation marker written next to the bundle.

    Raises:
        InstallError: When the manifest or any file fails verification.
    """
    raw_manifest = fetch_bytes(manifest_url)
    actual_sha256 = sha256_bytes(raw_manifest)
    if not hmac.compare_digest(actual_sha256, expected_sha256.lower()):
        raise InstallError(f"Manifest hash mismatch: expected {expected_sha256}, got {actual_sha256}. Update ui.lock.json after reviewing the new bundle.")

    try:
        manifest = json.loads(raw_manifest)
    except ValueError as exc:
        raise InstallError(f"Invalid manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise InstallError("Invalid manifest: expected an object.")

    files = manifest_files(manifest)
    base_url = manifest_url.rsplit("/", 1)[0] + "/"
    contents = {}
    for name, digest in files.items():
        data = fetch_bytes(base_url + quote(name))
        if not hmac.compare_digest(sha256_bytes(data), digest):
            raise InstallError(f"Hash mismatch for {name}.")
        contents[name] = data
    contents["manifest.json"] = raw_manifest

    marker = {
        "source": "remote",
        "manifestUrl": manifest_url,
        "manifestSha256": expected_sha256.lower(),
        "apiVersion": API_VERSION,
        "uiVersion": str(manifest.get("uiVersion") or "dev"),
        "files": {name: sha256_bytes(data) for name, data in contents.items()},
    }
    _install(contents, out, marker)
    return marker


def install_from_directory(source: Path, out: Path) -> dict:
    """Install a bundle from a local build directory.

    Args:
        source: Directory containing ``index.html`` (for example
            ``wosutil-site/dist/ui``).
        out: Destination directory.

    Returns:
        dict: Installation marker written next to the bundle.
    """
    source = Path(source)
    if not (source / "index.html").is_file():
        raise InstallError(f"{source} does not contain index.html. Build the UI first.")

    contents = {}
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        name = path.relative_to(source).as_posix()
        if name == MARKER_NAME:
            continue
        contents[name] = path.read_bytes()

    manifest = {}
    if "manifest.json" in contents:
        try:
            parsed = json.loads(contents["manifest.json"])
            manifest = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            manifest = {}

    marker = {
        "source": "local",
        "path": str(source.resolve()),
        "apiVersion": API_VERSION,
        "uiVersion": str(manifest.get("uiVersion") or "dev"),
        "files": {name: sha256_bytes(data) for name, data in contents.items()},
    }
    _install(contents, out, marker)
    return marker


def verify_installed(out: Path) -> dict:
    """Verify the installed bundle against its marker.

    Args:
        out: Installed bundle directory.

    Returns:
        dict: Installation marker.

    Raises:
        InstallError: When a file is missing, changed or incompatible.
    """
    marker_path = Path(out) / MARKER_NAME
    if not marker_path.is_file():
        raise InstallError(f"{out} has no {MARKER_NAME}; run the UI fetch step first.")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError(f"Invalid {MARKER_NAME}: {exc}") from exc
    if not isinstance(marker, dict) or marker.get("apiVersion") != API_VERSION:
        raise InstallError("Installed bundle has an incompatible API version.")
    files = marker.get("files")
    if not isinstance(files, dict):
        raise InstallError(f"Invalid {MARKER_NAME}: missing files map.")

    for name, digest in files.items():
        path = Path(out) / name
        if not isinstance(name, str) or not is_safe_relative_path(name):
            raise InstallError(f"Unsafe installed path: {name!r}.")
        if not path.is_file():
            raise InstallError(f"Missing installed file: {name}.")
        if sha256_bytes(path.read_bytes()) != digest:
            raise InstallError(f"Installed file changed: {name}.")
    return marker


def installed_manifest_sha256(out: Path) -> Optional[str]:
    """Return the manifest hash recorded by a previous remote installation."""
    marker_path = Path(out) / MARKER_NAME
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = marker.get("manifestSha256") if isinstance(marker, dict) else None
    return value if isinstance(value, str) else None


def _install(contents: dict[str, bytes], out: Path, marker: dict) -> None:
    """Write a validated file map to ``out``, replacing the previous bundle."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="wosutil-ui-", dir=str(out.parent)))
    try:
        for name, data in contents.items():
            if not is_safe_relative_path(name):
                raise InstallError(f"Unsafe bundle path: {name!r}.")
            target = temp_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (temp_dir / MARKER_NAME).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        try:
            if out.exists():
                shutil.rmtree(out)
            temp_dir.rename(out)
        except OSError as exc:
            raise InstallError(f"Could not replace {out}: {exc}. Close the app and try again.") from exc
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
