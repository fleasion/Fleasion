"""Native package and AppImage installs of Linux clients."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from fleasion.utils import mocktail_config, platform_linux
from fleasion.utils.linux_clients import (
    CORDIAL_CLIENT,
    MOCKTAIL_CLIENT,
    SOBER_CLIENT,
    CommandRunner,
    LinuxClientInstallation,
    detect_installed_clients,
)

CA_PEM = '-----BEGIN CERTIFICATE-----\nZmxlYXNpb24=\n-----END CERTIFICATE-----\n'
CORDIAL_DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=Cordial
Exec=cordial-shell %u
MimeType=x-scheme-handler/roblox-player;x-scheme-handler/roblox;
DBusActivatable=true

[Desktop Action Quest]
Name=Quest
Exec=cordial-shell --quest
"""


def _no_flatpak(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 1, '', '')


NativeProxy = tuple[Path, Path, Path]


def _which(found: dict[str, str]) -> Callable[[str], str | None]:
    return found.get


def _recording_popen(
    launched: list[tuple[list[str], dict[str, str] | None]],
) -> Callable[..., None]:
    def popen(command: list[str], extra_env: dict[str, str] | None = None) -> None:
        launched.append((command, extra_env))

    return popen


def _returning[T](value: T) -> Callable[..., T]:
    def callback(*_args: object, **_kwargs: object) -> T:
        return value

    return callback


def _appimage(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'\x7fELF')
    path.chmod(0o755)
    return path


# Registry paths


def test_sober_is_flatpak_only_and_the_others_ship_natively() -> None:
    assert SOBER_CLIENT.install_kinds == ('flatpak',)
    assert MOCKTAIL_CLIENT.install_kinds == ('flatpak', 'native', 'appimage')
    assert CORDIAL_CLIENT.install_kinds == ('flatpak', 'native', 'appimage')


def test_native_paths_use_the_host_xdg_directories(tmp_path: Path) -> None:
    paths = CORDIAL_CLIENT.paths(home=tmp_path, environ={}, kind='native')

    assert paths.flatpak_root is None
    assert paths.config_root == tmp_path / '.config' / 'cordial'
    assert paths.data_root == tmp_path / '.local' / 'share' / 'cordial'
    assert paths.cache_root == tmp_path / '.cache' / 'cordial'
    assert paths.resource_roots == (tmp_path / '.config' / 'cordial' / 'overlay',)

    profile = tmp_path / '.local' / 'share' / 'cordial' / 'profiles' / 'default'
    profile.mkdir(parents=True)
    assert paths.fflag_files() == (profile / 'flags.json',)


def test_native_paths_honour_absolute_xdg_overrides_only(tmp_path: Path) -> None:
    environ = {
        'XDG_CONFIG_HOME': str(tmp_path / 'cfg'),
        'XDG_DATA_HOME': 'relative/data',
    }
    paths = MOCKTAIL_CLIENT.paths(home=tmp_path, environ=environ, kind='native')

    assert paths.config_file == tmp_path / 'cfg' / 'mocktail' / 'config.yaml'
    # The XDG spec says relative values must be ignored
    assert paths.data_root == tmp_path / '.local' / 'share' / 'mocktail'


def test_all_paths_lists_flatpak_and_host_layouts_once(tmp_path: Path) -> None:
    kinds = [paths.kind for paths in MOCKTAIL_CLIENT.all_paths(home=tmp_path, environ={})]
    assert kinds == ['flatpak', 'native']
    assert [paths.kind for paths in SOBER_CLIENT.all_paths(home=tmp_path, environ={})] == [
        'flatpak'
    ]


# Detection


def test_detects_distro_packages_on_path(tmp_path: Path) -> None:
    installed = detect_installed_clients(
        home=tmp_path,
        environ={},
        which=_which({'mocktail': '/usr/bin/mocktail', 'cordial-shell': '/usr/bin/cordial-shell'}),
        run=cast('CommandRunner', _no_flatpak),
    )

    assert [(item.key, item.kind) for item in installed] == [
        ('mocktail', 'native'),
        ('cordial', 'native'),
    ]
    cordial = installed[1]
    assert cordial.desktop_id == 'io.github.luohoa97.Cordial.desktop'
    assert cordial.launch_command('roblox://x') == ['/usr/bin/cordial-shell', 'roblox://x']
    assert cordial.paths.data_root == tmp_path / '.local' / 'share' / 'cordial'


