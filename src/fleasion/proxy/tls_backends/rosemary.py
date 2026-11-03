"""Rosemary macOS Roblox session transport provider using Linux /proc secret recovery."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fleasion.proxy.rosemary_keys import find_rosemary_pid

from .contracts import BackendId, BackendProbe, BackendStatus, BackendUnavailableError

if TYPE_CHECKING:
    from .contracts import AuthenticatedSession


class RosemaryBackend:
    """Rosemary macOS Roblox transport provider backed by live TLS secret recovery."""

    backend_id = BackendId.ROSEMARY

    def probe(self) -> BackendProbe:
        """Return the current readiness status of the Rosemary transport.

        Returns
        -------
        BackendProbe
            Backend readiness status and diagnostic message.
        """
        if sys.platform != 'linux':
            return BackendProbe(
                self.backend_id,
                BackendStatus.NOT_IMPLEMENTED,
                'Rosemary transport integration requires Linux',
            )

        pid = find_rosemary_pid()
        if pid is None:
            return BackendProbe(
                self.backend_id,
                BackendStatus.NOT_IMPLEMENTED,
                'Rosemary RobloxPlayer process not found',
            )
        return BackendProbe(
            self.backend_id,
            BackendStatus.READY,
            f'Rosemary macOS Roblox process active (PID {pid})',
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
