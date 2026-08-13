from __future__ import annotations

import importlib.util
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest


class _Response:
    def __init__(
        self,
        body: bytes = b'',
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.closed = False
        self.read_sizes: list[int] = []
        self._body = body
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            size = len(self._body) - self._offset
        result = self._body[self._offset : self._offset + size]
        self._offset += len(result)
        return result

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _load_http_module() -> Any:
    path = Path(__file__).resolve().parents[1] / 'src' / 'fleasion' / 'utils' / 'http.py'
    spec = importlib.util.spec_from_file_location('fleasion_http_test', path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _address_result(address: str, port: int) -> list[tuple[object, ...]]:
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            '',
            (address, port),
        )
    ]


def _public_dns(
    _hostname: str,
    port: int,
    *,
    family: int,
    type: int,
) -> list[tuple[object, ...]]:
    del family, type
    return _address_result('8.8.8.8', port)


def test_https_record_layer_failure_retries_with_tls12(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _load_http_module()
    http._tls12_context.cache_clear()
    http._certifi_tls12_context.cache_clear()
    calls: list[ssl.SSLContext | None] = []
    response = object()

    def fake_open_once(
        _req: urllib.request.Request,
        _timeout: int,
        context: ssl.SSLContext | None,
    ) -> object:
        calls.append(context)
        if context is None:
            raise urllib.error.URLError(
                ssl.SSLError('[SSL: RECORD_LAYER_FAILURE] record layer failure')
            )
        return response

    monkeypatch.setattr(http, '_open_once', fake_open_once)
    monkeypatch.setattr(http, '_certifi_tls12_context', lambda: None)

    req = urllib.request.Request('https://file.garden/example.obj')

    assert http._open_verified(req, 'https://file.garden/example.obj', 30) is response
    assert calls[0] is None
    assert calls[1] is not None
    assert calls[1].minimum_version is ssl.TLSVersion.TLSv1_2
    assert calls[1].maximum_version is ssl.TLSVersion.TLSv1_2


def test_certificate_failure_without_certifi_reraises_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = _load_http_module()
    original = urllib.error.URLError(ssl.SSLCertVerificationError('CERTIFICATE_VERIFY_FAILED'))

    def fake_open_once(
        _req: urllib.request.Request,
        _timeout: int,
        _context: ssl.SSLContext | None,
    ) -> object:
        raise original

    monkeypatch.setattr(http, '_open_once', fake_open_once)
    monkeypatch.setattr(http, '_certifi_context', lambda: None)

    req = urllib.request.Request('https://example.test/file')

    with pytest.raises(urllib.error.URLError) as exc_info:
        http._open_verified(req, 'https://example.test/file', 30)

    assert exc_info.value is original


def test_hostname_resolving_to_loopback_is_rejected_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = _load_http_module()
    opened = False

    def loopback_dns(
        _hostname: str,
        port: int,
        *,
        family: int,
        type: int,
    ) -> list[tuple[object, ...]]:
        del family, type
        return _address_result('127.0.0.1', port)

    def fake_open(*_args: object) -> object:
        nonlocal opened
        opened = True
        return _Response()

    monkeypatch.setattr(http.socket, 'getaddrinfo', loopback_dns)
    monkeypatch.setattr(http, '_open_verified', fake_open)

    with pytest.raises(http.HttpSafetyError, match='non-global'):
        http.http_get('https://attacker.example/preset.json')

    assert not opened


def test_redirect_destination_is_resolved_and_private_redirect_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = _load_http_module()
    requests: list[str] = []
    redirect = _Response(
        status=302,
        headers={'Location': 'https://private.example/secret'},
    )

    def dns(
        hostname: str,
        port: int,
        *,
        family: int,
        type: int,
    ) -> list[tuple[object, ...]]:
        del family, type
        address = '127.0.0.1' if hostname == 'private.example' else '8.8.8.8'
        return _address_result(address, port)

    def fake_open(req: urllib.request.Request, _url: str, _timeout: int) -> _Response:
        requests.append(req.full_url)
        return redirect

    monkeypatch.setattr(http.socket, 'getaddrinfo', dns)
    monkeypatch.setattr(http, '_open_verified', fake_open)

    with pytest.raises(http.HttpSafetyError, match='non-global'):
        http.http_get('https://public.example/start')

    assert requests == ['https://public.example/start']
    assert redirect.closed


def test_cross_origin_redirect_drops_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = _load_http_module()
    requests: list[urllib.request.Request] = []

    def fake_open(req: urllib.request.Request, _url: str, _timeout: int) -> _Response:
        requests.append(req)
        if len(requests) == 1:
            return _Response(
                status=302,
                headers={'Location': 'https://cdn.example/file'},
            )
        return _Response(b'ok')

    monkeypatch.setattr(http.socket, 'getaddrinfo', _public_dns)
    monkeypatch.setattr(http, '_open_verified', fake_open)

    result = http.http_get(
        'https://source.example/start',
        headers={
            'Authorization': 'Bearer secret',
            'Cookie': 'session=secret',
            'Accept': 'application/json',
        },
    )

    assert result == b'ok'
    first_headers = {key.casefold(): value for key, value in requests[0].header_items()}
    second_headers = {key.casefold(): value for key, value in requests[1].header_items()}
    assert first_headers['authorization'] == 'Bearer secret'
    assert first_headers['cookie'] == 'session=secret'
    assert 'authorization' not in second_headers
    assert 'cookie' not in second_headers
    assert second_headers['accept'] == 'application/json'


def test_streamed_response_stops_at_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _load_http_module()
    response = _Response(b'abcdef')
    monkeypatch.setattr(http.socket, 'getaddrinfo', _public_dns)
    monkeypatch.setattr(http, '_open_verified', lambda *_args: response)

    with pytest.raises(http.HttpSizeLimitError, match='5 byte'):
        http.http_get('https://cdn.example/file', max_bytes=5)

    assert response.read_sizes == [6]
    assert response.closed


def test_oversized_download_keeps_existing_destination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    http = _load_http_module()
    response = _Response(b'oversized')
    destination = tmp_path / 'asset.bin'
    destination.write_bytes(b'original')
    monkeypatch.setattr(http.socket, 'getaddrinfo', _public_dns)
    monkeypatch.setattr(http, '_open_verified', lambda *_args: response)

    with pytest.raises(http.HttpSizeLimitError):
        http.http_download_to(
            'https://cdn.example/asset.bin',
            destination,
            max_bytes=4,
        )

    assert destination.read_bytes() == b'original'
    assert not list(tmp_path.glob('*.download'))


def test_curl_fallback_is_pinned_bounded_and_does_not_follow_redirects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    http = _load_http_module()
    original = urllib.error.URLError(
        ssl.SSLError('[SSL: RECORD_LAYER_FAILURE] record layer failure')
    )
    destination = tmp_path / 'asset.obj'
    calls: dict[str, object] = {}

    def fail_open(*_args: object) -> object:
        raise original

    def fake_run(
        cmd: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
        creationflags: int,
    ) -> subprocess.CompletedProcess[str]:
        calls['cmd'] = cmd
        calls['capture_output'] = capture_output
        calls['check'] = check
        calls['text'] = text
        calls['creationflags'] = creationflags
        Path(cmd[cmd.index('--output') + 1]).write_bytes(b'from curl')
        return subprocess.CompletedProcess(cmd, 0, '200', '')

    monkeypatch.setattr(http.socket, 'getaddrinfo', _public_dns)
    monkeypatch.setattr(http, '_open_once', fail_open)
    monkeypatch.setattr(http.shutil, 'which', lambda _name: '/usr/bin/curl')
    monkeypatch.setattr(http.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    monkeypatch.setattr(http.subprocess, 'run', fake_run)

    http.http_download_to(
        'https://file.garden/example.obj',
        destination,
        timeout=30,
        headers={'Accept': '*/*'},
        max_bytes=1024,
    )

    assert destination.read_bytes() == b'from curl'
    command = calls['cmd']
    assert isinstance(command, list)
    assert '--location' not in command
    assert command[command.index('--max-filesize') + 1] == '1024'
    assert command[command.index('--resolve') + 1] == 'file.garden:443:8.8.8.8'
    assert command[-1] == 'https://file.garden/example.obj'
    assert calls['capture_output'] is True
    assert calls['check'] is False
    assert calls['text'] is True
    assert calls['creationflags'] == 0x08000000


@pytest.mark.parametrize(
    'url',
    (
        'https://raw.githubusercontent.com/fleasion/Fleasion/refs/heads/clog/CLOG.json',
        'https://clientsettingscdn.roblox.com/v2/settings/application/PCDesktopClient',
    ),
)
def test_approved_roblox_and_github_https_sources_remain_supported(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    http = _load_http_module()
    monkeypatch.setattr(http.socket, 'getaddrinfo', _public_dns)

    assert http.validate_public_https_url(url) == url
