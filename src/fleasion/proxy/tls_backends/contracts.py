"""Platform-neutral contracts for optional, already-decrypted TLS transports.

These contracts intentionally contain no key-reading or process-inspection logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import asyncio

    from fleasion.proxy.server import FleasionProxy
    from fleasion.proxy.upstream import UpstreamConnectResult


class BackendId(StrEnum):
    WINDOWS = 'windows'
    ANDROID_COMMUNITY = 'android_community'
    SOBER = 'sober'
    MACOS = 'macos'
    ROSEMARY = 'rosemary'


class BackendStatus(StrEnum):
    NOT_IMPLEMENTED = 'not_implemented'
    RESEARCH_PENDING = 'research_pending'
    READY = 'ready'
    FAILED = 'failed'


class BackendUnavailableError(RuntimeError):
    """A platform backend cannot provide an authenticated session."""


@dataclass(frozen=True, slots=True)
class BackendProbe:
    backend: BackendId
    status: BackendStatus
    reason: str

    @property
    def ready(self) -> bool:
        return self.status is BackendStatus.READY


@dataclass(slots=True)
class AuthenticatedSession:
    """Existing plaintext streams with an independently authenticated origin.

    Both stream pairs must belong to the *same* verified session. Providers own
    acquisition and verification; the feature engine never obtains TLS keys.
    """

    hostname: str
    alpn: str
    client_reader: asyncio.StreamReader
    client_writer: asyncio.StreamWriter
    upstream: UpstreamConnectResult
    server_authenticated: bool
    initial_client_data: bytes = b''


class SessionBackend(Protocol):
    backend_id: BackendId

    def probe(self) -> BackendProbe: ...

    async def open_session(self, hostname: str) -> AuthenticatedSession: ...


def validate_session(session: AuthenticatedSession, hostname: str) -> None:
    """Reject unauthenticated, cross-host, or unsupported session metadata."""
    normalized = hostname.strip().lower().rstrip('.')
    if not normalized or session.hostname.strip().lower().rstrip('.') != normalized:
        msg = 'Authenticated session hostname does not match requested hostname'
        raise BackendUnavailableError(msg)
    if not session.server_authenticated:
        msg = 'Upstream server authentication has not been verified'
        raise BackendUnavailableError(msg)
    if session.upstream.reader is None or session.upstream.writer is None:
        msg = 'Authenticated session has no upstream connection'
        raise BackendUnavailableError(msg)
    if session.alpn not in {'h2', 'http/1.1'}:
        msg = 'Unsupported negotiated ALPN protocol'
        raise BackendUnavailableError(msg)
    if (
        session.alpn == 'h2'
        and session.initial_client_data
        and len(session.initial_client_data) > 65536
    ):
        msg = 'Initial HTTP/2 data exceeds inspection limit'
        raise BackendUnavailableError(msg)
    if session.alpn != 'h2' and session.initial_client_data:
        msg = 'Initial client bytes require HTTP/2 negotiation'
        raise BackendUnavailableError(msg)


async def dispatch_authenticated_session(
    proxy: FleasionProxy, session: AuthenticatedSession, *, expected_hostname: str
) -> None:
    """Dispatch verified plaintext streams into the existing feature engine.

    This function does not claim the backend is usable until a complete request
    has traversed the engine. The caller retains cleanup responsibility.
    """
    validate_session(session, expected_hostname)
    if session.alpn == 'h2':
        await proxy.serve_decrypted_h2_session(
            session.client_reader,
            session.client_writer,
            expected_hostname,
            session.upstream,
            initial_client_data=session.initial_client_data,
        )
    else:
        await proxy.serve_decrypted_http_session(
            session.client_reader,
            session.client_writer,
            expected_hostname,
            session.upstream,
        )
