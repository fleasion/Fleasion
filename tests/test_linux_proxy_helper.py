from pathlib import Path

from Fleasion.utils import linux_proxy_helper


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
