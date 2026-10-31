"""Shared cache records and export contracts."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class AssetEntry(TypedDict):
    id: str
    type: int
    type_name: str
    url: str
    size: int
    compressed: bool
    hash: str
    cached_at: str
    metadata: dict[str, object]
    detected_type: NotRequired[str]
    raw_size: NotRequired[int]


class CacheIndex(TypedDict):
    assets: dict[str, AssetEntry]
    version: str


class CacheStats(TypedDict):
    total_assets: int
    total_size: int
    types_count: dict[str, int]
