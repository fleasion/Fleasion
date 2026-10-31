"""Placeholder for the macOS Roblox runtime hosted through Rosemary on Linux."""

from __future__ import annotations

from ._stub import UnimplementedBackend
from .contracts import BackendId


class RosemaryBackend(UnimplementedBackend):
    backend_id = BackendId.ROSEMARY
    reason = 'Rosemary transport integration is not implemented'
