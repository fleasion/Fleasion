"""Opt-in per-request CDN locations stay isolated and survive repeat requests."""

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

from fleasion.proxy.addons.texture_stripper import TextureStripper, is_synthetic_cdn_request
from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.upstream import UpstreamConnectResult
from fleasion.utils.certs import generate_ca, generate_host_cert, generate_multi_host_cert

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.config.types import ReplacementMaps
    from fleasion.proxy.addons.cache_scraper import CacheScraper

_ASSET_HOST = 'assetdelivery.roblox.com'
_CDN_HOST = 'fts.rbxcdn.com'
_SHARED_URL = f'https://{_CDN_HOST}/shared-original-hash?__token__=do-not-copy'


class _Config:
    replacements_generation = 0

    def __init__(self, local: dict[int, str]) -> None:
        self.local = local

    def get_all_replacements(self) -> ReplacementMaps:
        return {}, set(), {}, cast('dict[int | str, str]', self.local)


def _batch_location(entry: dict[str, object], version: str) -> str:
    if version == 'v1':
        return cast('str', entry['location'])
    entries = cast('list[dict[str, object]]', entry['locations'])
    return cast('str', entries[0]['location'])


class _Writer:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass

    def is_closing(self) -> bool:
        return False


@pytest.mark.parametrize('version', ['v1', 'v2'])
def test_identical_original_hash_has_isolated_persistent_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, version: str
) -> None:
    monkeypatch.setenv('FLEASION_ASSET_SYNTHETIC_CDN', '1')
    first, second = tmp_path / 'first.mesh', tmp_path / 'second.mesh'
    first.write_bytes(b'FIRST-MESH')
    second.write_bytes(b'SECOND-MESH')
    config = _Config({111: str(first), 222: str(second)})
    TextureStripper.reset_routes()
    try:
        stripper = TextureStripper(cast('ConfigManager', config))
        entries = [
            {'requestId': 'A', 'assetId': 111, 'assetType': 'Mesh'},
            {'requestId': 'B', 'assetId': 222, 'assetType': 'Mesh'},
        ]
        request = json.dumps(entries).encode()
        upstream_items: list[dict[str, object]] = []
        for request_id in ('A', 'B'):
            item: dict[str, object] = {'requestId': request_id}
            if version == 'v1':
                item['location'] = _SHARED_URL
            else:
                item['locations'] = [{'assetFormat': 'source', 'location': _SHARED_URL}]
            upstream_items.append(item)
        outbound, _ = cast(
            'tuple[bytes, bytes]',
            stripper.process_batch_request(
                request, {}, config.get_all_replacements(), f'collision-{version}'
            ),
        )
        upstream = json.dumps(upstream_items).encode()
        rewritten = stripper.process_batch_response(outbound, upstream, {}, f'collision-{version}')
        assert rewritten is not None
        assert _SHARED_URL.encode() not in rewritten
        assert b'do-not-copy' not in rewritten
        items = cast('list[dict[str, object]]', json.loads(rewritten))
        urls = [_batch_location(item, version) for item in items]
        assert urls[0] != urls[1]
        assert all(urlsplit(url).hostname == _CDN_HOST for url in urls)
        assert all(is_synthetic_cdn_request(_CDN_HOST, urlsplit(url).path) for url in urls)
        assert stripper.check_cdn_request(_CDN_HOST, '/shared-original-hash') is None
        for _repeat in range(3):
            for url, path in zip(urls, (first, second), strict=True):
                assert stripper.check_cdn_request(_CDN_HOST, urlsplit(url).path) == (
                    'local',
                    str(path),
                )
        TextureStripper.reset_routes()
        for url in urls:
            assert stripper.check_cdn_request(_CDN_HOST, urlsplit(url).path) is None
    finally:
        TextureStripper.reset_routes()


def test_sticky_mode_disabled_keeps_original_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv('FLEASION_ASSET_SYNTHETIC_CDN', raising=False)
    replacement = tmp_path / 'replacement.dat'
    replacement.write_bytes(b'new')
    config = _Config({111: str(replacement)})
    TextureStripper.reset_routes()
    try:
        stripper = TextureStripper(cast('ConfigManager', config))
        request = b'[{"requestId":"A","assetId":111,"assetType":"Image"}]'
        original = json.dumps([{'requestId': 'A', 'location': _SHARED_URL}]).encode()
        stripper.process_batch_request(request, {}, config.get_all_replacements(), 'disabled')
        assert stripper.process_batch_response(request, original, {}, 'disabled') is None
        assert stripper.check_cdn_request(_CDN_HOST, '/shared-original-hash') == (
            'local',
            str(replacement),
        )
        assert stripper.check_cdn_request(_CDN_HOST, '/shared-original-hash') is None
    finally:
        TextureStripper.reset_routes()


