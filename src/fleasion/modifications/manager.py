"""ModificationManager — core eager-apply, stash & restore engine.

Owns the concept of a *modification entry*: each entry maps a target
path inside the Roblox directory to a source file (local, asset ID, or
bundled).  Files are written eagerly (immediately), and originals are
stashed so they can be restored on exit / shutdown.
"""

from __future__ import annotations

import json
import ntpath
import os
import re
import shlex
import shutil
import stat
import sys
import threading
import uuid
from collections.abc import Callable  # ruff: ignore[typing-only-standard-library-import]
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from collections.abc import Iterable


class _RegistryKey(Protocol):
    def __enter__(self) -> _RegistryKey: ...  # ruff: ignore[non-self-return-type]
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool | None: ...


class _WinregLike(Protocol):
    HKEY_CURRENT_USER: object
    REG_SZ: int
    OpenKey: Callable[[object, str], _RegistryKey]
    EnumKey: Callable[[_RegistryKey, int], str]
    QueryValueEx: Callable[[_RegistryKey, str], tuple[object, int]]


def _winreg_module() -> _WinregLike:
    return cast('_WinregLike', __import__('winreg'))


import contextlib  # ruff: ignore[module-import-not-at-top-of-file]

from fleasion.cache.tools.ktx_to_png import (  # ruff: ignore[module-import-not-at-top-of-file]
    strip_prefixed_ktx,
)
from fleasion.utils import (  # ruff: ignore[module-import-not-at-top-of-file]
    CONFIG_DIR,
    LOCAL_APPDATA,
    ROBLOX_PROCESS,
    format_count,
    get_roblox_player_exe_path,
    log_buffer,
)
from fleasion.utils.roblox_dirs import (  # ruff: ignore[module-import-not-at-top-of-file]
    is_roblox_studio_resource_dir,
    load_saved_roblox_dirs,
    save_saved_roblox_dirs,
)
from fleasion.utils.threading import run_in_thread  # ruff: ignore[module-import-not-at-top-of-file]

from .fflag_manager import (  # ruff: ignore[module-import-not-at-top-of-file]
    FastFlagManager,
    client_settings_paths_for_resource_dir,
)
from .font_utils import (  # ruff: ignore[module-import-not-at-top-of-file]
    CUSTOM_FONT_REL,
    FAMILIES_REL,
    apply_custom_font,
    restore_font_families,
    validate_font_bytes,
)
from .global_settings_manager import (  # ruff: ignore[module-import-not-at-top-of-file]
    GlobalSettingsManager,
)
from .platform_targets import (  # ruff: ignore[module-import-not-at-top-of-file]
    canonical_target_path,
    content_prefixed_resource_root,
    read_current_platform_original_asset,
    read_current_platform_original_directory,
    target_path_for_resource_dir,
)
from .stash_paths import resource_stash_dir  # ruff: ignore[module-import-not-at-top-of-file]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MODIFICATIONS_JSON = CONFIG_DIR / 'modifications.json'
MOD_ORIGINALS_DIR = CONFIG_DIR / 'ModOriginals'
MOD_CACHE_DIR = CONFIG_DIR / 'ModCache'
READ_ONLY_STATE_FILE = CONFIG_DIR / 'read_only_modes.json'


class _NewModificationEntry(TypedDict, total=False):
    display_name: str
    target_path: str
    source_type: str | None
    source_value: str | None
    status: str
    error_message: str | None
    converted_cache_path: str | None
    _is_font: bool
    _apply_gen: int


class _ModificationEntry(_NewModificationEntry):
    id: str


class _FastFlagSettings(TypedDict, total=False):
    rendering_mode: str
    msaa: str
    disable_dpi_scale: bool
    alt_enter_fullscreen: bool
    texture_quality: str
    mesh_lod_enabled: bool
    mesh_lod: int
    frm_quality_enabled: bool
    frm_quality: int
    grey_sky: bool
    pause_voxelizer: bool
    grass_max: int | None
    grass_min: int | None
    grass_motion: int | None
    framerate_cap: int | str | None


class _GlobalSettings(TypedDict, total=False):
    framerate_cap: int | str | None


class _ModificationData(TypedDict):
    entries: list[_ModificationEntry]
    fast_flags_enabled: bool
    fast_flags: _FastFlagSettings
    global_settings: _GlobalSettings


class _GetRoblosecurity(Protocol):
    def __call__(self, *, wait: bool = False) -> str | None: ...