def test_flatpak_exported_commands_are_not_native_installs(tmp_path: Path) -> None:
    installed = detect_installed_clients(
        home=tmp_path,
        environ={},
        which=_which({'mocktail': '/var/lib/flatpak/exports/bin/mocktail'}),
        run=cast('CommandRunner', _no_flatpak),
    )

    assert installed == ()


def test_detects_appimage_through_its_desktop_entry(tmp_path: Path) -> None:
    appimage = _appimage(tmp_path / 'Games' / 'Cordial-0.34.0-0-gfe20025-x86_64.AppImage')
    applications = tmp_path / '.local' / 'share' / 'applications'
    applications.mkdir(parents=True)
    entry = applications / 'appimagekit_0123-Cordial.desktop'
    entry.write_text(f'[Desktop Entry]\nType=Application\nExec="{appimage}" %u\n', encoding='utf-8')

    (cordial,) = detect_installed_clients(
        home=tmp_path, environ={}, which=_which({}), run=cast('CommandRunner', _no_flatpak)
    )

    assert cordial.kind == 'appimage'
    assert cordial.executable == appimage
    assert cordial.desktop_entry == entry
    assert cordial.desktop_id == 'appimagekit_0123-Cordial.desktop'
    assert cordial.launch_command('roblox://x') == [str(appimage), 'roblox://x']


def test_detects_newest_appimage_in_conventional_folders(tmp_path: Path) -> None:
    old = _appimage(tmp_path / 'Applications' / 'Mocktail-1.0.3-x86_64.AppImage')
    new = _appimage(tmp_path / 'Applications' / 'Mocktail-1.0.4-x86_64.AppImage')
    os.utime(old, (1, 1))
    _appimage(tmp_path / 'Applications' / 'Unrelated-x86_64.AppImage')

    (mocktail,) = detect_installed_clients(
        home=tmp_path, environ={}, which=_which({}), run=cast('CommandRunner', _no_flatpak)
    )

    assert (mocktail.key, mocktail.kind, mocktail.executable) == ('mocktail', 'appimage', new)
    assert mocktail.desktop_entry is None


def _flatpak_cordial_only(command: list[str], **_kwargs: object):
    installed = command[-1] == CORDIAL_CLIENT.app_id
    return subprocess.CompletedProcess(command, 0 if installed else 1, '', '')


def test_flatpak_wins_when_installed_both_ways_without_a_desktop_hint(tmp_path: Path) -> None:
    (cordial,) = detect_installed_clients(
        home=tmp_path,
        environ={'XDG_DATA_DIRS': str(tmp_path / 'empty')},
        which=_which({'flatpak': '/usr/bin/flatpak', 'cordial-shell': '/usr/bin/cordial-shell'}),
        run=cast('CommandRunner', _flatpak_cordial_only),
    )

    assert cordial.kind == 'flatpak'


@pytest.mark.parametrize(
    ('exec_line', 'kind'),
    [
        ('cordial-shell %u', 'native'),
        ('/usr/bin/flatpak run io.github.luohoa97.Cordial %u', 'flatpak'),
    ],
)
def test_the_desktop_entry_that_handles_links_picks_the_install(tmp_path: Path, exec_line: str, kind: str) -> None:
    system = tmp_path / 'system'
    (system / 'applications').mkdir(parents=True)
    (system / 'applications' / CORDIAL_CLIENT.desktop_id).write_text(
        f'[Desktop Entry]\nExec={exec_line}\n', encoding='utf-8'
    )

    (cordial,) = detect_installed_clients(
        home=tmp_path,
        environ={'XDG_DATA_DIRS': str(system)},
        which=_which({'flatpak': '/usr/bin/flatpak', 'cordial-shell': '/usr/bin/cordial-shell'}),
        run=cast('CommandRunner', _flatpak_cordial_only),
    )

    assert cordial.kind == kind