@pytest.mark.parametrize('version', ['v1', 'v2'])
@pytest.mark.parametrize('compression', ['identity', 'gzip', 'zstd'])
def test_synthetic_url_live_https_get_head_range_and_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, version: str, compression: str
) -> None:
    monkeypatch.setenv('FLEASION_ASSET_SYNTHETIC_CDN', '1')

    async def run() -> None:
        TextureStripper.reset_routes()
        replacement = tmp_path / 'replaced.audio'
        replacement.write_bytes(b'REPLACEMENT-AUDIO-BYTES')
        config = _Config({111: str(replacement)})
        stripper = TextureStripper(cast('ConfigManager', config))
        ca, key = generate_ca(tmp_path)
        certs = {
            host: generate_host_cert(host, ca, key, tmp_path) for host in (_ASSET_HOST, _CDN_HOST)
        }
        default = generate_multi_host_cert('synthetic', {_ASSET_HOST, _CDN_HOST}, ca, key, tmp_path)
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
            if host == _ASSET_HOST:
                entry: dict[str, object] = {'requestId': 'A', 'assetTypeId': 3}
                if version == 'v1':
                    entry['location'] = _SHARED_URL
                else:
                    entry['locations'] = [{'assetFormat': 'source', 'location': _SHARED_URL}]
                body = json.dumps([entry]).encode()
                if compression == 'gzip':
                    body = gzip.compress(body)
                elif compression == 'zstd':
                    body = zstandard.ZstdCompressor().compress(body)
                encoding = (
                    f'Content-Encoding: {compression}\r\n' if compression != 'identity' else ''
                )
            else:
                body = b'ORIGINAL-CDN-AUDIO'
                encoding = ''
            reader.feed_data(
                (
                    f'HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n'
                    f'{encoding}Connection: close\r\n\r\n'
                ).encode()
                + body
            )
            reader.feed_eof()
            writer = _Writer()
            upstream_writes.append((host, writer))
            return UpstreamConnectResult(
                reader=reader,
                writer=cast('asyncio.StreamWriter', writer),
                endpoint='stub',
                method='stub',
            )

        proxy.__dict__['_connect_upstream'] = connect
        await proxy.start()
        sockets = cast('list[socket.socket]', getattr(proxy.__dict__['_server'], 'sockets'))
        port = sockets[0].getsockname()[1]
        ctx = ssl.create_default_context(cafile=str(ca))

        async def exchange(
            host: str, method: str, path: str, body: bytes = b'', headers: str = ''
        ) -> bytes:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(f'CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n'.encode())
            await writer.drain()
            assert (await reader.readuntil(b'\r\n\r\n')).startswith(b'HTTP/1.1 200 ')
            await writer.start_tls(ctx, server_hostname=host)
            writer.write(
                f'{method} {path} HTTP/1.1\r\nHost: {host}\r\n'.encode()
                + headers.encode()
                + f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()
                + body
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), timeout=4)
            writer.close()
            await writer.wait_closed()
            return response

        try:
            body = b'[{"requestId":"A","assetId":111,"assetType":"Audio"}]'
            response = await exchange(
                _ASSET_HOST,
                'POST',
                f'/{version}/assets/batch',
                body,
                'Content-Type: application/json\r\n',
            )
            assert response.startswith(b'HTTP/1.1 200')
            assert b'content-encoding:' not in response.partition(b'\r\n\r\n')[0].lower()
            rewritten = cast(
                'list[dict[str, object]]', json.loads(response.partition(b'\r\n\r\n')[2])
            )
            url = _batch_location(rewritten[0], version)
            path = urlsplit(url).path
            assert path.startswith('/__fleasion__/asset/')
            assert len(upstream_writes) == 1
            for _ in range(3):
                fetched = await exchange(_CDN_HOST, 'GET', path)
                assert fetched.endswith(b'\r\n\r\nREPLACEMENT-AUDIO-BYTES')
            partial = await exchange(_CDN_HOST, 'GET', path, headers='Range: bytes=0-5\r\n')
            assert partial.startswith(b'HTTP/1.1 206 Partial Content')
            assert partial.endswith(b'\r\n\r\nREPLAC')
            head = await exchange(_CDN_HOST, 'HEAD', path)
            head_headers, marker, head_body = head.partition(b'\r\n\r\n')
            assert marker and not head_body
            assert b'Content-Length: 23' in head_headers
            ranged_head = await exchange(_CDN_HOST, 'HEAD', path, headers='Range: bytes=0-5\r\n')
            ranged_headers = ranged_head.partition(b'\r\n\r\n')[0]
            assert b'HTTP/1.1 200 OK' in ranged_headers
            assert b'Content-Range:' not in ranged_headers
            assert len(upstream_writes) == 1
            unsupported = await exchange(_CDN_HOST, 'POST', path, body=b'no-upstream')
            assert unsupported.startswith(b'HTTP/1.1 405 Method Not Allowed')
            assert len(upstream_writes) == 1
            TextureStripper.reset_routes()
            stale = await exchange(_CDN_HOST, 'GET', path)
            assert stale.startswith(b'HTTP/1.1 410 Gone')
            assert len(upstream_writes) == 1
            invalid = await exchange(_CDN_HOST, 'GET', '/__fleasion__/asset/invalid')
            assert invalid.startswith(b'HTTP/1.1 410 Gone')
            assert len(upstream_writes) == 1
        finally:
            await proxy.stop()
            TextureStripper.reset_routes()

    asyncio.run(run())


