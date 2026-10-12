"""Compositor blur behind Fleasion windows via GNOME's Blur my Shell extension.

Enabling adds Fleasion's app ID to the extension's application whitelist and
backs up the extension's previous application-blur values, so disabling
restores them exactly.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - runs fixed desktop CLI tools
import sys
from typing import Final, Literal, cast

from ...utils.paths import CONFIG_DIR

type BlurStatus = Literal['on', 'off', 'unavailable']

APP_ID: Final = 'fleasion'
_EXTENSION: Final = 'blur-my-shell@aunetx'
_SCHEMA_PATH: Final = '/org/gnome/shell/extensions/blur-my-shell/applications/'
_BACKED_UP_KEYS: Final = ('blur', 'opacity', 'dynamic-opacity')
_BACKUP_FILE: Final = CONFIG_DIR / 'blur-my-shell-backup.json'
_TIMEOUT: Final = 3


def _run(*args: str) -> str | None:
    """Run a desktop CLI tool and return its stdout, or None if unavailable."""
    executable = shutil.which(args[0])
    if executable is None:
        return None
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed tool, arguments built here
            [executable, *args[1:]],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return result.stdout.strip()


def _read(key: str) -> str:
    return _run('dconf', 'read', _SCHEMA_PATH + key) or ''


def _write(key: str, value: str) -> None:
    _run('dconf', 'write', _SCHEMA_PATH + key, value)


def _read_list(key: str) -> list[str]:
    raw = _read(key).replace('@as', '').strip().strip('[]')
    return [item.strip().strip('\'"') for item in raw.split(',') if item.strip()]


def _write_list(key: str, items: list[str]) -> None:
    # dconf cannot infer the type of an empty list, so it has to be spelled out
    value = '[' + ', '.join(f"'{item}'" for item in items) + ']' if items else '@as []'
    _write(key, value)


def _extension_active() -> bool:
    if not sys.platform.startswith('linux'):
        return False
    info = _run('gnome-extensions', 'info', _EXTENSION) or ''
    return 'ACTIVE' in info or 'ENABLED' in info


def _load_backup() -> dict[str, str]:
    try:
        saved: object = json.loads(_BACKUP_FILE.read_text(encoding='utf-8'))
    except OSError, ValueError:
        return {}
    if not isinstance(saved, dict):
        return {}
    entries = cast('dict[object, object]', saved)
    return {str(key): str(value) for key, value in entries.items()}


class DesktopBlur:
    """Status is cached because it is read on every window paint."""

    def __init__(self) -> None:
        self._status: BlurStatus | None = None

    def status(self, *, refresh: bool = False) -> BlurStatus:
        if self._status is None or refresh:
            self._status = self._probe()
        return self._status

    @staticmethod
    def _probe() -> BlurStatus:
        if not _extension_active():
            return 'unavailable'
        blurring = _read('blur') == 'true'
        covered = _read('enable-all') == 'true' or APP_ID in _read_list('whitelist')
        return 'on' if blurring and covered else 'off'

    def enable(self) -> bool:
        if not _extension_active():
            return False
        if not _BACKUP_FILE.is_file():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            backup = {key: _read(key) for key in _BACKED_UP_KEYS}
            _BACKUP_FILE.write_text(json.dumps(backup), encoding='utf-8')
        whitelist = _read_list('whitelist')
        if APP_ID not in whitelist:
            _write_list('whitelist', [*whitelist, APP_ID])
        _write('blur', 'true')
        # Fleasion paints its own alpha, so stop the extension fading text as well
        _write('opacity', '255')
        _write('dynamic-opacity', 'false')
        return self.status(refresh=True) == 'on'

    def disable(self) -> None:
        _write_list('whitelist', [item for item in _read_list('whitelist') if item != APP_ID])
        if _BACKUP_FILE.is_file():
            values = _load_backup()
            for key in _BACKED_UP_KEYS:
                if value := values.get(key):
                    _write(key, value)
                else:
                    _run('dconf', 'reset', _SCHEMA_PATH + key)
            _BACKUP_FILE.unlink(missing_ok=True)
        self.status(refresh=True)


blur = DesktopBlur()
