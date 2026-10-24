"""Filtered local/CDN replacement rules (audio length and file size)."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest
import soundfile  # pyright: ignore[reportMissingTypeStubs]

from fleasion.config import manager as config_module
from fleasion.config.manager import ConfigManager, ReplacementMaps
from fleasion.proxy.addons.texture_stripper import TextureStripper
from fleasion.utils.replacement_filters import (
    ALWAYS,
    FilteredRoute,
    ReplacementFilter,
    audio_duration_seconds,
    choose_route,
    describe,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _ogg(seconds: float) -> bytes:
    buffer = io.BytesIO()
    write = cast('Callable[..., None]', soundfile.write)
    write(buffer, np.zeros(int(44100 * seconds)), 44100, format='OGG')
    return buffer.getvalue()


def setup_function() -> None:
    TextureStripper.reset_routes()


# Filter parsing and matching


def test_filters_parse_positive_bounds_and_ignore_the_rest() -> None:
    parsed = ReplacementFilter.from_config(
        {'min_seconds': 0, 'max_seconds': '3', 'max_kb': 200, 'min_kb': True}
    )

    assert parsed == ReplacementFilter(max_seconds=3.0, max_kb=200.0)
    assert parsed is not None
    assert parsed.to_config() == {'max_seconds': 3.0, 'max_kb': 200.0}
    assert ReplacementFilter.from_config({'max_seconds': 0}) is None
    assert ReplacementFilter.from_config(None) is None


def test_length_bounds_never_match_assets_that_are_not_audio() -> None:
    short_audio = ReplacementFilter(max_seconds=2)

    assert short_audio.matches(10_000, 1.5)
    assert not short_audio.matches(10_000, 2.5)
    assert not short_audio.matches(10_000, None)
    assert ReplacementFilter(max_kb=1).matches(1024, None)
    assert not ReplacementFilter(max_kb=1).matches(1025, None)


def test_audio_length_is_read_from_the_original() -> None:
    assert audio_duration_seconds(_ogg(2)) == pytest.approx(2.0)
    assert audio_duration_seconds(b'not audio') is None


def test_first_matching_rule_wins_and_unfiltered_fallback_comes_last() -> None:
    routes = (
        FilteredRoute(ReplacementFilter(max_seconds=1), 'local', 'click.ogg'),
        FilteredRoute(ReplacementFilter(min_seconds=30), 'local', 'song.ogg'),
        FilteredRoute(ALWAYS, 'cdn', 'https://example.com/default.ogg'),
    )

    short = choose_route(routes, _ogg(0.5))
    medium = choose_route(routes, _ogg(2))
    assert short is not None and short.value == 'click.ogg'
    assert medium is not None and medium.value == 'https://example.com/default.ogg'
    assert choose_route(routes[:2], _ogg(2)) is None


def test_describe_summarises_bounds() -> None:
    assert describe({'max_seconds': 3}) == '<=3s'
    assert describe({'min_seconds': 1, 'max_seconds': 3, 'min_kb': 50}) == '1-3s, >=50KB'
    assert describe({}) == ''


# Routing through the proxy


class _Config:
    def __init__(
        self,
        filtered: dict[int | str, tuple[FilteredRoute, ...]],
        local: dict[int | str, str] | None = None,
    ) -> None:
        self._filtered = filtered
        self._local = local or {}

    def get_all_replacements(self) -> ReplacementMaps:
        return {}, set(), {}, self._local

    def get_filtered_replacements(self) -> dict[int | str, tuple[FilteredRoute, ...]]:
        return self._filtered


def _route_one(
    stripper: TextureStripper, config: _Config, asset_id: int = 1234
) -> tuple[str, object] | None:
    body = json.dumps([{'assetId': asset_id, 'assetType': 'Audio', 'requestId': 'a'}]).encode()
    process_request = cast('Callable[..., tuple[bytes, bytes]]', stripper.process_batch_request)
    modified, _ = process_request(body, {}, config.get_all_replacements(), 'b')
    response = json.dumps([{'requestId': 'a', 'location': 'https://fts.rbxcdn.com/sound'}])
    stripper.process_batch_response(modified, response.encode(), {}, 'b')
    return stripper.check_cdn_request('fts.rbxcdn.com', '/sound')


def _stripper(config: _Config) -> TextureStripper:
    return TextureStripper(cast('ConfigManager', config))


def test_type_rule_with_filters_is_decided_at_download_time() -> None:
    short = FilteredRoute(ReplacementFilter(max_seconds=1), 'local', '/tmp/click.ogg')
    config = _Config({3: (short,)}, local={3: '/tmp/song.ogg'})
    stripper = _stripper(config)

    match = _route_one(stripper, config)

    assert match is not None
    action, routes = match
    assert action == 'filtered'
    assert routes == (short, FilteredRoute(ALWAYS, 'local', '/tmp/song.ogg'))
    chosen = stripper.choose_filtered_route(
        cast('tuple[FilteredRoute, ...]', routes), _ogg(3), 'https://fts.rbxcdn.com/sound'
    )
    assert chosen is not None and chosen.value == '/tmp/song.ogg'


def test_an_unfiltered_rule_for_the_asset_itself_beats_a_filtered_type_rule() -> None:
    short = FilteredRoute(ReplacementFilter(max_seconds=1), 'local', '/tmp/click.ogg')
    config = _Config({3: (short,)}, local={1234: '/tmp/exact.ogg'})
    stripper = _stripper(config)

    assert _route_one(stripper, config) == ('local', '/tmp/exact.ogg')


def test_config_keeps_filtered_rules_out_of_the_unconditional_maps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', tmp_path / 'configs')
    replacement = tmp_path / 'click.ogg'
    replacement.write_bytes(b'OggS')
    manager = ConfigManager()
    manager.create_config('c')
    manager.set_replacement_rules(
        'c',
        [
            {
                'name': 'clicks',
                'replace_ids': ['Audio'],
                'mode': 'local',
                'local_path': str(replacement),
                'filters': {'max_seconds': 1},
            }
        ],
    )
    manager.enabled_configs = ['c']

    _replacements, _removals, _cdn, local = manager.get_all_replacements()
    filtered = manager.get_filtered_replacements()

    assert local == {}
    (route,) = filtered[3]
    assert route.filters == ReplacementFilter(max_seconds=1)
    assert route.mode == 'local'
