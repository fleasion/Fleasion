"""Unit tests for macOS OpenSSL 3.5.0 TLS 1.3 memory key scanner under Rosemary."""

from __future__ import annotations

from pathlib import Path
import struct
import sys

import pytest

from fleasion.proxy.rosemary_keys import (
    STRUCT_SIZE,
    TLS13,
    find_rosemary_pid,
    scan,
    validate_openssl_struct,
)
from fleasion.proxy.tls_records import expand_label


def test_rosemary_validate_openssl_struct_synthetic_valid() -> None:
    buf = bytearray(STRUCT_SIZE)
    struct.pack_into('<I', buf, 0x48, TLS13)
    struct.pack_into('<I', buf, 0x7C, 0)
    cr = bytes(range(1, 33))
    sr = bytes(range(33, 65))
    buf[0x188:0x1A8] = cr
    buf[0x168:0x188] = sr

    n = 32
    hn = 'sha256'
    fh = bytes(range(10, 42))
    hh = bytes(range(42, 74))
    buf[0x6FC : 0x6FC + n] = fh
    buf[0x73C : 0x73C + n] = hh

    hs = bytes(range(100, 132))
    master = bytes(range(132, 164))
    buf[0x5BC : 0x5BC + n] = hs
    buf[0x5FC : 0x5FC + n] = master

    ch = expand_label(hs, b'c hs traffic', n, hn, hh)
    sh = expand_label(hs, b's hs traffic', n, hn, hh)
    client = expand_label(master, b'c ap traffic', n, hn, fh)
    server = expand_label(master, b's ap traffic', n, hn, fh)
    exporter = expand_label(master, b'exp master', n, hn, fh)

    buf[0x77C : 0x77C + n] = client
    buf[0x7BC : 0x7BC + n] = server
    buf[0x7FC : 0x7FC + n] = exporter
    buf[0x67C : 0x67C + n] = expand_label(ch, b'finished', n, hn, b'')
    buf[0x6BC : 0x6BC + n] = expand_label(sh, b'finished', n, hn, b'')

    res = validate_openssl_struct(bytes(buf))
    assert res is not None
    client_random_hex, secrets = res
    assert client_random_hex == cr.hex()
    assert secrets['CLIENT_TRAFFIC_SECRET_0'] == client
    assert secrets['SERVER_TRAFFIC_SECRET_0'] == server
    assert secrets['CLIENT_HANDSHAKE_TRAFFIC_SECRET'] == ch
    assert secrets['SERVER_HANDSHAKE_TRAFFIC_SECRET'] == sh
    assert secrets['EXPORTER_SECRET'] == exporter


def test_rosemary_validate_openssl_struct_rejects_truncated() -> None:
    assert validate_openssl_struct(b'\x00' * (STRUCT_SIZE - 1)) is None


def test_rosemary_validate_openssl_struct_rejects_wrong_version() -> None:
    buf = bytearray(STRUCT_SIZE)
    struct.pack_into('<I', buf, 0x48, 0x303)  # TLS 1.2
    assert validate_openssl_struct(bytes(buf)) is None


def test_rosemary_validate_openssl_struct_rejects_corrupted_secret() -> None:
    buf = bytearray(STRUCT_SIZE)
    struct.pack_into('<I', buf, 0x48, TLS13)
    struct.pack_into('<I', buf, 0x7C, 0)
    cr = bytes(range(1, 33))
    sr = bytes(range(33, 65))
    buf[0x188:0x1A8] = cr
    buf[0x168:0x188] = sr

    n = 32
    buf[0x6FC : 0x6FC + n] = bytes(range(10, 42))
    buf[0x73C : 0x73C + n] = bytes(range(42, 74))
    buf[0x5BC : 0x5BC + n] = bytes(range(100, 132))
    buf[0x5FC : 0x5FC + n] = bytes(range(132, 164))
    # Fill secrets with wrong bytes
    buf[0x77C : 0x77C + n] = b'B' * 32
    assert validate_openssl_struct(bytes(buf)) is None


def test_find_rosemary_pid_filters_studio_and_zombies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')

    # Studio PID 100 - must be ignored
    p100 = tmp_path / '100'
    p100.mkdir()
    (p100 / 'cmdline').write_bytes(
        b'/usr/bin/rosemary\x00run\x00/Applications/RobloxStudio.app/Contents/MacOS/RobloxStudio\x00'
    )
    (p100 / 'status').write_text('Name:\trosemary\nState:\tS (sleeping)\n')
    (p100 / 'statm').write_text('1000 500 0 0 0 0 0\n')

    # Zombie Player PID 200 - must be ignored
    p200 = tmp_path / '200'
    p200.mkdir()
    (p200 / 'cmdline').write_bytes(
        b'/usr/bin/rosemary\x00run\x00/Applications/Roblox.app/Contents/MacOS/RobloxPlayer\x00'
    )
    (p200 / 'status').write_text('Name:\trosemary\nState:\tZ (zombie)\n')
    (p200 / 'statm').write_text('1000 500 0 0 0 0 0\n')

    # Real Player PID 300 - must be selected
    p300 = tmp_path / '300'
    p300.mkdir()
    (p300 / 'cmdline').write_bytes(
        b'/usr/bin/rosemary\x00run\x00/Applications/Roblox.app/Contents/MacOS/RobloxPlayer\x00'
    )
    (p300 / 'status').write_text('Name:\trosemary\nState:\tS (sleeping)\n')
    (p300 / 'statm').write_text('1000 800 0 0 0 0 0\n')

    # Irrelevant PID 400 - must be ignored
    p400 = tmp_path / '400'
    p400.mkdir()
    (p400 / 'cmdline').write_bytes(b'/bin/bash\x00')
    (p400 / 'status').write_text('Name:\tbash\nState:\tS (sleeping)\n')
    (p400 / 'statm').write_text('100 50 0 0 0 0 0\n')

    def mock_path(p: str | Path) -> Path:
        return tmp_path if str(p) == '/proc' else Path(p)

    monkeypatch.setattr('fleasion.proxy.rosemary_keys.Path', mock_path)

    pid = find_rosemary_pid()
    assert pid == 300


def test_find_rosemary_pid_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'win32')
    assert find_rosemary_pid() is None


def test_scan_rosemary_safe_execution() -> None:
    # Must not raise on invalid PID
    result = scan(0, client_random='ab' * 32)
    assert isinstance(result, dict)


def test_live_rosemary_process_scanning() -> None:
    # If Rosemary Roblox Player is actively running, verify detection and key recovery
    pid = find_rosemary_pid()
    if pid is None:
        pytest.skip('Rosemary Roblox player is not running')

    keys = scan(pid)
    assert isinstance(keys, dict)
    # If active TLS connections exist, ensure every key has required secrets
    for client_random, secrets in keys.items():
        assert len(client_random) == 64
        assert 'CLIENT_TRAFFIC_SECRET_0' in secrets
        assert 'SERVER_TRAFFIC_SECRET_0' in secrets


def test_invalid_client_random_skips_linux_memory_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.proxy import rosemary_keys

    monkeypatch.setattr(
        rosemary_keys,
        '_module_bounds',
        lambda _pid: pytest.fail('invalid random reached Linux memory inspection'),
    )
    assert rosemary_keys.scan(99999, client_random='invalid') == {}