class _FetchAssetWithPlaceIdRetry(Protocol):
    def __call__(
        self,
        asset_id: str,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[bytes | None, int | None]: ...


class _FastFlagManagerLike(Protocol):
    def write(self, settings: _FastFlagSettings) -> set[Path]: ...

    def restore(self) -> None: ...

    def reassert_macos_bootstrapper_flags(self, settings: _FastFlagSettings) -> int: ...


def normalise_target_path(target_path: str | Path) -> Path:
    """Return a safe relative target path using platform path separators.

    Built-in modification entries are stored with Windows-style backslashes.
    On POSIX, pathlib treats those as literal filename characters, so normalize
    before joining with a Roblox resource directory or stash directory.
    """
    text = str(target_path or '').strip()
    if not text:
        msg = 'Target path is empty'
        raise ValueError(msg)
    text = text.replace('\\', '/')
    drive, _tail = ntpath.splitdrive(text)
    if drive or text.startswith('/'):
        msg = 'Target path must be relative to the Roblox resources directory'
        raise ValueError(msg)
    parts = [part for part in text.split('/') if part and part != '.']
    if not parts:
        msg = 'Target path is empty'
        raise ValueError(msg)
    if any(part == '..' for part in parts):
        msg = 'Target path cannot contain ".." segments'
        raise ValueError(msg)
    return Path(*parts)


def target_path_for_roblox_dir(target_path: str | Path, roblox_dir: Path) -> Path:
    """Return a safe target path relative to one client resource root."""
    return normalise_target_path(target_path_for_resource_dir(target_path, roblox_dir))


def _font_helper_dirs(roblox_dirs: Iterable[Path]) -> list[Path]:
    """Adapt root-aware directories for legacy ``content/...`` font helpers."""
    result: list[Path] = []
    seen: set[str] = set()
    for roblox_dir in roblox_dirs:
        root = content_prefixed_resource_root(roblox_dir)
        try:
            key = str(root.resolve()).casefold()
        except OSError:
            key = str(root).casefold()
        if key not in seen:
            seen.add(key)
            result.append(root)
    return result


def _clear_read_only(path: Path) -> None:
    """Clear a file's read-only bit before Fleasion writes/restores it."""
    if not path.exists():
        return
    try:
        mode = path.stat().st_mode
        if mode & stat.S_IWRITE:
            return
        path.chmod(mode | stat.S_IWRITE)
    except OSError:
        pass


def _set_read_only(path: Path) -> None:
    """Set a file's read-only bit without changing other mode bits."""
    if not path.is_file():
        return
    try:
        mode = path.stat().st_mode
        if not (mode & stat.S_IWRITE):
            return
        path.chmod(mode & ~stat.S_IWRITE)
    except OSError:
        pass


def _instance_attr(obj: object, name: str, default: object = None) -> object:
    """Read attributes safely on partially initialized QObject test doubles."""
    try:
        return object.__getattribute__(obj, name)  # ruff: ignore[unnecessary-dunder-call]
    except AttributeError, RuntimeError:
        return default


# ---------------------------------------------------------------------------
# Roblox directory discovery  (mirrors proxy/master.py::_find_roblox_dirs)
# ---------------------------------------------------------------------------


def _find_roblox_dirs() -> list[Path]:
    """Locate Roblox resource directories that can receive file modifications."""
    if sys.platform == 'darwin':
        from fleasion.utils.platform_macos import (  # ruff: ignore[import-outside-top-level]
            find_roblox_resource_dirs,
        )
    elif sys.platform.startswith('linux'):
        from fleasion.utils.platform_linux import (  # ruff: ignore[import-outside-top-level]
            find_roblox_resource_dirs,
        )
    else:
        find_roblox_resource_dirs = None

    if find_roblox_resource_dirs is not None:
        found: list[Path] = []
        seen: set[str] = set()

        def _add_non_windows(path: Path) -> None:
            if '\x00' in str(path) or is_roblox_studio_resource_dir(path):
                return
            try:
                key = str(path.resolve()).lower()
            except OSError, ValueError:
                key = str(path).lower()
            if key in seen:
                return
            seen.add(key)
            found.append(path)

        for roblox_dir in find_roblox_resource_dirs(include_studio=False):
            _add_non_windows(roblox_dir)
        for cached_dir in load_saved_roblox_dirs():
            _add_non_windows(cached_dir)
        save_saved_roblox_dirs(found)
        if sys.platform == 'darwin':
            from fleasion.utils.platform_macos import (  # ruff: ignore[import-outside-top-level]
                find_bootstrapper_restore_resource_dirs,
            )

            # Bootstrapper snapshots are transient mirrors, not installations:
            # manage them while present, but never persist them as Roblox dirs.
            for backup_dir in find_bootstrapper_restore_resource_dirs():
                _add_non_windows(backup_dir)
        return found

    winreg = _winreg_module()

    found: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> bool:
        if '\x00' in str(path) or is_roblox_studio_resource_dir(path):
            return False
        key = str(path)
        if key not in seen:
            found.append(path)
            seen.add(key)
            return True
        return False

    def _extract_exe_from_command(command: str) -> Path | None:
        command = (command or '').replace('\x00', '').strip()
        if not command:
            return None
        match = re.match(r'(.+?\.exe)(?:["\s]|$)', command, re.IGNORECASE)
        if match:
            exe_path = match.group(1).strip('"')
        else:
            try:
                parts = shlex.split(command, posix=False)
            except ValueError:
                parts = []
            exe_path = parts[0].strip('"') if parts else command.split()[0]
        if not exe_path:
            return None
        return Path(exe_path)

    def _scan_for_exe(root: Path, max_depth: int) -> list[Path]:
        results: list[Path] = []

        def _has_player(path: Path) -> bool:
            try:
                return os.path.isfile(os.path.join(path, ROBLOX_PROCESS))  # ruff: ignore[os-path-isfile, os-path-join]
            except OSError, ValueError:
                return False

        try:
            root_is_dir = root.is_dir()
        except OSError, ValueError:
            return results
        if root_is_dir and _has_player(root):
            results.append(root)

        def _recurse(p: Path, depth: int) -> None:
            try:  # ruff: ignore[too-many-statements-in-try-clause]
                for entry in os.scandir(p):
                    if not entry.is_dir():
                        continue
                    entry_path = Path(entry.path)
                    if _has_player(entry_path):
                        results.append(entry_path)
                    if depth < max_depth:
                        _recurse(entry_path, depth + 1)
            except OSError, ValueError:
                pass

        if root_is_dir:
            _recurse(root, 1)
        return results

    # 1. Registry: HKCU\Software — two levels for "PlayerPath"
    try:  # ruff: ignore[too-many-nested-blocks, too-many-statements-in-try-clause]
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software') as hkey:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(hkey, i)
                    i += 1
                except OSError:
                    break
                if '\x00' in name:
                    continue
                try:  # ruff: ignore[too-many-statements-in-try-clause]
                    with winreg.OpenKey(hkey, name) as sub:
                        try:  # ruff: ignore[too-many-statements-in-try-clause]
                            val, rtype = winreg.QueryValueEx(sub, 'PlayerPath')
                            if rtype == winreg.REG_SZ and val:
                                val = cast('str', val).replace('\x00', '').strip()
                                p = Path(val)
                                if p.name.lower() == ROBLOX_PROCESS.lower():
                                    p = p.parent
                                if os.path.isfile(os.path.join(str(p), ROBLOX_PROCESS)):  # ruff: ignore[os-path-isfile, os-path-join]
                                    _add(p)
                                else:
                                    for d in _scan_for_exe(p, 1):
                                        _add(d)
                        except OSError, ValueError:
                            pass
                        # One nested level
                        j = 0
                        while True:
                            try:
                                sub_name = winreg.EnumKey(sub, j)
                                j += 1
                            except OSError:
                                break
                            if '\x00' in sub_name:
                                continue
                            try:  # ruff: ignore[too-many-statements-in-try-clause]
                                with winreg.OpenKey(sub, sub_name) as sub2:
                                    val2, rtype2 = winreg.QueryValueEx(sub2, 'PlayerPath')
                                    if rtype2 == winreg.REG_SZ and val2:
                                        val2 = cast('str', val2).replace('\x00', '').strip()
                                        p2 = Path(val2)
                                        if p2.name.lower() == ROBLOX_PROCESS.lower():
                                            p2 = p2.parent
                                        if os.path.isfile(os.path.join(str(p2), ROBLOX_PROCESS)):  # ruff: ignore[os-path-isfile, os-path-join]
                                            _add(p2)
                                        else:
                                            for d in _scan_for_exe(p2, 1):
                                                _add(d)
                            except OSError, ValueError:
                                pass
                except OSError, ValueError:
                    pass
    except OSError, ValueError:
        pass

    # 2. MS Store: C:\XboxGames\Roblox
    xbox = Path('C:/XboxGames/Roblox')
    for d in _scan_for_exe(xbox, 2):
        _add(d)

    # 3. Active Roblox — HKCU\...\roblox-player\open\command
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Classes\roblox-player\shell\open\command',
        ) as key:
            val, _ = winreg.QueryValueEx(key, '')
            exe_path = _extract_exe_from_command(cast('str', val))
            if exe_path is not None:
                for d in _scan_for_exe(exe_path.parent, 2):
                    _add(d)
    except OSError, ValueError:
        pass

    # 4. Program Files (x86) Roblox installs
    program_files_versions = Path(r'C:\Program Files (x86)\Roblox\Versions')
    for d in _scan_for_exe(program_files_versions, 2):
        _add(d)

    # 5. %LocalAppData%\Roblox\Versions
    local_versions = LOCAL_APPDATA / 'Roblox' / 'Versions'
    for d in _scan_for_exe(local_versions, 1):
        _add(d)

    # 6. Live running Roblox Player install directory
    running_player = get_roblox_player_exe_path()
    if running_player is not None:
        _add(running_player.parent)

    for cached_dir in load_saved_roblox_dirs():
        _add(cached_dir)

    save_saved_roblox_dirs(found)

    return found


