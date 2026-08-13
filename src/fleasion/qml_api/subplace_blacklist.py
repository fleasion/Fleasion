"""QML settings bridge and proxy interceptor for subplace blocking."""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement

from ..utils.logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    type Clock = Callable[[], float]

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_JOIN_PATHS: Final = frozenset(
    {
        '/v1/join-game',
        '/v1/join-play-together-game',
        '/v1/join-game-instance',
        '/v1/join-reserved-game',
    }
)


def normalize_subplace_id(value: object) -> str | None:
    """Normalize one Roblox place identifier using the legacy integer rules."""
    try:
        return str(int(str(value).strip()))
    except TypeError, ValueError:
        return None


def parse_subplace_ids(raw_value: str) -> list[str]:
    """Parse comma, space, newline, or semicolon-separated place identifiers."""
    content = raw_value.replace('\n', ',').replace(';', ',').replace(' ', ',')
    parsed: list[str] = []
    for part in content.split(','):
        normalized = normalize_subplace_id(part)
        if normalized is not None:
            parsed.append(normalized)
    return parsed


def _sorted_ids(values: Iterable[str]) -> list[str]:
    return sorted(set(values), key=int)


@QmlElement
class SubplaceBlacklistApi(QObject):
    """Persist blacklist settings and short-circuit matching join requests."""

    blacklistChanged = Signal()
    modeChanged = Signal()
    bypassChanged = Signal()
    notificationRequested = Signal(str, str, str)

    def __init__(
        self,
        config_manager: Any,
        clock: Clock = time.time,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        configured = getattr(config_manager, 'subplace_blacklist', [])
        initial = parse_subplace_ids(','.join(str(value) for value in configured))
        configured_mode = str(getattr(config_manager, 'subplace_blacklist_mode', 'block'))
        self._config = config_manager
        self._clock: Final = clock
        self._lock = threading.Lock()
        self._ids = frozenset(initial)
        self._mode = 'stall' if configured_mode == 'stall' else 'block'
        self._bypass_until = 0.0
        self._blocked_log_at: dict[str, float] = {}
        self._bypass_timer = QTimer(self)
        self._bypass_timer.setInterval(100)
        self._bypass_timer.timeout.connect(self._tick_bypass)

    @Property(str, notify=blacklistChanged)
    def blacklistText(self) -> str:  # noqa: N802
        with self._lock:
            values = _sorted_ids(self._ids)
        return ', '.join(values)

    @Property(int, notify=blacklistChanged)
    def blacklistCount(self) -> int:  # noqa: N802
        with self._lock:
            return len(self._ids)

    @Property(str, notify=modeChanged)
    def mode(self) -> str:  # pyright: ignore[reportRedeclaration]
        with self._lock:
            return self._mode

    @mode.setter  # pyright: ignore[reportRedeclaration]
    def mode(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]
        normalized = 'stall' if value == 'stall' else 'block'
        with self._lock:
            if normalized == self._mode:
                return
            self._mode = normalized
        self._config.subplace_blacklist_mode = normalized
        self.modeChanged.emit()
        label = 'Infinitely stall' if normalized == 'stall' else 'Block immediately'
        log_buffer.log('subplace', f'Subplace blacklist mode: {label}')

    @Property(bool, notify=bypassChanged)
    def bypassActive(self) -> bool:  # noqa: N802
        return self._remaining_milliseconds() > 0

    @Property(int, notify=bypassChanged)
    def bypassMillisecondsRemaining(self) -> int:  # noqa: N802
        return self._remaining_milliseconds()

    @Slot(str, result=bool)
    def applyBlacklist(self, raw_value: str) -> bool:  # noqa: N802
        parsed = parse_subplace_ids(raw_value)
        with self._lock:
            self._ids = frozenset(parsed)
            values = _sorted_ids(self._ids)
        self._config.subplace_blacklist = values
        self.blacklistChanged.emit()
        count = len(values)
        if values:
            log_buffer.log(
                'subplace',
                f'Subplace blacklist updated: {count} ID(s) active - {", ".join(values)}',
            )
        else:
            log_buffer.log('subplace', 'Subplace blacklist cleared')
        self.notificationRequested.emit(
            'Subplace blacklist updated',
            f'{count} place ID(s) active',
            'success',
        )
        return True

    @Slot()
    def bypassForFiveSeconds(self) -> None:  # noqa: N802
        with self._lock:
            self._bypass_until = self._clock() + 5.0
        self._bypass_timer.start()
        self.bypassChanged.emit()
        log_buffer.log('subplace', 'Subplace blacklist bypass enabled for 5 seconds')
        self.notificationRequested.emit(
            'Blacklist bypass active',
            'Subplace joins are allowed for five seconds.',
            'info',
        )

    def request(self, flow: Any) -> None:
        """Block a blacklisted gamejoin request using the configured response mode."""
        url = str(flow.request.pretty_url)
        if 'gamejoin.roblox.com' not in url or urlparse(url).path not in _JOIN_PATHS:
            return
        try:
            payload = json.loads(flow.request.content)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        place_id = normalize_subplace_id(payload.get('placeId'))
        if place_id is None:
            return
        with self._lock:
            if place_id not in self._ids or self._clock() < self._bypass_until:
                return
            mode = self._mode
        attempt_id = str(payload.get('gameJoinAttemptId') or '')
        self._drop_request(flow, place_id, attempt_id, mode)

    def response(self, _flow: Any) -> None:
        """Accept the common interceptor interface; responses need no handling."""

    @Slot()
    def shutdown(self) -> None:
        self._bypass_timer.stop()

    @Slot()
    def _tick_bypass(self) -> None:
        active = self.bypassActive
        self.bypassChanged.emit()
        if not active:
            self._bypass_timer.stop()

    def _remaining_milliseconds(self) -> int:
        with self._lock:
            return max(0, int((self._bypass_until - self._clock()) * 1000))

    def _drop_request(self, flow: Any, place_id: str, attempt_id: str, mode: str) -> None:
        if mode == 'stall':
            status = 1
            message = ''
            log_interval = 10.0
        else:
            status = 12
            message = 'Teleport blocked by Subplace Blacklist.'
            log_interval = 5.0
        response = {
            'jobId': None,
            'status': status,
            'joinScriptUrl': None,
            'authenticationUrl': None,
            'authenticationTicket': None,
            'message': message,
            'joinScript': None,
            'queuePosition': 0,
        }
        flow.drop_request = True
        flow.drop_status_code = 200
        flow.drop_body = json.dumps(response, separators=(',', ':')).encode('utf-8')

        key = f'{place_id}:{attempt_id}'
        now = self._clock()
        with self._lock:
            last = self._blocked_log_at.get(key, 0.0)
            if now - last < log_interval:
                return
            self._blocked_log_at[key] = now
            if len(self._blocked_log_at) > 512:
                cutoff = now - 30.0
                self._blocked_log_at = {
                    value: timestamp
                    for value, timestamp in self._blocked_log_at.items()
                    if timestamp >= cutoff
                }
        log_buffer.log(
            'subplace',
            f'Blocked join request to blacklisted subplace ID: {place_id}',
        )


class GameJoinInterceptorChain:
    """Apply focused gamejoin interceptors in a deterministic order."""

    def __init__(self, *interceptors: Any) -> None:
        self._interceptors = interceptors

    def request(self, flow: Any) -> None:
        for interceptor in self._interceptors:
            interceptor.request(flow)

    def response(self, flow: Any) -> None:
        for interceptor in self._interceptors:
            interceptor.response(flow)


__all__ = [
    'GameJoinInterceptorChain',
    'SubplaceBlacklistApi',
    'normalize_subplace_id',
    'parse_subplace_ids',
]
