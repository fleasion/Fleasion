"""Short-lived, bounded, proxy-local ClientSettings snapshot for dynamic FastFlags."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

type ClientSettingsKey = tuple[str, str, bytes, bytes, bytes]
type FlagSignature = tuple[tuple[str, str], ...]

CLIENT_SETTINGS_UPSTREAM_REVALIDATE_SECONDS = 30.0
CLIENT_SETTINGS_UPSTREAM_RETRY_SECONDS = 5.0
CLIENT_SETTINGS_MAX_SNAPSHOT_BYTES = 512 * 1024
CLIENT_SETTINGS_MAX_SNAPSHOTS = 8


@dataclass(slots=True)
class ClientSettingsSnapshot:
    """Validated upstream body plus the last delivered client-facing response."""

    status_line: bytes
    headers: dict[bytes, bytes]
    upstream_body: bytes
    delivered_body: bytes
    encoding: bytes | None
    signature: FlagSignature
    generation: int
    checked_at: float
    etag: bytes
    response: bytes


def client_settings_cache_key(
    host: str, path: str, method: str, headers: dict[bytes, bytes], body: bytes
) -> ClientSettingsKey | None:
    """Only cache ordinary, public GET variants of dynamic ClientSettings."""
    if (
        method.upper() != 'GET'
        or body
        or any(name in headers for name in (b'authorization', b'cookie', b'range', b'if-range'))
    ):
        return None
    return (
        host,
        path,
        headers.get(b'accept-encoding', b''),
        headers.get(b'accept-language', b''),
        headers.get(b'user-agent', b''),
    )


def may_cache_client_settings_response(
    status: int, headers: dict[bytes, bytes], upstream_body: bytes
) -> bool:
    """Avoid caching malformed, private, or unexpectedly large documents."""
    if (
        status != 200
        or not upstream_body
        or len(upstream_body) > CLIENT_SETTINGS_MAX_SNAPSHOT_BYTES
    ):
        return False
    if b'set-cookie' in headers or b'www-authenticate' in headers:
        return False
    directives = headers.get(b'cache-control', b'').lower()
    if b'private' in directives or b'no-store' in directives:
        return False
    vary = headers.get(b'vary', b'').strip().lower()
    return not vary or all(
        token.strip() in {b'accept-encoding', b'accept-language'} for token in vary.split(b',')
    )


def make_local_client_settings_response(
    body: bytes,
    encoding: bytes | None,
    status_line: bytes,
    headers: dict[bytes, bytes],
) -> tuple[bytes, bytes]:
    """Build a strong local ETag for the *modified* representation."""
    checksum = hashlib.sha256((encoding or b'') + b'\x00' + body).hexdigest().encode('ascii')
    etag = b'"fleasion-' + checksum + b'"'
    excluded = {
        b'content-length',
        b'transfer-encoding',
        b'content-encoding',
        b'etag',
        b'content-md5',
        b'x-signature-ed25519',
        b'connection',
        b'cache-control',
        b'proxy-connection',
        b'proxy-authenticate',
        b'proxy-authorization',
    }
    header_lines = [key + b': ' + value for key, value in headers.items() if key not in excluded]
    if encoding is not None:
        header_lines.append(b'content-encoding: ' + encoding)
    header_lines.extend(
        (
            b'etag: ' + etag,
            b'cache-control: no-cache',
            b'content-length: ' + str(len(body)).encode('ascii'),
            b'connection: keep-alive',
        )
    )
    return etag, b'\r\n'.join([status_line, *header_lines]) + b'\r\n\r\n' + body


def local_client_settings_reply(
    snapshot: ClientSettingsSnapshot, req_headers: dict[bytes, bytes]
) -> tuple[bytes, bool]:
    """Emit a 304 only when the client actually has our validator."""
    validators = req_headers.get(b'if-none-match', b'')
    if validators and (
        validators == snapshot.etag
        or snapshot.etag in {tag.strip() for tag in validators.split(b',')}
    ):
        return (
            b'HTTP/1.1 304 Not Modified\r\n'
            b'ETag: '
            + snapshot.etag
            + b'\r\nCache-Control: no-cache\r\nContent-Length: 0'
            + b'\r\nConnection: keep-alive\r\n\r\n',
            False,
        )
    return snapshot.response, True


class ClientSettingsLocalCache:
    """Bound snapshot count and revalidate Roblox periodically, not per frame."""

    def __init__(self) -> None:
        self._snapshots: dict[ClientSettingsKey, ClientSettingsSnapshot] = {}
        self._next_retry: dict[ClientSettingsKey, float] = {}

    def snapshot(self, key: ClientSettingsKey, generation: int) -> ClientSettingsSnapshot | None:
        result = self._snapshots.get(key)
        return result if result is not None and result.generation == generation else None

    def should_fetch(self, key: ClientSettingsKey, generation: int) -> bool:
        snapshot = self.snapshot(key, generation)
        if snapshot is None:
            return True
        return (
            time.monotonic() - snapshot.checked_at >= CLIENT_SETTINGS_UPSTREAM_REVALIDATE_SECONDS
            and time.monotonic() >= self._next_retry.get(key, 0.0)
        )

    def note_fetch_attempt(self, key: ClientSettingsKey) -> None:
        if key in self._snapshots:
            self._next_retry[key] = time.monotonic() + CLIENT_SETTINGS_UPSTREAM_RETRY_SECONDS

    def note_not_modified(self, key: ClientSettingsKey, generation: int) -> None:
        snapshot = self.snapshot(key, generation)
        if snapshot is not None:
            snapshot.checked_at = time.monotonic()
            self._next_retry.pop(key, None)

    def store(self, key: ClientSettingsKey, snapshot: ClientSettingsSnapshot) -> None:
        if key not in self._snapshots and len(self._snapshots) >= CLIENT_SETTINGS_MAX_SNAPSHOTS:
            oldest_key = min(self._snapshots, key=lambda item: self._snapshots[item].checked_at)
            self._snapshots.pop(oldest_key)
            self._next_retry.pop(oldest_key, None)
        self._snapshots[key] = snapshot
        self._next_retry.pop(key, None)

    def update_render(
        self,
        key: ClientSettingsKey,
        previous: ClientSettingsSnapshot,
        updated: ClientSettingsSnapshot,
    ) -> None:
        """Avoid overwriting a newer upstream response after an asynchronous render."""
        if self._snapshots.get(key) is previous:
            self._snapshots[key] = updated

    def clear(self) -> None:
        self._snapshots.clear()
        self._next_retry.clear()
