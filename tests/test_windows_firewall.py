from types import SimpleNamespace

from fleasion.utils import windows_firewall


def test_firewall_repair_state_round_trips(tmp_path):
    windows_firewall.write_pending_repair(tmp_path)
    assert windows_firewall.read_pending_repair(tmp_path)

    result = {'ok': True, 'rules': ['in', 'out'], 'failed': []}
    windows_firewall.write_repair_result(result, tmp_path)
    assert windows_firewall.read_repair_result(tmp_path) == result

    windows_firewall.clear_pending_repair(tmp_path)
    windows_firewall.clear_repair_result(tmp_path)
    assert not windows_firewall.read_pending_repair(tmp_path)
    assert windows_firewall.read_repair_result(tmp_path) is None


def test_install_firewall_rules_targets_both_directions_and_profiles(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(windows_firewall.sys, 'platform', 'win32')
    monkeypatch.setattr(windows_firewall, '_is_admin', lambda: True)
    monkeypatch.setattr(windows_firewall.sys, 'executable', str(tmp_path / 'Fleasion.exe'))
    monkeypatch.setattr(
        windows_firewall.subprocess,
        'run',
        lambda command, **_kwargs: commands.append(command)
        or SimpleNamespace(returncode=0, stdout='', stderr=''),
    )

    result = windows_firewall.install_fleasion_firewall_rules()

    assert result['ok']
    assert [command[7] for command in commands] == ['dir=in', 'dir=out']
    assert all('profile=private,public' in command for command in commands)
    assert all('action=allow' in command for command in commands)
    assert all(command[6].endswith('Fleasion.exe') for command in commands)


def test_install_firewall_rules_reports_netsh_failure(monkeypatch):
    monkeypatch.setattr(windows_firewall.sys, 'platform', 'win32')
    monkeypatch.setattr(windows_firewall, '_is_admin', lambda: True)
    monkeypatch.setattr(
        windows_firewall.subprocess,
        'run',
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout='', stderr='Access is denied.'
        ),
    )

    result = windows_firewall.install_fleasion_firewall_rules('C:/Fleasion.exe')

    assert not result['ok']
    assert len(result['failed']) == 2
    assert all(item['error'] == 'Access is denied.' for item in result['failed'])
