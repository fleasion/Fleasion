"""Per-game profiles: switching configs and filters when Roblox joins a place."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from fleasion.config import manager
from fleasion.proxy.server import ProxyFlow
from fleasion.rules.asset_filters import make_step
from fleasion.rules.game_profiles import (
    GameProfileTracker,
    make_profile,
    normalize_game_profiles,
    parse_place_id,
    place_id_from_join_request,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> manager.ConfigManager:
    monkeypatch.setattr(manager, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(manager, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path / 'configs')
    return manager.ConfigManager()


def _join_flow(path: str, body: object, host: str = 'gamejoin.roblox.com') -> ProxyFlow:
    payload = json.dumps(body).encode()
    first_line = f'POST {path} HTTP/1.1'.encode()
    return ProxyFlow(first_line, {b'content-type': b'application/json'}, payload, host)


def test_parse_place_id_accepts_only_positive_ids() -> None:
    assert parse_place_id(73337916796932) == 73337916796932
    assert parse_place_id(' 42 ') == 42
    for bad in (0, -5, True, '12a', '', None, 2**64, 1.5):
        assert parse_place_id(bad) is None


@pytest.mark.parametrize(
    'path',
    ['/v1/join-game', '/v1/join-game-instance', '/v1/join-private-game', '/v1/join-play-together-game'],
)
def test_join_requests_report_their_place(path: str) -> None:
    assert place_id_from_join_request(path, b'{"placeId": 606849621, "isTeleport": false}') == 606849621


def test_non_join_or_malformed_requests_are_ignored() -> None:
    assert place_id_from_join_request('/v1/other', b'{"placeId": 5}') is None
    assert place_id_from_join_request('/v1/join-game', b'not json') is None
    assert place_id_from_join_request('/v1/join-game', b'[1, 2]') is None
    assert place_id_from_join_request('/v1/join-game', b'{"placeId": "x"}') is None


def test_tracker_reports_joins_on_gamejoin_host_only() -> None:
    joined: list[int] = []
    tracker = GameProfileTracker(joined.append)
    tracker.request(_join_flow('/v1/join-game', {'placeId': 1818}))
    tracker.request(_join_flow('/v1/join-game', {'placeId': 99}, host='example.com'))
    tracker.request(_join_flow('/v1/join-game-instance', {'placeId': 2020, 'gameId': 'abc'}))
    assert joined == [1818, 2020]
    assert tracker.last_place_id == 2020


def test_normalize_profiles_drops_invalid_and_duplicate_places() -> None:
    profiles = normalize_game_profiles(
        [
            {'place_id': 10, 'name': 'Surf', 'configs': ['Sky', 3, ''], 'filters': {'enabled': True}},
            {'place_id': 10, 'name': 'Duplicate'},
            {'place_id': 'nope'},
            'junk',
            {'place_id': '11', 'enabled': False},
        ]
    )
    assert [profile['place_id'] for profile in profiles] == [10, 11]
    assert profiles[0]['configs'] == ['Sky']
    assert profiles[0]['filters'] is not None and profiles[0]['filters']['enabled']
    assert profiles[1]['name'] == 'Place 11' and profiles[1]['filters'] is None
    assert not profiles[1]['enabled']
    assert normalize_game_profiles({'place_id': 1}) == []


def _write_config(config: manager.ConfigManager, name: str, asset_id: int) -> None:
    rules: list[manager.JsonValue] = [{'replace_ids': [asset_id], 'mode': 'remove', 'enabled': True}]
    config.set_replacement_rules(name, rules)


def test_profiles_switch_the_replacements_the_proxy_sees(config: manager.ConfigManager) -> None:
    _write_config(config, 'Everyday', 111)
    _write_config(config, 'SurfOnly', 222)
    config.enabled_configs = ['Everyday']
    config.game_profiles = [make_profile(5000, 'Surf', ['SurfOnly', 'Missing'])]

    assert config.get_all_replacements()[1] == {111}
    assert config.set_active_place(5000) is not None
    assert config.effective_enabled_configs() == ['SurfOnly']
    assert config.get_all_replacements()[1] == {222}
    assert config.enabled_configs == ['Everyday']  # the user's own selection is untouched

    assert config.set_active_place(6000) is None
    assert config.get_all_replacements()[1] == {111}


def test_disabled_profiles_and_unset_filters_fall_back(config: manager.ConfigManager) -> None:
    config.asset_filters = {'enabled': True, 'include_materials': False, 'steps': [make_step('invert')]}
    profile = make_profile(7, 'Plain')
    config.game_profiles = [profile]
    config.set_active_place(7)
    assert config.effective_asset_filters() == config.asset_filters

    tinted = make_profile(7, 'Tinted', filters={'enabled': True, 'include_materials': False, 'steps': [make_step('grayscale')]})
    config.game_profiles = [tinted]
    assert config.effective_asset_filters()['steps'][0]['effect'] == 'grayscale'

    tinted['enabled'] = False
    config.game_profiles = [tinted]
    assert config.active_game_profile is None
    assert config.effective_asset_filters()['steps'][0]['effect'] == 'invert'


def test_profiles_and_filters_persist(config: manager.ConfigManager, tmp_path: Path) -> None:
    config.game_profiles = [make_profile(123, 'Saved', ['A'])]
    config.asset_filters = {'enabled': True, 'include_materials': True, 'steps': [make_step('tint', color='#ff0000')]}
    reloaded = manager.ConfigManager()
    assert reloaded.game_profiles[0]['name'] == 'Saved'
    assert reloaded.asset_filters['steps'][0]['color'] == '#ff0000'
    assert reloaded.active_place_id is None
    assert (tmp_path / 'settings.json').exists()
