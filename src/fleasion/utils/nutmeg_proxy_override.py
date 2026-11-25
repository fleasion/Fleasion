"""Reversible Nutmeg guest-proxy environment configuration."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Mapping

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


def _load_object(path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        msg = f'Refusing symlink at {path}'
        raise ValueError(msg)
    if not path.exists():
        return None
    if not path.is_file():
        msg = f'Not a regular JSON file: {path}'
        raise ValueError(msg)
    data: object = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not all(
        isinstance(key, str) for key in cast('dict[object, object]', data)
    ):
        msg = f'Invalid JSON object at {path}'
        raise ValueError(msg)
    return cast('dict[str, object]', data)


def _config(path: Path) -> tuple[dict[str, object], dict[str, str], bool, bool]:
    loaded = _load_object(path)
    existed = loaded is not None
    document = {} if loaded is None else loaded
    extra = document.get('extra_env', {})
    if not isinstance(extra, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in cast('dict[object, object]', extra).items()
    ):
        msg = 'Nutmeg extra_env must contain string keys and values'
        raise ValueError(msg)
    return document, cast('dict[str, str]', extra), existed, 'extra_env' in document


def _write(path: Path, data: Mapping[str, object], *, mode: int) -> None:
    if path.is_symlink():
        msg = f'Refusing symlink at {path}'
        raise ValueError(msg)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _state(state_path: Path, config_path: Path) -> dict[str, object] | None:
    state = _load_object(state_path)
    if state is None:
        return None
    prior = state.get('prior')
    applied = state.get('applied')
    header_valid = (
        state.get('version') == _STATE_VERSION
        and state.get('config_path') == str(config_path)
        and isinstance(state.get('config_existed'), bool)
        and isinstance(state.get('extra_existed'), bool)
    )
    prior_map = cast('dict[object, object]', prior) if isinstance(prior, dict) else {}
    applied_map = cast('dict[object, object]', applied) if isinstance(applied, dict) else {}
    entries_valid = (
        set(prior_map) == set(_MANAGED_NAMES)
        and set(applied_map) == set(_MANAGED_NAMES)
        and all(value is None or isinstance(value, str) for value in prior_map.values())
        and all(isinstance(value, str) for value in applied_map.values())
    )
    if not header_valid or not entries_valid:
        msg = 'Invalid Nutmeg proxy recovery state'
        raise ValueError(msg)
    return state


def _mode(path: Path) -> int:
    if not path.exists():
        return 0o600
    if path.is_symlink() or not path.is_file():
        msg = f'Not a regular Nutmeg configuration file: {path}'
        raise ValueError(msg)
    return stat.S_IMODE(path.stat().st_mode)


def _restore(config_path: Path, state_path: Path) -> bool:
    state = _state(state_path, config_path)
    if state is None:
        return True
    document, extra, existed, _extra_existed = _config(config_path)
    if not existed:
        state_path.unlink()
        return True
    prior = cast('dict[str, str | None]', state['prior'])
    applied = cast('dict[str, str]', state['applied'])
    updated = dict(extra)
    for name in _MANAGED_NAMES:
        if updated.get(name) != applied[name]:
            continue
        old_value = prior[name]
        if old_value is None:
            updated.pop(name, None)
        else:
            updated[name] = old_value
    if state['extra_existed'] or updated:
        document['extra_env'] = updated
    else:
        document.pop('extra_env', None)
    if not state['config_existed'] and not document:
        config_path.unlink()
    else:
        _write(config_path, document, mode=_mode(config_path))
    state_path.unlink()
    return True


def clear(config_path: Path, state_path: Path) -> bool:
    """Restore only Fleasion-owned Nutmeg variables, preserving other edits."""
    try:
        return _restore(config_path, state_path)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        log_buffer.log('Launcher', f'Could not restore Nutmeg proxy configuration: {exc}')
        return False


def _arm(proxy_url: str, config_path: Path, state_path: Path) -> None:
    _restore(config_path, state_path)
    document, extra, config_existed, extra_existed = _config(config_path)
    original = {name: extra.get(name) for name in _MANAGED_NAMES}
    applied = {
        name: (_BYPASS if name.casefold() == 'no_proxy' else proxy_url) for name in _PROXY_NAMES
    }
    exposed = '\n'.join(_PROXY_NAMES)
    previous_environ = original['ROSEMARY_ENVIRON']
    applied['ROSEMARY_ENVIRON'] = f'{previous_environ}\n{exposed}' if previous_environ else exposed
    mode = _mode(config_path)
    state = {
        'version': _STATE_VERSION,
        'config_path': str(config_path),
        'config_existed': config_existed,
        'extra_existed': extra_existed,
        'prior': original,
        'applied': applied,
    }
    _write(state_path, state, mode=0o600)
    document['extra_env'] = {**extra, **applied}
    _write(config_path, document, mode=mode)


def arm(proxy_url: str, config_path: Path, state_path: Path) -> bool:
    """Write proxy variables into Nutmeg's next Rosemary launch, with recovery."""
    if not proxy_url.startswith('http://127.0.0.1:'):
        return False
    try:
        _arm(proxy_url, config_path, state_path)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        log_buffer.log('Launcher', f'Could not configure Nutmeg proxy environment: {exc}')
        return False
    return True
