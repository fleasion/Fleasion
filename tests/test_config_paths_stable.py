"""The config manager must not follow module-global paths after construction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleasion.config import manager as config_module
from fleasion.config.manager import ConfigManager, SettingsLoadError


def test_manager_keeps_its_original_paths_when_globals_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    monkeypatch.setattr(config_module, 'CONFIG_DIR', first)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', first / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', first / 'configs')
    original = ConfigManager()
    original.first_time_setup_complete = True
    assert original.create_config('Kept')
    original.enabled_configs = ['Kept']

    monkeypatch.setattr(config_module, 'CONFIG_DIR', second)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', second / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', second / 'configs')

    original.last_config = 'Kept'
    original.enabled_configs = ['Kept']
    original.create_config('Another')
    assert original.configs_folder == first / 'configs'
    assert sorted(original.config_names) == ['Another', 'Default', 'Kept']
    assert original.first_time_setup_complete
    assert json.loads((first / 'settings.json').read_text())['enabled_configs'] == ['Kept']
    assert (first / 'configs' / 'Another.json').exists()
    assert not (second / 'settings.json').exists()
    assert not (second / 'configs').exists()


@pytest.mark.parametrize('invalid_data', ['{"enabled_configs": [', '[]', 'null'])
def test_unreadable_existing_settings_fail_without_resetting_or_rewriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_data: str
) -> None:
    monkeypatch.setattr(config_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', tmp_path / 'configs')
    path = tmp_path / 'settings.json'
    path.write_text(invalid_data, encoding='utf-8')

    with pytest.raises(SettingsLoadError, match='Fleasion settings'):
        ConfigManager()

    assert path.read_text(encoding='utf-8') == invalid_data
    assert not (tmp_path / 'settings.json.bak').exists()
    assert not (tmp_path / 'configs').exists() or not list((tmp_path / 'configs').glob('*.json'))



def test_settings_atomic_write_leaves_no_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', tmp_path / 'configs')
    config = ConfigManager()
    config.first_time_setup_complete = True
    config.audio_volume = 42
    assert json.loads((tmp_path / 'settings.json').read_text())['audio_volume'] == 42
    assert not list(tmp_path.glob('*.tmp'))
    assert not list(tmp_path.glob('.*.tmp'))
