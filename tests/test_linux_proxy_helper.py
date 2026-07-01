from pathlib import Path

from Fleasion.utils import linux_proxy_helper


def test_host_subprocess_env_restores_pyinstaller_original_library_path(monkeypatch, tmp_path):
    bundle_root = tmp_path / '_MEI12345'
    host_libs = tmp_path / 'host-libs'
    monkeypatch.setattr(linux_proxy_helper.sys, '_MEIPASS', str(bundle_root), raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', f'{bundle_root}:{host_libs}')
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', str(host_libs))

    env = linux_proxy_helper._host_subprocess_env()

    assert env['LD_LIBRARY_PATH'] == str(host_libs)
    assert 'LD_LIBRARY_PATH_ORIG' not in env


def test_host_subprocess_env_removes_bundle_path_without_original(monkeypatch, tmp_path):
    bundle_root = tmp_path / '_MEI12345'
    host_libs = tmp_path / 'host-libs'
    monkeypatch.setattr(linux_proxy_helper.sys, '_MEIPASS', str(bundle_root), raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', f'{bundle_root}:{host_libs}')
    monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)

    env = linux_proxy_helper._host_subprocess_env()

    assert env['LD_LIBRARY_PATH'] == str(host_libs)


def test_helper_command_uses_source_script_when_not_frozen(monkeypatch):
    monkeypatch.delattr(linux_proxy_helper.sys, '_MEIPASS', raising=False)
    monkeypatch.setattr(linux_proxy_helper.sys, 'frozen', False, raising=False)
    monkeypatch.setattr(linux_proxy_helper.sys, 'executable', '/usr/bin/python3')
    monkeypatch.setattr(linux_proxy_helper, '_is_trusted_installed_helper', lambda: False)

    command = linux_proxy_helper._helper_command()

    assert command == [
        '/usr/bin/python3',
        str(Path(linux_proxy_helper.__file__).resolve().parents[1] / 'linux_proxy_helper_daemon.py'),
    ]


def test_helper_command_prefers_bundled_executable_when_frozen(monkeypatch, tmp_path):
    helper = tmp_path / linux_proxy_helper.HELPER_BUNDLED_EXECUTABLE_NAME
    helper.write_text('helper', encoding='utf-8')
    monkeypatch.setattr(linux_proxy_helper.sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(linux_proxy_helper.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(linux_proxy_helper.sys, 'executable', '/opt/Fleasion/Fleasion')
    monkeypatch.setattr(linux_proxy_helper, '_is_trusted_installed_helper', lambda: False)

    assert linux_proxy_helper._helper_command() == [str(helper)]


def test_helper_command_self_dispatches_when_frozen_without_bundled_helper(monkeypatch, tmp_path):
    (tmp_path / 'linux_proxy_helper_daemon.py').write_text('helper source', encoding='utf-8')
    monkeypatch.setattr(linux_proxy_helper.sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(linux_proxy_helper.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(linux_proxy_helper.sys, 'executable', '/opt/Fleasion/Fleasion')
    monkeypatch.setattr(linux_proxy_helper, '_is_trusted_installed_helper', lambda: False)

    assert linux_proxy_helper._helper_command() == ['/opt/Fleasion/Fleasion', '--linux-proxy-helper']


def test_installable_helper_source_uses_bundled_binary_when_frozen(monkeypatch, tmp_path):
    helper = tmp_path / linux_proxy_helper.HELPER_BUNDLED_EXECUTABLE_NAME
    helper.write_text('helper', encoding='utf-8')
    (tmp_path / 'linux_proxy_helper_daemon.py').write_text('helper source', encoding='utf-8')
    monkeypatch.setattr(linux_proxy_helper.sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(linux_proxy_helper.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(linux_proxy_helper.sys, 'executable', '/opt/Fleasion/Fleasion')

    assert linux_proxy_helper._installable_helper_source() == (helper, False)


def test_installable_helper_source_uses_compiled_app_dispatch_without_bundled_helper(monkeypatch, tmp_path):
    (tmp_path / 'linux_proxy_helper_daemon.py').write_text('helper source', encoding='utf-8')
    monkeypatch.setattr(linux_proxy_helper.sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(linux_proxy_helper.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(linux_proxy_helper.sys, 'executable', '/opt/Fleasion/Fleasion')

    assert linux_proxy_helper._installable_helper_source() == (Path('/opt/Fleasion/Fleasion'), True)


def test_helper_command_prefers_installed_root_owned_helper(monkeypatch):
    monkeypatch.setattr(linux_proxy_helper, '_is_trusted_installed_helper', lambda: True)

    assert linux_proxy_helper._helper_command() == [str(linux_proxy_helper.INSTALLED_HELPER_PATH)]


def test_start_helper_requires_ca_cert_when_system_trust_is_required(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(linux_proxy_helper.shutil, 'which', lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None)
    monkeypatch.setattr(linux_proxy_helper, '_popen_host_command', lambda *args, **kwargs: calls.append(args))

    assert linux_proxy_helper.start_helper({'apis.roblox.com'}, require_system_ca=True, timeout=0.01) is False
    assert calls == []


def test_start_helper_passes_required_system_ca_flag(monkeypatch, tmp_path):
    commands = []
    ca = tmp_path / 'ca.crt'
    ca.write_text('ca', encoding='utf-8')

    class Process:
        def poll(self):
            return None

    def fake_popen(cmd, **_kwargs):
        commands.append(cmd)
        return Process()

    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(linux_proxy_helper.shutil, 'which', lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None)
    monkeypatch.setattr(linux_proxy_helper, '_helper_command', lambda: ['/opt/fleasion-helper'])
    monkeypatch.setattr(linux_proxy_helper, '_current_process_start_time', lambda: '12345')
    monkeypatch.setattr(linux_proxy_helper, 'ensure_privileged_helper_installed', lambda **_kwargs: True)
    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_is_current', lambda _path: True)
    monkeypatch.setattr(linux_proxy_helper, 'linux_system_ca_needs_install', lambda _path: False)
    monkeypatch.setattr(linux_proxy_helper, '_popen_host_command', fake_popen)
    monkeypatch.setattr(
        linux_proxy_helper,
        '_read_ready',
        lambda: {'ok': True, 'system_ca': {'ok': True}},
    )

    assert linux_proxy_helper.start_helper(
        {'apis.roblox.com'},
        ca_cert_path=ca,
        require_system_ca=True,
        timeout=1.0,
    ) is True

    assert '--ca-cert' in commands[0]
    assert str(ca) in commands[0]
    assert '--hosts-file' in commands[0]
    assert str(tmp_path / 'hosts.json') in commands[0]
    assert '--parent-start-time' in commands[0]
    assert '12345' in commands[0]
    assert '--require-system-ca' in commands[0]


def test_start_helper_installs_persistent_helper_before_launch(monkeypatch, tmp_path):
    commands = []
    installed = {'ok': False}

    class Process:
        def poll(self):
            return None

    def fake_install(**kwargs):
        installed['ok'] = True
        installed['kwargs'] = kwargs
        return {'ok': True, 'helper': str(linux_proxy_helper.INSTALLED_HELPER_PATH), 'promptless_rule': None}

    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_READY_FILE', tmp_path / 'ready.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_STOP_FILE', tmp_path / 'stop')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', tmp_path / 'hosts.json')
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_LOG_FILE', tmp_path / 'helper.log')
    monkeypatch.setattr(linux_proxy_helper.shutil, 'which', lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None)
    monkeypatch.setattr(linux_proxy_helper, '_is_trusted_installed_helper', lambda: installed['ok'])
    monkeypatch.setattr(linux_proxy_helper, '_installed_policy_is_current', lambda: installed['ok'])
    monkeypatch.setattr(linux_proxy_helper, 'install_privileged_helper', fake_install)
    monkeypatch.setattr(linux_proxy_helper, '_current_process_start_time', lambda: '12345')
    monkeypatch.setattr(linux_proxy_helper, '_popen_host_command', lambda cmd, **_kwargs: commands.append(cmd) or Process())
    monkeypatch.setattr(linux_proxy_helper, '_read_ready', lambda: {'ok': True})

    assert linux_proxy_helper.start_helper({'gamejoin.roblox.com'}, timeout=1.0) is True

    assert installed['kwargs'] == {'enable_promptless': True}
    assert commands[0][1] == str(linux_proxy_helper.INSTALLED_HELPER_PATH)


def test_update_helper_hosts_writes_atomic_hosts_request(monkeypatch, tmp_path):
    hosts_file = tmp_path / 'hosts.json'
    monkeypatch.setattr(linux_proxy_helper, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(linux_proxy_helper, 'HELPER_HOSTS_FILE', hosts_file)

    assert linux_proxy_helper.update_helper_hosts({'gamejoin.roblox.com', 'apis.roblox.com'}) is True

    assert hosts_file.read_text(encoding='utf-8') == '{"hosts":["apis.roblox.com","gamejoin.roblox.com"]}'


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

    assert result == {'db': str(tmp_path / 'nssdb'), 'ok': True}
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


def test_linux_system_ca_needs_install_false_when_current(monkeypatch, tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_bytes(b'current')
    ca_dir = tmp_path / 'system-ca'
    ca_dir.mkdir()
    (ca_dir / linux_proxy_helper.SYSTEM_CA_NAME).write_bytes(b'current')
    monkeypatch.setattr(linux_proxy_helper, 'SYSTEM_CA_DIRS', (ca_dir,))

    assert linux_proxy_helper.linux_system_ca_needs_install(ca) is False


def test_linux_system_ca_needs_install_true_when_stale(monkeypatch, tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_bytes(b'current')
    ca_dir = tmp_path / 'system-ca'
    ca_dir.mkdir()
    (ca_dir / linux_proxy_helper.SYSTEM_CA_NAME).write_bytes(b'old')
    monkeypatch.setattr(linux_proxy_helper, 'SYSTEM_CA_DIRS', (ca_dir,))

    assert linux_proxy_helper.linux_system_ca_needs_install(ca) is True