# ---------------------------------------------------------------------------
# Bundled asset resolver
# ---------------------------------------------------------------------------


def _bundled_path(name: str) -> Path:
    """Resolve a bundled asset filename to an absolute path."""
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass is None:
            base = Path(__file__).parent / 'bundled'
        else:
            base = Path(meipass) / 'fleasion' / 'modifications' / 'bundled'
    else:
        base = Path(__file__).parent / 'bundled'
    return base / name


# ---------------------------------------------------------------------------
# PendingModificationsQueue
# ---------------------------------------------------------------------------


class PendingModificationsQueue:
    """Stores pending FFlag and framerate modifications to apply later.

    When Roblox Player is running, changes are queued instead of applied immediately.
    When Roblox exits, all queued changes are applied at once.
    """

    def __init__(self) -> None:
        self._pending_fast_flags: _FastFlagSettings | None = None
        self._pending_framerate_cap: int | None = None
        self._lock = threading.Lock()

    def enqueue_fast_flags(self, settings: _FastFlagSettings) -> None:
        """Queue a fast-flags update to be applied later."""
        with self._lock:
            self._pending_fast_flags = settings

    def enqueue_framerate_cap(self, value: int) -> None:
        """Queue a framerate cap update to be applied later."""
        with self._lock:
            self._pending_framerate_cap = value

    def has_pending(self) -> bool:
        """Check if there are any pending modifications."""
        with self._lock:
            return self._pending_fast_flags is not None or self._pending_framerate_cap is not None

    def get_pending(self) -> tuple[_FastFlagSettings | None, int | None]:
        """Get and clear all pending modifications."""
        with self._lock:
            flags = self._pending_fast_flags
            framerate = self._pending_framerate_cap
            self._pending_fast_flags = None
            self._pending_framerate_cap = None
            return flags, framerate

    def clear(self) -> None:
        """Clear all pending modifications."""
        with self._lock:
            self._pending_fast_flags = None
            self._pending_framerate_cap = None


# ---------------------------------------------------------------------------
# ModificationManager
# ---------------------------------------------------------------------------


