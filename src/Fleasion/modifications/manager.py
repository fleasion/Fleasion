"""ModificationManager — core eager-apply, stash & restore engine.

Owns the concept of a *modification entry*: each entry maps a target
path inside the Roblox directory to a source file (local, asset ID, or
bundled).  Files are written eagerly (immediately), and originals are
stashed so they can be restored on exit / shutdown.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from ..utils import CONFIG_DIR, LOCAL_APPDATA, ROBLOX_PROCESS, get_roblox_player_exe_path, log_buffer
from ..utils.roblox_dirs import load_saved_roblox_dirs, save_saved_roblox_dirs
from ..utils.threading import run_in_thread
from .fflag_manager import FastFlagManager
from .global_settings_manager import GlobalSettingsManager
from .font_utils import apply_custom_font, restore_font_families, validate_font_bytes

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MODIFICATIONS_JSON = CONFIG_DIR / 'modifications.json'
MOD_ORIGINALS_DIR = CONFIG_DIR / 'ModOriginals'
MOD_CACHE_DIR = CONFIG_DIR / 'ModCache'

# ---------------------------------------------------------------------------
# Roblox directory discovery  (mirrors proxy/master.py::_find_roblox_dirs)
# ---------------------------------------------------------------------------

def _find_roblox_dirs() -> list[Path]:
    """Locate every RobloxPlayerBeta.exe installation directory."""
    import winreg

    found: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> bool:
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
        if command.startswith('"'):
            end_quote = command.find('"', 1)
            if end_quote <= 1:
                return None
            exe_path = command[1:end_quote]
        else:
            exe_path = command.split()[0]
        if not exe_path:
            return None
        return Path(exe_path)

    def _scan_for_exe(root: Path, max_depth: int) -> list[Path]:
        results: list[Path] = []

        def _has_player(path: Path) -> bool:
            return os.path.isfile(os.path.join(path, ROBLOX_PROCESS))

        if root.is_dir() and _has_player(root):
            results.append(root)

        def _recurse(p: Path, depth: int) -> None:
            try:
                for entry in os.scandir(p):
                    if not entry.is_dir():
                        continue
                    entry_path = Path(entry.path)
                    if _has_player(entry_path):
                        results.append(entry_path)
                    if depth < max_depth:
                        _recurse(entry_path, depth + 1)
            except OSError:
                pass

        if root.is_dir():
            _recurse(root, 1)
        return results

    # 1. Registry: HKCU\Software — two levels for "PlayerPath"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software') as hkey:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(hkey, i); i += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(hkey, name) as sub:
                        try:
                            val, rtype = winreg.QueryValueEx(sub, 'PlayerPath')
                            if rtype == winreg.REG_SZ and val:
                                val = val.replace('\x00', '').strip()
                                p = Path(val)
                                if p.name.lower() == ROBLOX_PROCESS.lower():
                                    p = p.parent
                                if os.path.isfile(os.path.join(str(p), ROBLOX_PROCESS)):
                                    _add(p)
                                else:
                                    for d in _scan_for_exe(p, 1):
                                        _add(d)
                        except OSError:
                            pass
                        # One nested level
                        j = 0
                        while True:
                            try:
                                sub_name = winreg.EnumKey(sub, j); j += 1
                            except OSError:
                                break
                            try:
                                with winreg.OpenKey(sub, sub_name) as sub2:
                                    val2, rtype2 = winreg.QueryValueEx(sub2, 'PlayerPath')
                                    if rtype2 == winreg.REG_SZ and val2:
                                        val2 = val2.replace('\x00', '').strip()
                                        p2 = Path(val2)
                                        if p2.name.lower() == ROBLOX_PROCESS.lower():
                                            p2 = p2.parent
                                        if os.path.isfile(os.path.join(str(p2), ROBLOX_PROCESS)):
                                            _add(p2)
                                        else:
                                            for d in _scan_for_exe(p2, 1):
                                                _add(d)
                            except OSError:
                                pass
                except OSError:
                    pass
    except OSError:
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
            exe_path = _extract_exe_from_command(val)
            if exe_path is not None:
                for d in _scan_for_exe(exe_path.parent, 2):
                    _add(d)
    except OSError:
        pass

    # 4. Program Files (x86) Roblox installs
    program_files_versions = Path(r'C:\Program Files (x86)\Roblox\Versions')
    for d in _scan_for_exe(program_files_versions, 2):
        _add(d)

    # 5. %LocalAppData%\Roblox\Versions
    local_versions = LOCAL_APPDATA / 'Roblox' / 'Versions'
    for d in _scan_for_exe(local_versions, 1):
        _add(d)

    # 6. Live running RobloxPlayerBeta.exe install directory
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
            base = Path(meipass) / 'Fleasion' / 'modifications' / 'bundled'
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

    def __init__(self):
        self._pending_fast_flags: dict | None = None
        self._pending_framerate_cap: int | None = None
        self._lock = threading.Lock()

    def enqueue_fast_flags(self, settings: dict) -> None:
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

    def get_pending(self) -> tuple[dict | None, int | None]:
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

    entry_status_changed = pyqtSignal(str, str, str)  # (entry_id, status, error_msg)
    apply_started = pyqtSignal(str)   # entry_id
    apply_finished = pyqtSignal(str)  # entry_id
    restore_finished = pyqtSignal()

    def __init__(self, cache_scraper=None):
        super().__init__()
        self._cache_scraper = cache_scraper
        self._roblox_dirs: list[Path] = _find_roblox_dirs()
        self._stash_dir = MOD_ORIGINALS_DIR

        log_buffer.log('Modifications', f'Discovered {len(self._roblox_dirs)} Roblox dir(s)')

        # Ensure directories exist
        MOD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._stash_dir.mkdir(parents=True, exist_ok=True)

        # Lock that serialises all file-system writes/restores.  Prevents
        # a background apply thread from writing to dst after the main thread
        # has already restored the original (Apply → Reset race condition).
        self._fs_lock = threading.Lock()

        # Load persisted data
        self._data = self._load_json()

        # FastFlagManager
        self.fflag_manager = FastFlagManager(self._roblox_dirs, self._stash_dir)
        
        # GlobalSettingsManager (for Roblox GlobalBasicSettings_13.xml)
        self.global_settings_manager = GlobalSettingsManager(self._stash_dir)
        
        # Queue for pending modifications when Roblox is running
        self.pending_modifications_queue = PendingModificationsQueue()

    @property
    def roblox_dirs(self) -> list[Path]:
        return list(self._roblox_dirs)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_json(self) -> dict:
        if MODIFICATIONS_JSON.exists():
            try:
                with MODIFICATIONS_JSON.open('r', encoding='utf-8') as fp:
                    data = json.load(fp)
                # Deduplicate entries by target_path, keeping the last (most
                # recent) entry per path.  Duplicate entries could accumulate
                # from previous sessions with race-condition bugs.
                entries = data.get('entries', [])
                seen: dict[str, dict] = {}
                for e in entries:
                    tp = e.get('target_path', '')
                    if tp:
                        seen[tp] = e   # later entry wins
                    else:
                        # No target_path key — keep as-is (edge case)
                        seen[e.get('id', str(id(e)))] = e
                data['entries'] = list(seen.values())
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return {'entries': [], 'fast_flags_enabled': False, 'fast_flags': {}}

    def _save_json(self) -> None:
        MODIFICATIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
        with MODIFICATIONS_JSON.open('w', encoding='utf-8') as fp:
            json.dump(self._data, fp, indent=2)

    # ------------------------------------------------------------------
    # Entry CRUD
    # ------------------------------------------------------------------

    @property
    def entries(self) -> list[dict]:
        return self._data.setdefault('entries', [])

    def _find_entry(self, entry_id: str) -> dict | None:
        for e in self.entries:
            if e.get('id') == entry_id:
                return e
        return None

    def add_entry(self, entry: dict) -> str:
        """Add a new modification entry and eagerly apply it.

        If an entry with the same target_path already exists it is reused
        (acting as an update) to prevent duplicate entries from accumulating.
        """
        target = entry.get('target_path', '')
        if target:
            existing = next(
                (e for e in self.entries if e.get('target_path') == target), None
            )
            if existing is not None:
                # Reuse the existing entry — delegate to update_entry.
                existing_id = existing['id']
                self.update_entry(
                    existing_id,
                    source_type=entry.get('source_type'),
                    source_value=entry.get('source_value'),
                    display_name=entry.get('display_name', existing.get('display_name', '')),
                    **{k: v for k, v in entry.items()
                       if k not in ('id', 'status', 'error_message',
                                    'converted_cache_path', 'target_path',
                                    'source_type', 'source_value', 'display_name')},
                )
                return existing_id

        entry_id = str(uuid.uuid4())
        entry['id'] = entry_id
        entry.setdefault('status', 'pending')
        entry.setdefault('error_message', None)
        entry.setdefault('converted_cache_path', None)
        self.entries.append(entry)
        self._save_json()

        run_in_thread(self._process_and_apply_entry)(entry)
        return entry_id

    def remove_entry(self, entry_id: str) -> None:
        """Remove an entry and restore its original file."""
        entry = self._find_entry(entry_id)
        if entry is None:
            return
        self._restore_entry(entry)
        self._data['entries'] = [e for e in self.entries if e.get('id') != entry_id]
        self._save_json()
        # Notify the status bar that an entry was removed.
        self.restore_finished.emit()

    def update_entry(self, entry_id: str, **kwargs) -> None:
        """Update an entry's source, restore old files, and re-apply."""
        entry = self._find_entry(entry_id)
        if entry is None:
            return
        # Invalidate any in-flight apply so its background thread discards
        # its result instead of overwriting the freshly-written new file.
        entry['_apply_gen'] = entry.get('_apply_gen', 0) + 1
        # Only restore if there is an active source to undo.  When
        # source_type is None the entry was previously cleared and the
        # original Roblox file is already sitting at dst — calling
        # _restore_entry would incorrectly delete it via the "new file"
        # fallback branch.
        if entry.get('source_type') is not None:
            self._restore_entry(entry)
        entry.update(kwargs)
        entry['status'] = 'pending'
        entry['error_message'] = None
        self._save_json()
        run_in_thread(self._process_and_apply_entry)(entry)

    def clear_entry(self, entry_id: str) -> None:
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
            return
        # Invalidate any in-flight apply before restoring the original file.
        entry['_apply_gen'] = entry.get('_apply_gen', 0) + 1
        self._restore_entry(entry)
        self._data['entries'] = [e for e in self.entries if e.get('id') != entry_id]
        self._save_json()
        self.entry_status_changed.emit(entry_id, 'not_set', '')
        # Notify status bar that an active modification was cleared.
        self.restore_finished.emit()

    # ------------------------------------------------------------------
    # Processing & applying
    # ------------------------------------------------------------------

    def _process_and_apply_entry(self, entry: dict) -> None:
        """Resolve source, convert if needed, stash & write."""
        entry_id = entry['id']
        # Snapshot the generation counter before doing any work.  If
        # clear_entry or update_entry runs on the main thread while we are
        # processing, they increment _apply_gen and we discard our stale result.
        apply_gen = entry.get('_apply_gen', 0)
        self.apply_started.emit(entry_id)

        try:
            data = self._resolve_source(entry)
            if data is None:
                raise ValueError('Could not resolve source data')

            target = entry.get('target_path', '')

            # Font special-case
            if target.lower().endswith(('customfont.ttf',)) or entry.get('_is_font'):
                if not validate_font_bytes(data):
                    raise ValueError('Not a valid font file (invalid header)')
                apply_custom_font(data, self._roblox_dirs, self._stash_dir)
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

        except Exception as exc:
            if entry.get('_apply_gen', 0) == apply_gen:
                entry['status'] = 'error'
                entry['error_message'] = str(exc)
                self._save_json()
                self.entry_status_changed.emit(entry_id, 'error', str(exc))
            log_buffer.log('Modifications', f'Error applying {entry.get("display_name", "?")}: {exc}')

        self.apply_finished.emit(entry_id)

    def _resolve_source(self, entry: dict) -> bytes | None:
        """Resolve the entry's source to raw bytes."""
        src_type = entry.get('source_type')
        src_value = entry.get('source_value', '')

        if src_type == 'local_file':
            p = Path(src_value)
            if not p.is_file():
                raise FileNotFoundError(f'File not found: {src_value}')
            return p.read_bytes()

        if src_type == 'bundled':
            # e.g. "bundled:empty.mp3" → strip prefix
            name = src_value.replace('bundled:', '', 1) if src_value.startswith('bundled:') else src_value
            # Special sentinel: write a zero-byte file (unsupported extension fallback)
            if name == 'zero':
                return b''
            bp = _bundled_path(name)
            if not bp.is_file():
                raise FileNotFoundError(f'Bundled file not found: {name}')
            return bp.read_bytes()

        if src_type == 'asset_id':
            return self._fetch_asset(src_value)

        if src_type == 'cdn_url':
            return self._fetch_cdn_url(src_value)

        return None

    def _fetch_cdn_url(self, url: str) -> bytes:
        """Download a CDN URL, caching to ModCache."""
        import hashlib
        import urllib.request
        from urllib.parse import urlparse
        from urllib.error import URLError

        MOD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        # Preserve the file extension so _looks_like_obj can detect .obj files.
        ext = Path(urlparse(url).path).suffix.lower() or '.bin'
        cache_file = MOD_CACHE_DIR / f'cdn_{url_hash}{ext}'

        if cache_file.is_file():
            return cache_file.read_bytes()

        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0')
            with urllib.request.urlopen(req, timeout=30) as resp:
                data: bytes = resp.read()
        except URLError as exc:
            raise RuntimeError(f'CDN download failed: {exc}') from exc

        cache_file.write_bytes(data)
        return data

    def _fetch_asset(self, asset_id: str) -> bytes:
        """Download an asset by ID, caching to ModCache."""
        cache_file = MOD_CACHE_DIR / f'{asset_id}.bin'
        if cache_file.is_file():
            return cache_file.read_bytes()

        if self._cache_scraper is None:
            raise RuntimeError(
                'No cache scraper available. Asset ID download requires the proxy to be running.'
            )

        extra_hdrs = {}
        cookie = self._cache_scraper._get_roblosecurity()
        if cookie:
            extra_hdrs['Cookie'] = f'.ROBLOSECURITY={cookie};'
        data, status = self._cache_scraper._fetch_asset_with_place_id_retry(str(asset_id), extra_headers=extra_hdrs or None)
        if data is None:
            if status == 403:
                raise PermissionError(
                    'Asset not found or private. Add .ROBLOSECURITY cookie.'
                )
            raise RuntimeError(f'Asset download failed (HTTP {status})')

        cache_file.write_bytes(data)
        return data

    @staticmethod
    def _looks_like_obj(data: bytes) -> bool:
        """Heuristic: does this data look like a Wavefront OBJ?"""
        try:
            head = data[:512].decode('utf-8', errors='ignore')
            return head.lstrip().startswith(('v ', 'vn ', '#', 'o ', 'g '))
        except Exception:
            return False

    @staticmethod
    def _convert_obj_to_mesh(data: bytes) -> bytes:
        """Convert OBJ bytes → Roblox V2.00 .mesh bytes."""
        from ..cache.tools.solidmodel_converter.obj_to_mesh import (
            export_v2_mesh,
            parse_obj_for_mesh,
        )

        obj_text = data.decode('utf-8', errors='replace')
        vertices, colors, indices = parse_obj_for_mesh(obj_text)
        return export_v2_mesh(vertices, colors, indices)

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
        with self._fs_lock:
            for roblox_dir in self._roblox_dirs:
                dst = roblox_dir / target_path_rel
                stash = self._stash_dir / roblox_dir.name / target_path_rel
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
                dst.write_bytes(new_bytes)

    def _restore_entry(self, entry: dict) -> None:
        """Undo a single entry: restore the stash or delete the mod file."""
        target = entry.get('target_path', '')

        # Font special-case
        if target.lower().endswith(('customfont.ttf',)) or entry.get('_is_font'):
            restore_font_families(self._roblox_dirs, self._stash_dir)
            return

        with self._fs_lock:
            for roblox_dir in self._roblox_dirs:
                dst = roblox_dir / target
                stash = self._stash_dir / roblox_dir.name / target
                marker = stash.with_name(stash.name + self._NEW_FILE_MARKER_SUFFIX)
                if stash.exists():
                    shutil.copy2(stash, dst)
                    stash.unlink()
                elif marker.exists():
                    # Was a brand-new file (no original existed) — delete it
                    # and clean up the marker.
                    marker.unlink(missing_ok=True)
                    if dst.exists():
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
                dst = roblox_dir / target_path
                stash = self._stash_dir / roblox_dir.name / target_path
                marker = stash.with_name(stash.name + self._NEW_FILE_MARKER_SUFFIX)
                if stash.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(stash, dst)
                    stash.unlink()
                    restored = True
                elif marker.exists():
                    marker.unlink(missing_ok=True)
                    if dst.exists():
                        dst.unlink()
                    restored = True
            return restored

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def restore_all(self) -> None:
        """Restore every applied modification and fast-flags."""
        for entry in self.entries:
            if entry.get('status') == 'applied':
                try:
                    self._restore_entry(entry)
                except Exception as exc:
                    log_buffer.log('Modifications', f'Restore failed for {entry.get("display_name", "?")}: {exc}')

        if self._data.get('fast_flags_enabled'):
            try:
                self.fflag_manager.restore()
            except Exception as exc:
                log_buffer.log('FastFlags', f'Restore failed: {exc}')
        
        # Restore global settings
        try:
            self.global_settings_manager.restore()
        except Exception as exc:
            log_buffer.log('GlobalSettings', f'Restore failed: {exc}')

        self.restore_finished.emit()
        log_buffer.log('Modifications', 'All modifications restored')

    def reapply_all(self) -> None:
        """Re-apply all entries (crash recovery on startup)."""
        for entry in self.entries:
            if entry.get('source_type') and entry.get('source_value'):
                self._process_and_apply_entry(entry)

        if self._data.get('fast_flags_enabled') and self._data.get('fast_flags'):
            self.fflag_manager.write(self._data['fast_flags'])

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
            try:
                self.fflag_manager.restore()
            except Exception:
                pass
        self._save_json()

    @property
    def fast_flags(self) -> dict:
        return self._data.get('fast_flags', {})

    @fast_flags.setter
    def fast_flags(self, settings: dict) -> None:
        self._data['fast_flags'] = settings
        self._save_json()

    def write_fast_flags(self, settings: dict) -> None:
        """Update and write fast-flags to disk."""
        self._data['fast_flags'] = settings
        self._data['fast_flags_enabled'] = True
        self._save_json()
        self.fflag_manager.write(settings)

    def refresh_roblox_dirs(self) -> None:
        """Re-discover Roblox directories (e.g. after an update)."""
        self._roblox_dirs = _find_roblox_dirs()
        self.fflag_manager._roblox_dirs = self._roblox_dirs
        log_buffer.log('Modifications', f'Refreshed: {len(self._roblox_dirs)} Roblox dir(s)')

    def apply_pending_modifications(self) -> None:
        """Apply all pending modifications that were queued while Roblox was running."""
        flags, framerate = self.pending_modifications_queue.get_pending()
        
        if flags is not None:
            try:
                self.write_fast_flags(flags)
                log_buffer.log('Modifications', 'Applied queued Fast Flags after Roblox exit')
            except Exception as exc:
                log_buffer.log('Modifications', f'Error applying queued Fast Flags: {exc}')
        
        if framerate is not None:
            try:
                if framerate == 0:
                    self.global_settings_manager.restore()
                else:
                    run_in_thread(self.global_settings_manager.write)(framerate)
                log_buffer.log('Modifications', 'Applied queued framerate cap after Roblox exit')
            except Exception as exc:
                log_buffer.log('Modifications', f'Error applying queued framerate cap: {exc}')
