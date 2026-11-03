"""Mocktail account switching never writes real login credentials in tests."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from fleasion.utils import roblox_auth
from fleasion.utils.roblox_auth import LinuxAuthWriteError, MOCKTAIL_LOCAL_AUTH_PROVIDER

@pytest.fixture
def mocktail_cookie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / '.local/share/mocktail/auth/roblox.cookie'
    path.parent.mkdir(parents=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_bytes(b'.ROBLOSECURITY=original-synthetic\n')
    path.chmod(0o600)
    monkeypatch.setattr(roblox_auth, '_selected_linux_client_installation', lambda: None)
    monkeypatch.setattr(roblox_auth, 'running_mocktail_players', lambda: ())
    for name in ('MOCKTAIL_USE_LIBSECRET', 'MOCKTAIL_COOKIE_FILE', 'MOCKTAIL_ROBLOX_COOKIES'):
        monkeypatch.delenv(name, raising=False)
    return path


def test_mocktail_switch_atomic_preserves_format_and_permissions(mocktail_cookie: Path) -> None:
    before = mocktail_cookie.stat()
    assert MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'other-synthetic')
    assert mocktail_cookie.read_bytes() == b'.ROBLOSECURITY=other-synthetic\n'
    after = mocktail_cookie.stat()
    assert (after.st_mode & 0o777) == 0o600
    assert after.st_ino != before.st_ino
    assert (mocktail_cookie.parent / '.roblox.cookie.mocktail-writer.lock').stat().st_mode & 0o777 == 0o600
    assert MOCKTAIL_LOCAL_AUTH_PROVIDER.read_roblosecurity(mocktail_cookie) == 'other-synthetic'


def test_mocktail_switch_preserves_other_cookies_and_newline(mocktail_cookie: Path) -> None:
    old = b'RBXEventTrackerV2=tracker-synthetic; .ROBLOSECURITY=original-synthetic; Preferences=abc\n'
    mocktail_cookie.write_bytes(old)
    assert MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'replacement')
    assert mocktail_cookie.read_bytes() == (
        b'RBXEventTrackerV2=tracker-synthetic; .ROBLOSECURITY=replacement; Preferences=abc\n'
    )


@pytest.mark.parametrize(
    'payload',
    [
        b'not-a-cookie\n', b'.ROBLOSECURITY=first; .ROBLOSECURITY=second\n',
        b'.ROBLOSECURITY=first\nsecond=bad\n', b'.ROBLOSECURITY=first\0',
        b'.ROBLOSECURITY=first\xff', b'\n',
    ],
)
def test_mocktail_rejects_unknown_cookie_formats(mocktail_cookie: Path, payload: bytes) -> None:
    mocktail_cookie.write_bytes(payload)
    with pytest.raises(LinuxAuthWriteError) as exc:
        MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'replacement')
    assert exc.value.code == 'cookie_store_unknown_format'
    assert mocktail_cookie.read_bytes() == payload


@pytest.mark.parametrize('bad', ['', 'bad\nvalue', 'bad;value', 'bad value', 'caf\u00e9'])
def test_mocktail_rejects_malformed_selected_tokens(mocktail_cookie: Path, bad: str) -> None:
    original = mocktail_cookie.read_bytes()
    with pytest.raises(LinuxAuthWriteError) as exc:
        MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, bad)
    assert exc.value.code == 'invalid_cookie_value'
    assert mocktail_cookie.read_bytes() == original


def test_mocktail_exclusive_writer_lock_refuses_racing_writer(mocktail_cookie: Path) -> None:
    lock = mocktail_cookie.parent / '.roblox.cookie.mocktail-writer.lock'
    with lock.open('w+b') as handle:
        lock.chmod(0o600)
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(LinuxAuthWriteError) as exc:
            MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'replacement')
        assert exc.value.code == 'cookie_store_busy'
        assert mocktail_cookie.read_bytes() == b'.ROBLOSECURITY=original-synthetic\n'


def test_mocktail_switch_refuses_running_player(
    mocktail_cookie: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(roblox_auth, 'running_mocktail_players', lambda: (object(),))
    with pytest.raises(LinuxAuthWriteError) as exc:
        MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'replacement')
    assert exc.value.code == 'mocktail_running'
    assert mocktail_cookie.read_bytes() == b'.ROBLOSECURITY=original-synthetic\n'


@pytest.mark.parametrize(
    'config_text',
    [
        'auth:\n  use_libsecret: true\n',
        'auth:\n  use_libsecret: YES\n',
        'auth: {use_libsecret: true}\n',
    ],
)
def test_mocktail_secure_or_unrecognized_config_refuses_plaintext(
    mocktail_cookie: Path, config_text: str
) -> None:
    config = mocktail_cookie.parents[4] / '.config/mocktail/config.yaml'
    config.parent.mkdir(parents=True)
    config.write_text(config_text)
    with pytest.raises(LinuxAuthWriteError) as exc:
        MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'replacement')
    assert exc.value.code == 'mocktail_secure_or_overridden_auth'
    assert mocktail_cookie.read_bytes() == b'.ROBLOSECURITY=original-synthetic\n'
    assert MOCKTAIL_LOCAL_AUTH_PROVIDER.read_roblosecurity(mocktail_cookie) is None


def test_mocktail_config_explicitly_disabling_secure_storage_allows_switch(mocktail_cookie: Path) -> None:
    config = mocktail_cookie.parents[4] / '.config/mocktail/config.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('auth:\n  use_libsecret: false\n')
    assert MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'replacement')


@pytest.mark.parametrize('name', ['MOCKTAIL_USE_LIBSECRET', 'MOCKTAIL_COOKIE_FILE', 'MOCKTAIL_ROBLOX_COOKIES'])
def test_mocktail_overridden_auth_refuses_plaintext(
    mocktail_cookie: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, '/tmp/overridden' if name != 'MOCKTAIL_USE_LIBSECRET' else '1')
    with pytest.raises(LinuxAuthWriteError) as exc:
        MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'replacement')
    assert exc.value.code == 'mocktail_secure_or_overridden_auth'


def test_mocktail_rejects_symlink_hardlink_and_unsafe_parent(
    mocktail_cookie: Path, tmp_path: Path
) -> None:
    original = mocktail_cookie.read_bytes()
    destination = tmp_path / 'protected'
    destination.write_bytes(original)
    mocktail_cookie.unlink()
    mocktail_cookie.symlink_to(destination)
    with pytest.raises(LinuxAuthWriteError):
        MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'replacement')
    assert destination.read_bytes() == original
    mocktail_cookie.unlink()
    os.link(destination, mocktail_cookie)
    with pytest.raises(LinuxAuthWriteError):
        MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'replacement')
    mocktail_cookie.unlink()
    mocktail_cookie.write_bytes(original)
    mocktail_cookie.chmod(0o600)
    mocktail_cookie.parent.chmod(0o755)
    with pytest.raises(LinuxAuthWriteError):
        MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'replacement')
    assert mocktail_cookie.read_bytes() == original


def test_mocktail_switch_detects_concurrent_cookie_change(
    mocktail_cookie: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = cast('Callable[[Path, bytes, os.stat_result], None]', roblox_auth.__dict__['_atomic_replace_linux_cookie_file'])

    def replaced_by_mocktail(path: Path, data: bytes, metadata: os.stat_result) -> None:
        sibling = path.with_suffix('.replacement')
        sibling.write_bytes(b'.ROBLOSECURITY=other-process\n')
        sibling.chmod(0o600)
        sibling.replace(path)
        original(path, data, metadata)

    monkeypatch.setattr(roblox_auth, '_atomic_replace_linux_cookie_file', replaced_by_mocktail)
    with pytest.raises(LinuxAuthWriteError) as exc:
        MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(mocktail_cookie, 'replacement')
    assert exc.value.code == 'cookie_store_changed_during_write'
    assert mocktail_cookie.read_bytes() == b'.ROBLOSECURITY=other-process\n'


def test_mocktail_cache_clear_refuses_symlinked_storage_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fleasion.utils import platform_linux
    from fleasion.utils.linux_clients import MOCKTAIL_CLIENT, LinuxClientInstallation

    installation = LinuxClientInstallation(
        MOCKTAIL_CLIENT, MOCKTAIL_CLIENT.paths(home=tmp_path, environ={}), tmp_path / 'mocktail'
    )
    data_root = installation.paths.data_root
    data_root.mkdir(parents=True)
    external = tmp_path / 'external-owned-data'
    external.mkdir()
    external_database = external / 'rbx-storage.db'
    external_database.write_text('unrelated data')
    (data_root / 'appData').symlink_to(external, target_is_directory=True)
    cache_root = installation.paths.cache_root
    external_cache = tmp_path / 'external-cache'
    external_cache.mkdir()
    (external_cache / 'rbx-storage').mkdir()
    cache_root.parent.mkdir(parents=True, exist_ok=True)
    cache_root.symlink_to(external_cache, target_is_directory=True)
    monkeypatch.setattr(
        platform_linux, 'get_selected_linux_client_installation', lambda: installation
    )
    monkeypatch.setattr(platform_linux, 'is_roblox_running', lambda: False)
    monkeypatch.setattr(platform_linux, 'APP_CACHE_DIR', tmp_path / 'unused-fleasion-cache')
    messages = platform_linux.delete_cache()
    assert external_database.read_text() == 'unrelated data'
    assert (external_cache / 'rbx-storage').is_dir()
    assert any('outside a safe Mocktail storage path' in message for message in messages)


def test_mocktail_selected_client_set_roblosecurity_integration(
    mocktail_cookie: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        roblox_auth, '_selected_linux_local_auth_candidate',
        lambda: (MOCKTAIL_LOCAL_AUTH_PROVIDER, mocktail_cookie),
    )
    monkeypatch.setattr(roblox_auth.sys, 'platform', 'linux')
    assert roblox_auth.set_roblosecurity('saved-account-synthetic')
    assert mocktail_cookie.read_bytes() == b'.ROBLOSECURITY=saved-account-synthetic\n'
