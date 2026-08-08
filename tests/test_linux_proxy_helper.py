import sys

import pytest

from fleasion.utils import linux_proxy_helper


pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='Linux-only proxy helper tests')


def test_start_helper_requires_ca_cert_when_system_trust_is_required(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None,
    )
    monkeypatch.setattr(
        linux_proxy_helper, '_popen_host_command', lambda *args, **kwargs: calls.append(args)
    )

    assert (
        linux_proxy_helper.start_helper({'apis.roblox.com'}, require_system_ca=True, timeout=0.01)
        is False
    )
    assert calls == []


def test_start_helper_passes_required_system_ca_flag(monkeypatch, tmp_path):
    commands = []
    popen_kwargs = []
    ca = tmp_path / 'ca.crt'
    ca.write_text('ca', encoding='utf-8')

    class Process:
        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        commands.append(cmd)
        popen_kwargs.append(kwargs)
        return Process()

    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None,
    )
    monkeypatch.setattr(linux_proxy_helper, '_helper_command', lambda: ['/opt/fleasion-helper'])
    monkeypatch.setattr(linux_proxy_helper, '_current_process_start_time', lambda: '12345')
    monkeypatch.setattr(
        linux_proxy_helper, 'ensure_privileged_helper_installed', lambda **_kwargs: True
    )
    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_store_supported', lambda: True)
    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_is_current', lambda _path: True)
    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_needs_install', lambda _path: False)
    monkeypatch.setattr(
        linux_proxy_helper,
        '_install_ca_into_linux_system_store',
        lambda _path: {'ok': True, 'stores': ['system-ca:already-current']},
    )
    monkeypatch.setattr(linux_proxy_helper, '_popen_host_command', fake_popen)
    monkeypatch.setattr(
        linux_proxy_helper,
        '_read_ready',
        lambda: {'ok': True, 'system_ca': {'ok': True}},
    )

    assert (
        linux_proxy_helper.start_helper(
            {'apis.roblox.com'},
            ca_cert_path=ca,
            require_system_ca=True,
            timeout=1.0,
        )
        is True
    )

    assert '--ca-cert' in commands[0]
    assert str(ca) in commands[0]
    assert '--hosts-file' in commands[0]
    assert str(tmp_path / 'hosts.json') in commands[0]
    assert '--parent-start-time' in commands[0]
    assert '12345' in commands[0]
    assert '--require-system-ca' in commands[0]
    assert len(popen_kwargs) == 1
    assert set(popen_kwargs[0]) == {'stdout', 'stderr'}
    assert 'start_new_session' not in popen_kwargs[0]


def test_start_helper_does_not_reinstall_system_ca_when_current(monkeypatch, tmp_path):
    commands = []
    install_calls = []
    ca = tmp_path / 'ca.crt'
    ca.write_text('ca', encoding='utf-8')

    class Process:
        def poll(self):
            return None

    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None,
    )
    monkeypatch.setattr(linux_proxy_helper, '_helper_command', lambda: ['/opt/fleasion-helper'])
    monkeypatch.setattr(linux_proxy_helper, '_current_process_start_time', lambda: '12345')
    monkeypatch.setattr(
        linux_proxy_helper, 'ensure_privileged_helper_installed', lambda **_kwargs: True
    )
    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_store_supported', lambda: True)
    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_is_current', lambda _path: True)
    monkeypatch.setattr(
        linux_proxy_helper,
        '_install_ca_into_linux_system_store',
        lambda _path: install_calls.append(_path) or {'ok': True},
    )
    monkeypatch.setattr(
        linux_proxy_helper,
        '_popen_host_command',
        lambda cmd, **_kwargs: commands.append(cmd) or Process(),
    )
    monkeypatch.setattr(
        linux_proxy_helper, '_read_ready', lambda: {'ok': True, 'system_ca': {'ok': True}}
    )

    assert (
        linux_proxy_helper.start_helper(
            {'apis.roblox.com'},
            ca_cert_path=ca,
            require_system_ca=True,
            timeout=1.0,
        )
        is True
    )

    assert install_calls == []
    assert commands


