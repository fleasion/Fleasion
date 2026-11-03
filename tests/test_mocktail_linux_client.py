"""Mocktail native Linux integration and process isolation tests."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from fleasion.utils import mocktail_launch_interceptor as interceptor
from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import (
    MOCKTAIL_CLIENT,
    LinuxClientInstallation,
    detect_installed_clients,
    read_mocktail_session,
    running_mocktail_players,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _fixture(
    tmp_path: Path, *, pid: int = 4321, generation: int = 456789
) -> tuple[Path, Path]:
    root = tmp_path / 'proc'
    process = root / str(pid)
    process.mkdir(parents=True, exist_ok=True)
    binary = tmp_path / 'bin/mocktail'
    binary.parent.mkdir(exist_ok=True)
    binary.write_bytes(b'ELF-test-mocktail')
    binary.chmod(0o700)
    (process / 'exe').symlink_to(binary)
    (process / 'cwd').symlink_to(tmp_path, target_is_directory=True)
    (process / 'cmdline').write_bytes(
        str(binary).encode() + b'\0--launch-uri\0roblox-player:synthetic\0'
    )
    (process / 'environ').write_bytes(
        f'HOME={tmp_path}'.encode() + b'\0CUSTOM_OPTION=present\0'
    )
    (process / 'comm').write_text('Main\n')
    (process / 'maps').write_text(
        '1000-2000 r-xp 00000000 00:01 123 /mocktail/libroblox.so\n'
    )
    (process / 'stat').write_text(
        f'{pid} (Main) S ' + ' '.join(['0'] * 18 + [str(generation)] + ['0'] * 2)
    )
    return root, binary


def _installation(tmp_path: Path, binary: Path) -> LinuxClientInstallation:
    return LinuxClientInstallation(
        MOCKTAIL_CLIENT, MOCKTAIL_CLIENT.paths(home=tmp_path, environ={}), binary
    )


def test_detect_installed_mocktail_native_with_synthetic_home(tmp_path: Path) -> None:
    _root, executable = _fixture(tmp_path)
    result = detect_installed_clients(
        home=tmp_path,
        environ={},
        which=lambda name: str(executable) if name == 'mocktail' else None,
    )
    assert len(result) == 1
    assert result[0].key == 'mocktail'
    assert result[0].launch_command('roblox-player:synthetic') == [
        str(executable), '--launch-uri', 'roblox-player:synthetic'
    ]
    assert result[0].launch_command() == [str(executable)]
    assert result[0].paths.config_file == tmp_path / '.config/mocktail/config.yaml'
    assert result[0].paths.data_root == tmp_path / '.local/share/mocktail/android/data'
    assert result[0].paths.cache_root == tmp_path / '.cache/mocktail'
    assert result[0].paths.resource_roots == ()


def test_mocktail_pid_detection_rejects_helper_and_wrong_binary(tmp_path: Path) -> None:
    root, binary = _fixture(tmp_path)
    sessions = running_mocktail_players(proc_root=root, executable=binary)
    assert len(sessions) == 1
    assert sessions[0].pid == 4321
    assert sessions[0].start_ticks == 456789

    helper = root / '4322'
    helper.mkdir()
    helper_binary = tmp_path / 'bin/mocktail_updater'
    helper_binary.write_bytes(b'helper')
    (helper / 'exe').symlink_to(helper_binary)
    (helper / 'cmdline').write_bytes(str(helper_binary).encode() + b'\0')
    (helper / 'stat').write_text('4322 (mocktail_updater) S ' + '0 ' * 24)
    assert running_mocktail_players(proc_root=root) == sessions

    unrelated = tmp_path / 'other/mocktail'
    unrelated.parent.mkdir()
    unrelated.write_bytes(b'not installed executable')
    unrelated.chmod(0o700)
    (root / '4321/exe').unlink()
    (root / '4321/exe').symlink_to(unrelated)
    assert read_mocktail_session(root / '4321', executable=binary) is None


def test_mocktail_capture_and_exact_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, binary = _fixture(tmp_path)
    session = running_mocktail_players(proc_root=root, executable=binary)[0]
    snapshot = interceptor.capture_mocktail_launch(session, proc_root=root)
    assert snapshot is not None
    assert snapshot.argv == (
        str(binary), '--launch-uri', 'roblox-player:synthetic'
    )
    assert snapshot.env['CUSTOM_OPTION'] == 'present'
    stopped: list[int] = []
    spawned: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        interceptor.os, 'pidfd_open',
        lambda _pid, _flags: os.open('/dev/null', os.O_RDONLY),
    )
    monkeypatch.setattr(
        interceptor.signal, 'pidfd_send_signal',
        lambda _fd, sig: stopped.append(sig),
    )
    monkeypatch.setattr(
        interceptor.subprocess, 'Popen',
        lambda argv, **kwargs: spawned.append((argv, kwargs)) or SimpleNamespace(pid=9000),
    )
    assert interceptor.replay_mocktail_launch(
        snapshot, proxy_url='http://127.0.0.1:58443',
        proxy_environment={'HTTPS_PROXY': 'http://127.0.0.1:58443'},
        proc_root=root,
    )
    assert stopped == [interceptor.signal.SIGKILL]
    assert len(spawned) == 1
    argv, options = spawned[0]
    assert argv == list(snapshot.argv)
    assert options['executable'] == str(binary)
    assert options['cwd'] == tmp_path
    assert options['env']['CUSTOM_OPTION'] == 'present'
    assert options['env']['FLEASION_MOCKTAIL_ENV_REPLAY'] == '1'
    assert options['env']['HTTPS_PROXY'] == 'http://127.0.0.1:58443'

    (root / '4321/stat').write_text(
        '4321 (mocktail) S ' + ' '.join(['0'] * 18 + ['456790'] + ['0'] * 2)
    )
    assert not interceptor.replay_mocktail_launch(
        snapshot, proxy_url='http://127.0.0.1:58443',
        proxy_environment={}, proc_root=root,
    )
    assert len(stopped) == 1


def test_mocktail_watcher_skips_preexisting_and_proxied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _bin = _fixture(tmp_path)
    replays: list[int] = []
    monkeypatch.setattr(
        interceptor, 'replay_mocktail_launch',
        lambda snapshot, **_kw: replays.append(snapshot.session.pid) or True,
    )
    watcher = interceptor.MocktailLaunchInterceptor(
        is_armed=lambda: True,
        proxy_url=lambda: 'http://127.0.0.1:58443',
        proxy_environment=lambda: {'HTTPS_PROXY': 'http://127.0.0.1:58443'},
        proc_root=root,
    )
    watcher._poll_once()
    assert replays == []
    _fixture(tmp_path, pid=4322)
    watcher._poll_once()
    assert replays == [4322]
    _fixture(tmp_path, pid=4323)
    with (root / '4323/environ').open('ab') as out:
        out.write(b'HTTPS_PROXY=http://127.0.0.1:58443\0')
    watcher._poll_once()
    assert replays == [4322]
    _fixture(tmp_path, pid=4324)
    with (root / '4324/environ').open('ab') as out:
        out.write(b'FLEASION_MOCKTAIL_ENV_REPLAY=1\0')
    watcher._poll_once()
    assert replays == [4322]
    watcher.stop()


def test_mocktail_cache_preserves_auth_payload_and_clientsettings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / 'mocktail'
    binary.write_bytes(b'program')
    installation = _installation(tmp_path, binary)
    storage = installation.paths.data_root / 'rbx-storage'
    storage.mkdir(parents=True)
    (storage / 'cache-entry').write_text('cache')
    cache = installation.paths.cache_storage_dir
    assert cache is not None
    cache.mkdir(parents=True)
    (cache / 'cache-entry').write_text('cache')
    db = installation.paths.storage_db
    assert db is not None
    db.parent.mkdir(parents=True)
    db.write_text('database')
    auth = installation.paths.flatpak_root / 'auth/roblox.cookie'
    auth.parent.mkdir(parents=True)
    auth.write_text('sensitive cookie must survive')
    payload = installation.paths.flatpak_root / 'payloads/v1/libroblox.so'
    payload.parent.mkdir(parents=True)
    payload.write_text('managed payload must survive')
    settings = installation.paths.cache_root / 'clientsettings/GoogleAndroidApp.json'
    settings.parent.mkdir(parents=True)
    settings.write_text('settings must survive')

    monkeypatch.setattr(
        platform_linux, 'get_selected_linux_client_installation', lambda: installation
    )
    monkeypatch.setattr(platform_linux, 'is_roblox_running', lambda: False)
    monkeypatch.setattr(platform_linux, 'APP_CACHE_DIR', tmp_path / 'no-fleasion-cache')
    messages = platform_linux.delete_cache()
    assert any('Mocktail Android rbx-storage' in line for line in messages)
    assert not storage.exists()
    assert not cache.exists()
    assert not db.exists()
    assert auth.read_text() == 'sensitive cookie must survive'
    assert payload.read_text() == 'managed payload must survive'
    assert settings.read_text() == 'settings must survive'


def test_mocktail_cookie_switch_and_symlink_rejection(tmp_path: Path) -> None:
    from fleasion.utils.roblox_auth import (
        LinuxAuthWriteError,
        MOCKTAIL_LOCAL_AUTH_PROVIDER,
    )

    installation = _installation(tmp_path, tmp_path / 'mocktail')
    path = MOCKTAIL_LOCAL_AUTH_PROVIDER.cookie_path(installation)
    assert path == tmp_path / '.local/share/mocktail/auth/roblox.cookie'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text('.ROBLOSECURITY=synthetic-token\n')
    path.chmod(0o600)
    assert MOCKTAIL_LOCAL_AUTH_PROVIDER.read_roblosecurity(path) == 'synthetic-token'
    assert MOCKTAIL_LOCAL_AUTH_PROVIDER.write_roblosecurity(path, 'other-synthetic')
    assert MOCKTAIL_LOCAL_AUTH_PROVIDER.read_roblosecurity(path) == 'other-synthetic'
    path.unlink()
    other = tmp_path / 'other'
    other.write_text('.ROBLOSECURITY=unexpected')
    path.symlink_to(other)
    assert MOCKTAIL_LOCAL_AUTH_PROVIDER.read_roblosecurity(path) is None


def test_mocktail_replay_terminates_verified_lock_child_before_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, binary = _fixture(tmp_path)
    child_pid = 4335
    child = root / str(child_pid)
    child.mkdir()
    (child / 'exe').symlink_to(binary)
    (child / 'comm').write_text('mocktail\n')
    (child / 'stat').write_text(
        f'{child_pid} (mocktail) S {4321} ' + '0 ' * 19
    )
    session = running_mocktail_players(proc_root=root, executable=binary)[0]
    snapshot = interceptor.capture_mocktail_launch(session, proc_root=root)
    assert snapshot is not None
    signaled: list[tuple[int, int]] = []
    spawned: list[object] = []
    # Preserve the FD/PID association rather than mocking it as a single
    # arbitrary integer. Signals must affect the child before the engine.
    original_open = interceptor.os.open
    pidfds: dict[int, int] = {}

    def open_pidfd(pid: int, _flags: int) -> int:
        fd = original_open('/dev/null', os.O_RDONLY)
        pidfds[fd] = pid
        return fd

    monkeypatch.setattr(interceptor.os, 'pidfd_open', open_pidfd)
    monkeypatch.setattr(
        interceptor.signal, 'pidfd_send_signal',
        lambda fd, sig: signaled.append((pidfds[fd], sig)),
    )
    monkeypatch.setattr(interceptor, '_wait_pid_exit', lambda _fd, _ms: True)
    monkeypatch.setattr(
        interceptor.subprocess, 'Popen',
        lambda *args, **kwargs: spawned.append((args, kwargs)) or SimpleNamespace(pid=4444),
    )

    assert interceptor.replay_mocktail_launch(
        snapshot,
        proxy_url='http://127.0.0.1:58443',
        proxy_environment={'HTTPS_PROXY': 'http://127.0.0.1:58443'},
        proc_root=root,
    )
    assert signaled == [
        (child_pid, interceptor.signal.SIGTERM),
        (4321, interceptor.signal.SIGKILL),
    ]
    assert len(spawned) == 1
