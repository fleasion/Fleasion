"""Explicit, bounded acquisition of optional authenticated stream providers."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from .contracts import (
    AuthenticatedSession,
    BackendUnavailableError,
    SessionBackend,
    dispatch_authenticated_session,
    validate_session,
)

if TYPE_CHECKING:
    from fleasion.proxy.server import FleasionProxy


async def _close_session_streams(session: AuthenticatedSession) -> None:
    """Release both stream pairs after rejection or completed dispatch."""
    writers = (session.client_writer, session.upstream.writer)
    unique: list[asyncio.StreamWriter] = []
    for writer in writers:
        if writer is not None and all(writer is not prior for prior in unique):
            unique.append(writer)
            with contextlib.suppress(Exception):
                writer.close()
    for writer in unique:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), timeout=0.25)


async def acquire_verified_session(
    backend: SessionBackend, hostname: str, *, timeout_seconds: float = 3.0
) -> AuthenticatedSession:
    """Acquire one session without silently switching platforms or trust paths.

    The caller owns the returned session's stream lifetimes. The provider must
    independently authenticate its upstream server and maintain stream provenance.
    """
    if not 0 < timeout_seconds <= 60:
        msg = 'Session acquisition timeout must be between 0 and 60 seconds'
        raise ValueError(msg)
    probe = backend.probe()
    if not probe.ready:
        msg = f'{backend.backend_id.value}: {probe.status.value}: {probe.reason}'
        raise BackendUnavailableError(msg)
    try:
        session = await asyncio.wait_for(backend.open_session(hostname), timeout_seconds)
    except TimeoutError as exc:
        msg = f'{backend.backend_id.value}: session acquisition timed out'
        raise BackendUnavailableError(msg) from exc
    except OSError as exc:
        msg = f'{backend.backend_id.value}: session acquisition failed'
        raise BackendUnavailableError(msg) from exc
    try:
        validate_session(session, hostname)
    except BackendUnavailableError:
        # Preserve validation failure after closing any acquired streams
        await _close_session_streams(session)
        raise
    return session


async def run_managed_backend_session(
    proxy: FleasionProxy,
    backend: SessionBackend,
    hostname: str,
    *,
    timeout_seconds: float = 3.0,
) -> None:
    """Dispatch one validated provider session and always close its streams.

    Does not retry or switch to another backend after any failure. Platform
    providers remain inactive until they implement and verify the interface.
    """
    session = await acquire_verified_session(backend, hostname, timeout_seconds=timeout_seconds)
    try:
        await dispatch_authenticated_session(proxy, session, expected_hostname=hostname)
    finally:
        await _close_session_streams(session)
