"""Feature parity across already-decrypted HTTP/1.1 and HTTP/2 transports.

All traffic stays on loopback with synthetic payloads and locally generated certs.
No Roblox client, game account, or process secrets are involved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, StreamEnded

from fleasion.proxy.server import FleasionProxy, ProxyFlow
from fleasion.proxy.tls_backends import (
    AuthenticatedSession,
    dispatch_authenticated_session,
)
from fleasion.proxy.types import AutoReplaceRule, ModuleInterceptor
from fleasion.proxy.upstream import UpstreamConnectResult
from fleasion.utils.certs import generate_ca, generate_multi_host_cert

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.custom_fflags import CustomFFlagModifier
    from fleasion.proxy.addons.texture_stripper import TextureStripper


class AddonCalls:
    def __init__(self) -> None:
        self.upstream: list[tuple[bytes, bytes, bytes]] = []
        self.cdn: list[tuple[str, str, bytes, str]] = []
        self.batches: list[tuple[bytes, bytes, dict[bytes, bytes], str]] = []
        self.batch_cache: list[tuple[bytes, bytes]] = []
        self.direct_assets: list[tuple[str, int, str, bytes, str]] = []
        self.fflag_success: list[tuple[tuple[str, str], ...]] = []
        self.gamejoin_stages: list[str] = []
        self.solid_models: list[tuple[bytes, str, str, bool]] = []


def _empty_replacements() -> tuple[
    dict[object, object], set[object], dict[object, object], dict[object, object]
]:
    return {}, set(), {}, {}


def _fake_addons(
    calls: AddonCalls,
    *,
    local_file: Path | None = None,
    solid_file: Path | None = None,
    scraper: bool = False,
) -> tuple[TextureStripper, CacheScraper]:
    def check_cdn_request(_host: str, _path: str) -> tuple[str, str] | None:
        if local_file is not None:
            return 'local', str(local_file)
        if solid_file is not None:
            return 'solid', str(solid_file)
        return None

    def process_batch_request(
        _body: bytes, _headers: dict[bytes, bytes], _replacements: object, _batch_id: str
    ) -> tuple[bytes, bytes]:
        return b'[{"id":42,"type":"Mesh"}]', b'[{"id":42,"type":"Mesh"}]'

    def process_batch_response(
        body: bytes, response: bytes, headers: dict[bytes, bytes], batch_id: str
    ) -> None:
        calls.batches.append((body, response, headers, batch_id))

    def scrape_cdn(url: str, path: str, body: bytes, content_type: str) -> None:
        calls.cdn.append((url, path, body, content_type))

    def scrape_batch(request: bytes, response: bytes) -> None:
        calls.batch_cache.append((request, response))

    def scrape_direct(
        path: str, status: int, location: str, body: bytes, content_type: str
    ) -> None:
        calls.direct_assets.append((path, status, location, body, content_type))

    def process_solid_model(body: bytes, replacement_path: str, url: str, prefer_v3: bool) -> bytes:
        calls.solid_models.append((body, replacement_path, url, prefer_v3))
        return b'controlled-solid-model-output'

    stripper = cast(
        'TextureStripper',
        SimpleNamespace(
            config_manager=SimpleNamespace(get_all_replacements=_empty_replacements),
            check_cdn_request=check_cdn_request,
            has_pending=lambda: False,
            process_batch_request=process_batch_request,
            process_batch_response=process_batch_response,
            process_solidmodel_response=process_solid_model,
        ),
    )
    cache = cast(
        'CacheScraper',
        SimpleNamespace(
            enabled=scraper,
            process_cdn_response=scrape_cdn,
            process_batch_response=scrape_batch,
            process_direct_asset_response=scrape_direct,
        ),
    )
    return stripper, cache


def _proxy(
    tmp_path: Path,
    host: str,
    calls: AddonCalls,
    *,
    local_file: Path | None = None,
    solid_file: Path | None = None,
    scraper: bool = False,
    rules: list[AutoReplaceRule] | None = None,
    flag_modifier: CustomFFlagModifier | None = None,
    log_all: bool = True,
) -> FleasionProxy:
    ca, key = generate_ca(tmp_path)
    default_cert = generate_multi_host_cert('parity', {host}, ca, key, tmp_path)
    stripper, cache = _fake_addons(
        calls, local_file=local_file, solid_file=solid_file, scraper=scraper
    )
    return FleasionProxy(
        texture_stripper=stripper,
        cache_scraper=cache,
        host_certs={},
        default_cert=default_cert,
        intercept_all_hosts=log_all,
        intercept_hosts={host},
        auto_replace_rules=rules,
        custom_fflag_modifier=flag_modifier,
    )


def _http1_message(first: bytes, host: str, body: bytes, *, response: bool = False) -> bytes:
    common = (
        b'Content-Type: application/octet-stream\r\n'
        + b'Content-Length: '
        + str(len(body)).encode()
        + b'\r\nConnection: close\r\n'
    )
    return (
        first
        + b'\r\n'
        + (b'' if response else f'Host: {host}\r\n'.encode())
        + common
        + b'\r\n'
        + body
    )


async def _exchange(
    proxy: FleasionProxy,
    calls: AddonCalls,
    *,
    protocol: str,
    host: str,
    path: str,
    body: bytes = b'',
    upstream_body: bytes = b'upstream-original',
    method: str = 'POST',
) -> tuple[bytes, list[tuple[bytes, bytes, bytes]]]:
    async def upstream_http1(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = await reader.readuntil(b'\r\n\r\n')
            first = data.split(b'\r\n', 1)[0]
            header_lines = data.lower().split(b'\r\n')
            length = next(
                (
                    int(line.split(b':', 1)[1])
                    for line in header_lines
                    if line.startswith(b'content-length:')
                ),
                0,
            )
            sent = await reader.readexactly(length)
            calls.upstream.append((first, data, sent))
            writer.write(_http1_message(b'HTTP/1.1 200 OK', host, upstream_body, response=True))
            await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def upstream_h2(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = H2Connection(H2Configuration(client_side=False))
        conn.initiate_connection()
        writer.write(conn.data_to_send())
        await writer.drain()
        pending: dict[int, tuple[bytes, bytearray, bytes]] = {}
        try:
            while data := await reader.read(65536):
                for event in conn.receive_data(data):
                    if isinstance(event, RequestReceived):
                        headers = dict(event.headers)
                        first = headers[b':method'] + b' ' + headers[b':path'] + b' HTTP/2'
                        serialized_headers = b'\r\n'.join(
                            key + b': ' + value
                            for key, value in headers.items()
                            if not key.startswith(b':')
                        )
                        pending[event.stream_id] = (first, bytearray(), serialized_headers)
                    elif isinstance(event, DataReceived):
                        pending[event.stream_id][1].extend(event.data)
                        conn.acknowledge_received_data(
                            event.flow_controlled_length, event.stream_id
                        )
                    elif isinstance(event, StreamEnded):
                        first, sent, serialized_headers = pending.pop(event.stream_id)
                        calls.upstream.append((first, serialized_headers, bytes(sent)))
                        conn.send_headers(
                            event.stream_id,
                            [(b':status', b'200'), (b'content-type', b'application/octet-stream')],
                        )
                        if upstream_body:
                            conn.send_data(event.stream_id, upstream_body, end_stream=True)
                        else:
                            conn.end_stream(event.stream_id)
                writer.write(conn.data_to_send())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    upstream_server = await asyncio.start_server(
        upstream_h2 if protocol == 'h2' else upstream_http1, '127.0.0.1', 0
    )
    upstream_port = upstream_server.sockets[0].getsockname()[1]
    assert isinstance(upstream_port, int)

    async def bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        up_reader, up_writer = await asyncio.open_connection('127.0.0.1', upstream_port)
        try:
            await dispatch_authenticated_session(
                proxy,
                AuthenticatedSession(
                    hostname=host,
                    alpn=protocol,
                    client_reader=reader,
                    client_writer=writer,
                    upstream=UpstreamConnectResult(
                        reader=up_reader,
                        writer=up_writer,
                        method='local_parity_test',
                        endpoint='127.0.0.1',
                    ),
                    server_authenticated=True,
                ),
                expected_hostname=host,
            )
        finally:
            writer.close()
            up_writer.close()
            await writer.wait_closed()
            await up_writer.wait_closed()

    bridge_server = await asyncio.start_server(bridge, '127.0.0.1', 0)
    bridge_port = bridge_server.sockets[0].getsockname()[1]
    assert isinstance(bridge_port, int)
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', bridge_port)
        if protocol == 'http/1.1':
            writer.write(_http1_message(f'{method} {path} HTTP/1.1'.encode(), host, body))
            await writer.drain()
            result = await asyncio.wait_for(reader.read(), timeout=5)
            assert result.startswith(b'HTTP/1.1 ')
            response = result.partition(b'\r\n\r\n')[2]
        else:
            conn = H2Connection()
            conn.initiate_connection()
            conn.send_headers(
                1,
                [
                    (b':method', method.encode()),
                    (b':scheme', b'https'),
                    (b':authority', host.encode()),
                    (b':path', path.encode()),
                ],
                end_stream=not body,
            )
            if body:
                conn.send_data(1, body, end_stream=True)
            writer.write(conn.data_to_send())
            await writer.drain()
            response_bytes = bytearray()
            done = False
            while not done:
                packet = await asyncio.wait_for(reader.read(65536), timeout=5)
                assert packet, 'HTTP/2 response did not finish'
                for event in conn.receive_data(packet):
                    if isinstance(event, DataReceived):
                        response_bytes.extend(event.data)
                        conn.acknowledge_received_data(
                            event.flow_controlled_length, event.stream_id
                        )
                    elif isinstance(event, StreamEnded) and event.stream_id == 1:
                        done = True
                writer.write(conn.data_to_send())
                await writer.drain()
            response = bytes(response_bytes)
        writer.close()
        await writer.wait_closed()
        return response, calls.upstream
    finally:
        bridge_server.close()
        upstream_server.close()
        await bridge_server.wait_closed()
        await upstream_server.wait_closed()
        await proxy.stop()


@pytest.mark.parametrize('protocol', ['http/1.1', 'h2'])
def test_auto_replace_and_proxy_request_log_parity(tmp_path: Path, protocol: str) -> None:
    async def run() -> None:
        calls = AddonCalls()
        rules: list[AutoReplaceRule] = [
            {'direction': 'request', 'match': 'original', 'replacement': 'modified'},
            {'direction': 'response', 'match': 'upstream', 'replacement': 'client'},
        ]
        proxy = _proxy(tmp_path, 'apis.roblox.com', calls, rules=rules)
        response, upstream = await _exchange(
            proxy,
            calls,
            protocol=protocol,
            host='apis.roblox.com',
            path='/example',
            body=b'original',
        )
        assert response == b'client-original'
        assert len(upstream) == 1 and upstream[0][2] == b'modified'
        logged = proxy.get_request_log()
        assert len(logged) == 1
        assert logged[0]['host'] == 'apis.roblox.com'
        assert logged[0]['path'] == '/example'
        assert logged[0]['status'] == 200
        assert logged[0]['intercepted'] is True

    asyncio.run(run())


@pytest.mark.parametrize('protocol', ['http/1.1', 'h2'])
def test_cdn_local_mesh_replacement_and_scraping_parity(tmp_path: Path, protocol: str) -> None:
    async def run() -> None:
        payload = b'controlled-mesh-replacement'
        file = tmp_path / 'replacement.mesh'
        file.write_bytes(payload)
        calls = AddonCalls()
        host = 'fts.rbxcdn.com'
        proxy = _proxy(tmp_path, host, calls, local_file=file, scraper=True)
        response, upstream = await _exchange(
            proxy,
            calls,
            protocol=protocol,
            host=host,
            path='/synthetic-mesh',
            method='GET',
        )
        assert response == payload
        assert upstream == []
        assert len(calls.cdn) == 1
        assert calls.cdn[0][0] == f'https://{host}/synthetic-mesh'
        assert calls.cdn[0][2] == payload

    asyncio.run(run())


@pytest.mark.parametrize('protocol', ['http/1.1', 'h2'])
def test_batch_replacement_and_cache_scraper_parity(tmp_path: Path, protocol: str) -> None:
    async def run() -> None:
        calls = AddonCalls()
        host = 'assetdelivery.roblox.com'
        proxy = _proxy(tmp_path, host, calls, scraper=True)
        response, upstream = await _exchange(
            proxy,
            calls,
            protocol=protocol,
            host=host,
            path='/v1/assets/batch',
            body=b'[{"id":42,"type":"Image"}]',
            upstream_body=b'[{"location":"https://example.invalid/mesh"}]',
        )
        assert response == b'[{"location":"https://example.invalid/mesh"}]'
        assert upstream[0][2] == b'[{"id":42,"type":"Mesh"}]'
        assert len(calls.batches) == 1
        assert calls.batches[0][0] == upstream[0][2]
        assert len(calls.batch_cache) == 1
        assert calls.batch_cache[0][0] == upstream[0][2]

    asyncio.run(run())


@pytest.mark.parametrize('protocol', ['http/1.1', 'h2'])
def test_gamejoin_request_and_response_interceptor_parity(tmp_path: Path, protocol: str) -> None:
    async def run() -> None:
        calls = AddonCalls()
        host = 'gamejoin.roblox.com'
        proxy = _proxy(tmp_path, host, calls)

        def on_request(flow: ProxyFlow) -> None:
            calls.gamejoin_stages.append('request')
            flow.request.raw_content = b'local-join-modified'

        def on_response(flow: ProxyFlow) -> None:
            calls.gamejoin_stages.append('response')
            assert flow.response is not None
            flow.response.content = b'local-join-response-modified'

        proxy.set_module_interceptors(
            [cast('ModuleInterceptor', SimpleNamespace(request=on_request, response=on_response))]
        )
        response, upstream = await _exchange(
            proxy,
            calls,
            protocol=protocol,
            host=host,
            path='/v1/join-game',
            body=b'local-join-original',
        )
        assert len(upstream) == 1
        assert upstream[0][2] == b'local-join-modified'
        assert response == b'local-join-response-modified'
        assert calls.gamejoin_stages == ['request', 'response']

    asyncio.run(run())


@pytest.mark.parametrize('protocol', ['http/1.1', 'h2'])
def test_custom_flag_response_delivery_parity(tmp_path: Path, protocol: str) -> None:
    async def run() -> None:
        calls = AddonCalls()
        signature = (('DFFlagTestFixture', 'True'),)

        def modify(path: str, body: bytes) -> tuple[bytes, tuple[tuple[str, str], ...]]:
            assert '/settings/application/' in path
            assert body == b'{"applicationSettings":{}}'
            return b'{"applicationSettings":{"DFFlagTestFixture":"True"}}', signature

        def acknowledge(
            delivered_signature: tuple[tuple[str, str], ...], *, generation: int | None = None
        ) -> None:
            assert generation == 17
            calls.fflag_success.append(delivered_signature)

        def handles_settings_path(path: str) -> bool:
            return '/settings/application/' in path

        modifier = cast(
            'CustomFFlagModifier',
            SimpleNamespace(
                handles_path=handles_settings_path,
                delivery_generation=lambda: 17,
                is_enabled=lambda: True,
                requires_fresh_response=lambda: False,
                modify_response_with_delivery=modify,
                note_response_success=acknowledge,
            ),
        )
        host = 'clientsettingscdn.roblox.com'
        proxy = _proxy(tmp_path, host, calls, flag_modifier=modifier)
        response, upstream = await _exchange(
            proxy,
            calls,
            protocol=protocol,
            host=host,
            path='/v2/settings/application/PCDesktopClient',
            upstream_body=b'{"applicationSettings":{}}',
            method='GET',
        )
        assert response == b'{"applicationSettings":{"DFFlagTestFixture":"True"}}'
        assert len(upstream) == 1
        assert calls.fflag_success == [signature]

    asyncio.run(run())


@pytest.mark.parametrize('protocol', ['http/1.1', 'h2'])
def test_direct_asset_scraper_parity(tmp_path: Path, protocol: str) -> None:
    async def run() -> None:
        calls = AddonCalls()
        host = 'assetdelivery.roblox.com'
        proxy = _proxy(tmp_path, host, calls, scraper=True)
        response, upstream = await _exchange(
            proxy,
            calls,
            protocol=protocol,
            host=host,
            path='/v1/asset?id=400',
            method='GET',
            upstream_body=b'local-mesh-asset',
        )
        assert response == b'local-mesh-asset'
        assert len(upstream) == 1
        assert len(calls.direct_assets) == 1
        assert calls.direct_assets[0][:2] == ('/v1/asset?id=400', 200)
        assert calls.direct_assets[0][3] == b'local-mesh-asset'

    asyncio.run(run())


@pytest.mark.parametrize('protocol', ['http/1.1', 'h2'])
def test_query_and_request_header_replacement_parity(tmp_path: Path, protocol: str) -> None:
    async def run() -> None:
        calls = AddonCalls()
        rules: list[AutoReplaceRule] = [
            {'type': 'query_param', 'direction': 'request', 'match': 'id', 'replacement': '200'},
            {
                'type': 'header',
                'direction': 'request',
                'match': 'x-fixture-header',
                'replacement': 'new-value',
            },
        ]
        proxy = _proxy(tmp_path, 'apis.roblox.com', calls, rules=rules)
        response, upstream = await _exchange(
            proxy,
            calls,
            protocol=protocol,
            host='apis.roblox.com',
            path='/resource?id=100',
            method='GET',
        )
        assert response == b'upstream-original'
        assert len(upstream) == 1
        assert b'/resource?id=200' in upstream[0][0]
        assert b'x-fixture-header: new-value' in upstream[0][1].lower()
        assert proxy.get_request_log()[0]['path'] == '/resource?id=200'

    asyncio.run(run())


@pytest.mark.parametrize('protocol', ['http/1.1', 'h2'])
def test_solid_model_transformation_callback_parity(tmp_path: Path, protocol: str) -> None:
    async def run() -> None:
        calls = AddonCalls()
        host = 'contentdelivery.roblox.com'
        replacement = tmp_path / 'dummy-solid.rbxm'
        replacement.write_bytes(b'controlled-stand-in-not-an-actual-rbxm')
        proxy = _proxy(tmp_path, host, calls, solid_file=replacement, scraper=True)
        response, upstream = await _exchange(
            proxy,
            calls,
            protocol=protocol,
            host=host,
            path='/synthetic-solid-model',
            method='GET',
            upstream_body=b'original-solid-bytes',
        )
        assert len(upstream) == 1
        assert response == b'controlled-solid-model-output'
        assert calls.solid_models == [
            (
                b'original-solid-bytes',
                str(replacement),
                f'https://{host}/synthetic-solid-model',
                False,
            )
        ]
        assert len(calls.cdn) == 1
        assert calls.cdn[0][2] == b'controlled-solid-model-output'

    asyncio.run(run())


@pytest.mark.parametrize('protocol', ['http/1.1', 'h2'])
def test_feature_replacement_with_proxy_tab_logging_disabled(tmp_path: Path, protocol: str) -> None:
    async def run() -> None:
        calls = AddonCalls()
        proxy = _proxy(
            tmp_path,
            'apis.roblox.com',
            calls,
            log_all=False,
            rules=[{'direction': 'response', 'match': 'upstream', 'replacement': 'replaced'}],
        )
        response, upstream = await _exchange(
            proxy,
            calls,
            protocol=protocol,
            host='apis.roblox.com',
            path='/synthetic-feature',
            method='GET',
        )
        assert response == b'replaced-original'
        assert len(upstream) == 1
        assert proxy.get_request_log() == []

    asyncio.run(run())


@pytest.mark.parametrize('protocol', ['http/1.1', 'h2'])
def test_gamejoin_module_can_drop_request_without_upstream_access(
    tmp_path: Path, protocol: str
) -> None:
    async def run() -> None:
        calls = AddonCalls()
        host = 'gamejoin.roblox.com'
        proxy = _proxy(tmp_path, host, calls)

        def on_request(flow: ProxyFlow) -> None:
            calls.gamejoin_stages.append('request')
            flow.drop_request = True
            flow.drop_status_code = 200
            flow.drop_body = b'local-blocked-gamejoin'

        def on_response(_flow: ProxyFlow) -> None:
            calls.gamejoin_stages.append('response')

        proxy.set_module_interceptors(
            [cast('ModuleInterceptor', SimpleNamespace(request=on_request, response=on_response))]
        )
        response, upstream = await _exchange(
            proxy,
            calls,
            protocol=protocol,
            host=host,
            path='/v1/join-game',
            body=b'local-gamejoin-request',
        )
        assert response == b'local-blocked-gamejoin'
        assert upstream == []
        assert calls.gamejoin_stages == ['request']

    asyncio.run(run())
