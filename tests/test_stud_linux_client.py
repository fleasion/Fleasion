"""Stud 2.x Rosemary provider integration and proxy ownership boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import (
    ROSEMARY_CLIENT,
    LinuxClientInstallation,
    RosemarySession,
    detect_installed_clients,
    read_rosemary_session,
    rosemary_guest_home,
)
from fleasion.utils.rosemary_launch_interceptor import (
    RosemaryLaunchInterceptor,
    capture_rosemary_launch,
    replay_rosemary_launch,
)
from fleasion.utils.stud_linux import STUD_APP_ID, StudSource, stud_data_root_for_bundle

if TYPE_CHECKING:
    import pytest

PROXY = 'http://127.0.0.1:58443'


def _make_stud(home: Path, *, flatpak: bool = False, build: str = 'version-abc123') -> tuple[StudSource, Path]:
    data = home / f'.var/app/{STUD_APP_ID}/data/stud' if flatpak else home / '.local/share/stud'
    config = home / f'.var/app/{STUD_APP_ID}/config/stud' if flatpak else home / '.config/stud'
    client = data / 'client'
    client.mkdir(parents=True, exist_ok=True)
    (client / 'current').write_text(f'{build}\n2.0.1\n')
    app = client / build / 'RobloxPlayer.app'
    exe = app / 'Contents/MacOS/RobloxPlayer'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'MachO')
    cert = app / 'Contents/Resources/ssl/cacert.pem'
    cert.parent.mkdir(parents=True)
    cert.write_bytes(b'cert')
    program = '/usr/bin/flatpak' if flatpak else str(home / 'bin/stud')
    if not flatpak:
        binary = Path(program)
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'bin')
        binary.chmod(0o755)
    source = StudSource((program, 'run', STUD_APP_ID) if flatpak else (program,), data, config, flatpak=flatpak)
    return source, app


def _proc_process(proc_root: Path, pid: int, command: list[str], environment: dict[str, str]) -> None:
    process = proc_root / str(pid)
    process.mkdir(parents=True)
    (process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in command) + b'\0')
    (process / 'environ').write_bytes(
        b'\0'.join(f'{k}={v}'.encode() for k, v in environment.items()) + b'\0'
    )
    (process / 'stat').write_text(f'{pid} (Main) S ' + ' '.join(['0'] * 18 + ['100'] + ['0'] * 3))
    (process / 'cwd').symlink_to(proc_root.parent, target_is_directory=True)


def _installation(home: Path, source: StudSource, app: Path) -> LinuxClientInstallation:
    return LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=home),
        None, app, stud_managed=True, stud_command=source.command,
    )


def test_native_stud_discovery_launch_and_guest_home(tmp_path: Path) -> None:
    source, app = _make_stud(tmp_path)
    proc = tmp_path / 'proc'
    proc.mkdir()
    found = detect_installed_clients(
        home=tmp_path, environ={}, proc_root=proc,
        which=lambda name: str(tmp_path / 'bin/stud') if name == 'stud' else None,
    )
    assert len(found) == 1
    installation = found[0]
    assert installation.key == 'rosemary'
    assert installation.stud_managed
    assert installation.stud_command == source.command
    assert installation.app_bundle == app
    assert installation.paths.resource_roots == (app / 'Contents/Resources',)
    assert installation.launch_command('roblox-player:synthetic') == [
        str(tmp_path / 'bin/stud'), 'roblox-player:synthetic',
    ]
    assert installation.launch_command() == [str(tmp_path / 'bin/stud'), '--roblox']
    assert rosemary_guest_home(installation, environ={}) == source.prefix / 'root/Users' / tmp_path.name
    assert rosemary_guest_home(installation, environ={}, prefix_path=source.prefix) == (
        source.prefix / 'root/Users' / tmp_path.name
    )
    assert rosemary_guest_home(installation, environ={}, prefix_path=tmp_path / 'other') is None


def test_stud_reader_rejects_stale_and_traversal_builds(tmp_path: Path) -> None:
    source, app = _make_stud(tmp_path)
    marker = source.data_root / 'client/current'
    assert source.bundle == app
    marker.write_text('../version-abc123\n')
    assert source.bundle is None
    marker.write_text('/tmp/elsewhere\n')
    assert source.bundle is None
    marker.write_text('version-other\n')
    assert source.bundle is None
    marker.write_text('version-abc123\n')
    assert source.bundle == app
    assert stud_data_root_for_bundle(app) == source.data_root
    assert stud_data_root_for_bundle(tmp_path / 'Roblox.app') is None


def test_stud_appimage_discovery(tmp_path: Path) -> None:
    _source, app = _make_stud(tmp_path)
    (tmp_path / 'bin/stud').unlink()
    executable = tmp_path / 'Downloads/Stud-x86_64.AppImage'
    executable.parent.mkdir()
    executable.write_bytes(b'AppImage')
    executable.chmod(0o755)
    desktop = tmp_path / f'.local/share/applications/{STUD_APP_ID}.desktop'
    desktop.parent.mkdir(parents=True)
    desktop.write_text(f'[Desktop Entry]\nType=Application\nExec="{executable}" %u\n')
    proc = tmp_path / 'proc'
    proc.mkdir()
    found = detect_installed_clients(
        home=tmp_path, environ={}, proc_root=proc, which=lambda _name: None,
    )
    assert len(found) == 1
    assert found[0].app_bundle == app
    assert found[0].stud_command == (str(executable),)
    assert found[0].launch_command() == [str(executable), '--roblox']


def test_stud_running_player_uses_versioned_executable_and_not_studio(tmp_path: Path) -> None:
    source, app = _make_stud(tmp_path)
    proc = tmp_path / 'proc'
    proc.mkdir()
    rosemary = str(tmp_path / 'libexec/stud/rosemary')
    _proc_process(proc, 99, [rosemary, 'run', str(app / 'Contents/MacOS/RobloxPlayer')],
                  {'ROSEMARY_PREFIX': str(source.prefix)})
    _proc_process(proc, 100, [rosemary, 'run', str(source.data_root / 'studio/version-1/RobloxStudio.app')], {})
    expected = RosemarySession(99, app.resolve(), Path(rosemary), 100)
    assert read_rosemary_session(proc / '99') == expected
    assert read_rosemary_session(proc / '100') is None


def test_stud_guest_only_proxy_environment_and_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, app = _make_stud(tmp_path)
    installed = _installation(tmp_path, source, app)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installed)
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', None)
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', PROXY)
    monkeypatch.delenv('ROSEMARY_ENVIRON', raising=False)
    launched: list[tuple[list[str], dict[str, str]]] = []

    def record(command: list[str], *, extra_env: dict[str, str]) -> None:
        launched.append((command, extra_env))

    monkeypatch.setattr(platform_linux, '_standard_user_popen', record)
    assert platform_linux.launch_as_standard_user('roblox://experiences/start?placeId=1234')
    command, env = launched[0]
    assert command == [source.command[0], 'roblox://experiences/start?placeId=1234']
    assert set(env) == {'ROSEMARY_ENVIRON'}
    guest = env['ROSEMARY_ENVIRON'].splitlines()
    assert f'HTTPS_PROXY={PROXY}' in guest
    assert f'https_proxy={PROXY}' in guest
    assert f'NO_PROXY=localhost,127.0.0.1,::1' in guest
    assert not any('setup.rbxcdn.com' in line for line in guest)
    assert platform_linux.launch_as_standard_user(ROSEMARY_CLIENT.app_id)
    # The normal client request also uses Stud, never a raw Rosemary executable
    assert all(command[0] == source.command[0] for command, _env in launched)


def test_stud_prefix_mismatch_blocks_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, app = _make_stud(tmp_path)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation',
                        lambda: _installation(tmp_path, source, app))
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', str(tmp_path / 'wrong'))
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', PROXY)
    calls: list[object] = []
    def record_failed_launch(_command: list[str], *, extra_env: dict[str, str]) -> None:
        calls.append(extra_env)

    monkeypatch.setattr(platform_linux, '_standard_user_popen', record_failed_launch)
    assert not platform_linux.launch_as_standard_user('roblox-player:test')
    assert not calls


def test_flatpak_stud_uses_env_argument_not_host_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, app = _make_stud(tmp_path, flatpak=True)
    installed = _installation(tmp_path, source, app)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installed)
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', None)
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', PROXY)
    launched: list[tuple[list[str], dict[str, str]]] = []

    def record(command: list[str], *, extra_env: dict[str, str]) -> None:
        launched.append((command, extra_env))

    monkeypatch.setattr(platform_linux, '_standard_user_popen', record)
    assert platform_linux.launch_as_standard_user('roblox-player:synthetic')
    command, env = launched[0]
    assert command[:2] == ['/usr/bin/flatpak', 'run']
    assert command[3:] == [STUD_APP_ID, 'roblox-player:synthetic']
    assert command[2] == '--env=ROSEMARY_ENVIRON=' + env['ROSEMARY_ENVIRON']
    assert set(env) == {'ROSEMARY_ENVIRON'}


def test_stud_running_proxy_status_checks_guest_and_selected_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, app = _make_stud(tmp_path)
    selected = _installation(tmp_path, source, app)
    proc = tmp_path / 'proc'
    proc.mkdir()
    rosemary = str(tmp_path / 'libexec/stud/rosemary')
    _proc_process(proc, 501, [rosemary, 'run', str(app)],
                  {'ROSEMARY_ENVIRON': f'HTTPS_PROXY={PROXY}\nHTTP_PROXY={PROXY}'})
    session = RosemarySession(501, app.resolve(), Path(rosemary), 100)
    monkeypatch.setattr(platform_linux, 'PROC_ROOT', proc)
    def one_session(*, proc_root: Path) -> tuple[RosemarySession, ...]:
        del proc_root
        return (session,)

    monkeypatch.setattr(platform_linux, 'running_rosemary_players', one_session)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: selected)
    assert platform_linux.running_rosemary_proxy_status(PROXY) == 'configured'
    (proc / '501/environ').write_bytes(b'ROSEMARY_ENVIRON=HTTPS_PROXY=http://127.0.0.1:9999\0')
    assert platform_linux.running_rosemary_proxy_status(PROXY) == 'missing'
    unrelated = _installation(tmp_path, source, tmp_path / 'other/RobloxPlayer.app')
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: unrelated)
    assert platform_linux.running_rosemary_proxy_status(PROXY) == 'unreadable'


def test_stud_is_never_automatically_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, app = _make_stud(tmp_path)
    proc = tmp_path / 'proc'
    proc.mkdir()
    rosemary = str(tmp_path / 'bin/rosemary')
    binary = Path(rosemary)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text('binary')
    binary.chmod(0o700)
    _proc_process(proc, 501, [rosemary, 'run', str(app)],
                  {'ROSEMARY_PREFIX': str(source.prefix)})
    session = RosemarySession(501, app.resolve(), binary, 100)
    # A replay remains prohibited even if the caller bypasses the watcher
    snapshot = capture_rosemary_launch(session, proc_root=proc)
    assert snapshot is not None
    assert not replay_rosemary_launch(snapshot, proxy_url=PROXY,
                                     proxy_environment={}, proc_root=proc)
    replayed: list[int] = []
    def record_replay(_snapshot: object, **_kwargs: object) -> bool:
        replayed.append(501)
        return True

    monkeypatch.setattr('fleasion.utils.rosemary_launch_interceptor.replay_rosemary_launch', record_replay)
    watcher = RosemaryLaunchInterceptor(
        is_armed=lambda: True, proxy_url=lambda: PROXY,
        proxy_environment=lambda: {'https_proxy': PROXY}, proc_root=proc,
    )
    _proc_process(proc, 502, [rosemary, 'run', str(app)],
                  {'ROSEMARY_PREFIX': str(source.prefix)})
    watcher._poll_once()  # pyright: ignore[reportPrivateUsage]
    assert not replayed
    watcher.stop()


def test_running_unregistered_stud_appimage_is_discovered(
    tmp_path: Path,
) -> None:
    source, app = _make_stud(tmp_path)
    # The stable executable is an AppImage, not a registered desktop entry
    appimage = tmp_path / 'Downloads/Stud-x86_64.AppImage'
    appimage.parent.mkdir(parents=True)
    appimage.write_bytes(b'AppImage')
    appimage.chmod(0o755)
    proc = tmp_path / 'proc'
    proc.mkdir()
    rosemary = tmp_path / 'mounted/libexec/stud/rosemary'
    rosemary.parent.mkdir(parents=True)
    rosemary.write_bytes(b'Rosemary')
    rosemary.chmod(0o755)
    _proc_process(proc, 501, [str(rosemary), 'run', str(app / 'Contents/MacOS/RobloxPlayer')],
                  {'ROSEMARY_PREFIX': str(source.prefix)})
    stat = proc / '501/stat'
    stat.write_text(stat.read_text().replace('501 (Main) S 0 ', '501 (Main) S 444 ', 1))
    parent = proc / '444'
    parent.mkdir()
    stud_binary = tmp_path / '.mount_Stud/bin/stud'
    stud_binary.parent.mkdir(parents=True)
    stud_binary.write_bytes(b'Stud')
    (parent / 'exe').symlink_to(stud_binary)
    (parent / 'cmdline').write_bytes(str(stud_binary).encode() + b'\0--roblox\0')
    (parent / 'environ').write_bytes(f'APPIMAGE={appimage}\0'.encode())
    found = detect_installed_clients(
        home=tmp_path, environ={}, proc_root=proc, which=lambda _name: None,
    )
    assert len(found) == 1
    assert found[0].stud_managed
    assert found[0].app_bundle == app
    assert found[0].stud_command == (str(appimage),)
    assert found[0].launch_command('roblox-player:synthetic') == [
        str(appimage), 'roblox-player:synthetic',
    ]


def test_stud_unproxied_launch_ignores_other_providers_prefix_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, app = _make_stud(tmp_path)
    installed = _installation(tmp_path, source, app)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installed)
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', str(tmp_path / 'nutmeg-prefix'))
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', None)
    launched: list[tuple[list[str], dict[str, str]]] = []

    def record(command: list[str], *, extra_env: dict[str, str]) -> None:
        launched.append((command, extra_env))

    monkeypatch.setattr(platform_linux, '_standard_user_popen', record)
    assert platform_linux.launch_as_standard_user('roblox-player:synthetic')
    assert launched == [([source.command[0], 'roblox-player:synthetic'], {})]
