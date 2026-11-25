"""Sober Android Roblox session transport provider using Linux memory secret recovery."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fleasion.proxy.sober_keys import find_sober_pid

from .contracts import BackendId, BackendProbe, BackendStatus, BackendUnavailableError

if TYPE_CHECKING:
    from .contracts import AuthenticatedSession


class SoberBackend:
    """Sober Android Roblox transport provider backed by live TLS secret recovery."""

    backend_id = BackendId.SOBER

    def probe(self) -> BackendProbe:
        """Return the current readiness status of the Sober transport.

        Returns
        -------
        BackendProbe
            Backend readiness status and diagnostic message.
        """
        if sys.platform != 'linux':
            return BackendProbe(
                self.backend_id,
                BackendStatus.NOT_IMPLEMENTED,
                'Sober transport integration requires Linux',
            )

        pid = find_sober_pid()
        if pid is None:
            return BackendProbe(
                self.backend_id,
                BackendStatus.NOT_IMPLEMENTED,
                'Sober Roblox process not found',
            )
        return BackendProbe(
            self.backend_id,
            BackendStatus.READY,
            f'Sober Roblox process active (PID {pid})',
        )

    async def open_session(self, hostname: str) -> AuthenticatedSession:
        """Acquire an authenticated session for the given hostname.

        Parameters
        ----------
        hostname : str
            Requested target hostname.

        Returns
        -------
        AuthenticatedSession
            Authenticated session streams.
        """
        del hostname
        probe = self.probe()
        if not probe.ready:
            msg = f'{self.backend_id.value}: {probe.reason}'
            raise BackendUnavailableError(msg)
        msg = f'{self.backend_id.value}: live session handoff requires an active tunnel'
        raise BackendUnavailableError(msg)
