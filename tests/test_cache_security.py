from __future__ import annotations

import gzip
import struct
from typing import Any, cast

import pytest
import requests

from fleasion.cache import roblox_document
from fleasion.cache.roblox_document import RBXM_MAGIC, decompress_if_needed
from fleasion.cache.tools.solidmodel_converter.rbxm.deserializer import RbxmDeserializer
from fleasion.qml_api.cache import CacheApi, _roblox_session


def _response(request: requests.PreparedRequest, status: int, **headers: str) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers)
    response.url = request.url or ''
    response.request = request
    response._content = b''
    response._content_consumed = True
    return response


def test_roblox_cookie_is_only_prepared_for_secure_roblox_hosts() -> None:
    with _roblox_session('secret') as session:
        roblox = session.prepare_request(
            requests.Request('GET', 'https://develop.roblox.com/v1/assets')
        )
        foreign = session.prepare_request(requests.Request('GET', 'https://example.com/'))
        insecure = session.prepare_request(requests.Request('GET', 'http://develop.roblox.com/'))

    assert roblox.headers['Cookie'] == '.ROBLOSECURITY=secret'
    assert 'Cookie' not in foreign.headers
    assert 'Cookie' not in insecure.headers


def test_manual_asset_rejects_cross_origin_redirect_before_requesting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared: list[requests.PreparedRequest] = []

    def send(
        _session: requests.Session,
        request: requests.PreparedRequest,
        **_kwargs: object,
    ) -> requests.Response:
        prepared.append(request)
        return _response(request, 302, Location='https://example.com/asset')

    monkeypatch.setattr('fleasion.utils.roblox_auth.get_roblosecurity', lambda: 'secret')
    monkeypatch.setattr(requests.Session, 'send', send)

    assert cast(Any, CacheApi)._fetch_manual_asset('123') is None
    assert len(prepared) == 1
    assert prepared[0].headers['Cookie'] == '.ROBLOSECURITY=secret'


def test_manual_asset_follows_trusted_cdn_without_forwarding_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared: list[requests.PreparedRequest] = []

    def send(
        _session: requests.Session,
        request: requests.PreparedRequest,
        **_kwargs: object,
    ) -> requests.Response:
        prepared.append(request)
        if len(prepared) == 1:
            return _response(request, 302, Location='https://c0.rbxcdn.com/asset')
        response = _response(request, 200)
        response._content = b'asset-data'
        return response

    monkeypatch.setattr('fleasion.utils.roblox_auth.get_roblosecurity', lambda: 'secret')
    monkeypatch.setattr(requests.Session, 'send', send)

    assert cast(Any, CacheApi)._fetch_manual_asset('123') == b'asset-data'
    assert len(prepared) == 2
    assert prepared[1].url == 'https://c0.rbxcdn.com/asset'
    assert 'Cookie' not in prepared[1].headers


def test_gzip_document_expansion_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(roblox_document, 'MAX_DOCUMENT_BYTES', 32)

    with pytest.raises(ValueError, match='expands beyond'):
        decompress_if_needed(gzip.compress(b'x' * 33))


def test_tiny_rbxm_cannot_expand_attacker_controlled_type_index() -> None:
    inst = struct.pack('<II', 10_000, 0) + b'\x00' + struct.pack('<I', 0)
    header = RBXM_MAGIC + struct.pack('<HII', 0, 1, 0) + b'\x00' * 8
    payload = header + b'INST' + struct.pack('<III', 0, len(inst), 0) + inst
    parser = RbxmDeserializer()

    with pytest.raises(ValueError, match='outside the declared type count'):
        parser.deserialize(payload)

    assert parser._type_infos == {}


def test_tiny_rbxm_cannot_declare_unbounded_chunk_output() -> None:
    header = RBXM_MAGIC + struct.pack('<HII', 0, 0, 0) + b'\x00' * 8
    payload = header + b'META' + struct.pack('<III', 1, 0xFFFFFFFF, 0) + b'x'

    with pytest.raises(ValueError, match='expands to'):
        RbxmDeserializer().deserialize(payload)
