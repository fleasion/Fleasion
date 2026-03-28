"""Font family JSON rewriting — ported from Fishstrap's Bootstrapper.cs.

Copies a custom font file to ``content/fonts/CustomFont.ttf`` in each Roblox
directory and rewrites every ``content/fonts/families/*.json`` manifest so
that all ``assetId`` fields point to ``rbxasset://fonts/CustomFont.ttf``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..utils import log_buffer

# Recognised font magic bytes (first 4 bytes of the file).
FONT_HEADERS: dict[str, bytes] = {
    'ttf': b'\x00\x01\x00\x00',
    'otf': b'\x4F\x54\x54\x4F',  # "OTTO"
    'ttc': b'\x74\x74\x63\x66',  # "ttcf"
}

CUSTOM_FONT_PATH = 'rbxasset://fonts/CustomFont.ttf'
CUSTOM_FONT_REL = Path('content') / 'fonts' / 'CustomFont.ttf'
FAMILIES_REL = Path('content') / 'fonts' / 'families'


def validate_font_bytes(data: bytes) -> bool:
    """Return ``True`` if *data* starts with a known font magic header."""
    if len(data) < 4:
        return False
    header = data[:4]
    return any(header == magic for magic in FONT_HEADERS.values())


def apply_custom_font(
    font_data: bytes,
    roblox_dirs: list[Path],
    stash_dir: Path,
) -> None:
    """Copy the custom font and rewrite family manifests in every Roblox dir.

    Parameters
    ----------
    font_data:
        Raw bytes of the ``.ttf`` / ``.otf`` / ``.ttc`` font file.
    roblox_dirs:
        Discovered Roblox installation directories.
    stash_dir:
        ``ModOriginals`` directory used for stashing originals.
    """
    for roblox_dir in roblox_dirs:
        # --- Copy font file -----------------------------------------------
        dst_font = roblox_dir / CUSTOM_FONT_REL
        dst_font.parent.mkdir(parents=True, exist_ok=True)

        stash_font = stash_dir / roblox_dir.name / CUSTOM_FONT_REL
        if dst_font.exists() and not stash_font.exists():
            stash_font.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst_font, stash_font)

        dst_font.write_bytes(font_data)

        # --- Rewrite family manifests -------------------------------------
        families_dir = roblox_dir / FAMILIES_REL
        if not families_dir.is_dir():
            continue

        for json_path in families_dir.glob('*.json'):
            stash_json = stash_dir / roblox_dir.name / FAMILIES_REL / json_path.name
            # Stash the original once
            if not stash_json.exists():
                stash_json.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(json_path, stash_json)

            try:
                with json_path.open('r', encoding='utf-8') as fp:
                    family = json.load(fp)
            except (json.JSONDecodeError, OSError):
                continue

            changed = False
            for face in family.get('faces', []):
                if face.get('assetId') != CUSTOM_FONT_PATH:
                    face['assetId'] = CUSTOM_FONT_PATH
                    changed = True

            if changed:
                with json_path.open('w', encoding='utf-8') as fp:
                    json.dump(family, fp, indent=2)

        log_buffer.log('Modifications', f'Applied custom font in {roblox_dir.name}')


def restore_font_families(
    roblox_dirs: list[Path],
    stash_dir: Path,
) -> None:
    """Remove ``CustomFont.ttf`` and restore original family JSONs from stash."""
    for roblox_dir in roblox_dirs:
        # Restore font file
        dst_font = roblox_dir / CUSTOM_FONT_REL
        stash_font = stash_dir / roblox_dir.name / CUSTOM_FONT_REL
        if stash_font.exists():
            shutil.copy2(stash_font, dst_font)
            stash_font.unlink()
        elif dst_font.exists():
            dst_font.unlink()

        # Restore family JSONs
        families_dir = roblox_dir / FAMILIES_REL
        stash_families = stash_dir / roblox_dir.name / FAMILIES_REL
        if stash_families.is_dir():
            for stash_json in stash_families.glob('*.json'):
                dst_json = families_dir / stash_json.name
                shutil.copy2(stash_json, dst_json)
                stash_json.unlink()

        log_buffer.log('Modifications', f'Restored font families in {roblox_dir.name}')
