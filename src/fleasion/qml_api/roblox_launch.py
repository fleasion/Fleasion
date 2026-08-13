"""Roblox authentication and launch helpers independent of any visual toolkit."""

from __future__ import annotations

import random
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

import requests

from ..utils.windows import launch_as_standard_user

LaunchCallable = Callable[[str], bool]


class RobloxAccountClient:
    """Validate cookies and request account-scoped authentication tickets."""

    def __init__(self, session_factory: Callable[[], requests.Session] | None = None) -> None:
        self._session_factory = session_factory or requests.Session

    def validate(self, cookie: str) -> dict[str, str]:
        cleaned = cookie.strip()
        if not cleaned:
            raise ValueError('Paste a .ROBLOSECURITY cookie first')
        session = self._new_session(cleaned)
        response = session.get('https://users.roblox.com/v1/users/authenticated', timeout=10)
        if response.status_code != 200:
            raise ValueError(f'Roblox rejected that cookie (HTTP {response.status_code})')
        payload = response.json()
        username = str(payload.get('name') or '').strip()
        if not username:
            raise ValueError('Roblox did not return a username for that cookie')
        return {
            'username': username,
            'userId': str(payload.get('id') or ''),
            'cookie': cleaned,
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
            timeout=10,
        )
        token = response.headers.get('x-csrf-token')
        if response.status_code == 403 and token:
            headers['X-CSRF-TOKEN'] = token
            response = session.post(
                'https://auth.roblox.com/v1/authentication-ticket',
                headers=headers,
                json={},
                timeout=10,
            )
        ticket = response.headers.get('rbx-authentication-ticket', '')
        if response.status_code != 200 or not ticket:
            raise RuntimeError('Roblox did not issue an authentication ticket')
        return ticket

    def _new_session(self, cookie: str) -> requests.Session:
        session = self._session_factory()
        session.trust_env = False
        session.proxies = {}
        session.headers['User-Agent'] = 'Fleasion/2 Account Manager'
        session.cookies.set('.ROBLOSECURITY', cookie, domain='.roblox.com')
        return session


class AccountLauncher:
    """Launch Roblox with an account ticket and optional place/server target."""

    def __init__(
        self,
        client: RobloxAccountClient | None = None,
        launcher: LaunchCallable | None = None,
    ) -> None:
        self._client = client or RobloxAccountClient()
        self._launcher = launcher or launch_as_standard_user

    def launch(self, cookie: str, place_id: str = '', job_id: str = '') -> bool:
        ticket = self._client.authentication_ticket(cookie)
        target = build_ticket_uri(ticket, place_id=place_id, job_id=job_id)
        return self._launcher(target)


def build_ticket_uri(ticket: str, place_id: str = '', job_id: str = '') -> str:
    """Build a deterministic-shape Roblox Player authentication URI."""
    launch_mode = 'play' if place_id else 'app'
    pieces = [
        'roblox-player:1',
        f'launchmode:{launch_mode}',
        f'gameinfo:{ticket}',
        f'launchtime:{int(time.time() * 1000)}',
    ]
    if place_id:
        tracker_id = random.randint(10_000_000_000, 99_999_999_999)
        params: dict[str, Any] = {
            'request': 'RequestGameJob' if job_id else 'RequestGame',
            'browserTrackerId': tracker_id,
            'placeId': place_id,
            'joinAttemptId': str(uuid.uuid4()),
        }
        if job_id:
            params['gameId'] = job_id
        launcher_url = 'https://www.roblox.com/Game/PlaceLauncher.ashx?' + urlencode(params)
        pieces.extend(
            [
                f'placelauncherurl:{quote(launcher_url, safe="")}',
                f'browsertrackerid:{tracker_id}',
            ]
        )
    pieces.extend(['robloxLocale:en_us', 'gameLocale:en_us', 'channel:', 'LaunchExp:InApp'])
    return '+'.join(pieces)
