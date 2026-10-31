"""The optional backend entry point fails closed on unavailable/slow providers."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from fleasion.proxy.tls_backends import (
    AuthenticatedSession,
    BackendId,
    BackendProbe,
    BackendStatus,
    BackendUnavailableError,
    create_backend,
)
from fleasion.proxy.tls_backends.runtime import acquire_verified_session
from fleasion.proxy.upstream import UpstreamConnectResult


class _FakeProvider:
    backend_id = BackendId.WINDOWS

    def __init__(self, *, delay: float = 0, authenticated: bool = True) -> None:
        self.delay = delay
        self.authenticated = authenticated
        self.open_calls = 0

    def probe(self) -> BackendProbe:
        return BackendProbe(self.backend_id, BackendStatus.READY, "Controlled fixture")

    async def open_session(self, hostname: str) -> AuthenticatedSession:
        self.open_calls += 1
        await asyncio.sleep(self.delay)
        return AuthenticatedSession(
            hostname=hostname,
            alpn="h2",
            client_reader=asyncio.StreamReader(),
            client_writer=cast("asyncio.StreamWriter", object()),
            upstream=UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast("asyncio.StreamWriter", object()),
                method="synthetic_local_test",
                endpoint="localhost",
            ),
            server_authenticated=self.authenticated,
        )


def test_unavailable_provider_is_not_started() -> None:
    async def run() -> None:
        backend = create_backend(BackendId.WINDOWS)
        with pytest.raises(BackendUnavailableError, match="not_implemented"):
            await acquire_verified_session(backend, "apis.roblox.com")

    asyncio.run(run())


def test_ready_fixture_can_open_verified_synthetic_session() -> None:
    async def run() -> None:
        backend = _FakeProvider()
        result = await acquire_verified_session(backend, "apis.roblox.com")
        assert result.server_authenticated
        assert backend.open_calls == 1

    asyncio.run(run())


def test_backend_open_times_out_without_fallback() -> None:
    async def run() -> None:
        backend = _FakeProvider(delay=0.5)
        with pytest.raises(BackendUnavailableError, match="timed out"):
            await acquire_verified_session(backend, "apis.roblox.com", timeout_seconds=0.01)
        assert backend.open_calls == 1

    asyncio.run(run())


def test_backend_cannot_return_unauthenticated_session() -> None:
    async def run() -> None:
        backend = _FakeProvider(authenticated=False)
        with pytest.raises(BackendUnavailableError, match="authentication"):
            await acquire_verified_session(backend, "apis.roblox.com")

    asyncio.run(run())


@pytest.mark.parametrize("timeout", [0, -1, 61])
def test_bad_acquisition_timeout_rejected(timeout: float) -> None:
    async def run() -> None:
        with pytest.raises(ValueError, match="timeout"):
            await acquire_verified_session(
                _FakeProvider(), "apis.roblox.com", timeout_seconds=timeout
            )

    asyncio.run(run())