def test_animation_rig_detection_keeps_upstream_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv('FLEASION_ASSET_SYNTHETIC_CDN', '1')
    replacement = tmp_path / 'replacement.anim'
    replacement.write_bytes(b'FAKE-ANIMATION')
    config = _Config({111: str(replacement)})
    TextureStripper.reset_routes()
    try:
        stripper = TextureStripper(cast('ConfigManager', config))
        request = b'[{"requestId":"A","assetId":111,"assetType":"Animation","assetTypeId":24}]'
        original = json.dumps([{'requestId': 'A', 'location': _SHARED_URL}]).encode()
        stripper.process_batch_request(request, {}, config.get_all_replacements(), 'animation')
        assert stripper.process_batch_response(request, original, {}, 'animation') is None
        action = stripper.check_cdn_request(_CDN_HOST, '/shared-original-hash')
        assert action == ('anim_rig', (str(replacement), 'any'))
    finally:
        TextureStripper.reset_routes()


def test_multi_format_v2_response_stays_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv('FLEASION_ASSET_SYNTHETIC_CDN', '1')
    replacement = tmp_path / 'replacement.ktx2'
    replacement.write_bytes(b'FAKE-TEXTURE')
    config = _Config({111: str(replacement)})
    TextureStripper.reset_routes()
    try:
        stripper = TextureStripper(cast('ConfigManager', config))
        request = b'[{"requestId":"A","assetId":111,"assetType":"Image"}]'
        original = json.dumps(
            [
                {
                    'requestId': 'A',
                    'locations': [
                        {'assetFormat': 'source', 'location': _SHARED_URL},
                        {
                            'assetFormat': 'ktx2',
                            'location': f'https://{_CDN_HOST}/alternative-hash',
                        },
                    ],
                }
            ]
        ).encode()
        stripper.process_batch_request(request, {}, config.get_all_replacements(), 'multi')
        assert stripper.process_batch_response(request, original, {}, 'multi') is None
        assert stripper.check_cdn_request(_CDN_HOST, '/shared-original-hash') == (
            'local',
            str(replacement),
        )
        assert stripper.check_cdn_request(_CDN_HOST, '/alternative-hash') == (
            'local',
            str(replacement),
        )
    finally:
        TextureStripper.reset_routes()


def test_configuration_change_revokes_live_synthetic_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv('FLEASION_ASSET_SYNTHETIC_CDN', '1')
    replacement = tmp_path / 'replacement.mesh'
    replacement.write_bytes(b'FAKE-MESH')
    config = _Config({111: str(replacement)})
    TextureStripper.reset_routes()
    try:
        stripper = TextureStripper(cast('ConfigManager', config))
        request = b'[{"requestId":"A","assetId":111,"assetType":"Mesh"}]'
        original = json.dumps([{'requestId': 'A', 'location': _SHARED_URL}]).encode()
        stripper.process_batch_request(request, {}, config.get_all_replacements(), 'config')
        modified = stripper.process_batch_response(request, original, {}, 'config')
        assert modified is not None
        path = urlsplit(
            _batch_location(cast('list[dict[str, object]]', json.loads(modified))[0], 'v1')
        ).path
        assert stripper.check_cdn_request(_CDN_HOST, path) == ('local', str(replacement))
        config.replacements_generation = 1
        config.local = {}
        assert stripper.check_cdn_request(_CDN_HOST, path) is None
        assert stripper.check_cdn_request(_CDN_HOST, '/shared-original-hash') is None
    finally:
        TextureStripper.reset_routes()


