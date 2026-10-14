"""Merge independent Fleasion flag layers into native Linux client files."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
from typing import TYPE_CHECKING, Literal, cast

from fleasion.utils.logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

type FlagLayer = Literal['presets', 'custom']
type FlagLayers = dict[FlagLayer, dict[str, object]]

_FILE_LOCK = threading.RLock()


def linux_flag_stash_paths(stash_dir: Path, path: Path) -> tuple[Path, Path]:
    digest = hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:16]
    base = stash_dir / 'linux_client_fflags' / f'{path.parent.name}-{digest}'
    return base.with_suffix('.json'), base.with_suffix('.absent')


def _read_object(path: Path) -> dict[str, object]:
    loaded: object = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(loaded, dict):
        msg = f'{path} must contain a JSON object'
        raise TypeError(msg)
    return cast('dict[str, object]', loaded)


def _read_layers(path: Path) -> FlagLayers:
    if not path.exists():
        return {}
    result: FlagLayers = {}
    for name, value in _read_object(path).items():
        if name not in {'presets', 'custom'} or not isinstance(value, dict):
            msg = f'{path} contains an invalid FastFlag layer'
            raise ValueError(msg)
        result[cast('FlagLayer', name)] = cast('dict[str, object]', value)
    return result


def _write_json[Key: str](path: Path, payload: Mapping[Key, object], mode: int) -> None:
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore(path: Path, stash: Path, absent: Path, manifest: Path) -> None:
    if stash.exists():
        temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
        try:
            shutil.copy2(stash, temporary)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        stash.unlink()
    elif absent.exists():
        path.unlink(missing_ok=True)
        absent.unlink()
    manifest.unlink(missing_ok=True)


def _synchronize_layer(
    path: Path, stash: Path, absent: Path, layer: FlagLayer, flags: Mapping[str, object]
) -> None:
    manifest = stash.with_suffix('.layers.json')
    layers = _read_layers(manifest)
    if not flags:
        layers.pop(layer, None)
        if not layers:
            _restore(path, stash, absent, manifest)
            return
    else:
        layers[layer] = dict(flags)
    if not stash.exists() and not absent.exists():
        stash.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            # Validate before preserving or changing the user's file
            _read_object(path)
            shutil.copy2(path, stash)
        else:
            absent.touch()
    merged = _read_object(stash) if stash.exists() else {}
    for name in ('presets', 'custom'):
        merged.update(layers.get(name, {}))
    mode = stat.S_IMODE(stash.stat().st_mode) if stash.exists() else 0o600
    _write_json(manifest, layers, 0o600)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, merged, mode)


def set_linux_flag_layer(
    path: Path, stash_dir: Path, layer: FlagLayer, flags: Mapping[str, object]
) -> bool:
    """Apply one layer, restoring the original file when no layers remain."""
    with _FILE_LOCK:
        stash, absent = linux_flag_stash_paths(stash_dir, path)
        try:
            _synchronize_layer(path, stash, absent, layer, flags)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            log_buffer.log('FastFlags', f'Could not synchronize {path}: {exc}')
            return False
        return True
