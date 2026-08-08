"""Stable per-install paths for modification originals."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


def resource_stash_key(resource_dir: Path) -> str:
    """Return a stable key that cannot collide across macOS app bundles."""
    resource_dir = Path(resource_dir)
    if sys.platform != 'darwin':
        return resource_dir.name

    try:
        resolved = resource_dir.resolve()
    except OSError:
        resolved = resource_dir.absolute()

    if resource_dir.parent.name == 'Contents' and resource_dir.parent.parent.suffix == '.app':
        label = resource_dir.parent.parent.stem
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


def resource_stash_dir(stash_dir: Path, resource_dir: Path) -> Path:
    return Path(stash_dir) / resource_stash_key(resource_dir)
