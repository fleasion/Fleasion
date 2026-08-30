"""Persistence helpers for discovered Roblox installation directories."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import CONFIG_DIR, ROBLOX_PROCESS, ROBLOX_STUDIO_PROCESS

ROBLOX_DIRS_FILE = CONFIG_DIR / 'roblox_dirs.json'


if TYPE_CHECKING:

    def _object_dict(value: object) -> dict[str, object] | None: ...

    def _object_list(value: object) -> list[object] | None: ...
else:

    def _object_dict(value: object) -> dict[str, object] | None:
        return value if isinstance(value, dict) else None

    def _object_list(value: object) -> list[object] | None:
        return value if isinstance(value, list) else None


def _normalise_roblox_dir(value: str | Path) -> Path | None:
    """Return a valid Roblox install/resource directory, or None."""
    try:
        path = Path(value)
    except TypeError, ValueError:
        return None
    if '\x00' in str(path):
        return None
    if sys.platform == 'darwin':
        if path.name == ROBLOX_PROCESS:
            resources = path.parent.parent / 'Resources'
            return resources if resources.is_dir() else None
        if path.suffix == '.app':
            resources = path / 'Contents' / 'Resources'
            exe = path / 'Contents' / 'MacOS' / ROBLOX_PROCESS
            return resources if resources.is_dir() and exe.is_file() else None
        if path.name == 'MacOS':
            resources = path.parent / 'Resources'
            return resources if resources.is_dir() else None
        if path.name == 'Resources' and path.is_dir():
            return path
        if (path / 'ssl' / 'cacert.pem').is_file() or (path / 'content').is_dir():
            return path if path.is_dir() else None
        return None

    if sys.platform.startswith('linux'):
        try:
            from .platform_linux import SOBER_ASSET_OVERLAY_DIR, SOBER_LEGACY_EXE_DIR

            resolved = path.resolve()
            for candidate in (SOBER_ASSET_OVERLAY_DIR, SOBER_LEGACY_EXE_DIR):
                try:
                    if resolved == candidate.resolve() and path.is_dir():
                        return path
                except OSError:
                    pass
        except Exception:
            pass
        if path.name in {'asset_overlay', 'exe'} and path.is_dir():
            return path
        if (path / 'ssl' / 'cacert.pem').is_file() or (path / 'content').is_dir():
            return path if path.is_dir() else None
        return None

    if path.name.lower() == ROBLOX_PROCESS.lower():
        path = path.parent
    if not path.is_dir():
        return None
    if not (path / ROBLOX_PROCESS).is_file():
        return None
    return path


def is_roblox_studio_resource_dir(path: Path) -> bool:
    """Return True when *path* points at a Roblox Studio resource root."""
    if '\x00' in str(path):
        return False
    try:
        resolved = path.resolve()
    except OSError, ValueError:
        resolved = path

    if sys.platform == 'darwin':
        if resolved.name == 'RobloxStudio.app':
            return True
        if resolved.name == 'Resources':
            try:
                app_bundle = resolved.parent.parent
            except Exception:
                return False
            return app_bundle.name == 'RobloxStudio.app'
        return False

    if sys.platform.startswith('linux'):
        return False

    try:
        return resolved.is_dir() and (resolved / ROBLOX_STUDIO_PROCESS).is_file()
    except OSError:
        return False


def load_saved_roblox_dirs() -> list[Path]:
    """Load previously discovered Roblox directories from disk."""
    if not ROBLOX_DIRS_FILE.exists():
        return []

    try:
        with ROBLOX_DIRS_FILE.open('r', encoding='utf-8') as f:
            payload_value: object = json.load(f)
            payload = _object_dict(payload_value)
    except json.JSONDecodeError, OSError:
        return []

    raw_dirs = _object_list(payload.get('roblox_dirs', [])) if payload is not None else []
    if raw_dirs is None:
        return []

    loaded: list[Path] = []
    seen: set[str] = set()
    for raw in raw_dirs:
        path = _normalise_roblox_dir(raw) if isinstance(raw, str | Path) else None
        if path is None:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        loaded.append(path)
    return loaded


def save_saved_roblox_dirs(dirs: Iterable[Path]) -> None:
    """Persist Roblox directories to disk, ignoring write failures."""
    serialised: list[str] = []
    seen: set[str] = set()

    for raw in dirs:
        path = _normalise_roblox_dir(raw)
        if path is None:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        serialised.append(str(path))

    try:
        ROBLOX_DIRS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ROBLOX_DIRS_FILE.open('w', encoding='utf-8') as f:
            json.dump({'roblox_dirs': serialised}, f, indent=2)
    except OSError:
        pass
