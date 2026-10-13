"""Compositor blur behind Fleasion windows via GNOME's Blur my Shell extension.

Enabling adds only Fleasion's app ID to an already active application-blur
whitelist. Global blur and opacity settings belong to the desktop user.
Disabling restores the original whitelist, preserving subsequent user edits.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from ast import literal_eval
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from time import monotonic
from typing import Final, Literal, cast

from fleasion.utils.paths import CONFIG_DIR

type BlurStatus = Literal['on', 'off', 'unavailable']

APP_ID: Final = 'fleasion'
_EXTENSION: Final = 'blur-my-shell@aunetx'
_SCHEMA_PATH: Final = '/org/gnome/shell/extensions/blur-my-shell/applications/'
_BACKUP_FILE: Final = CONFIG_DIR / 'blur-my-shell-backup.json'
_TIMEOUT: Final = 3
_REFRESH_SECONDS: Final = 15
log = logging.getLogger(__name__)


def _run(*args: str) -> str | None:
    """Run a desktop CLI tool and return its stdout, or None if unavailable."""
    executable = shutil.which(args[0])
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, *args[1:]],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        log.warning('Desktop command %s failed: %s', args[0], result.stderr.strip())
        return None
    return result.stdout.strip()


def _read(key: str) -> str:
    return _run('dconf', 'read', _SCHEMA_PATH + key) or ''


def _write(key: str, value: str) -> bool:
    return _run('dconf', 'write', _SCHEMA_PATH + key, value) is not None


def _read_list(key: str) -> list[str]:
    return _parse_list(_read(key)) or []


def _parse_list(raw: str) -> list[str] | None:
    if not raw:
        return []
    try:
        parsed: object = literal_eval(raw.removeprefix('@as').strip())
    except ValueError, SyntaxError:
        return None
    if not isinstance(parsed, list):
        return None
    entries = cast('list[object]', parsed)
    if not all(isinstance(item, str) for item in entries):
        return None
    return cast('list[str]', entries)


def _list_value(items: list[str]) -> str:
    # dconf cannot infer the type of an empty list, so it has to be spelled out
    return '[' + ', '.join(repr(item) for item in items) + ']' if items else '@as []'


def _extension_active() -> bool:
    if not sys.platform.startswith('linux'):
        return False
    info = _run('gnome-extensions', 'info', _EXTENSION) or ''
    return any(
        line.partition(':')[2].strip().upper() == 'ACTIVE'
        for line in info.splitlines()
        if line.partition(':')[0].strip().lower() == 'state'
    )


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
    """Cache compositor status and refresh it without blocking window painting."""

    def __init__(self) -> None:
        self._status: BlurStatus | None = None
        self._can_toggle = False
        self._executor: ThreadPoolExecutor | None = None
        self._pending: Future[tuple[BlurStatus, bool]] | None = None
        self._toggle_pending: Future[tuple[BlurStatus, bool]] | None = None
        self._pending_generation = 0
        self._generation = 0
        self._next_refresh = 0.0

    def status(self, *, refresh: bool = False) -> BlurStatus:
        if self._status is None or refresh:
            self._generation += 1
            self._status, self._can_toggle = self._probe()
        return self._status

    def cached_status(self) -> BlurStatus:
        """Read the latest known state without invoking desktop commands in a paint event."""
        return self._status or 'unavailable'

    def can_toggle(self) -> bool:
        """Read cached availability without blocking Qt on a compositor probe."""
        return self._can_toggle

    @property
    def toggle_pending(self) -> bool:
        """Whether a compositor toggle has not yet been consumed by the GUI."""
        return self._toggle_pending is not None

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='glass-blur')
        return self._executor

    def toggle(self) -> bool:
        """Queue one toggle after the current probe, without blocking the caller."""
        if (
            self._toggle_pending is not None
            or not self._can_toggle
            or self._status == 'unavailable'
        ):
            return False
        self._generation += 1
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None
        self._toggle_pending = self._ensure_executor().submit(self._toggle_work)
        return True

    @staticmethod
    def _toggle_work() -> tuple[BlurStatus, bool]:
        """Recheck permission and make the change entirely on the worker thread."""
        _status, can_toggle = DesktopBlur._probe()
        if can_toggle:
            if DesktopBlur.owns_entry():
                DesktopBlur._restore_whitelist()
            else:
                DesktopBlur._enable_whitelist()
        return DesktopBlur._probe()

    @staticmethod
    def owns_entry() -> bool:
        backup = _load_backup()
        return 'whitelist' in backup and 'whitelist_applied' in backup

    @staticmethod
    def _probe() -> tuple[BlurStatus, bool]:
        if not _extension_active():
            return 'unavailable', False
        blurring = _read('blur') == 'true'
        all_apps = _read('enable-all') == 'true'
        listed = APP_ID in _read_list('whitelist')
        covered = all_apps or listed
        owned = DesktopBlur.owns_entry()
        can_toggle = owned or (
            blurring and not all_apps and not listed and not _BACKUP_FILE.exists()
        )
        return ('on' if blurring and covered else 'off'), can_toggle

    def poll_refresh(self) -> bool:
        """Schedule and collect compositor checks on the GUI thread without waiting.

        Returns True when the cached status or toggle availability changes.
        """
        changed = False
        toggle = self._toggle_pending
        if toggle is not None:
            if not toggle.done():
                return False
            self._toggle_pending = None
            self._next_refresh = monotonic() + _REFRESH_SECONDS
            try:
                self._status, self._can_toggle = toggle.result()
            except CancelledError, OSError, ValueError, subprocess.SubprocessError:
                log.exception('Could not toggle compositor blur')
                self._next_refresh = 0.0  # Recheck partial writes asynchronously
            return True  # The GUI must re-enable its button even if status did not change

        pending = self._pending
        if pending is not None:
            if not pending.done():
                return False
            self._pending = None
            try:
                result = pending.result()
            except OSError, ValueError, subprocess.SubprocessError:
                log.exception('Could not refresh compositor blur status')
            else:
                if self._pending_generation == self._generation:
                    previous = self._status, self._can_toggle
                    self._status, self._can_toggle = result
                    changed = previous != result
        if self._pending is None and monotonic() >= self._next_refresh:
            self._pending_generation = self._generation
            self._pending = self._ensure_executor().submit(self._probe)
            self._next_refresh = monotonic() + _REFRESH_SECONDS
        return changed

    def stop_polling(self) -> None:
        """Cancel unused work after Glass is disabled without waiting for desktop commands."""
        self._generation += 1
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        # A running toggle is allowed to finish. Keep its future so a later
        # theme activation can collect the result before accepting another toggle
        self._pending = None
        self._next_refresh = 0.0

    def enable(self) -> bool:
        if self.status(refresh=True) == 'on':
            return True
        if not self.can_toggle():
            return False
        if not self._enable_whitelist():
            return False
        return self.status(refresh=True) == 'on'

    @staticmethod
    def _enable_whitelist() -> bool:
        if _read('blur') != 'true' or _read('enable-all') == 'true':
            return False
        original = _run('dconf', 'read', _SCHEMA_PATH + 'whitelist')
        if original is None or (whitelist := _parse_list(original)) is None:
            return False
        if APP_ID in whitelist:
            return False
        applied = _list_value([*whitelist, APP_ID])
        if not _BACKUP_FILE.exists():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            backup = {'whitelist': original, 'whitelist_applied': applied}
            _BACKUP_FILE.write_text(json.dumps(backup), encoding='utf-8')
        return _write('whitelist', applied)

    def disable(self) -> None:
        if self._restore_whitelist():
            self.status(refresh=True)

    @staticmethod
    def _restore_whitelist() -> bool:
        values = _load_backup()
        if 'whitelist' not in values or 'whitelist_applied' not in values:
            return False
        current = _run('dconf', 'read', _SCHEMA_PATH + 'whitelist')
        if current is None:
            return False
        whitelist = _parse_list(current)
        if whitelist is None:
            return False
        if whitelist == _parse_list(values['whitelist_applied']):
            original = values['whitelist']
            restored = (
                _write('whitelist', original)
                if original
                else _run('dconf', 'reset', _SCHEMA_PATH + 'whitelist') is not None
            )
        else:
            # Remove only our added entry, retaining subsequent desktop edits
            restored = APP_ID not in whitelist or _write(
                'whitelist', _list_value([item for item in whitelist if item != APP_ID])
            )
        if restored:
            _BACKUP_FILE.unlink(missing_ok=True)
        return restored


blur = DesktopBlur()
