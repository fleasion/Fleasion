"""Private Rosemary memory protocol and owned-session lifecycle."""

from __future__ import annotations

import os
import socket
import struct
import sys
import threading
from typing import TYPE_CHECKING

import pytest

from fleasion.utils import macos_rosemary_memory as memory

if TYPE_CHECKING:
    from collections.abc import Callable


def _serve(
    child: socket.socket,
    response: Callable[[bytes, int, int, bytes], tuple[int, bytes]],
) -> None:
    def read_exact(size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            try:
                part = child.recv(size - len(data))
            except ConnectionResetError:
                return b''
            if not part:
                return b''
            data.extend(part)
        return bytes(data)

    try:
        while request := read_exact(13):
            op, address, length = struct.unpack('<cQI', request)
            random = read_exact(length) if op == b'K' and length == 32 else b''
            status, payload = response(op, address, length, random)
            child.sendall(struct.pack('<BI', status, len(payload)) + payload)
    finally:
        child.close()


def test_channel_returns_only_verified_tls13_secrets() -> None:
    parent, child = socket.socketpair()
    random = bytes(range(32))
    keys = [bytes([i + 1]) * 32 for i in range(5)]
    calls: list[tuple[bytes, int, int, bytes]] = []

    def reply(op: bytes, address: int, length: int, received: bytes) -> tuple[int, bytes]:
        calls.append((op, address, length, received))
        return 0, bytes([1, 1, 32]) + b''.join(keys)

    worker = threading.Thread(target=_serve, args=(child, reply), daemon=True)
    worker.start()
    channel = memory.MemoryChannel(parent)
    try:
        assert channel.secrets_for(random.hex()) == {
            random.hex(): dict(zip(memory._KEY_LABELS, keys, strict=True)),
        }
        assert calls == [(b'K', 0, 32, random)]
        assert not hasattr(channel, 'read')
        assert not hasattr(channel, 'regions')
    finally:
        channel.close()
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_key_only_channel_ping_never_requests_memory() -> None:
    parent, child = socket.socketpair()
    calls: list[tuple[bytes, int, int, bytes]] = []

    def reply(op: bytes, address: int, length: int, random: bytes) -> tuple[int, bytes]:
        calls.append((op, address, length, random))
        return 0, b''

    worker = threading.Thread(target=_serve, args=(child, reply), daemon=True)
    worker.start()
    channel = memory.MemoryChannel(parent)
    try:
        channel.ping()
        assert calls == [(b'P', 0, 0, b'')]
    finally:
        channel.close()
        worker.join(timeout=2)


def test_channel_handles_absent_keys_and_tls12_master() -> None:
    parent, child = socket.socketpair()
    master = bytes(range(48))
    calls = 0

    def reply(op: bytes, address: int, length: int, random: bytes) -> tuple[int, bytes]:
        nonlocal calls
        assert (op, address, length, random) == (b'K', 0, 32, bytes(range(32)))
        calls += 1
        if calls == 1:
            return 0, b''
        return 0, bytes([1, 2, 48]) + master

    worker = threading.Thread(target=_serve, args=(child, reply), daemon=True)
    worker.start()
    channel = memory.MemoryChannel(parent)
    try:
        assert channel.secrets_for(bytes(range(32)).hex()) == {}
        assert channel.secrets_for(bytes(range(32)).hex()) == {
            bytes(range(32)).hex(): {'CLIENT_RANDOM': master},
        }
        with pytest.raises(ValueError, match='32 bytes'):
            channel.secrets_for('not-a-client-random')
    finally:
        channel.close()
        worker.join(timeout=2)


def test_shutdown_only_terminates_the_owned_managed_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    class Session:
        def __init__(self) -> None:
            self.process = SimpleNamespace(pid=8543, poll=lambda: None)
            self.closed = False
        @property
        def running(self) -> bool:
            return self.process.poll() is None
        def close(self) -> None:
            self.closed = True

    first = Session()
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(memory.os, 'killpg', lambda pid, sig: calls.append((pid, sig)))
    memory.set_active_session(first)
    memory.shutdown_active_session(terminate=False)
    assert first.closed
    assert calls == []
    second = Session()
    memory.set_active_session(second)
    memory.shutdown_active_session(terminate=True)
    assert second.closed
    assert calls == [(8543, memory.signal.SIGTERM)]
    assert memory.active_session() is None


def test_malformed_reply_closes_channel() -> None:
    parent, child = socket.socketpair()
    worker = threading.Thread(
        target=_serve,
        args=(child, lambda *_: (0, b'X' * (memory._MAX_REPLY + 1))),
        daemon=True,
    )
    worker.start()
    channel = memory.MemoryChannel(parent)
    with pytest.raises(ConnectionError, match='Malformed Rosemary TLS key reply'):
        channel.secrets_for('a1' * 32)
    assert channel._closed
    channel.close()
    worker.join(timeout=2)


def test_inherited_descriptor_is_not_inherited_without_explicit_opt_in() -> None:
    script = '''import os,socket,struct\nfd=int(os.environ["FLEASION_ROSEMARY_IPC_FD"])\nassert int(os.environ["FLEASION_ROSEMARY_LAUNCHER_PID"]) == os.getppid()\ns=socket.socket(fileno=fd)\nrequest=s.recv(45)\nassert request[:1]==b"K"\ns.sendall(struct.pack("<BI",0,0))\n'''
    session = memory.launch_with_private_memory([sys.executable, '-c', script], env=os.environ.copy())
    try:
        assert session.channel.secrets_for('00' * 32) == {}
    finally:
        session.close()
        session.process.wait(timeout=3)


def test_owned_session_cleanup_registration_is_lazy_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    registrations: list[object] = []
    monkeypatch.setattr(memory, '_cleanup_registered', threading.Event())
    monkeypatch.setattr(memory.atexit, 'register', lambda fn: registrations.append(fn))
    memory.set_active_session(None)
    assert registrations == []

    class Session:
        def __init__(self) -> None:
            self.process = SimpleNamespace(pid=8765, poll=lambda: 0)
        @property
        def running(self) -> bool:
            return False
        def close(self) -> None:
            pass

    session = Session()
    memory.set_active_session(session)  # type: ignore[arg-type]
    memory.set_active_session(session)  # type: ignore[arg-type]
    assert registrations == [memory.shutdown_active_session]
    memory.shutdown_active_session(terminate=False)


def test_native_scanner_failure_is_not_silently_reported_as_missing_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    class FailedChannel:
        def secrets_for(self, _random: str) -> dict[str, dict[str, bytes]]:
            raise OSError('native region scanner unavailable')

    session = SimpleNamespace(
        running=True, channel=FailedChannel(), process=SimpleNamespace(poll=lambda: None),
    )
    monkeypatch.setattr(memory, 'active_session', lambda: session)
    with pytest.raises(OSError, match='native region scanner unavailable'):
        memory.scan_active(client_random='b5' * 32)


def test_timeout_invalidates_ipc_framing_instead_of_reusing_late_key_reply() -> None:
    parent, child = socket.socketpair()
    channel = memory.MemoryChannel(parent)
    channel._socket.settimeout(0.02)
    with pytest.raises(TimeoutError):
        channel.secrets_for('ab' * 32)
    assert channel._closed
    with pytest.raises(ConnectionError, match='closed'):
        channel.secrets_for('cd' * 32)
    child.close()
