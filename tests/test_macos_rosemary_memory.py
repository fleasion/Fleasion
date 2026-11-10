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


def _serve(child: socket.socket, response: Callable[[bytes, int, int], tuple[int, bytes]]) -> None:
    try:
        while request := child.recv(13):
            if len(request) != 13:
                return
            op, address, length = struct.unpack('<cQI', request)
            status, payload = response(op, address, length)
            child.sendall(struct.pack('<BI', status, len(payload)) + payload)
    finally:
        child.close()


def test_channel_region_listing_reads_and_denials() -> None:
    parent, child = socket.socketpair()

    def reply(op: bytes, address: int, length: int) -> tuple[int, bytes]:
        if op == b'M':
            return 0, struct.pack('<QQ', 4096, 8192)
        if op == b'R' and address == 4096 and length == 4:
            return 0, b'DATA'
        return 1, b''

    worker = threading.Thread(target=_serve, args=(child, reply), daemon=True)
    worker.start()
    channel = memory.MemoryChannel(parent)
    assert channel.regions() == [(4096, 8192)]
    reader = memory.MemoryReader(channel)
    assert reader.seek(4096) == 4096
    assert reader.read(4) == b'DATA'
    assert reader.read(4) == b''
    assert channel.read(0, 4) == b''
    assert channel.read(4096, memory._MAX_READ + 1) == b''
    channel.close()
    worker.join(timeout=2)
    assert not worker.is_alive()



def test_reads_above_single_ipc_chunk_are_split_without_losing_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory, '_MAX_CHUNK', 4)
    monkeypatch.setattr(memory, '_MAX_READ', 8)
    parent, child = socket.socketpair()
    requests: list[tuple[int, int]] = []

    def reply(op: bytes, address: int, length: int) -> tuple[int, bytes]:
        assert op == b'R'
        requests.append((address, length))
        if (address, length) == (4096, 4):
            return 0, b'ABCD'
        if (address, length) == (4100, 2):
            return 0, b'EF'
        return 1, b''

    worker = threading.Thread(target=_serve, args=(child, reply), daemon=True)
    worker.start()
    channel = memory.MemoryChannel(parent)
    try:
        assert channel.read(4096, 6) == b'ABCDEF'
        assert requests == [(4096, 4), (4100, 2)]
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
    worker = threading.Thread(target=_serve, args=(child, lambda *_: (0, b'X' * 33)), daemon=True)
    worker.start()
    channel = memory.MemoryChannel(parent)
    with pytest.raises(OSError, match='could not enumerate'):
        channel.regions()
    channel.close()
    worker.join(timeout=2)


def test_inherited_descriptor_is_not_inherited_without_explicit_opt_in() -> None:
    script = '''import os,socket,struct\nfd=int(os.environ["FLEASION_ROSEMARY_IPC_FD"])\ns=socket.socket(fileno=fd)\nrequest=s.recv(13)\nassert request[0:1]==b"M"\ns.sendall(struct.pack("<BIQQ",0,16,4096,8192))\n'''
    session = memory.launch_with_private_memory([sys.executable, '-c', script], env=os.environ.copy())
    try:
        assert session.channel.regions() == [(4096, 8192)]
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
