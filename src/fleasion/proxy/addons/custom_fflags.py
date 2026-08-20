"""Custom FastFlag response modifier for Roblox ClientSettings traffic."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from ...utils import log_buffer
from ...utils.paths import CONFIG_FILE, LOCAL_APPDATA


CLIENT_SETTINGS_APPLICATION_PATH = '/settings/application/'
CLIENT_SETTINGS_COMPRESSED_PATH = '/settings-compressed/application/'
BOOTSTRAPPER_CLIENT_SETTINGS_PLATFORM = 'PCClientBootstrapper'
DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG = 'DFIntSecondsBetweenDynamicVariableReloading'
DYNAMIC_VARIABLE_RELOAD_INTERVAL_SECONDS = '1'
WINDOWS_FLAG_CACHE_PATH = LOCAL_APPDATA / 'Temp' / 'Roblox' / 'cache' / 'flag_cache.dat'
MACOS_CLIENT_SETTINGS_REL = Path('ClientSettings') / 'ClientAppSettings.json'


def normalize_flag_value(value: Any) -> str:
    """Return the string representation Roblox uses for FastFlag values."""
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    raise ValueError('FastFlag values must be strings, numbers, or booleans')


def normalize_custom_fflags(value: Any) -> dict[str, str]:
    """Validate and normalize a custom FastFlag mapping."""
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, str] = {}
    for raw_name, raw_value in value.items():
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
        config_manager,
        flag_cache_path: Path | None = None,
        settings_path: Path | None = None,
        reload_settings_from_disk: bool = False,
        macos_resource_dirs: list[Path] | None = None,
    ):
        self.config_manager = config_manager
        self._flag_cache_path = flag_cache_path
        self._macos_resource_dirs = (
            list(macos_resource_dirs) if macos_resource_dirs is not None else None
        )
        self._macos_seeded_flag_names: set[str] = set()
        self._windows_seeded_flag_names: set[str] = set()
        self._last_fresh_response_flags: tuple[tuple[str, str], ...] | None = None
        self._settings_path = settings_path or (CONFIG_FILE if reload_settings_from_disk else None)
        self._settings_signature: tuple[int, int] | None = None
        self._disk_enabled: bool | None = None
        self._disk_flags: dict | None = None
        self._disk_disabled: list | None = None

    def _refresh_settings_from_disk(self) -> None:
        """Refresh only the custom-flag fields when the saved settings change."""
        if self._settings_path is None:
            return
        try:
            stat_result = self._settings_path.stat()
            signature = (stat_result.st_mtime_ns, stat_result.st_size)
            if signature == self._settings_signature:
                return
            data = json.loads(self._settings_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return

        self._settings_signature = signature
        if isinstance(data, dict):
            self._disk_enabled = bool(data.get('custom_fflags_enabled', False))
            saved_flags = data.get('custom_fflags', {})
            self._disk_flags = saved_flags if isinstance(saved_flags, dict) else {}
            disabled = data.get('custom_fflag_disabled', [])
            self._disk_disabled = disabled if isinstance(disabled, list) else []

    def is_enabled(self) -> bool:
        self._refresh_settings_from_disk()
        if self._disk_enabled is not None:
            return self._disk_enabled
        return bool(getattr(self.config_manager, 'custom_fflags_enabled', False))

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
        return (
            is_application_settings
            and BOOTSTRAPPER_CLIENT_SETTINGS_PLATFORM not in path_only
        )

    def runtime_flags(self) -> dict[str, str]:
        """Return saved flags plus Fleasion's non-persisted refresh companion."""
        self._refresh_settings_from_disk()
        saved_flags = (
            self._disk_flags
            if self._disk_flags is not None
            else getattr(self.config_manager, 'custom_fflags', {})
        )
        flags = normalize_custom_fflags(saved_flags)
        disabled = (
            self._disk_disabled
            if self._disk_disabled is not None
            else getattr(self.config_manager, 'custom_fflag_disabled', [])
        )
        disabled_names = {str(name).strip() for name in disabled}
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
        """Return whether changed overrides need one non-conditional response.

        Roblox normally answers the one-second reloader request with HTTP 304.
        That is ideal when flags have not changed, but it cannot deliver a
        newly added, changed, or removed override.  The proxy uses this marker
        to make exactly one normal 200 response available after each change.
        """
        active_flags = tuple(sorted(self.runtime_flags().items()))
        if active_flags == self._last_fresh_response_flags:
            return False
        self._last_fresh_response_flags = active_flags
        return True

    def prepare_for_player_launch(self) -> None:
        """Force a fresh ClientSettings response for the next Player instance.

        The proxy modifier outlives Roblox Player across Env Proxy relaunches.
        Roblox can therefore send the new process a conditional request for a
        flag set that this modifier has already seen, which would otherwise
        allow a cached 304 response through until Roblox performs its next
        normal refresh.  Reset the per-process delivery marker so the first
        ClientSettings request of every relaunch gets one fresh response.
        """
        self._last_fresh_response_flags = None

    def prime_windows_flag_cache(self) -> bool:
        """Synchronize active overrides into Roblox's uncompressed Windows flag cache.

        Some flags, including the task-scheduler target FPS, are consumed before
        the dynamic reloader's first network request.  Roblox's current cache
        layout is a four-byte signature length, that many signature bytes, one
        compression byte, then the ClientSettings JSON.  We preserve the header,
        remove stale overrides when disabled, and replace the JSON atomically
        only for the known uncompressed layout.  Disabled mode never adds flags;
        it only clears values previously seeded by Fleasion.
        """
        if self._flag_cache_path is None and sys.platform != 'win32':
            return False

        cache_path = self._flag_cache_path or WINDOWS_FLAG_CACHE_PATH
        try:
            raw = cache_path.read_bytes()
            if len(raw) < 5:
                return False
            signature_length = int.from_bytes(raw[:4], 'little')
            compression_offset = 4 + signature_length
            payload_offset = compression_offset + 1
            if payload_offset >= len(raw) or raw[compression_offset] != 0:
                return False

            payload = json.loads(raw[payload_offset:])
            application_settings = payload.get('applicationSettings')
            if not isinstance(application_settings, dict):
                return False

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
                self._windows_seeded_flag_names
                | saved_names
                | {DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG}
            ) - set(flags)
            removed_names = {
                name for name in stale_names if application_settings.pop(name, None) is not None
            }
            application_settings.update(flags)
            updated = raw[:payload_offset] + json.dumps(
                payload, separators=(',', ':'), ensure_ascii=False
            ).encode('utf-8')
            self._windows_seeded_flag_names = set(flags)
            if updated == raw:
                return False
            temporary_path = cache_path.with_name(f'.{cache_path.name}.{os.getpid()}.tmp')
            try:
                temporary_path.write_bytes(updated)
                temporary_path.replace(cache_path)
            finally:
                temporary_path.unlink(missing_ok=True)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return False

        if flags:
            log_buffer.log(
                'CustomFFlags',
                f'Pre-seeded Roblox flag cache with {len(flags)} custom FastFlag(s)',
            )
        elif removed_names:
            log_buffer.log(
                'CustomFFlags',
                f'Removed {len(removed_names)} disabled custom FastFlag(s) from Roblox flag cache',
            )
        return True

    def _macos_client_settings_paths(self) -> list[Path]:
        """Return live Player ClientSettings files used during macOS startup."""
        resource_dirs = self._macos_resource_dirs
        if resource_dirs is None:
            if sys.platform != 'darwin':
                return []
            try:
                from ...utils.platform_macos import find_roblox_resource_dirs

                resource_dirs = find_roblox_resource_dirs(include_studio=False)
            except Exception:
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

    def prime_macos_client_settings(self) -> bool:
        """Seed custom flags into Player's local macOS startup settings.

        Roblox loads the Resources ClientSettings file before its first remote
        ClientSettings request.  Seeding the file makes startup-only custom
        flags available immediately; the proxy response path remains in place
        for live changes after launch.
        """
        paths = self._macos_client_settings_paths()
        if not paths:
            return False

        enabled = self.is_enabled()
        flags = self.runtime_flags() if enabled else {}
        desired_names = set(flags)
        saved_names = set()
        if not enabled:
            self._refresh_settings_from_disk()
            saved_flags = (
                self._disk_flags
                if self._disk_flags is not None
                else getattr(self.config_manager, 'custom_fflags', {})
            )
            saved_names = set(normalize_custom_fflags(saved_flags))
            saved_names.add(DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG)
        stale_names = (self._macos_seeded_flag_names | saved_names) - desired_names
        updated_paths = 0

        for target in paths:
            try:
                existing: dict[str, Any]
                if target.exists():
                    try:
                        loaded = json.loads(target.read_text(encoding='utf-8'))
                    except json.JSONDecodeError:
                        log_buffer.log(
                            'CustomFFlags',
                            f'Could not decode macOS ClientSettings file; left unchanged: {target}',
                        )
                        continue
                    if not isinstance(loaded, dict):
                        log_buffer.log(
                            'CustomFFlags',
                            f'macOS ClientSettings root was not an object; left unchanged: {target}',
                        )
                        continue
                    existing = loaded
                else:
                    existing = {}

                merged = dict(existing)
                for name in stale_names:
                    merged.pop(name, None)
                merged.update(flags)
                if merged == existing:
                    continue

                original_mode = None
                if target.exists():
                    original_mode = stat.S_IMODE(target.stat().st_mode)
                    self._clear_read_only(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(
                    f'.{target.name}.fleasion-{os.getpid()}.tmp'
                )
                try:
                    temporary.write_text(json.dumps(merged, indent=2), encoding='utf-8')
                    if original_mode is not None:
                        temporary.chmod(original_mode)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
                updated_paths += 1
            except (OSError, UnicodeDecodeError, TypeError, ValueError) as exc:
                log_buffer.log(
                    'CustomFFlags',
                    f'Failed to seed macOS ClientSettings file {target}: {exc}',
                )

        self._macos_seeded_flag_names = desired_names
        if updated_paths:
            log_buffer.log(
                'CustomFFlags',
                f'Pre-seeded macOS Roblox ClientSettings with {len(flags)} '
                f'custom FastFlag(s) into {updated_paths} Player file(s)',
            )
        return bool(updated_paths)

    def prime_startup_flag_cache(self) -> bool:
        """Seed the platform-specific local flag source used before networking."""
        # Explicit resource-dir injection is also the cross-platform test and
        # helper contract for macOS; it must take precedence over the host OS.
        if self._macos_resource_dirs is not None or sys.platform == 'darwin':
            return self.prime_macos_client_settings()
        return self.prime_windows_flag_cache()

    def modify_response(self, path: str, body: bytes) -> bytes:
        """Return a ClientSettings JSON response with configured overrides merged in."""
        if not self.is_enabled() or not self.handles_path(path):
            return body

        flags = self.runtime_flags()

        try:
            payload = json.loads(body)
        except json.JSONDecodeError, UnicodeDecodeError:
            log_buffer.log(
                'CustomFFlags',
                f'Could not decode ClientSettings response for {path[:160]}; response left unchanged',
            )
            return body

        if not isinstance(payload, dict):
            return body

        application_settings = payload.get('applicationSettings')
        if not isinstance(application_settings, dict):
            return body

        application_settings.update(flags)
        modified = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        fps_marker = flags.get('DFIntTaskSchedulerTargetFps')
        marker_text = f' (target FPS={fps_marker})' if fps_marker is not None else ''
        log_buffer.log(
            'CustomFFlags',
            f'Injected {len(flags)} custom FastFlag(s){marker_text} into Roblox ClientSettings response',
        )
        return modified
