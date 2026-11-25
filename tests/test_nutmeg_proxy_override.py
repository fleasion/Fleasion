"""Reversible Nutmeg configuration for Rosemary guest proxy routing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from fleasion.utils import nutmeg_proxy_override as override

PROXY_A = 'http://127.0.0.1:58443'
PROXY_B = 'http://127.0.0.1:58444'


def _read(path: Path) -> dict[str, object]:
    data: object = json.loads(path.read_text(encoding='utf-8'))
    assert isinstance(data, dict)
    return cast('dict[str, object]', data)


def _extra(path: Path) -> dict[str, str]:
    value = _read(path).get('extra_env')
    assert isinstance(value, dict)
    assert all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in cast('dict[object, object]', value).items()
    )
    return cast('dict[str, str]', value)


def test_arm_injects_guest_environment_and_clear_restores_original(tmp_path: Path) -> None:
    config = tmp_path / 'nutmeg/config.json'
    state = tmp_path / 'fleasion/state.json'
    config.parent.mkdir()
    original = {
        'rosemary_path': '/custom/rosemary',
        'extra_env': {
            'CUSTOM': 'untouched',
            'HTTP_PROXY': 'http://previous.example:8080',
            'ROSEMARY_ENVIRON': 'CUSTOM\nLANG',
        },
    }
    config.write_text(json.dumps(original), encoding='utf-8')
    assert override.arm(PROXY_A, config, state)
    assert state.is_file()
    assert state.stat().st_mode & 0o777 == 0o600
    values = _extra(config)
    assert values['HTTP_PROXY'] == PROXY_A
    assert values['HTTPS_PROXY'] == PROXY_A
    assert values['no_proxy'] == 'localhost,127.0.0.1,::1'
    assert values['CUSTOM'] == 'untouched'
    assert values['ROSEMARY_ENVIRON'].startswith('CUSTOM\nLANG\n')
    assert values['ROSEMARY_ENVIRON'].endswith('no_proxy')

    assert override.clear(config, state)
    assert _read(config) == original
    assert not state.exists()


def test_crash_recovery_preserves_unrelated_user_edits(tmp_path: Path) -> None:
    config = tmp_path / 'config.json'
    state = tmp_path / 'override.json'
    config.write_text('{"extra_env":{"CUSTOM":"original","ALL_PROXY":"old"}}')
    assert override.arm(PROXY_A, config, state)
    updated = _read(config)
    values = _extra(config)
    values['CUSTOM'] = 'user-edited'
    values['HTTPS_PROXY'] = 'http://user-custom-proxy:1234'
    updated['extra_env'] = values
    updated['decorations'] = 'client'
    config.write_text(json.dumps(updated))

    assert override.clear(config, state)
    recovered = _read(config)
    result = _extra(config)
    assert result['CUSTOM'] == 'user-edited'
    assert result['HTTPS_PROXY'] == 'http://user-custom-proxy:1234'
    assert result['ALL_PROXY'] == 'old'
    assert 'HTTP_PROXY' not in result
    assert 'ROSEMARY_ENVIRON' not in result
    assert recovered['decorations'] == 'client'
    assert not state.exists()


def test_rearm_replaces_proxy_without_losing_preexisting_values(tmp_path: Path) -> None:
    config = tmp_path / 'config.json'
    state = tmp_path / 'state.json'
    config.write_text('{"extra_env":{"NO_PROXY":"original-bypass","USER_VAR":"keep"}}')
    assert override.arm(PROXY_A, config, state)
    assert override.arm(PROXY_B, config, state)
    assert _extra(config)['HTTP_PROXY'] == PROXY_B
    assert _extra(config)['USER_VAR'] == 'keep'
    assert override.clear(config, state)
    assert _extra(config) == {'NO_PROXY': 'original-bypass', 'USER_VAR': 'keep'}
    assert not state.exists()


def test_missing_config_created_and_cleaned_up(tmp_path: Path) -> None:
    config = tmp_path / 'nutmeg/config.json'
    state = tmp_path / 'state.json'
    assert override.arm(PROXY_A, config, state)
    assert _extra(config)['https_proxy'] == PROXY_A
    assert override.clear(config, state)
    assert not config.exists()
    assert not state.exists()


def test_recovery_after_state_persisted_but_before_config_write(tmp_path: Path) -> None:
    config = tmp_path / 'config.json'
    state = tmp_path / 'state.json'
    config.write_text('{"extra_env":{"CUSTOM":"preserved"}}')
    original_write = override._write  # pyright: ignore[reportPrivateUsage]

    def fail_config_write(path: Path, data: dict[str, object], *, mode: int) -> None:
        if path == config:
            msg = 'simulated disk failure'
            raise OSError(msg)
        original_write(path, data, mode=mode)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(override, '_write', fail_config_write)
        assert not override.arm(PROXY_A, config, state)
    assert state.exists()
    assert override.clear(config, state)
    assert _extra(config) == {'CUSTOM': 'preserved'}
    assert not state.exists()


def test_invalid_config_or_state_cannot_be_overwritten(tmp_path: Path) -> None:
    config = tmp_path / 'config.json'
    state = tmp_path / 'state.json'
    config.write_text('{not valid json')
    assert not override.arm(PROXY_A, config, state)
    assert config.read_text() == '{not valid json'
    assert not state.exists()
    config.write_text('{"extra_env":{}}')
    state.write_text('{"bad":true}')
    assert not override.arm(PROXY_A, config, state)
    assert not override.clear(config, state)
    assert _extra(config) == {}
    assert state.exists()


def test_config_and_state_symlinks_are_refused(tmp_path: Path) -> None:
    real = tmp_path / 'real.json'
    real.write_text('{"extra_env":{}}')
    config = tmp_path / 'config.json'
    state = tmp_path / 'state.json'
    config.symlink_to(real)
    assert not override.arm(PROXY_A, config, state)
    assert not state.exists()
    config.unlink()
    config.write_text('{"extra_env":{}}')
    state.symlink_to(real)
    assert not override.arm(PROXY_A, config, state)
    assert not override.clear(config, state)
    assert _extra(real) == {}
