"""Font family JSON rewriting — ported from Fishstrap's Bootstrapper.cs.

Copies a custom font file to ``content/fonts/CustomFont.ttf`` in each Roblox
directory and rewrites every ``content/fonts/families/*.json`` manifest so
that all ``assetId`` fields point to ``rbxasset://fonts/CustomFont.ttf``.
"""

from __future__ import annotations

import json
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from typing import TypeIs

from ..utils import log_buffer
from .stash_paths import resource_stash_dir

# Recognised font magic bytes (first 4 bytes of the file).
FONT_HEADERS: dict[str, bytes] = {
    'ttf': b'\x00\x01\x00\x00',
    'otf': b'\x4f\x54\x54\x4f',  # "OTTO"
    'ttc': b'\x74\x74\x63\x66',  # "ttcf"
}

CUSTOM_FONT_PATH = 'rbxasset://fonts/CustomFont.ttf'
CUSTOM_FONT_REL = Path('content') / 'fonts' / 'CustomFont.ttf'
FAMILIES_REL = Path('content') / 'fonts' / 'families'
GENERATED_FONT_MARKER_REL = Path('content') / 'fonts' / '.fleasion-generated-custom-font'
GENERATED_FAMILIES_MARKER_REL = (
    Path('content') / 'fonts' / '.fleasion-generated-family-manifests.json'
)


def _clear_read_only(path: Path) -> None:
    if not path.exists():
        return
    try:
        mode = path.stat().st_mode
        if mode & stat.S_IWRITE:
            return
        path.chmod(mode | stat.S_IWRITE)
    except OSError:
        pass


def validate_font_bytes(data: bytes) -> bool:
    """Return ``True`` if *data* starts with a known font magic header."""
    if len(data) < 4:
        return False
    header = data[:4]
    return any(header == magic for magic in FONT_HEADERS.values())


def _is_object_list(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _load_generated_family_names(marker_path: Path) -> set[str]:
    try:
        value: object = json.loads(marker_path.read_text(encoding='utf-8'))
    except OSError, json.JSONDecodeError:
        return set()
    if not _is_object_list(value):
        return set()
    return {
        name
        for name in value
        if isinstance(name, str) and Path(name).name == name and name.lower().endswith('.json')
    }


def _save_generated_family_names(marker_path: Path, names: set[str]) -> None:
    if not names:
        try:
            marker_path.unlink()
        except FileNotFoundError, OSError:
            pass
        return
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(sorted(names), indent=2), encoding='utf-8')


def apply_custom_font(
    font_data: bytes,
    roblox_dirs: list[Path],
    stash_dir: Path,
    family_manifest_loader: Callable[[Path], dict[str, bytes]] | None = None,
) -> None:
    """Copy the custom font and rewrite family manifests in every Roblox dir.

    ``family_manifest_loader`` supplies packaged manifests for clients such as
    Sober, where the originals live inside an APK instead of the writable
    resource root. Missing manifests are materialized into the overlay and
    tracked so reset can remove only files Fleasion created.
    """
    for roblox_dir in roblox_dirs:
        # --- Copy font file -----------------------------------------------
        dst_font = roblox_dir / CUSTOM_FONT_REL
        dst_font.parent.mkdir(parents=True, exist_ok=True)

        install_stash = resource_stash_dir(stash_dir, roblox_dir)
        stash_font = install_stash / CUSTOM_FONT_REL
        generated_font_marker = install_stash / GENERATED_FONT_MARKER_REL
        if dst_font.exists() and not stash_font.exists() and not generated_font_marker.exists():
            stash_font.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst_font, stash_font)
        elif (
            not dst_font.exists() and not stash_font.exists() and not generated_font_marker.exists()
        ):
            generated_font_marker.parent.mkdir(parents=True, exist_ok=True)
            generated_font_marker.touch()

        _clear_read_only(dst_font)
        dst_font.write_bytes(font_data)

        # --- Rewrite family manifests -------------------------------------
        families_dir = roblox_dir / FAMILIES_REL
        marker_path = install_stash / GENERATED_FAMILIES_MARKER_REL
        generated_names = _load_generated_family_names(marker_path)
        packaged_manifests = (
            family_manifest_loader(roblox_dir) if family_manifest_loader is not None else {}
        )

        existing_paths: dict[str, Path] = {}
        if families_dir.is_dir():
            try:
                existing_paths = {path.name: path for path in families_dir.glob('*.json')}
            except OSError:
                existing_paths = {}

        candidate_names = sorted(existing_paths.keys() | packaged_manifests.keys())
        for name in candidate_names:
            if Path(name).name != name or not name.lower().endswith('.json'):
                continue

            json_path = existing_paths.get(name, families_dir / name)
            generated = name in generated_names
            needs_write = not json_path.is_file()

            if needs_write:
                source = packaged_manifests.get(name)
                if source is None:
                    continue
                try:
                    family = json.loads(source.decode('utf-8'))
                except UnicodeDecodeError, json.JSONDecodeError:
                    continue
                generated_names.add(name)
                # Persist ownership before creating the overlay file so a
                # crash cannot strand an untracked generated manifest.
                _save_generated_family_names(marker_path, generated_names)
            else:
                stash_json = install_stash / FAMILIES_REL / name
                if not generated and not stash_json.exists():
                    stash_json.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(json_path, stash_json)
                try:
                    with json_path.open('r', encoding='utf-8') as fp:
                        family = json.load(fp)
                except json.JSONDecodeError, OSError:
                    continue

            changed = False
            for face in family.get('faces', []):
                if face.get('assetId') != CUSTOM_FONT_PATH:
                    face['assetId'] = CUSTOM_FONT_PATH
                    changed = True

            if changed or needs_write:
                json_path.parent.mkdir(parents=True, exist_ok=True)
                _clear_read_only(json_path)
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
        install_stash = resource_stash_dir(stash_dir, roblox_dir)
        stash_font = install_stash / CUSTOM_FONT_REL
        generated_font_marker = install_stash / GENERATED_FONT_MARKER_REL
        if stash_font.exists():
            _clear_read_only(dst_font)
            shutil.copy2(stash_font, dst_font)
            _clear_read_only(stash_font)
            stash_font.unlink()
        elif dst_font.exists():
            _clear_read_only(dst_font)
            dst_font.unlink()
        try:
            generated_font_marker.unlink()
        except FileNotFoundError, OSError:
            pass

        # Restore family JSONs that predated Fleasion.
        families_dir = roblox_dir / FAMILIES_REL
        stash_families = install_stash / FAMILIES_REL
        if stash_families.is_dir():
            for stash_json in stash_families.glob('*.json'):
                dst_json = families_dir / stash_json.name
                dst_json.parent.mkdir(parents=True, exist_ok=True)
                _clear_read_only(dst_json)
                shutil.copy2(stash_json, dst_json)
                _clear_read_only(stash_json)
                stash_json.unlink()

        # Packaged manifests materialized for Sober did not exist in the
        # writable overlay before this modification, so remove them outright.
        marker_path = install_stash / GENERATED_FAMILIES_MARKER_REL
        generated_names = _load_generated_family_names(marker_path)
        for name in generated_names:
            dst_json = families_dir / name
            if dst_json.exists():
                _clear_read_only(dst_json)
                try:
                    dst_json.unlink()
                except OSError:
                    pass
        _save_generated_family_names(marker_path, set())

        for directory in (families_dir, roblox_dir / 'content' / 'fonts'):
            try:
                directory.rmdir()
            except OSError:
                pass

        log_buffer.log('Modifications', f'Restored font families in {roblox_dir.name}')