def test_native_processes_are_those_outside_the_flatpak_scope(tmp_path: Path) -> None:
    flatpak = CORDIAL_CLIENT.paths(home=tmp_path, environ={})
    native = CORDIAL_CLIENT.paths(home=tmp_path, environ={}, kind='native')
    in_scope = '0::/user.slice/app-flatpak-io.github.luohoa97.Cordial-2.scope\n'
    on_host = '0::/user.slice/app-gnome-cordial-3.scope\n'

    flatpak_install = LinuxClientInstallation(CORDIAL_CLIENT, flatpak, Path('/usr/bin/flatpak'))
    native_install = LinuxClientInstallation(CORDIAL_CLIENT, native, Path('/usr/bin/cordial-shell'))

    assert flatpak_install.owns_process_cgroup(in_scope)
    assert not flatpak_install.owns_process_cgroup(on_host)
    assert native_install.owns_process_cgroup(on_host)
    assert not native_install.owns_process_cgroup(in_scope)


# Proxy environment through a user desktop entry


@pytest.fixture
def native_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NativeProxy:
    applications = tmp_path / 'home' / '.local' / 'share' / 'applications'
    monkeypatch.setattr(platform_linux, 'LINUX_APPLICATIONS_DIR', applications)
    monkeypatch.setattr(
        platform_linux, 'LINUX_CLIENT_DESKTOP_BACKUP_DIR', tmp_path / 'fleasion' / 'desktop'
    )
    monkeypatch.setattr(
        platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', tmp_path / 'fleasion' / 'override.json'
    )
    monkeypatch.setattr(
        platform_linux, 'MOCKTAIL_INTEGRATION_STATE', tmp_path / 'fleasion' / 'mocktail.json'
    )
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(platform_linux, '_active_linux_client_environment', {})
    system = tmp_path / 'system'
    (system / 'applications').mkdir(parents=True)
    monkeypatch.setenv('XDG_DATA_DIRS', str(system))
    monkeypatch.setenv('XDG_DATA_HOME', str(applications.parent))

    def forbid_flatpak(*_args: object) -> bool:
        raise AssertionError('a native client must not get a flatpak override')

    monkeypatch.setattr(platform_linux, '_flatpak_override', forbid_flatpak)
    return tmp_path, system / 'applications', applications


def _use(monkeypatch: pytest.MonkeyPatch, installation: LinuxClientInstallation) -> None:
    monkeypatch.setattr(platform_linux, '_installation_for_client_key', _returning(installation))


def test_native_cordial_env_proxy_wraps_and_restores_the_desktop_entry(native_proxy: NativeProxy, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path, system_apps, user_apps = native_proxy
    (system_apps / CORDIAL_CLIENT.desktop_id).write_text(CORDIAL_DESKTOP_ENTRY, encoding='utf-8')
    installation = LinuxClientInstallation(
        CORDIAL_CLIENT,
        CORDIAL_CLIENT.paths(home=tmp_path / 'home', environ={}, kind='native'),
        Path('/usr/bin/cordial-shell'),
    )
    _use(monkeypatch, installation)

    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='cordial'
    )
    shadow = (user_apps / CORDIAL_CLIENT.desktop_id).read_text(encoding='utf-8')
    exec_lines = [line for line in shadow.splitlines() if line.startswith('Exec=')]
    assert len(exec_lines) == 2
    for line in exec_lines:
        assert line.startswith('Exec=env ALL_PROXY=http://127.0.0.1:60001 ')
        assert 'no_proxy=localhost,127.0.0.1,::1' in line
    assert exec_lines[0].endswith(' cordial-shell %u')
    assert 'DBusActivatable' not in shadow
    assert 'X-Fleasion-Environment=true' in shadow
    assert '"install": "native"' in platform_linux.LINUX_PROXY_OVERRIDE_STATE.read_text()

    launched: list[tuple[list[str], dict[str, str] | None]] = []
    monkeypatch.setattr(
        platform_linux,
        '_standard_user_popen',
        _recording_popen(launched),
    )
    monkeypatch.setattr(
        platform_linux, 'get_selected_linux_client_installation', _returning(installation)
    )
    assert platform_linux.launch_as_standard_user('roblox://x')
    assert launched[0][0] == ['/usr/bin/cordial-shell', 'roblox://x']
    extra_env = launched[0][1]
    assert extra_env is not None
    assert extra_env['HTTPS_PROXY'] == 'http://127.0.0.1:60001'

    assert platform_linux.clear_linux_client_env_proxy_override(client_key='cordial')
    assert not (user_apps / CORDIAL_CLIENT.desktop_id).exists()
    assert not platform_linux.LINUX_PROXY_OVERRIDE_STATE.exists()
    assert platform_linux._active_linux_client_environment == {}  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]


