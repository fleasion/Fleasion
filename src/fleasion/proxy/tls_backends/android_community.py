"""Placeholder shared by Cordial, Nightcap and Mocktail Android runtimes."""

from __future__ import annotations

from ._stub import UnimplementedBackend
from .contracts import BackendId

ANDROID_COMMUNITY_CLIENTS = ('cordial', 'nightcap', 'mocktail')


class AndroidCommunityBackend(UnimplementedBackend):
    backend_id = BackendId.ANDROID_COMMUNITY
    reason = 'Community Android runtime transport integration is not implemented'

    def __init__(self, client: str) -> None:
        normalized = client.strip().lower()
        if normalized not in ANDROID_COMMUNITY_CLIENTS:
            msg = f'Unknown community Android runtime: {client}'
            raise ValueError(msg)
        self.client = normalized
