import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from fleasion import __version__
from fleasion.utils import platform_linux

pytestmark = pytest.mark.skipif(
    sys.platform == 'win32', reason='Linux-only desktop integration tests'
)


def _copy_linux_app_payload() -> tuple[Path | None, Path | None]:
    callback = cast(
        'Callable[[], tuple[Path | None, Path | None]]',
        platform_linux.__dict__['_copy_linux_app_payload'],
    )
    return callback()


def _no_payload() -> tuple[None, None]:
    return None, None


def _launch_command(
    installed_app: Path | None = None,
) -> tuple[list[str], Path | None]:
    del installed_app
    return ['/usr/bin/python3', '-m', 'fleasion'], Path('/opt/fleasion')


def _launch_command_no_cwd(
    installed_app: Path | None = None,
) -> tuple[list[str], Path | None]:
    del installed_app
    return ['/usr/bin/python3', '-m', 'fleasion'], None


def _no_icon() -> None:
    return None


def _no_which(_name: str) -> None:
    return None


def _xdg_which(name: str) -> str | None:
    return '/usr/bin/xdg-mime' if name == 'xdg-mime' else None


def _record_log(logs: list[tuple[str, str]]) -> object:
    class Log:
        @staticmethod
        def log(category: str, message: str) -> None:
            logs.append((category, message))

    return Log()


def test_install_desktop_entries_writes_user_launcher_and_removes_deprecated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr(platform_linux, '_copy_linux_app_payload', _no_payload)
    monkeypatch.setattr(
        platform_linux,
        '_linux_app_launch_command',
        _launch_command,
    )
    monkeypatch.setattr(platform_linux, 'get_icon_path', _no_icon)
    monkeypatch.setattr(platform_linux.shutil, 'which', _no_which)

    result = platform_linux.install_desktop_entries()

    desktop_text = (applications / 'fleasion.desktop').read_text(encoding='utf-8')
    launcher_text = (bin_dir / 'fleasion-launch').read_text(encoding='utf-8')

    assert 'Name=Fleasion' in desktop_text
    assert f'Exec={bin_dir / "fleasion-launch"}' in desktop_text
    assert 'fleasion-non-admin' not in desktop_text
    assert 'pkexec' not in launcher_text
    assert 'FLEASION_USER_HOME' in launcher_text
    assert 'exec /usr/bin/python3 -m fleasion' in launcher_text
    assert 'MimeType=x-scheme-handler/roblox' not in desktop_text
    assert '%U' not in desktop_text
    assert not deprecated.exists()
    assert result['removed_deprecated_entries'] == [str(deprecated)]


def test_install_desktop_entries_restores_sober_when_fleasion_is_uri_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    applications = tmp_path / '.local' / 'share' / 'applications'
    bin_dir = tmp_path / '.local' / 'bin'
    commands: list[list[str]] = []

    monkeypatch.setattr(platform_linux, 'USER_HOME', tmp_path)
    monkeypatch.setattr(platform_linux, 'LINUX_APPLICATIONS_DIR', applications)
    monkeypatch.setattr(platform_linux, 'LINUX_BIN_DIR', bin_dir)
    monkeypatch.setattr(
        platform_linux, 'LINUX_DESKTOP_ENTRY_PATH', applications / 'fleasion.desktop'
    )
    monkeypatch.setattr(platform_linux, 'LINUX_LAUNCHER_PATH', bin_dir / 'fleasion-launch')
    monkeypatch.setattr(platform_linux, 'LINUX_DEPRECATED_DESKTOP_ENTRY_PATHS', ())
    monkeypatch.setattr(platform_linux, '_copy_linux_app_payload', _no_payload)
    monkeypatch.setattr(
        platform_linux,
        '_linux_app_launch_command',
        _launch_command_no_cwd,
    )
    monkeypatch.setattr(platform_linux, 'get_icon_path', _no_icon)
    installation = platform_linux.LinuxClientInstallation(
        client=platform_linux.SOBER_CLIENT,
        paths=platform_linux.SOBER_CLIENT.paths(home=tmp_path, environ={}),
        executable=Path('/usr/bin/flatpak'),
    )

    def selected_installation() -> platform_linux.LinuxClientInstallation:
        return installation

    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        selected_installation,
    )
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        _xdg_which,
    )

    def run(
        command: list[str], **_kwargs: object
    ) -> platform_linux.subprocess.CompletedProcess[str]:
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


def test_copy_linux_app_payload_copies_frozen_binary_and_icon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_binary = tmp_path / 'Downloads' / f'Fleasion-v{__version__}'
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

    def source_icon_path() -> Path:
        return source_icon

    monkeypatch.setattr(platform_linux, 'get_icon_path', source_icon_path)

    installed_app, installed_icon = _copy_linux_app_payload()

    assert installed_app == install_dir / 'Fleasion'
    assert installed_icon == install_dir / 'fleasionlogoHR.ico'
    assert installed_app is not None
    assert installed_icon is not None
    assert installed_app.read_bytes() == b'binary'
    assert installed_icon.read_bytes() == b'icon'
    assert installed_app.stat().st_mode & 0o111


def test_copy_linux_app_payload_does_not_copy_nix_store_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_binary = Path('/nix/store/abc123-fleasion/bin/Fleasion')
    install_dir = tmp_path / '.local' / 'share' / 'Fleasion'
    logs: list[tuple[str, str]] = []

    monkeypatch.setattr(platform_linux.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(platform_linux.sys, 'executable', str(source_binary))
    monkeypatch.setattr(platform_linux, 'LINUX_INSTALL_DIR', install_dir)
    monkeypatch.setattr(platform_linux, 'LINUX_INSTALLED_APP_PATH', install_dir / 'Fleasion')
    monkeypatch.setattr(
        platform_linux, 'LINUX_INSTALLED_ICON_PATH', install_dir / 'fleasionlogoHR.ico'
    )
    monkeypatch.setattr(platform_linux, 'log_buffer', _record_log(logs))

    installed_app, installed_icon = _copy_linux_app_payload()

    assert installed_app is None
    assert installed_icon is None
    assert not install_dir.exists()
    assert any('Nix store executable' in message for _category, message in logs)
