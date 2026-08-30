import plistlib
from pathlib import Path

import pytest

from fleasion.utils import desktop_integration


def _record_result(calls: list[str], name: str):
    def callback() -> bool:
        calls.append(name)
        return True

    return callback


def _record_install(calls: list[str]):
    def install() -> dict[str, object]:
        calls.append('install')
        return {}

    return install


def test_sync_desktop_integration_dispatches_windows_create_and_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(desktop_integration.sys, 'platform', 'win32')
    monkeypatch.setattr(
        desktop_integration, '_create_windows_shortcut', _record_result(calls, 'create')
    )
    monkeypatch.setattr(
        desktop_integration, '_remove_windows_shortcut', _record_result(calls, 'remove')
    )

    assert desktop_integration.sync_desktop_integration(True)
    assert desktop_integration.sync_desktop_integration(False)
    assert calls == ['create', 'remove']


def test_sync_desktop_integration_delegates_linux_install_and_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop_entry = tmp_path / 'applications' / 'fleasion.desktop'
    launcher = tmp_path / 'bin' / 'fleasion-launch'
    desktop_entry.parent.mkdir()
    launcher.parent.mkdir()
    desktop_entry.write_text('entry', encoding='utf-8')
    launcher.write_text('launcher', encoding='utf-8')
    calls: list[str] = []

    monkeypatch.setattr(desktop_integration.sys, 'platform', 'linux')

    from fleasion.utils import platform_linux

    monkeypatch.setattr(platform_linux, 'LINUX_DESKTOP_ENTRY_PATH', desktop_entry)
    monkeypatch.setattr(platform_linux, 'LINUX_LAUNCHER_PATH', launcher)
    monkeypatch.setattr(platform_linux, 'install_desktop_entries', _record_install(calls))

    assert desktop_integration.sync_desktop_integration(True)
    assert calls == ['install']
    assert desktop_entry.exists()
    assert launcher.exists()

    assert desktop_integration.sync_desktop_integration(False)
    assert not desktop_entry.exists()
    assert not launcher.exists()


def test_macos_launcher_app_contains_metadata_icon_and_current_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_path = tmp_path / 'Applications' / 'Fleasion.app'
    icon = tmp_path / 'fleasionlogoHR.icns'
    icon.write_bytes(b'icon')
    project = tmp_path / 'Project'

    monkeypatch.setattr(desktop_integration.sys, 'platform', 'darwin')
    monkeypatch.setattr(desktop_integration, 'APP_VERSION', '2.4.0b1')
    monkeypatch.setattr(desktop_integration, 'MACOS_APPLICATION_PATH', app_path)

    def icon_path() -> Path:
        return icon

    def launch_command() -> tuple[list[str], Path, dict[str, str]]:
        return (
            ['/usr/bin/uv', '--project', str(project), 'run', 'fleasion'],
            project,
            {},
        )

    monkeypatch.setattr(desktop_integration, 'get_icon_path', icon_path)
    monkeypatch.setattr(desktop_integration, '_launch_command', launch_command)

    assert desktop_integration.sync_desktop_integration(True)

    info = plistlib.loads((app_path / 'Contents' / 'Info.plist').read_bytes())
    script = (app_path / 'Contents' / 'MacOS' / 'Fleasion').read_text(encoding='utf-8')

    assert info['CFBundleDisplayName'] == 'Fleasion'
    assert info['CFBundleShortVersionString'] == '2.4.0'
    assert info['CFBundleVersion'] == '2.4.0'
    assert info['CFBundleIconFile'] == 'fleasionlogoHR'
    assert info['NSHumanReadableCopyright'] == 'Roblox asset interceptor and replacer'
    assert 'CFBundleURLTypes' not in info
    assert 'exec /usr/bin/uv --project' in script
    assert 'run fleasion' in script
    assert (app_path / 'Contents' / 'Resources' / 'fleasionlogoHR.icns').read_bytes() == b'icon'
    assert (app_path / 'Contents' / '.fleasion-managed-launcher').exists()


def test_macos_remove_only_deletes_managed_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_path = tmp_path / 'Applications' / 'Fleasion.app'
    contents = app_path / 'Contents'
    contents.mkdir(parents=True)
    (contents / '.fleasion-managed-launcher').write_text('managed', encoding='utf-8')

    monkeypatch.setattr(desktop_integration.sys, 'platform', 'darwin')
    monkeypatch.setattr(desktop_integration, 'MACOS_APPLICATION_PATH', app_path)

    assert desktop_integration.sync_desktop_integration(False)
    assert not app_path.exists()


def test_macos_create_refuses_to_overwrite_unmanaged_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_path = tmp_path / 'Applications' / 'Fleasion.app'
    (app_path / 'Contents').mkdir(parents=True)

    monkeypatch.setattr(desktop_integration.sys, 'platform', 'darwin')
    monkeypatch.setattr(desktop_integration, 'MACOS_APPLICATION_PATH', app_path)

    assert not desktop_integration.sync_desktop_integration(True)
    assert app_path.exists()
