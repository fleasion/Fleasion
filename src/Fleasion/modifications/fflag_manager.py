"""FastFlag manager — writes/restores ClientAppSettings.json.

Mirrors the 18 allowlisted flags from Fishstrap's ``FastFlagManager.cs``
and ``FastFlagsViewModel.cs``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..utils import log_buffer

# ---------------------------------------------------------------------------
# Preset flag name mapping (mirrors Fishstrap PresetFlags)
# ---------------------------------------------------------------------------

PRESET_FLAGS: dict[str, str] = {
    'Rendering.ManualFullscreen':              'FFlagHandleAltEnterFullscreenManually',
    'Rendering.DisableScaling':                'DFFlagDisableDPIScale',
    'Rendering.MSAA':                          'FIntDebugForceMSAASamples',
    'Rendering.FRMQualityOverride':            'DFIntDebugFRMQualityLevelOverride',
    'Rendering.Mode.DisableD3D11':             'FFlagDebugGraphicsDisableDirect3D11',
    'Rendering.Mode.D3D11':                    'FFlagDebugGraphicsPreferD3D11',
    'Rendering.Mode.Vulkan':                   'FFlagDebugGraphicsPreferVulkan',
    'Rendering.Mode.OpenGL':                   'FFlagDebugGraphicsPreferOpenGL',
    'Geometry.MeshLOD.Static':                 'DFIntCSGLevelOfDetailSwitchingDistanceStatic',
    'Geometry.MeshLOD.L0':                     'DFIntCSGLevelOfDetailSwitchingDistance',
    'Geometry.MeshLOD.L12':                    'DFIntCSGLevelOfDetailSwitchingDistanceL12',
    'Geometry.MeshLOD.L23':                    'DFIntCSGLevelOfDetailSwitchingDistanceL23',
    'Geometry.MeshLOD.L34':                    'DFIntCSGLevelOfDetailSwitchingDistanceL34',
    'Rendering.TextureQuality.OverrideEnabled': 'DFFlagTextureQualityOverrideEnabled',
    'Rendering.TextureQuality.Level':          'DFIntTextureQualityOverride',
}

# Additional standalone toggles not in the preset dict above
EXTRA_FLAGS: dict[str, str] = {
    'grey_sky':       'FFlagDebugSkyGray',
    'pause_voxelizer': 'DFFlagDebugPauseVoxelizer',
    'grass_max':      'FIntFRMMaxGrassDistance',
    'grass_min':      'FIntFRMMinGrassDistance',
    'grass_motion':   'FIntGrassMovementReducedMotionFactor',
}

CLIENT_SETTINGS_REL = Path('ClientSettings') / 'ClientAppSettings.json'

LOD_LEVELS = ('L0', 'L12', 'L23', 'L34')


class FastFlagManager:
    """Builds and writes ``ClientAppSettings.json`` from a UI settings dict."""

    def __init__(self, roblox_dirs: list[Path], stash_dir: Path):
        self._roblox_dirs = roblox_dirs
        self._stash_dir = stash_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_json(self, settings: dict) -> dict:
        """Convert a UI settings dict into the flags dict that becomes ClientAppSettings.json."""
        flags: dict[str, str] = {}

        # ── Rendering Mode ──────────────────────────────────────────
        mode = settings.get('rendering_mode', 'Default')
        if mode != 'Default':
            flag_key = f'Rendering.Mode.{mode}'
            if flag_key in PRESET_FLAGS:
                flags[PRESET_FLAGS[flag_key]] = 'True'
            # Vulkan and OpenGL require disabling D3D11
            if mode in ('Vulkan', 'OpenGL'):
                flags[PRESET_FLAGS['Rendering.Mode.DisableD3D11']] = 'True'

        # ── MSAA ────────────────────────────────────────────────────
        msaa = settings.get('msaa', 'Default')
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
        tex = settings.get('texture_quality', 'Default')
        if tex != 'Default':
            # Extract numeric value from "Level X" or "Level X (Lowest/Highest)" format
            tex_val = tex.replace('Level ', '').split(' ')[0]
            flags[PRESET_FLAGS['Rendering.TextureQuality.OverrideEnabled']] = 'True'
            flags[PRESET_FLAGS['Rendering.TextureQuality.Level']] = tex_val

        # ── Mesh LOD (mirrors Fishstrap MeshQuality setter) ─────────
        # Slider: 0 = Default (no flag), 1 = Level 0, 2 = Level 1, 3 = Level 2, 4 = Level 3
        if settings.get('mesh_lod_enabled'):
            level = int(settings.get('mesh_lod', 4))
            if level > 0:  # 0 = Default means no flag written
                level = max(1, min(level, len(LOD_LEVELS)))  # 1-4 maps to Level 0-3
                for i, lod_name in enumerate(LOD_LEVELS):
                    lod_value = max(0, min(level - 1 - i, 3))
                    flags[PRESET_FLAGS[f'Geometry.MeshLOD.{lod_name}']] = str(lod_value)
                flags[PRESET_FLAGS['Geometry.MeshLOD.Static']] = str(level - 1)  # Store as 0-3

        # ── FRM Quality Override ────────────────────────────────────
        # Slider: 0 = Default (no flag), 1-21 = quality level
        if settings.get('frm_quality_enabled'):
            val = int(settings.get('frm_quality', 21))
            if val > 0:  # 0 = Default means no flag written
                flags[PRESET_FLAGS['Rendering.FRMQualityOverride']] = str(val)

        # ── Extra standalone flags ──────────────────────────────────
        if settings.get('grey_sky'):
            flags[EXTRA_FLAGS['grey_sky']] = 'True'
        if settings.get('pause_voxelizer'):
            flags[EXTRA_FLAGS['pause_voxelizer']] = 'True'

        for key in ('grass_max', 'grass_min', 'grass_motion'):
            val = settings.get(key)
            if val is not None and val != '':
                flags[EXTRA_FLAGS[key]] = str(int(val))

        return flags

    def write(self, settings: dict) -> None:
        """Build flags and write ``ClientAppSettings.json`` in every Roblox dir."""
        flags = self.build_json(settings)
        content = json.dumps(flags, indent=2).encode('utf-8') if flags else b'{}'

        for roblox_dir in self._roblox_dirs:
            dst = roblox_dir / CLIENT_SETTINGS_REL
            stash = self._stash_dir / roblox_dir.name / CLIENT_SETTINGS_REL

            # Stash original once
            if dst.exists() and not stash.exists():
                stash.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, stash)

            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(content)

        log_buffer.log('FastFlags', f'Wrote {len(flags)} flag(s) to {len(self._roblox_dirs)} Roblox dir(s)')

    def restore(self) -> None:
        """Restore (or delete) ``ClientAppSettings.json`` in every Roblox dir."""
        for roblox_dir in self._roblox_dirs:
            dst = roblox_dir / CLIENT_SETTINGS_REL
            stash = self._stash_dir / roblox_dir.name / CLIENT_SETTINGS_REL
            if stash.exists():
                shutil.copy2(stash, dst)
                stash.unlink()
            elif dst.exists():
                dst.unlink()

        log_buffer.log('FastFlags', 'Restored ClientAppSettings.json')
