from pathlib import Path
from types import SimpleNamespace

from fleasion.utils import roblox_dirs, windows_permissions


def _player_install(tmp_path: Path) -> Path:
    install = tmp_path / 'Roblox' / 'Versions' / 'version-test'
    install.mkdir(parents=True)
    (install / 'RobloxPlayerBeta.exe').write_bytes(b'')
    return install


def test_grant_uses_only_current_user_sid_and_modify_inheritance(tmp_path, monkeypatch):
    install = _player_install(tmp_path)
    calls = []

    monkeypatch.setattr(windows_permissions, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(roblox_dirs.sys, 'platform', 'win32')
    monkeypatch.setattr(roblox_dirs, 'ROBLOX_PROCESS', 'RobloxPlayerBeta.exe')
    monkeypatch.setattr(roblox_dirs, 'ROBLOX_STUDIO_PROCESS', 'RobloxStudioBeta.exe')
    monkeypatch.setattr(windows_permissions, '_current_user_sid', lambda: 'S-1-5-21-1234')
    monkeypatch.setattr(
        windows_permissions.subprocess,
        'run',
        lambda command, **kwargs: calls.append((command, kwargs))
        or SimpleNamespace(returncode=0, stdout='', stderr=''),
    )

    result = windows_permissions.grant_current_user_modify_access([install])

    assert result['ok'] is True
    assert result['granted'] == [str(install.resolve())]
    command = calls[0][0]
    assert command[:3] == ['icacls.exe', str(install.resolve()), '/grant:r']
    assert '*S-1-5-21-1234:(OI)(CI)M' in command
    assert 'S-1-5-32-545' not in ' '.join(command)
    assert calls[0][1]['timeout'] == 120


def test_grant_rejects_non_install_and_studio_paths(tmp_path, monkeypatch):
    install = _player_install(tmp_path)
    (install / 'RobloxStudioBeta.exe').write_bytes(b'')
    monkeypatch.setattr(windows_permissions, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(roblox_dirs.sys, 'platform', 'win32')
    monkeypatch.setattr(roblox_dirs, 'ROBLOX_PROCESS', 'RobloxPlayerBeta.exe')
    monkeypatch.setattr(roblox_dirs, 'ROBLOX_STUDIO_PROCESS', 'RobloxStudioBeta.exe')

    result = windows_permissions.grant_current_user_modify_access([install])

    assert result['ok'] is False
    assert result['granted'] == []
    assert result['failed'][0]['path'] == str(install)
