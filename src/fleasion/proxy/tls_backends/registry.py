"""Registry of optional, currently unavailable platform backends."""

from __future__ import annotations

from .android_community import AndroidCommunityBackend
from .contracts import BackendId, BackendProbe, SessionBackend
from .macos import MacOSBackend
from .rosemary import RosemaryBackend
from .sober import SoberBackend
from .windows import WindowsBackend


def create_backend(backend: BackendId, *, community_client: str | None = None) -> SessionBackend:
    """Create a platform placeholder without selecting an implicit fallback."""
    if backend is BackendId.WINDOWS:
        return WindowsBackend()
    if backend is BackendId.SOBER:
        return SoberBackend()
    if backend is BackendId.MACOS:
        return MacOSBackend()
    if backend is BackendId.ROSEMARY:
        return RosemaryBackend()
    if backend is BackendId.ANDROID_COMMUNITY:
        if community_client is None:
            msg = 'Community Android backend requires a specific runtime name'
            raise ValueError(msg)
        return AndroidCommunityBackend(community_client)
    msg = f'Unknown transport backend: {backend!r}'
    raise ValueError(msg)


def backend_probes() -> tuple[BackendProbe, ...]:
    """Read-only statuses; does not inspect or attach to client processes."""
    return tuple(
        create_backend(backend, community_client='cordial').probe() for backend in BackendId
    )
