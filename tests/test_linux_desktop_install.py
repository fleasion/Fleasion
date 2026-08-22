import sys
from pathlib import Path

import pytest

from fleasion import __version__ as APP_VERSION
from fleasion.utils import platform_linux


pytestmark = pytest.mark.skipif(
    sys.platform == 'win32', reason='Linux-only desktop integration tests'
)


def test_install_desktop_entries_writes_user_launcher_and_removes_deprecated(tmp_path, monkeypatch):
    applications = tmp_path / '.local' / 'share' / 'applications'
    bin_dir = tmp_path / '.local' / 'bin'
    deprecated = applications / 'fleasion-non-admin.desktop'
    deprecated.parent.mkdir(parents=True)
    deprecated.write_text('old', encoding='utf-8')

    monkeypatch.setattr(platform_linux, 'USER_HOME', tmp_path)
    monkeypatch.setattr(platform_linux, 'LINUX_APPLICATIONS_DIR', applications)
    install_dir = tmp_path / '.local' / 'share' / 'Fleasion'

    monkeypatch.setattr(platform_linux, 'LINUX_BIN_DIR', bin_dir)
    monkeypatch.setattr(platform_linux, 'LINUX_INSTALL_DIR', install_dir)
    monkeypatch.setattr(
        platform_linux, 'LINUX_DESKTOP_ENTRY_PATH', applications / 'fleasion.desktop'
    )
    monkeypatch.setattr(platform_linux, 'LINUX_LAUNCHER_PATH', bin_dir / 'fleasion-launch')
    monkeypatch.setattr(platform_linux, 'LINUX_INSTALLED_APP_PATH', install_dir / 'Fleasion')
    monkeypatch.setattr(
        platform_linux, 'LINUX_INSTALLED_ICON_PATH', install_dir / 'fleasionlogoHR.ico'
    )
    monkeypatch.setattr(platform_linux, 'LINUX_DEPRECATED_DESKTOP_ENTRY_PATHS', (deprecated,))
    monkeypatch.setattr(platform_linux, '_copy_linux_app_payload', lambda: (None, None))
    monkeypatch.setattr(
        platform_linux,
        '_linux_app_launch_command',
        lambda installed_app=None: (['/usr/bin/python3', 'launcher.py'], Path('/opt/fleasion')),
    )
    monkeypatch.setattr(platform_linux, 'get_icon_path', lambda: None)
    monkeypatch.setattr(platform_linux.shutil, 'which', lambda name: None)

    result = platform_linux.install_desktop_entries()

    desktop_text = (applications / 'fleasion.desktop').read_text(encoding='utf-8')
    launcher_text = (bin_dir / 'fleasion-launch').read_text(encoding='utf-8')

    assert 'Name=Fleasion' in desktop_text
    assert f'Exec={bin_dir / "fleasion-launch"}' in desktop_text
    assert 'fleasion-non-admin' not in desktop_text
    assert 'pkexec' not in launcher_text
    assert 'FLEASION_USER_HOME' in launcher_text
    assert 'exec /usr/bin/python3 launcher.py' in launcher_text
    assert 'MimeType=x-scheme-handler/roblox' not in desktop_text
    assert '%U' not in desktop_text
    assert not deprecated.exists()
    assert result['removed_deprecated_entries'] == [str(deprecated)]


