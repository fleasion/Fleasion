"""Data shared by the modifications manager and its widgets."""

from __future__ import annotations

from typing import TypedDict


class NewModificationEntry(TypedDict, total=False):
    display_name: str
    target_path: str
    source_type: str | None
    source_value: str | None
    status: str
    error_message: str | None
    converted_cache_path: str | None
    _is_font: bool
    _apply_gen: int


class ModificationEntry(NewModificationEntry):
    id: str


class FastFlagSettings(TypedDict, total=False):
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
