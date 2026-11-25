from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from fleasion.config import manager as config_module
from fleasion.config.rules import is_rule_entry
from fleasion.config.values import bool_value, int_value, parse_int, str_value, string_list

if TYPE_CHECKING:
    from pathlib import Path

    from fleasion.utils.json_types import JsonValue


@pytest.mark.parametrize('value', ['false', 1, [], {}])
def test_boolean_settings_reject_non_booleans(value: object) -> None:
    assert bool_value(value) is False
    assert bool_value(value, default=True) is True


def test_scalar_settings_validate_without_coercing() -> None:
    assert bool_value(False, default=True) is False
    assert int_value(True, 70) == 70
    assert int_value('90', 70) == 70
    assert int_value(90, 70) == 90
    assert str_value({}, 'Default') == 'Default'
    assert parse_int('123') == 123
    assert parse_int({}, 16) == 16
    assert parse_int(float('inf'), 16) == 16


def test_string_list_settings_filter_invalid_elements() -> None:
    values: list[JsonValue] = ['first', 1, False, {}, 'last']
    assert string_list(values) == ['first', 'last']
    assert string_list(None, ('name', 'id')) == ['name', 'id']


INVALID_RULES: tuple[object, ...] = (
    {'name': 1},
    {'enabled': 'yes'},
    {'replace_ids': [1, dict[str, object]()]},
    {'with_id': True},
    {'cdn_url': 42},
    {'local_path': dict[str, object]()},
    {'type': 'group', 'name': 'Group', 'expanded': 'yes'},
)


@pytest.mark.parametrize('rule', INVALID_RULES)
def test_rule_guard_rejects_invalid_field_types(rule: object) -> None:
    assert not is_rule_entry(rule)


def test_group_children_remain_raw_for_lossless_config_editing() -> None:
    assert is_rule_entry({'name': 'Legacy', 'replace_ids': [123, '456'], 'notes': 'kept'})
    assert is_rule_entry({'type': 'group', 'name': 'Group', 'children': [None, 42]})
    assert is_rule_entry({'type': 'group', 'name': 'Group', 'children': 'invalid'})


def test_invalid_persisted_settings_and_replacement_targets_are_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', tmp_path / 'configs')
    (tmp_path / 'settings.json').write_text(
        json.dumps({'strip_textures': 'false', 'audio_volume': {}, 'scraper_blacklist': [1, '123']}),
        encoding='utf-8',
    )
    manager = config_module.ConfigManager()
    assert manager.strip_textures is False
    assert manager.audio_volume == 70
    assert manager.scraper_blacklist == ['123']
    manager.replacement_rules = [
        {'replace_ids': [123], 'mode': 'cdn', 'cdn_url': 42},
        {'replace_ids': [456], 'mode': 'local', 'local_path': {'bad': 'path'}},
        {'replace_ids': [789], 'mode': 'cdn', 'cdn_url': 'https://example.test/asset'},
    ]
    manager.enabled_configs = ['Default']
    _, _, cdn, local = manager.get_all_replacements()
    assert cdn == {789: 'https://example.test/asset'}
    assert local == {}
