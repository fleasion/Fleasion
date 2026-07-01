import asyncio
import subprocess
from types import SimpleNamespace

from Fleasion import linux_proxy_helper_daemon as daemon


def test_host_subprocess_env_restores_pyinstaller_original_library_path(monkeypatch, tmp_path):
    bundle_root = tmp_path / '_MEI12345'
    host_libs = tmp_path / 'host-libs'
    monkeypatch.setattr(daemon.sys, '_MEIPASS', str(bundle_root), raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', f'{bundle_root}:{host_libs}')
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', str(host_libs))

    env = daemon._host_subprocess_env()

    assert env['LD_LIBRARY_PATH'] == str(host_libs)
    assert 'LD_LIBRARY_PATH_ORIG' not in env


def test_host_subprocess_env_removes_bundle_path_without_original(monkeypatch, tmp_path):
    bundle_root = tmp_path / '_MEI12345'
    host_libs = tmp_path / 'host-libs'
    monkeypatch.setattr(daemon.sys, '_MEIPASS', str(bundle_root), raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', f'{bundle_root}:{host_libs}')
    monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)

    env = daemon._host_subprocess_env()

    assert env['LD_LIBRARY_PATH'] == str(host_libs)


def test_boot_guard_command_removes_only_fleasion_hosts_lines(tmp_path, monkeypatch):
    hosts = tmp_path / 'hosts'
    unit = tmp_path / 'fleasion-hosts-restore.service'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake_systemctl = fake_bin / 'systemctl'
    fake_systemctl.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    fake_systemctl.chmod(0o755)
    hosts.write_text(
        '127.0.0.1 localhost\n'
        f'127.0.0.1 assetdelivery.roblox.com {daemon.HOSTS_MARKER}\n'
        '203.0.113.10 example.test\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(daemon, 'HOSTS_FILE', hosts)
    monkeypatch.setattr(daemon, 'BOOT_GUARD_PATH', unit)
    monkeypatch.setenv('PATH', f'{fake_bin}:{daemon.os.environ.get("PATH", "")}')

    subprocess.run(
        ['/bin/sh', '-c', daemon._boot_guard_command()],
        check=True,
        timeout=10,
    )

    assert hosts.read_text(encoding='utf-8') == (
        '127.0.0.1 localhost\n'
        '203.0.113.10 example.test\n'
    )
    assert not unit.exists()


def test_install_boot_guard_writes_and_enables_systemd_unit(tmp_path, monkeypatch):
    unit = tmp_path / 'systemd' / 'fleasion-hosts-restore.service'
    unit.parent.mkdir()
    calls = []

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_run(args, **_kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(daemon, 'BOOT_GUARD_PATH', unit)
    monkeypatch.setattr(daemon.shutil, 'which', lambda name: '/usr/bin/systemctl' if name == 'systemctl' else None)
    monkeypatch.setattr(daemon.subprocess, 'run', fake_run)

    assert daemon._install_boot_guard()

    unit_text = unit.read_text(encoding='utf-8')
    assert 'Restore /etc/hosts after an unclean Fleasion proxy shutdown' in unit_text
    assert daemon.HOSTS_MARKER in unit_text
    assert calls == [
        ['/usr/bin/systemctl', 'daemon-reload'],
        ['/usr/bin/systemctl', 'enable', daemon.BOOT_GUARD_SERVICE],
    ]


def test_remove_boot_guard_disables_deletes_and_reloads(tmp_path, monkeypatch):
    unit = tmp_path / 'systemd' / 'fleasion-hosts-restore.service'
    unit.parent.mkdir()
    unit.write_text('unit', encoding='utf-8')
    calls = []

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_run(args, **_kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(daemon, 'BOOT_GUARD_PATH', unit)
    monkeypatch.setattr(daemon.shutil, 'which', lambda name: '/usr/bin/systemctl' if name == 'systemctl' else None)
    monkeypatch.setattr(daemon.subprocess, 'run', fake_run)

    assert daemon._remove_boot_guard()

    assert not unit.exists()
    assert calls == [
        ['/usr/bin/systemctl', 'disable', daemon.BOOT_GUARD_SERVICE],
        ['/usr/bin/systemctl', 'daemon-reload'],
    ]


def test_install_system_ca_skips_update_when_target_is_current(tmp_path, monkeypatch):
    ca = tmp_path / 'ca.crt'
    ca.write_bytes(b'current')
    ca_dir = tmp_path / 'ca-certificates'
    rpm_dir = tmp_path / 'anchors'
    ca_dir.mkdir()
    rpm_dir.mkdir()
    (ca_dir / daemon.SYSTEM_CA_NAME).write_bytes(b'current')
    calls = []

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_which(name):
        if name == 'update-ca-certificates':
            return '/usr/sbin/update-ca-certificates'
        return None

    def fake_run(args, **_kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(daemon, 'SYSTEM_CA_DIRS', (ca_dir, rpm_dir))
    monkeypatch.setattr(daemon.shutil, 'which', fake_which)
    monkeypatch.setattr(daemon.subprocess, 'run', fake_run)

    assert daemon._install_system_ca(ca) == {
        'ok': True,
        'stores': ['update-ca-certificates:already-current'],
        'failures': [],
    }
    assert calls == []


def test_read_hosts_update_rejects_non_allowlisted_hosts(tmp_path):
    hosts_file = tmp_path / 'hosts.json'
    hosts_file.write_text('{"hosts":["assetdelivery.roblox.com","example.com"]}', encoding='utf-8')

    try:
        daemon._read_hosts_update(hosts_file)
    except RuntimeError as exc:
        assert 'unsupported hosts requested: example.com' in str(exc)
    else:
        raise AssertionError('expected invalid hosts update failure')


def test_read_hosts_update_accepts_allowlisted_hosts(tmp_path):
    hosts_file = tmp_path / 'hosts.json'
    hosts_file.write_text('{"hosts":["apis.roblox.com","gamejoin.roblox.com"]}', encoding='utf-8')

    assert daemon._read_hosts_update(hosts_file) == {'apis.roblox.com', 'gamejoin.roblox.com'}


def test_parent_alive_rejects_linux_zombie_parent(monkeypatch):
    monkeypatch.setattr(daemon.sys, 'platform', 'linux')
    monkeypatch.setattr(daemon, '_linux_process_state_and_start_time', lambda _pid: ('Z', '12345'))

    assert daemon._parent_alive(1234, '12345') is False


def test_parent_alive_rejects_reused_linux_pid(monkeypatch):
    monkeypatch.setattr(daemon.sys, 'platform', 'linux')
    monkeypatch.setattr(daemon, '_linux_process_state_and_start_time', lambda _pid: ('S', '99999'))

    assert daemon._parent_alive(1234, '12345') is False


def test_parent_alive_accepts_matching_linux_parent(monkeypatch):
    monkeypatch.setattr(daemon.sys, 'platform', 'linux')
    monkeypatch.setattr(daemon, '_linux_process_state_and_start_time', lambda _pid: ('S', '12345'))

    assert daemon._parent_alive(1234, '12345') is True


def test_serve_requires_system_ca_before_applying_hosts(tmp_path, monkeypatch):
    hosts_calls = []
    home = tmp_path / 'home'
    config_dir = home / '.config' / 'Fleasion'
    ca = config_dir / 'proxy_ca' / 'ca.crt'
    ca.parent.mkdir(parents=True)
    ca.write_text('ca', encoding='utf-8')

    monkeypatch.setattr(daemon, '_repair_config_ownership', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(daemon, '_repair_sober_cert_ownership', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(daemon, '_system_ca_is_current', lambda _path: False)
    monkeypatch.setattr(daemon, '_apply_hosts', lambda hosts: hosts_calls.append(hosts))
    monkeypatch.setattr(daemon.pwd, 'getpwuid', lambda _uid: SimpleNamespace(pw_dir=str(home), pw_gid=1000))

    args = SimpleNamespace(
        hosts='apis.roblox.com',
        stop_file=str(config_dir / daemon.HELPER_STOP_NAME),
        ready_file=str(config_dir / daemon.HELPER_READY_NAME),
        hosts_file=str(config_dir / daemon.HELPER_HOSTS_NAME),
        config_dir=str(config_dir),
        owner_uid=1000,
        owner_gid=1000,
        ca_cert=str(ca),
        require_system_ca=True,
        backend_host='127.0.0.1',
        backend_port=daemon.BACKEND_PORT,
        listen_host='127.0.0.1',
        listen_port=daemon.PROXY_PORT,
        parent_pid=0,
    )

    try:
        asyncio.run(daemon._serve(args))
    except RuntimeError as exc:
        assert 'Linux system trust-store install failed' in str(exc)
    else:
        raise AssertionError('expected required system CA failure')

    assert hosts_calls == []


def test_repair_sober_cert_ownership_repairs_only_user_home_paths(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    cert = home / '.var' / 'app' / 'org.vinegarhq.Sober' / 'data' / 'sober' / 'asset_overlay' / 'ssl' / 'cacert.pem'
    cert.parent.mkdir(parents=True)
    cert.write_text('cert', encoding='utf-8')
    chowned = []

    class Pw:
        pw_dir = str(home)

    def fake_lstat(self):
        class Stat:
            st_uid = 0
            st_gid = 0

        return Stat()

    monkeypatch.setattr(daemon.pwd, 'getpwuid', lambda _uid: Pw())
    monkeypatch.setattr(daemon.os, 'chown', lambda path, uid, gid, follow_symlinks=False: chowned.append((path, uid, gid, follow_symlinks)))
    monkeypatch.setattr(daemon.Path, 'lstat', fake_lstat)

    daemon._repair_sober_cert_ownership(1000, 1000)

    assert (cert.parent.parent, 1000, 1000, False) in chowned
    assert (cert.parent, 1000, 1000, False) in chowned
    assert (cert, 1000, 1000, False) in chowned
