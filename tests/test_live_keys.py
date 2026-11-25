"""Unit tests for the live OpenSSL 3.5.0 TLS 1.3 memory key scanner."""

from __future__ import annotations

import struct
from fleasion.proxy.live_keys import (
    STRUCT_SIZE,
    TLS13,
    find_roblox_pid,
    scan,
    validate_openssl_struct,
)
from fleasion.proxy.tls_records import expand_label


def test_validate_openssl_struct_synthetic_valid() -> None:
    buf = bytearray(STRUCT_SIZE)
    struct.pack_into('<I', buf, 0x48, TLS13)
    struct.pack_into('<I', buf, 0x78, 0)
    cr = bytes(range(1, 33))
    sr = bytes(range(33, 65))
    buf[0x184:0x1A4] = cr
    buf[0x164:0x184] = sr

    n = 32
    hn = 'sha256'
    fh = bytes(range(10, 42))
    hh = bytes(range(42, 74))
    buf[0x6F4 : 0x6F4 + n] = fh
    buf[0x734 : 0x734 + n] = hh

    hs = bytes(range(100, 132))
    master = bytes(range(132, 164))
    buf[0x5B4 : 0x5B4 + n] = hs
    buf[0x5F4 : 0x5F4 + n] = master

    ch = expand_label(hs, b'c hs traffic', n, hn, hh)
    sh = expand_label(hs, b's hs traffic', n, hn, hh)
    client = expand_label(master, b'c ap traffic', n, hn, fh)
    server = expand_label(master, b's ap traffic', n, hn, fh)
    exporter = expand_label(master, b'exp master', n, hn, fh)

    buf[0x774 : 0x774 + n] = client
    buf[0x7B4 : 0x7B4 + n] = server
    buf[0x7F4 : 0x7F4 + n] = exporter
    buf[0x674 : 0x674 + n] = expand_label(ch, b'finished', n, hn, b'')
    buf[0x6B4 : 0x6B4 + n] = expand_label(sh, b'finished', n, hn, b'')

    res = validate_openssl_struct(bytes(buf))
    assert res is not None
    client_random_hex, secrets = res
    assert client_random_hex == cr.hex()
    assert secrets['CLIENT_TRAFFIC_SECRET_0'] == client
    assert secrets['SERVER_TRAFFIC_SECRET_0'] == server
    assert secrets['CLIENT_HANDSHAKE_TRAFFIC_SECRET'] == ch
    assert secrets['SERVER_HANDSHAKE_TRAFFIC_SECRET'] == sh
    assert secrets['EXPORTER_SECRET'] == exporter


def test_validate_openssl_struct_rejects_truncated() -> None:
    assert validate_openssl_struct(b'\x00' * (STRUCT_SIZE - 1)) is None


def test_validate_openssl_struct_rejects_wrong_version() -> None:
    buf = bytearray(STRUCT_SIZE)
    struct.pack_into('<I', buf, 0x48, 0x303)  # TLS 1.2
    assert validate_openssl_struct(bytes(buf)) is None


def test_validate_openssl_struct_rejects_corrupted_secret() -> None:
    buf = bytearray(STRUCT_SIZE)
    struct.pack_into('<I', buf, 0x48, TLS13)
    struct.pack_into('<I', buf, 0x78, 0)
    cr = bytes(range(1, 33))
    sr = bytes(range(33, 65))
    buf[0x184:0x1A4] = cr
    buf[0x164:0x184] = sr

    n = 32
    buf[0x6F4 : 0x6F4 + n] = bytes(range(10, 42))
    buf[0x734 : 0x734 + n] = bytes(range(42, 74))
    buf[0x5B4 : 0x5B4 + n] = bytes(range(100, 132))
    buf[0x5F4 : 0x5F4 + n] = bytes(range(132, 164))
    # Fill secrets with wrong bytes
    buf[0x774 : 0x774 + n] = b'A' * 32
    assert validate_openssl_struct(bytes(buf)) is None


def test_find_roblox_pid_safe_execution() -> None:
    # Must not raise on any platform
    pid = find_roblox_pid()
    assert pid is None or isinstance(pid, int)


def test_scan_safe_execution() -> None:
    # Must not raise on any platform
    result = scan(0, client_random='ab' * 32)
    assert isinstance(result, dict)
