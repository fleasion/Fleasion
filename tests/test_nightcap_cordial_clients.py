"""Nightcap and Cordial Linux client identity, storage and proxy isolation."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import pytest

from fleasion.proxy import android_community_keys
from fleasion.utils import mocktail_launch_interceptor, platform_linux, roblox_auth
from fleasion.utils.linux_clients import (
    CORDIAL_CLIENT,
    MOCKTAIL_CLIENT,
    NIGHTCAP_CLIENT,
    CommandRunner,
    LinuxClientInstallation,
    detect_installed_clients,
    running_cordial_players,
    running_mocktail_players,
    select_linux_client,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _installed(client: object, home: Path, *, executable: Path | None = None) -> LinuxClientInstallation:
    assert isinstance(client, type(NIGHTCAP_CLIENT))
    return LinuxClientInstallation(client, client.paths(home=home, environ={}), executable)


def _process(
    proc_root: Path,
    *,
    pid: int,
    executable: Path,
    comm: str,
    cgroup: str = '',
    generation: int = 12345,
    libroblox: bool = True,
) -> Path:
    process = proc_root / str(pid)
    process.mkdir(parents=True)
    (process / 'comm').write_text(comm + '\n')
    (process / 'exe').symlink_to(executable)
    (process / 'cmdline').write_bytes(os.fsencode(str(executable)) + b'\0')
    (process / 'cgroup').write_text(cgroup)
    (process / 'maps').write_text(
        '1000-2000 r-xp 00000000 00:01 123 /game/libroblox.so\n' if libroblox
        else '1000-2000 r-xp 00000000 00:01 123 /usr/lib/libc.so\n'
    )
    (process / 'status').write_text('Name:\tMain\nState:\tS (sleeping)\n')
    (process / 'statm').write_text('1000 800 0 0 0 0 0\n')
    (process / 'stat').write_text(
        f'{pid} ({comm}) S ' + ' '.join(['0'] * 18 + [str(generation)] + ['0'] * 2)
    )
    return process


def test_storage_layouts_and_launch_commands(tmp_path: Path) -> None:
    nightcap_flatpak = _installed(NIGHTCAP_CLIENT, tmp_path, executable=Path('/usr/bin/flatpak'))
    root = tmp_path / '.var/app/io.github.CoderDayton.nightcap'
    assert nightcap_flatpak.paths.data_root == root / 'data/mocktail/android/data'
    assert nightcap_flatpak.paths.config_file == root / 'config/mocktail/config.yaml'
    assert nightcap_flatpak.paths.resource_roots == (root / 'data/mocktail/asset_overlay',)
    assert nightcap_flatpak.launch_command('roblox-player:synthetic') == [
        '/usr/bin/flatpak', 'run', NIGHTCAP_CLIENT.app_id, 'roblox-player:synthetic',
    ]
    native_nightcap = _installed(replace(NIGHTCAP_CLIENT, runtime='native'), tmp_path, executable=Path('/usr/bin/mocktail'))
    assert native_nightcap.paths.resource_roots == (
        tmp_path / '.local/share/mocktail/asset_overlay',
    )
    assert native_nightcap.launch_command('roblox-player:synthetic') == [
        '/usr/bin/mocktail', '--launch-uri', 'roblox-player:synthetic',
    ]

    cordial_flatpak = _installed(CORDIAL_CLIENT, tmp_path, executable=Path('/usr/bin/flatpak'))
    cordial_root = tmp_path / '.var/app/io.github.luohoa97.Cordial'
    assert cordial_flatpak.paths.resource_roots == (cordial_root / 'config/cordial/overlay',)
    assert cordial_flatpak.paths.data_root == cordial_root / 'data/cordial'
    assert cordial_flatpak.paths.storage_db is None  # No global database: per-profile only
    native_cordial = _installed(replace(CORDIAL_CLIENT, runtime='native'), tmp_path, executable=Path('/usr/bin/cordial-shell'))
    assert native_cordial.launch_command('roblox-player:synthetic') == [
        '/usr/bin/cordial-shell', 'roblox-player:synthetic',
    ]


def test_flatpak_installed_detection_and_auto_selection(tmp_path: Path) -> None:
    seen: list[str] = []

    def run(
        command: Sequence[str], *, env: Mapping[str, str], capture_output: bool,
        text: Literal[True], timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, capture_output, text, timeout
        app = command[-1]
        seen.append(app)
        return subprocess.CompletedProcess(
            list(command), 0 if app in {NIGHTCAP_CLIENT.app_id, CORDIAL_CLIENT.app_id} else 1, '', ''
        )

    installed = detect_installed_clients(
        home=tmp_path, environ={},
        which=lambda name: '/usr/bin/flatpak' if name == 'flatpak' else None,
        run=cast('CommandRunner', run),
    )
    assert [item.key for item in installed] == ['nightcap', 'cordial']
    assert NIGHTCAP_CLIENT.app_id in seen
    assert CORDIAL_CLIENT.app_id in seen
    assert select_linux_client(installed=installed, running_client_starts={
        'nightcap': 100, 'cordial': 200,
    }).key == 'cordial'
    assert select_linux_client('nightcap', installed=installed).key == 'nightcap'


def test_engine_identity_isolates_flatpaks_and_helpers(tmp_path: Path) -> None:
    root = tmp_path / 'proc'
    root.mkdir()
    mocktail = tmp_path / 'mocktail'
    cordial = tmp_path / 'cordial-run'
    mocktail.write_bytes(b'test-engine')
    cordial.write_bytes(b'test-engine')

    _process(root, pid=101, executable=mocktail, comm='Main',
             cgroup='0::/user.slice/app-flatpak-io.github.CoderDayton.nightcap-123.scope')
    _process(root, pid=102, executable=mocktail, comm='Main',
             cgroup='0::/user.slice/app-flatpak-space.bigrat.mocktail-789.scope')
    _process(root, pid=103, executable=cordial, comm='Main',
             cgroup='0::/user.slice/app-flatpak-io.github.luohoa97.Cordial-456.scope')
    _process(root, pid=104, executable=cordial, comm='cordial-shell',
             cgroup='0::/user.slice/app-flatpak-io.github.luohoa97.Cordial-456.scope')
    _process(root, pid=105, executable=cordial, comm='cordial-run',
             cgroup='0::/user.slice/app-flatpak-other.runtime-456.scope')

    assert [item.pid for item in running_mocktail_players(proc_root=root, flatpak_id=NIGHTCAP_CLIENT.app_id)] == [101]
    assert [item.pid for item in running_mocktail_players(proc_root=root, flatpak_id=MOCKTAIL_CLIENT.app_id)] == [102]
    assert [item.pid for item in running_cordial_players(proc_root=root, flatpak_id=CORDIAL_CLIENT.app_id)] == [103]


def test_tls_identity_never_confuses_flatpak_nightcap_with_mocktail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = tmp_path / 'proc'
    proc.mkdir()
    binary = tmp_path / 'mocktail'
    binary.write_bytes(b'test')
    nightcap = _process(
        proc, pid=101, executable=binary, comm='Main',
        cgroup='0::/app-flatpak-io.github.CoderDayton.nightcap-123.scope',
    )
    mocktail = _process(
        proc, pid=102, executable=binary, comm='Main',
        cgroup='0::/app-flatpak-space.bigrat.mocktail-456.scope',
    )
    assert android_community_keys._client_identity(nightcap, ('mocktail ', 'mocktail', 'Main')) == 'nightcap'
    assert android_community_keys._client_identity(mocktail, ('mocktail ', 'mocktail', 'Main')) == 'mocktail'
    assert android_community_keys._client_identity(
        nightcap, ('cordial-run ', 'cordial-run', 'Main')
    ) == 'cordial'

    from fleasion.proxy import splice_keys
    received: list[str | None] = []
    monkeypatch.setattr(splice_keys, 'find_android_community_pid',
                        lambda key: received.append(key) or (101, str(key)))
    monkeypatch.setattr(splice_keys, 'scan_android_community',
                        lambda _pid, **_kwargs: {'synthetic': {}})
    for client in ('nightcap', 'cordial', 'mocktail'):
        assert splice_keys.KeyProvider(linux_client_key=client)._read('aa' * 32)
    assert received == ['nightcap', 'cordial', 'mocktail']


def test_nightcap_local_cookie_provider_preserves_isolated_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _installed(replace(NIGHTCAP_CLIENT, runtime='native'), tmp_path)
    provider = roblox_auth.NIGHTCAP_LOCAL_AUTH_PROVIDER
    path = provider.cookie_path(installation)
    assert path == tmp_path / '.local/share/mocktail/auth/roblox.cookie'
    path.parent.mkdir(parents=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_bytes(b'.ROBLOSECURITY=synthetic\n')
    path.chmod(0o600)
    monkeypatch.setattr(roblox_auth, '_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(roblox_auth, 'running_mocktail_players', lambda: ())
    for name in ('MOCKTAIL_USE_LIBSECRET', 'MOCKTAIL_COOKIE_FILE', 'MOCKTAIL_ROBLOX_COOKIES'):
        monkeypatch.delenv(name, raising=False)
    assert provider.read_roblosecurity(path) == 'synthetic'
    assert provider.write_roblosecurity(path, 'synthetic-replaced')
    assert path.read_bytes() == b'.ROBLOSECURITY=synthetic-replaced\n'
    assert 'cordial' not in roblox_auth.LINUX_LOCAL_AUTH_PROVIDERS_BY_KEY


def test_cordial_cache_clear_preserves_profiles_and_blocks_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _installed(replace(CORDIAL_CLIENT, runtime='native'), tmp_path)
    profiles = installation.paths.data_root / 'profiles'
    first = profiles / 'default'
    second = profiles / 'alt'
    first_cache = first / 'data/cache'
    second_cache = second / 'data/cache'
    asset_cache = first / 'data/files/appData/rbx-storage'
    first_cache.mkdir(parents=True)
    second_cache.mkdir(parents=True)
    asset_cache.mkdir(parents=True)
    (first_cache / 'asset').write_text('cache')
    (second_cache / 'asset').write_text('cache')
    (asset_cache / 'asset').write_text('cache')
    (first / 'flags.json').write_text('{"FFlagSynthetic":true}')
    (second / 'cookies').write_text('synthetic')
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'protected').write_text('untouched')
    (profiles / 'bad').symlink_to(external, target_is_directory=True)

    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(platform_linux, 'is_roblox_running', lambda _installation: False)
    monkeypatch.setattr(platform_linux, 'APP_CACHE_DIR', tmp_path / 'none')
    messages = platform_linux.delete_cache()
    assert not first_cache.exists()
    assert not second_cache.exists()
    assert not asset_cache.exists()
    assert (first / 'flags.json').read_text() == '{"FFlagSynthetic":true}'
    assert (second / 'cookies').read_text() == 'synthetic'
    assert (external / 'protected').read_text() == 'untouched'
    assert any('Cordial default Runtime cache' in message for message in messages)


def test_startup_launchers_select_client_before_engine_is_mapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.utils.linux_clients import running_community_launchers

    proc = tmp_path / 'proc'
    proc.mkdir()
    nightcap_exe = tmp_path / 'mocktail'
    cordial_exe = tmp_path / 'cordial-shell'
    nightcap_exe.write_bytes(b'nightcap-test')
    cordial_exe.write_bytes(b'cordial-test')
    _process(
        proc, pid=111, executable=nightcap_exe, comm='mocktail',
        cgroup='0::/app-flatpak-io.github.CoderDayton.nightcap-AAA.scope',
        libroblox=False, generation=500,
    )
    _process(
        proc, pid=222, executable=cordial_exe, comm='cordial-shell',
        cgroup='0::/app-flatpak-io.github.luohoa97.Cordial-BBB.scope',
        libroblox=False, generation=700,
    )
    _process(
        proc, pid=333, executable=cordial_exe, comm='cordial-shell',
        cgroup='0::/app-flatpak-other.app-CCC.scope',
        libroblox=False, generation=900,
    )
    assert running_mocktail_players(proc_root=proc) == ()
    assert running_cordial_players(proc_root=proc) == ()
    assert [session.pid for session in running_community_launchers(
        'mocktail', proc_root=proc, flatpak_id=NIGHTCAP_CLIENT.app_id
    )] == [111]
    assert [session.pid for session in running_community_launchers(
        'cordial-shell', proc_root=proc, flatpak_id=CORDIAL_CLIENT.app_id
    )] == [222]

    installed = [
        _installed(NIGHTCAP_CLIENT, tmp_path, executable=Path('/usr/bin/flatpak')),
        _installed(CORDIAL_CLIENT, tmp_path, executable=Path('/usr/bin/flatpak')),
    ]
    selected = select_linux_client(installed=installed, proc_root=proc)
    assert selected is not None and selected.key == 'cordial'
    monkeypatch.setattr(platform_linux, 'PROC_ROOT', proc)
    assert platform_linux._client_pids(installed[0]) == [111]
    assert platform_linux._client_pids(installed[1]) == [222]


def test_native_discovery_distinguishes_nightcap_from_mocktail(tmp_path: Path) -> None:
    applications = tmp_path / '.local/share/applications'
    applications.mkdir(parents=True)
    (applications / NIGHTCAP_CLIENT.desktop_id).write_text(
        '[Desktop Entry]\nName=Nightcap\nExec=env SDL_VIDEODRIVER=wayland,x11 mocktail %u\n'
    )
    mocktail = tmp_path / 'mocktail'
    mocktail.write_text('binary')
    mocktail.chmod(0o700)
    cordial_shell = tmp_path / 'cordial-shell'
    cordial_shell.write_text('binary')
    cordial_shell.chmod(0o700)

    native = detect_installed_clients(
        home=tmp_path, environ={},
        which=lambda command: str(mocktail) if command == 'mocktail'
              else str(cordial_shell) if command == 'cordial-shell' else None,
    )
    assert [item.key for item in native] == ['nightcap', 'cordial']
    assert all(item.client.runtime == 'native' for item in native)
    assert native[0].paths.config_file == tmp_path / '.config/mocktail/config.yaml'
    assert native[1].paths.config_file == tmp_path / '.config/cordial/shell.json'

    (applications / NIGHTCAP_CLIENT.desktop_id).unlink()
    ordinary = detect_installed_clients(
        home=tmp_path, environ={},
        which=lambda command: str(mocktail) if command == 'mocktail'
              else str(cordial_shell) if command == 'cordial-shell' else None,
    )
    assert [item.key for item in ordinary] == ['mocktail', 'cordial']


def test_native_replay_watcher_only_restarts_selected_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    root = tmp_path / 'proc'
    root.mkdir()
    a = tmp_path / 'nightcap/mocktail'
    b = tmp_path / 'mocktail/mocktail'
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_bytes(b'a')
    b.write_bytes(b'b')
    _process(root, pid=101, executable=a, comm='Main')
    _process(root, pid=102, executable=b, comm='Main')
    selection = [a]
    called: list[int] = []
    monkeypatch.setattr(
        mocktail_launch_interceptor, 'capture_mocktail_launch',
        lambda session, **_kwargs: SimpleNamespace(session=session, env={}),
    )
    monkeypatch.setattr(
        mocktail_launch_interceptor, 'replay_mocktail_launch',
        lambda snapshot, **_kwargs: called.append(snapshot.session.pid) or True,
    )
    watcher = mocktail_launch_interceptor.MocktailLaunchInterceptor(
        is_armed=lambda: True, proxy_url=lambda: 'http://127.0.0.1:58443',
        proxy_environment=lambda: {}, executable=lambda: selection[0], proc_root=root,
    )
    _process(root, pid=103, executable=b, comm='Main')
    _process(root, pid=104, executable=a, comm='Main')
    watcher._poll_once()
    assert called == [104]

    selection[0] = b
    assert watcher._poll_when_armed(True)
    assert called == [104]  # Preexisting Mocktail must not be restarted
    _process(root, pid=105, executable=b, comm='Main')
    watcher._poll_once()
    assert called == [104, 105]
    watcher.stop()
