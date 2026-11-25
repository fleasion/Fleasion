"""Placeholder for native macOS Roblox; feasibility remains unverified."""

from __future__ import annotations

from ._stub import UnimplementedBackend
from .contracts import BackendId, BackendStatus


class MacOSBackend(UnimplementedBackend):
    backend_id = BackendId.MACOS
    status = BackendStatus.RESEARCH_PENDING
    reason = 'Native macOS session transport feasibility is still under investigation'
