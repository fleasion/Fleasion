"""Native Windows Roblox session transport provider using memory key extraction."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fleasion.proxy.live_keys import find_roblox_pid

from .contracts import BackendId, BackendProbe, BackendStatus, BackendUnavailableError

if TYPE_CHECKING:
    from .contracts import AuthenticatedSession


class WindowsBackend:
    """Windows Roblox transport provider backed by live TLS secret recovery."""

    backend_id = BackendId.WINDOWS

    def probe(self) -> BackendProbe:
        """Return the current readiness status of the Windows transport.

        Returns
        -------
        BackendProbe
            Backend readiness status and diagnostic message.
        """
        if sys.platform != 'win32':
            return BackendProbe(
                self.backend_id,
                BackendStatus.NOT_IMPLEMENTED,
                'Windows transport integration requires Windows',
            )

        pid = find_roblox_pid()
        if pid is None:
            return BackendProbe(
                self.backend_id,
                BackendStatus.NOT_IMPLEMENTED,
                'RobloxPlayerBeta process not found',
            )
        return BackendProbe(
            self.backend_id,
            BackendStatus.READY,
            f'Windows Roblox process active (PID {pid})',
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