def test_start_helper_combines_helper_update_with_missing_system_ca(monkeypatch, tmp_path):
    commands = []
    installed = {'ok': False}
    ca = tmp_path / 'ca.crt'
    ca.write_text('ca', encoding='utf-8')

    class Process:
        def poll(self):
            return None

    def fake_install(**kwargs):
        installed['ok'] = True
        installed['kwargs'] = kwargs
        return {
            'ok': True,
            'helper': str(linux_proxy_helper.INSTALLED_HELPER_PATH),
            'promptless_rule': None,
            'system_ca': {'ok': True, 'stores': ['update-ca-certificates']},
        }

    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None,
    )
    monkeypatch.setattr(linux_proxy_helper, '_is_trusted_installed_helper', lambda: installed['ok'])
    monkeypatch.setattr(linux_proxy_helper, '_installed_policy_is_current', lambda: installed['ok'])
    monkeypatch.setattr(
        linux_proxy_helper, '_installed_helper_metadata_is_current', lambda: installed['ok']
    )
    monkeypatch.setattr(
        linux_proxy_helper, '_persistent_helper_install_path_is_read_only', lambda: False
    )
    monkeypatch.setattr(linux_proxy_helper, 'install_privileged_helper', fake_install)
    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_store_supported', lambda: True)
    monkeypatch.setattr(
        linux_proxy_helper,
        '_install_ca_into_linux_system_store',
        lambda _path: (_ for _ in ()).throw(
            AssertionError('separate system CA prompt should not run')
        ),
    )
    monkeypatch.setattr(linux_proxy_helper, '_current_process_start_time', lambda: '12345')
    monkeypatch.setattr(
        linux_proxy_helper,
        '_popen_host_command',
        lambda cmd, **_kwargs: commands.append(cmd) or Process(),
    )
    monkeypatch.setattr(
        linux_proxy_helper, '_read_ready', lambda: {'ok': True, 'system_ca': {'ok': True}}
    )

    def current_after_helper(_path):
        if installed['ok']:
            return True
        return False

    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_is_current', current_after_helper)

    assert (
        linux_proxy_helper.start_helper(
            {'apis.roblox.com'},
            ca_cert_path=ca,
            require_system_ca=True,
            timeout=1.0,
        )
        is True
    )

    assert installed['kwargs'] == {'enable_promptless': True, 'ca_cert_path': ca}
    assert commands[0][1] == str(linux_proxy_helper.INSTALLED_HELPER_PATH)


def test_start_helper_does_not_require_system_ca_when_store_is_unsupported(monkeypatch, tmp_path):
    commands = []
    ca = tmp_path / 'ca.crt'
    ca.write_text('ca', encoding='utf-8')

    class Process:
        def poll(self):
            return None

    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None,
    )
    monkeypatch.setattr(linux_proxy_helper, '_helper_command', lambda: ['/opt/fleasion-helper'])
    monkeypatch.setattr(linux_proxy_helper, '_current_process_start_time', lambda: '12345')
    monkeypatch.setattr(
        linux_proxy_helper, 'ensure_privileged_helper_installed', lambda **_kwargs: True
    )
    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_store_supported', lambda: False)
    monkeypatch.setattr(
        linux_proxy_helper,
        '_popen_host_command',
        lambda cmd, **_kwargs: commands.append(cmd) or Process(),
    )
    monkeypatch.setattr(
        linux_proxy_helper,
        '_read_ready',
        lambda: {
            'ok': True,
            'system_ca': {'ok': False, 'error': 'no_supported_system_trust_store'},
        },
    )

    assert (
        linux_proxy_helper.start_helper(
            {'apis.roblox.com'},
            ca_cert_path=ca,
            require_system_ca=True,
            timeout=1.0,
        )
        is True
    )

    assert '--ca-cert' in commands[0]
    assert str(ca) in commands[0]
    assert '--require-system-ca' not in commands[0]


