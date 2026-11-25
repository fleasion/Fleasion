"""Unit tests for community Android OpenSSL 3.5.0 TLS 1.3 memory key scanner."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from fleasion.proxy.android_community_keys import (
    find_android_community_pid,
    scan,
)


def test_find_android_community_pid_synthetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')

    # PID 100: mocktail with libroblox.so mapped
    p100 = tmp_path / '100'
    p100.mkdir()
    (p100 / 'cmdline').write_bytes(b'/usr/bin/mocktail\x00')
    (p100 / 'exe').symlink_to('/usr/bin/mocktail')
    (p100 / 'status').write_text('Name:\tmocktail\nState:\tS (sleeping)\n')
    (p100 / 'statm').write_text('1000 800 0 0 0 0 0\n')
    (p100 / 'maps').write_text(
        '7f79b0440000-7f79b6fbe000 r-xp 00000000 103:03 8131164 /path/to/libroblox.so\n'
    )

    # PID 200: cordial without libroblox.so mapped (e.g. failure dialog or helper)
    p200 = tmp_path / '200'
    p200.mkdir()
    (p200 / 'cmdline').write_bytes(b'/usr/bin/cordial\x00')
    (p200 / 'exe').symlink_to('/usr/bin/cordial')
    (p200 / 'status').write_text('Name:\tcordial\nState:\tS (sleeping)\n')
    (p200 / 'statm').write_text('1000 500 0 0 0 0 0\n')
    (p200 / 'maps').write_text('7f79b0440000-7f79b6fbe000 r-xp 00000000 103:03 8131164 /usr/lib64/libc.so\n')

    # PID 300: nightcap with libroblox.so but zombie state
    p300 = tmp_path / '300'
    p300.mkdir()
    (p300 / 'cmdline').write_bytes(b'/usr/bin/nightcap\x00')
    (p300 / 'exe').symlink_to('/usr/bin/nightcap')
    (p300 / 'status').write_text('Name:\tnightcap\nState:\tZ (zombie)\n')
    (p300 / 'statm').write_text('1000 900 0 0 0 0 0\n')
    (p300 / 'maps').write_text(
        '7f79b0440000-7f79b6fbe000 r-xp 00000000 103:03 8131164 /path/to/libroblox.so\n'
    )

    def mock_path(p: str | Path) -> Path:
        return tmp_path if str(p) == '/proc' else Path(p)

    monkeypatch.setattr('fleasion.proxy.android_community_keys.Path', mock_path)

    assert find_android_community_pid() == (100, 'mocktail')
    assert find_android_community_pid('mocktail') == (100, 'mocktail')
    assert find_android_community_pid('cordial') is None
    assert find_android_community_pid('nightcap') is None


def test_find_android_community_pid_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'win32')
    assert find_android_community_pid() is None


def test_scan_android_community_safe_execution() -> None:
    # Must not raise on invalid PID
    result = scan(0, client_random='ab' * 32)
    assert isinstance(result, dict)


def test_live_mocktail_process_scanning() -> None:
    # If Mocktail is running live, verify detection and secret recovery
    match = find_android_community_pid('mocktail')
    if match is None:
        pytest.skip('Mocktail is not running')

    pid, client_name = match
    assert client_name == 'mocktail'
    keys = scan(pid)
    assert isinstance(keys, dict)
    for client_random, secrets in keys.items():
        assert len(client_random) == 64
        assert 'CLIENT_TRAFFIC_SECRET_0' in secrets
        assert 'SERVER_TRAFFIC_SECRET_0' in secrets
