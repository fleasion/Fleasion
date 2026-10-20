"""Filtered local/CDN replacement rules (audio length and file size)."""

from __future__ import annotations

import io
import json

import numpy as np
import pytest
import soundfile

from fleasion.proxy.addons.texture_stripper import TextureStripper
from fleasion.utils.replacement_filters import (
    ALWAYS,
    FilteredRoute,
    ReplacementFilter,
    audio_duration_seconds,
    choose_route,
    describe,
)


def _ogg(seconds: float) -> bytes:
    buffer = io.BytesIO()
    soundfile.write(buffer, np.zeros(int(44100 * seconds)), 44100, format='OGG')
    return buffer.getvalue()


def setup_function():
    TextureStripper.reset_routes()


# Filter parsing and matching


def test_filters_parse_positive_bounds_and_ignore_the_rest():
    parsed = ReplacementFilter.from_config(
        {'min_seconds': 0, 'max_seconds': '3', 'max_kb': 200, 'min_kb': True}
    )

    assert parsed == ReplacementFilter(max_seconds=3.0, max_kb=200.0)
    assert parsed.to_config() == {'max_seconds': 3.0, 'max_kb': 200.0}
    assert ReplacementFilter.from_config({'max_seconds': 0}) is None
    assert ReplacementFilter.from_config(None) is None


def test_length_bounds_never_match_assets_that_are_not_audio():
    short_audio = ReplacementFilter(max_seconds=2)

    assert short_audio.matches(10_000, 1.5)
    assert not short_audio.matches(10_000, 2.5)
    assert not short_audio.matches(10_000, None)
    assert ReplacementFilter(max_kb=1).matches(1024, None)
    assert not ReplacementFilter(max_kb=1).matches(1025, None)


def test_audio_length_is_read_from_the_original():
    assert audio_duration_seconds(_ogg(2)) == pytest.approx(2.0)
    assert audio_duration_seconds(b'not audio') is None


def test_first_matching_rule_wins_and_unfiltered_fallback_comes_last():
    routes = (
        FilteredRoute(ReplacementFilter(max_seconds=1), 'local', 'click.ogg'),
        FilteredRoute(ReplacementFilter(min_seconds=30), 'local', 'song.ogg'),
        FilteredRoute(ALWAYS, 'cdn', 'https://example.com/default.ogg'),
    )

    assert choose_route(routes, _ogg(0.5)).value == 'click.ogg'
    assert choose_route(routes, _ogg(2)).value == 'https://example.com/default.ogg'
    assert choose_route(routes[:2], _ogg(2)) is None


def test_describe_summarises_bounds():
    assert describe({'max_seconds': 3}) == '<=3s'
    assert describe({'min_seconds': 1, 'max_seconds': 3, 'min_kb': 50}) == '1-3s, >=50KB'
    assert describe({}) == ''


# Routing through the proxy


class _Config:
    def __init__(self, filtered, local=None):
        self._filtered = filtered
        self._local = local or {}

    def get_all_replacements(self):
        return {}, set(), {}, self._local

    def get_filtered_replacements(self):
        return self._filtered


def _route_one(stripper, config, asset_id=1234):
    body = json.dumps([{'assetId': asset_id, 'assetType': 'Audio', 'requestId': 'a'}]).encode()
    modified, _ = stripper.process_batch_request(body, {}, config.get_all_replacements(), 'b')
    response = json.dumps([{'requestId': 'a', 'location': 'https://fts.rbxcdn.com/sound'}])
    stripper.process_batch_response(modified, response.encode(), {}, 'b')
    return stripper.check_cdn_request('fts.rbxcdn.com', '/sound')


def test_type_rule_with_filters_is_decided_at_download_time():
    short = FilteredRoute(ReplacementFilter(max_seconds=1), 'local', '/tmp/click.ogg')
    config = _Config({3: (short,)}, local={3: '/tmp/song.ogg'})
    stripper = TextureStripper(config)

    action, routes = _route_one(stripper, config)

    assert action == 'filtered'
    assert routes == (short, FilteredRoute(ALWAYS, 'local', '/tmp/song.ogg'))
    chosen = stripper.choose_filtered_route(routes, _ogg(3), 'https://fts.rbxcdn.com/sound')
    assert chosen.value == '/tmp/song.ogg'


def test_an_unfiltered_rule_for_the_asset_itself_beats_a_filtered_type_rule():
    short = FilteredRoute(ReplacementFilter(max_seconds=1), 'local', '/tmp/click.ogg')
    config = _Config({3: (short,)}, local={1234: '/tmp/exact.ogg'})
    stripper = TextureStripper(config)

    assert _route_one(stripper, config) == ('local', '/tmp/exact.ogg')


def test_config_keeps_filtered_rules_out_of_the_unconditional_maps(tmp_path, monkeypatch):
    from fleasion.config import manager as config_module

    replacement = tmp_path / 'click.ogg'
    replacement.write_bytes(b'OggS')
    manager = config_module.ConfigManager.__new__(config_module.ConfigManager)
    manager._all_replacements_cache = None
    manager._all_replacements_cache_signature = None
    manager._filtered_replacements_cache = {}
    rules = [
        {
            'name': 'clicks',
            'replace_ids': ['Audio'],
            'mode': 'local',
            'local_path': str(replacement),
            'filters': {'max_seconds': 1},
        }
    ]
    monkeypatch.setattr(
        config_module.ConfigManager, 'enabled_configs', property(lambda _self: ['c'])
    )
    monkeypatch.setattr(
        manager, '_get_config_path', lambda _name: tmp_path / 'c.json', raising=False
    )
    monkeypatch.setattr(manager, '_file_signature', lambda _path: None, raising=False)
    monkeypatch.setattr(manager, 'get_replacement_rules', lambda _name: rules, raising=False)

    _replacements, _removals, _cdn, local = manager.get_all_replacements()
    filtered = manager.get_filtered_replacements()

    assert local == {}
    (route,) = filtered[3]
    assert route.filters == ReplacementFilter(max_seconds=1)
    assert route.mode == 'local'
