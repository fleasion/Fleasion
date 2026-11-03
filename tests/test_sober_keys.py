"""Unit tests for Sober Android OpenSSL 3.5.0 TLS 1.3 memory key scanner."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from fleasion.proxy.sober_keys import (
    find_sober_pid,
    scan,
)


def test_find_sober_pid_synthetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')

    # PID 100: Sober Main engine process with high RSS and cgroup
    p100 = tmp_path / '100'
    p100.mkdir()
    (p100 / 'cgroup').write_text(
        '0::/user.slice/app-flatpak-org.vinegarhq.Sober-123.scope\n'
    )
    (p100 / 'cmdline').write_bytes(b'/proc/self/exe\x00--\x00')
    (p100 / 'comm').write_text('Main\n')
    (p100 / 'status').write_text('Name:\tMain\nState:\tS (sleeping)\n')
    (p100 / 'statm').write_text('250000 200000 0 0 0 0 0\n')

    # PID 200: Sober helper process with lower RSS
    p200 = tmp_path / '200'
    p200.mkdir()
    (p200 / 'cgroup').write_text(
        '0::/user.slice/app-flatpak-org.vinegarhq.Sober-123.scope\n'
    )
    (p200 / 'cmdline').write_bytes(b'/proc/self/exe\x00--\x00')
    (p200 / 'comm').write_text('sober\n')
    (p200 / 'status').write_text('Name:\tsober\nState:\tS (sleeping)\n')
    (p200 / 'statm').write_text('5000 4000 0 0 0 0 0\n')

    # PID 300: Sober process in zombie state
    p300 = tmp_path / '300'
    p300.mkdir()
    (p300 / 'cgroup').write_text(
        '0::/user.slice/app-flatpak-org.vinegarhq.Sober-123.scope\n'
    )
    (p300 / 'cmdline').write_bytes(b'/proc/self/exe\x00--\x00')
    (p300 / 'comm').write_text('Main\n')
    (p300 / 'status').write_text('Name:\tMain\nState:\tZ (zombie)\n')
    (p300 / 'statm').write_text('1000 800 0 0 0 0 0\n')

    # PID 400: Unrelated process
    p400 = tmp_path / '400'
    p400.mkdir()
    (p400 / 'cgroup').write_text('0::/user.slice/session.scope\n')
    (p400 / 'cmdline').write_bytes(b'/usr/bin/bash\x00')
    (p400 / 'comm').write_text('bash\n')
    (p400 / 'status').write_text('Name:\tbash\nState:\tS (sleeping)\n')
    (p400 / 'statm').write_text('1000 500 0 0 0 0 0\n')

    def mock_path(p: str | Path) -> Path:
        return tmp_path if str(p) == '/proc' else Path(p)

    monkeypatch.setattr('fleasion.proxy.sober_keys.Path', mock_path)

    assert find_sober_pid() == 100


def test_unrelated_command_line_mentioning_sober_is_not_a_client(tmp_path: Path) -> None:
    from fleasion.proxy import sober_keys

    proc = tmp_path / '123'
    proc.mkdir()
    (proc / 'comm').write_text('python3\n')
    (proc / 'cgroup').write_text('0::/user.slice/session.scope\n')
    (proc / 'cmdline').write_bytes(
        b'/usr/bin/python3\0/tmp/fleasion-sober-debugger.py\0'
    )
    assert not sober_keys._is_sober_entry(proc)  # pyright: ignore[reportPrivateUsage]


def test_find_sober_pid_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'win32')
    assert find_sober_pid() is None


def test_scan_sober_safe_execution() -> None:
    # Must not raise on invalid PID
    result = scan(0, client_random='ab' * 32)
    assert isinstance(result, dict)


def test_live_sober_process_scanning() -> None:
    # If Sober is running live, verify detection and secret recovery
    pid = find_sober_pid()
    if pid is None:
        pytest.skip('Sober is not running')

    keys = scan(pid)
    assert isinstance(keys, dict)
    for client_random, secrets in keys.items():
        assert len(client_random) == 64
        assert 'CLIENT_TRAFFIC_SECRET_0' in secrets
        assert 'SERVER_TRAFFIC_SECRET_0' in secrets
