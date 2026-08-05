import asyncio
from datetime import datetime, timedelta, timezone
import errno
import json
import subprocess
from types import SimpleNamespace

import pytest

pytest.importorskip('pwd', reason='Linux helper tests require the POSIX pwd module')

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from fleasion import linux_proxy_helper_daemon as daemon


def _make_ca_pem(common_name='Fleasion Proxy CA', organization='Fleasion', *, is_ca=True, can_sign=True) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=can_sign,
                crl_sign=can_sign,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


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


def test_install_privileged_helper_writes_current_metadata(tmp_path, monkeypatch):
    source = tmp_path / 'linux_proxy_helper_daemon.py'
    source.write_text('print("helper")\n', encoding='utf-8')
    install_root = tmp_path / 'usr' / 'local' / 'libexec'
    policy_root = tmp_path / 'polkit'
    legacy_policy_root = tmp_path / 'legacy-polkit'

    monkeypatch.setattr(daemon, 'INSTALLED_HELPER_PATH', install_root / 'fleasion-linux-proxy-helper')
    monkeypatch.setattr(daemon, 'INSTALLED_HELPER_SCRIPT_PATH', install_root / 'fleasion-linux-proxy-helper.py')
    monkeypatch.setattr(daemon, 'INSTALLED_HELPER_METADATA_PATH', install_root / 'fleasion-linux-proxy-helper.metadata.json')
    monkeypatch.setattr(daemon, 'POLKIT_POLICY_PATH', policy_root / 'com.fleasion.proxy-helper.policy')
    monkeypatch.setattr(daemon, 'LEGACY_POLKIT_POLICY_PATH', legacy_policy_root / 'com.fleasion.proxy-helper.policy')
    monkeypatch.setattr(daemon.os, 'chown', lambda *_args, **_kwargs: None)

    details = daemon._install_privileged_helper(str(source))

    metadata = json.loads(daemon.INSTALLED_HELPER_METADATA_PATH.read_text(encoding='utf-8'))
    assert details['ok'] is True
    assert details['helper_metadata'] == metadata
    assert metadata == {
        'metadata_version': daemon.HELPER_METADATA_VERSION,
        'source_sha256': daemon._file_sha256(source),
        'source_helper_needs_dispatch_flag': False,
    }


