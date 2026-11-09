"""Rosemary uses native processes and macOS app bundles, never Flatpak metadata."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import (
    ROSEMARY_CLIENT,
    SOBER_CLIENT,
    LinuxClientInstallation,
    RosemarySession,
    detect_installed_clients,
    running_rosemary_players,
    select_linux_client,
)

if TYPE_CHECKING:
    import pytest


def _make_bundle(home: Path, location: str = 'Games/Roblox.app') -> Path:
    bundle = home / location
    executable = bundle / 'Contents/MacOS/RobloxPlayer'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'mach-o')
    cert = bundle / 'Contents/Resources/ssl/cacert.pem'
    cert.parent.mkdir(parents=True)
    cert.write_text('test-CA', encoding='utf-8')
    return bundle


def _proc_process(proc_root: Path, pid: int, command: list[str], ticks: int = 12345) -> None:
    root = proc_root / str(pid)
    root.mkdir(parents=True)
    (root / 'cmdline').write_bytes(b'\0'.join(arg.encode() for arg in command) + b'\0')
    fields = ['S', *['0'] * 18, str(ticks)]
    (root / 'stat').write_text(f'{pid} (Main) ' + ' '.join(fields), encoding='utf-8')


def test_rosemary_process_identifies_only_player_not_studio_or_helpers(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    studio = tmp_path / 'RobloxStudio.app'
    studio.mkdir()
    proc = tmp_path / 'proc'
    proc.mkdir()
    rosemary = '/opt/user-custom/rosemary'
    _proc_process(proc, 101, [rosemary, 'run', str(bundle)], ticks=500)
    _proc_process(proc, 102, [rosemary, 'run', str(studio)])
    _proc_process(proc, 103, [rosemary, 'run', str(bundle / 'Contents/MacOS/RobloxCrashHandler')])
    _proc_process(proc, 104, [rosemary, 'server', '--directory', str(tmp_path / 'server')])
    _proc_process(proc, 105, ['/usr/bin/bash', '-c', f'{rosemary} run {bundle}'])
    _proc_process(proc, 106, [rosemary, 'run', '--new-instance', str(studio)])
    assert running_rosemary_players(proc_root=proc) == (
        RosemarySession(101, bundle, Path(rosemary), 500),
    )


def test_detects_custom_location_without_flatpak_or_nutmeg(tmp_path: Path) -> None:
    binary = tmp_path / 'bin/rosemary'
    binary.parent.mkdir()
    binary.write_text('binary', encoding='utf-8')
    binary.chmod(0o755)
    bundle = _make_bundle(tmp_path, 'other-drive/Roblox.app')
    installations = detect_installed_clients(
        home=tmp_path, environ={}, which=lambda _name: None,
        rosemary_app_path=bundle, rosemary_binary_path=binary,
    )
    assert len(installations) == 1
    installation = installations[0]
    assert installation.key == 'rosemary'
    assert installation.app_bundle == bundle
    assert installation.paths.resource_roots == (bundle / 'Contents/Resources',)
    assert installation.launch_command() == [str(binary), 'run', str(bundle)]
    assert installation.launch_command('roblox://place/123') == [
        str(binary), 'run', str(bundle), '-protocolString', 'roblox://place/123'
    ]


def test_rosemary_binary_without_player_or_desktop_is_not_installation(tmp_path: Path) -> None:
    detected = detect_installed_clients(
        home=tmp_path, environ={}, which=lambda name: '/usr/bin/rosemary'
        if name == 'rosemary' else None,
    )
    assert detected == ()


def test_detects_running_custom_rosemary_with_foreign_home(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path, 'special-path/Roblox.app')
    proc = tmp_path / 'proc'
    proc.mkdir()
    _proc_process(proc, 456, ['/opt/custom/rosemary', 'run', str(bundle)])
    installed = detect_installed_clients(
        home=tmp_path, environ={}, which=lambda _name: None, proc_root=proc,
    )
    assert [item.key for item in installed] == ['rosemary']
    assert installed[0].app_bundle == bundle
    assert installed[0].executable is None  # nonexistent launcher is not runnable


def test_auto_prefers_running_rosemary_over_sober(tmp_path: Path) -> None:
    sober = LinuxClientInstallation(SOBER_CLIENT, SOBER_CLIENT.paths(home=tmp_path), Path('/usr/bin/flatpak'))
    rosemary = LinuxClientInstallation(ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=tmp_path), Path('/usr/bin/rosemary'))
    assert select_linux_client(
        installed=(sober, rosemary), running_client_starts={'rosemary': 123}
    ) is rosemary
    assert select_linux_client('sober', installed=(sober, rosemary)) is sober
    assert select_linux_client('rosemary', installed=(sober, rosemary)) is rosemary


def test_native_proxy_environment_and_launch_are_scoped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / 'nutmeg/config.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"extra_env":{}}', encoding='utf-8')
    monkeypatch.setattr(platform_linux, 'NUTMEG_CONFIG_PATH', config_path)
    monkeypatch.setattr(platform_linux, 'NUTMEG_PROXY_OVERRIDE_STATE', tmp_path / 'proxy-state.json')
    binary = tmp_path / 'rosemary'
    binary.write_text('bin', encoding='utf-8')
    bundle = _make_bundle(tmp_path)
    installation = LinuxClientInstallation(
        ROSEMARY_CLIENT,
        replace(ROSEMARY_CLIENT.paths(home=tmp_path), resource_roots=(bundle / 'Contents/Resources',)),
        binary,
        bundle,
        desktop_launcher=False,
    )
    launches: list[tuple[list[str], dict[str, str] | None]] = []
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(platform_linux, '_installation_for_client_key', lambda _key: installation)
    monkeypatch.setattr(platform_linux, '_standard_user_popen', lambda cmd, **kw: launches.append((cmd, kw.get('extra_env'))))
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', None)
    assert platform_linux.set_linux_client_env_proxy_override('http://127.0.0.1:9999')
    assert platform_linux.launch_as_standard_user('rosemary')
    assert launches[0][0] == [str(binary), 'run', str(bundle)]
    assert launches[0][1] is not None
    assert launches[0][1]['HTTPS_PROXY'] == 'http://127.0.0.1:9999'
    assert 'HTTPS_PROXY' in launches[0][1]['ROSEMARY_ENVIRON'].splitlines()
    import json
    applied = json.loads(config_path.read_text(encoding='utf-8'))
    assert applied['extra_env']['HTTPS_PROXY'] == 'http://127.0.0.1:9999'
    assert 'HTTPS_PROXY' in applied['extra_env']['ROSEMARY_ENVIRON'].splitlines()
    assert platform_linux.launch_as_standard_user(str(bundle / 'Contents/MacOS/RobloxPlayer'))
    assert launches[1][0] == [str(binary), 'run', str(bundle)]
    assert platform_linux.clear_linux_client_env_proxy_override(client_key='rosemary')
    assert json.loads(config_path.read_text(encoding='utf-8'))['extra_env'] == {}
    assert not (tmp_path / 'proxy-state.json').exists()
    assert platform_linux.launch_as_standard_user('rosemary')
    assert launches[2][1] == {}


def test_rosemary_stale_nutmeg_proxy_override_is_restored_on_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    config_path = tmp_path / 'nutmeg/config.json'
    state_path = tmp_path / 'fleasion/nutmeg-state.json'
    config_path.parent.mkdir()
    config_path.write_text('{"extra_env":{"CUSTOM":"original"}}', encoding='utf-8')
    original = json.loads(config_path.read_text(encoding='utf-8'))
    monkeypatch.setattr(platform_linux, 'NUTMEG_CONFIG_PATH', config_path)
    monkeypatch.setattr(platform_linux, 'NUTMEG_PROXY_OVERRIDE_STATE', state_path)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    from fleasion.utils import nutmeg_proxy_override

    assert nutmeg_proxy_override.arm('http://127.0.0.1:58443', config_path, state_path)
    assert json.loads(config_path.read_text(encoding='utf-8'))['extra_env']['HTTPS_PROXY']
    assert platform_linux.recover_stale_linux_client_env_proxy_override()
    assert json.loads(config_path.read_text(encoding='utf-8')) == original
    assert not state_path.exists()


def test_nutmeg_desktop_launch_routes_uri_and_bypasses_bootstrap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _make_bundle(tmp_path)
    installation = LinuxClientInstallation(
        ROSEMARY_CLIENT,
        replace(ROSEMARY_CLIENT.paths(home=tmp_path), resource_roots=(bundle / 'Contents/Resources',)),
        None,
        bundle,
        desktop_launcher=True,
    )
    monkeypatch.setattr('fleasion.utils.linux_clients.shutil.which', lambda name: '/usr/bin/gtk-launch' if name == 'gtk-launch' else None)
    assert installation.launch_command('roblox-player:synthetic') == [
        '/usr/bin/gtk-launch', 'io.github.frolleks.Nutmeg', 'roblox-player:synthetic',
    ]
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', 'http://127.0.0.1:9999')
    passthrough = platform_linux._rosemary_proxy_environment(nutmeg_launcher=True)
    direct = platform_linux._rosemary_proxy_environment()
    assert 'clientsettingscdn.roblox.com' in passthrough['NO_PROXY']
    assert 'setup.rbxcdn.com' in passthrough['NO_PROXY']
    assert 'setup.rbxcdn.com' not in direct['NO_PROXY']


def test_rosemary_custom_prefix_uses_direct_launcher_and_sets_guest_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / 'rosemary'
    binary.write_text('binary')
    bundle = _make_bundle(tmp_path)
    installation = LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=tmp_path),
        binary, bundle, desktop_launcher=True,
    )
    prefix = tmp_path / 'custom-prefix'
    monkeypatch.setattr(platform_linux, 'detect_installed_clients', lambda **_kw: (installation,))
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', str(prefix))
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', None)
    monkeypatch.setattr(platform_linux, '_linux_client_preference', 'rosemary')
    installed = platform_linux.linux_client_installations()
    assert len(installed) == 1
    assert installed[0].desktop_launcher is False
    assert installed[0].launch_command('roblox-player:synthetic') == [
        str(binary), 'run', str(bundle), '-protocolString', 'roblox-player:synthetic'
    ]
    launched: list[tuple[list[str], dict[str, str] | None]] = []
    monkeypatch.setattr(
        platform_linux, '_standard_user_popen',
        lambda command, **kw: launched.append((command, kw.get('extra_env')))
    )
    assert platform_linux.launch_as_standard_user('roblox-player:synthetic')
    assert launched[0][0][0] == str(binary)
    assert launched[0][1] == {'ROSEMARY_PREFIX': str(prefix)}


def test_rosemary_relative_prefix_refuses_to_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / 'rosemary'
    binary.write_text('binary')
    bundle = _make_bundle(tmp_path)
    installation = LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=tmp_path),
        binary, bundle, desktop_launcher=False,
    )
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', 'relative/unsafe-prefix')
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', None)
    launched: list[object] = []
    monkeypatch.setattr(
        platform_linux, '_standard_user_popen',
        lambda command, **_kw: launched.append(command)
    )
    assert not platform_linux.launch_as_standard_user('roblox-player:synthetic')
    assert not launched


def test_rosemary_env_proxy_launch_avoids_gtk_dbus_and_keeps_clientsettings_routed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / 'rosemary'
    binary.write_text('binary')
    bundle = _make_bundle(tmp_path)
    installation = LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=tmp_path),
        binary, bundle, desktop_launcher=True,
    )
    monkeypatch.setattr(
        'fleasion.utils.linux_clients.shutil.which',
        lambda name: '/usr/bin/gtk-launch' if name == 'gtk-launch' else None,
    )
    monkeypatch.setattr(
        platform_linux, 'get_selected_linux_client_installation', lambda: installation
    )
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', None)
    monkeypatch.setattr(
        platform_linux, '_active_rosemary_proxy_url', 'http://127.0.0.1:58443'
    )
    launched: list[tuple[list[str], dict[str, str] | None]] = []
    monkeypatch.setattr(
        platform_linux, '_standard_user_popen',
        lambda command, **kwargs: launched.append((command, kwargs.get('extra_env'))),
    )
    assert platform_linux.launch_as_standard_user('roblox-player:test')
    assert launched[0][0] == [
        str(binary), 'run', str(bundle), '-protocolString', 'roblox-player:test',
    ]
    environment = launched[0][1]
    assert environment is not None
    assert environment['HTTPS_PROXY'] == 'http://127.0.0.1:58443'
    assert environment['https_proxy'] == 'http://127.0.0.1:58443'
    assert environment['NO_PROXY'] == 'localhost,127.0.0.1,::1'
    assert 'clientsettingscdn.roblox.com' not in environment['NO_PROXY']
    assert 'gtk-launch' not in launched[0][0][0]


def test_running_rosemary_proxy_status_does_not_guess_from_launcher_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pid = 42
    session = RosemarySession(
        pid, tmp_path / 'Roblox.app', tmp_path / 'rosemary', 12345,
    )
    root = tmp_path / 'proc'
    location = root / str(pid)
    location.mkdir(parents=True)
    monkeypatch.setattr(platform_linux, 'PROC_ROOT', root)
    monkeypatch.setattr(
        platform_linux, 'running_rosemary_players', lambda **_kwargs: (session,)
    )
    environment = location / 'environ'
    proxy = 'http://127.0.0.1:58443'
    environment.write_bytes(b'ROSEMARY_PREFIX=/tmp/guest\0')
    assert platform_linux.running_rosemary_proxy_status(proxy) == 'missing'
    environment.write_bytes(
        b'ROSEMARY_PREFIX=/tmp/guest\0'
        b'https_proxy=http://127.0.0.1:58443\0'
    )
    assert platform_linux.running_rosemary_proxy_status(proxy) == 'configured'
    environment.write_bytes(b'https_proxy=http://127.0.0.1:9999\0')
    assert platform_linux.running_rosemary_proxy_status(proxy) == 'missing'
    environment.unlink()
    assert platform_linux.running_rosemary_proxy_status(proxy) == 'unreadable'


def test_running_rosemary_launcher_precedes_unrelated_path_binary(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    proc = tmp_path / 'proc'
    proc.mkdir()
    active = tmp_path / 'running/rosemary'
    active.parent.mkdir()
    active.write_bytes(b'active')
    active.chmod(0o700)
    stale = tmp_path / 'stale/rosemary'
    stale.parent.mkdir()
    stale.write_bytes(b'stale')
    stale.chmod(0o700)
    _proc_process(proc, 500, [str(active), 'run', str(bundle)], ticks=700)

    def find_binary(name: str) -> str | None:
        return str(stale) if name == 'rosemary' else None

    detected = detect_installed_clients(
        home=tmp_path, environ={}, which=find_binary, proc_root=proc,
    )
    assert len(detected) == 1
    assert detected[0].executable == active
    configured = detect_installed_clients(
        home=tmp_path, environ={}, which=find_binary, proc_root=proc,
        rosemary_binary_path=stale,
    )
    assert configured[0].executable == stale