def test_install_desktop_entries_restores_sober_when_fleasion_is_uri_handler(tmp_path, monkeypatch):
    applications = tmp_path / '.local' / 'share' / 'applications'
    bin_dir = tmp_path / '.local' / 'bin'
    commands = []

    monkeypatch.setattr(platform_linux, 'USER_HOME', tmp_path)
    monkeypatch.setattr(platform_linux, 'LINUX_APPLICATIONS_DIR', applications)
    monkeypatch.setattr(platform_linux, 'LINUX_BIN_DIR', bin_dir)
    monkeypatch.setattr(
        platform_linux, 'LINUX_DESKTOP_ENTRY_PATH', applications / 'fleasion.desktop'
    )
    monkeypatch.setattr(platform_linux, 'LINUX_LAUNCHER_PATH', bin_dir / 'fleasion-launch')
    monkeypatch.setattr(platform_linux, 'LINUX_DEPRECATED_DESKTOP_ENTRY_PATHS', ())
    monkeypatch.setattr(platform_linux, '_copy_linux_app_payload', lambda: (None, None))
    monkeypatch.setattr(
        platform_linux,
        '_linux_app_launch_command',
        lambda installed_app=None: (['/usr/bin/python3', 'launcher.py'], None),
    )
    monkeypatch.setattr(platform_linux, 'get_icon_path', lambda: None)
    installation = platform_linux.LinuxClientInstallation(
        client=platform_linux.SOBER_CLIENT,
        paths=platform_linux.SOBER_CLIENT.paths(home=tmp_path, environ={}),
        executable=Path('/usr/bin/flatpak'),
    )
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        lambda name: '/usr/bin/xdg-mime' if name == 'xdg-mime' else None,
    )

    def run(command, **_kwargs):
        commands.append(command)
        if command[1:3] == ['query', 'default']:
            return platform_linux.subprocess.CompletedProcess(command, 0, 'fleasion.desktop\n', '')
        return platform_linux.subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(platform_linux.subprocess, 'run', run)

    result = platform_linux.install_desktop_entries()

    assert result['sober_uri_handler_restored']
    assert commands == [
        ['/usr/bin/xdg-mime', 'query', 'default', 'x-scheme-handler/roblox'],
        ['/usr/bin/xdg-mime', 'query', 'default', 'x-scheme-handler/roblox-player'],
        [
            '/usr/bin/xdg-mime',
            'default',
            'org.vinegarhq.Sober.desktop',
            'x-scheme-handler/roblox',
        ],
        [
            '/usr/bin/xdg-mime',
            'default',
            'org.vinegarhq.Sober.desktop',
            'x-scheme-handler/roblox-player',
        ],
    ]


def test_copy_linux_app_payload_copies_frozen_binary_and_icon(tmp_path, monkeypatch):
    source_binary = tmp_path / 'Downloads' / f'Fleasion-v{APP_VERSION}'
    source_binary.parent.mkdir()
    source_binary.write_bytes(b'binary')
    source_icon = tmp_path / 'Downloads' / 'fleasionlogoHR.ico'
    source_icon.write_bytes(b'icon')
    install_dir = tmp_path / '.local' / 'share' / 'Fleasion'

    monkeypatch.setattr(platform_linux.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(platform_linux.sys, 'executable', str(source_binary))
    monkeypatch.setattr(platform_linux, 'LINUX_INSTALL_DIR', install_dir)
    monkeypatch.setattr(platform_linux, 'LINUX_INSTALLED_APP_PATH', install_dir / 'Fleasion')
    monkeypatch.setattr(
        platform_linux, 'LINUX_INSTALLED_ICON_PATH', install_dir / 'fleasionlogoHR.ico'
    )
    monkeypatch.setattr(platform_linux, 'get_icon_path', lambda: source_icon)

    installed_app, installed_icon = platform_linux._copy_linux_app_payload()

    assert installed_app == install_dir / 'Fleasion'
    assert installed_icon == install_dir / 'fleasionlogoHR.ico'
    assert installed_app.read_bytes() == b'binary'
    assert installed_icon.read_bytes() == b'icon'
    assert installed_app.stat().st_mode & 0o111


def test_copy_linux_app_payload_does_not_copy_nix_store_binary(monkeypatch, tmp_path):
    source_binary = Path('/nix/store/abc123-fleasion/bin/Fleasion')
    install_dir = tmp_path / '.local' / 'share' / 'Fleasion'
    logs = []

    monkeypatch.setattr(platform_linux.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(platform_linux.sys, 'executable', str(source_binary))
    monkeypatch.setattr(platform_linux, 'LINUX_INSTALL_DIR', install_dir)
    monkeypatch.setattr(platform_linux, 'LINUX_INSTALLED_APP_PATH', install_dir / 'Fleasion')
    monkeypatch.setattr(
        platform_linux, 'LINUX_INSTALLED_ICON_PATH', install_dir / 'fleasionlogoHR.ico'
    )
    monkeypatch.setattr(
        platform_linux,
        'log_buffer',
        type(
            'Log',
            (),
            {'log': staticmethod(lambda category, message: logs.append((category, message)))},
        )(),
    )

    installed_app, installed_icon = platform_linux._copy_linux_app_payload()

    assert installed_app is None
    assert installed_icon is None
    assert not install_dir.exists()
    assert any('Nix store executable' in message for _category, message in logs)
