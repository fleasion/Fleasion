"""Merge independent Fleasion flag layers into native Linux client files."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from fleasion.utils.logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Mapping

type FlagLayer = Literal['presets', 'custom']
type FlagLayers = dict[FlagLayer, dict[str, object]]

_FILE_LOCK = threading.RLock()


def linux_flag_stash_paths(stash_dir: Path, path: Path) -> tuple[Path, Path]:
    digest = hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:16]
    base = stash_dir / 'linux_client_fflags' / f'{path.parent.name}-{digest}'
    return base.with_suffix('.json'), base.with_suffix('.absent')


def managed_linux_flag_paths(
    stash_dir: Path, layer: FlagLayer, *, legacy_candidates: Iterable[Path] = ()
) -> set[Path]:
    """Recover a layer's managed targets from on-disk manifests."""
    root = stash_dir / 'linux_client_fflags'
    paths: set[Path] = set()
    for manifest in root.glob('*.layers.json'):
        try:
            payload = _read_object(manifest)
            if not isinstance(payload.get(layer), dict):
                continue
            target = payload.get('_target')
            if not isinstance(target, str):
                continue
            path = Path(target)
            if (
                path.is_absolute()
                and linux_flag_stash_paths(stash_dir, path)[0].with_suffix('.layers.json')
                == manifest
            ):
                paths.add(path)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            log_buffer.log('FastFlags', f'Could not read managed flag target {manifest}: {exc}')

    # Older manifests had no path field; known client files can still be recovered
    for path in legacy_candidates:
        stash, _absent = linux_flag_stash_paths(stash_dir, path)
        try:
            if layer in _read_layers(stash.with_suffix('.layers.json')):
                paths.add(path)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            log_buffer.log('FastFlags', f'Could not read managed flag target {path}: {exc}')
    return paths


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
        if name in {'_target', '_original'}:
            continue
        if name not in {'presets', 'custom'} or not isinstance(value, dict):
            msg = f'{path} contains an invalid FastFlag layer'
            raise ValueError(msg)
        result[cast('FlagLayer', name)] = cast('dict[str, object]', value)
    return result


def _json_equal(left: object, right: object) -> bool:
    """Compare JSON values without collapsing booleans into numeric values."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _managed_values(layers: FlagLayers) -> dict[str, object]:
    values: dict[str, object] = {}
    for name in ('presets', 'custom'):
        values.update(layers.get(name, {}))
    return values


def _original_values(
    manifest: Path, baseline: dict[str, object], owned: set[str]
) -> dict[str, dict[str, object]]:
    """Read per-flag originals, including manifests from older installations."""
    payload = _read_object(manifest) if manifest.exists() else {}
    raw: object = payload.get('_original', {})
    if not isinstance(raw, dict):
        msg = f'{manifest} has invalid saved FastFlag originals'
        raise ValueError(msg)
    entries = cast('dict[str, object]', raw)
    originals: dict[str, dict[str, object]] = {}
    for key in owned:
        entry = entries.get(key)
        if entry is None:
            originals[key] = (
                {'exists': True, 'value': baseline[key]} if key in baseline else {'exists': False}
            )
            continue
        if not isinstance(entry, dict):
            msg = f'{manifest} has an invalid original value for {key}'
            raise ValueError(msg)
        record = cast('dict[str, object]', entry)
        present = record.get('exists')
        if not isinstance(present, bool) or (present and 'value' not in record):
            msg = f'{manifest} has an invalid original value for {key}'
            raise ValueError(msg)
        originals[key] = record
    return originals


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


def _retire_recovery_state(stash: Path, absent: Path, manifest: Path) -> None:
    """Drop obsolete ownership records without touching the native client path."""
    stash.unlink(missing_ok=True)
    absent.unlink(missing_ok=True)
    manifest.unlink(missing_ok=True)


def _synchronize_layer(
    path: Path, stash: Path, absent: Path, layer: FlagLayer, flags: Mapping[str, object]
) -> None:
    manifest = stash.with_suffix('.layers.json')
    try:
        native_mode = path.lstat().st_mode
    except FileNotFoundError:
        native_mode = None
    if native_mode is not None and stat.S_ISLNK(native_mode):
        if flags:
            msg = f'Native FastFlag file {path} is a symlink; refusing to replace it'
            raise ValueError(msg)
        # A user-supplied replacement link cannot contain our former managed file
        _retire_recovery_state(stash, absent, manifest)
        return
    if native_mode is None and not flags:
        # Client removed the file: honour the deletion and release every stale layer
        _retire_recovery_state(stash, absent, manifest)
        return

    previous_layers = _read_layers(manifest)
    old_values = _managed_values(previous_layers)
    if previous_layers and not (stash.exists() or absent.exists()):
        msg = f'Missing original FastFlag state for {path}; refusing to overwrite it'
        raise ValueError(msg)

    baseline = _read_object(stash) if stash.exists() else {}
    current = _read_object(path) if path.exists() else {}
    if old_values and not path.exists():
        msg = f'Native FastFlag file {path} was removed while managed; refusing to recreate it'
        raise ValueError(msg)
    for key, expected in old_values.items():
        if key not in current or not _json_equal(current[key], expected):
            msg = f'Native FastFlag {key} in {path} was edited externally; refusing to overwrite it'
            raise ValueError(msg)

    originals = _original_values(manifest, baseline, set(old_values))
    layers = dict(previous_layers)
    if flags:
        layers[layer] = dict(flags)
    else:
        layers.pop(layer, None)
    new_values = _managed_values(layers)
    for key in new_values.keys() - old_values.keys():
        originals[key] = (
            {'exists': True, 'value': current[key]} if key in current else {'exists': False}
        )

    merged = dict(current)
    for key in old_values.keys() | new_values.keys():
        if key in new_values:
            merged[key] = new_values[key]
        else:
            original = originals[key]
            if original['exists']:
                merged[key] = original['value']
            else:
                merged.pop(key, None)
            originals.pop(key)

    if not layers:
        if (stash.exists() and _json_equal(merged, baseline)) or (absent.exists() and not merged):
            _restore(path, stash, absent, manifest)
            return
        if not _json_equal(merged, current):
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
            _write_json(path, merged, mode)
        _retire_recovery_state(stash, absent, manifest)
        return

    if not stash.exists() and not absent.exists():
        stash.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, stash)
        else:
            absent.touch()
    mode = (
        stat.S_IMODE(path.stat().st_mode)
        if path.exists()
        else stat.S_IMODE(stash.stat().st_mode)
        if stash.exists()
        else 0o600
    )
    _write_json(manifest, {'_target': str(path), '_original': originals, **layers}, 0o600)
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
