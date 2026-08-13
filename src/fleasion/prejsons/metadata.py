"""Roblox metadata enrichment for community preset cards."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..utils.http import http_get

if TYPE_CHECKING:
    from collections.abc import Callable

    type FetchBytes = Callable[[str, int], bytes]


@dataclass(frozen=True, slots=True)
class PresetMetadata:
    """Optional live metadata for a Roblox place."""

    name: str = ''
    created: str = ''
    updated: str = ''
    thumbnail_url: str = ''


class RobloxPresetMetadataClient:
    """Fetch and cache metadata used by preset cards."""

    def __init__(self, fetch: FetchBytes = http_get) -> None:
        self._fetch = fetch
        self._cache: dict[int, PresetMetadata] = {}
        self._lock = threading.Lock()

    def fetch(self, place_id: int) -> PresetMetadata:
        """Return the best available metadata for a place.

        Parameters
        ----------
        place_id
            Roblox place identifier.

        Returns
        -------
        PresetMetadata
            Metadata fields that could be resolved. Individual endpoint
            failures leave their corresponding fields empty.
        """
        with self._lock:
            cached = self._cache.get(place_id)
        if cached is not None:
            return cached

        universe_id = self._universe_id(place_id)
        game = self._game(universe_id) if universe_id is not None else {}
        metadata = PresetMetadata(
            name=_string_value(game.get('name')),
            created=_string_value(game.get('created')),
            updated=_string_value(game.get('updated')),
            thumbnail_url=self._thumbnail_url(place_id),
        )
        with self._lock:
            self._cache[place_id] = metadata
        return metadata

    def _universe_id(self, place_id: int) -> int | None:
        try:
            data = _json_object(
                self._fetch(
                    f'https://apis.roblox.com/universes/v1/places/{place_id}/universe',
                    10,
                )
            )
            value = data.get('universeId')
            return int(value) if isinstance(value, int | float | str) else None
        except OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError:
            return None

    def _game(self, universe_id: int) -> dict[str, object]:
        try:
            data = _json_object(
                self._fetch(
                    f'https://games.roblox.com/v1/games?universeIds={universe_id}',
                    10,
                )
            )
            entries = data.get('data')
            if isinstance(entries, list) and entries and isinstance(entries[0], dict):
                return {str(key): value for key, value in entries[0].items()}
        except OSError, RuntimeError, json.JSONDecodeError:
            pass
        return {}

    def _thumbnail_url(self, place_id: int) -> str:
        try:
            data = _json_object(
                self._fetch(
                    'https://thumbnails.roblox.com/v1/places/gameicons'
                    f'?placeIds={place_id}&size=512x512&format=Png&isCircular=false',
                    10,
                )
            )
            entries = data.get('data')
            if isinstance(entries, list) and entries and isinstance(entries[0], dict):
                return _string_value(entries[0].get('imageUrl'))
        except OSError, RuntimeError, json.JSONDecodeError:
            pass
        return ''


def _json_object(raw: bytes) -> dict[str, object]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object.')
    return {str(key): item for key, item in value.items()}


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ''


__all__ = ['PresetMetadata', 'RobloxPresetMetadataClient']
