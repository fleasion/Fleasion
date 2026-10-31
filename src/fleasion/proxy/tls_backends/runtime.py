"""Explicit, bounded acquisition of optional authenticated stream providers."""

from __future__ import annotations

import asyncio

from .contracts import (
    AuthenticatedSession,
    BackendUnavailableError,
    SessionBackend,
    validate_session,
)


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
    validate_session(session, hostname)
    return session
