#!/usr/bin/env python
"""Download or copy the desktop UI bundle pinned by ``ui.lock.json``."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wosutil.ui.contract import API_VERSION  # noqa: E402
from wosutil.ui.install import (  # noqa: E402
    InstallError,
    install_from_directory,
    install_from_manifest,
    installed_manifest_sha256,
    verify_installed,
)

DEFAULT_LOCK = ROOT / "ui.lock.json"
DEFAULT_OUT = ROOT / "src" / "wosutil" / "webui" / "static"


def load_lock(path: Path) -> dict:
    """Load and validate the lock file.

    Args:
        path: ``ui.lock.json`` path.

    Raises:
        InstallError: When the file is missing, invalid or targets another API.
    """
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InstallError(f"Could not read {path}: {exc}") from exc
    except ValueError as exc:
        raise InstallError(f"Invalid {path}: {exc}") from exc
    if not isinstance(lock, dict):
        raise InstallError(f"Invalid {path}: expected an object.")

    url = lock.get("manifestUrl")
    digest = lock.get("manifestSha256")
    if not isinstance(url, str) or not url:
        raise InstallError(f"{path} has no manifestUrl.")
    if not isinstance(digest, str) or len(digest) != 64:
        raise InstallError(f"{path} has no valid manifestSha256.")
    if lock.get("apiVersion") != API_VERSION:
        raise InstallError(f"{path} targets API {lock.get('apiVersion')} but this build implements {API_VERSION}.")
    return lock


def main(argv=None) -> int:
    """Run the UI bundle fetch.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--from-dir", type=Path, help="Install a local build instead of downloading.")
    parser.add_argument("--check", action="store_true", help="Verify the installed bundle and exit.")
    parser.add_argument("--force", action="store_true", help="Re-download even when the pin is already installed.")
    args = parser.parse_args(argv)

    try:
        if args.check:
            marker = verify_installed(args.out)
            print(f"UI bundle OK: {marker.get('uiVersion', 'dev')} ({len(marker.get('files', {}))} files)")
            return 0

        if args.from_dir is not None:
            marker = install_from_directory(args.from_dir, args.out)
            print(f"Installed local UI bundle {marker['uiVersion']} into {args.out}")
            return 0

        lock = load_lock(args.lock)
        if not args.force and installed_manifest_sha256(args.out) == lock["manifestSha256"].lower():
            marker = verify_installed(args.out)
            print(f"UI bundle already installed: {marker.get('uiVersion', 'dev')}")
            return 0

        marker = install_from_manifest(lock["manifestUrl"], lock["manifestSha256"], args.out)
        print(f"Installed UI bundle {marker['uiVersion']} from {lock['manifestUrl']}")
        return 0
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
