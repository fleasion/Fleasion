"""Fail-closed platform provider contracts and synthetic session dispatch tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.tls_backends import (
    ANDROID_COMMUNITY_CLIENTS,
    AuthenticatedSession,
    BackendId,
    BackendStatus,
    BackendUnavailableError,
    backend_probes,
    create_backend,
    dispatch_authenticated_session,
    validate_session,
)
from fleasion.proxy.upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from fleasion.proxy.server import FleasionProxy


@pytest.mark.parametrize("backend_id", list(BackendId))
def test_platform_placeholders_remain_disabled(backend_id: BackendId) -> None:
    async def run() -> None:
        backend = create_backend(backend_id, community_client="cordial")
        probe = backend.probe()
        assert probe.backend == backend_id
        assert not probe.ready
        if backend_id is BackendId.MACOS:
            assert probe.status is BackendStatus.RESEARCH_PENDING
        else:
            assert probe.status is BackendStatus.NOT_IMPLEMENTED
        assert probe.reason
        with pytest.raises(BackendUnavailableError):
            await backend.open_session("apis.roblox.com")

    asyncio.run(run())


def test_all_five_backend_slots_have_explicit_status() -> None:
    probes = backend_probes()
    assert len(probes) == 5
    assert {probe.backend for probe in probes} == set(BackendId)
    assert all(not probe.ready for probe in probes)
    assert ANDROID_COMMUNITY_CLIENTS == ("cordial", "nightcap", "mocktail")


@pytest.mark.parametrize("runtime", ["cordial", "nightcap", "mocktail"])
def test_android_community_placeholder_tracks_runtime(runtime: str) -> None:
    backend = create_backend(BackendId.ANDROID_COMMUNITY, community_client=runtime)
    assert getattr(backend, "client") == runtime
    assert not backend.probe().ready


def test_android_community_requires_known_runtime() -> None:
    with pytest.raises(ValueError, match="requires a specific runtime"):
        create_backend(BackendId.ANDROID_COMMUNITY)
    with pytest.raises(ValueError, match="Unknown community Android runtime"):
        create_backend(BackendId.ANDROID_COMMUNITY, community_client="other")


class _RecordingProxy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def serve_decrypted_h2_session(
        self,
        _reader: asyncio.StreamReader,
        _writer: asyncio.StreamWriter,
        hostname: str,
        _upstream: UpstreamConnectResult,
        *,
        initial_client_data: bytes = b"",
    ) -> None:
        assert initial_client_data == b""
        self.calls.append(("h2", hostname))

    async def serve_decrypted_http_session(
        self,
        _reader: asyncio.StreamReader,
        _writer: asyncio.StreamWriter,
        hostname: str,
        _upstream: UpstreamConnectResult,
    ) -> None:
        self.calls.append(("http/1.1", hostname))


def _session(alpn: str = "h2") -> AuthenticatedSession:
    return AuthenticatedSession(
        hostname="apis.roblox.com",
        alpn=alpn,
        client_reader=asyncio.StreamReader(),
        client_writer=cast("asyncio.StreamWriter", object()),
        upstream=UpstreamConnectResult(
            reader=asyncio.StreamReader(),
            writer=cast("asyncio.StreamWriter", object()),
            method="synthetic_local_test",
            endpoint="127.0.0.1",
        ),
        server_authenticated=True,
    )


@pytest.mark.parametrize("alpn", ["h2", "http/1.1"])
def test_dispatch_routes_synthetic_authenticated_streams(alpn: str) -> None:
    async def run() -> None:
        proxy = _RecordingProxy()
        await dispatch_authenticated_session(
            cast("FleasionProxy", proxy), _session(alpn), expected_hostname="apis.roblox.com"
        )
        assert proxy.calls == [(alpn, "apis.roblox.com")]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("attribute", "value", "reason"),
    [
        ("hostname", "other.example", "hostname"),
        ("server_authenticated", False, "authentication"),
        ("alpn", "h3", "ALPN"),
        ("initial_client_data", b"x", "HTTP/2"),
    ],
)
def test_invalid_session_fails_before_dispatch(attribute: str, value: object, reason: str) -> None:
    async def run() -> None:
        session = _session("http/1.1")
        setattr(session, attribute, value)
        proxy = _RecordingProxy()
        with pytest.raises(BackendUnavailableError, match=reason):
            await dispatch_authenticated_session(
                cast("FleasionProxy", proxy), session, expected_hostname="apis.roblox.com"
            )
        assert not proxy.calls

    asyncio.run(run())


def test_session_requires_upstream_peer() -> None:
    async def run() -> None:
        session = _session()
        session.upstream.reader = None
        with pytest.raises(BackendUnavailableError, match="upstream connection"):
            validate_session(session, "apis.roblox.com")

    asyncio.run(run())
