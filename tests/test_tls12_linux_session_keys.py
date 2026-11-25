"""TLS 1.2 memory extraction across macOS and Android Roblox guest layouts."""

from __future__ import annotations

import struct
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy import android_community_keys, rosemary_keys, sober_keys
from fleasion.proxy.tls12_session import (
    LP64_OPENSSL_350, SESSION_READ_SIZE, validate_tls12_session,
)

if TYPE_CHECKING:
    from fleasion.proxy.sober_keys import _MemoryReader  # pyright: ignore[reportPrivateUsage]


class _RemoteMemory:
    def __init__(self, position: int, connection: bytes, session_pointer: int, session: bytes) -> None:
        self.position = position
        self.connection = connection
        self.session_pointer = session_pointer
        self.session = session
        self.cursor = 0

    def seek(self, address: int) -> None:
        self.cursor = address

    def read(self, size: int) -> bytes:
        if self.cursor == self.position:
            return self.connection[:size]
        if self.cursor == self.session_pointer:
            return self.session[:size]
        return b''


def _synthetic_session(offset: int) -> tuple[_RemoteMemory, bytes, bytes]:
    addr = 0x2000000
    ptr = 0x3000000
    connection = bytearray(LP64_OPENSSL_350.minimum_size)
    struct.pack_into('<I', connection, 0x48, 0x303)
    struct.pack_into('<I', connection, 0x7C, 0)
    struct.pack_into('<Q', connection, 0x18, 0x700100)
    client_random = bytes(range(1, 33))
    connection[0x188:0x1A8] = client_random
    connection[0x168:0x188] = bytes(range(33, 65))
    struct.pack_into('<Q', connection, offset, ptr)
    session = bytearray(SESSION_READ_SIZE)
    struct.pack_into('<I', session, 0, 0x303)
    struct.pack_into('<Q', session, 8, 48)
    master = bytes(range(80, 128))
    session[80:128] = master
    return _RemoteMemory(addr, bytes(connection), ptr, bytes(session)), client_random, master


@pytest.mark.parametrize('offset', LP64_OPENSSL_350.session_offsets)
def test_lp64_tls12_session_offsets(offset: int) -> None:
    memory, random, master = _synthetic_session(offset)
    result = validate_tls12_session(
        memory.connection, lambda a, n: memory.session[:n] if a == memory.session_pointer else b'',
        layout=LP64_OPENSSL_350,
    )
    assert result == (random.hex(), {'CLIENT_RANDOM': master})


@pytest.mark.parametrize('module', [rosemary_keys, android_community_keys, sober_keys])
def test_native_client_scanner_matches_tls12_session_without_touching_tls13(module: object) -> None:
    memory, random, master = _synthetic_session(0x900)
    code = cast('Callable[..., tuple[str, dict[str, bytes]] | None]', getattr(module, '_scan_candidate'))
    start = memory.position
    kwargs = dict(
        start=start, offset=0, position=0x188, field_offset=0x188,
        module_base=0x700000, module_end=0x800000,
    )
    if module is sober_keys:
        class SoberReader:
            def read(self, addr: int, size: int) -> bytes:
                memory.seek(addr)
                return memory.read(size)

        result = code(cast('_MemoryReader', SoberReader()), memory.connection[:0x900], **kwargs)
    else:
        result = code(memory, memory.connection[:0x900], **kwargs)
    assert result == (random.hex(), {'CLIENT_RANDOM': master})


@pytest.mark.parametrize('module', [rosemary_keys, android_community_keys, sober_keys])
def test_native_client_scanner_does_not_accept_mismatched_method_ptr(module: object) -> None:
    memory, _random, _master = _synthetic_session(0x900)
    code = cast('Callable[..., tuple[str, dict[str, bytes]] | None]', getattr(module, '_scan_candidate'))
    kwargs = dict(
        start=memory.position, offset=0, position=0x188, field_offset=0x188,
        module_base=0x800000, module_end=0x900000,
    )
    if module is sober_keys:
        class SoberReader:
            def read(self, addr: int, size: int) -> bytes:
                memory.seek(addr)
                return memory.read(size)
        result = code(cast('_MemoryReader', SoberReader()), memory.connection[:0x900], **kwargs)
    else:
        result = code(memory, memory.connection[:0x900], **kwargs)
    assert result is None
