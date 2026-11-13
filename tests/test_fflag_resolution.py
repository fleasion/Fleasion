"""Tests for manually entered unprefixed FastFlag expansion."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleasion.proxy.addons.custom_fflags import (
    DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG,
    CustomFFlagModifier,
)
from fleasion.utils.fflag_resolution import (
    CLIENT_FFLAG_PREFIXES,
    expand_unprefixed_fflags,
    full_prefix,
    known_typed_names,
    qualified_matches,
)


def test_names_stay_fully_qualified_and_known_suffix_collisions_expand() -> None:
    catalog = frozenset({'DFIntNetwork', 'FLogNetwork', 'FIntOther'})
    assert qualified_matches('DFIntNetwork', catalog) == ('DFIntNetwork',)
    assert qualified_matches('Network', catalog) == ('DFIntNetwork', 'FLogNetwork')
    assert qualified_matches('Other', catalog) == ('FIntOther',)
    assert full_prefix('SFFlagServerOnly') == 'SFFlag'
    assert qualified_matches('SFFlagServerOnly', catalog) == ('SFFlagServerOnly',)
    assert expand_unprefixed_fflags({'Network': '2'}, catalog) == {
        'DFIntNetwork': '2',
        'FLogNetwork': '2',
    }


def test_missing_catalog_name_tries_client_prefixes_but_never_server_prefixes() -> None:
    flags = expand_unprefixed_fflags({'TaskSchedulerTargetFps': '50'}, frozenset())
    assert flags == {f'{prefix}TaskSchedulerTargetFps': '50' for prefix in CLIENT_FFLAG_PREFIXES}
    assert all(not key.startswith('SFFlag') for key in flags)


def test_explicit_qualified_override_wins_and_remains_unmodified() -> None:
    catalog = frozenset({'DFIntNetwork', 'FLogNetwork'})
    result = expand_unprefixed_fflags(
        {'FLogNetwork': '6', 'Network': '8', 'DFIntNetwork': '14'}, catalog
    )
    assert result == {'DFIntNetwork': '14', 'FLogNetwork': '6'}


def test_known_names_read_only_current_browser_cache(tmp_path: Path) -> None:
    path = tmp_path / 'fflag_browser.json'
    path.write_text(
        json.dumps({'version': 3, 'fetched_at': time.time(), 'flags': {
            'DFIntNetwork': '5', 'FLogNetwork': None, 'SFFlagServerOnly': None
        }}),
        encoding='utf-8',
    )
    assert known_typed_names(path) == frozenset({'DFIntNetwork', 'FLogNetwork'})
    # A previously memoized index must still expire after 24 hours
    import os

    stale_mtime = time.time() - 24 * 60 * 60 - 1
    os.utime(path, (stale_mtime, stale_mtime))
    assert known_typed_names(path) is None
    os.utime(path, None)
    assert known_typed_names(path) == frozenset({'DFIntNetwork', 'FLogNetwork'})
    outdated = json.loads(path.read_text(encoding='utf-8'))
    outdated['fetched_at'] = time.time() - 24 * 60 * 60 - 1
    path.write_text(json.dumps(outdated), encoding='utf-8')
    assert known_typed_names(path) is None
    outdated['fetched_at'] = time.time()
    path.write_text(json.dumps(outdated), encoding='utf-8')
    assert known_typed_names(path) == frozenset({'DFIntNetwork', 'FLogNetwork'})
    path.write_text(json.dumps({'version': 2, 'flags': {'FIntOther': None}}), encoding='utf-8')
    assert known_typed_names(path) is None


def test_proxy_expands_unprefixed_flags_only_on_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleasion.utils import fflag_resolution

    monkeypatch.setattr(
        fflag_resolution,
        'known_typed_names',
        lambda: frozenset({'DFIntNetwork', 'FLogNetwork'}),
    )
    config = SimpleNamespace(
        custom_fflags={'Network': '8', 'DFIntNetwork': '12'},
        custom_fflags_enabled=True,
        custom_fflag_disabled=[],
        custom_fflag_folders={},
        custom_fflag_disabled_folders=[],
    )
    modifier = CustomFFlagModifier(config)
    flags = modifier.runtime_flags()
    assert flags == {
        'FLogNetwork': '8',
        'DFIntNetwork': '12',
        DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG: '0',
    }
    assert config.custom_fflags == {'Network': '8', 'DFIntNetwork': '12'}
