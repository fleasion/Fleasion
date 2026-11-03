"""Notice when Roblox refuses Fleasion's certificate.

Roblox on Windows and macOS can verify TLS against a CA bundle compiled into
the client instead of ssl/cacert.pem. The patched bundle then has no effect:
every intercepted connection fails its handshake with an "unknown CA" alert
and Fleasion silently stops working. This turns those failures into one
clear signal the app can explain to the user.
"""

from __future__ import annotations

import ssl
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# OpenSSL reasons a TLS client sends when it does not trust the certificate
_REJECTION_REASONS = frozenset(
    {
        'TLSV1_ALERT_UNKNOWN_CA',
        'SSLV3_ALERT_CERTIFICATE_UNKNOWN',
        'SSLV3_ALERT_BAD_CERTIFICATE',
    }
)
_REJECTION_TEXT = ('unknown ca', 'certificate unknown', 'bad certificate')


def is_certificate_rejection(exc: BaseException) -> bool:
    """Whether a failed client handshake means the client distrusts our CA."""
    if not isinstance(exc, ssl.SSLError):
        return False
    if getattr(exc, 'reason', None) in _REJECTION_REASONS:
        return True
    text = str(exc).lower()
    return any(fragment in text for fragment in _REJECTION_TEXT)


class CertificateRejectionMonitor:
    """Report once when several rejections arrive close together.

    A single rejection can come from an unrelated app using the proxy, so
    ``threshold`` rejections within ``window`` seconds are required.
    """

    def __init__(
        self,
        on_rejected: Callable[[list[str]], None],
        *,
        threshold: int = 3,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_rejected = on_rejected
        self._threshold = threshold
        self._window = window
        self._clock = clock
        self._events: deque[tuple[float, str]] = deque()
        self._reported = False
        self._lock = threading.Lock()

    @property
    def reported(self) -> bool:
        return self._reported

    def record(self, host: str) -> None:
        with self._lock:
            if self._reported:
                return
            now = self._clock()
            self._events.append((now, host))
            while self._events and now - self._events[0][0] > self._window:
                self._events.popleft()
            if len(self._events) < self._threshold:
                return
            self._reported = True
            hosts = sorted({event_host for _when, event_host in self._events})
        self._on_rejected(hosts)
