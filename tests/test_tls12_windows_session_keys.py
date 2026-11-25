"""OpenSSL 3.5.0 TLS 1.2 master-secret recovery from synthetic process memory."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from typing import Callable, cast

import pytest

from fleasion.proxy import live_keys
from fleasion.proxy.live_keys import (
    TLS12,
    TLS12_SESSION_POINTER_OFFSETS,
    TLS12_SESSION_PREFIX_SIZE,
    TLS12_STRUCT_SIZE,
    validate_tls12_openssl_struct,
)


def _memory_fixture(pointer_offset: int = 0x900) -> tuple[bytearray, bytes, int, bytes]:
    assert pointer_offset in TLS12_SESSION_POINTER_OFFSETS
    connection = bytearray(TLS12_STRUCT_SIZE)
    struct.pack_into('<I', connection, 0x48, TLS12)
    struct.pack_into('<I', connection, 0x78, 0)
    random = bytes(range(10, 42))
    connection[0x184:0x1A4] = random
    connection[0x164:0x184] = bytes(range(42, 74))
    pointer = 0x100000000
    struct.pack_into('<Q', connection, pointer_offset, pointer)
    master = bytes(range(86, 134))
    session = bytearray(TLS12_SESSION_PREFIX_SIZE)
    struct.pack_into('<I', session, 0, TLS12)
    struct.pack_into('<Q', session, 8, 48)
    session[80:128] = master
    return connection, bytes(session), pointer, master


@pytest.mark.parametrize('offset', TLS12_SESSION_POINTER_OFFSETS)
def test_session_master_secret_is_found_without_assuming_one_pointer_offset(offset: int) -> None:
    connection, session, pointer, master = _memory_fixture(offset)
    found = validate_tls12_openssl_struct(
        bytes(connection), lambda addr, _size: session if addr == pointer else b''
    )
    assert found == (bytes(range(10, 42)).hex(), {'CLIENT_RANDOM': master})


@pytest.mark.parametrize('defect', ['wrong_version', 'wrong_size', 'no_entropy', 'bad_pointer', 'wrong_connection'])
def test_reject_invalid_session_master_secret(defect: str) -> None:
    connection, initial_session, pointer, _master = _memory_fixture()
    session = bytearray(initial_session)
    if defect == 'wrong_version':
        struct.pack_into('<I', session, 0, 0x304)
    elif defect == 'wrong_size':
        struct.pack_into('<Q', session, 8, 32)
    elif defect == 'no_entropy':
        session[80:128] = b'\x00' * 48
    elif defect == 'bad_pointer':
        struct.pack_into('<Q', connection, 0x900, 0)
    else:
        struct.pack_into('<I', connection, 0x48, 0x304)
    assert validate_tls12_openssl_struct(
        bytes(connection), lambda addr, _size: bytes(session) if addr == pointer else b''
    ) is None


def test_windows_targeted_scan_integrates_tls12_session_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection, session, pointer, master = _memory_fixture()
    executable_base = 0x100000
    struct.pack_into('<Q', connection, 0x18, executable_base + 0x200)
    source = 0x60000000

    def read(_handle: int, address: int, _size: int) -> bytes:
        if address == source:
            return bytes(connection)
        if address == pointer:
            return session
        return b''

    def candidates(
        _handle: int, _pid: int, _stop: object = None, _wanted: bytes | None = None
    ) -> Iterator[tuple[int, tuple[int, int], int]]:
        yield source, (source, 8192), 0

    def remember(_pid: int, _region: tuple[int, int], _offset: int) -> None:
        pass

    monkeypatch.setattr(live_keys, '_read', read)
    monkeypatch.setattr(live_keys, '_candidates', candidates)
    monkeypatch.setattr(live_keys, '_remember_scan_location', remember)
    scan = cast('Callable[..., dict[str, dict[str, bytes]]]', getattr(live_keys, '_scan_handle'))
    result = scan(
        42, 7, executable_base, executable_base + 0x8000,
        stop=None, wanted=bytes(range(10, 42)), client_random=bytes(range(10, 42)).hex(),
    )
    assert result == {bytes(range(10, 42)).hex(): {'CLIENT_RANDOM': master}}
