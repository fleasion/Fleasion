"""Persistent named profiles for Fleasion custom FastFlags."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ..proxy.addons.custom_fflags import normalize_custom_fflags
from ..utils.paths import FASTFLAG_PROFILES_FOLDER

if TYPE_CHECKING:

    def _object_dict(value: object) -> dict[str, object] | None: ...
else:

    def _object_dict(value: object) -> dict[str, object] | None:
        return value if isinstance(value, dict) else None


class FastFlagProfileManager:
    """Store portable custom-FastFlag profiles as JSON files.

    Profiles deliberately live beside Fleasion's other app data instead of in
    ``settings.json``. This keeps them easy to back up, copy, and share.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = Path(directory) if directory is not None else FASTFLAG_PROFILES_FOLDER

    @staticmethod
    def _normalise_name(name: str) -> str:
        name = str(name or '').strip()
        if name.lower().endswith('.json'):
            name = name[:-5].strip()
        if not name:
            raise ValueError('Profile name cannot be empty.')
        if name in {'.', '..'} or any(character in name for character in '\\/:*?"<>|'):
            raise ValueError('Profile name contains an invalid character.')
        return name

    def _path_for(self, name: str) -> Path:
        return self._directory / f'{self._normalise_name(name)}.json'

    def list_profiles(self) -> list[str]:
        if not self._directory.exists():
            return []
        return sorted(
            (path.stem for path in self._directory.glob('*.json') if path.is_file()),
            key=str.casefold,
        )

    def save(self, name: str, flags: dict[str, object]) -> str:
        path = self._path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(normalize_custom_fflags(flags), indent=2, ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        return path.stem

    def load(self, name: str) -> dict[str, str]:
        path = self._path_for(name)
        try:
            payload_value: object = json.loads(path.read_text(encoding='utf-8'))
            payload = _object_dict(payload_value)
        except FileNotFoundError:
            raise ValueError('Profile no longer exists.') from None
        except json.JSONDecodeError as exc:
            raise ValueError(f'Profile is not valid JSON: {exc.msg}.') from exc
        if payload is None:
            raise ValueError('Profile JSON must contain FastFlag name/value pairs.')
        flags = normalize_custom_fflags(payload)
        if len(flags) != len(payload):
            raise ValueError('Each FastFlag value must be a string, number, or boolean.')
        return flags

    def delete(self, name: str) -> None:
        try:
            self._path_for(name).unlink()
        except FileNotFoundError:
            raise ValueError('Profile no longer exists.') from None

    def rename(self, old_name: str, new_name: str) -> str:
        old_path = self._path_for(old_name)
        new_path = self._path_for(new_name)
        if new_path.exists() and new_path != old_path:
            raise ValueError('A profile with that name already exists.')
        try:
            old_path.rename(new_path)
        except FileNotFoundError:
            raise ValueError('Profile no longer exists.') from None
        return new_path.stem
