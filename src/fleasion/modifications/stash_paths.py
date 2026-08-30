"""Stable per-install paths for modification originals."""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
from pathlib import Path


def resource_stash_key(resource_dir: Path) -> str:
    """Return a stable per-install key on platforms with same-named roots."""
    resource_dir = Path(resource_dir)
    if sys.platform == 'win32':
        return resource_dir.name

    try:
        resolved = resource_dir.resolve()
    except OSError:
        resolved = resource_dir.absolute()

    if resource_dir.parent.name == 'Contents' and resource_dir.parent.parent.suffix == '.app':
        label = resource_dir.parent.parent.stem
    elif sys.platform.startswith('linux'):
        label = f'LinuxRoblox-{resource_dir.name}'
    elif 'AppleBlox' in resource_dir.parts:
        label = 'AppleBloxBackup'
    elif 'Froststrap' in resource_dir.parts and 'ModBackup' in resource_dir.parts:
        label = f'FroststrapBackup-{resource_dir.name}'
    elif resource_dir.name != 'Resources':
        label = resource_dir.name
    else:
        label = resource_dir.parent.name or 'Resources'
    label = re.sub(r'[^A-Za-z0-9_.-]+', '-', label).strip('-') or 'Resources'
    digest = hashlib.sha256(str(resolved).encode('utf-8')).hexdigest()[:12]
    return f'Resources-{label}-{digest}'


def _is_sober_resource_path(resource_dir: Path) -> bool:
    if 'org.vinegarhq.Sober' in resource_dir.parts:
        return True
    try:
        from fleasion.utils.platform_linux import (  # ruff: ignore[import-outside-top-level]
            is_sober_resource_dir,
        )

        return is_sober_resource_dir(resource_dir)
    except Exception:  # ruff: ignore[blind-except]
        return False


def _migrate_legacy_sober_stash(
    stash_dir: Path,
    resource_dir: Path,
    destination: Path,
) -> None:
    """Move an unambiguous pre-registry Sober stash to its hashed key.

    Old Linux builds keyed originals only by ``asset_overlay`` or ``exe``.
    Those names are safe to claim only for a known Sober resource root.  A
    failed rename falls back to a non-destructive copy, leaving the legacy
    directory available for recovery.
    """
    if not sys.platform.startswith('linux') or not _is_sober_resource_path(resource_dir):
        return
    legacy = stash_dir / resource_dir.name
    if destination.exists() or not legacy.is_dir() or legacy == destination:
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        legacy.rename(destination)
    except OSError:
        try:
            shutil.copytree(legacy, destination)
        except OSError:
            # The caller can still use a fresh hashed stash.  Never delete or
            # overwrite the only legacy copy when migration is uncertain.
            pass


def resource_stash_dir(stash_dir: Path, resource_dir: Path) -> Path:
    stash_root = Path(stash_dir)
    resource_root = Path(resource_dir)
    destination = stash_root / resource_stash_key(resource_root)
    _migrate_legacy_sober_stash(stash_root, resource_root, destination)
    return destination
