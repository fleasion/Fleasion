"""Ensure transport health never passes on an open port or unverified session."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from fleasion.proxy.tls_backends import AuthenticatedSession, BackendId
from fleasion.proxy.tls_backends.health import (
    HealthPhase,
    http1_roundtrip_health,
    provider_health,
    session_health,
)
from fleasion.proxy.upstream import UpstreamConnectResult


def _synthetic_session() -> AuthenticatedSession:
    return AuthenticatedSession(
        hostname="apis.roblox.com",
        alpn="http/1.1",
        client_reader=asyncio.StreamReader(),
        client_writer=cast("asyncio.StreamWriter", object()),
        upstream=UpstreamConnectResult(
            reader=asyncio.StreamReader(),
            writer=cast("asyncio.StreamWriter", object()),
            method="synthetic_test",
            endpoint="localhost",
        ),
        server_authenticated=True,
    )


@pytest.mark.parametrize("backend", list(BackendId))
def test_unimplemented_providers_never_pass_health_check(backend: BackendId) -> None:
    result = provider_health(backend, community_client="cordial")
    assert result.phase is HealthPhase.PROVIDER_UNAVAILABLE
    assert not result.working


def test_session_health_is_not_a_successful_request() -> None:
    async def run() -> None:
        session = _synthetic_session()
        result = session_health(BackendId.WINDOWS, session, hostname="apis.roblox.com")
        assert result.phase is HealthPhase.SESSION_VALIDATED
        assert not result.working

    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["none", "bad_status", "bad_framing", "bad_body", "truncated"])
def test_roundtrip_requires_http_status_framing_and_exact_challenge(mutation: str) -> None:
    async def run() -> None:
        nonce = b"loopback-challenge-123"
        response = b"HTTP/1.1 200 OK\r\n" + f"Content-Length: {len(nonce)}\r\n\r\n".encode() + nonce
        if mutation == "bad_status":
            response = response.replace(b"200 OK", b"503 Unavailable")
        if mutation == "bad_framing":
            response = response.replace(
                f"Content-Length: {len(nonce)}".encode(), b"Content-Length: 1"
            )
        if mutation == "bad_body":
            response = response.replace(nonce, b"wrong-body")
        if mutation == "truncated":
            response = response[:-3]
        result = http1_roundtrip_health(
            BackendId.WINDOWS,
            _synthetic_session(),
            hostname="apis.roblox.com",
            expected_challenge=nonce,
            raw_response=response,
        )
        assert result.working == (mutation == "none")
        assert result.phase is (
            HealthPhase.END_TO_END_VERIFIED if mutation == "none" else HealthPhase.REQUEST_FAILED
        )

    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["none", "missing_end", "bad_status", "bad_body"])
def test_http2_probe_needs_valid_completed_challenge(mutation: str) -> None:
    from fleasion.proxy.tls_backends.health import http2_roundtrip_health

    async def run() -> None:
        session = _synthetic_session()
        session.alpn = "h2"
        result = http2_roundtrip_health(
            BackendId.WINDOWS,
            session,
            hostname="apis.roblox.com",
            expected_challenge=b"challenge",
            response_status=503 if mutation == "bad_status" else 200,
            response_body=b"wrong" if mutation == "bad_body" else b"challenge",
            stream_ended=mutation != "missing_end",
        )
        assert result.working == (mutation == "none")

    asyncio.run(run())
