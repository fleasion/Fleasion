"""Community Android Roblox session transport provider (Cordial, Nightcap, Mocktail)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fleasion.proxy.android_community_keys import find_android_community_pid

from .contracts import BackendId, BackendProbe, BackendStatus, BackendUnavailableError

if TYPE_CHECKING:
    from .contracts import AuthenticatedSession

ANDROID_COMMUNITY_CLIENTS = ('cordial', 'nightcap', 'mocktail')


class AndroidCommunityBackend:
    """Community Android Roblox transport provider backed by live TLS secret recovery."""

    backend_id = BackendId.ANDROID_COMMUNITY

    def __init__(self, client: str) -> None:
        normalized = client.strip().lower()
        if normalized not in ANDROID_COMMUNITY_CLIENTS:
            msg = f'Unknown community Android runtime: {client}'
            raise ValueError(msg)
        self.client = normalized

    def probe(self) -> BackendProbe:
        """Return the current readiness status of the Android Community transport.

        Returns
        -------
        BackendProbe
            Backend readiness status and diagnostic message.
        """
        if sys.platform != 'linux':
            return BackendProbe(
                self.backend_id,
                BackendStatus.NOT_IMPLEMENTED,
                'Community Android transport integration requires Linux',
            )

        match = find_android_community_pid(self.client)
        if match is None:
            return BackendProbe(
                self.backend_id,
                BackendStatus.NOT_IMPLEMENTED,
                f'Community Android {self.client} process not found',
            )
        pid, client_name = match
        return BackendProbe(
            self.backend_id,
            BackendStatus.READY,
            f'Community Android {client_name} process active (PID {pid})',
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
