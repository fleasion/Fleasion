"""Built-in Roblox modification catalog shared by every presentation layer."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from ..utils.http import validate_public_https_url
from .platform_targets import target_path_for_current_platform

ModificationCategory = Literal[
    'skybox',
    'indoor_skybox',
    'textures',
    'avatar_meshes',
    'sounds',
    'fonts',
]


@dataclass(frozen=True, slots=True)
class ModificationCatalogEntry:
    """Describe one stable built-in Roblox resource replacement."""

    key: str
    category: ModificationCategory
    name: str
    target_path: str
    file_filter: str
    mute_source: str = ''
    is_font: bool = False
    supported: bool = True
    limitation: str = ''


IMAGE_FILTER: Final = 'Image files (*.png *.jpg *.jpeg *.tex);;All files (*)'
DDS_FILTER: Final = 'Image files (*.dds *.png);;All files (*)'
JPG_FILTER: Final = 'Image files (*.jpg *.jpeg *.png);;All files (*)'
MESH_FILTER: Final = 'Mesh files (*.mesh *.obj);;All files (*)'
SOUND_FILTER: Final = 'Audio files (*.mp3 *.ogg *.wav);;All files (*)'
FONT_FILTER: Final = 'Font files (*.ttf *.otf *.ttc);;All files (*)'

_SOBER_MESH_LIMITATION: Final = (
    'R6 default avatar mesh replacements are unavailable in Sober because its '
    'asset overlay does not apply them.'
)


def _entry(
    key: str,
    category: ModificationCategory,
    name: str,
    target_path: str,
    file_filter: str,
    *,
    mute_source: str = '',
    is_font: bool = False,
) -> ModificationCatalogEntry:
    mesh_on_sober = category == 'avatar_meshes' and sys.platform.startswith('linux')
    return ModificationCatalogEntry(
        key=key,
        category=category,
        name=name,
        target_path=target_path_for_current_platform(target_path),
        file_filter=file_filter,
        mute_source=mute_source,
        is_font=is_font,
        supported=not mesh_on_sober,
        limitation=_SOBER_MESH_LIMITATION if mesh_on_sober else '',
    )


_SKY_SUFFIXES: Final = (
    ('back', 'Back', 'bk'),
    ('down', 'Down', 'dn'),
    ('front', 'Front', 'ft'),
    ('left', 'Left', 'lf'),
    ('right', 'Right', 'rt'),
    ('up', 'Up', 'up'),
)


def _sky_entries() -> tuple[ModificationCatalogEntry, ...]:
    rows: list[ModificationCatalogEntry] = []
    for key, name, suffix in _SKY_SUFFIXES:
        rows.append(
            _entry(
                f'sky-{key}',
                'skybox',
                f'Sky — {name}',
                rf'PlatformContent\pc\textures\sky\sky512_{suffix}.tex',
                IMAGE_FILTER,
            )
        )
    for key, name, suffix in _SKY_SUFFIXES:
        rows.append(
            _entry(
                f'indoor-{key}',
                'indoor_skybox',
                f'Indoor — {name}',
                rf'PlatformContent\pc\textures\sky\indoor512_{suffix}.tex',
                IMAGE_FILTER,
            )
        )
    return tuple(rows)


_TEXTURE_DEFINITIONS: Final = (
    (
        'studs-diffuse',
        'High Quality Studs — Diffuse',
        r'PlatformContent\pc\textures\plastic\diffuse.dds',
        DDS_FILTER,
    ),
    (
        'studs-normal',
        'High Quality Studs — Normal',
        r'PlatformContent\pc\textures\plastic\normal.dds',
        DDS_FILTER,
    ),
    (
        'studs-detail',
        'High Quality Studs — Detail',
        r'PlatformContent\pc\textures\plastic\normaldetail.dds',
        DDS_FILTER,
    ),
    (
        'studs-low',
        'Low Quality Studs',
        r'PlatformContent\pc\textures\studs.dds',
        DDS_FILTER,
    ),
    (
        'shiftlock-cursor',
        'Shiftlock Cursor',
        r'content\textures\MouseLockedCursor.png',
        IMAGE_FILTER,
    ),
    (
        'cursor-pointing',
        'Cursor — Pointing',
        r'content\textures\Cursors\KeyboardMouse\ArrowCursor.png',
        IMAGE_FILTER,
    ),
    (
        'cursor-arrow',
        'Cursor — Arrow',
        r'content\textures\Cursors\KeyboardMouse\ArrowFarCursor.png',
        IMAGE_FILTER,
    ),
    (
        'cursor-ibeam',
        'Cursor — IBeam',
        r'content\textures\Cursors\KeyboardMouse\IBeamCursor.png',
        IMAGE_FILTER,
    ),
    ('moon', 'Moon', r'content\sky\moon.jpg', JPG_FILTER),
    ('sun', 'Sun', r'content\sky\sun.jpg', JPG_FILTER),
)

_AVATAR_DEFINITIONS: Final = (
    ('r6-left-arm', 'Left Arm', r'content\avatar\meshes\leftarm.mesh'),
    ('r6-left-leg', 'Left Leg', r'content\avatar\meshes\leftleg.mesh'),
    ('r6-right-arm', 'Right Arm', r'content\avatar\meshes\rightarm.mesh'),
    ('r6-right-leg', 'Right Leg', r'content\avatar\meshes\rightleg.mesh'),
    ('r6-torso', 'Torso', r'content\avatar\meshes\torso.mesh'),
    ('r6-head', 'Head', r'content\avatar\heads\head.mesh'),
)

_SOUND_DEFINITIONS: Final = (
    ('sound-footsteps', 'Footsteps (Plastic)', 'action_footsteps_plastic.mp3'),
    ('sound-falling', 'Falling', 'action_falling.ogg'),
    ('sound-get-up', 'Get Up', 'action_get_up.mp3'),
    ('sound-jump', 'Jump', 'action_jump.mp3'),
    ('sound-jump-land', 'Jump Land', 'action_jump_land.mp3'),
    ('sound-swim', 'Swim', 'action_swim.mp3'),
    ('sound-explosion', 'Explosion', 'impact_explosion_03.mp3'),
    ('sound-water-impact', 'Water Impact', 'impact_water.mp3'),
    ('sound-oof', 'Oof', 'oof.ogg'),
    ('sound-ouch', 'Ouch', 'ouch.ogg'),
    ('sound-volume-slider', 'Volume Slider', 'volume_slider.ogg'),
)


def built_in_modifications() -> tuple[ModificationCatalogEntry, ...]:
    """Return the platform-aware built-in catalog in display order."""
    rows = list(_sky_entries())
    rows.extend(
        _entry(key, 'textures', name, target, file_filter)
        for key, name, target, file_filter in _TEXTURE_DEFINITIONS
    )
    rows.extend(
        _entry(key, 'avatar_meshes', name, target, MESH_FILTER)
        for key, name, target in _AVATAR_DEFINITIONS
    )
    rows.extend(head_variant_entries())
    for key, name, filename in _SOUND_DEFINITIONS:
        if sys.platform.startswith('linux') and filename == 'ouch.ogg':
            continue
        suffix = Path(filename).suffix.casefold()
        rows.append(
            _entry(
                key,
                'sounds',
                name,
                rf'content\sounds\{filename}',
                SOUND_FILTER,
                mute_source=f'bundled:empty{suffix}',
            )
        )
    rows.append(
        _entry(
            'custom-font',
            'fonts',
            'Custom Font',
            r'content\fonts\CustomFont.ttf',
            FONT_FILTER,
            is_font=True,
        )
    )
    return tuple(rows)


def head_variant_entries() -> tuple[ModificationCatalogEntry, ...]:
    """Return every optional R6 head variant supported by Roblox."""
    return tuple(
        _entry(
            f'r6-head-{letter.casefold()}',
            'avatar_meshes',
            f'Head {letter}',
            rf'content\avatar\heads\head{letter}.mesh',
            MESH_FILTER,
        )
        for letter in (chr(code) for code in range(ord('A'), ord('P') + 1))
    )


def detect_modification_source(target_path: str, source: str) -> tuple[str, str]:
    """Validate and classify a local path, asset ID, URL, or remove sentinel."""
    value = source.strip().strip('"\'')
    if not value:
        raise ValueError('Choose a replacement source.')
    lowered = value.casefold()
    if lowered in {'remove', 'mute', 'bundled:empty'}:
        suffix = Path(target_path.replace('\\', '/')).suffix.casefold()
        bundled = {
            '.mp3': 'bundled:empty.mp3',
            '.ogg': 'bundled:empty.ogg',
            '.wav': 'bundled:empty.mp3',
            '.mesh': 'bundled:empty.mesh',
            '.tex': 'bundled:empty.tex',
        }.get(suffix, 'bundled:zero')
        return ('bundled', bundled)
    if value.isdecimal():
        return ('asset_id', value)
    if lowered.startswith('rbxassetid://'):
        asset_id = value[len('rbxassetid://') :].strip()
        if not asset_id.isdecimal():
            raise ValueError('The Roblox asset ID is invalid.')
        return ('asset_id', asset_id)
    if lowered.startswith(('https://', 'http://')):
        _validate_public_http_url(value)
        return ('cdn_url', value)
    path = Path(value).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f'The selected source file does not exist: {path}')
    return ('local_file', str(path))


def _validate_public_http_url(value: str) -> None:
    validate_public_https_url(value)
