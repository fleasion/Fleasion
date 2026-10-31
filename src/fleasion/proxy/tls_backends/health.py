"""Layered readiness evidence for optional decrypted-stream transports.

A listening socket or a provider's self-report is not proof of interception.
Only a completed, challenge-matched HTTP exchange is marked end-to-end ready.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contracts import AuthenticatedSession, BackendId, BackendUnavailableError, validate_session
from .registry import create_backend


class HealthPhase(StrEnum):
    PROVIDER_UNAVAILABLE = 'provider_unavailable'
    PROVIDER_ONLY = 'provider_only'
    SESSION_INVALID = 'session_invalid'
    SESSION_VALIDATED = 'session_validated'
    REQUEST_FAILED = 'request_failed'
    END_TO_END_VERIFIED = 'end_to_end_verified'


@dataclass(frozen=True, slots=True)
class TransportHealth:
    backend: BackendId
    phase: HealthPhase
    detail: str

    @property
    def working(self) -> bool:
        return self.phase is HealthPhase.END_TO_END_VERIFIED


def provider_health(backend: BackendId, *, community_client: str | None = None) -> TransportHealth:
    """Read a provider's declared readiness without creating client processes."""
    probe = create_backend(backend, community_client=community_client).probe()
    if not probe.ready:
        return TransportHealth(backend, HealthPhase.PROVIDER_UNAVAILABLE, probe.reason)
    return TransportHealth(
        backend, HealthPhase.PROVIDER_ONLY, 'Backend reports ready; no session or request verified'
    )


def session_health(
    backend: BackendId, session: AuthenticatedSession, *, hostname: str
) -> TransportHealth:
    """Validate a supplied session without falsely claiming request success."""
    try:
        validate_session(session, hostname)
    except BackendUnavailableError as exc:
        return TransportHealth(backend, HealthPhase.SESSION_INVALID, str(exc))
    return TransportHealth(
        backend,
        HealthPhase.SESSION_VALIDATED,
        'Authenticated session metadata validated; request untested',
    )


def http1_roundtrip_health(
    backend: BackendId,
    session: AuthenticatedSession,
    *,
    hostname: str,
    expected_challenge: bytes,
    raw_response: bytes,
) -> TransportHealth:
    """Require an exact HTTP success response containing the test challenge.

    The caller must acquire ``raw_response`` from a completed proxy exchange,
    not from the listening-port check. This verifies application-level evidence.
    """
    prior = session_health(backend, session, hostname=hostname)
    if prior.phase is not HealthPhase.SESSION_VALIDATED:
        return prior
    reason = ''
    if not expected_challenge or len(expected_challenge) > 4096:
        reason = 'Invalid challenge size'
    else:
        header_bytes, separator, response_body = raw_response.partition(b'\r\n\r\n')
        if not separator:
            reason = 'Incomplete HTTP response'
        else:
            first_line, *headers = header_bytes.split(b'\r\n')
            content_lengths = [
                value.strip()
                for name, delimiter, value in (line.partition(b':') for line in headers)
                if delimiter and name.strip().lower() == b'content-length'
            ]
            if first_line.split(b' ', 2)[:2] != [b'HTTP/1.1', b'200']:
                reason = 'Unexpected HTTP status'
            elif (
                len(content_lengths) != 1
                or content_lengths[0] != str(len(expected_challenge)).encode()
            ):
                reason = 'Invalid response framing'
            elif response_body != expected_challenge:
                reason = 'End-to-end challenge mismatch'
    if reason:
        return TransportHealth(backend, HealthPhase.REQUEST_FAILED, reason)
    return TransportHealth(
        backend, HealthPhase.END_TO_END_VERIFIED, 'Completed HTTP exchange matched test challenge'
    )


def http2_roundtrip_health(
    backend: BackendId,
    session: AuthenticatedSession,
    *,
    hostname: str,
    expected_challenge: bytes,
    response_status: int | None,
    response_body: bytes,
    stream_ended: bool,
) -> TransportHealth:
    """Require HTTP/2 END_STREAM, status 200, and the exact response challenge."""
    prior = session_health(backend, session, hostname=hostname)
    if prior.phase is not HealthPhase.SESSION_VALIDATED:
        return prior
    reason = ''
    if session.alpn != 'h2':
        reason = 'Health probe was not negotiated over HTTP/2'
    elif not expected_challenge or len(expected_challenge) > 4096:
        reason = 'Invalid challenge size'
    elif not stream_ended:
        reason = 'HTTP/2 stream did not complete'
    elif response_status != 200:
        reason = 'Unexpected HTTP/2 status'
    elif response_body != expected_challenge:
        reason = 'End-to-end challenge mismatch'
    if reason:
        return TransportHealth(backend, HealthPhase.REQUEST_FAILED, reason)
    return TransportHealth(
        backend, HealthPhase.END_TO_END_VERIFIED, 'Completed HTTP/2 exchange matched test challenge'
    )
