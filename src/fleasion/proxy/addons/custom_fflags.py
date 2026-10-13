"""Custom FastFlag response modifier for Roblox ClientSettings traffic."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from compression.zstd import ZstdError, compress as zstd_compress, decompress as zstd_decompress
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeIs

if TYPE_CHECKING:
    from collections.abc import Callable

if sys.platform == 'darwin':
    from fleasion.utils.platform_macos import find_roblox_resource_dirs

from fleasion.utils import log_buffer
from fleasion.utils.json_types import (
    JsonObject,
    JsonValue,
    as_json_array,
    as_json_object,
    as_object_dict,
)
from fleasion.utils.paths import CONFIG_FILE, LOCAL_APPDATA


class _CustomFFlagConfig(Protocol):
    @property
    def custom_fflags_enabled(self) -> bool: ...

    @property
    def custom_fflags(self) -> object: ...


def _is_custom_fflag_config(value: object) -> TypeIs[_CustomFFlagConfig]:
    return isinstance(getattr(value, 'custom_fflags_enabled', None), bool) and isinstance(
        getattr(value, 'custom_fflags', None), dict
    )


CLIENT_SETTINGS_APPLICATION_PATH = '/settings/application/'
CLIENT_SETTINGS_COMPRESSED_PATH = '/settings-compressed/application/'
BOOTSTRAPPER_CLIENT_SETTINGS_PLATFORM = 'PCClientBootstrapper'
DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG = 'DFIntSecondsBetweenDynamicVariableReloading'
DYNAMIC_VARIABLE_RELOAD_INTERVAL_SECONDS = '1'
WINDOWS_FLAG_CACHE_PATH = LOCAL_APPDATA / 'Temp' / 'Roblox' / 'cache' / 'flag_cache.dat'
MACOS_CLIENT_SETTINGS_REL = Path('ClientSettings') / 'ClientAppSettings.json'
CLIENT_SETTINGS_FAILURE_LOG_INTERVAL_SECONDS = 30.0
CLIENT_SETTINGS_STALE_SUCCESS_SECONDS = 15.0


# Protect only the flag-cache entry; sibling cache maintenance stays writable
LINUX_LEGACY_SEED_LOCKED_DIR_MODE = 0o555


def _linux_set_write_access(path: Path, *, enabled: bool) -> None:
    """Adjust write permission without widening read or execute access."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        updated = (
            mode | stat.S_IWUSR if enabled else mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        )
        if updated != mode:
            path.chmod(updated)
    except FileNotFoundError:
        return
    except OSError as exc:
        log_buffer.log('CustomFFlags', f'Could not update flag-cache permissions for {path}: {exc}')


def _lock_linux_flag_cache(cache_path: Path) -> None:
    """Protect the seeded entry from in-place writes without locking its directory."""
    _linux_set_write_access(cache_path, enabled=False)


def _unlock_linux_flag_cache(cache_dir: Path, cache_path: Path) -> None:
    """Recover file write access and the directory lock used by earlier versions."""
    try:
        if stat.S_IMODE(cache_dir.stat().st_mode) == LINUX_LEGACY_SEED_LOCKED_DIR_MODE:
            _linux_set_write_access(cache_dir, enabled=True)
    except FileNotFoundError:
        return
    except OSError as exc:
        log_buffer.log(
            'CustomFFlags', f'Could not inspect legacy flag-cache directory {cache_dir}: {exc}'
        )
    _linux_set_write_access(cache_path, enabled=True)


def _find_macos_resource_dirs() -> list[Path]:
    if sys.platform != 'darwin':
        return []
    return find_roblox_resource_dirs(include_studio=False)


def normalize_flag_value(value: object) -> str:
    """Return the string representation Roblox uses for FastFlag values."""
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    msg = 'FastFlag values must be strings, numbers, or booleans'
    raise ValueError(msg)


def normalize_custom_fflags(value: object) -> dict[str, str]:
    """Validate and normalize a custom FastFlag mapping."""
    value_map = as_object_dict(value)
    if value_map is None:
        return {}

    normalized: dict[str, str] = {}
    for raw_name, raw_value in value_map.items():
        name = str(raw_name).strip()
        if not name:
            continue
        try:
            normalized[name] = normalize_flag_value(raw_value)
        except ValueError:
            continue
    return normalized


