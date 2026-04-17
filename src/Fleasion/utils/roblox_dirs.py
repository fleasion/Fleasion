"""Persistence helpers for discovered Roblox installation directories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .paths import CONFIG_DIR, ROBLOX_PROCESS

ROBLOX_DIRS_FILE = CONFIG_DIR / 'roblox_dirs.json'


def _normalise_roblox_dir(value: str | Path) -> Path | None:
    """Return a valid Roblox Player install directory, or None."""
    path = Path(value)
    if path.name.lower() == ROBLOX_PROCESS.lower():
        path = path.parent
    if not path.is_dir():
        return None
    if not (path / ROBLOX_PROCESS).is_file():
        return None
    return path


def load_saved_roblox_dirs() -> list[Path]:
    """Load previously discovered Roblox directories from disk."""
    if not ROBLOX_DIRS_FILE.exists():
        return []

    try:
        with ROBLOX_DIRS_FILE.open('r', encoding='utf-8') as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    raw_dirs = payload.get('roblox_dirs', []) if isinstance(payload, dict) else []
    if not isinstance(raw_dirs, list):
        return []

    loaded: list[Path] = []
    seen: set[str] = set()
    for raw in raw_dirs:
        path = _normalise_roblox_dir(raw)
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