def test_a_users_own_desktop_entry_is_backed_up_and_put_back(native_proxy: NativeProxy, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path, _system_apps, user_apps = native_proxy
    user_apps.mkdir(parents=True)
    own = '[Desktop Entry]\nType=Application\nName=My Cordial\nExec=cordial-shell --x %u\n'
    (user_apps / CORDIAL_CLIENT.desktop_id).write_text(own, encoding='utf-8')
    _use(
        monkeypatch,
        LinuxClientInstallation(
            CORDIAL_CLIENT,
            CORDIAL_CLIENT.paths(home=tmp_path / 'home', environ={}, kind='native'),
            Path('/usr/bin/cordial-shell'),
        ),
    )

    for port in (60001, 60002):  # rearming must not back up Fleasion's own entry
        assert platform_linux.set_linux_client_env_proxy_override(
            f'http://127.0.0.1:{port}', client_key='cordial'
        )
    shadow = (user_apps / CORDIAL_CLIENT.desktop_id).read_text(encoding='utf-8')
    assert 'Name=My Cordial' in shadow
    assert 'HTTPS_PROXY=http://127.0.0.1:60002' in shadow

    assert platform_linux.clear_linux_client_env_proxy_override(client_key='cordial')
    assert (user_apps / CORDIAL_CLIENT.desktop_id).read_text(encoding='utf-8') == own


def test_an_entry_the_user_rewrote_while_armed_is_left_alone(native_proxy: NativeProxy, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path, system_apps, user_apps = native_proxy
    (system_apps / CORDIAL_CLIENT.desktop_id).write_text(CORDIAL_DESKTOP_ENTRY, encoding='utf-8')
    _use(
        monkeypatch,
        LinuxClientInstallation(
            CORDIAL_CLIENT,
            CORDIAL_CLIENT.paths(home=tmp_path / 'home', environ={}, kind='native'),
            Path('/usr/bin/cordial-shell'),
        ),
    )
    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='cordial'
    )
    rewritten = '[Desktop Entry]\nExec=cordial-shell --mine %u\n'
    (user_apps / CORDIAL_CLIENT.desktop_id).write_text(rewritten, encoding='utf-8')

    assert platform_linux.clear_linux_client_env_proxy_override(client_key='cordial')
    assert (user_apps / CORDIAL_CLIENT.desktop_id).read_text(encoding='utf-8') == rewritten


def test_appimage_env_proxy_edits_its_own_entry_in_place(native_proxy: NativeProxy, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path, _system_apps, user_apps = native_proxy
    appimage = _appimage(tmp_path / 'home' / 'Applications' / 'Cordial-x86_64.AppImage')
    user_apps.mkdir(parents=True)
    entry = user_apps / 'gearlever_cordial.desktop'
    original = f'[Desktop Entry]\nType=Application\nExec={appimage} %u\n'
    entry.write_text(original, encoding='utf-8')
    _use(
        monkeypatch,
        LinuxClientInstallation(
            CORDIAL_CLIENT,
            CORDIAL_CLIENT.paths(home=tmp_path / 'home', environ={}, kind='appimage'),
            appimage,
            desktop_entry=entry,
        ),
    )

    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='cordial'
    )
    (exec_line,) = [line for line in entry.read_text().splitlines() if line.startswith('Exec=')]
    assert 'HTTPS_PROXY=http://127.0.0.1:60001' in exec_line
    assert exec_line.endswith(f' {appimage} %u')
    assert not (user_apps / CORDIAL_CLIENT.desktop_id).exists()

    assert platform_linux.clear_linux_client_env_proxy_override(client_key='cordial')
    assert entry.read_text(encoding='utf-8') == original


def test_native_mocktail_gets_its_integration_without_a_flatpak(native_proxy: NativeProxy, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path, system_apps, user_apps = native_proxy
    (system_apps / MOCKTAIL_CLIENT.desktop_id).write_text(
        '[Desktop Entry]\nType=Application\nExec=mocktail %u\n', encoding='utf-8'
    )
    for name in (*platform_linux.MOCKTAIL_INTEGRATION_ENV_NAMES, 'MOCKTAIL_USE_SYSTEM_PROXY'):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / 'home'
    installation = LinuxClientInstallation(
        MOCKTAIL_CLIENT,
        MOCKTAIL_CLIENT.paths(home=home, environ={}, kind='native'),
        Path('/usr/bin/mocktail'),
    )
    _use(monkeypatch, installation)
    config = installation.paths.config_file
    config.parent.mkdir(parents=True)
    config.write_text('version: 1\n', encoding='utf-8')
    ca_file = tmp_path / 'ca.crt'
    ca_file.write_text(CA_PEM, encoding='utf-8')

    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca_file
    )
    host_ca = home / '.config' / 'Fleasion' / 'proxy_ca' / 'ca.crt'
    assert host_ca.read_text(encoding='utf-8') == CA_PEM
    assert mocktail_config.read_integration(config.read_text(encoding='utf-8'))['enabled'] == (
        'true'
    )
    shadow = (user_apps / MOCKTAIL_CLIENT.desktop_id).read_text(encoding='utf-8')
    assert f'MOCKTAIL_FLEASION_CA_CERTIFICATE={host_ca}' in shadow
    assert 'MOCKTAIL_FLEASION_PROXY_PORT=60001 mocktail %u' in shadow

    assert platform_linux.clear_linux_client_env_proxy_override(client_key='mocktail')
    assert config.read_text(encoding='utf-8') == 'version: 1\n'
    assert not (user_apps / MOCKTAIL_CLIENT.desktop_id).exists()


def test_native_mocktail_refuses_a_conflicting_session_environment(native_proxy: NativeProxy, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path, _system_apps, _user_apps = native_proxy
    monkeypatch.setenv('MOCKTAIL_USE_SYSTEM_PROXY', '1')
    installation = LinuxClientInstallation(
        MOCKTAIL_CLIENT,
        MOCKTAIL_CLIENT.paths(home=tmp_path / 'home', environ={}, kind='native'),
        Path('/usr/bin/mocktail'),
    )
    _use(monkeypatch, installation)
    ca_file = tmp_path / 'ca.crt'
    ca_file.write_text(CA_PEM, encoding='utf-8')

    assert not platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca_file
    )


def test_cleanup_targets_the_install_kind_that_was_armed(native_proxy: NativeProxy, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path, _system_apps, _user_apps = native_proxy
    platform_linux._write_linux_proxy_override_state('cordial', 'native')  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    flatpak = LinuxClientInstallation(
        CORDIAL_CLIENT,
        CORDIAL_CLIENT.paths(home=tmp_path / 'home', environ={}),
        Path('/usr/bin/flatpak'),
    )
    _use(monkeypatch, flatpak)

    cleanup = platform_linux._installation_for_cleanup('cordial')  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]

    assert cleanup is not None
    assert cleanup.kind == 'native'


def test_legacy_override_state_is_a_flatpak(native_proxy: NativeProxy) -> None:
    platform_linux.LINUX_PROXY_OVERRIDE_STATE.parent.mkdir(parents=True)
    platform_linux.LINUX_PROXY_OVERRIDE_STATE.write_text('{"client": "cordial"}\n')

    assert platform_linux._read_linux_proxy_override_kind('cordial') == 'flatpak'  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('HTTPS_PROXY=http://127.0.0.1:1', 'HTTPS_PROXY=http://127.0.0.1:1'),
        ('CA=/home/a b/ca.crt', '"CA=/home/a b/ca.crt"'),
        ('X=$HOME"', '"X=\\\\$HOME\\\\""'),
    ],
)
def test_desktop_exec_arguments_are_quoted_per_the_spec(value: str, expected: str) -> None:
    assert platform_linux._desktop_exec_argument(value) == expected  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
