import importlib.util
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Never, Protocol, cast

import pytest


class _CachedContext(Protocol):
    def __call__(self) -> ssl.SSLContext | None: ...
    def cache_clear(self) -> None: ...


class _HttpModule(Protocol):
    _tls12_context: _CachedContext
    _certifi_tls12_context: _CachedContext
    _certifi_context: _CachedContext
    shutil: ModuleType
    socket: ModuleType
    subprocess: ModuleType

    def _create_connection_ipv4_first(
        self,
        address: tuple[str, int],
        timeout: object,
        source_address: tuple[str, int] | None,
    ) -> socket.socket: ...

    def _open_verified(self, req: urllib.request.Request, url: str, timeout: int) -> object: ...

    def http_download_to(
        self,
        url: str,
        dest: Path,
        timeout: int = 30,
        headers: dict[str, str] | None = None,
    ) -> None: ...


def _cached_context(module: object, name: str) -> _CachedContext:
    return cast('_CachedContext', vars(module)[name])


def _open_verified(module: object, req: urllib.request.Request, url: str, timeout: int) -> object:
    callback = cast(
        'Callable[[urllib.request.Request, str, int], object]',
        vars(module)['_open_verified'],
    )
    return callback(req, url, timeout)


def _create_connection_ipv4_first(
    module: object,
    address: tuple[str, int],
    timeout: object,
    source_address: tuple[str, int] | None,
) -> socket.socket:
    callback = cast(
        'Callable[[tuple[str, int], object, tuple[str, int] | None], socket.socket]',
        vars(module)['_create_connection_ipv4_first'],
    )
    return callback(address, timeout, source_address)


