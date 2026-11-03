"""Bounded-lifetime traffic keys for local splice connections."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from typing import TYPE_CHECKING

from .android_community_keys import find_android_community_pid, scan as scan_android_community
from .live_keys import find_roblox_pid, scan
from .rosemary_keys import find_rosemary_pid, scan as scan_rosemary

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

type ConnectionSecrets = dict[str, bytes]
type SecretMap = dict[str, ConnectionSecrets]

KEYLOG_FIELD_COUNT = 3
CLIENT_RANDOM_SIZE = 32
MIN_REFRESH_INTERVAL = 0.1
MAX_CACHED_CONNECTIONS = 4096


def _parse_keylog_line(line: str, keys: SecretMap) -> None:
    """Parse one line of NSS keylog format."""
    parts = line.split()
    if len(parts) != KEYLOG_FIELD_COUNT or parts[0].startswith('#'):
        return
    label, random_hex, value_hex = parts
    try:
        secret = bytes.fromhex(value_hex)
        client_random = bytes.fromhex(random_hex)
    except ValueError:
        return
    if len(client_random) == CLIENT_RANDOM_SIZE and len(secret) in {32, 48}:
        keys.setdefault(random_hex.lower(), {})[label] = secret


class KeyProvider:
    """Provider for TLS traffic keys via keylog or memory scanner."""

    def __init__(self, keylog: Path | None = None) -> None:
        self.keylog = keylog
        self._keys: SecretMap = {}
        self._lock = asyncio.Lock()
        self._last_refresh = 0.0
        self._stop = threading.Event()

    def _read(self, client_random: str) -> SecretMap:
        if self.keylog is not None:
            keys: SecretMap = {}
            try:
                with self.keylog.open(encoding='ascii') as source:
                    for line in source:
                        _parse_keylog_line(line, keys)
            except FileNotFoundError:
                pass
            return keys
        if sys.platform == 'win32':
            pid = find_roblox_pid()
            return scan(pid, stop=self._stop, client_random=client_random) if pid else {}
        if sys.platform == 'linux':
            rosemary_pid = find_rosemary_pid()
            if rosemary_pid:
                return scan_rosemary(rosemary_pid, stop=self._stop, client_random=client_random)
            android_match = find_android_community_pid()
            if android_match:
                return scan_android_community(android_match[0], stop=self._stop, client_random=client_random)
            return {}
        msg = f'Live splice key recovery is not supported on {sys.platform}'
        raise RuntimeError(msg)

    async def get(self, client_random: str, label: str, *, wait_seconds: float = 10.0) -> bytes:
        """Wait for and retrieve a secret for the given client_random and label.

        Parameters
        ----------
        client_random : str
            Hexadecimal 32-byte TLS client random.
        label : str
            Secret label name (e.g. 'CLIENT_TRAFFIC_SECRET_0').
        wait_seconds : float, optional
            Maximum seconds to wait before raising TimeoutError.

        Returns
        -------
        bytes
            The recovered traffic secret.
        """
        async with asyncio.timeout(wait_seconds):
            return await self._wait_for_secret(client_random, label)

    async def _wait_for_secret(self, client_random: str, label: str) -> bytes:
        while True:
            secret = self._keys.get(client_random, {}).get(label)
            if secret is not None:
                return secret
            async with self._lock:
                if time.monotonic() - self._last_refresh >= MIN_REFRESH_INTERVAL:
                    found = await asyncio.to_thread(self._read, client_random)
                    if len(self._keys) >= MAX_CACHED_CONNECTIONS:
                        self._keys.clear()
                    self._keys.update(found)
                    if self._stop.is_set():
                        self._keys.clear()
                        msg = 'Splice key provider has stopped'
                        raise ConnectionAbortedError(msg)
                    self._last_refresh = time.monotonic()
            secret = self._keys.get(client_random, {}).get(label)
            if secret is not None:
                return secret
            await asyncio.sleep(0.05)

    def clear(self) -> None:
        """Signal provider to stop and clear cached keys."""
        self._stop.set()
        self._keys.clear()
