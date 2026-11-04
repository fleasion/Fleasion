"""V1/V2 asset-delivery response and compressed-request compatibility."""

from __future__ import annotations

import asyncio
import gzip
import json
import socket
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import pytest
import zstandard

from fleasion.proxy.addons.cache_scraper import CacheScraper
from fleasion.proxy.addons.texture_stripper import TextureStripper
from fleasion.proxy.asset_delivery import (
    batch_response_locations,
    direct_asset_id,
    is_asset_batch_path,
    is_direct_asset_path,
)
from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.upstream import UpstreamConnectResult
from fleasion.utils.certs import (
    generate_ca,
    generate_host_cert,
    generate_multi_host_cert,
)

if TYPE_CHECKING:
    from fleasion.cache.cache_manager import CacheManager
    from fleasion.config.manager import ConfigManager
    from fleasion.config.types import ReplacementMaps

_HOST = 'assetdelivery.roblox.com'
_CDN = 'fts.rbxcdn.com'
_SOURCE = 5447528495
_REPLACEMENT_PATH = '/downloaded-image-hash'


class _Config:
    replacements_generation = 0

    def __init__(self, local: str | None) -> None:
        self.local = local

    def get_all_replacements(self) -> ReplacementMaps:
        return {}, set(), {}, {_SOURCE: self.local} if self.local is not None else {}


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass

    def is_closing(self) -> bool:
        return False


def test_asset_endpoint_detection_is_exact() -> None:
    for ver in ('v1', 'v2'):
        assert is_asset_batch_path(f'/{ver}/assets/batch?client=studio')
        assert is_direct_asset_path(f'/{ver}/asset/?id=123')
        assert is_direct_asset_path(f'/{ver}/assetId/123')
        assert direct_asset_id(f'/{ver}/assetId/123') == '123'
        assert direct_asset_id(f'/{ver}/asset/?id=123') == '123'
        assert direct_asset_id(f'/{ver}/asset/?id=123&id=456') is None
        assert direct_asset_id(f'/{ver}/assetId/123?id=456') is None
        assert not is_direct_asset_path(f'/{ver}/assetId/123/version/1')
        assert not is_asset_batch_path(f'/{ver}/assets/batch/subpath')
        assert not is_direct_asset_path(f'/{ver}/assets/batch?id=123')
    assert not is_asset_batch_path('/v3/assets/batch')


def test_asset_v2_locations_preserve_format_entries() -> None:
    v1 = {'location': 'https://fts.rbxcdn.com/h1?t=hidden'}
    v2 = {
        'locations': [
            {'assetFormat': 'source', 'location': 'https://fts.rbxcdn.com/h1?t=hidden'},
            {'assetFormat': 'alternative', 'location': 'https://fts.rbxcdn.com/h2?t=hidden'},
            {'assetFormat': 'source', 'location': 'https://fts.rbxcdn.com/h1?t=changed'},
        ]
    }
    assert len(batch_response_locations(v1)) == 1
    assert len(batch_response_locations(v2)) == 3
    assert batch_response_locations({'locations': [42, {'location': False}]}) == ()
    assert batch_response_locations({'locations': 'not-an-array'}) == ()


@pytest.mark.parametrize('version', ('v1', 'v2'))
def test_batch_response_registers_all_locations(version: str, tmp_path: Path) -> None:
    replacement = tmp_path / 'replacement.png'
    replacement.write_bytes(b'REPLACEMENT-IMAGE')
    config = _Config(str(replacement))
    TextureStripper.reset_routes()
    try:
        stripper = TextureStripper(cast('ConfigManager', config))
        request = json.dumps([{'assetId': _SOURCE, 'assetType': 'Image', 'requestId': 'one'}]).encode()
        response_entry: dict[str, object] = {'requestId': 'one'}
        locations = [
            'https://fts.rbxcdn.com/image-content-hash-a?Expires=10&__token__=hidden',
            'https://fts.rbxcdn.com/image-content-hash-b?Expires=10&__token__=hidden',
        ]
        if version == 'v1':
            response_entry['location'] = locations[0]
        else:
            response_entry['locations'] = [
                {'assetFormat': 'source', 'location': locations[0]},
                {'assetFormat': 'alternative', 'location': locations[1]},
            ]
        outbound, _ = cast('tuple[bytes, bytes]', stripper.process_batch_request(
            request, {}, config.get_all_replacements(), version
        ))
        stripper.process_batch_response(outbound, json.dumps([response_entry]).encode(), {}, version)
        for i, location in enumerate(locations[: 1 if version == 'v1' else 2]):
            p = urlsplit(location)
            assert stripper.check_cdn_request(p.hostname or '', p.path + '?new-signature=1') == (
                'local', str(replacement)
            ), i
    finally:
        TextureStripper.reset_routes()