class CustomFFlagModifier:
    """Merge user-defined flags into Roblox's remote and startup settings."""

    def __init__(
        self,
        config_manager: object,
        *,
        flag_cache_path: Path | None = None,
        settings_path: Path | None = None,
        reload_settings_from_disk: bool = False,
        macos_resource_dirs: list[Path] | None = None,
        linux_flag_cache_path: Path | None = None,
    ) -> None:
        if not _is_custom_fflag_config(config_manager):
            msg = 'Custom FastFlag config is missing its enabled state or flag mapping'
            raise TypeError(msg)
        self.config_manager = config_manager
        self._flag_cache_path = flag_cache_path
        self._linux_flag_cache_path = linux_flag_cache_path
        self._macos_resource_dirs = (
            list(macos_resource_dirs) if macos_resource_dirs is not None else None
        )
        self._macos_seeded_flag_names: set[str] = set()
        self._windows_seeded_flag_names: set[str] = set()
        self._linux_seeded_flag_names: set[str] = set()
        self._last_fresh_response_flags: tuple[tuple[str, str], ...] | None = None
        self._delivery_generation = 0
        self._delivery_state_lock = threading.Lock()
        self._delivery_notification_callback: Callable[[str, str], None] | None = None
        self._notification_generation = 0
        self._delivery_notifications_sent = 0
        self._settings_path = settings_path or (CONFIG_FILE if reload_settings_from_disk else None)
        self._settings_signature: tuple[int, int] | None = None
        self._disk_enabled: bool | None = None
        self._disk_flags: JsonObject | None = None
        self._disk_disabled: list[JsonValue] | None = None
        self._disk_folders: JsonObject | None = None
        self._disk_disabled_folders: list[JsonValue] | None = None
        self._last_response_success_at: float | None = None
        self._first_response_failure_at: float | None = None
        self._last_failure_log_at: dict[str, float] = {}
        if reload_settings_from_disk:
            self.release_linux_flag_cache_lock()

    @staticmethod
    def _flag_signature(flags: dict[str, str]) -> tuple[tuple[str, str], ...]:
        """Return a stable signature for one delivered override set."""
        return tuple(sorted(flags.items()))

    def delivery_generation(self) -> int:
        """Return the current Player delivery generation.

        Requests capture this value before contacting Roblox.  A relaunch bumps
        the generation so an older Player's late ClientSettings response cannot
        satisfy the fresh-response requirement for the new Player.
        """
        with self._delivery_state_lock:
            return self._delivery_generation

    def set_delivery_notification_callback(
        self, callback: Callable[[str, str], None] | None
    ) -> None:
        """Set the callback used to surface delivery-progress notifications."""
        self._delivery_notification_callback = callback

    def note_response_success(
        self,
        delivered_signature: tuple[tuple[str, str], ...] | None = None,
        *,
        generation: int | None = None,
    ) -> bool:
        """Record a ClientSettings response that carried Fleasion's overrides.

        Fresh-response state is committed only after the client-facing writer
        successfully drains.  This keeps a changed flag set armed when the
        upstream request fails, response decoding fails, or the client
        disconnects before delivery.  Responses from an older Player delivery
        generation are ignored after a relaunch has armed the next Player.
        """
        if delivered_signature is None:
            delivered_signature = self._flag_signature(self.runtime_flags())
        notification: str | None = None
        with self._delivery_state_lock:
            if generation is not None and generation != self._delivery_generation:
                return False
            success_at = time.monotonic()
            self._last_fresh_response_flags = delivered_signature
            self._last_response_success_at = success_at
            self._first_response_failure_at = None
            self._last_failure_log_at.clear()
            notification = self._advance_delivery_notifications_locked()
        if notification is not None:
            self._dispatch_delivery_notification(notification)
        return True

    def note_client_settings_seen(self, *, generation: int | None = None) -> bool:
        """Record a ClientSettings round trip that kept the live loop proven.

        A 304 Not Modified (or otherwise bodyless success) means the client
        kept its cached - already modified - copy, so Fleasion's flags remain
        applied even though no body was intercepted.  This advances the
        delivery notifications without touching fresh-response bookkeeping,
        which must only reflect responses whose body was actually inspected.
        """
        if not self.is_enabled():
            return False
        active_signature = self._flag_signature(self.runtime_flags())
        notification: str | None = None
        with self._delivery_state_lock:
            if generation is not None and generation != self._delivery_generation:
                return False
            if self._last_fresh_response_flags != active_signature:
                return False
            notification = self._advance_delivery_notifications_locked()
        if notification is not None:
            self._dispatch_delivery_notification(notification)
        return True

    def _advance_delivery_notifications_locked(self) -> str | None:
        """Return the next notification message to dispatch, if any.

        Closing and reopening the client bumps the delivery generation, which
        re-arms both notifications so a fresh Player sees them in the right
        order.  Late responses from the old process are rejected by the
        generation check and can never advance this counter.
        """
        if self._delivery_notification_callback is None or not self.is_enabled():
            return None
        if self._notification_generation != self._delivery_generation:
            self._notification_generation = self._delivery_generation
            self._delivery_notifications_sent = 0
        if self._delivery_notifications_sent >= 2:
            return None
        self._delivery_notifications_sent += 1
        if self._delivery_notifications_sent == 1:
            return 'Dynamic FFlags applied'
        return 'Live FFlag editing now available'

    def _dispatch_delivery_notification(self, message: str) -> None:
        """Forward one delivery notification; never let UI faults break the proxy."""
        callback = self._delivery_notification_callback
        if callback is None:
            return
        try:
            callback('Fleasion', message)
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log(
                'CustomFFlags',
                f'Failed to dispatch FastFlag delivery notification: {exc}',
            )

    def log_response_failure(self, key: str, message: str) -> None:
        """Rate-limit repeated ClientSettings failures while keeping stalls visible."""
        now = time.monotonic()
        with self._delivery_state_lock:
            if self._first_response_failure_at is None:
                self._first_response_failure_at = now

            last_log = self._last_failure_log_at.get(key)
            if (
                last_log is not None
                and now - last_log < CLIENT_SETTINGS_FAILURE_LOG_INTERVAL_SECONDS
            ):
                return
            self._last_failure_log_at[key] = now

            reference = self._last_response_success_at
            if reference is None:
                reference = self._first_response_failure_at
            stale_for = max(0.0, now - reference)
            if stale_for >= CLIENT_SETTINGS_STALE_SUCCESS_SECONDS:
                message = (
                    f'{message}; no successfully delivered ClientSettings response '
                    f'for {stale_for:.0f}s'
                )
        log_buffer.log('CustomFFlags', message)

    def _refresh_settings_from_disk(self) -> None:
        """Refresh only the custom-flag fields when the saved settings change."""
        if self._settings_path is None:
            return
        try:
            stat_result = self._settings_path.stat()
            signature = (stat_result.st_mtime_ns, stat_result.st_size)
            if signature == self._settings_signature:
                return
            data_value: object = json.loads(self._settings_path.read_text(encoding='utf-8'))
        except OSError, UnicodeDecodeError, json.JSONDecodeError:
            return

        self._settings_signature = signature
        data = as_json_object(data_value)
        if data is not None:
            self._disk_enabled = bool(data.get('custom_fflags_enabled', False))
            saved_flags = as_json_object(data.get('custom_fflags', {}))
            self._disk_flags = saved_flags or {}
            disabled = as_json_array(data.get('custom_fflag_disabled', []))
            self._disk_disabled = disabled or []
            folders = as_json_object(data.get('custom_fflag_folders', {}))
            self._disk_folders = folders or {}
            disabled_folders = as_json_array(data.get('custom_fflag_disabled_folders', []))
            self._disk_disabled_folders = disabled_folders or []

    def is_enabled(self) -> bool:
        self._refresh_settings_from_disk()
        if self._disk_enabled is not None:
            return self._disk_enabled
        return self.config_manager.custom_fflags_enabled

    @staticmethod
    def handles_path(path: str) -> bool:
        """Return whether this is a Player ClientSettings document to modify.

        The Windows bootstrapper reads its own ClientSettings document before
        it starts Roblox Player.  It must travel through the TLS proxy unchanged
        so enabling custom FastFlags before launch cannot delay or block the
        bootstrapper.  Every non-bootstrapper application document remains
        eligible, preserving the existing Android/macOS behavior.
        """
        path_only = str(path or '').split('?', 1)[0]
        is_application_settings = (
            CLIENT_SETTINGS_APPLICATION_PATH in path_only
            or CLIENT_SETTINGS_COMPRESSED_PATH in path_only
        )
        return is_application_settings and BOOTSTRAPPER_CLIENT_SETTINGS_PLATFORM not in path_only

    def runtime_flags(self) -> dict[str, str]:
        """Return saved flags plus Fleasion's non-persisted refresh companion."""
        self._refresh_settings_from_disk()
        saved_flags = (
            self._disk_flags if self._disk_flags is not None else self.config_manager.custom_fflags
        )
        flags = normalize_custom_fflags(saved_flags)
        disabled = (
            self._disk_disabled
            if self._disk_disabled is not None
            else getattr(self.config_manager, 'custom_fflag_disabled', [])
        )
        disabled_names = {str(name).strip() for name in disabled}
        folders = (
            self._disk_folders
            if self._disk_folders is not None
            else getattr(self.config_manager, 'custom_fflag_folders', {})
        )
        disabled_folders = (
            self._disk_disabled_folders
            if self._disk_disabled_folders is not None
            else getattr(self.config_manager, 'custom_fflag_disabled_folders', [])
        )
        folder_mapping = as_json_object(folders) or {}
        disabled_folder_names = {str(name).strip() for name in disabled_folders}
        for folder_name in disabled_folder_names:
            members = as_json_array(folder_mapping.get(folder_name, [])) or []
            disabled_names.update(str(name).strip() for name in members)
        flags = {name: value for name, value in flags.items() if name not in disabled_names}
        # Roblox/Sober reads the reloader interval before applying the response
        # it has just fetched. Therefore, when this companion flag first
        # arrives through Sober's 120-second dynamic fetch, its next wait can
        # still be 120 seconds. The following refresh uses this one-second
        # interval. It deliberately overrides any saved value and is never
        # persisted to the user's custom flag list.
        flags[DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG] = DYNAMIC_VARIABLE_RELOAD_INTERVAL_SECONDS
        return flags

    def requires_fresh_response(self) -> bool:
        """Return whether changed overrides still need successful delivery.

        Roblox normally answers the one-second reloader request with HTTP 304.
        That is ideal when flags have not changed, but it cannot deliver a
        newly added, changed, or removed override.  Keep stripping conditional
        headers until a response carrying the active override set is actually
        delivered to Roblox; ``note_response_success`` commits that delivery.
        """
        active_flags = self._flag_signature(self.runtime_flags())
        with self._delivery_state_lock:
            return active_flags != self._last_fresh_response_flags

    def prepare_for_player_launch(self) -> None:
        """Force a fresh ClientSettings response for the next Player instance.

        The proxy modifier outlives Roblox Player across Env Proxy relaunches.
        Roblox can therefore send the new process a conditional request for a
        flag set that this modifier has already seen, which would otherwise
        allow a cached 304 response through until Roblox performs its next
        normal refresh.  Reset the per-process delivery marker so the first
        ClientSettings request of every relaunch gets one fresh response.  The
        generation bump also prevents a late response from the outgoing Player
        from satisfying the new Player's delivery requirement.
        """
        with self._delivery_state_lock:
            self._delivery_generation += 1
            self._last_fresh_response_flags = None

    def prime_windows_flag_cache(self) -> bool:
        """Synchronize active overrides into Roblox's Windows flag cache.

        Some flags, including the task-scheduler target FPS, are consumed before
        the dynamic reloader's first network request.  Roblox's current cache
        layout is a four-byte signature length, that many signature bytes, one
        compression byte (0 = raw, 1 = zstd), then the ClientSettings JSON.  We
        preserve the header, remove stale overrides when disabled, and replace
        the JSON atomically, keeping the original compression.  Disabled mode
        never adds flags; it only clears values previously seeded by Fleasion.
        """
        if self._flag_cache_path is None and sys.platform != 'win32':
            return False

        cache_path = self._flag_cache_path or WINDOWS_FLAG_CACHE_PATH
        changed, flags, removed_names = self._prime_flag_cache_file(
            cache_path, self._windows_seeded_flag_names
        )
        self._windows_seeded_flag_names = set(flags)
        if changed:
            self._log_flag_cache_seed(flags, removed_names)
        return changed

    def prime_linux_flag_cache(self) -> bool:
        """Seed custom flags into Sober's flag_cache.dat before launch.

        The file mirrors the Windows flag cache layout (little-endian
        signature length, signature blob, compression byte, then either a raw
        or zstd-compressed JSON body).  Sober's Player loads this cache before
        it issues its first ClientSettings request, so seeding it makes
        startup-only custom flags available immediately — the Linux
        counterpart of the Windows flag cache.  Sober's own fetched defaults
        and unrelated entries are preserved.  After a successful seed the
        cache file is made read-only to prevent in-place writes. Its parent
        stays writable for unrelated cache maintenance; atomic replacement
        by the client remains possible. Startup recovery and every re-seed
        restore write access to this entry. Disabled mode never adds flags;
        it only clears values previously seeded by Fleasion.
        """
        if self._linux_flag_cache_path is None and not sys.platform.startswith('linux'):
            return False

        cache_path = self._linux_flag_cache_path
        if cache_path is None:
            from fleasion.utils.platform_linux import (  # ruff: ignore[import-outside-top-level]
                SOBER_FLAG_CACHE_PATH,
            )

            cache_path = SOBER_FLAG_CACHE_PATH
        cache_dir = cache_path.parent
        _unlock_linux_flag_cache(cache_dir, cache_path)
        changed, flags, removed_names = self._prime_flag_cache_file(
            cache_path, self._linux_seeded_flag_names
        )
        self._linux_seeded_flag_names = set(flags)
        if changed:
            self._log_flag_cache_seed(flags, removed_names, label='Sober flag cache')
        if flags and self.is_enabled():
            _lock_linux_flag_cache(cache_path)
        return changed

    def release_linux_flag_cache_lock(self) -> None:
        """Restore cache write access when Fleasion stops managing delivery."""
        if self._linux_flag_cache_path is None and not sys.platform.startswith('linux'):
            return
        cache_path = self._linux_flag_cache_path
        if cache_path is None:
            from fleasion.utils.platform_linux import (  # ruff: ignore[import-outside-top-level]
                SOBER_FLAG_CACHE_PATH,
            )

            cache_path = SOBER_FLAG_CACHE_PATH
        _unlock_linux_flag_cache(cache_path.parent, cache_path)

    def _prime_flag_cache_file(  # ruff: ignore[too-many-return-statements]
        self,
        cache_path: Path,
        seeded_names: set[str],
    ) -> tuple[bool, dict[str, str], set[str]]:
        """Merge active overrides into one flag cache file.

        Returns whether the file changed, the flag set now recorded as seeded,
        and the names removed as stale.  Never adds flags while disabled; it
        only clears values previously seeded by Fleasion.  Supports the raw
        (compression byte 0) and zstd (byte 1) layouts and preserves the
        original compression on write.
        """
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            original_mode = stat.S_IMODE(cache_path.stat().st_mode)
            raw = cache_path.read_bytes()
            if len(raw) < 5:
                return False, {}, set()
            signature_length = int.from_bytes(raw[:4], 'little')
            compression_offset = 4 + signature_length
            payload_offset = compression_offset + 1
            if payload_offset >= len(raw):
                return False, {}, set()
            compression = raw[compression_offset]
            compressed_payload = raw[payload_offset:]
            if compression == 0:
                payload_bytes = compressed_payload
            elif compression == 1:
                payload_bytes = zstd_decompress(compressed_payload)
            else:
                return False, {}, set()

            payload_value: object = json.loads(payload_bytes)
            payload = as_json_object(payload_value)
            if payload is None:
                return False, {}, set()
            app_settings = as_json_object(payload.get('applicationSettings'))
            if app_settings is None:
                return False, {}, set()

            enabled = self.is_enabled()
            flags = self.runtime_flags() if enabled else {}
            self._refresh_settings_from_disk()
            saved_flags = (
                self._disk_flags
                if self._disk_flags is not None
                else getattr(self.config_manager, 'custom_fflags', {})
            )
            saved_names = set(normalize_custom_fflags(saved_flags))
            stale_names = (
                seeded_names | saved_names | {DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG}
            ) - set(flags)
            removed_names = {
                name for name in stale_names if app_settings.pop(name, None) is not None
            }
            app_settings.update(flags)
            updated_payload = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode(
                'utf-8'
            )
            updated = raw[:payload_offset] + (
                updated_payload if compression == 0 else zstd_compress(updated_payload)
            )
            if updated == raw:
                return False, flags, set()
            temporary_path = cache_path.with_name(f'.{cache_path.name}.{os.getpid()}.tmp')
            try:
                temporary_path.write_bytes(updated)
                temporary_path.chmod(original_mode)
                temporary_path.replace(cache_path)
            finally:
                temporary_path.unlink(missing_ok=True)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ZstdError,
        ):
            return False, {}, set()
        return True, flags, removed_names

    @staticmethod
    def _log_flag_cache_seed(
        flags: dict[str, str],
        removed_names: set[str],
        *,
        label: str = 'Roblox flag cache',
    ) -> None:
        if flags:
            log_buffer.log(
                'CustomFFlags',
                f'Pre-seeded {label} with {len(flags)} custom FastFlag(s)',
            )
        elif removed_names:
            log_buffer.log(
                'CustomFFlags',
                f'Removed {len(removed_names)} disabled custom FastFlag(s) from {label}',
            )

    def _macos_client_settings_paths(self) -> list[Path]:
        """Return live Player ClientSettings files used during macOS startup."""
        resource_dirs = self._macos_resource_dirs
        if resource_dirs is None:
            if sys.platform != 'darwin':
                return []
            try:
                resource_dirs = _find_macos_resource_dirs()
            except ImportError, OSError:
                return []

        paths: list[Path] = []
        for resource_dir in resource_dirs:
            if not (
                resource_dir.name == 'Resources'
                and resource_dir.parent.name == 'Contents'
                and resource_dir.parent.parent.suffix == '.app'
            ):
                continue
            paths.append(resource_dir / MACOS_CLIENT_SETTINGS_REL)
        return paths

    @staticmethod
    def _clear_read_only(path: Path) -> None:
        """Make a locally-owned Roblox settings file writable for one atomic update."""
        try:
            mode = path.stat().st_mode
            if not mode & stat.S_IWRITE:
                path.chmod(mode | stat.S_IWRITE)
        except OSError:
            pass

    @staticmethod
    def _load_macos_client_settings(target: Path) -> JsonObject | None:
        if not target.exists():
            return {}
        try:
            loaded_value: object = json.loads(target.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            log_buffer.log(
                'CustomFFlags',
                f'Could not decode macOS ClientSettings file; left unchanged: {target}',
            )
            return None
        loaded = as_json_object(loaded_value)
        if loaded is None:
            log_buffer.log(
                'CustomFFlags',
                f'macOS ClientSettings root was not an object; left unchanged: {target}',
            )
        return loaded

    def _prime_macos_client_settings_path_unchecked(
        self,
        target: Path,
        flags: dict[str, str],
        stale_names: set[str],
    ) -> bool:
        existing = self._load_macos_client_settings(target)
        if existing is None:
            return False
        merged = dict(existing)
        for name in stale_names:
            merged.pop(name, None)
        merged.update(flags)
        if merged == existing:
            return False

        original_mode = None
        if target.exists():
            original_mode = stat.S_IMODE(target.stat().st_mode)
            self._clear_read_only(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f'.{target.name}.fleasion-{os.getpid()}.tmp')
        try:
            temporary.write_text(json.dumps(merged, indent=2), encoding='utf-8')
            if original_mode is not None:
                temporary.chmod(original_mode)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return True

    def _prime_macos_client_settings_path(
        self,
        target: Path,
        flags: dict[str, str],
        stale_names: set[str],
    ) -> bool:
        try:
            return self._prime_macos_client_settings_path_unchecked(
                target,
                flags,
                stale_names,
            )
        except (OSError, UnicodeDecodeError, TypeError, ValueError) as exc:
            log_buffer.log(
                'CustomFFlags',
                f'Failed to seed macOS ClientSettings file {target}: {exc}',
            )
            return False

    def prime_macos_client_settings(self) -> bool:
        """Seed custom flags into Player's local macOS startup settings."""
        paths = self._macos_client_settings_paths()
        if not paths:
            return False

        enabled = self.is_enabled()
        flags = self.runtime_flags() if enabled else {}
        desired_names = set(flags)
        saved_names: set[str] = set()
        if not enabled:
            self._refresh_settings_from_disk()
            saved_flags = (
                self._disk_flags
                if self._disk_flags is not None
                else self.config_manager.custom_fflags
            )
            saved_names = set(normalize_custom_fflags(saved_flags))
            saved_names.add(DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG)
        stale_names = (self._macos_seeded_flag_names | saved_names) - desired_names
        updated_paths = sum(
            self._prime_macos_client_settings_path(target, flags, stale_names) for target in paths
        )
        self._macos_seeded_flag_names = desired_names
        return bool(updated_paths)

    def prime_startup_flag_cache(self) -> bool:
        """Seed the platform-specific local flag source used before networking."""
        # Explicit resource-dir injection is also the cross-platform test and
        # helper contract for macOS; it must take precedence over the host OS.
        if self._macos_resource_dirs is not None or sys.platform == 'darwin':
            return self.prime_macos_client_settings()
        if self._linux_flag_cache_path is not None or sys.platform.startswith('linux'):
            return self.prime_linux_flag_cache()
        return self.prime_windows_flag_cache()

    @staticmethod
    def body_carries_signature(
        body: bytes,
        delivered_signature: tuple[tuple[str, str], ...],
    ) -> bool:
        """Return whether a final plain ClientSettings body still carries a signature."""
        try:
            payload_value: object = json.loads(body)
        except json.JSONDecodeError, UnicodeDecodeError:
            return False
        payload = as_json_object(payload_value)
        if payload is None:
            return False
        application_settings = as_json_object(payload.get('applicationSettings'))
        if application_settings is None:
            return False
        return all(application_settings.get(name) == value for name, value in delivered_signature)

    def modify_response_with_delivery(
        self,
        path: str,
        body: bytes,
    ) -> tuple[bytes, tuple[tuple[str, str], ...] | None]:
        """Merge overrides and return the exact flag-set signature now carried.

        The signature is non-None whenever the resulting ClientSettings body
        already contained or was successfully updated with every active
        override.  Callers use it only after the response is delivered to
        Roblox, so freshness is never acknowledged merely because processing
        began.
        """
        if not self.is_enabled() or not self.handles_path(path):
            return body, None

        flags = self.runtime_flags()
        delivered_signature = self._flag_signature(flags)

        try:
            payload_value = json.loads(body)
        except json.JSONDecodeError, UnicodeDecodeError:
            self.log_response_failure(
                'decode',
                f'Could not decode ClientSettings response for {path[:160]}; response left unchanged',
            )
            return body, None

        payload = as_json_object(payload_value)
        if payload is None:
            self.log_response_failure(
                'invalid-root',
                f'ClientSettings response for {path[:160]} was not a JSON object; response left unchanged',
            )
            return body, None

        application_settings = as_json_object(payload.get('applicationSettings'))
        if application_settings is None:
            self.log_response_failure(
                'missing-application-settings',
                f'ClientSettings response for {path[:160]} had no applicationSettings object; response left unchanged',
            )
            return body, None

        if all(application_settings.get(name) == value for name, value in flags.items()):
            return body, delivered_signature

        application_settings.update(flags)
        modified = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        return modified, delivered_signature

    def modify_response(self, path: str, body: bytes) -> bytes:
        """Return a ClientSettings JSON response with configured overrides merged in."""
        modified, _delivered_signature = self.modify_response_with_delivery(path, body)
        return modified
