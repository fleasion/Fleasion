# pyright: reportPrivateUsage=false
"""Original-avatar previews bypass active interception using verified upstream TLS."""

from __future__ import annotations

import json
import ssl
from types import SimpleNamespace
from typing import cast

import pytest

from fleasion.gui.modifications_tab import ModificationsTab
from fleasion.proxy.addons.profile_picture_spoofer import ProfilePictureSpoofer
from fleasion.proxy.original_thumbnail import fetch_original_avatar_url
from fleasion.proxy import server as proxy_server
from fleasion.proxy.server import ProxyFlow
from fleasion.proxy.upstream import UpstreamEndpoint

REAL_IMAGE = 'https://tr.rbxcdn.com/unspoofed-original.png'
SPOOFED_IMAGE = 'https://example.com/spoofed.png'


class _Response:
    status = 200

    def __init__(self, content: bytes) -> None:
        self._content = content
        self.closed = False

    def read(self, limit: int) -> bytes:
        assert limit <= 128 * 1024 + 1
        return self._content[:limit]

    def close(self) -> None:
        self.closed = True


class _Pool:
    def __init__(self, response: _Response, calls: list[object]) -> None:
        self.response = response
        self.calls = calls
        self.closed = False

    def urlopen(self, method: str, path: str, **kwargs: object) -> _Response:
        self.calls.append((method, path, kwargs))
        assert method == 'GET'
        assert path.startswith('/v1/users/avatar-headshot?')
        assert kwargs['headers'] == {
            'Host': 'thumbnails.roblox.com',
            'Accept-Encoding': 'identity',
        }
        assert kwargs['redirect'] is False
        assert kwargs['preload_content'] is False
        return self.response

    def close(self) -> None:
        self.closed = True


def test_original_avatar_bypasses_enabled_spoofer_and_preserves_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.proxy import original_thumbnail as origin

    original = {'data': [{'targetId': 5, 'imageUrl': REAL_IMAGE}]}
    flow = ProxyFlow(
        b'GET /v1/users/avatar-headshot?userIds=5 HTTP/1.1',
        {},
        b'',
        'thumbnails.roblox.com',
    )
    flow.response = proxy_server._FlowResponse(b'HTTP/1.1 200 OK', json.dumps(original).encode())
    spoofer = ProfilePictureSpoofer()
    spoofer.set_entries([{'enabled': True, 'target': '5', 'image_url': SPOOFED_IMAGE}])
    spoofer.response(flow)
    assert flow.response is not None
    assert json.loads(flow.response.content)['data'][0]['imageUrl'] == SPOOFED_IMAGE

    calls: list[object] = []
    response = _Response(json.dumps(original).encode())
    pool = _Pool(response, calls)

    def pool_factory(host: str, **kwargs: object) -> _Pool:
        assert host == '8.8.8.8'  # A previously resolved real endpoint, not OS hosts
        assert kwargs['server_hostname'] == 'thumbnails.roblox.com'
        assert kwargs['assert_hostname'] == 'thumbnails.roblox.com'
        assert kwargs['cert_reqs'] == ssl.CERT_REQUIRED
        return pool

    monkeypatch.setattr(origin.urllib3, 'HTTPSConnectionPool', pool_factory)
    master = SimpleNamespace(
        original_thumbnail_upstreams=lambda: [
            UpstreamEndpoint(host='thumbnails.roblox.com', ip='8.8.8.8')
        ]
    )
    fake_gui = cast('ModificationsTab', SimpleNamespace(_proxy_master=master))
    assert ModificationsTab._fetch_pfp_avatar_url(fake_gui, '5') == REAL_IMAGE
    assert response.closed and pool.closed
    assert len(calls) == 1


def test_original_avatar_fails_closed_if_only_intercepted_endpoint_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.proxy import original_thumbnail as origin

    def forbidden_pool(*_args: object, **_kwargs: object) -> None:
        pytest.fail('Must not connect to a localhost-intercepted address')

    monkeypatch.setattr(origin.urllib3, 'HTTPSConnectionPool', forbidden_pool)
    assert fetch_original_avatar_url(
        '5', [UpstreamEndpoint(host='thumbnails.roblox.com', ip='127.0.0.1')]
    ) == ''
    assert fetch_original_avatar_url('5', []) == ''


def test_original_avatar_rejects_unbounded_or_wrong_user_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.proxy import original_thumbnail as origin

    responses = [
        {'data': [{'targetId': 999, 'imageUrl': REAL_IMAGE}]},
        {'data': [{'targetId': 5, 'imageUrl': 'http://example.com/untrusted.png'}]},
    ]

    def pool_factory(_host: str, **_kwargs: object) -> _Pool:
        response = _Response(json.dumps(responses.pop(0)).encode())
        return _Pool(response, [])

    monkeypatch.setattr(origin.urllib3, 'HTTPSConnectionPool', pool_factory)
    endpoints = [
        UpstreamEndpoint(host='thumbnails.roblox.com', ip='8.8.8.8'),
        UpstreamEndpoint(host='thumbnails.roblox.com', ip='1.1.1.1'),
    ]
    assert fetch_original_avatar_url('5', endpoints) == ''