def test_scraper_tracks_distinct_ids_sharing_one_cdn_hash() -> None:
    scraper = CacheScraper(cast('CacheManager', SimpleNamespace()))
    try:
        scraper.enabled = True
        request = json.dumps([
            {'assetId': 111, 'assetType': 'Image', 'requestId': 'a'},
            {'assetId': 222, 'assetType': 'Image', 'requestId': 'b'},
            {'assetId': 111, 'assetType': 'Image', 'requestId': 'c'},
        ]).encode()
        location = 'https://fts.rbxcdn.com/shared-hash?secret=hidden'
        response = json.dumps([
            {'assetTypeId': 1, 'locations': [{'assetFormat': 'source', 'location': location}]}
            for _ in range(3)
        ]).encode()
        scraper.process_batch_response(request, response)
        tracked = cast('dict[str, list[int]]', getattr(scraper, '_url_to_asset'))
        assert tracked['https://fts.rbxcdn.com/shared-hash'] == [111, 222]
        assert set(scraper.cache_logs) == {111, 222}
    finally:
        getattr(scraper, '_executor').shutdown(wait=True, cancel_futures=True)


def test_scraper_reads_v2_locations_without_http_fetch(tmp_path: Path) -> None:
    scraper = CacheScraper(cast('CacheManager', SimpleNamespace()))
    try:
        scraper.enabled = True
        request = json.dumps([{'assetId': _SOURCE, 'assetType': 'Image', 'requestId': 'one'}]).encode()
        response = json.dumps([{
            'requestId': 'one', 'assetTypeId': 1,
            'locations': [
                {'assetFormat': 'source', 'location': 'https://fts.rbxcdn.com/a?secret=x'},
                {'assetFormat': 'ktx2', 'location': 'https://fts.rbxcdn.com/b?secret=y'},
            ],
        }]).encode()
        scraper.process_batch_response(request, response)
        assert scraper.cache_logs[_SOURCE]['assetTypeId'] == 1
        tracked = cast('dict[str, list[int]]', getattr(scraper, '_url_to_asset'))
        assert tracked['https://fts.rbxcdn.com/a'] == [_SOURCE]
        assert tracked['https://fts.rbxcdn.com/b'] == [_SOURCE]
    finally:
        getattr(scraper, '_executor').shutdown(wait=True, cancel_futures=True)


