"""Bounded Roblox account API access for launch preparation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Final, cast
from urllib.parse import quote

import requests

from .account_launch_target import (
    AccountLaunchRequest,
    CancelEvent,
    ResolvedLaunchRequest,
    RobloxTarget,
    normalize_code,
    normalize_place_id,
    raise_if_cancelled,
)

_REQUEST_TIMEOUT: Final = 10
_MAX_RESPONSE_BYTES: Final = 512 * 1024
_ACCESS_CODE_PATTERNS: Final = (
    re.compile(r"Roblox\.GameLauncher\.joinPrivateGame\(\d+,\s*'([\w-]+)'"),
    re.compile(r'Roblox\.GameLauncher\.joinPrivateGame\(\d+,\s*"([\w-]+)"'),
    re.compile(r'"accessCode"\s*:\s*"([\w-]{36})"'),
)


def _response_header(response: Any, name: str) -> str:
    headers = getattr(response, 'headers', {})
    for key, value in headers.items():
        if str(key).casefold() == name.casefold():
            return str(value)
    return ''


def _close_response(response: Any) -> None:
    close = getattr(response, 'close', None)
    if callable(close):
        close()


def _bounded_response_bytes(response: Any) -> bytes:
    content_length = _response_header(response, 'content-length')
    if content_length:
        try:
            too_large = int(content_length) > _MAX_RESPONSE_BYTES
        except ValueError:
            too_large = False
        if too_large:
            raise ValueError('Roblox returned an unexpectedly large response.')

    iterator = getattr(response, 'iter_content', None)
    if callable(iterator):
        chunks: list[bytes] = []
        total = 0
        for chunk in cast('Iterable[bytes]', iterator(chunk_size=16 * 1024)):
            if not chunk:
                continue
            data = bytes(chunk)
            total += len(data)
            if total > _MAX_RESPONSE_BYTES:
                raise ValueError('Roblox returned an unexpectedly large response.')
            chunks.append(data)
        return b''.join(chunks)

    content = getattr(response, 'content', b'')
    data = bytes(content) if isinstance(content, bytes | bytearray) else str(content).encode()
    if len(data) > _MAX_RESPONSE_BYTES:
        raise ValueError('Roblox returned an unexpectedly large response.')
    return data


def _response_json(response: Any) -> Mapping[str, Any]:
    try:
        body = _bounded_response_bytes(response)
        if body:
            payload = json.loads(body)
        else:
            payload = response.json()
            if len(json.dumps(payload, separators=(',', ':')).encode()) > _MAX_RESPONSE_BYTES:
                raise ValueError('Roblox returned an unexpectedly large response.')
    finally:
        _close_response(response)
    if not isinstance(payload, Mapping):
        raise ValueError('Roblox returned malformed account data.')
    return payload


class RobloxAccountClient:
    """Validate cookies and resolve account-scoped Roblox launch data."""

    def __init__(self, session_factory: Callable[[], requests.Session] | None = None) -> None:
        self._session_factory = session_factory or requests.Session

    def validate(self, cookie: str) -> dict[str, str]:
        cleaned = cookie.strip()
        if not cleaned:
            raise ValueError('Paste a .ROBLOSECURITY cookie first')
        response = self._new_session(cleaned).get(
            'https://users.roblox.com/v1/users/authenticated',
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        if response.status_code != 200:
            _close_response(response)
            raise ValueError(f'Roblox rejected that cookie (HTTP {response.status_code})')
        payload = _response_json(response)
        username = str(payload.get('name') or '').strip()
        if not username:
            raise ValueError('Roblox did not return a username for that cookie')
        return {
            'username': username,
            'userId': str(payload.get('id') or ''),
        }

    def authentication_ticket(self, cookie: str) -> str:
        session = self._new_session(cookie)
        headers = {
            'Referer': 'https://www.roblox.com',
            'Content-Type': 'application/json',
        }
        response = session.post(
            'https://auth.roblox.com/v1/authentication-ticket',
            headers=headers,
            json={},
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        token = _response_header(response, 'x-csrf-token')
        if response.status_code == 403 and token:
            _close_response(response)
            headers['X-CSRF-TOKEN'] = token
            response = session.post(
                'https://auth.roblox.com/v1/authentication-ticket',
                headers=headers,
                json={},
                timeout=_REQUEST_TIMEOUT,
                allow_redirects=False,
            )
        ticket = _response_header(response, 'rbx-authentication-ticket')
        status_code = int(response.status_code)
        _close_response(response)
        if status_code != 200 or not ticket or len(ticket) > 4096:
            raise RuntimeError('Roblox did not issue an authentication ticket')
        return ticket

    def resolve_share_target(
        self,
        cookie: str,
        target: RobloxTarget,
        cancel_event: CancelEvent | None = None,
    ) -> RobloxTarget:
        if not target.is_share:
            return target
        raise_if_cancelled(cancel_event)
        session = self._new_session(cookie)
        session.headers.update(
            {
                'Referer': 'https://www.roblox.com/',
                'Accept': 'application/json',
                'Content-Type': 'application/json;charset=UTF-8',
            }
        )
        body = {'linkId': target.share_code, 'linkType': target.share_type}
        response = session.post(
            'https://apis.roblox.com/sharelinks/v1/resolve-link',
            json=body,
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        token = _response_header(response, 'x-csrf-token')
        if response.status_code == 403 and token:
            _close_response(response)
            session.headers['X-CSRF-TOKEN'] = token
            raise_if_cancelled(cancel_event)
            response = session.post(
                'https://apis.roblox.com/sharelinks/v1/resolve-link',
                json=body,
                timeout=_REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
        if response.status_code != 200:
            status_code = response.status_code
            _close_response(response)
            raise ValueError(f'Roblox could not resolve that share link (HTTP {status_code}).')
        payload = _response_json(response)
        candidates = [payload]
        for key in ('privateServerInviteData', 'privateServerData', 'gameDetails', 'serverData'):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                candidates.append(nested)
        place_id = ''
        link_code = ''
        for candidate in candidates:
            raw_place_id = candidate.get('placeId') or candidate.get('rootPlaceId')
            raw_link_code = (
                candidate.get('privateServerLinkCode')
                or candidate.get('linkCode')
                or candidate.get('accessCode')
            )
            if raw_place_id and not place_id:
                place_id = normalize_place_id(raw_place_id)
            if raw_link_code and not link_code:
                link_code = normalize_code(raw_link_code, 'private-server link code')
        if not place_id or not link_code:
            raise ValueError('The Roblox share link did not resolve to a private server.')
        return RobloxTarget(place_id=place_id, link_code=link_code)

    def private_server_access_code(
        self,
        cookie: str,
        place_id: str,
        link_code: str,
        cancel_event: CancelEvent | None = None,
    ) -> str:
        normalized_place = normalize_place_id(place_id)
        normalized_link = normalize_code(link_code, 'private-server link code')
        session = self._new_session(cookie)
        attempts: tuple[tuple[str, dict[str, Any]], ...] = (
            (
                'https://games.roblox.com/v1/private-servers',
                {'params': {'serverLinkCode': normalized_link}},
            ),
            (
                f'https://games.roblox.com/v1/private-servers/{quote(normalized_link, safe="")}',
                {},
            ),
        )
        for url, options in attempts:
            raise_if_cancelled(cancel_event)
            try:
                response = session.get(
                    url,
                    timeout=_REQUEST_TIMEOUT,
                    allow_redirects=False,
                    stream=True,
                    **options,
                )
            except requests.RequestException:
                continue
            if response.status_code != 200:
                _close_response(response)
                continue
            payload = _response_json(response)
            access_code = payload.get('accessCode') or payload.get('vipServerAccessCode')
            if access_code:
                return normalize_code(access_code, 'private-server access code')

        raise_if_cancelled(cancel_event)
        try:
            response = session.get(
                f'https://www.roblox.com/games/{normalized_place}',
                params={'privateServerLinkCode': normalized_link},
                headers={'Referer': 'https://www.roblox.com/games/'},
                timeout=_REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException:
            return normalized_link
        if response.status_code != 200:
            _close_response(response)
            return normalized_link
        try:
            page = _bounded_response_bytes(response).decode('utf-8', errors='replace')
        finally:
            _close_response(response)
        for pattern in _ACCESS_CODE_PATTERNS:
            match = pattern.search(page)
            if match is not None:
                return normalize_code(match.group(1), 'private-server access code')
        return normalized_link

    def resolve_launch_request(
        self,
        cookie: str,
        request: AccountLaunchRequest,
        cancel_event: CancelEvent | None = None,
    ) -> ResolvedLaunchRequest:
        target = self.resolve_share_target(cookie, request.target, cancel_event)
        if target.link_code and request.job_id:
            raise ValueError('A Job ID cannot be combined with a private-server link.')
        launch_place_id = request.subplace_id or target.place_id
        root_place_id = target.place_id or launch_place_id
        return ResolvedLaunchRequest(
            root_place_id=root_place_id,
            launch_place_id=launch_place_id,
            link_code=target.link_code,
            job_id=request.job_id,
        )

    def _new_session(self, cookie: str) -> requests.Session:
        session = self._session_factory()
        session.trust_env = False
        session.proxies = {}
        session.headers['User-Agent'] = 'Fleasion/2 Account Manager'
        session.cookies.set(
            '.ROBLOSECURITY',
            cookie,
            domain='.roblox.com',
            secure=True,
        )
        return session


__all__ = ['RobloxAccountClient']