def test_sticky_routes_bound_memory_and_fail_closed_when_evicted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleasion.proxy.addons.texture_stripper as module

    monkeypatch.setenv('FLEASION_ASSET_SYNTHETIC_CDN', '1')
    monkeypatch.setattr(module, '_SYNTHETIC_CDN_ROUTE_LIMIT', 2)
    replacement = tmp_path / 'replacement.audio'
    replacement.write_bytes(b'AUDIO')
    config = _Config({111: str(replacement)})
    TextureStripper.reset_routes()
    try:
        stripper = TextureStripper(cast('ConfigManager', config))
        request = b'[{"requestId":"A","assetId":111,"assetType":"Audio"}]'
        urls: list[str] = []
        for iteration in range(4):
            batch = f'limited-{iteration}'
            original = json.dumps([{'requestId': 'A', 'location': _SHARED_URL}]).encode()
            stripper.process_batch_request(request, {}, config.get_all_replacements(), batch)
            modified = stripper.process_batch_response(request, original, {}, batch)
            assert modified is not None
            urls.append(
                urlsplit(
                    _batch_location(cast('list[dict[str, object]]', json.loads(modified))[0], 'v1')
                ).path
            )
        assert len(set(urls)) == 4
        assert stripper.check_cdn_request(_CDN_HOST, urls[0]) is None
        assert stripper.check_cdn_request(_CDN_HOST, urls[1]) is None
        for path in urls[2:]:
            assert stripper.check_cdn_request(_CDN_HOST, path) == ('local', str(replacement))
        assert stripper.check_cdn_request(_CDN_HOST, '/shared-original-hash') is None
    finally:
        TextureStripper.reset_routes()


def test_synthetic_location_rejects_untrusted_or_invalid_original_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv('FLEASION_ASSET_SYNTHETIC_CDN', '1')
    replacement = tmp_path / 'replacement.png'
    replacement.write_bytes(b'PNG')
    config = _Config({111: str(replacement)})
    TextureStripper.reset_routes()
    try:
        stripper = TextureStripper(cast('ConfigManager', config))
        request = b'[{"requestId":"A","assetId":111,"assetType":"Image"}]'
        invalid_urls = (
            'https://other.example.com/not-cdn',
            'http://fts.rbxcdn.com/not-https',
            'https://fts.rbxcdn.com:abc/bad-port',
            'https://user:pass@fts.rbxcdn.com/bad-userinfo',
            'https://fts.rbxcdn.com/not-allowed#fragment',
        )
        for iteration, location in enumerate(invalid_urls):
            batch = f'invalid-{iteration}'
            original = json.dumps([{'requestId': 'A', 'location': location}]).encode()
            stripper.process_batch_request(request, {}, config.get_all_replacements(), batch)
            assert stripper.process_batch_response(request, original, {}, batch) is None
    finally:
        TextureStripper.reset_routes()


def test_sticky_cdn_redirect_route_survives_repeated_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('FLEASION_ASSET_SYNTHETIC_CDN', '1')
    destination = 'https://user-content.example.net/replacement.bin'

    class _CdnConfig:
        @staticmethod
        def get_all_replacements() -> ReplacementMaps:
            return {}, set(), {111: destination}, {}

    TextureStripper.reset_routes()
    try:
        config = _CdnConfig()
        stripper = TextureStripper(cast('ConfigManager', config))
        request = b'[{"requestId":"A","assetId":111,"assetType":"Audio"}]'
        upstream = json.dumps([{'requestId': 'A', 'location': _SHARED_URL}]).encode()
        stripper.process_batch_request(request, {}, config.get_all_replacements(), 'redirect')
        modified = stripper.process_batch_response(request, upstream, {}, 'redirect')
        assert modified is not None
        item = cast('list[dict[str, object]]', json.loads(modified))[0]
        path = urlsplit(_batch_location(item, 'v1')).path
        assert path.startswith('/__fleasion__/asset/')
        for _ in range(4):
            assert stripper.check_cdn_request(_CDN_HOST, path) == ('cdn', destination)
        TextureStripper.reset_routes()
        assert stripper.check_cdn_request(_CDN_HOST, path) is None
    finally:
        TextureStripper.reset_routes()


def test_ambiguous_v1_v2_location_payload_is_not_rewritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv('FLEASION_ASSET_SYNTHETIC_CDN', '1')
    replacement = tmp_path / 'replacement.mesh'
    replacement.write_bytes(b'MESH')
    config = _Config({111: str(replacement)})
    TextureStripper.reset_routes()
    try:
        stripper = TextureStripper(cast('ConfigManager', config))
        request = b'[{"requestId":"A","assetId":111,"assetType":"Mesh"}]'
        original = json.dumps(
            [
                {
                    'requestId': 'A',
                    'location': _SHARED_URL,
                    'locations': [{'assetFormat': 'source', 'location': _SHARED_URL}],
                }
            ]
        ).encode()
        stripper.process_batch_request(request, {}, config.get_all_replacements(), 'ambiguous')
        assert stripper.process_batch_response(request, original, {}, 'ambiguous') is None
        assert stripper.check_cdn_request(_CDN_HOST, '/shared-original-hash') == (
            'local',
            str(replacement),
        )
    finally:
        TextureStripper.reset_routes()
