"""Fail-closed boundaries for the dynamic ClientSettings local-response cache."""

from __future__ import annotations

from fleasion.proxy import client_settings_cache as cache_module
from fleasion.proxy.client_settings_cache import (
    CLIENT_SETTINGS_MAX_SNAPSHOTS,
    CLIENT_SETTINGS_MAX_SNAPSHOT_BYTES,
    ClientSettingsKey,
    ClientSettingsLocalCache,
    ClientSettingsSnapshot,
    client_settings_cache_key,
    local_client_settings_reply,
    make_local_client_settings_response,
    may_cache_client_settings_response,
)


def _snapshot(*, when: float = 0.0) -> ClientSettingsSnapshot:
    payload = b'{"applicationSettings":{"FFlagExample":"True"}}'
    etag, response = make_local_client_settings_response(
        payload, None, b'HTTP/1.1 200 OK', {b'cache-control': b'public, max-age=10'}
    )
    return ClientSettingsSnapshot(
        status_line=b'HTTP/1.1 200 OK',
        headers={},
        upstream_body=payload,
        delivered_body=payload,
        encoding=None,
        signature=(('FFlagExample', 'True'),),
        generation=0,
        checked_at=when,
        etag=etag,
        response=response,
    )


def test_cache_key_rejects_private_and_non_get_requests() -> None:
    key = client_settings_cache_key('clientsettings.roblox.com', '/settings/application/X', 'GET', {}, b'')
    assert key is not None
    for headers in (
        {b'authorization': b'Bearer credential'},
        {b'cookie': b'session=value'},
        {b'range': b'bytes=0-100'},
        {b'if-range': b'validator'},
    ):
        assert client_settings_cache_key('clientsettings.roblox.com', '/settings/application/X', 'GET', headers, b'') is None
    assert client_settings_cache_key('clientsettings.roblox.com', '/settings/application/X', 'POST', {}, b'') is None
    assert client_settings_cache_key('clientsettings.roblox.com', '/settings/application/X', 'GET', {}, b'not empty') is None
    assert key != client_settings_cache_key(
        'clientsettings.roblox.com', '/settings/application/X', 'GET', {b'user-agent': b'another-client'}, b''
    )


def test_cache_rejects_private_invalid_or_unknown_vary_responses() -> None:
    payload = b'{"applicationSettings":{}}'
    assert may_cache_client_settings_response(200, {}, payload)
    assert may_cache_client_settings_response(200, {b'vary': b'Accept-Encoding, Accept-Language'}, payload)
    for status, headers, body in (
        (304, {}, payload),
        (200, {}, b''),
        (200, {}, b'x' * (CLIENT_SETTINGS_MAX_SNAPSHOT_BYTES + 1)),
        (200, {b'set-cookie': b'a=b'}, payload),
        (200, {b'cache-control': b'private, no-cache'}, payload),
        (200, {b'cache-control': b'no-store'}, payload),
        (200, {b'vary': b'Accept-Encoding, Cookie'}, payload),
        (200, {b'vary': b'*'}, payload),
    ):
        assert not may_cache_client_settings_response(status, headers, body)


def test_local_304_requires_our_validator_and_has_no_body() -> None:
    snapshot = _snapshot()
    header_block, _, body = snapshot.response.partition(b'\r\n\r\n')
    assert b'etag: ' + snapshot.etag in header_block
    assert header_block.count(b'cache-control:') == 1
    assert body == snapshot.delivered_body
    assert b'content-length: ' + str(len(body)).encode('ascii') in header_block
    unvalidated, full_body = local_client_settings_reply(snapshot, {b'if-none-match': b'"upstream-tag"'})
    assert full_body
    assert unvalidated == snapshot.response
    validated, full_body = local_client_settings_reply(snapshot, {b'if-none-match': snapshot.etag})
    assert not full_body
    assert validated.startswith(b'HTTP/1.1 304 Not Modified\r\n')
    assert validated.endswith(b'\r\n\r\n')
    assert b'content-length: 0' in validated.lower()


def test_cache_revalidation_throttle_survives_local_flag_changes() -> None:
    # In these unit tests the absolute deadline is enough; no system clock changes
    cache = ClientSettingsLocalCache()
    key = client_settings_cache_key('clientsettings.roblox.com', '/settings/application/X', 'GET', {}, b'')
    assert key is not None
    original = _snapshot(when=cache_module.time.monotonic() - 40.0)
    cache.store(key, original)
    assert cache.should_fetch(key, 0)
    cache.note_fetch_attempt(key)
    assert not cache.should_fetch(key, 0)
    updated = _snapshot(when=original.checked_at)
    cache.update_render(key, original, updated)
    assert cache.snapshot(key, 0) is updated
    assert not cache.should_fetch(key, 0)
    cache.update_render(key, original, _snapshot())
    assert cache.snapshot(key, 0) is updated
    cache.note_not_modified(key, 0)
    assert not cache.should_fetch(key, 0)


def test_cache_bounds_entry_count_and_rejects_old_generations() -> None:
    cache = ClientSettingsLocalCache()
    keys: list[ClientSettingsKey] = []
    for i in range(CLIENT_SETTINGS_MAX_SNAPSHOTS + 5):
        key = client_settings_cache_key('clientsettings.roblox.com', f'/settings/application/{i}', 'GET', {}, b'')
        assert key is not None
        keys.append(key)
        cache.store(key, _snapshot(when=float(i)))
    assert cache.snapshot(keys[0], 0) is None
    assert cache.snapshot(keys[-1], 0) is not None
    assert cache.snapshot(keys[-1], 1) is None
    cache.clear()
    assert cache.snapshot(keys[-1], 0) is None
