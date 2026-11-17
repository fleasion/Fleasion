"""Reversible proxy environment entries in Quadra's native TOML settings."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

import tomlkit
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import AoT, Array, Table

from .logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

_PROXY_NAMES = (
    'HTTP_PROXY',
    'HTTPS_PROXY',
    'ALL_PROXY',
    'http_proxy',
    'https_proxy',
    'all_proxy',
    'NO_PROXY',
    'no_proxy',
)
_MANAGED_NAMES = (*_PROXY_NAMES, 'ROSEMARY_ENVIRON')
_BYPASS = 'localhost,127.0.0.1,::1'
_STATE_VERSION = 1


def _read_settings(path: Path) -> tomlkit.TOMLDocument:
    if path.is_symlink() or not path.is_file():
        msg = f'Quadra settings must be an existing regular file: {path}'
        raise ValueError(msg)
    document = tomlkit.parse(path.read_text(encoding='utf-8'))
    variables: object = cast('MutableMapping[str, object]', document).get('environment_variables')
    if (
        variables is not None
        and not isinstance(variables, AoT)
        and not (isinstance(variables, Array) and not variables)
    ):
        msg = 'Quadra environment_variables must be tables or an empty array'
        raise TypeError(msg)
    for entry in cast('list[object]', variables) if isinstance(variables, AoT) else ():
        if not isinstance(entry, Table):
            msg = 'Quadra environment_variables must contain tables'
            raise TypeError(msg)
        fields = cast('Mapping[str, object]', entry)
        if (
            not isinstance(fields.get('name'), str)
            or not isinstance(fields.get('value'), str)
            or not isinstance(fields.get('enabled'), bool)
        ):
            msg = 'Quadra environment_variables must contain named, typed entries'
            raise TypeError(msg)
    return document


def _read_state(path: Path, settings_path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        msg = f'Unsafe Quadra proxy state path: {path}'
        raise ValueError(msg)
    if not path.exists():
        return None
    payload: object = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        msg = 'Invalid Quadra proxy recovery state'
        raise TypeError(msg)
    state = cast('dict[str, object]', payload)
    values = state.get('applied')
    applied = cast('dict[str, object]', values) if isinstance(values, dict) else {}
    prior_length = state.get('prior_length')
    header_valid = (
        state.get('version') == _STATE_VERSION
        and state.get('settings_path') == str(settings_path)
        and isinstance(prior_length, int)
        and not isinstance(prior_length, bool)
        and prior_length >= 0
        and state.get('prior_style') in {'missing', 'empty_array', 'tables'}
    )
    entries_valid = (
        isinstance(values, dict)
        and set(applied) == set(_MANAGED_NAMES)
        and all(isinstance(value, str) for value in applied.values())
    )
    if not header_valid or not entries_valid:
        msg = 'Invalid Quadra proxy recovery state'
        raise ValueError(msg)
    return state


def _write(path: Path, contents: str, *, mode: int) -> None:
    if path.is_symlink():
        msg = f'Unsafe Quadra settings path: {path}'
        raise ValueError(msg)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    temporary = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore(settings_path: Path, state_path: Path) -> bool:
    state = _read_state(state_path, settings_path)
    if state is None:
        return True
    document = _read_settings(settings_path)
    variables: object = cast('MutableMapping[str, object]', document).get('environment_variables')
    applied = cast('dict[str, str]', state['applied'])
    if variables is None or (isinstance(variables, Array) and not variables):
        # The settings write may have failed after the recovery record landed
        # or Quadra may have rewritten its settings while it was already open
        state_path.unlink()
        return True
    if not isinstance(variables, AoT):
        msg = 'Quadra proxy recovery entries have an unsupported format'
        raise TypeError(msg)
    entries = cast('list[dict[str, object]]', variables)
    prior_length = cast('int', state['prior_length'])
    # Never remove pre-existing entries, even when they have matching names/values.
    for index in range(len(entries) - 1, prior_length - 1, -1):
        entry = entries[index]
        name = entry.get('name')
        if (
            isinstance(name, str)
            and name in applied
            and entry.get('value') == applied[name]
            and entry.get('enabled') is True
        ):
            entries.pop(index)
    if not entries and prior_length == 0:
        if state['prior_style'] == 'missing':
            cast('MutableMapping[str, object]', document).pop('environment_variables', None)
        elif state['prior_style'] == 'empty_array':
            cast('MutableMapping[str, object]', document)['environment_variables'] = tomlkit.array()
    _write(settings_path, tomlkit.dumps(document), mode=stat.S_IMODE(settings_path.stat().st_mode))
    state_path.unlink()
    return True


def clear(settings_path: Path, state_path: Path) -> bool:
    """Remove only unchanged Fleasion-added Quadra entries."""
    try:
        return _restore(settings_path, state_path)
    except (
        OSError,
        ValueError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
        TOMLKitError,
    ) as exc:
        log_buffer.log('Launcher', f'Could not restore Quadra proxy settings: {exc}')
        return False


def _arm(proxy_url: str, settings_path: Path, state_path: Path) -> None:
    _restore(settings_path, state_path)
    document = _read_settings(settings_path)
    variables: object = cast('MutableMapping[str, object]', document).get('environment_variables')
    prior_style = (
        'missing'
        if variables is None
        else 'empty_array'
        if isinstance(variables, Array)
        else 'tables'
    )
    if variables is None or isinstance(variables, Array):
        variables = tomlkit.aot()
        cast('MutableMapping[str, object]', document)['environment_variables'] = variables
    if not isinstance(variables, AoT):
        msg = 'Invalid Quadra environment_variables'
        raise TypeError(msg)
    entries = cast('list[dict[str, object]]', variables)
    # Quadra applies enabled variables in order. Appending wins over existing
    # values without modifying the user's own environment-variable entries.
    applied = {
        name: (_BYPASS if name.casefold() == 'no_proxy' else proxy_url) for name in _PROXY_NAMES
    }
    prior_environ = next(
        (
            value
            for entry in reversed(entries)
            if entry.get('name') == 'ROSEMARY_ENVIRON' and entry.get('enabled') is True
            if isinstance(value := entry.get('value'), str)
        ),
        '',
    )
    exposed = '\n'.join(_PROXY_NAMES)
    applied['ROSEMARY_ENVIRON'] = f'{prior_environ}\n{exposed}' if prior_environ else exposed
    state: Mapping[str, object] = {
        'version': _STATE_VERSION,
        'settings_path': str(settings_path),
        'prior_length': len(entries),
        'prior_style': prior_style,
        'applied': applied,
    }
    for name, value in applied.items():
        entry = tomlkit.table()
        entry['name'] = name
        entry['value'] = value
        entry['enabled'] = True
        entries.append(cast('dict[str, object]', entry))
    mode = stat.S_IMODE(settings_path.stat().st_mode)
    # Save the recovery record first; if the settings write fails, cleanup is safe.
    _write(state_path, json.dumps(state) + '\n', mode=0o600)
    _write(settings_path, tomlkit.dumps(document), mode=mode)


def arm(proxy_url: str, settings_path: Path, state_path: Path) -> bool:
    """Configure the next Quadra-owned Rosemary launch without replacing settings."""
    if not proxy_url.startswith('http://127.0.0.1:'):
        return False
    try:
        _arm(proxy_url, settings_path, state_path)
    except (
        OSError,
        ValueError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
        TOMLKitError,
    ) as exc:
        log_buffer.log('Launcher', f'Could not configure Quadra proxy settings: {exc}')
        return False
    return True
