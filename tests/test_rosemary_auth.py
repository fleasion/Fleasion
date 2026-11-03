"""Regression checks for Rosemary Player's read-only macOS cookie store."""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from fleasion.utils import roblox_auth
from fleasion.utils.linux_clients import ROSEMARY_CLIENT, LinuxClientInstallation


def _installation(home: Path) -> LinuxClientInstallation:
    return LinuxClientInstallation(
        ROSEMARY_CLIENT,
        replace(ROSEMARY_CLIENT.paths(home=home), resource_roots=()),
        executable=None,
    )


def _row(value: str, *, domain: str = 'roblox.com', secure: str = '1', expires: int | None = None) -> bytes:
    expires = int(time.time()) + 86400 if expires is None else expires
    return f'.ROBLOSECURITY\t{value}\t{domain}\t/\t{secure}\t{expires}\n'.encode()


def test_rosemary_parser_accepts_only_live_secure_domain_scoped_cookie() -> None:
    valid = _row('safe-synthetic-cookie')
    assert roblox_auth._parse_rosemary_cookie_tsv(b'GuestData\tabc\troblox.com\t/\t0\t0\n' + valid) == 'safe-synthetic-cookie'
    assert roblox_auth._parse_rosemary_cookie_tsv(_row('bad', domain='attacker.example')) is None
    assert roblox_auth._parse_rosemary_cookie_tsv(_row('bad', secure='0')) is None
    assert roblox_auth._parse_rosemary_cookie_tsv(_row('bad', expires=int(time.time()) - 2)) is None
    assert roblox_auth._parse_rosemary_cookie_tsv(valid + _row('different')) is None
    assert roblox_auth._parse_rosemary_cookie_tsv(b'\xff') is None


def test_rosemary_cookie_source_uses_nutmeg_prefix_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('ROSEMARY_PREFIX', raising=False)
    monkeypatch.delenv('ROSEMARY_PREFIX_HOME', raising=False)
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    monkeypatch.delenv('XDG_DATA_HOME', raising=False)
    installation = _installation(tmp_path)
    provider = roblox_auth.ROSEMARY_LOCAL_AUTH_PROVIDER
    default_path = provider.cookie_path(installation)
    assert default_path == tmp_path / '.local/share/nutmeg/prefix/root/Users' / tmp_path.name / 'Library/Cookies/com.roblox.RobloxPlayer.cookies.tsv'
    config = tmp_path / '.config/nutmeg/config.json'
    config.parent.mkdir(parents=True)
    custom_prefix = tmp_path / 'Games/custom-rosemary-prefix'
    config.write_text(json.dumps({'prefix_path': str(custom_prefix)}))
    assert provider.cookie_path(installation) == custom_prefix / 'root/Users' / tmp_path.name / 'Library/Cookies/com.roblox.RobloxPlayer.cookies.tsv'
    config.write_text(json.dumps({'prefix_path': str(custom_prefix), 'prefix_home': 'host'}))
    assert provider.cookie_path(installation) == tmp_path / 'Library/Cookies/com.roblox.RobloxPlayer.cookies.tsv'


def test_rosemary_reads_plaintext_tsv_without_writing(tmp_path: Path) -> None:
    path = tmp_path / 'com.roblox.RobloxPlayer.cookies.tsv'
    data = _row('synthetic-local-token')
    path.write_bytes(data)
    path.chmod(0o644)
    provider = roblox_auth.ROSEMARY_LOCAL_AUTH_PROVIDER
    assert provider.read_roblosecurity(path) == 'synthetic-local-token'
    assert path.read_bytes() == data
    assert path.stat().st_mode & 0o777 == 0o644
    with pytest.raises(roblox_auth.LinuxAuthWriteError) as error:
        provider.write_roblosecurity(path, 'replacement')
    assert error.value.code == 'rosemary_cookie_read_only'
    assert path.read_bytes() == data


def test_rosemary_rejects_linked_and_wrong_owner_cookie_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_path = tmp_path / 'real.tsv'
    real_path.write_bytes(_row('synthetic-local-token'))
    link = tmp_path / 'linked.tsv'
    link.symlink_to(real_path)
    provider = roblox_auth.ROSEMARY_LOCAL_AUTH_PROVIDER
    assert provider.read_roblosecurity(link) is None
    hardlink = tmp_path / 'hardlink.tsv'
    os.link(real_path, hardlink)
    assert provider.read_roblosecurity(real_path) is None
    hardlink.unlink()
    monkeypatch.setattr(roblox_auth.os, 'getuid', lambda: real_path.stat().st_uid + 1)
    assert provider.read_roblosecurity(real_path) is None


def test_rosemary_selected_linux_auth_source_is_player_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = _installation(tmp_path)
    provider = roblox_auth.ROSEMARY_LOCAL_AUTH_PROVIDER
    path = provider.cookie_path(installation)
    path.parent.mkdir(parents=True)
    path.write_bytes(_row('player-only-test-token'))
    studio = path.with_name('com.Roblox.RobloxStudio.cookies.tsv')
    studio.write_bytes(_row('studio-should-not-be-read'))
    monkeypatch.setattr(roblox_auth, '_selected_linux_client_installation', lambda: installation)
    assert roblox_auth._discover_local_roblosecurity([], []) == 'player-only-test-token'
