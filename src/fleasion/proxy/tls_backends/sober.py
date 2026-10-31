"""Placeholder for the separately maintained Sober Android runtime."""

from __future__ import annotations

from ._stub import UnimplementedBackend
from .contracts import BackendId


class SoberBackend(UnimplementedBackend):
    backend_id = BackendId.SOBER
    reason = 'Sober transport integration is not implemented'