@pytest.mark.parametrize('version', ('v1', 'v2'))
@pytest.mark.parametrize('kind', ('v2_local', 'gzip_passthrough', 'zstd_passthrough', 'br_passthrough', 'dcz_passthrough', 'gzip_modified', 'zstd_modified'))
def test_proxy_batch_transport_compat(version: str, kind: str, tmp_path: Path) -> None:
    async def run() -> None:
        TextureStripper.reset_routes()
        local = tmp_path / 'replacement.png'
        local.write_bytes(b'LOCAL-IMAGE-REPLACEMENT')
        config = _Config(str(local) if kind == 'v2_local' else None)
        if kind in {'gzip_modified', 'zstd_modified'}:
            config.get_all_replacements = lambda: ({_SOURCE: 123456}, set(), {}, {})  # type: ignore[method-assign]
        stripper = TextureStripper(cast('ConfigManager', config))
        ca, key = generate_ca(tmp_path)
        certs = {h: generate_host_cert(h, ca, key, tmp_path) for h in (_HOST, _CDN)}
        default = generate_multi_host_cert('asset-batch', {_HOST, _CDN}, ca, key, tmp_path)
        proxy = FleasionProxy(
            texture_stripper=stripper,
            cache_scraper=cast('CacheScraper', SimpleNamespace(enabled=False)),
            host_certs=certs,
            default_cert=default,
            upstream_endpoints={},
            explicit_proxy=True,
            port=0,
        )
        upstream_writes: list[tuple[str, _Writer]] = []

        async def connect(host: str, **_kwargs: object) -> UpstreamConnectResult:
            reader = asyncio.StreamReader()
            if host == _HOST:
                entry: dict[str, object] = {'requestId': 'one', 'assetTypeId': 1}
                location = f'https://fts.rbxcdn.com{_REPLACEMENT_PATH}?signed=hidden'
                if version == 'v2':
                    entry['locations'] = [{'assetFormat': 'source', 'location': location}]
                else:
                    entry['location'] = location
                body = json.dumps([entry]).encode()
            else:
                body = b'ROBLOX-ORIGINAL-IMAGE'
            reader.feed_data(
                f'HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n'
                'Connection: close\r\n\r\n'.encode() + body
            )
            reader.feed_eof()
            writer = _Writer()
            upstream_writes.append((host, writer))
            return UpstreamConnectResult(
                reader=reader, writer=cast('asyncio.StreamWriter', writer),
                endpoint='test', method='fake-upstream',
            )

        proxy.__dict__['_connect_upstream'] = connect
        await proxy.start()
        ctx = ssl.create_default_context(cafile=str(ca))
        sockets = cast('list[socket.socket]', getattr(proxy.__dict__['_server'], 'sockets'))
        port = sockets[0].getsockname()[1]

        async def fetch(host: str, request: bytes) -> bytes:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(f'CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n'.encode())
            await writer.drain()
            assert (await reader.readuntil(b'\r\n\r\n')).startswith(b'HTTP/1.1 200 ')
            await writer.start_tls(ctx, server_hostname=host)
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), timeout=4)
            writer.close()
            await writer.wait_closed()
            return response

        body = json.dumps([{'assetId': _SOURCE, 'assetType': 'Image', 'requestId': 'one'}]).encode()
        if kind in {'br_passthrough', 'dcz_passthrough'}:
            wire_body, ce = b'unknown-future-compression', kind.split('_')[0].encode('ascii')
        elif kind in {'gzip_passthrough', 'gzip_modified'}:
            wire_body, ce = gzip.compress(body), b'gzip'
        elif kind in {'zstd_passthrough', 'zstd_modified'}:
            wire_body, ce = zstandard.ZstdCompressor().compress(body), b'zstd'
        else:
            wire_body, ce = body, b''
        path = f'/{version}/assets/batch'
        request = (
            f'POST {path} HTTP/1.1\r\nHost: {_HOST}\r\n'.encode()
            + (b'Content-Encoding: ' + ce + b'\r\n' if ce else b'')
            + f'Content-Length: {len(wire_body)}\r\nConnection: close\r\n\r\n'.encode()
            + wire_body
        )
        try:
            response = await fetch(_HOST, request)
            assert response.startswith(b'HTTP/1.1 200 ')
            forwarded = bytes(upstream_writes[0][1].buffer)
            if kind in {'gzip_modified', 'zstd_modified'}:
                assert b'content-encoding:' not in forwarded.lower()
                assert b'"assetId":123456' in forwarded
                assert b'"assetId":5447528495' not in forwarded
            else:
                assert forwarded == request
            if kind == 'v2_local':
                cdn = await fetch(
                    _CDN,
                    f'GET {_REPLACEMENT_PATH}?other-sig=1 HTTP/1.1\r\nHost: {_CDN}\r\n'
                    'Connection: close\r\n\r\n'.encode(),
                )
                assert cdn.endswith(b'\r\n\r\nLOCAL-IMAGE-REPLACEMENT')
                assert len(upstream_writes) == 1
        finally:
            await proxy.stop()
            TextureStripper.reset_routes()

    asyncio.run(run())