def _load_http_module() -> _HttpModule:
    path = Path(__file__).resolve().parents[1] / 'src' / 'fleasion' / 'utils' / 'http.py'
    spec = importlib.util.spec_from_file_location('fleasion_http_test', path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast('_HttpModule', module)


def test_create_connection_prefers_ipv4_and_retains_ipv6_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = _load_http_module()
    attempts: list[int] = []

    class FakeSocket:
        def __init__(self, family: int, _socktype: int, _proto: int) -> None:
            self.family = family

        def settimeout(self, _timeout: float | None) -> None:
            return None

        def bind(self, _source_address: tuple[str, int]) -> None:
            return None

        def connect(self, _sockaddr: object) -> None:
            attempts.append(self.family)
            if self.family == socket.AF_INET:
                raise OSError('IPv4 unavailable')

        def close(self) -> None:
            return None

    def fake_getaddrinfo(
        _host: str,
        _port: int,
        _family: int,
        _socktype: int,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
        ]

    def fake_socket(family: int, socktype: int, proto: int) -> FakeSocket:
        return FakeSocket(family, socktype, proto)

    monkeypatch.setattr(http.socket, 'getaddrinfo', fake_getaddrinfo)
    monkeypatch.setattr(http.socket, 'socket', fake_socket)

    connected = _create_connection_ipv4_first(http, ('example.test', 443), 1.0, None)

    assert attempts == [socket.AF_INET, socket.AF_INET6]
    assert isinstance(connected, FakeSocket)
    assert connected.family == socket.AF_INET6


def test_create_connection_stops_after_working_ipv4(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _load_http_module()
    attempts: list[int] = []

    class FakeSocket:
        def __init__(self, family: int, _socktype: int, _proto: int) -> None:
            self.family = family

        def settimeout(self, _timeout: float | None) -> None:
            return None

        def bind(self, _source_address: tuple[str, int]) -> None:
            return None

        def connect(self, _sockaddr: object) -> None:
            attempts.append(self.family)

        def close(self) -> None:
            return None

    def fake_getaddrinfo(
        _host: str,
        _port: int,
        _family: int,
        _socktype: int,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
        ]

    def fake_socket(family: int, socktype: int, proto: int) -> FakeSocket:
        return FakeSocket(family, socktype, proto)

    monkeypatch.setattr(http.socket, 'getaddrinfo', fake_getaddrinfo)
    monkeypatch.setattr(http.socket, 'socket', fake_socket)

    connected = _create_connection_ipv4_first(http, ('example.test', 443), 1.0, None)

    assert attempts == [socket.AF_INET]
    assert isinstance(connected, FakeSocket)
    assert connected.family == socket.AF_INET


def test_https_record_layer_failure_retries_with_tls12(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _load_http_module()
    _cached_context(http, '_tls12_context').cache_clear()
    _cached_context(http, '_certifi_tls12_context').cache_clear()
    calls: list[ssl.SSLContext | None] = []
    response = object()

    def fake_urlopen(
        req: urllib.request.Request,
        *,
        timeout: int,
        context: ssl.SSLContext | None = None,
    ) -> object:
        del req, timeout
        calls.append(context)
        if context is None:
            raise urllib.error.URLError(
                ssl.SSLError('[SSL: RECORD_LAYER_FAILURE] record layer failure')
            )
        return response

    monkeypatch.setattr(http, '_urlopen', fake_urlopen)

    def no_certifi_tls12() -> None:
        return None

    monkeypatch.setattr(http, '_certifi_tls12_context', no_certifi_tls12)

    req = urllib.request.Request('https://file.garden/example.obj')

    assert _open_verified(http, req, 'https://file.garden/example.obj', 30) is response
    assert calls[0] is None
    retry_context = calls[1]
    assert retry_context is not None
    assert retry_context.minimum_version is ssl.TLSVersion.TLSv1_2
    assert retry_context.maximum_version is ssl.TLSVersion.TLSv1_2


def test_certificate_failure_without_certifi_reraises_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = _load_http_module()
    original = urllib.error.URLError(ssl.SSLCertVerificationError('CERTIFICATE_VERIFY_FAILED'))

    def fake_urlopen(
        req: urllib.request.Request,
        *,
        timeout: int,
        context: ssl.SSLContext | None = None,
    ) -> Never:
        del req, timeout, context
        raise original

    monkeypatch.setattr(http, '_urlopen', fake_urlopen)

    def no_certifi_context() -> None:
        return None

    monkeypatch.setattr(http, '_certifi_context', no_certifi_context)

    req = urllib.request.Request('https://example.test/file')

    with pytest.raises(urllib.error.URLError) as exc_info:
        _open_verified(http, req, 'https://example.test/file', 30)

    assert exc_info.value is original


def test_http_download_to_uses_curl_fallback_after_urllib_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    http = _load_http_module()
    original = urllib.error.URLError(
        ssl.SSLError('[SSL: RECORD_LAYER_FAILURE] record layer failure')
    )
    dest = tmp_path / 'asset.obj'
    calls: list[tuple[list[str], bool, bool, bool, int]] = []

    def fake_urlopen(
        req: urllib.request.Request,
        *,
        timeout: int,
        context: ssl.SSLContext | None = None,
    ) -> Never:
        del req, timeout, context
        raise original

    def fake_run(
        cmd: list[str],
        capture_output: bool,
        check: bool,
        text: bool,
        creationflags: int,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, capture_output, check, text, creationflags))
        Path(cmd[cmd.index('--output') + 1]).write_bytes(b'from curl')
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(http, '_urlopen', fake_urlopen)

    def find_curl(name: str) -> str | None:
        return '/usr/bin/curl' if name == 'curl' else None

    monkeypatch.setattr(http.shutil, 'which', find_curl)
    monkeypatch.setattr(http.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    monkeypatch.setattr(http.subprocess, 'run', fake_run)

    http.http_download_to(
        'https://file.garden/example.obj',
        dest,
        timeout=30,
        headers={'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'},
    )

    assert dest.read_bytes() == b'from curl'
    assert len(calls) == 1
    cmd, capture_output, check, text, creationflags = calls[0]
    assert cmd[:8] == [
        '/usr/bin/curl',
        '--fail',
        '--location',
        '--silent',
        '--show-error',
        '--max-time',
        '30',
        '--output',
    ]
    assert '--user-agent' in cmd
    assert 'Mozilla/5.0' in cmd
    assert '--header' in cmd
    assert 'Accept: */*' in cmd
    assert cmd[-1] == 'https://file.garden/example.obj'
    assert capture_output is True
    assert check is False
    assert text is True
    assert creationflags == 0x08000000
