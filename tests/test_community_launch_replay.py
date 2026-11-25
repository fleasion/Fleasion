"""Exact launch replay isolation for Cordial native and community Flatpaks."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.utils import community_flatpak_replay as flatpak_replay
from fleasion.utils import cordial_launch_interceptor as cordial_replay
from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import (
    CORDIAL_CLIENT,
    NIGHTCAP_CLIENT,
    LinuxClientInstallation,
    running_cordial_players,
)

if TYPE_CHECKING:
    from fleasion.config import ConfigManager


_PROXY = 'http://127.0.0.1:58443'
_TICKET = 'roblox-player:1+gameinfo=one-use-secret-ticket'


def _which_flatpak(_name: str) -> str:
    return '/usr/bin/flatpak'


def _pidfd_open(_pid: int, _flags: int) -> int:
    return os.open('/dev/null', os.O_RDONLY)


def _pid_exited(_fd: int, _timeout_ms: int) -> bool:
    return True


def _is_running(_installation: LinuxClientInstallation) -> bool:
    return True


def _proc(
    tmp_path: Path, *, pid: int, name: str, comm: str, argv: tuple[str, ...],
    cgroup: str = '0::/user.slice/test.service',
    env: tuple[str, ...] = ('CUSTOM_ENV=present',),
    gen: int = 12345,
    mapped: bool = False,
) -> tuple[Path, Path]:
    proc_root = tmp_path / 'proc'
    proc_root.mkdir(exist_ok=True)
    process = proc_root / str(pid)
    process.mkdir()
    binary = tmp_path / 'bin' / name
    binary.parent.mkdir(exist_ok=True)
    if not binary.exists():
        binary.write_bytes(b'ELF-fixture')
        binary.chmod(0o700)
    (process / 'exe').symlink_to(binary)
    (process / 'cwd').symlink_to(tmp_path, target_is_directory=True)
    (process / 'cmdline').write_bytes(b'\0'.join(os.fsencode(a) for a in argv) + b'\0')
    (process / 'environ').write_bytes(b'\0'.join(os.fsencode(e) for e in env) + b'\0')
    (process / 'comm').write_text(comm + '\n')
    (process / 'cgroup').write_text(cgroup + '\n')
    (process / 'maps').write_text(
        '100-200 r-xp 0000 00:01 123 /game/libroblox.so\n' if mapped else '/lib/libc.so\n'
    )
    (process / 'stat').write_text(
        f'{pid} ({comm}) S ' + ' '.join(['0'] * 18 + [str(gen)] + ['0'] * 2)
    )
    return proc_root, binary


def _flatpak(client: str, tmp_path: Path) -> LinuxClientInstallation:
    descriptor = NIGHTCAP_CLIENT if client == 'nightcap' else CORDIAL_CLIENT
    return LinuxClientInstallation(
        descriptor, descriptor.paths(home=tmp_path, environ={}), Path('/usr/bin/flatpak')
    )


@pytest.mark.parametrize('client', ['nightcap', 'cordial'])
def test_flatpak_replay_preserves_invocation_and_kills_exact_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str
) -> None:
    installation = _flatpak(client, tmp_path)
    binary = 'mocktail' if client == 'nightcap' else 'cordial-shell'
    args = ('--launch-uri', _TICKET) if client == 'nightcap' else ('--profile', 'P2', '--play', _TICKET)
    app = installation.app_id
    proc, _exe = _proc(
        tmp_path, pid=881, name=binary, comm='Main' if client == 'nightcap' else binary,
        argv=(binary, *args),
        cgroup=f'0::/user.slice/app-flatpak-{app}-187.scope',
        mapped=client == 'nightcap',
    )
    snap = flatpak_replay.capture_community_flatpak_launch(
        installation, proxy_url=_PROXY, proc_root=proc
    )
    assert snap is not None
    assert snap.args == args
    assert snap.scope == f'app-flatpak-{app}-187.scope'
    assert not snap.already_proxied
    assert snap.scope in flatpak_replay.flatpak_community_scopes(installation, proc_root=proc)
    commands: list[list[str]] = []
    spawned: list[tuple[list[str], Path | None]] = []
    active = [True]

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1] == 'ps':
            return subprocess.CompletedProcess(command, 0, f'187\t{app}\n' if active[0] else '', '')
        assert command == ['/usr/bin/flatpak', 'kill', '187']
        active[0] = False
        shutil.rmtree(proc / '881')
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(flatpak_replay.shutil, 'which', _which_flatpak)
    monkeypatch.setattr(platform_linux, '_standard_user_run', run)
    def spawn(command: list[str], **options: object) -> SimpleNamespace:
        cwd = options.get('cwd')
        assert cwd is None or isinstance(cwd, Path)
        spawned.append((command, cwd))
        return SimpleNamespace(pid=9)

    monkeypatch.setattr(platform_linux, '_standard_user_popen', spawn)
    assert flatpak_replay.replay_community_flatpak_launch(
        installation, snap, proxy_url=_PROXY, proc_root=proc
    )
    assert commands.count(['/usr/bin/flatpak', 'kill', '187']) == 1
    assert spawned == [(['/usr/bin/flatpak', 'run', app, *args], tmp_path)]


@pytest.mark.parametrize('client', ['nightcap', 'cordial'])
def test_flatpak_replay_refuses_stale_scope_and_multiple_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str
) -> None:
    installation = _flatpak(client, tmp_path)
    app = installation.app_id
    name = 'mocktail' if client == 'nightcap' else 'cordial-shell'
    proc, _exe = _proc(
        tmp_path, pid=771, name=name, comm='Main' if client == 'nightcap' else name,
        argv=(name, '--profile', 'P2'),
        cgroup=f'0::/app-flatpak-{app}-187.scope', mapped=client == 'nightcap',
    )
    snap = flatpak_replay.capture_community_flatpak_launch(
        installation, proxy_url=_PROXY, proc_root=proc
    )
    assert snap is not None
    killed: list[list[str]] = []
    monkeypatch.setattr(flatpak_replay.shutil, 'which', _which_flatpak)
    def run_wrong_instance(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        killed.append(command)
        return subprocess.CompletedProcess(command, 0, f'999\t{app}\n', '')

    monkeypatch.setattr(platform_linux, '_standard_user_run', run_wrong_instance)
    assert not flatpak_replay.replay_community_flatpak_launch(
        installation, snap, proxy_url=_PROXY, proc_root=proc
    )
    assert not any(command[1] == 'kill' for command in killed)
    _proc(
        tmp_path, pid=772, name=name, comm='Main' if client == 'nightcap' else name,
        argv=(name, '--profile', 'P3'),
        cgroup=f'0::/app-flatpak-{app}-188.scope', mapped=client == 'nightcap',
    )
    assert flatpak_replay.capture_community_flatpak_launch(
        installation, proxy_url=_PROXY, proc_root=proc
    ) is None


def test_flatpak_already_proxied_and_identity_changed_are_not_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = _flatpak('nightcap', tmp_path)
    app = installation.app_id
    proc, _exe = _proc(
        tmp_path, pid=751, name='mocktail', comm='Main', argv=('mocktail', _TICKET),
        cgroup=f'0::/app-flatpak-{app}-187.scope', mapped=True,
        env=(f'HTTPS_PROXY={_PROXY}',),
    )
    snapshot = flatpak_replay.capture_community_flatpak_launch(
        installation, proxy_url=_PROXY, proc_root=proc
    )
    assert snapshot is not None and snapshot.already_proxied
    monkeypatch.setattr(flatpak_replay.shutil, 'which', _which_flatpak)
    assert not flatpak_replay.replay_community_flatpak_launch(
        installation, snapshot, proxy_url=_PROXY, proc_root=proc
    )
    (proc / '751/environ').write_bytes(b'CUSTOM=other\0')
    assert not flatpak_replay.replay_community_flatpak_launch(
        installation, replace(snapshot, already_proxied=False),
        proxy_url=_PROXY, proc_root=proc
    )


def test_native_cordial_replay_preserves_profile_join_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc, binary = _proc(
        tmp_path, pid=891, name='cordial-run', comm='Main',
        argv=('cordial-run', '--profile', 'P2', '--join-url', _TICKET, '--run', '86400'),
        mapped=True,
    )
    shell = binary.with_name('cordial-shell')
    shell.write_bytes(b'ELF-shell')
    shell.chmod(0o700)
    assert cordial_replay.cordial_engine_executable(shell) == binary
    session = running_cordial_players(proc_root=proc, executable=binary)[0]
    snap = cordial_replay.capture_cordial_launch(session, executable=binary, proc_root=proc)
    assert snap is not None
    assert snap.argv[3] == '--join-url' and snap.argv[4] == _TICKET
    assert snap.env['CUSTOM_ENV'] == 'present'
    signals: list[int] = []
    spawned: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(cordial_replay.os, 'pidfd_open', _pidfd_open)
    def signal_fd(_fd: int, sig: int) -> None:
        signals.append(sig)

    def popen(command: list[str], **options: object) -> SimpleNamespace:
        spawned.append((command, options))
        return SimpleNamespace(pid=3)

    monkeypatch.setattr(cordial_replay.signal, 'pidfd_send_signal', signal_fd)
    monkeypatch.setattr(cordial_replay, '_wait_pid_exit', _pid_exited)
    monkeypatch.setattr(cordial_replay.subprocess, 'Popen', popen)
    assert cordial_replay.replay_cordial_launch(
        snap, proxy_url=_PROXY, proxy_environment={'HTTPS_PROXY': _PROXY}, proc_root=proc
    )
    assert signals == [signal.SIGTERM]
    command, options = spawned[0]
    assert command == list(snap.argv)
    assert options['executable'] == str(binary)
    env = cast('dict[str, str]', options['env'])
    assert env['CUSTOM_ENV'] == 'present'
    assert env['FLEASION_CORDIAL_ENV_REPLAY'] == '1'
    assert env['HTTPS_PROXY'] == _PROXY
    (proc / '891/stat').write_text(
        '891 (Main) S ' + ' '.join(['0'] * 18 + ['12346'] + ['0'] * 2)
    )
    assert not cordial_replay.replay_cordial_launch(
        snap, proxy_url=_PROXY, proxy_environment={}, proc_root=proc
    )
    assert len(signals) == 1


def test_native_cordial_watcher_handles_new_engine_only_and_never_flatpak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc, binary = _proc(
        tmp_path, pid=591, name='cordial-run', comm='Main',
        argv=('cordial-run', '--profile', 'Preexisting'), mapped=True,
    )
    armed = [False]
    called: list[int] = []
    def replay(snapshot: cordial_replay.CordialLaunchSnapshot, **_kwargs: object) -> bool:
        called.append(snapshot.session.pid)
        return True

    monkeypatch.setattr(cordial_replay, 'replay_cordial_launch', replay)
    watcher = cordial_replay.CordialLaunchInterceptor(
        is_armed=lambda: armed[0], proxy_url=lambda: _PROXY,
        proxy_environment=lambda: {'HTTPS_PROXY': _PROXY},
        executable=lambda: binary, proc_root=proc,
    )
    _proc(
        tmp_path, pid=592, name='cordial-run', comm='Main',
        argv=('cordial-run', '--profile', 'New'), mapped=True,
    )
    watcher._poll_once()  # pyright: ignore[reportPrivateUsage]
    assert called == []
    armed[0] = True
    watcher._poll_once()  # pyright: ignore[reportPrivateUsage]
    assert called == [592]
    _proc(
        tmp_path, pid=593, name='cordial-run', comm='Main',
        argv=('cordial-run', '--profile', 'Flatpak'), mapped=True,
        cgroup='0::/app-flatpak-io.github.luohoa97.Cordial-500.scope',
    )
    watcher._poll_once()  # pyright: ignore[reportPrivateUsage]
    assert called == [592]
    watcher.stop()


def test_native_cordial_never_replays_unselected_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc, _binary = _proc(
        tmp_path, pid=661, name='cordial-run', comm='Main',
        argv=('cordial-run', '--profile', 'P2'), mapped=True,
    )
    other = tmp_path / 'other/cordial-run'
    other.parent.mkdir()
    other.write_bytes(b'other')
    other.chmod(0o700)
    assert running_cordial_players(proc_root=proc, executable=other) == ()
    called: list[int] = []
    def replay(snapshot: cordial_replay.CordialLaunchSnapshot, **_kwargs: object) -> bool:
        called.append(snapshot.session.pid)
        return True

    monkeypatch.setattr(cordial_replay, 'replay_cordial_launch', replay)
    watcher = cordial_replay.CordialLaunchInterceptor(
        is_armed=lambda: True, proxy_url=lambda: _PROXY,
        proxy_environment=lambda: {}, executable=lambda: other, proc_root=proc,
    )
    _proc(
        tmp_path, pid=662, name='cordial-run', comm='Main',
        argv=('cordial-run', '--profile', 'P3'), mapped=True,
    )
    watcher._poll_once()  # pyright: ignore[reportPrivateUsage]
    assert called == []
    watcher.stop()


@pytest.mark.parametrize('client', ['nightcap', 'cordial'])
def test_linux_auto_handoff_replays_selected_community_flatpak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str,
) -> None:
    from fleasion.app import roblox_monitor as monitor_module

    installation = _flatpak(client, tmp_path)
    app = installation.app_id
    scope = f'app-flatpak-{app}-187.scope'
    snapshot = flatpak_replay.CommunityFlatpakSnapshot(
        client, app, scope, 712, 12345, ('--profile', 'P2', _TICKET), tmp_path, False
    )
    calls: list[object] = []
    config = SimpleNamespace(
        proxy_mode='env', proxy_features_enabled=True, linux_client='auto',
        auto_delete_cache_on_exit=True,
    )

    class Proxy:
        is_running = True
        _active_linux_client_key = 'rosemary'
        _linux_env_proxy_override_client_key: str | None = None

        def roblox_env_proxy_url(self) -> str:
            return _PROXY

        def wait_for_env_proxy_ready(self, timeout: float = 0.0) -> bool:
            return True

        def stop(self) -> None:
            calls.append('stop')
            self.is_running = False

        def start(self) -> None:
            calls.append('start')
            self.is_running = True
            self._active_linux_client_key = client
            self._linux_env_proxy_override_client_key = client

        def rearm_custom_fflag_delivery_for_player_launch(self) -> None:
            calls.append('rearm')

        def set_roblox_player_running(self, _running: bool) -> None:
            calls.append('player')

    monitor = monitor_module.RobloxExitMonitor(cast('ConfigManager', config))
    proxy = Proxy()
    monkeypatch.setattr(monitor, '_proxy_master', proxy)
    monkeypatch.setattr(monitor, '_community_preexisting_scopes', {})
    selected = [installation]
    monkeypatch.setattr(
        monitor_module, 'get_selected_linux_client_installation', lambda: selected[0]
    )
    monkeypatch.setattr(monitor_module, 'is_linux_client_running', _is_running)
    def capture(_installation: LinuxClientInstallation, *, proxy_url: str) -> tuple[flatpak_replay.CommunityFlatpakSnapshot, ...]:
        return (snapshot,)

    def replay(_installation: LinuxClientInstallation, snap: flatpak_replay.CommunityFlatpakSnapshot, *, proxy_url: str) -> bool:
        calls.append(('replay', snap))
        return True

    monkeypatch.setattr(monitor_module, 'capture_community_flatpak_launches', capture)
    monkeypatch.setattr(monitor_module, 'replay_community_flatpak_launch', replay)
    assert not monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]
    assert calls == ['stop', 'start', ('replay', snapshot), 'rearm']
    assert not monitor.was_running
    assert monitor._community_handoff is not None  # pyright: ignore[reportPrivateUsage]
    selected[0] = _flatpak('cordial' if client == 'nightcap' else 'nightcap', tmp_path)
    assert not monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]
    assert calls.count('start') == 1
    selected[0] = installation
    assert monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]
    assert monitor._community_handoff is None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize('client', ['nightcap', 'cordial'])
def test_linux_auto_never_replays_preexisting_community_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str,
) -> None:
    from fleasion.app import roblox_monitor as monitor_module

    installation = _flatpak(client, tmp_path)
    scope = f'app-flatpak-{installation.app_id}-187.scope'
    snapshot = flatpak_replay.CommunityFlatpakSnapshot(
        client, installation.app_id, scope, 712, 12345, (), tmp_path, False
    )
    calls: list[str] = []
    config = SimpleNamespace(
        proxy_mode='env', proxy_features_enabled=True, linux_client='auto',
        auto_delete_cache_on_exit=False,
    )

    class Proxy:
        is_running = True
        _active_linux_client_key = 'rosemary'
        _linux_env_proxy_override_client_key = None

        def roblox_env_proxy_url(self) -> str:
            return _PROXY

        def wait_for_env_proxy_ready(self, timeout: float = 0.0) -> bool:
            return True

        def stop(self) -> None:
            calls.append('stop')

        def start(self) -> None:
            calls.append('start')
            self._active_linux_client_key = client
            self._linux_env_proxy_override_client_key = client

        def set_roblox_player_running(self, _running: bool) -> None:
            pass

    monitor = monitor_module.RobloxExitMonitor(cast('ConfigManager', config))
    monkeypatch.setattr(monitor, '_proxy_master', Proxy())
    monkeypatch.setattr(monitor, '_community_preexisting_scopes', {client: frozenset({scope})})
    monkeypatch.setattr(
        monitor_module, 'get_selected_linux_client_installation', lambda: installation
    )
    monkeypatch.setattr(monitor_module, 'is_linux_client_running', _is_running)
    def capture(_installation: LinuxClientInstallation, *, proxy_url: str) -> tuple[flatpak_replay.CommunityFlatpakSnapshot, ...]:
        return (snapshot,)

    def replay(_installation: LinuxClientInstallation, _snapshot: flatpak_replay.CommunityFlatpakSnapshot, *, proxy_url: str) -> bool:
        calls.append('replay')
        return True

    monkeypatch.setattr(monitor_module, 'capture_community_flatpak_launches', capture)
    monkeypatch.setattr(monitor_module, 'replay_community_flatpak_launch', replay)
    assert monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]
    assert calls == ['stop', 'start']


def test_cordial_flatpak_replays_multiple_independent_profiles_without_cross_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _flatpak('cordial', tmp_path)
    app = installation.app_id
    proc = tmp_path / 'proc'
    for pid, instance, profile in [(801, '187', 'P2'), (802, '188', 'P3')]:
        _proc(
            tmp_path, pid=pid, name='cordial-shell', comm='cordial-shell',
            argv=('cordial-shell', '--profile', profile, '--play'),
            cgroup=f'0::/app-flatpak-{app}-{instance}.scope',
        )
    snapshots = flatpak_replay.capture_community_flatpak_launches(
        installation, proxy_url=_PROXY, proc_root=proc,
    )
    assert len(snapshots) == 2
    assert {snap.args[1] for snap in snapshots} == {'P2', 'P3'}
    assert flatpak_replay.capture_community_flatpak_launch(
        installation, proxy_url=_PROXY, proc_root=proc
    ) is None
    active = {'187': 801, '188': 802}
    killed: list[str] = []
    spawned: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1] == 'ps':
            rows = ''.join(f'{instance}\t{app}\n' for instance in active)
            return subprocess.CompletedProcess(command, 0, rows, '')
        assert command[1] == 'kill'
        instance = command[2]
        killed.append(instance)
        shutil.rmtree(proc / str(active.pop(instance)))
        return subprocess.CompletedProcess(command, 0, '', '')

    def popen(command: list[str], **_kwargs: object) -> SimpleNamespace:
        spawned.append(command)
        return SimpleNamespace(pid=987)

    monkeypatch.setattr(flatpak_replay.shutil, 'which', _which_flatpak)
    monkeypatch.setattr(platform_linux, '_standard_user_run', run)
    monkeypatch.setattr(platform_linux, '_standard_user_popen', popen)
    for snapshot in snapshots:
        assert flatpak_replay.replay_community_flatpak_launch(
            installation, snapshot, proxy_url=_PROXY, proc_root=proc
        )
    assert killed == ['187', '188']
    assert spawned == [
        ['/usr/bin/flatpak', 'run', app, '--profile', 'P2', '--play'],
        ['/usr/bin/flatpak', 'run', app, '--profile', 'P3', '--play'],
    ]


def test_cordial_flatpak_waits_for_detached_engine_before_relaunch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _flatpak('cordial', tmp_path)
    app = installation.app_id
    proc, _shell = _proc(
        tmp_path, pid=812, name='cordial-shell', comm='cordial-shell',
        argv=('cordial-shell', '--profile', 'P2', '--play'),
        cgroup=f'0::/app-flatpak-{app}-187.scope',
    )
    _proc(
        tmp_path, pid=813, name='cordial-run', comm='Main',
        argv=('cordial-run', '--profile', 'P2'), mapped=True,
        cgroup=f'0::/app-flatpak-{app}-187.scope',
    )
    snapshot = flatpak_replay.capture_community_flatpak_launch(
        installation, proxy_url=_PROXY, proc_root=proc,
    )
    assert snapshot is not None
    active = [True]
    spawned: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1] == 'ps':
            return subprocess.CompletedProcess(
                command, 0, f'187\t{app}\n' if active[0] else '', ''
            )
        assert command == ['/usr/bin/flatpak', 'kill', '187']
        shutil.rmtree(proc / '812')
        active[0] = False
        return subprocess.CompletedProcess(command, 0, '', '')

    def popen(command: list[str], **_kwargs: object) -> SimpleNamespace:
        spawned.append(command)
        return SimpleNamespace(pid=9)

    monkeypatch.setattr(flatpak_replay.shutil, 'which', _which_flatpak)
    monkeypatch.setattr(flatpak_replay, '_EXIT_TIMEOUT', 0.03)
    monkeypatch.setattr(platform_linux, '_standard_user_run', run)
    monkeypatch.setattr(platform_linux, '_standard_user_popen', popen)
    assert not flatpak_replay.replay_community_flatpak_launch(
        installation, snapshot, proxy_url=_PROXY, proc_root=proc
    )
    assert not spawned


def test_cordial_same_scope_multi_launcher_is_never_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = _flatpak('cordial', tmp_path)
    app = installation.app_id
    for pid, profile in [(831, 'P2'), (832, 'P3')]:
        _proc(
            tmp_path, pid=pid, name='cordial-shell', comm='cordial-shell',
            argv=('cordial-shell', '--profile', profile, '--play'),
            cgroup=f'0::/app-flatpak-{app}-187.scope',
        )
    assert flatpak_replay.capture_community_flatpak_launches(
        installation, proxy_url=_PROXY, proc_root=tmp_path / 'proc'
    ) == ()


def test_native_cordial_processes_do_not_include_flatpak_or_other_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.utils.linux_clients import running_installed_cordial_players

    native = tmp_path / 'native'
    native.mkdir()
    shell = native / 'cordial-shell'
    engine = native / 'cordial-run'
    shell.write_bytes(b'native-shell')
    engine.write_bytes(b'native-engine')
    shell.chmod(0o700)
    engine.chmod(0o700)
    installation = LinuxClientInstallation(
        replace(CORDIAL_CLIENT, runtime='native'),
        replace(CORDIAL_CLIENT, runtime='native').paths(home=tmp_path, environ={}),
        shell,
    )
    proc, _exe = _proc(
        tmp_path, pid=901, name='cordial-run', comm='Main',
        argv=('cordial-run', '--profile', 'P2'), mapped=True,
    )
    # The native fixture must point to the exact selected native engine
    (proc / '901/exe').unlink()
    (proc / '901/exe').symlink_to(engine)
    (proc / '901/cmdline').write_bytes(b'cordial-run\0--profile\0P2\0')
    _proc(
        tmp_path, pid=902, name='cordial-run', comm='Main',
        argv=('cordial-run', '--profile', 'Flatpak'), mapped=True,
        cgroup=f'0::/app-flatpak-{CORDIAL_CLIENT.app_id}-889.scope',
    )
    _proc(
        tmp_path, pid=903, name='cordial-shell', comm='cordial-shell',
        argv=('cordial-shell',), mapped=False,
    )
    (proc / '903/exe').unlink()
    (proc / '903/exe').symlink_to(shell)
    _proc(
        tmp_path, pid=904, name='cordial-run', comm='Main',
        argv=('cordial-run', '--profile', 'P3'), mapped=True,
    )
    assert [session.pid for session in running_installed_cordial_players(
        installation, proc_root=proc
    )] == [901]
    monkeypatch.setattr(platform_linux, 'PROC_ROOT', proc)
    assert platform_linux._client_pids(installation) == [901, 903]  # pyright: ignore[reportPrivateUsage]
    selected = platform_linux.linux_client_main_process(installation)
    assert selected is not None and selected[0] == 901
    flatpak = _flatpak('cordial', tmp_path)
    assert platform_linux._client_pids(flatpak) == [902]  # pyright: ignore[reportPrivateUsage]
