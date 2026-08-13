from __future__ import annotations

from pathlib import Path

import pytest

from fleasion.modifications import fflag_catalog
from fleasion.modifications.fflag_catalog import FastFlagCatalog


def test_fflag_browser_extracts_application_values():
    assert FastFlagCatalog.extract_flags(
        {'applicationSettings': {'FFlagExample': True, 'DFIntLimit': 120, 'skip': []}}
    ) == {'FFlagExample': 'True', 'DFIntLimit': '120'}
    with pytest.raises(ValueError, match='application FastFlags'):
        FastFlagCatalog.extract_flags({})


def test_fflag_browser_extracts_tracker_only_fastvariables():
    assert FastFlagCatalog.extract_tracker_flags(
        b'[C++] DFFlagDebugDrawBroadPhaseAABBs\n'
        b'[C++] DFIntTaskSchedulerTargetFps\n'
        b'[C++] NotAFastVariable\n'
    ) == {
        'DFFlagDebugDrawBroadPhaseAABBs': None,
        'DFIntTaskSchedulerTargetFps': None,
    }


def test_fflag_browser_merges_live_values_with_tracker_lists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(FastFlagCatalog, 'cache_path', tmp_path / 'fflag_browser.json')

    def fake_http_get(
        url: str,
        timeout: int,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        assert timeout > 0
        if url == FastFlagCatalog.SETTINGS_URL:
            assert headers == FastFlagCatalog.BYPASS_CUSTOM_FFLAGS_HEADER
            return b'{"applicationSettings":{"DFFlagDebugDrawBroadPhaseAABBs":"False"}}'
        if url in FastFlagCatalog.settings_urls():
            assert headers == FastFlagCatalog.BYPASS_CUSTOM_FFLAGS_HEADER
            return b'{"applicationSettings":{"DFIntTaskSchedulerTargetFps":"60"}}'
        if url == FastFlagCatalog.TRACKER_VARIABLES_URL:
            return b'[C++] DFFlagDebugDrawBroadPhaseAABBs\n'
        if url == FastFlagCatalog.HISTORICAL_TRACKER_VARIABLES_URL:
            return b'[C++] DFIntTaskSchedulerTargetFps\n'
        raise AssertionError(f'unexpected URL: {url}')

    monkeypatch.setattr(fflag_catalog, 'http_get', fake_http_get)
    fetched = FastFlagCatalog.fetch()

    assert fetched == {
        'DFFlagDebugDrawBroadPhaseAABBs': 'False',
        'DFIntTaskSchedulerTargetFps': None,
    }


def test_fflag_browser_cache_expires_after_one_hour(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    cache_path = tmp_path / 'fflag_browser.json'
    monkeypatch.setattr(FastFlagCatalog, 'cache_path', cache_path)
    flags = {'DFFlagDebugDrawBroadPhaseAABBs': None, 'FFlagExample': 'True'}

    FastFlagCatalog.write_cache(flags, now=10_000)

    assert FastFlagCatalog.read_cache(now=13_599) == flags
    assert FastFlagCatalog.read_cache(now=13_600) is None