def test_install_privileged_helper_can_install_system_ca_in_same_prompt(tmp_path, monkeypatch):
    source = tmp_path / 'linux_proxy_helper_daemon.py'
    source.write_text('print("helper")\n', encoding='utf-8')
    ca = tmp_path / 'home' / '.config' / daemon.CONFIG_DIR_NAME / 'proxy_ca' / 'ca.crt'
    ca.parent.mkdir(parents=True)
    ca.write_text('ca', encoding='utf-8')
    install_root = tmp_path / 'usr' / 'local' / 'libexec'
    policy_root = tmp_path / 'polkit'
    legacy_policy_root = tmp_path / 'legacy-polkit'

    monkeypatch.setattr(daemon, 'INSTALLED_HELPER_PATH', install_root / 'fleasion-linux-proxy-helper')
    monkeypatch.setattr(daemon, 'INSTALLED_HELPER_SCRIPT_PATH', install_root / 'fleasion-linux-proxy-helper.py')
    monkeypatch.setattr(daemon, 'INSTALLED_HELPER_METADATA_PATH', install_root / 'fleasion-linux-proxy-helper.metadata.json')
    monkeypatch.setattr(daemon, 'POLKIT_POLICY_PATH', policy_root / 'com.fleasion.proxy-helper.policy')
    monkeypatch.setattr(daemon, 'LEGACY_POLKIT_POLICY_PATH', legacy_policy_root / 'com.fleasion.proxy-helper.policy')
    monkeypatch.setattr(daemon.os, 'chown', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(daemon, '_validate_install_system_ca_args', lambda value: ca if value == str(ca) else None)
    monkeypatch.setattr(daemon, '_install_system_ca', lambda path: {'ok': path == ca, 'stores': ['update-ca-certificates']})

    details = daemon._install_privileged_helper(str(source), ca_cert=str(ca))

    assert details['ok'] is True
    assert details['system_ca'] == {'ok': True, 'stores': ['update-ca-certificates']}


def test_install_privileged_helper_allows_unsupported_system_ca(tmp_path, monkeypatch):
    source = tmp_path / 'linux_proxy_helper_daemon.py'
    source.write_text('print("helper")\n', encoding='utf-8')
    ca = tmp_path / 'home' / '.config' / daemon.CONFIG_DIR_NAME / 'proxy_ca' / 'ca.crt'
    ca.parent.mkdir(parents=True)
    ca.write_text('ca', encoding='utf-8')
    install_root = tmp_path / 'usr' / 'local' / 'libexec'
    policy_root = tmp_path / 'polkit'
    legacy_policy_root = tmp_path / 'legacy-polkit'

    monkeypatch.setattr(daemon, 'INSTALLED_HELPER_PATH', install_root / 'fleasion-linux-proxy-helper')
    monkeypatch.setattr(daemon, 'INSTALLED_HELPER_SCRIPT_PATH', install_root / 'fleasion-linux-proxy-helper.py')
    monkeypatch.setattr(daemon, 'INSTALLED_HELPER_METADATA_PATH', install_root / 'fleasion-linux-proxy-helper.metadata.json')
    monkeypatch.setattr(daemon, 'POLKIT_POLICY_PATH', policy_root / 'com.fleasion.proxy-helper.policy')
    monkeypatch.setattr(daemon, 'LEGACY_POLKIT_POLICY_PATH', legacy_policy_root / 'com.fleasion.proxy-helper.policy')
    monkeypatch.setattr(daemon.os, 'chown', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(daemon, '_validate_install_system_ca_args', lambda value: ca if value == str(ca) else None)
    monkeypatch.setattr(daemon, '_install_system_ca', lambda _path: {'ok': False, 'error': 'no_supported_system_trust_store'})

    details = daemon._install_privileged_helper(str(source), ca_cert=str(ca))

    assert details['ok'] is True
    assert details['system_ca'] == {'ok': False, 'error': 'no_supported_system_trust_store'}


def test_validate_fleasion_ca_certificate_accepts_fleasion_ca(tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_bytes(_make_ca_pem())

    daemon._validate_fleasion_ca_certificate(ca)


def test_validate_fleasion_ca_certificate_rejects_non_fleasion_subject(tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_bytes(_make_ca_pem(common_name='Other CA', organization='Other'))

    try:
        daemon._validate_fleasion_ca_certificate(ca)
    except RuntimeError as exc:
        assert 'not Fleasion Proxy CA' in str(exc)
    else:
        raise AssertionError('expected non-Fleasion CA rejection')


def test_validate_fleasion_ca_certificate_rejects_non_ca(tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_bytes(_make_ca_pem(is_ca=False))

    try:
        daemon._validate_fleasion_ca_certificate(ca)
    except RuntimeError as exc:
        assert 'not marked as a certificate authority' in str(exc)
    else:
        raise AssertionError('expected non-CA rejection')


def test_install_system_ca_refreshes_trust_when_target_is_current(tmp_path, monkeypatch):
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
    assert calls == [['/usr/sbin/update-ca-certificates']]


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


def test_apply_hosts_continues_when_read_only_hosts_already_has_entries(tmp_path, monkeypatch):
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text(
        '127.0.0.1 assetdelivery.roblox.com\n'
        '127.0.0.1 gamejoin.roblox.com\n',
        encoding='utf-8',
    )

    def fail_apply(_hosts):
        raise OSError(errno.EROFS, 'Read-only file system', str(hosts_file))

    monkeypatch.setattr(daemon, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(daemon, '_clear_hosts', lambda: None)
    monkeypatch.setattr(daemon, '_install_boot_guard', lambda: False)
    monkeypatch.setattr(daemon, '_apply_hosts', fail_apply)

    assert daemon._apply_hosts_or_use_existing_read_only({
        'assetdelivery.roblox.com',
        'gamejoin.roblox.com',
    }) is True


def test_apply_hosts_raises_when_read_only_hosts_is_missing_entries(tmp_path, monkeypatch):
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text('127.0.0.1 assetdelivery.roblox.com\n', encoding='utf-8')

    def fail_apply(_hosts):
        raise OSError(errno.EROFS, 'Read-only file system', str(hosts_file))

    monkeypatch.setattr(daemon, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(daemon, '_clear_hosts', lambda: None)
    monkeypatch.setattr(daemon, '_install_boot_guard', lambda: False)
    monkeypatch.setattr(daemon, '_apply_hosts', fail_apply)

    try:
        daemon._apply_hosts_or_use_existing_read_only({
            'assetdelivery.roblox.com',
            'gamejoin.roblox.com',
        })
    except OSError as exc:
        assert exc.errno == errno.EROFS
    else:
        raise AssertionError('expected read-only hosts failure when mappings are missing')


def test_apply_hosts_delta_preserves_retained_entries(tmp_path, monkeypatch):
    hosts_file = tmp_path / 'hosts'
    retained = 'apis.roblox.com'
    added = 'clientsettings.roblox.com'
    hosts_file.write_text(
        '127.0.0.1 apis.roblox.com # Fleasion proxy entry\n'
        '127.0.0.1 gamejoin.roblox.com # Fleasion proxy entry\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(daemon, 'HOSTS_FILE', hosts_file)

    daemon._apply_hosts_delta(
        {retained, 'gamejoin.roblox.com'},
        {retained, added},
    )

    assert hosts_file.read_text(encoding='utf-8') == (
        '127.0.0.1 apis.roblox.com # Fleasion proxy entry\n'
        '127.0.0.1 clientsettings.roblox.com # Fleasion proxy entry\n'
    )


def test_host_failure_payload_marks_read_only_hosts_error(monkeypatch):
    args = SimpleNamespace(hosts='gamejoin.roblox.com,assetdelivery.roblox.com')
    error = OSError(errno.EROFS, 'Read-only file system', '/etc/hosts')
    monkeypatch.setattr(daemon, '_system_hosts_path_is_read_only', lambda: True)

    payload = daemon._host_failure_payload(args, error)

    assert payload['ok'] is False
    assert payload['code'] == 'linux_hosts_read_only'
    assert payload['system_read_only'] is True
    assert payload['hosts_path'] == str(daemon.HOSTS_FILE)
    assert payload['hosts'] == ['assetdelivery.roblox.com', 'gamejoin.roblox.com']


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
