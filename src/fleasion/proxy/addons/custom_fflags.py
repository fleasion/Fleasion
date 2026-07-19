"""Custom FastFlag response modifier for Roblox ClientSettings traffic."""

from __future__ import annotations

import json
import os
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
    """Merge user-defined flags into Roblox's remote application settings."""

    def __init__(
        self,
        config_manager,
        flag_cache_path: Path | None = None,
        settings_path: Path | None = None,
        reload_settings_from_disk: bool = False,
    ):
        self.config_manager = config_manager
        self._flag_cache_path = flag_cache_path
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

    def prime_windows_flag_cache(self) -> bool:
        """Write active overrides into Roblox's uncompressed Windows flag cache.

        Some flags, including the task-scheduler target FPS, are consumed before
        the dynamic reloader's first network request.  Roblox's current cache
        layout is a four-byte signature length, that many signature bytes, one
        compression byte, then the ClientSettings JSON.  We preserve the header
        and replace the JSON atomically only for the known uncompressed layout.
        """
        if not self.is_enabled():
            return False
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

            flags = self.runtime_flags()
            application_settings.update(flags)
            updated = raw[:payload_offset] + json.dumps(
                payload, separators=(',', ':'), ensure_ascii=False
            ).encode('utf-8')
            temporary_path = cache_path.with_name(f'.{cache_path.name}.{os.getpid()}.tmp')
            try:
                temporary_path.write_bytes(updated)
                temporary_path.replace(cache_path)
            finally:
                temporary_path.unlink(missing_ok=True)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return False

        log_buffer.log(
            'CustomFFlags',
            f'Pre-seeded Roblox flag cache with {len(flags)} custom FastFlag(s)',
        )
        return True

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
