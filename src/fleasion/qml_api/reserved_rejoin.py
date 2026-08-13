"""Reserved-server capture and rejoin interceptor with no visual dependencies."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse


StateCallback = Callable[[str, str, float], None]


class ReservedRejoinInterceptor:
    """Capture reserved-server credentials and redirect the next join request."""

    _JOIN_PATHS = {
        '/v1/join-game',
        '/v1/join-play-together-game',
        '/v1/join-game-instance',
    }
    _RESERVED_PATH = '/v1/join-reserved-game'

    def __init__(self, on_state_changed: StateCallback | None = None) -> None:
        self._lock = threading.Lock()
        self._place_id = ''
        self._access_code = ''
        self._session_id = ''
        self._expires_at = 0.0
        self._armed = False
        self._active_attempt_id = ''
        self._on_state_changed = on_state_changed

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                'placeId': self._place_id,
                'accessCode': self._access_code,
                'expiresAt': self._expires_at,
                'armed': self._armed,
            }

    def set_credentials(self, place_id: str, access_code: str) -> None:
        normalized_place = place_id.strip()
        normalized_code = access_code.strip()
        with self._lock:
            self._place_id = normalized_place
            self._access_code = normalized_code
            self._expires_at = time.time() + 300 if normalized_place and normalized_code else 0.0
        self._notify()

    def arm(self) -> bool:
        with self._lock:
            if not self._place_id or not self._access_code or self._expires_at <= time.time():
                return False
            self._armed = True
            self._active_attempt_id = ''
        self._notify()
        return True

    def request(self, flow: Any) -> None:
        url = str(flow.request.pretty_url)
        if 'gamejoin.roblox.com' not in url:
            return
        path = urlparse(url).path
        if path == self._RESERVED_PATH:
            self._capture(flow)
            return
        if path not in self._JOIN_PATHS:
            return
        try:
            payload = json.loads(flow.request.content)
        except Exception:
            return
        attempt_id = str(payload.get('gameJoinAttemptId') or '')
        with self._lock:
            if self._armed:
                self._armed = False
                self._active_attempt_id = attempt_id
            elif not self._active_attempt_id or attempt_id != self._active_attempt_id:
                return
            place_id = self._place_id
            access_code = self._access_code
            session_id = self._session_id
            expired = self._expires_at <= time.time()
        if not place_id or not access_code or expired:
            self._clear_active()
            return
        replacement = {
            'placeId': place_id,
            'accessCode': access_code,
            'isTeleport': True,
            'isImmersiveAdsTeleport': False,
        }
        flow.request.url = 'https://gamejoin.roblox.com/v1/join-reserved-game'
        flow.request.raw_content = json.dumps(replacement, separators=(',', ':')).encode('utf-8')
        if session_id:
            flow.request.headers['Roblox-Session-Id'] = session_id

    def response(self, flow: Any) -> None:
        if 'gamejoin.roblox.com' not in str(flow.request.pretty_url):
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
            self._clear_active()
            return
        if (
            payload.get('status') == 2
            or payload.get('joinScriptUrl')
            or response.status_code >= 400
        ):
            self._clear_active()

    def _capture(self, flow: Any) -> None:
        try:
            payload = json.loads(flow.request.content)
        except Exception:
            return
        place_id = str(payload.get('placeId') or '').strip()
        access_code = str(payload.get('accessCode') or '').strip()
        if not place_id or not access_code:
            return
        session_id = str(flow.request.headers.get('Roblox-Session-Id', '') or '')
        with self._lock:
            self._place_id = place_id
            self._access_code = access_code
            self._session_id = session_id
            self._expires_at = time.time() + 300
        self._notify()

    def _clear_active(self) -> None:
        with self._lock:
            self._armed = False
            self._active_attempt_id = ''
        self._notify()

    def _notify(self) -> None:
        callback = self._on_state_changed
        if callback is None:
            return
        with self._lock:
            snapshot = (self._place_id, self._access_code, self._expires_at)
        callback(*snapshot)
