"""Persistence helpers for discovered Roblox installation directories."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import CONFIG_DIR, ROBLOX_PROCESS, ROBLOX_STUDIO_PROCESS

ROBLOX_DIRS_FILE = CONFIG_DIR / 'roblox_dirs.json'


if TYPE_CHECKING:

    from collections.abc import Iterable
    def _object_dict(value: object) -> dict[str, object] | None: ...

    def _object_list(value: object) -> list[object] | None: ...
else:

    def _object_dict(value: object) -> dict[str, object] | None:
        return value if isinstance(value, dict) else None

    def _object_list(value: object) -> list[object] | None:
        return value if isinstance(value, list) else None


def _normalise_macos_roblox_dir(path: Path) -> Path | None:
    if path.name == ROBLOX_PROCESS:
        resources = path.parent.parent / 'Resources'
        return resources if resources.is_dir() else None
    if path.suffix == '.app':
        resources = path / 'Contents' / 'Resources'
        executable = path / 'Contents' / 'MacOS' / ROBLOX_PROCESS
        return resources if resources.is_dir() and executable.is_file() else None
    if path.name == 'MacOS':
        resources = path.parent / 'Resources'
        return resources if resources.is_dir() else None
    if path.name == 'Resources' and path.is_dir():
        return path
    if (path / 'ssl' / 'cacert.pem').is_file() or (path / 'content').is_dir():
        return path if path.is_dir() else None
    return None


def _matches_known_sober_dir(path: Path) -> bool:
    try:
        platform_linux = importlib.import_module('.platform_linux', __package__)
        resolved = path.resolve()
    except (ImportError, AttributeError, OSError):
        return False

    for candidate in (
        platform_linux.SOBER_ASSET_OVERLAY_DIR,
        platform_linux.SOBER_LEGACY_EXE_DIR,
    ):
        try:
            if resolved == candidate.resolve() and path.is_dir():
                return True
        except OSError:
            continue
    return False


def _normalise_linux_roblox_dir(path: Path) -> Path | None:
    if _matches_known_sober_dir(path):
        return path
    if path.name in {'asset_overlay', 'exe'} and path.is_dir():
        return path
    if (path / 'ssl' / 'cacert.pem').is_file() or (path / 'content').is_dir():
        return path if path.is_dir() else None
    return None


def _normalise_windows_roblox_dir(path: Path) -> Path | None:
    install_dir = path.parent if path.name.lower() == ROBLOX_PROCESS.lower() else path
    if install_dir.is_dir() and (install_dir / ROBLOX_PROCESS).is_file():
        return install_dir
    return None


def _normalise_roblox_dir(value: str | Path) -> Path | None:
    """Return a valid Roblox install/resource directory, or None."""
    try:
        path = Path(value)
    except (TypeError, ValueError):
        return None
    if '\x00' in str(path):
        return None
    if sys.platform == 'darwin':
        return _normalise_macos_roblox_dir(path)
    if sys.platform.startswith('linux'):
        return _normalise_linux_roblox_dir(path)
    return _normalise_windows_roblox_dir(path)


def _is_macos_studio_resource_dir(path: Path) -> bool:
    if path.name == 'RobloxStudio.app':
        return True
    if path.name == 'Resources':
        return path.parent.parent.name == 'RobloxStudio.app'
    return False


def is_roblox_studio_resource_dir(path: Path) -> bool:
    """Return True when *path* points at a Roblox Studio resource root."""
    if '\x00' in str(path):
        return False
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        resolved = path

    if sys.platform == 'darwin':
        return _is_macos_studio_resource_dir(resolved)
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
