from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, cast

from fleasion.utils import roblox_dirs, windows_permissions

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

ICACLS_TIMEOUT_SECONDS = 120
SID_TYPE_USER = 1


@dataclass(frozen=True, slots=True)
class _FakeSid:
    value: str


def _validated_user_sid_stub(requested: str | None = None) -> str:
    return requested or 'S-1-5-21-1234'


def _convert_string_sid_to_sid(value: str) -> _FakeSid:
    return _FakeSid(value)


def _convert_sid_to_string_sid(sid: object) -> str:
    assert isinstance(sid, _FakeSid)
    return sid.value


def _lookup_account_sid(_system: object | None, _sid: object) -> tuple[str, str, int]:
    return 'OriginalUser', 'DesktopDomain', SID_TYPE_USER


def _player_install(tmp_path: Path) -> Path:
    install = tmp_path / 'Roblox' / 'Versions' / 'version-test'
    install.mkdir(parents=True)
    (install / 'RobloxPlayerBeta.exe').write_bytes(b'')
    return install


def test_grant_uses_only_current_user_sid_and_modify_inheritance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = _player_install(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(windows_permissions, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(roblox_dirs.sys, 'platform', 'win32')
    monkeypatch.setattr(roblox_dirs, 'ROBLOX_PROCESS', 'RobloxPlayerBeta.exe')
    monkeypatch.setattr(roblox_dirs, 'ROBLOX_STUDIO_PROCESS', 'RobloxStudioBeta.exe')
    monkeypatch.setattr(windows_permissions, '_validated_user_sid', _validated_user_sid_stub)
    monkeypatch.setattr(windows_permissions.subprocess, 'run', fake_run)

    result = windows_permissions.grant_current_user_modify_access(
        [install],
        user_sid='S-1-5-21-1234',
    )

    assert result['ok'] is True
    assert result['granted'] == [str(install.resolve())]
    command = calls[0][0]
    assert command[:3] == ['icacls.exe', str(install.resolve()), '/grant:r']
    assert '*S-1-5-21-1234:(OI)(CI)M' in command
    assert 'S-1-5-32-545' not in ' '.join(command)
    assert calls[0][1]['timeout'] == ICACLS_TIMEOUT_SECONDS


def test_grant_rejects_non_install_and_studio_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = _player_install(tmp_path)
    (install / 'RobloxStudioBeta.exe').write_bytes(b'')
    monkeypatch.setattr(windows_permissions, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(roblox_dirs.sys, 'platform', 'win32')
    monkeypatch.setattr(roblox_dirs, 'ROBLOX_PROCESS', 'RobloxPlayerBeta.exe')
    monkeypatch.setattr(roblox_dirs, 'ROBLOX_STUDIO_PROCESS', 'RobloxStudioBeta.exe')

    result = windows_permissions.grant_current_user_modify_access([install])

    assert result['ok'] is False
    assert result['granted'] == []
    failed = result['failed']
    assert isinstance(failed, list)
    failed_items = cast('list[object]', failed)
    assert all(isinstance(item, dict) for item in failed_items)
    failures = cast('list[dict[object, object]]', failed_items)
    assert failures[0]['path'] == str(install)


def test_windows_user_id_is_derived_from_original_sid(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_security = ModuleType('win32security')
    monkeypatch.setattr(fake_security, 'SidTypeUser', SID_TYPE_USER, raising=False)
    monkeypatch.setattr(
        fake_security,
        'ConvertStringSidToSid',
        _convert_string_sid_to_sid,
        raising=False,
    )
    monkeypatch.setattr(
        fake_security,
        'ConvertSidToStringSid',
        _convert_sid_to_string_sid,
        raising=False,
    )
    monkeypatch.setattr(fake_security, 'LookupAccountSid', _lookup_account_sid, raising=False)
    monkeypatch.setitem(sys.modules, 'win32security', fake_security)

    assert (
        windows_permissions.windows_user_id_from_sid('S-1-5-21-1234')
        == r'DesktopDomain\OriginalUser'
    )
