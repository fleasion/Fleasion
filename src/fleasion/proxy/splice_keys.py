"""Bounded-lifetime traffic keys for local splice connections."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from typing import TYPE_CHECKING

from fleasion.utils.logging import log_buffer

from .android_community_keys import find_android_community_pid, scan as scan_android_community
from .live_keys import find_roblox_pid, scan
from .rosemary_keys import find_rosemary_pid, scan as scan_rosemary
from .sober_keys import find_sober_pid, scan as scan_sober

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

    def __init__(self, keylog: Path | None = None, *, linux_client_key: str | None = None) -> None:
        self.keylog = keylog
        self.linux_client_key = linux_client_key
        self._keys: SecretMap = {}
        self._scan_slots = asyncio.Semaphore(2)
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._last_refresh: dict[str, float] = {}
        self._stop = threading.Event()

    def _read(self, client_random: str) -> SecretMap:  # ruff: ignore[too-many-return-statements]
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
            # Never read another client's secrets merely because its process
            # is running. The active Fleasion selection owns this proxy.
            if self.linux_client_key == 'mocktail':
                match = find_android_community_pid('mocktail')
                return (
                    scan_android_community(match[0], stop=self._stop, client_random=client_random)
                    if match
                    else {}
                )
            if self.linux_client_key == 'sober':
                sober_pid = find_sober_pid()
                return (
                    scan_sober(sober_pid, stop=self._stop, client_random=client_random)
                    if sober_pid
                    else {}
                )
            if self.linux_client_key == 'rosemary':
                rosemary_pid = find_rosemary_pid()
                return (
                    scan_rosemary(rosemary_pid, stop=self._stop, client_random=client_random)
                    if rosemary_pid
                    else {}
                )
            rosemary_pid = find_rosemary_pid()
            if rosemary_pid:
                return scan_rosemary(rosemary_pid, stop=self._stop, client_random=client_random)
            android_match = find_android_community_pid()
            if android_match:
                return scan_android_community(
                    android_match[0], stop=self._stop, client_random=client_random
                )
            sober_pid = find_sober_pid()
            if sober_pid:
                return scan_sober(sober_pid, stop=self._stop, client_random=client_random)
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

    async def _scan_for_secret(self, client_random: str) -> None:
        """Bound process scans without blocking unrelated TLS connections."""
        started = time.monotonic()
        try:
            async with self._scan_slots:
                if self._stop.is_set():
                    return
                scan_started = time.monotonic()
                found = await asyncio.to_thread(self._read, client_random)
                scan_ended = time.monotonic()
                if not self._stop.is_set():
                    if len(self._keys) >= MAX_CACHED_CONNECTIONS:
                        self._keys.clear()
                    self._keys.update(found)
                elapsed = scan_ended - started
                if elapsed >= 1.0:
                    log_buffer.log(
                        'TLS',
                        f'Slow TLS key lookup: client={self.linux_client_key or "default"} '
                        f'elapsed_ms={int(elapsed * 1000)} '
                        f'queue_ms={int((scan_started - started) * 1000)} '
                        f'scan_ms={int((scan_ended - scan_started) * 1000)} '
                        f'found={client_random in found}',
                    )
        finally:
            if len(self._last_refresh) >= MAX_CACHED_CONNECTIONS:
                self._last_refresh.clear()
            self._last_refresh[client_random] = time.monotonic()
            self._inflight.pop(client_random, None)

    async def _wait_for_secret(self, client_random: str, label: str) -> bytes:
        while True:
            if self._stop.is_set():
                msg = 'Splice key provider has stopped'
                raise ConnectionAbortedError(msg)
            secret = self._keys.get(client_random, {}).get(label)
            if secret is not None:
                return secret
            scan_task = self._inflight.get(client_random)
            if (
                scan_task is None
                and time.monotonic() - self._last_refresh.get(client_random, 0.0)
                >= MIN_REFRESH_INTERVAL
            ):
                scan_task = asyncio.create_task(self._scan_for_secret(client_random))
                self._inflight[client_random] = scan_task
                # A timed-out caller must not abandon a still-running memory scan
                scan_task.add_done_callback(
                    lambda done: done.exception() if not done.cancelled() else None
                )
            if scan_task is not None:
                await asyncio.shield(scan_task)
            else:
                await asyncio.sleep(0.05)

    def clear(self) -> None:
        """Signal provider to stop and clear cached keys."""
        self._stop.set()
        self._keys.clear()
