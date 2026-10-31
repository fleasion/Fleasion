"""Placeholder for the native Windows Roblox session transport."""

from __future__ import annotations

from ._stub import UnimplementedBackend
from .contracts import BackendId


class WindowsBackend(UnimplementedBackend):
    backend_id = BackendId.WINDOWS
    reason = 'Windows transport integration is not implemented'