class ModificationManager(QObject):
    """Core engine for modification entries: eager-write, stash, restore."""

    entry_status_changed = Signal(str, str, str)  # (entry_id, status, error_msg)
    apply_started = Signal(str)  # entry_id
    apply_finished = Signal(str)  # entry_id
    restore_finished = Signal()

    def __init__(
        self,
        cache_scraper: object | None = None,
        *,
        read_only_lock_enabled: bool = False,
    ) -> None:
        super().__init__()
        self._cache_scraper = cache_scraper
        self._roblox_dirs: list[Path] = _find_roblox_dirs()
        self._stash_dir = MOD_ORIGINALS_DIR

        log_buffer.log(
            'Modifications',
            f'Discovered {format_count(self._roblox_dirs, "Roblox dir")}',
        )

        # Ensure directories exist
        MOD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._stash_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_macos_stash()

        # Lock that serialises all file-system writes/restores.  Prevents
        # a background apply thread from writing to dst after the main thread
        # has already restored the original (Apply → Reset race condition).
        self._fs_lock = threading.Lock()
        self._read_only_lock_enabled = bool(read_only_lock_enabled)
        self._read_only_state_file = READ_ONLY_STATE_FILE
        self._read_only_original_modes = self._load_read_only_original_modes()
        self._read_only_extra_paths: set[Path] = set()
        self._permission_denied_lock = threading.Lock()
        self._permission_denied_dirs: set[Path] = set()

        # Load persisted data
        self._data: _ModificationData = self._load_json()
        if self._migrate_target_paths_for_current_platform():
            self._save_json()

        # FastFlagManager
        self.fflag_manager = cast(
            '_FastFlagManagerLike', FastFlagManager(self._roblox_dirs, self._stash_dir)
        )

        # GlobalSettingsManager (for Roblox GlobalBasicSettings_13.xml)
        self.global_settings_manager = GlobalSettingsManager(self._stash_dir)

        # Queue for pending modifications when Roblox is running
        self.pending_modifications_queue = PendingModificationsQueue()

    @property
    def roblox_dirs(self) -> list[Path]:
        return list(self._roblox_dirs)

    def _migrate_legacy_macos_stash(self) -> None:
        """Assign the old shared ``Resources`` stash to the primary install."""
        if sys.platform != 'darwin' or not self._roblox_dirs:
            return
        legacy = self._stash_dir / 'Resources'
        if not legacy.is_dir():
            return
        primary = next(
            (path for path in self._roblox_dirs if path.name == 'Resources'),
            None,
        )
        if primary is None:
            return
        destination = resource_stash_dir(self._stash_dir, primary)
        if destination.exists():
            log_buffer.log(
                'Modifications',
                f'Legacy stash retained because {destination.name} already exists',
            )
            return
        shutil.move(str(legacy), str(destination))
        log_buffer.log(
            'Modifications',
            f'Migrated legacy macOS stash to {destination.name}',
        )

    def _record_permission_denied_dir(self, roblox_dir: Path) -> None:
        """Remember a protected Roblox install for the user-facing repair prompt."""
        try:
            path = roblox_dir.resolve()
        except OSError:
            path = roblox_dir
        lock = cast('threading.Lock | None', _instance_attr(self, '_permission_denied_lock'))
        if lock is None:
            self._permission_denied_lock = threading.Lock()
            lock = self._permission_denied_lock
        with lock:
            self._permission_denied_dirs.add(path)

    def take_permission_denied_dirs(self) -> list[Path]:
        """Return and clear protected installs recorded since the last poll."""
        lock = cast('threading.Lock | None', _instance_attr(self, '_permission_denied_lock'))
        if lock is None:
            return []
        denied_dirs = cast('set[Path] | None', _instance_attr(self, '_permission_denied_dirs'))
        if denied_dirs is None:
            return []
        with lock:
            paths = sorted(denied_dirs, key=lambda value: str(value).lower())
            denied_dirs.clear()
        return paths

    def _active_managed_resource_files(
        self,
        extra_paths: Iterable[Path] = (),
        *,
        existing_only: bool = True,
    ) -> list[Path]:
        """Return Roblox-version paths Fleasion currently owns."""
        files: list[Path] = []
        seen: set[str] = set()

        def _add(path: Path) -> None:
            try:
                key = str(path.resolve()).lower()
            except OSError:
                key = str(path).lower()
            if key in seen:
                return
            seen.add(key)
            files.append(path)

        data_obj = _instance_attr(self, '_data', {})
        entries = (
            cast('_ModificationData', data_obj).get('entries', [])
            if isinstance(data_obj, dict)
            else []
        )

        for roblox_dir in self._roblox_dirs:
            for entry in entries:
                target = entry.get('target_path', '')
                if not target:
                    continue
                if not (entry.get('source_type') and entry.get('source_value')):
                    continue
                if target.lower().endswith(('customfont.ttf',)) or entry.get('_is_font'):
                    font_root = content_prefixed_resource_root(roblox_dir)
                    _add(font_root / CUSTOM_FONT_REL)
                    families_dir = font_root / FAMILIES_REL
                    try:
                        for json_path in families_dir.glob('*.json'):
                            _add(json_path)
                    except OSError:
                        pass
                    continue
                try:
                    _add(roblox_dir / target_path_for_roblox_dir(target, roblox_dir))
                except ValueError as exc:
                    log_buffer.log(
                        'Modifications',
                        f'Skipping read-only guard for invalid target path {target!r}: {exc}',
                    )

            if isinstance(data_obj, dict) and cast('_ModificationData', data_obj).get(
                'fast_flags_enabled'
            ):
                for settings_path in client_settings_paths_for_resource_dir(roblox_dir):
                    _add(settings_path)

        for raw_path in extra_paths:
            _add(Path(raw_path))

        return [path for path in files if path.is_file()] if existing_only else files

    def _load_read_only_original_modes(self) -> dict[Path, int]:
        state_file = cast('Path | None', _instance_attr(self, '_read_only_state_file'))
        if state_file is None:
            return {}
        try:
            payload: object = json.loads(Path(state_file).read_text(encoding='utf-8'))
        except OSError, ValueError, TypeError, json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        typed_payload = cast('dict[str, int | str]', payload)
        modes: dict[Path, int] = {}
        for raw_path, raw_mode in typed_payload.items():
            try:
                modes[Path(raw_path)] = int(raw_mode)
            except TypeError, ValueError:
                continue
        return modes

    def _save_read_only_original_modes_locked(self) -> None:
        state_file = cast('Path | None', _instance_attr(self, '_read_only_state_file'))
        if state_file is None:
            return
        protected = (
            cast('dict[Path, int] | None', _instance_attr(self, '_read_only_original_modes')) or {}
        )
        path = Path(state_file)
        if not protected:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(path.suffix + '.tmp')
            temp_path.write_text(
                json.dumps(
                    {str(file_path): stat.S_IMODE(mode) for file_path, mode in protected.items()},
                    indent=2,
                ),
                encoding='utf-8',
            )
            temp_path.replace(path)
        except OSError as exc:
            log_buffer.log('Modifications', f'Failed to persist read-only guard state: {exc}')

    def managed_resource_paths(self) -> list[Path]:
        """Return live and snapshot paths Fleasion must keep authoritative."""
        return self._active_managed_resource_files(existing_only=False)

    def protect_managed_files(self, extra_paths: Iterable[Path] = ()) -> None:
        """Mark Fleasion-managed Roblox files read-only until Fleasion needs to write."""
        if not bool(_instance_attr(self, '_read_only_lock_enabled', False)):  # ruff: ignore[boolean-positional-value-in-call]
            return
        lock = cast('threading.Lock | None', _instance_attr(self, '_fs_lock'))
        if lock is None:
            self._protect_managed_files_locked(extra_paths)
            return
        with lock:
            self._protect_managed_files_locked(extra_paths)

    def _protect_managed_files_locked(self, extra_paths: Iterable[Path] = ()) -> None:
        if not bool(_instance_attr(self, '_read_only_lock_enabled', False)):  # ruff: ignore[boolean-positional-value-in-call]
            return
        protected = cast(
            'dict[Path, int] | None', _instance_attr(self, '_read_only_original_modes')
        )
        if protected is None:
            protected = {}
            self._read_only_original_modes = protected
        registered_extra_paths = cast(
            'set[Path] | None', _instance_attr(self, '_read_only_extra_paths')
        )
        if registered_extra_paths is None:
            registered_extra_paths = set[Path]()
            self._read_only_extra_paths = registered_extra_paths
        registered_extra_paths.update(Path(path) for path in extra_paths)

        newly_protected = 0
        for path in self._active_managed_resource_files(registered_extra_paths):
            try:
                mode = path.stat().st_mode
            except OSError:
                continue
            if path not in protected:
                protected[path] = mode
                newly_protected += 1
            _set_read_only(path)

        if newly_protected:
            self._save_read_only_original_modes_locked()
            log_buffer.log(
                'Modifications',
                f'Read-only guarded {format_count(newly_protected, "managed Roblox file")}',
            )

    def _unlock_managed_files_locked(self) -> None:
        """Temporarily make guarded files writable without forgetting modes."""
        protected = (
            cast('dict[Path, int] | None', _instance_attr(self, '_read_only_original_modes')) or {}
        )
        for path in protected:
            try:
                if path.exists():
                    _clear_read_only(path)
            except OSError as exc:
                log_buffer.log(
                    'Modifications',
                    f'Failed to temporarily unlock managed file {path}: {exc}',
                )

    def clear_managed_file_read_only(
        self,
        extra_paths: Iterable[Path] = (),
        *,
        clear_untracked: bool = False,
    ) -> None:
        """Clear Fleasion's read-only guard from managed Roblox files."""
        lock = cast('threading.Lock | None', _instance_attr(self, '_fs_lock'))
        if lock is None:
            self._clear_managed_file_read_only_locked(extra_paths, clear_untracked=clear_untracked)
            return
        with lock:
            self._clear_managed_file_read_only_locked(extra_paths, clear_untracked=clear_untracked)

    def set_read_only_lock_enabled(self, enabled: bool) -> None:
        """Apply or remove the optional persistent modification-file guard."""
        enabled = bool(enabled)
        lock = cast('threading.Lock | None', _instance_attr(self, '_fs_lock'))
        if lock is None:
            self._read_only_lock_enabled = enabled
            if enabled:
                self._protect_managed_files_locked()
            else:
                self._clear_managed_file_read_only_locked()
            return
        with lock:
            self._read_only_lock_enabled = enabled
            if enabled:
                self._protect_managed_files_locked()
            else:
                self._clear_managed_file_read_only_locked()

    def _clear_managed_file_read_only_locked(
        self,
        extra_paths: Iterable[Path] = (),
        *,
        clear_untracked: bool = False,
    ) -> None:
        protected = cast(
            'dict[Path, int] | None', _instance_attr(self, '_read_only_original_modes')
        )
        if protected is None:
            protected = {}
            self._read_only_original_modes = protected
        registered_extra_paths = cast(
            'set[Path] | None', _instance_attr(self, '_read_only_extra_paths')
        )
        if registered_extra_paths is None:
            registered_extra_paths = set[Path]()
            self._read_only_extra_paths = registered_extra_paths

        paths: list[Path] = []
        seen: set[str] = set()

        def _add(path: Path) -> None:
            try:
                key = str(path.resolve()).lower()
            except OSError:
                key = str(path).lower()
            if key in seen:
                return
            seen.add(key)
            paths.append(path)

        for path in protected:
            _add(path)
        if clear_untracked:
            for path in self._active_managed_resource_files(registered_extra_paths):
                _add(path)
            for path in extra_paths:
                _add(Path(path))

        cleared = 0
        for path in paths:
            try:  # ruff: ignore[too-many-statements-in-try-clause]
                if path.exists():
                    original_mode = protected.get(path)
                    if original_mode is None:
                        # This may be a stale guard left by an older Fleasion
                        # build, before original modes were restored exactly.
                        _clear_read_only(path)
                    else:
                        path.chmod(stat.S_IMODE(original_mode))
                    cleared += 1
            except OSError as exc:
                log_buffer.log(
                    'Modifications',
                    f'Failed to clear read-only guard for {path}: {exc}',
                )
        protected.clear()
        registered_extra_paths.clear()
        self._save_read_only_original_modes_locked()
        if cleared:
            log_buffer.log(
                'Modifications',
                f'Cleared read-only guard for {format_count(cleared, "managed Roblox file")}',
            )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_json(self) -> _ModificationData:
        if MODIFICATIONS_JSON.exists():
            try:  # ruff: ignore[too-many-statements-in-try-clause]
                with MODIFICATIONS_JSON.open('r', encoding='utf-8') as fp:
                    data = cast('_ModificationData', json.load(fp))
                # Deduplicate entries by target_path, keeping the last (most
                # recent) entry per path.  Duplicate entries could accumulate
                # from previous sessions with race-condition bugs.
                entries = data.get('entries', [])
                seen: dict[str, _ModificationEntry] = {}
                for e in entries:
                    tp = e.get('target_path', '')
                    if tp:
                        seen[tp] = e  # later entry wins
                    else:
                        # No target_path key — keep as-is (edge case)
                        seen[e.get('id', str(id(e)))] = e
                data['entries'] = list(seen.values())
                if 'global_settings' not in data:
                    data['global_settings'] = _GlobalSettings()
                legacy_framerate = data.get('fast_flags', {}).pop('framerate_cap', None)
                if (
                    legacy_framerate is not None
                    and data['global_settings'].get('framerate_cap') is None
                ):
                    data['global_settings']['framerate_cap'] = legacy_framerate
                return data  # ruff: ignore[try-consider-else]
            except json.JSONDecodeError, OSError:
                pass
        return {
            'entries': [],
            'fast_flags_enabled': False,
            'fast_flags': {},
            'global_settings': {},
        }

    def _save_json(self) -> None:
        MODIFICATIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
        with MODIFICATIONS_JSON.open('w', encoding='utf-8') as fp:
            json.dump(self._data, fp, indent=2)

    def _migrate_target_paths_for_current_platform(self) -> bool:
        """Migrate resolved Linux paths back to portable logical targets."""
        changed = False
        for entry in self._data.get('entries', []):
            target = entry.get('target_path')
            if not target:
                continue
            mapped = canonical_target_path(target)
            if mapped.replace('\\', '/').strip('/') != str(target).replace('\\', '/').strip('/'):
                entry['target_path'] = mapped
                changed = True
        return changed

    # ------------------------------------------------------------------
    # Entry CRUD
    # ------------------------------------------------------------------

    @property
    def entries(self) -> list[_ModificationEntry]:
        return self._data.setdefault('entries', [])

    def _find_entry(self, entry_id: str) -> _ModificationEntry | None:
        for e in self.entries:
            if e.get('id') == entry_id:
                return e
        return None

    def add_entry(self, entry: _NewModificationEntry) -> str:
        """Add a new modification entry and eagerly apply it.

        If an entry with the same target_path already exists it is reused
        (acting as an update) to prevent duplicate entries from accumulating.
        """
        target = entry.get('target_path', '')
        if target:
            existing = next((e for e in self.entries if e.get('target_path') == target), None)
            if existing is not None:
                # Reuse the existing entry — delegate to update_entry.
                existing_id = existing['id']
                self.update_entry(
                    existing_id,
                    source_type=entry.get('source_type'),
                    source_value=entry.get('source_value'),
                    display_name=entry.get('display_name', existing.get('display_name', '')),
                    **{
                        k: cast('str | int | bool | None', v)
                        for k, v in entry.items()
                        if k
                        not in (  # ruff: ignore[literal-membership]
                            'id',
                            'status',
                            'error_message',
                            'converted_cache_path',
                            'target_path',
                            'source_type',
                            'source_value',
                            'display_name',
                        )
                    },
                )
                return existing_id

        entry_id = str(uuid.uuid4())
        cast('dict[str, object]', entry)['id'] = entry_id
        entry.setdefault('status', 'pending')
        entry.setdefault('error_message', None)
        entry.setdefault('converted_cache_path', None)
        stored_entry = cast('_ModificationEntry', entry)
        self.entries.append(stored_entry)
        self._save_json()

        run_in_thread(self._process_and_apply_entry)(stored_entry)
        return entry_id

    def remove_entry(self, entry_id: str) -> bool:
        """Remove an entry and restore its original file."""
        entry = self._find_entry(entry_id)
        if entry is None:
            return True
        try:
            self._restore_entry(entry)
        except Exception as exc:  # ruff: ignore[blind-except]
            self._mark_restore_failed(entry, exc)
            return False
        self._data['entries'] = [e for e in self.entries if e.get('id') != entry_id]
        self._save_json()
        # Notify the status bar that an entry was removed.
        self.restore_finished.emit()
        return True

    def update_entry(self, entry_id: str, **kwargs: str | int | bool | None) -> bool:
        """Update an entry's source, restore old files, and re-apply."""
        entry = self._find_entry(entry_id)
        if entry is None:
            return True
        # Invalidate any in-flight apply so its background thread discards
        # its result instead of overwriting the freshly-written new file.
        entry['_apply_gen'] = entry.get('_apply_gen', 0) + 1
        # Only restore if there is an active source to undo.  When
        # source_type is None the entry was previously cleared and the
        # original Roblox file is already sitting at dst — calling
        # _restore_entry would incorrectly delete it via the "new file"
        # fallback branch.
        if entry.get('source_type') is not None:
            try:
                self._restore_entry(entry)
            except Exception as exc:  # ruff: ignore[blind-except]
                self._mark_restore_failed(entry, exc)
                return False
        cast('dict[str, str | int | bool | None]', entry).update(kwargs)
        entry['status'] = 'pending'
        entry['error_message'] = None
        self._save_json()
        run_in_thread(self._process_and_apply_entry)(entry)
        return True

    def clear_entry(self, entry_id: str) -> bool:
        """Restore the original file and delete this entry from the list.

        Keeping cleared entries as 'not_set' ghosts causes two problems:
        1. reapply_all on the next startup may re-apply a modification the
           user explicitly reset (if source_type was still set).
        2. The JSON grows without bound as users add and reset modifications.
        Deleting the entry is safe: _sync_from_manager falls back to
        _check_for_orphaned_stash when no entry is found, so the row still
        detects any leftover stash from crash/external edits.
        """
        entry = self._find_entry(entry_id)
        if entry is None:
            return True
        # Invalidate any in-flight apply before restoring the original file.
        entry['_apply_gen'] = entry.get('_apply_gen', 0) + 1
        try:
            self._restore_entry(entry)
        except Exception as exc:  # ruff: ignore[blind-except]
            self._mark_restore_failed(entry, exc)
            return False
        self._data['entries'] = [e for e in self.entries if e.get('id') != entry_id]
        self._save_json()
        self.entry_status_changed.emit(entry_id, 'not_set', '')
        # Notify status bar that an active modification was cleared.
        self.restore_finished.emit()
        return True

    def _mark_restore_failed(self, entry: _ModificationEntry, exc: Exception) -> None:
        """Keep the entry visible when restoring its original file fails."""
        entry_id = entry.get('id', '')
        error = f'Failed to restore original file: {exc}'
        entry['status'] = 'error'
        entry['error_message'] = error
        self._save_json()
        if entry_id:
            self.entry_status_changed.emit(entry_id, 'error', error)
        log_buffer.log(
            'Modifications',
            f'Restore failed for {entry.get("display_name", "?")}: {exc}',
        )

    # ------------------------------------------------------------------
    # Processing & applying
    # ------------------------------------------------------------------

    def _process_and_apply_entry(self, entry: _ModificationEntry) -> None:
        """Resolve source, convert if needed, stash & write."""
        entry_id = entry['id']
        # Snapshot the generation counter before doing any work.  If
        # clear_entry or update_entry runs on the main thread while we are
        # processing, they increment _apply_gen and we discard our stale result.
        apply_gen = entry.get('_apply_gen', 0)
        self.apply_started.emit(entry_id)

        try:  # ruff: ignore[too-many-statements-in-try-clause]
            data = self._resolve_source(entry)
            if data is None:
                msg = 'Could not resolve source data'
                raise ValueError(msg)

            target = entry.get('target_path', '')

            # Font special-case
            if target.lower().endswith(('customfont.ttf',)) or entry.get('_is_font'):
                if not validate_font_bytes(data):
                    msg = 'Not a valid font file (invalid header)'
                    raise ValueError(msg)
                with self._fs_lock:
                    self._unlock_managed_files_locked()
                    try:
                        apply_custom_font(
                            data,
                            _font_helper_dirs(self._roblox_dirs),
                            self._stash_dir,
                            family_manifest_loader=lambda resource_dir: (
                                read_current_platform_original_directory(
                                    FAMILIES_REL,
                                    resource_dir=resource_dir,
                                )
                            ),
                        )
                    finally:
                        self._protect_managed_files_locked()
                if entry.get('_apply_gen', 0) != apply_gen:
                    self.apply_finished.emit(entry_id)
                    return
                entry['status'] = 'applied'
                entry['error_message'] = None
                self._save_json()
                self.entry_status_changed.emit(entry_id, 'applied', '')
                self.apply_finished.emit(entry_id)
                return

            # Mesh conversion: .obj → .mesh
            if target.lower().endswith('.mesh') and self._looks_like_obj(data):
                data = self._convert_obj_to_mesh(data)

            data = self._coerce_replacement_for_target(target, data)

            self._stash_and_write(target, data)

            # Check if a reset/update happened while the write was in progress.
            # The fs_lock serialises file ops, so by the time we get here the
            # restore (if any) has already completed.  We just need to avoid
            # overwriting the restored file and misreporting the status.
            if entry.get('_apply_gen', 0) != apply_gen:
                # Our write is stale.  _restore_entry was already called by
                # clear_entry/update_entry (holding the lock); dst is already
                # back to the original.  Do nothing.
                self.apply_finished.emit(entry_id)
                return

            entry['status'] = 'applied'
            entry['error_message'] = None
            self._save_json()
            self.entry_status_changed.emit(entry_id, 'applied', '')

        except Exception as exc:  # ruff: ignore[blind-except]
            if entry.get('_apply_gen', 0) == apply_gen:
                entry['status'] = 'error'
                entry['error_message'] = str(exc)
                self._save_json()
                self.entry_status_changed.emit(entry_id, 'error', str(exc))
            log_buffer.log(
                'Modifications',
                f'Error applying {entry.get("display_name", "?")}: {exc}',
            )

        self.apply_finished.emit(entry_id)

    def _resolve_source(self, entry: _ModificationEntry) -> bytes | None:
        """Resolve the entry's source to raw bytes."""
        src_type = entry.get('source_type')
        src_value = cast('str', entry.get('source_value', ''))

        if src_type == 'local_file':
            p = Path(src_value)
            if not p.is_file():
                msg = f'File not found: {src_value}'
                raise FileNotFoundError(msg)
            return p.read_bytes()

        if src_type == 'bundled':
            # e.g. "bundled:empty.mp3" → strip prefix
            name = (
                src_value.replace('bundled:', '', 1)
                if src_value.startswith('bundled:')
                else src_value
            )
            # Special sentinel: write a zero-byte file (unsupported extension fallback)
            if name == 'zero':
                return b''
            bp = _bundled_path(name)
            if not bp.is_file():
                msg = f'Bundled file not found: {name}'
                raise FileNotFoundError(msg)
            return bp.read_bytes()

        if src_type == 'asset_id':
            return self._fetch_asset(src_value)

        if src_type == 'cdn_url':
            return self._fetch_cdn_url(src_value)

        return None

    def _fetch_cdn_url(self, url: str) -> bytes:
        """Download a CDN URL, caching to ModCache."""
        import hashlib  # ruff: ignore[import-outside-top-level]
        from urllib.error import URLError  # ruff: ignore[import-outside-top-level]
        from urllib.parse import urlparse  # ruff: ignore[import-outside-top-level]

        from fleasion.utils.http import http_get  # ruff: ignore[import-outside-top-level]

        MOD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        # Preserve the file extension so _looks_like_obj can detect .obj files.
        ext = Path(urlparse(url).path).suffix.lower() or '.bin'
        cache_file = MOD_CACHE_DIR / f'cdn_{url_hash}{ext}'

        if cache_file.is_file():
            return cache_file.read_bytes()

        try:
            data = http_get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
        except URLError as exc:
            msg = f'CDN download failed: {exc}'
            raise RuntimeError(msg) from exc

        cache_file.write_bytes(data)
        return data

    def _fetch_asset(self, asset_id: str) -> bytes:
        """Download an asset by ID, caching to ModCache."""
        cache_file = MOD_CACHE_DIR / f'{asset_id}.bin'
        if cache_file.is_file():
            return cache_file.read_bytes()

        if self._cache_scraper is None:
            msg = 'No cache scraper available. Asset ID download requires the proxy to be running.'
            raise RuntimeError(msg)

        extra_hdrs: dict[str, str] = {}
        get_roblosecurity = cast(
            '_GetRoblosecurity',
            getattr(self._cache_scraper, '_get_roblosecurity'),  # ruff: ignore[get-attr-with-constant]
        )
        cookie = get_roblosecurity(wait=True)
        if cookie:
            extra_hdrs['Cookie'] = f'.ROBLOSECURITY={cookie};'
        fetch_asset = cast(
            '_FetchAssetWithPlaceIdRetry',
            getattr(self._cache_scraper, '_fetch_asset_with_place_id_retry'),  # ruff: ignore[get-attr-with-constant]
        )
        data, status = fetch_asset(str(asset_id), extra_headers=extra_hdrs or None)
        if data is None:
            if status == 403:
                msg = 'Asset not found or private. Add .ROBLOSECURITY cookie.'
                raise PermissionError(msg)
            msg = f'Asset download failed (HTTP {status})'
            raise RuntimeError(msg)

        cache_file.write_bytes(data)
        return data

    @staticmethod
    def _looks_like_obj(data: bytes) -> bool:
        """Heuristic: does this data look like a Wavefront OBJ?"""
        try:
            head = data[:512].decode('utf-8', errors='ignore')
            return head.lstrip().startswith(('v ', 'vn ', '#', 'o ', 'g '))
        except Exception:  # ruff: ignore[blind-except]
            return False

    @staticmethod
    def _convert_obj_to_mesh(data: bytes) -> bytes:
        """Convert OBJ bytes → Roblox V2.00 .mesh bytes."""
        from fleasion.cache.tools.solidmodel_converter.obj_to_mesh import (  # ruff: ignore[import-outside-top-level]
            export_v2_mesh,
            parse_obj_for_mesh,
        )

        obj_text = data.decode('utf-8', errors='replace')
        vertices, colors, indices = parse_obj_for_mesh(obj_text)
        return export_v2_mesh(vertices, colors, indices)

    @staticmethod
    def _is_ktx(data: bytes) -> bool:
        return strip_prefixed_ktx(data) is not None

    def _coerce_replacement_for_target(self, target_path: str, data: bytes) -> bytes:
        """Convert image replacements to KTX2 when replacing KTX-backed targets."""
        if self._is_ktx(data):
            return data

        originals = [
            read_current_platform_original_asset(target_path, roblox_dir)
            for roblox_dir in cast('list[Path]', _instance_attr(self, '_roblox_dirs', []))
        ]
        if not originals:
            originals.append(read_current_platform_original_asset(target_path))
        if not any(original is not None and self._is_ktx(original) for original in originals):
            return data

        try:  # ruff: ignore[too-many-statements-in-try-clause]
            import hashlib  # ruff: ignore[import-outside-top-level]
            import io  # ruff: ignore[import-outside-top-level]

            from PIL import Image  # ruff: ignore[import-outside-top-level]

            from fleasion.cache.tools.rgba_ktx2 import (  # ruff: ignore[import-outside-top-level]
                RGBA8_KTX2_CACHE_VERSION,
                mipmap_mode_for_texture_name,
                write_rgba8_ktx2,
            )

            image = Image.open(io.BytesIO(data)).convert('RGBA')
            width, height = image.size
            mipmap_mode = mipmap_mode_for_texture_name(target_path)
            digest = hashlib.sha256(
                data
                + target_path.encode('utf-8')
                + RGBA8_KTX2_CACHE_VERSION
                + mipmap_mode.encode('ascii')
            ).hexdigest()[:16]
            out_path = MOD_CACHE_DIR / f'ktx2_{digest}.ktx2'
            if not out_path.exists():
                write_rgba8_ktx2(
                    image.tobytes(),
                    width,
                    height,
                    out_path,
                    mipmap_mode=mipmap_mode,
                )
            return out_path.read_bytes()
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('Modifications', f'KTX2 conversion skipped for {target_path}: {exc}')
            return data

    # ------------------------------------------------------------------
    # Stash & write / restore
    # ------------------------------------------------------------------

    # Sentinel suffix written alongside the stash directory when the
    # target file did NOT exist before a mod was applied.  _restore_entry
    # uses it to distinguish "new file → delete" from "original existed
    # but stash is gone → leave dst alone".
    _NEW_FILE_MARKER_SUFFIX = '.fleasion_new'

    def _stash_and_write(self, target_path_rel: str, new_bytes: bytes) -> None:
        """Stash the original file and write the mod in every Roblox dir."""
        with self._fs_lock:  # ruff: ignore[too-many-nested-blocks]
            self._unlock_managed_files_locked()
            try:
                failures: list[tuple[Path, PermissionError]] = []
                for roblox_dir in self._roblox_dirs:
                    try:  # ruff: ignore[too-many-statements-in-try-clause]
                        target_path = target_path_for_roblox_dir(target_path_rel, roblox_dir)
                        dst = roblox_dir / target_path
                        stash = resource_stash_dir(self._stash_dir, roblox_dir) / target_path
                        marker = stash.with_name(stash.name + self._NEW_FILE_MARKER_SUFFIX)

                        # Stash original ONCE (idempotent)
                        if dst.exists() and not stash.exists():
                            stash.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(dst, stash)
                            # Remove any stale new-file marker from a previous run
                            if marker.exists():
                                marker.unlink(missing_ok=True)
                        elif not dst.exists() and not stash.exists() and not marker.exists():
                            # Target is brand-new (no original to stash); leave a marker
                            # so _restore_entry knows it is safe to delete the file later.
                            stash.parent.mkdir(parents=True, exist_ok=True)
                            marker.touch()

                        # Write mod
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        _clear_read_only(dst)
                        dst.write_bytes(new_bytes)
                    except PermissionError as exc:
                        self._record_permission_denied_dir(roblox_dir)
                        failures.append((roblox_dir, exc))
                if failures:
                    failed_paths = ', '.join(str(path) for path, _exc in failures)
                    msg = f'Permission denied in Roblox installation(s): {failed_paths}'
                    raise PermissionError(msg)
            finally:
                self._protect_managed_files_locked()

    def _restore_entry(self, entry: _ModificationEntry) -> None:
        """Undo a single entry: restore the stash or delete the mod file."""
        target = entry.get('target_path', '')

        # Font special-case
        if target.lower().endswith(('customfont.ttf',)) or entry.get('_is_font'):
            with self._fs_lock:
                restore_font_families(
                    _font_helper_dirs(self._roblox_dirs),
                    self._stash_dir,
                )
            return

        with self._fs_lock:
            for roblox_dir in self._roblox_dirs:
                try:
                    target_path = target_path_for_roblox_dir(target, roblox_dir)
                except ValueError as exc:
                    log_buffer.log(
                        'Modifications',
                        f'Skipping restore for invalid target path {target!r}: {exc}',
                    )
                    continue
                dst = roblox_dir / target_path
                stash = resource_stash_dir(self._stash_dir, roblox_dir) / target_path
                marker = stash.with_name(stash.name + self._NEW_FILE_MARKER_SUFFIX)
                if stash.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    _clear_read_only(dst)
                    shutil.copy2(stash, dst)
                    _clear_read_only(stash)
                    stash.unlink()
                elif marker.exists():
                    # Was a brand-new file (no original existed) — delete it
                    # and clean up the marker.
                    marker.unlink(missing_ok=True)
                    if dst.exists():
                        _clear_read_only(dst)
                        dst.unlink()
                # else: no stash and no marker means the entry was previously
                # cleared (clear_entry already restored dst) or an error
                # occurred before the write — leave dst untouched.

    def restore_orphaned_stash(self, target_path: str) -> bool:
        """Restore an orphaned stash file that has no tracked JSON entry.

        Returns True if at least one stash was found and restored.  Called by
        the UI when a row detects a stash on disk (e.g. manual file edit, crash)
        but has no active modification entry to clear.
        """
        with self._fs_lock:
            restored = False
            for roblox_dir in self._roblox_dirs:
                try:
                    target_rel = target_path_for_roblox_dir(target_path, roblox_dir)
                except ValueError as exc:
                    log_buffer.log(
                        'Modifications',
                        f'Cannot restore orphaned stash for invalid target path '
                        f'{target_path!r}: {exc}',
                    )
                    continue
                dst = roblox_dir / target_rel
                stash = resource_stash_dir(self._stash_dir, roblox_dir) / target_rel
                marker = stash.with_name(stash.name + self._NEW_FILE_MARKER_SUFFIX)
                if stash.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    _clear_read_only(dst)
                    shutil.copy2(stash, dst)
                    _clear_read_only(stash)
                    stash.unlink()
                    restored = True
                elif marker.exists():
                    marker.unlink(missing_ok=True)
                    if dst.exists():
                        _clear_read_only(dst)
                        dst.unlink()
                    restored = True
            return restored

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def restore_all(self) -> None:
        """Restore every applied modification and fast-flags."""
        self.clear_managed_file_read_only()

        try:
            for entry in self.entries:
                if entry.get('status') == 'applied':
                    try:
                        self._restore_entry(entry)
                    except Exception as exc:  # ruff: ignore[blind-except]
                        log_buffer.log(
                            'Modifications',
                            f'Restore failed for {entry.get("display_name", "?")}: {exc}',
                        )

            if self._data.get('fast_flags_enabled'):
                try:
                    self.fflag_manager.restore()
                except Exception as exc:  # ruff: ignore[blind-except]
                    log_buffer.log('FastFlags', f'Restore failed: {exc}')

            # Restore global settings
            try:
                self.global_settings_manager.restore()
            except Exception as exc:  # ruff: ignore[blind-except]
                log_buffer.log('GlobalSettings', f'Restore failed: {exc}')
        finally:
            self.clear_managed_file_read_only()

        self.restore_finished.emit()
        log_buffer.log('Modifications', 'All modifications restored')

    def reapply_all(self) -> None:
        """Re-apply all entries (crash recovery on startup)."""
        for entry in self.entries:
            if entry.get('source_type') and entry.get('source_value'):
                self._process_and_apply_entry(entry)

        if self._data.get('fast_flags_enabled') and self._data.get('fast_flags'):
            with self._fs_lock:
                self._unlock_managed_files_locked()
                try:
                    failed_dirs = self.fflag_manager.write(self._data['fast_flags'])
                    for roblox_dir in failed_dirs:
                        self._record_permission_denied_dir(roblox_dir)
                finally:
                    self._protect_managed_files_locked()

        if self._data.get('fast_flags_enabled'):
            self.sync_saved_global_settings()

        log_buffer.log('Modifications', 'Re-applied all modifications (crash recovery)')

    # ------------------------------------------------------------------
    # Fast-flag helpers (delegated to FastFlagManager)
    # ------------------------------------------------------------------

    @property
    def fast_flags_enabled(self) -> bool:
        return self._data.get('fast_flags_enabled', False)

    @fast_flags_enabled.setter
    def fast_flags_enabled(self, value: bool) -> None:
        self._data['fast_flags_enabled'] = value
        if not value:
            with self._fs_lock:
                self._unlock_managed_files_locked()
                try:
                    self.fflag_manager.restore()
                except Exception:  # ruff: ignore[blind-except, try-except-pass]
                    pass
                finally:
                    self._protect_managed_files_locked()
            try:
                self.global_settings_manager.restore()
            except Exception:  # ruff: ignore[blind-except, try-except-pass]
                pass
        self._save_json()

    @property
    def fast_flags(self) -> _FastFlagSettings:
        return self._data.get('fast_flags', {})

    @fast_flags.setter
    def fast_flags(self, settings: _FastFlagSettings) -> None:
        self._data['fast_flags'] = cast(
            '_FastFlagSettings',
            {k: v for k, v in settings.items() if k != 'framerate_cap'},
        )
        self._save_json()

    @property
    def global_settings(self) -> _GlobalSettings:
        return self._data.setdefault('global_settings', {})

    @global_settings.setter
    def global_settings(self, settings: _GlobalSettings) -> None:
        self._data['global_settings'] = settings
        self._save_json()

    @property
    def framerate_cap(self) -> int:
        value = self.global_settings.get('framerate_cap')
        if value is None:
            value = self.fast_flags.get('framerate_cap')
        if value is None:
            # A legacy Fleasion version could leave an XML cap without a
            # corresponding saved setting. Reflect the actual active value in
            # the UI instead of presenting it as "Default".
            value = self.global_settings_manager.read_framerate_cap()
        try:
            return 0 if value in (None, '', 'Default') else int(value)  # ruff: ignore[literal-membership]
        except TypeError, ValueError:
            return 0

    @framerate_cap.setter
    def framerate_cap(self, value: int | None) -> None:
        settings = cast('_GlobalSettings', dict(self.global_settings))
        settings['framerate_cap'] = None if value in (None, 0) else int(value)  # ruff: ignore[literal-membership]
        self.global_settings = settings

    def sync_saved_global_settings(self) -> None:
        """Write the saved global settings to Roblox, or restore defaults."""
        framerate = self.framerate_cap
        if framerate:
            self.global_settings_manager.write(framerate)
        else:
            self.global_settings_manager.restore()

    def reset_framerate_cap(self) -> None:
        """Handle an explicit UI request to return Roblox to its default cap."""
        self.global_settings_manager.reset_framerate_cap()

    def write_fast_flags(self, settings: _FastFlagSettings) -> None:
        """Update and write fast-flags to disk."""
        self._data['fast_flags'] = cast(
            '_FastFlagSettings',
            {k: v for k, v in settings.items() if k != 'framerate_cap'},
        )
        self._data['fast_flags_enabled'] = True
        self._save_json()
        with self._fs_lock:
            self._unlock_managed_files_locked()
            try:
                failed_dirs = self.fflag_manager.write(settings)
                for roblox_dir in failed_dirs:
                    self._record_permission_denied_dir(roblox_dir)
            finally:
                self._protect_managed_files_locked()
        self.sync_saved_global_settings()

    def reassert_macos_bootstrapper_fast_flags(self) -> int:
        """Restore Fleasion's flags after a bootstrapper rewrites launch settings."""
        if not self._data.get('fast_flags_enabled') or not self._data.get('fast_flags'):
            return 0
        with self._fs_lock:
            updated = self.fflag_manager.reassert_macos_bootstrapper_flags(self._data['fast_flags'])
            if updated:
                self._protect_managed_files_locked()
            return updated

    def refresh_roblox_dirs(self, *, reapply_if_changed: bool = False) -> bool:
        """Re-discover Roblox directories (e.g. after an update)."""
        previous = {str(path.resolve()).lower() for path in self._roblox_dirs}
        self._roblox_dirs = _find_roblox_dirs()
        setattr(self.fflag_manager, '_roblox_dirs', self._roblox_dirs)  # ruff: ignore[set-attr-with-constant]
        self.global_settings_manager.refresh_roblox_dirs()
        current = {str(path.resolve()).lower() for path in self._roblox_dirs}
        log_buffer.log(
            'Modifications',
            f'Refreshed: {format_count(self._roblox_dirs, "Roblox dir")}',
        )
        changed = current != previous
        if changed and reapply_if_changed:
            self.reapply_all()
        return changed

    def apply_pending_modifications(self) -> None:
        """Apply all pending modifications that were queued while Roblox was running."""
        flags, framerate = self.pending_modifications_queue.get_pending()

        if flags is not None:
            try:
                self.write_fast_flags(flags)
                log_buffer.log('Modifications', 'Applied queued Fast Flags after Roblox exit')
            except Exception as exc:  # ruff: ignore[blind-except]
                log_buffer.log('Modifications', f'Error applying queued Fast Flags: {exc}')

        if framerate is not None:
            try:
                if framerate:
                    self.sync_saved_global_settings()
                else:
                    self.reset_framerate_cap()
                log_buffer.log('Modifications', 'Applied queued framerate cap after Roblox exit')
            except Exception as exc:  # ruff: ignore[blind-except]
                log_buffer.log('Modifications', f'Error applying queued framerate cap: {exc}')
