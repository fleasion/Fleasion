"""Explicit fail-closed base class for unfinished platform integrations."""

from __future__ import annotations

from fleasion.proxy.tls_backends.contracts import (
    AuthenticatedSession,
    BackendId,
    BackendProbe,
    BackendStatus,
    BackendUnavailableError,
)


class UnimplementedBackend:
    backend_id: BackendId
    reason: str = 'Platform provider has no verified session implementation'
    status: BackendStatus = BackendStatus.NOT_IMPLEMENTED

    def probe(self) -> BackendProbe:
        return BackendProbe(self.backend_id, self.status, self.reason)

    async def open_session(self, hostname: str) -> AuthenticatedSession:
        # TODO(contributors): Verify peer authentication and stream ownership
        msg = f'{self.backend_id.value} ({hostname}): {self.reason}'
        raise BackendUnavailableError(msg)
