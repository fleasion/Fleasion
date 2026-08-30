"""FastFlag manager — writes/restores ClientAppSettings.json.

Mirrors the 18 allowlisted flags from Fishstrap's ``FastFlagManager.cs``
and ``FastFlagsViewModel.cs``.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ..utils import format_count, log_buffer
from .stash_paths import resource_stash_dir

# ---------------------------------------------------------------------------
# Preset flag name mapping (mirrors Fishstrap PresetFlags)
# ---------------------------------------------------------------------------

PRESET_FLAGS: dict[str, str] = {
    'Rendering.ManualFullscreen': 'FFlagHandleAltEnterFullscreenManually',
    'Rendering.DisableScaling': 'DFFlagDisableDPIScale',
    'Rendering.MSAA': 'FIntDebugForceMSAASamples',
    'Rendering.FRMQualityOverride': 'DFIntDebugFRMQualityLevelOverride',
    'Rendering.Mode.DisableD3D11': 'FFlagDebugGraphicsDisableDirect3D11',
    'Rendering.Mode.D3D11': 'FFlagDebugGraphicsPreferD3D11',
    'Rendering.Mode.Vulkan': 'FFlagDebugGraphicsPreferVulkan',
    'Rendering.Mode.OpenGL': 'FFlagDebugGraphicsPreferOpenGL',
    'Geometry.MeshLOD.Static': 'DFIntCSGLevelOfDetailSwitchingDistanceStatic',
    'Geometry.MeshLOD.L0': 'DFIntCSGLevelOfDetailSwitchingDistance',
    'Geometry.MeshLOD.L12': 'DFIntCSGLevelOfDetailSwitchingDistanceL12',
    'Geometry.MeshLOD.L23': 'DFIntCSGLevelOfDetailSwitchingDistanceL23',
    'Geometry.MeshLOD.L34': 'DFIntCSGLevelOfDetailSwitchingDistanceL34',
    'Rendering.TextureQuality.OverrideEnabled': 'DFFlagTextureQualityOverrideEnabled',
    'Rendering.TextureQuality.Level': 'DFIntTextureQualityOverride',
}

# Additional standalone toggles not in the preset dict above
EXTRA_FLAGS: dict[str, str] = {
    'grey_sky': 'FFlagDebugSkyGray',
    'pause_voxelizer': 'DFFlagDebugPauseVoxelizer',
    'grass_max': 'FIntFRMMaxGrassDistance',
    'grass_min': 'FIntFRMMinGrassDistance',
    'grass_motion': 'FIntGrassMovementReducedMotionFactor',
}

CLIENT_SETTINGS_REL = Path('ClientSettings') / 'ClientAppSettings.json'
APPLEBLOX_CLIENT_SETTINGS_REL = Path('MacOS') / 'ClientSettings' / 'ClientAppSettings.json'

LOD_LEVELS = ('L0', 'L12', 'L23', 'L34')


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type FlagValue = str | bool | int


if TYPE_CHECKING:

    def _setting_str(settings: Mapping[str, object], key: str, default: str) -> str: ...

    def _setting_int_source(settings: Mapping[str, object], key: str, default: int) -> object: ...

    def _setting_value(settings: Mapping[str, object], key: str) -> object: ...

    def _json_object(value: object) -> JsonObject | None: ...

    def _int_value(value: object) -> int: ...
else:

    def _setting_str(settings: Mapping[str, object], key: str, default: str) -> str:
        return settings.get(key, default)

    def _setting_int_source(settings: Mapping[str, object], key: str, default: int) -> object:
        return settings.get(key, default)

    def _setting_value(settings: Mapping[str, object], key: str) -> object:
        return settings.get(key)

    def _json_object(value: object) -> JsonObject | None:
        return value if isinstance(value, dict) else None

    def _int_value(value: object) -> int:
        return int(value)


def _clear_read_only(path: Path) -> None:
    """Clear the read-only attribute on an existing file."""
    if not path.exists():
        return
    current_mode = path.stat().st_mode
    if current_mode & stat.S_IWRITE:
        return
    path.chmod(current_mode | stat.S_IWRITE)


def _is_read_only(path: Path) -> bool:
    try:
        return path.exists() and not bool(path.stat().st_mode & stat.S_IWRITE)
    except OSError:
        return False


def _restore_read_only(path: Path) -> None:
    try:
        if path.exists():
            path.chmod(path.stat().st_mode & ~stat.S_IWRITE)
    except OSError:
        pass


def _sober_config_path_for_resource_dir(roblox_dir: Path) -> Path | None:
    if not sys.platform.startswith('linux'):
        return None
    try:
        from ..utils.platform_linux import SOBER_CONFIG_FILE, is_sober_resource_dir

        return SOBER_CONFIG_FILE if is_sober_resource_dir(roblox_dir) else None
    except Exception:
        return None


def client_settings_targets_for_resource_dir(
    roblox_dir: Path,
) -> list[tuple[Path, Path]]:
    """Return live settings files and their per-install stash-relative paths."""
    targets = [(roblox_dir / CLIENT_SETTINGS_REL, CLIENT_SETTINGS_REL)]
    if (
        sys.platform == 'darwin'
        and roblox_dir.name == 'Resources'
        and roblox_dir.parent.name == 'Contents'
        and roblox_dir.parent.parent.suffix == '.app'
    ):
        targets.append(
            (
                roblox_dir.parent / 'MacOS' / CLIENT_SETTINGS_REL,
                APPLEBLOX_CLIENT_SETTINGS_REL,
            )
        )
    return targets


def client_settings_paths_for_resource_dir(roblox_dir: Path) -> list[Path]:
    return [path for path, _stash_rel in client_settings_targets_for_resource_dir(roblox_dir)]


def _sober_flag_value(value: str) -> FlagValue:
    lowered = value.lower()
    if lowered == 'true':
        return True
    if lowered == 'false':
        return False
    try:
        return int(value)
    except ValueError:
        return value


class FastFlagManager:
    """Builds and writes ``ClientAppSettings.json`` from a UI settings dict."""

    def __init__(self, roblox_dirs: list[Path], stash_dir: Path) -> None:
        self._roblox_dirs = roblox_dirs
        self._stash_dir = stash_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_json(self, settings: Mapping[str, object]) -> dict[str, str]:
        """Convert a UI settings dict into the flags dict that becomes ClientAppSettings.json."""
        flags: dict[str, str] = {}

        # ── Rendering Mode ──────────────────────────────────────────
        mode = _setting_str(settings, 'rendering_mode', 'Default')
        if mode != 'Default':
            flag_key = f'Rendering.Mode.{mode}'
            if flag_key in PRESET_FLAGS:
                flags[PRESET_FLAGS[flag_key]] = 'True'
            # Vulkan and OpenGL require disabling D3D11
            if mode in ('Vulkan', 'OpenGL'):
                flags[PRESET_FLAGS['Rendering.Mode.DisableD3D11']] = 'True'

        # ── MSAA ────────────────────────────────────────────────────
        msaa = _setting_str(settings, 'msaa', 'Default')
        if msaa != 'Default':
            # Strip "x" suffix and any "(Lowest)"/"(Highest)" suffix (e.g., "1x (Lowest)" -> "1")
            msaa_val = msaa.replace('x', '').split(' ')[0]
            flags[PRESET_FLAGS['Rendering.MSAA']] = msaa_val

        # ── Toggles ─────────────────────────────────────────────────
        if settings.get('disable_dpi_scale'):
            flags[PRESET_FLAGS['Rendering.DisableScaling']] = 'True'

        if settings.get('alt_enter_fullscreen'):
            flags[PRESET_FLAGS['Rendering.ManualFullscreen']] = 'True'

        # ── Texture Quality ─────────────────────────────────────────
        tex = _setting_str(settings, 'texture_quality', 'Default')
        if tex != 'Default':
            # Extract numeric value from "Level X" or "Level X (Lowest/Highest)" format
            tex_val = tex.replace('Level ', '').split(' ')[0]
            flags[PRESET_FLAGS['Rendering.TextureQuality.OverrideEnabled']] = 'True'
            flags[PRESET_FLAGS['Rendering.TextureQuality.Level']] = tex_val

        # ── Mesh LOD (mirrors Fishstrap MeshQuality setter) ─────────
        # Slider: 0 = Default (no flag), 1 = Level 0, 2 = Level 1, 3 = Level 2, 4 = Level 3
        if settings.get('mesh_lod_enabled'):
            level = _int_value(_setting_int_source(settings, 'mesh_lod', 4))
            if level > 0:  # 0 = Default means no flag written
                level = max(1, min(level, len(LOD_LEVELS)))  # 1-4 maps to Level 0-3
                for i, lod_name in enumerate(LOD_LEVELS):
                    lod_value = max(0, min(level - 1 - i, 3))
                    flags[PRESET_FLAGS[f'Geometry.MeshLOD.{lod_name}']] = str(lod_value)
                flags[PRESET_FLAGS['Geometry.MeshLOD.Static']] = str(level - 1)  # Store as 0-3

        # ── FRM Quality Override ────────────────────────────────────
        # Slider: 0 = Default (no flag), 1-21 = quality level
        if settings.get('frm_quality_enabled'):
            val = _int_value(_setting_int_source(settings, 'frm_quality', 21))
            if val > 0:  # 0 = Default means no flag written
                flags[PRESET_FLAGS['Rendering.FRMQualityOverride']] = str(val)

        # ── Extra standalone flags ──────────────────────────────────
        if settings.get('grey_sky'):
            flags[EXTRA_FLAGS['grey_sky']] = 'True'
        if settings.get('pause_voxelizer'):
            flags[EXTRA_FLAGS['pause_voxelizer']] = 'True'

        for key in ('grass_max', 'grass_min', 'grass_motion'):
            val = _setting_value(settings, key)
            if val is not None and val != '':
                flags[EXTRA_FLAGS[key]] = str(_int_value(val))

        return flags

    def write(self, settings: Mapping[str, object]) -> set[Path]:
        """Build flags and write settings, returning dirs blocked by permissions."""
        flags = self.build_json(settings)
        content = json.dumps(flags, indent=2).encode('utf-8') if flags else b'{}'

        written_dirs = 0
        failed = 0
        failed_dirs: set[Path] = set()

        for roblox_dir in self._roblox_dirs:
            install_stash = resource_stash_dir(self._stash_dir, roblox_dir)
            wrote_dir = False
            for dst, stash_rel in client_settings_targets_for_resource_dir(roblox_dir):
                stash = install_stash / stash_rel
                try:
                    # Stash original once
                    if dst.exists() and not stash.exists():
                        stash.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(dst, stash)

                    dst.parent.mkdir(parents=True, exist_ok=True)
                    _clear_read_only(dst)
                    dst.write_bytes(content)
                    wrote_dir = True
                except PermissionError as exc:
                    failed += 1
                    failed_dirs.add(roblox_dir)
                    log_buffer.log('FastFlags', f'Permission denied writing {dst}: {exc}')
            sober_config = _sober_config_path_for_resource_dir(roblox_dir)
            if sober_config is not None:
                stash_config = install_stash / 'sober_config.json'
                try:
                    config_payload: JsonObject = {}
                    if sober_config.exists():
                        if not stash_config.exists():
                            stash_config.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(sober_config, stash_config)
                        try:
                            loaded_value: object = json.loads(
                                sober_config.read_text(encoding='utf-8')
                            )
                            loaded = _json_object(loaded_value)
                            if loaded is not None:
                                config_payload = loaded
                        except json.JSONDecodeError:
                            config_payload = {}
                    config_payload['fflags'] = {
                        key: _sober_flag_value(value) for key, value in flags.items()
                    }
                    sober_config.parent.mkdir(parents=True, exist_ok=True)
                    restore_read_only = _is_read_only(sober_config)
                    _clear_read_only(sober_config)
                    try:
                        sober_config.write_text(
                            json.dumps(config_payload, indent=2), encoding='utf-8'
                        )
                    finally:
                        if restore_read_only:
                            _restore_read_only(sober_config)
                except PermissionError as exc:
                    failed += 1
                    failed_dirs.add(roblox_dir)
                    log_buffer.log(
                        'FastFlags',
                        f'Permission denied writing Sober config {sober_config}: {exc}',
                    )
                except OSError as exc:
                    failed += 1
                    log_buffer.log(
                        'FastFlags',
                        f'Failed writing Sober config {sober_config}: {exc}',
                    )

            if wrote_dir:
                written_dirs += 1

        message = f'Wrote {format_count(len(flags), "flag")} to {format_count(written_dirs, "Roblox dir")}'
        if failed:
            message += f'; skipped {format_count(failed, "Roblox dir")} due to permission errors'
        log_buffer.log('FastFlags', message)
        return failed_dirs

    def reassert_macos_bootstrapper_flags(self, settings: Mapping[str, object]) -> int:
        """Merge Fleasion flags into settings rewritten just before a macOS launch.

        AppleBlox removes and recreates ``Contents/MacOS/ClientSettings`` during
        every launch. This intentionally does not stash that transient file:
        normal Fleasion writes already captured the pre-Fleasion state, and
        treating AppleBlox's generated launch payload as an original would make
        Fleasion restore bootstrapper-owned flags later.
        """
        if sys.platform != 'darwin':
            return 0

        flags = self.build_json(settings)
        if not flags:
            return 0

        updated_count = 0
        for roblox_dir in self._roblox_dirs:
            if not (
                roblox_dir.name == 'Resources'
                and roblox_dir.parent.name == 'Contents'
                and roblox_dir.parent.parent.suffix == '.app'
            ):
                continue

            target = roblox_dir.parent / 'MacOS' / CLIENT_SETTINGS_REL
            if not target.is_file():
                continue

            try:
                try:
                    existing_value: object = json.loads(target.read_text(encoding='utf-8'))
                    existing = _json_object(existing_value) or {}
                except UnicodeDecodeError, json.JSONDecodeError:
                    existing = {}
                merged = {**existing, **flags}
                if merged == existing:
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                _clear_read_only(target)
                temporary = target.with_name(f'.{target.name}.fleasion-{os.getpid()}.tmp')
                try:
                    temporary.write_text(json.dumps(merged, indent=2), encoding='utf-8')
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
                updated_count += 1
            except (PermissionError, OSError) as exc:
                log_buffer.log(
                    'FastFlags',
                    f'Failed to merge Fleasion flags into bootstrapper launch settings '
                    f'{target}: {exc}',
                )

        if updated_count:
            log_buffer.log(
                'FastFlags',
                f'Merged {format_count(flags, "Fleasion flag")} into '
                f'{format_count(updated_count, "bootstrapper launch settings file")}',
            )
        return updated_count

    def restore(self) -> None:
        """Restore (or delete) ``ClientAppSettings.json`` in every Roblox dir."""
        restored = 0
        failed = 0

        for roblox_dir in self._roblox_dirs:
            install_stash = resource_stash_dir(self._stash_dir, roblox_dir)
            restored_dir = False
            for dst, stash_rel in client_settings_targets_for_resource_dir(roblox_dir):
                stash = install_stash / stash_rel
                try:
                    if stash.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        _clear_read_only(dst)
                        shutil.copy2(stash, dst)
                        _clear_read_only(stash)
                        stash.unlink()
                        restored_dir = True
                    elif dst.exists():
                        _clear_read_only(dst)
                        dst.unlink()
                        restored_dir = True
                except PermissionError as exc:
                    failed += 1
                    log_buffer.log('FastFlags', f'Permission denied restoring {dst}: {exc}')
            if restored_dir:
                restored += 1

            sober_config = _sober_config_path_for_resource_dir(roblox_dir)
            if sober_config is not None:
                stash_config = install_stash / 'sober_config.json'
                try:
                    if stash_config.exists():
                        sober_config.parent.mkdir(parents=True, exist_ok=True)
                        _clear_read_only(sober_config)
                        shutil.copy2(stash_config, sober_config)
                        _clear_read_only(stash_config)
                        stash_config.unlink()
                    elif sober_config.exists():
                        restore_read_only = _is_read_only(sober_config)
                        try:
                            payload_value: object = json.loads(
                                sober_config.read_text(encoding='utf-8')
                            )
                            payload = _json_object(payload_value) or {}
                        except json.JSONDecodeError:
                            payload = {}
                        if 'fflags' in payload:
                            payload.pop('fflags', None)
                            _clear_read_only(sober_config)
                            try:
                                sober_config.write_text(
                                    json.dumps(payload, indent=2), encoding='utf-8'
                                )
                            finally:
                                if restore_read_only:
                                    _restore_read_only(sober_config)
                except PermissionError as exc:
                    failed += 1
                    log_buffer.log(
                        'FastFlags',
                        f'Permission denied restoring Sober config {sober_config}: {exc}',
                    )
                except OSError as exc:
                    failed += 1
                    log_buffer.log(
                        'FastFlags',
                        f'Failed restoring Sober config {sober_config}: {exc}',
                    )

        message = 'Restored ClientAppSettings.json'
        if failed:
            message += f' in {format_count(restored, "Roblox dir")}; skipped {format_count(failed, "Roblox dir")} due to permission errors'
        log_buffer.log('FastFlags', message)
