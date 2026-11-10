"""Private read-only memory transport for a Fleasion-launched macOS Rosemary process."""

from __future__ import annotations

import atexit
import os
import signal
import socket
import struct
import subprocess
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fleasion.proxy.rosemary_keys import scan_mapped_regions
from fleasion.utils.logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Sequence

_HEADER = struct.Struct('<cQI')
_REPLY = struct.Struct('<BI')
_REGION = struct.Struct('<QQ')
_MAX_READ = 4 * 1024 * 1024 + 64
_MAX_CHUNK = 4 * 1024 * 1024
_MAX_REGIONS = 16_384
_MAX_REPLY = max(_MAX_READ, _MAX_REGIONS * _REGION.size)


class MemoryChannel:
    """A capability-bound Unix socket, not a remotely accessible listener."""

    def __init__(self, socket_: socket.socket) -> None:
        self._socket = socket_
        self._socket.settimeout(15)
        self._lock = threading.RLock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._socket.close()

    def _read_exact(self, length: int) -> bytes:
        received = bytearray()
        while len(received) < length:
            chunk = self._socket.recv(length - len(received))
            if not chunk:
                msg = 'Rosemary closed its private memory channel'
                raise ConnectionError(msg)
            received.extend(chunk)
        return bytes(received)

    def _request(self, operation: bytes, address: int, length: int) -> bytes | None:
        if not (0 <= address < (1 << 64) and 0 <= length <= 0xFFFFFFFF):
            msg = 'Invalid Rosemary memory request bounds'
            raise ValueError(msg)
        with self._lock:
            if self._closed:
                msg = 'Rosemary memory session is closed'
                raise ConnectionError(msg)
            self._socket.sendall(_HEADER.pack(operation, address, length))
            status, reply_len = _REPLY.unpack(self._read_exact(_REPLY.size))
            if reply_len > _MAX_REPLY or status not in {0, 1} or (status == 1 and reply_len):
                self._closed = True
                self._socket.close()
                msg = 'Malformed Rosemary memory reply'
                raise ConnectionError(msg)
            payload = self._read_exact(reply_len)
            return payload if status == 0 else None

    def regions(self) -> list[tuple[int, int]]:
        data = self._request(b'M', 0, 0)
        if data is None or len(data) % _REGION.size or len(data) > _MAX_REGIONS * _REGION.size:
            msg = 'Rosemary could not enumerate its readable memory'
            raise OSError(msg)
        regions = list(_REGION.iter_unpack(data))
        if any(size == 0 or start + size > 1 << 64 for start, size in regions):
            msg = 'Rosemary returned invalid memory region bounds'
            raise OSError(msg)
        return regions

    def read(self, address: int, size: int) -> bytes:
        if not 0 < size <= _MAX_READ:
            return b''
        # Rosemary limits individual IPC reads to 4 MiB, but the shared
        # OpenSSL scanner requests up to 64 extra bytes for needle overlap.
        data = bytearray()
        while len(data) < size:
            count = min(_MAX_CHUNK, size - len(data))
            result = self._request(b'R', address + len(data), count)
            if result is None:
                return bytes(data)
            if len(result) != count:
                msg = 'Rosemary returned a partial memory read'
                raise OSError(msg)
            data.extend(result)
        return bytes(data)


class MemoryReader:
    """Seekable adapter consumed by the existing OpenSSL layout validators."""

    def __init__(self, channel: MemoryChannel) -> None:
        self._channel = channel
        self._position = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence != 0 or not 0 <= offset < 1 << 64:
            msg = 'Rosemary memory reads require an absolute address'
            raise ValueError(msg)
        self._position = offset
        return offset

    def read(self, size: int = -1) -> bytes:
        data = self._channel.read(self._position, size)
        self._position += len(data)
        return data


@dataclass(slots=True)
class RosemarySession:
    process: subprocess.Popen[bytes]
    channel: MemoryChannel

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def close(self) -> None:
        self.channel.close()


_active: RosemarySession | None = None
_active_lock = threading.Lock()
_cleanup_registered = threading.Event()


def active_session() -> RosemarySession | None:
    """Return only the session Fleasion itself launched and still owns."""
    with _active_lock:
        session = _active
        return session if session is not None and session.running else None


def set_active_session(session: RosemarySession | None) -> None:
    global _active
    with _active_lock:
        if session is not None and not _cleanup_registered.is_set():
            atexit.register(shutdown_active_session)
            _cleanup_registered.set()
        previous, _active = _active, session
    if previous is not None and previous is not session:
        previous.close()


def shutdown_active_session(*, terminate: bool = False) -> None:
    """Drop the owned channel; optionally close only Fleasion's process group."""
    global _active
    with _active_lock:
        session, _active = _active, None
    if session is None:
        return
    session.close()
    if terminate and session.running:
        # launch_with_private_memory uses start_new_session=True, so this group
        # is dedicated to our Roblox launch and any helpers it spawned.
        try:
            os.killpg(session.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def launch_with_private_memory(
    command: Sequence[str], *, env: dict[str, str], stdout: int | None = None
) -> RosemarySession:
    """Spawn a Rosemary-owned process with one inherited AF_UNIX capability."""
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        environment = env.copy()
        environment['FLEASION_ROSEMARY_IPC_FD'] = str(child.fileno())
        process = subprocess.Popen(
            list(command),
            env=environment,
            pass_fds=(child.fileno(),),
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=stdout if stdout is not None else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except BaseException:
        parent.close()
        raise
    finally:
        child.close()
    return RosemarySession(process, MemoryChannel(parent))


def scan_active(
    *, client_random: str, stop: threading.Event | None = None
) -> dict[str, dict[str, bytes]]:
    """Recover only matching, cryptographically validated TLS secrets."""
    session = active_session()
    if session is None:
        return {}
    try:
        regions = session.channel.regions()
        return scan_mapped_regions(
            MemoryReader(session.channel),
            session.process.pid,
            regions,
            client_random=client_random,
            stop=stop,
        )
    except (OSError, ValueError) as exc:
        log_buffer.log('TLS', f'Pinned macOS Rosemary memory scan unavailable: {exc}')
        return {}