def test_start_helper_continues_when_privileged_system_ca_install_is_unsupported(
    monkeypatch, tmp_path
):
    commands = []
    ca = tmp_path / 'ca.crt'
    ca.write_text('ca', encoding='utf-8')

    class Process:
        def poll(self):
            return None

    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: '/usr/bin/pkexec' if name == 'pkexec' else f'/usr/sbin/{name}',
    )
    monkeypatch.setattr(linux_proxy_helper, '_helper_command', lambda: ['/opt/fleasion-helper'])
    monkeypatch.setattr(linux_proxy_helper, '_current_process_start_time', lambda: '12345')
    monkeypatch.setattr(
        linux_proxy_helper, 'ensure_privileged_helper_installed', lambda **_kwargs: True
    )
    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_store_supported', lambda: True)
    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_is_current', lambda _path: False)
    monkeypatch.setattr(
        linux_proxy_helper,
        '_install_ca_into_linux_system_store',
        lambda _path: {'ok': False, 'error': 'no_supported_system_trust_store'},
    )
    monkeypatch.setattr(
        linux_proxy_helper,
        '_popen_host_command',
        lambda cmd, **_kwargs: commands.append(cmd) or Process(),
    )
    monkeypatch.setattr(
        linux_proxy_helper,
        '_read_ready',
        lambda: {
            'ok': True,
            'system_ca': {'ok': False, 'error': 'no_supported_system_trust_store'},
        },
    )

    assert (
        linux_proxy_helper.start_helper(
            {'apis.roblox.com'},
            ca_cert_path=ca,
            require_system_ca=True,
            timeout=1.0,
        )
        is True
    )

    assert '--ca-cert' in commands[0]
    assert str(ca) in commands[0]
    assert '--require-system-ca' not in commands[0]


def test_start_helper_installs_persistent_helper_before_launch(monkeypatch, tmp_path):
    commands = []
    installed = {'ok': False}

    class Process:
        def poll(self):
            return None

    def fake_install(**kwargs):
        installed['ok'] = True
        installed['kwargs'] = kwargs
        return {
            'ok': True,
            'helper': str(linux_proxy_helper.INSTALLED_HELPER_PATH),
            'promptless_rule': None,
        }

    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None,
    )
    monkeypatch.setattr(linux_proxy_helper, '_is_trusted_installed_helper', lambda: installed['ok'])
    monkeypatch.setattr(linux_proxy_helper, '_installed_policy_is_current', lambda: installed['ok'])
    monkeypatch.setattr(
        linux_proxy_helper, '_installed_helper_metadata_is_current', lambda: installed['ok']
    )
    monkeypatch.setattr(
        linux_proxy_helper, '_persistent_helper_install_path_is_read_only', lambda: False
    )
    monkeypatch.setattr(linux_proxy_helper, 'install_privileged_helper', fake_install)
    monkeypatch.setattr(linux_proxy_helper, '_current_process_start_time', lambda: '12345')
    monkeypatch.setattr(
        linux_proxy_helper,
        '_popen_host_command',
        lambda cmd, **_kwargs: commands.append(cmd) or Process(),
    )
    monkeypatch.setattr(linux_proxy_helper, '_read_ready', lambda: {'ok': True})

    assert linux_proxy_helper.start_helper({'gamejoin.roblox.com'}, timeout=1.0) is True

    assert installed['kwargs'] == {'enable_promptless': True}
    assert commands[0][1] == str(linux_proxy_helper.INSTALLED_HELPER_PATH)


def test_start_helper_uses_source_helper_when_persistent_install_path_is_read_only(
    monkeypatch, tmp_path
):
    commands = []

    class Process:
        def poll(self):
            return None

    monkeypatch.setattr(linux_proxy_helper, '_force_source_helper_for_session', False)
    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None,
    )
    monkeypatch.setattr(linux_proxy_helper, '_is_trusted_installed_helper', lambda: False)
    monkeypatch.setattr(linux_proxy_helper, '_installed_policy_is_current', lambda: False)
    monkeypatch.setattr(linux_proxy_helper, '_installed_helper_metadata_is_current', lambda: False)
    monkeypatch.setattr(
        linux_proxy_helper, '_persistent_helper_install_path_is_read_only', lambda: False
    )
    monkeypatch.setattr(
        linux_proxy_helper,
        'install_privileged_helper',
        lambda **_kwargs: {
            'ok': False,
            'error': "[Errno 30] Read-only file system: '/usr/local/libexec/fleasion-linux-proxy-helper'",
        },
    )
    monkeypatch.setattr(
        linux_proxy_helper, '_source_helper_command', lambda: ['/current/fleasion-helper']
    )
    monkeypatch.setattr(linux_proxy_helper, '_current_process_start_time', lambda: '12345')
    monkeypatch.setattr(
        linux_proxy_helper,
        '_popen_host_command',
        lambda cmd, **_kwargs: commands.append(cmd) or Process(),
    )
    monkeypatch.setattr(linux_proxy_helper, '_read_ready', lambda: {'ok': True})

    assert linux_proxy_helper.start_helper({'gamejoin.roblox.com'}, timeout=1.0) is True

    assert commands[0][:2] == ['/usr/bin/pkexec', '/current/fleasion-helper']


