"""Privacy and correlation tests for opt-in asset-delivery diagnostics."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fleasion.proxy import asset_trace
from fleasion.proxy.asset_trace import AssetTrace

if TYPE_CHECKING:
    import pytest

    from fleasion.config.types import ReplacementMaps


def _capture(monkeypatch: pytest.MonkeyPatch) -> tuple[AssetTrace, list[str]]:
    events: list[str] = []

    def collect(category: str, message: str) -> None:
        assert category == 'AssetTrace'
        events.append(message)

    monkeypatch.setattr(asset_trace.log_buffer, 'log', collect)
    return AssetTrace(enabled=True), events


def _maps() -> ReplacementMaps:
    return ({98765: 22222}, set(), {}, {12345: '/private/replacement.mesh'})


def test_trace_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('FLEASION_ASSET_TRACE', raising=False)
    trace = AssetTrace()
    assert not trace.enabled
    trace.request('assetdelivery.roblox.com', 'GET', '/v1/asset/?id=12345', _maps())
    trace.cdn('fts.rbxcdn.com', '/redacted-hash?auth=secret', 'local')


def test_direct_request_reports_unhandled_exact_replacement_without_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace, events = _capture(monkeypatch)
    trace.request(
        'assetdelivery.roblox.com',
        'GET',
        '/v1/asset/?id=12345&auth=cookie-secret&ticket=private-secret',
        _maps(),
    )
    assert any('replacement_bypassed=True' in e and 'asset_id=12345' in e for e in events)
    assert all('cookie-secret' not in e and 'private-secret' not in e for e in events)
    trace.request('assetdelivery.roblox.com', 'GET', '/v1/asset/?id=777', _maps())
    assert any('asset_id=777' in e and 'replacement_bypassed=False' in e for e in events)


def test_cdn_repeated_fetch_detects_consumed_one_shot_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace, events = _capture(monkeypatch)
    trace.cdn('fts.rbxcdn.com', '/known-hash?auth=super-secret', 'local')
    trace.cdn('fts.rbxcdn.com', '/known-hash?auth=changed-secret', None)
    assert 'route=local previously_replaced=False' in events[-2]
    assert 'route=upstream previously_replaced=True prior_route=local' in events[-1]
    assert 'super-secret' not in '\n'.join(events)
    assert 'changed-secret' not in '\n'.join(events)
    assert '/known-hash' not in '\n'.join(events)


def test_replacement_history_expires_and_stays_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    clock: list[float] = [100.0]
    monkeypatch.setattr(asset_trace.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(asset_trace, '_RECENT_CDN_LIMIT', 2)
    monkeypatch.setattr(asset_trace, '_RECENT_CDN_TTL_SECONDS', 10)
    trace, events = _capture(monkeypatch)
    for name in ('first', 'second', 'third'):
        trace.cdn('fts.rbxcdn.com', f'/{name}', 'local')
    trace.cdn('fts.rbxcdn.com', '/first', None)
    assert 'previously_replaced=False' in events[-1]
    clock[0] += 11
    trace.cdn('fts.rbxcdn.com', '/second', None)
    assert 'previously_replaced=False' in events[-1]


def test_batch_records_target_ids_and_id_swaps_without_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace, events = _capture(monkeypatch)
    original = json.dumps([
        {'assetId': 98765, 'assetTypeId': 4, 'requestId': 'secret-request-id'},
        {'assetId': 12345, 'assetTypeId': 1, 'requestId': 'secret-request-id-2'},
        {'assetId': 54321, 'assetTypeId': 3, 'requestId': 'secret-request-id-3'},
    ]).encode()
    modified = json.dumps([
        {'assetId': 22222, 'assetTypeId': 4},
        {'assetId': 12345, 'assetTypeId': 1},
        {'assetId': 54321, 'assetTypeId': 3},
    ]).encode()
    trace.batch(original, modified, _maps())
    assert 'items=3 forwarded_items=3 id_swaps=1 target_count=2 target_ids=98765,12345' in events[-1]
    assert 'secret-request-id' not in events[-1]
    assert '/private/replacement.mesh' not in events[-1]


def test_trace_records_connection_recovery_without_any_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace, events = _capture(monkeypatch)
    trace.splice('fts.rbxcdn.com', 'open')
    trace.splice('fts.rbxcdn.com', 'error', detail='ConnectionResetError')
    trace.splice('fts.rbxcdn.com', 'closed', detail='client_plain_bytes=1500')
    trace.splice('fts.rbxcdn.com', 'open')
    trace.response('fts.rbxcdn.com', '/hash?auth=token-secret', 200, 4096)
    trace.response(
        'assetdelivery.roblox.com',
        '/v1/asset/?id=12345&cookie=secret-cookie',
        302,
        0,
        location=b'https://fts.rbxcdn.com/path-to-mesh?auth=redirect-token-secret',
    )
    assert sum('phase=open' in e for e in events) == 2
    assert 'phase=error ConnectionResetError' in events[2]
    assert 'status=200 body_bytes=4096' in events[-2]
    assert 'redirect_host=fts.rbxcdn.com redirect_path_key=' in events[-1]
    assert 'token-secret' not in '\n'.join(events)
    assert 'secret-cookie' not in '\n'.join(events)
    assert 'path-to-mesh' not in '\n'.join(events)
