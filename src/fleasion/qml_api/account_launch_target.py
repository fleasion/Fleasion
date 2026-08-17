"""Validated Roblox account-launch targets without network access."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Protocol
from urllib.parse import parse_qs, urlparse

_MAX_TARGET_LENGTH: Final = 2048
_MAX_CODE_LENGTH: Final = 512
_ROBLOX_WEB_HOSTS: Final = frozenset({'roblox.com', 'www.roblox.com'})
_UUID_RE: Final = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)
_LAUNCH_FRAGMENT_RE: Final = re.compile(
    r'(?:^|[;:\s])Join(?:Place|Game|PrivateGame)\s*[=:]',
    re.IGNORECASE,
)


class CancelEvent(Protocol):
    """Minimal cancellation event used by launch workers."""

    def is_set(self) -> bool: ...


class AccountLaunchCancelled(RuntimeError):
    """Raised when a queued account launch is cancelled safely."""


@dataclass(frozen=True, slots=True)
class RobloxTarget:
    """A validated direct game/private target or unresolved share target."""

    place_id: str = ''
    link_code: str = ''
    share_code: str = ''
    share_type: str = 'Server'

    @property
    def is_share(self) -> bool:
        return bool(self.share_code)


@dataclass(frozen=True, slots=True)
class AccountLaunchRequest:
    """Validated account launch fields before remote share-link resolution."""

    target: RobloxTarget
    subplace_id: str = ''
    job_id: str = ''


@dataclass(frozen=True, slots=True)
class ResolvedLaunchRequest:
    """Concrete launch coordinates resolved without exposing account credentials."""

    root_place_id: str = ''
    launch_place_id: str = ''
    link_code: str = ''
    job_id: str = ''

    @property
    def is_private_server(self) -> bool:
        return bool(self.link_code)

    @property
    def is_distinct_subplace(self) -> bool:
        return bool(
            self.root_place_id
            and self.launch_place_id
            and self.root_place_id != self.launch_place_id
        )


def normalize_place_id(value: object) -> str:
    """Return a canonical positive Roblox Place ID."""
    text = str(value or '').strip()
    if not text.isdecimal():
        raise ValueError('Place IDs must be positive whole numbers.')
    normalized = str(int(text))
    if normalized == '0':
        raise ValueError('Place IDs must be greater than zero.')
    return normalized


def normalize_code(value: object, label: str) -> str:
    """Validate a bounded Roblox share, link, or access code."""
    text = str(value or '').strip()
    if not text or len(text) > _MAX_CODE_LENGTH or any(ord(character) < 32 for character in text):
        raise ValueError(f'The Roblox {label} is invalid.')
    return text


def normalize_job_id(value: str) -> str:
    """Extract and validate a Roblox server Job ID."""
    text = value.strip()
    if not text:
        return ''
    match = _UUID_RE.search(text)
    if match is not None:
        return match.group(0)
    if _LAUNCH_FRAGMENT_RE.search(text):
        raise ValueError('Paste only the server Job ID, not a Roblox launcher command.')
    if len(text) > 128 or any(character.isspace() or ord(character) < 32 for character in text):
        raise ValueError('The server Job ID is invalid.')
    return text


def parse_roblox_target(value: str, *, allow_share: bool = True) -> RobloxTarget:
    """Parse a numeric Place ID or an official Roblox game/private/share URL."""
    text = value.strip()
    if not text:
        return RobloxTarget()
    if len(text) > _MAX_TARGET_LENGTH or any(ord(character) < 32 for character in text):
        raise ValueError('The Roblox target is too long or contains invalid characters.')
    if text.isdecimal():
        return RobloxTarget(place_id=normalize_place_id(text))
    if '://' not in text and text.casefold().startswith(('roblox.com/', 'www.roblox.com/')):
        text = f'https://{text}'

    parsed = urlparse(text)
    hostname = (parsed.hostname or '').rstrip('.').casefold()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('The Roblox URL contains an invalid port.') from exc
    if (
        parsed.scheme.casefold() != 'https'
        or hostname not in _ROBLOX_WEB_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ValueError('Use a numeric Place ID or an official HTTPS roblox.com link.')

    try:
        query = parse_qs(parsed.query, max_num_fields=32)
    except ValueError as exc:
        raise ValueError('The Roblox URL contains too many query parameters.') from exc
    path_parts = [part for part in parsed.path.split('/') if part]
    if len(path_parts) >= 2 and path_parts[0].casefold() == 'games':
        place_id = normalize_place_id(path_parts[1])
        link_values = query.get('privateServerLinkCode', [])
        link_code = normalize_code(link_values[0], 'private-server link code') if link_values else ''
        return RobloxTarget(place_id=place_id, link_code=link_code)

    if parsed.path.rstrip('/').casefold() == '/share':
        if not allow_share:
            raise ValueError('A share link can only be used as the main experience target.')
        share_values = query.get('code', [])
        if not share_values:
            raise ValueError('The Roblox share link does not contain a code.')
        share_type = str((query.get('type') or ['Server'])[0]).strip()
        if share_type.casefold() != 'server':
            raise ValueError('Only Roblox server share links can be launched.')
        return RobloxTarget(
            share_code=normalize_code(share_values[0], 'share code'),
            share_type='Server',
        )

    raise ValueError('Use a Roblox game, private-server, or server share link.')


def parse_account_launch_request(
    target: str,
    job_id: str = '',
    subplace: str = '',
) -> AccountLaunchRequest:
    """Validate the account launch form without performing network access."""
    parsed_target = parse_roblox_target(target)
    parsed_subplace = parse_roblox_target(subplace, allow_share=False)
    normalized_job = normalize_job_id(job_id)
    if parsed_subplace.link_code:
        raise ValueError('Use private-server links in the main experience field.')
    if normalized_job and parsed_target.link_code:
        raise ValueError('A Job ID cannot be combined with a private-server link.')
    if normalized_job and not (parsed_target.place_id or parsed_target.is_share or parsed_subplace.place_id):
        raise ValueError('Enter a Place ID or Roblox game link before a Job ID.')
    return AccountLaunchRequest(parsed_target, parsed_subplace.place_id, normalized_job)


def raise_if_cancelled(cancel_event: CancelEvent | None) -> None:
    """Stop a launch at a safe boundary after cancellation."""
    if cancel_event is not None and cancel_event.is_set():
        raise AccountLaunchCancelled('Account launch cancelled')


__all__ = [
    'AccountLaunchCancelled',
    'AccountLaunchRequest',
    'CancelEvent',
    'ResolvedLaunchRequest',
    'RobloxTarget',
    'normalize_code',
    'normalize_job_id',
    'normalize_place_id',
    'parse_account_launch_request',
    'parse_roblox_target',
    'raise_if_cancelled',
]