def test_start_helper_skips_persistent_install_prompt_when_install_path_mount_is_read_only(
    monkeypatch, tmp_path
):
    commands = []
    install_calls = []

    class Process:
        def poll(self):
            return None

    monkeypatch.setattr(linux_proxy_helper, '_force_source_helper_for_session', False)
    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None,
    )
    monkeypatch.setattr(linux_proxy_helper, '_is_trusted_installed_helper', lambda: False)
    monkeypatch.setattr(linux_proxy_helper, '_installed_policy_is_current', lambda: False)
    monkeypatch.setattr(linux_proxy_helper, '_installed_helper_metadata_is_current', lambda: False)
    monkeypatch.setattr(
        linux_proxy_helper, '_persistent_helper_install_path_is_read_only', lambda: True
    )
    monkeypatch.setattr(
        linux_proxy_helper,
        'install_privileged_helper',
        lambda **kwargs: install_calls.append(kwargs) or {'ok': True},
    )
    monkeypatch.setattr(
        linux_proxy_helper, '_source_helper_command', lambda: ['/current/fleasion-helper']
    )
    monkeypatch.setattr(linux_proxy_helper, '_current_process_start_time', lambda: '12345')
    monkeypatch.setattr(
        linux_proxy_helper,
        '_popen_host_command',
        lambda cmd, **_kwargs: commands.append(cmd) or Process(),
    )
    monkeypatch.setattr(linux_proxy_helper, '_read_ready', lambda: {'ok': True})

    assert linux_proxy_helper.start_helper({'gamejoin.roblox.com'}, timeout=1.0) is True

    assert install_calls == []
    assert commands[0][:2] == ['/usr/bin/pkexec', '/current/fleasion-helper']


def test_start_helper_records_read_only_hosts_ready_failure(monkeypatch, tmp_path):
    class Process:
        def poll(self):
            return None

    ready = {
        'ok': False,
        'code': 'linux_hosts_read_only',
        'error': "[Errno 30] Read-only file system: '/etc/hosts'",
        'hosts': ['assetdelivery.roblox.com', 'gamejoin.roblox.com'],
    }

    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None,
    )
    monkeypatch.setattr(
        linux_proxy_helper, 'ensure_privileged_helper_installed', lambda **_kwargs: True
    )
    monkeypatch.setattr(linux_proxy_helper, '_current_process_start_time', lambda: '12345')
    monkeypatch.setattr(
        linux_proxy_helper, '_popen_host_command', lambda *_args, **_kwargs: Process()
    )
    monkeypatch.setattr(linux_proxy_helper, '_read_ready', lambda: ready)

    assert linux_proxy_helper.start_helper({'gamejoin.roblox.com'}, timeout=1.0) is False
    assert linux_proxy_helper.last_start_error_details() == ready


def test_update_helper_hosts_writes_atomic_hosts_request(monkeypatch, tmp_path):
    hosts_file = tmp_path / 'hosts.json'
    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', hosts_file)

    assert (
        linux_proxy_helper.update_helper_hosts({'gamejoin.roblox.com', 'apis.roblox.com'}) is True
    )

    assert (
        hosts_file.read_text(encoding='utf-8')
        == '{"hosts":["apis.roblox.com","gamejoin.roblox.com"]}'
    )


def test_cleanup_hosts_with_pkexec_runs_one_shot_root_child(monkeypatch):
    commands = []

    monkeypatch.setattr(linux_proxy_helper.shutil, 'which', lambda _name: '/usr/bin/pkexec')
    monkeypatch.setattr(linux_proxy_helper, '_helper_command', lambda: ['/opt/fleasion-helper'])

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    monkeypatch.setattr(
        linux_proxy_helper,
        '_run_host_command',
        lambda command, **_kwargs: commands.append(command) or Result(),
    )

    assert linux_proxy_helper.cleanup_hosts_with_pkexec()
    assert commands == [['/usr/bin/pkexec', '/opt/fleasion-helper', '--cleanup-hosts']]


