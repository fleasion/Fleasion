"""HTTP/2 response framing for responses that cannot carry a message body."""

from __future__ import annotations

import pytest

from fleasion.proxy.h2_transport import _as_http2  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize('status', [204, 304])
def test_bodyless_response_has_no_content_length(status: int) -> None:
    headers, body = _as_http2(  # pyright: ignore[reportPrivateUsage]
        f'HTTP/1.1 {status} Response\r\ncontent-length: 0\r\n\r\n'.encode(),
        request=False,
        host='apis.roblox.com',
    )
    assert body == b''
    assert (b':status', str(status).encode()) in headers
    assert not any(name == b'content-length' for name, _ in headers)


def test_head_response_has_no_auto_content_length() -> None:
    headers, body = _as_http2(  # pyright: ignore[reportPrivateUsage]
        b'HTTP/1.1 200 OK\r\ncontent-length: 0\r\n\r\n',
        request=False,
        host='apis.roblox.com',
        request_method=b'HEAD',
    )
    assert body == b''
    assert (b':status', b'200') in headers
    assert not any(name == b'content-length' for name, _ in headers)


@pytest.mark.parametrize('status', [204, 304])
def test_forbidden_response_body_is_rejected(status: int) -> None:
    with pytest.raises(ValueError, match='body'):
        _as_http2(  # pyright: ignore[reportPrivateUsage]
            f'HTTP/1.1 {status} Response\r\n\r\ncontent'.encode(),
            request=False,
            host='apis.roblox.com',
        )


def test_head_response_with_body_is_rejected() -> None:
    with pytest.raises(ValueError, match='body'):
        _as_http2(  # pyright: ignore[reportPrivateUsage]
            b'HTTP/1.1 200 OK\r\n\r\ncontent',
            request=False,
            host='apis.roblox.com',
            request_method=b'HEAD',
        )
