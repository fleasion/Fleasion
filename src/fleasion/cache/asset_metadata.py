"""Display metadata helpers shared by cache presentation adapters."""

from __future__ import annotations

from typing import Any


def asset_metadata_needs_resolution(info: dict[str, Any]) -> bool:
    """Return whether an asset still has display metadata to resolve."""
    if (
        info.get('resolved_name') is None
        or info.get('created_at') is None
        or info.get('updated_at') is None
    ):
        return True
    return info.get('creator_id') is not None and info.get('creator_name') is None