def test_existing_nss_dbs_finds_shared_and_firefox_profiles(tmp_path):
    home = tmp_path / 'home'
    shared = home / '.pki' / 'nssdb'
    shared.mkdir(parents=True)
    firefox = home / '.mozilla' / 'firefox' / 'abc.default-release'
    firefox.mkdir(parents=True)
    (firefox / 'cert9.db').write_bytes(b'')
    empty_profile = home / '.mozilla' / 'firefox' / 'empty.default'
    empty_profile.mkdir(parents=True)

    assert set(linux_proxy_helper._existing_nss_dbs(home)) == {shared, firefox}


def test_install_ca_into_nss_db_replaces_then_adds(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

        class Result:
            returncode = 0
            stdout = ''
            stderr = ''

        return Result()

    monkeypatch.setattr(linux_proxy_helper.subprocess, 'run', fake_run)

    result = linux_proxy_helper._install_ca_into_nss_db(
        '/usr/bin/certutil',
        tmp_path / 'nssdb',
        tmp_path / 'ca.crt',
    )

    assert result == {'db': str(tmp_path / 'nssdb'), 'ok': True, 'status': 'installed'}
    assert calls[0][0][:5] == [
        '/usr/bin/certutil',
        '-D',
        '-d',
        f'sql:{tmp_path / "nssdb"}',
        '-n',
    ]
    assert calls[1][0] == [
        '/usr/bin/certutil',
        '-A',
        '-d',
        f'sql:{tmp_path / "nssdb"}',
        '-n',
        linux_proxy_helper.NSS_CERT_NICKNAME,
        '-t',
        'C,,',
        '-i',
        str(tmp_path / 'ca.crt'),
    ]


def test_install_ca_into_nss_db_skips_when_already_current(monkeypatch, tmp_path):
    calls = []
    ca = tmp_path / 'ca.crt'
    ca.write_text(
        '-----BEGIN CERTIFICATE-----\ncurrent\n-----END CERTIFICATE-----\n',
        encoding='utf-8',
    )

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

        class Result:
            returncode = 0
            stdout = '-----BEGIN CERTIFICATE-----\ncurrent\n-----END CERTIFICATE-----\n'
            stderr = ''

        return Result()

    monkeypatch.setattr(linux_proxy_helper.subprocess, 'run', fake_run)

    result = linux_proxy_helper._install_ca_into_nss_db(
        '/usr/bin/certutil',
        tmp_path / 'nssdb',
        ca,
    )

    assert result == {'db': str(tmp_path / 'nssdb'), 'ok': True, 'status': 'already_current'}
    assert len(calls) == 1
    assert calls[0][0][:5] == [
        '/usr/bin/certutil',
        '-L',
        '-d',
        f'sql:{tmp_path / "nssdb"}',
        '-n',
    ]


def test_linux_system_ca_needs_install_false_when_current(monkeypatch, tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_bytes(b'current')
    ca_dir = tmp_path / 'system-ca'
    ca_dir.mkdir()
    (ca_dir / linux_proxy_helper.SYSTEM_CA_NAME).write_bytes(b'current')
    monkeypatch.setattr(linux_proxy_helper, 'SYSTEM_CA_DIRS', (ca_dir,))
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: f'/usr/sbin/{name}' if name == 'update-ca-certificates' else None,
    )

    assert linux_proxy_helper.linux_system_ca_needs_install(ca) is False


def test_linux_system_ca_needs_install_true_when_stale(monkeypatch, tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_bytes(b'current')
    ca_dir = tmp_path / 'system-ca'
    ca_dir.mkdir()
    (ca_dir / linux_proxy_helper.SYSTEM_CA_NAME).write_bytes(b'old')
    monkeypatch.setattr(linux_proxy_helper, 'SYSTEM_CA_DIRS', (ca_dir,))
    monkeypatch.setattr(
        linux_proxy_helper.shutil,
        'which',
        lambda name: f'/usr/sbin/{name}' if name == 'update-ca-certificates' else None,
    )

    assert linux_proxy_helper.linux_system_ca_needs_install(ca) is True


def test_linux_system_ca_needs_install_false_when_store_unsupported(monkeypatch, tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_bytes(b'current')
    ca_dir = tmp_path / 'system-ca'
    ca_dir.mkdir()
    (ca_dir / linux_proxy_helper.SYSTEM_CA_NAME).write_bytes(b'old')
    monkeypatch.setattr(linux_proxy_helper, 'SYSTEM_CA_DIRS', (ca_dir,))
    monkeypatch.setattr(linux_proxy_helper.shutil, 'which', lambda _name: None)

    assert linux_proxy_helper.linux_system_ca_needs_install(ca) is False
