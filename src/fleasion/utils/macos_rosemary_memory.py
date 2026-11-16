"""Private TLS-secret-only transport for a Fleasion-launched macOS Rosemary process."""

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

from fleasion.utils.logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Sequence

_HEADER = struct.Struct('<cQI')
_REPLY = struct.Struct('<BI')
_KEY_REQUEST_SIZE = 32
_MAX_REPLY = 3 + 5 * 48
_KEY_LABELS = (
    'CLIENT_HANDSHAKE_TRAFFIC_SECRET',
    'SERVER_HANDSHAKE_TRAFFIC_SECRET',
    'CLIENT_TRAFFIC_SECRET_0',
    'SERVER_TRAFFIC_SECRET_0',
    'EXPORTER_SECRET',
)


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
            try:
                chunk = self._socket.recv(length - len(received))
            except OSError:
                # An incomplete or timed-out reply invalidates framing. Never
                # attribute a late response to another TLS connection
                self._closed = True
                self._socket.close()
                raise
            if not chunk:
                self._closed = True
                self._socket.close()
                msg = 'Rosemary closed its private TLS key channel'
                raise ConnectionError(msg)
            received.extend(chunk)
        return bytes(received)

    def ping(self) -> None:
        """Verify the owned Rosemary IPC service without accessing memory."""
        with self._lock:
            if self._closed:
                msg = 'Rosemary key channel is closed'
                raise ConnectionError(msg)
            self._socket.sendall(_HEADER.pack(b'P', 0, 0))
            status, reply_len = _REPLY.unpack(self._read_exact(_REPLY.size))
            if status != 0 or reply_len != 0:
                self.close()
                msg = 'Rosemary key-only IPC is unavailable'
                raise ConnectionError(msg)

    def secrets_for(self, client_random: str) -> dict[str, dict[str, bytes]]:
        """Request only a validated TLS key set from the owned Rosemary guest."""
        if len(client_random) != _KEY_REQUEST_SIZE * 2:
            msg = 'TLS client random must be 32 bytes'
            raise ValueError(msg)
        random = bytes.fromhex(client_random)
        with self._lock:
            if self._closed:
                msg = 'Rosemary key channel is closed'
                raise ConnectionError(msg)
            self._socket.sendall(_HEADER.pack(b'K', 0, len(random)) + random)
            status, reply_len = _REPLY.unpack(self._read_exact(_REPLY.size))
            if status not in {0, 1} or reply_len > _MAX_REPLY or (status == 1 and reply_len):
                self.close()
                msg = 'Malformed Rosemary TLS key reply'
                raise ConnectionError(msg)
            payload = self._read_exact(reply_len)
        if status == 1:
            msg = 'Rosemary TLS key scanner unavailable'
            raise OSError(msg)
        if not payload:
            return {}
        if len(payload) < 3 or payload[0] != 1:
            msg = 'Unsupported Rosemary TLS key protocol'
            raise ValueError(msg)
        kind, secret_len = payload[1], payload[2]
        if kind == 1 and secret_len in {32, 48} and len(payload) == 3 + 5 * secret_len:
            secrets = {
                label: payload[3 + i * secret_len : 3 + (i + 1) * secret_len]
                for i, label in enumerate(_KEY_LABELS)
            }
            return {client_random.lower(): secrets}
        if kind == 2 and secret_len == 48 and len(payload) == 51:
            return {client_random.lower(): {'CLIENT_RANDOM': payload[3:]}}
        msg = 'Malformed Rosemary TLS key payload'
        raise ValueError(msg)


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
        environment['FLEASION_ROSEMARY_LAUNCHER_PID'] = str(os.getpid())
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
        if stop is not None and stop.is_set():
            return {}
        return session.channel.secrets_for(client_random)
    except (OSError, ValueError) as exc:
        # A native scanner failure is not equivalent to a key that has not
        # appeared yet. It must reach the connection as an explicit failure
        log_buffer.log('Error', f'Managed Rosemary TLS key extraction failed: {exc}')
        raise
