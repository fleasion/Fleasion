"""Per-game profiles: switch replacement configs and filters when a game is joined.

Roblox asks ``gamejoin.roblox.com`` for a server every time it joins or
teleports into a place, and that request names the place.  The tracker below
watches those requests and tells the config manager which place is active, so
the next asset batch is resolved with that game's profile.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, TypedDict, cast
from urllib.parse import urlparse

from fleasion.rules.asset_filters import FilterSettings, normalize_filter_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from fleasion.proxy.server import ProxyFlow

GAMEJOIN_HOST = 'gamejoin.roblox.com'
JOIN_PATHS = frozenset(
    {
        '/v1/join-game',
        '/v1/join-game-instance',
        '/v1/join-private-game',
        '/v1/join-play-together-game',
        '/v1/join-reserved-game',
    }
)
MAX_PROFILE_NAME_LENGTH = 80
_MAX_PLACE_ID = 2**63 - 1


class GameProfile(TypedDict):
    """Replacement configs and filters used while one place is being played."""

    place_id: int
    name: str
    enabled: bool
    configs: list[str]
    """Configs to use instead of the normally enabled ones."""
    filters: FilterSettings | None
    """Filters for this game; ``None`` keeps the global filter settings."""


def parse_place_id(value: object) -> int | None:
    """Return a positive place ID from JSON or user input, else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            return None
        value = int(text)
    if isinstance(value, int) and 0 < value <= _MAX_PLACE_ID:
        return value
    return None


def make_profile(
    place_id: int,
    name: str = '',
    configs: Iterable[str] = (),
    filters: FilterSettings | None = None,
) -> GameProfile:
    return {
        'place_id': place_id,
        'name': name.strip()[:MAX_PROFILE_NAME_LENGTH] or f'Place {place_id}',
        'enabled': True,
        'configs': list(dict.fromkeys(configs)),
        'filters': filters,
    }


def normalize_game_profile(value: object) -> GameProfile | None:
    """Validate one profile from untrusted JSON."""
    if not isinstance(value, dict):
        return None
    raw = cast('dict[str, object]', value)
    place_id = parse_place_id(raw.get('place_id'))
    if place_id is None:
        return None
    name = raw.get('name')
    raw_configs = raw.get('configs')
    configs = (
        [item for item in cast('list[object]', raw_configs) if isinstance(item, str) and item]
        if isinstance(raw_configs, list)
        else []
    )
    raw_filters = raw.get('filters')
    profile = make_profile(
        place_id,
        name if isinstance(name, str) else '',
        configs,
        normalize_filter_settings(raw_filters) if raw_filters is not None else None,
    )
    profile['enabled'] = raw.get('enabled', True) is not False
    return profile


def normalize_game_profiles(value: object) -> list[GameProfile]:
    """Validate a profile list, keeping the first profile for each place."""
    if not isinstance(value, list):
        return []
    profiles: list[GameProfile] = []
    seen: set[int] = set()
    for item in cast('list[object]', value):
        profile = normalize_game_profile(item)
        if profile is not None and profile['place_id'] not in seen:
            seen.add(profile['place_id'])
            profiles.append(profile)
    return profiles


def place_id_from_join_request(path: str, body: bytes) -> int | None:
    """Return the place a gamejoin request is joining, if it is one."""
    if path not in JOIN_PATHS:
        return None
    try:
        payload: object = json.loads(body)
    except UnicodeDecodeError, ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return parse_place_id(cast('dict[str, object]', payload).get('placeId'))


class GameProfileTracker:
    """Proxy module interceptor that reports each place Roblox joins."""

    def __init__(self, on_place_joined: Callable[[int], None]) -> None:
        self._on_place_joined = on_place_joined
        self.last_place_id: int | None = None

    def request(self, flow: ProxyFlow) -> None:
        url = urlparse(flow.request.pretty_url)
        if url.hostname != GAMEJOIN_HOST:
            return
        place_id = place_id_from_join_request(url.path, flow.request.content)
        if place_id is None:
            return
        self.last_place_id = place_id
        self._on_place_joined(place_id)

    def response(self, flow: ProxyFlow) -> None:
        del flow
