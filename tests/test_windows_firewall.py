from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from fleasion.utils import windows_firewall


def _is_admin() -> bool:
    return True


def _status_result(output: str):
    def run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=output, stderr='')

    return run


def _failure_result(*_args: object, **_kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(returncode=1, stdout='', stderr='Access is denied.')


def test_firewall_repair_state_round_trips(tmp_path: Path) -> None:
    windows_firewall.write_pending_repair(tmp_path)
    assert windows_firewall.read_pending_repair(tmp_path)

    result = {'ok': True, 'rules': ['in', 'out'], 'failed': []}
    windows_firewall.write_repair_result(result, tmp_path)
    assert windows_firewall.read_repair_result(tmp_path) == result

    windows_firewall.clear_pending_repair(tmp_path)
    windows_firewall.clear_repair_result(tmp_path)
    assert not windows_firewall.read_pending_repair(tmp_path)
    assert windows_firewall.read_repair_result(tmp_path) is None


def test_install_firewall_rules_targets_both_directions_and_profiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(windows_firewall.sys, 'platform', 'win32')
    monkeypatch.setattr(windows_firewall, '_is_admin', _is_admin)
    monkeypatch.setattr(windows_firewall.sys, 'executable', str(tmp_path / 'Fleasion.exe'))

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(windows_firewall.subprocess, 'run', run)

    result = windows_firewall.install_fleasion_firewall_rules()

    assert result['ok']
    assert [command[7] for command in commands] == ['dir=in', 'dir=out']
    assert all('profile=private,public' in command for command in commands)
    assert all('action=allow' in command for command in commands)
    assert all(command[6].endswith('Fleasion.exe') for command in commands)


def test_firewall_rule_status_requires_both_rules_for_the_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(windows_firewall.sys, 'platform', 'win32')
    executable = tmp_path / 'Fleasion.exe'
    output = (
        'Rule Name: Fleasion - Allow inbound (Private,Public)\n'
        f'Program: {executable}\n'
        'Rule Name: Fleasion - Allow outbound (Private,Public)\n'
        f'Program: {executable}\n'
    )
    monkeypatch.setattr(
        windows_firewall.subprocess,
        'run',
        _status_result(output),
    )

    result = windows_firewall.get_fleasion_firewall_rule_status(executable)

    assert result['ok']
    rules = cast('list[str]', result['rules'])
    missing = cast('list[str]', result['missing'])
    assert len(rules) == 2
    assert missing == []


def test_install_firewall_rules_reports_netsh_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows_firewall.sys, 'platform', 'win32')
    monkeypatch.setattr(windows_firewall, '_is_admin', _is_admin)
    monkeypatch.setattr(
        windows_firewall.subprocess,
        'run',
        _failure_result,
    )

    result = windows_firewall.install_fleasion_firewall_rules('C:/Fleasion.exe')

    assert not result['ok']
    failed = cast('list[dict[str, str]]', result['failed'])
    assert len(failed) == 2
    assert all(item['error'] == 'Access is denied.' for item in failed)
