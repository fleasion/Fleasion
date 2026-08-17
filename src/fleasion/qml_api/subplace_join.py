"""Subplace join preparation and gamejoin interception without visual dependencies."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, Final, Protocol
from urllib.parse import urlparse

import requests

from ..utils.logging import log_buffer

_NORMAL_JOIN_PATHS: Final = frozenset(
    {
        '/v1/join-game',
        '/v1/join-play-together-game',
        '/v1/join-game-instance',
    }
)
_JOIN_PATHS: Final = _NORMAL_JOIN_PATHS | {
    '/v1/join-private-game',
    '/v1/join-reserved-game',
}
_ARM_SECONDS: Final = 90.0


def _normalized_place_id(value: object) -> str:
    text = str(value or '').strip()
    if not text.isdecimal():
        return ''
    normalized = str(int(text))
    return normalized if normalized != '0' else ''


class JoinSession(Protocol):
    trust_env: bool
    headers: Any
    cookies: Any

    def post(self, url: str, **kwargs: Any) -> Any: ...


class SubplaceJoinCoordinator:
    """Pre-seed a universe root and rewrite the next matching join request."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], JoinSession] = requests.Session,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._armed = False
        self._active_attempt_id = ''
        self._job_id = ''
        self._place_id = ''
        self._expires_at = 0.0

    def prepare(
        self,
        place_id: str,
        root_place_id: str,
        job_id: str,
        cookie: str | None,
    ) -> bool:
        """Pre-seed a non-root place and arm interception for its launch."""
        normalized_place = _normalized_place_id(place_id)
        normalized_root = _normalized_place_id(root_place_id)
        seeded = True
        if (
            normalized_root.isdecimal()
            and normalized_place.isdecimal()
            and normalized_root != normalized_place
        ):
            seeded = self.preseed_root(normalized_root, cookie)
        self.arm(job_id, normalized_place)
        return seeded

    def preseed_root(self, root_place_id: str, cookie: str | None) -> bool:
        normalized_root = _normalized_place_id(root_place_id)
        if not normalized_root or not cookie:
            return False
        session = self._session_factory()
        session.trust_env = False
        session.headers.update(
            {
                'User-Agent': 'Roblox/WinInet',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Referer': 'https://www.roblox.com/',
                'Origin': 'https://www.roblox.com',
            }
        )
        session.cookies.set(
            '.ROBLOSECURITY',
            cookie,
            domain='.roblox.com',
            secure=True,
        )
        payload = {
            'placeId': int(normalized_root),
            'isTeleport': True,
            'isImmersiveAdsTeleport': False,
            'gameJoinAttemptId': str(uuid.uuid4()),
        }
        try:
            response = session.post(
                'https://gamejoin.roblox.com/v1/join-game',
                json=payload,
                timeout=15,
                allow_redirects=False,
            )
            succeeded = response.status_code == 200 and response.json().get('status') == 2
        except Exception as exc:
            log_buffer.log('subplace', f'Root-place pre-seed failed: {exc}')
            return False
        log_buffer.log(
            'subplace',
            f'Root-place pre-seed {"succeeded" if succeeded else "failed"}: {normalized_root}',
        )
        return succeeded

    def arm(self, job_id: str = '', place_id: str = '') -> None:
        with self._lock:
            self._armed = True
            self._active_attempt_id = ''
            self._job_id = job_id.strip()
            self._place_id = _normalized_place_id(place_id)
            self._expires_at = self._clock() + _ARM_SECONDS

    def cancel(self) -> None:
        with self._lock:
            self._armed = False
            self._active_attempt_id = ''
            self._job_id = ''
            self._place_id = ''
            self._expires_at = 0.0

    def request(self, flow: Any) -> None:
        parsed = urlparse(str(flow.request.pretty_url))
        if parsed.hostname != 'gamejoin.roblox.com' or parsed.path not in _JOIN_PATHS:
            return
        try:
            payload = json.loads(flow.request.content)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        attempt_id = str(payload.get('gameJoinAttemptId') or '<unidentified>')
        payload_place_id = _normalized_place_id(payload.get('placeId'))
        with self._lock:
            if self._clock() >= self._expires_at:
                self._armed = False
                self._active_attempt_id = ''
                self._job_id = ''
                self._place_id = ''
            if self._armed and self._place_id and payload_place_id != self._place_id:
                return
            if self._armed:
                self._armed = False
                self._active_attempt_id = attempt_id
            elif not self._active_attempt_id or self._active_attempt_id != attempt_id:
                return
            job_id = self._job_id
        payload.setdefault('isTeleport', True)
        if job_id and parsed.path in _NORMAL_JOIN_PATHS:
            payload['gameId'] = job_id
            flow.request.url = 'https://gamejoin.roblox.com/v1/join-game-instance'
        flow.request.raw_content = json.dumps(payload, separators=(',', ':')).encode('utf-8')

    def response(self, flow: Any) -> None:
        parsed = urlparse(str(flow.request.pretty_url))
        if parsed.hostname != 'gamejoin.roblox.com' or parsed.path not in _JOIN_PATHS:
            return
        with self._lock:
            if not self._active_attempt_id:
                return
        response = getattr(flow, 'response', None)
        if response is None:
            return
        try:
            payload = json.loads(response.content)
        except Exception:
            payload = {}
        if payload.get('status') == 2 or getattr(response, 'status_code', 0) >= 400:
            self.cancel()


__all__ = ['SubplaceJoinCoordinator']
