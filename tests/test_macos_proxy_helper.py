import ast
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Never, Protocol, cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from fleasion import macos_proxy_helper_daemon as daemon
from fleasion.utils import macos_proxy_helper

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='macOS-only proxy helper tests')


type JsonObject = dict[str, object]


class _RelayHandlerLike(Protocol):
    client_address: tuple[str, int]

    def handle(self) -> None: ...


def _noop() -> None:
    return None


def _no_sleep(_seconds: float) -> None:
    return None


def _set_hosts(hosts: Iterable[object]) -> None:
    callback = cast('Callable[[Iterable[object]], None]', daemon.__dict__['_set_hosts'])
    callback(hosts)


def _handle_request(request: object) -> JsonObject:
    callback = cast('Callable[[object], JsonObject]', daemon.__dict__['_handle_request'])
    return callback(request)


def _probe_backend() -> JsonObject:
    callback = cast('Callable[[], JsonObject]', daemon.__dict__['_probe_backend'])
    return callback()


def _backend_port() -> int:
    return cast('int', daemon.__dict__['_backend_port'])


def _relay_handler() -> _RelayHandlerLike:
    relay_type = cast('type[object]', daemon.__dict__['_RelayHandler'])
    return cast('_RelayHandlerLike', object.__new__(relay_type))


def _build_plist() -> bytes:
    callback = cast('Callable[[], bytes]', macos_proxy_helper.__dict__['_build_plist'])
    return callback()


def _source_helper_path() -> Path:
    callback = cast('Callable[[], Path]', macos_proxy_helper.__dict__['_source_helper_path'])
    return callback()


def _helper_readiness_diagnostic() -> tuple[bool, str]:
    callback = cast(
        'Callable[[], tuple[bool, str]]',
        macos_proxy_helper.__dict__['_helper_readiness_diagnostic'],
    )
    return callback()


def _response_list(response: JsonObject, key: str) -> list[JsonObject]:
    value = response[key]
    assert isinstance(value, list)
    return cast('list[JsonObject]', value)


def _response_str(response: JsonObject, key: str) -> str:
    value = response[key]
    assert isinstance(value, str)
    return value


def _response_capabilities(response: JsonObject) -> list[str]:
    value = response['capabilities']
    assert isinstance(value, list)
    return cast('list[str]', value)


def _raise_connection_refused(*_args: object, **_kwargs: object) -> Never:
    raise ConnectionRefusedError(61, 'Connection refused')


def _raise_connection_refused_text(*_args: object, **_kwargs: object) -> Never:
    msg = 'connection refused'
    raise ConnectionRefusedError(msg)


def _backend_connection() -> SimpleNamespace:
    return SimpleNamespace(close=_noop)


def _status_factory(version: int, capabilities: list[str]) -> Callable[[float], JsonObject]:
    def status(timeout: float = 1.0) -> JsonObject:
        del timeout
        return {
            'ok': True,
            'version': version,
            'backend_port': macos_proxy_helper.MACOS_PROXY_BACKEND_PORT,
            'capabilities': capabilities,
        }

    return status


def test_client_and_daemon_require_the_same_exact_helper_identity() -> None:
    assert macos_proxy_helper.EXPECTED_HELPER_VERSION == daemon.HELPER_VERSION
    assert macos_proxy_helper.REQUIRED_HELPER_CAPABILITIES.issubset(daemon.HELPER_CAPABILITIES)


def _reset_daemon_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text('127.0.0.1 localhost\n10.0.0.1 custom.example\n', encoding='utf-8')
    token_file = tmp_path / 'token'
    token_file.write_text('x' * 48, encoding='utf-8')
    monkeypatch.setattr(daemon, 'HOSTS_FILE', str(hosts_file))
    monkeypatch.setattr(daemon, '_token_file', str(token_file))
    monkeypatch.setattr(daemon, '_flush_dns', _noop)
    monkeypatch.setattr(daemon, '_active_hosts', set[str]())
    monkeypatch.setattr(daemon, '_last_heartbeat', 0.0)
    return hosts_file, token_file


def test_source_helper_remains_compatible_with_pre_314_python_syntax() -> None:
    source = Path(daemon.__file__).read_text(encoding='utf-8')

    ast.parse(source, filename=daemon.__file__, feature_version=(3, 13))


def test_helper_only_manages_allowlisted_fleasion_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file, _ = _reset_daemon_state(tmp_path, monkeypatch)

    _set_hosts({'assetdelivery.roblox.com', 'gamejoin.roblox.com'})
    content = hosts_file.read_text(encoding='utf-8')

    assert '10.0.0.1 custom.example' in content
    assert '127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry' in content
    assert '127.0.0.1 gamejoin.roblox.com # Fleasion proxy entry' in content

    _set_hosts([])
    cleaned = hosts_file.read_text(encoding='utf-8')
    assert 'custom.example' in cleaned
    assert 'Fleasion proxy entry' not in cleaned

    with pytest.raises(ValueError):
        _set_hosts({'example.com'})


def test_helper_creates_missing_hosts_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_root = tmp_path / 'system'
    hosts_root.mkdir()
    hosts_file = hosts_root / 'etc' / 'hosts'
    token_file = tmp_path / 'token'
    token_file.write_text('x' * 48, encoding='utf-8')
    monkeypatch.setattr(daemon, 'HOSTS_FILE', str(hosts_file))
    monkeypatch.setattr(daemon, '_token_file', str(token_file))
    monkeypatch.setattr(daemon, '_flush_dns', _noop)
    monkeypatch.setattr(daemon, '_active_hosts', set[str]())
    monkeypatch.setattr(daemon, '_last_heartbeat', 0.0)

    _set_hosts({'assetdelivery.roblox.com'})

    assert hosts_file.parent.is_dir()
    assert '127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry' in hosts_file.read_text(
        encoding='utf-8'
    )


def test_helper_recreates_missing_hosts_file_with_macos_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    token_file = tmp_path / 'token'
    token_file.write_text('x' * 48, encoding='utf-8')
    monkeypatch.setattr(daemon, 'HOSTS_FILE', str(hosts_file))
    monkeypatch.setattr(daemon, '_token_file', str(token_file))
    monkeypatch.setattr(daemon, '_flush_dns', _noop)
    monkeypatch.setattr(daemon, '_active_hosts', set[str]())
    monkeypatch.setattr(daemon, '_last_heartbeat', 0.0)

    _set_hosts({'assetdelivery.roblox.com'})

    content = hosts_file.read_text(encoding='utf-8')
    assert '127.0.0.1\tlocalhost' in content
    assert '255.255.255.255\tbroadcasthost' in content
    assert '::1             localhost' in content
    assert '127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry' in content


def test_helper_rejects_conflicting_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file, _ = _reset_daemon_state(tmp_path, monkeypatch)
    hosts_file.write_text('203.0.113.1 assetdelivery.roblox.com\n', encoding='utf-8')

    with pytest.raises(RuntimeError, match='hosts conflict'):
        _set_hosts({'assetdelivery.roblox.com'})


def test_helper_control_requires_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)

    assert _handle_request({'token': 'wrong', 'action': 'status'})['ok'] is False
    response = _handle_request({'token': token_file.read_text(), 'action': 'status'})
    assert response['ok'] is True
    assert response['version'] == daemon.HELPER_VERSION
    assert response['pid'] == os.getpid()
    assert response['ppid'] == os.getppid()
    assert response['executable'] == sys.executable
    capabilities = _response_capabilities(response)
    assert 'patch_ca' in capabilities
    assert 'probe_backend' in capabilities


def test_helper_backend_probe_reports_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        daemon.socket,
        'create_connection',
        _raise_connection_refused,
    )

    response = _probe_backend()

    assert response['ok'] is True
    assert response['reachable'] is False
    assert response['backend_port'] == _backend_port()
    assert response['error_type'] == 'ConnectionRefusedError'
    assert response['errno'] == 61
    assert 'Connection refused' in _response_str(response, 'error')


def test_helper_backend_probe_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _backend_connection()

    def create_connection(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return backend

    monkeypatch.setattr(daemon.socket, 'create_connection', create_connection)

    response = _probe_backend()

    assert response['ok'] is True
    assert response['reachable'] is True
    assert response['backend_port'] == _backend_port()
    assert response['error'] == ''


def test_relay_logs_backend_connection_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        daemon.socket,
        'create_connection',
        _raise_connection_refused,
    )
    handler = _relay_handler()
    handler.client_address = ('127.0.0.1', 50123)

    with caplog.at_level('WARNING', logger='fleasion-proxy-helper'):
        handler.handle()

    assert 'relay backend connection failed' in caplog.text
    assert 'ConnectionRefusedError' in caplog.text
    assert 'errno=61' in caplog.text


def test_installed_helper_plist_runs_root_owned_helper_copy() -> None:
    plist = cast('dict[str, object]', macos_proxy_helper.plistlib.loads(_build_plist()))
    args = cast('list[str]', plist['ProgramArguments'])

    assert args[0] == str(macos_proxy_helper.HELPER_INSTALL_PATH)
    assert '/usr/bin/python3' not in args
    assert '/Users/' not in args[0]
    assert plist['RunAtLoad'] is True
    assert plist['KeepAlive'] is True
    assert plist['StandardOutPath'] == str(macos_proxy_helper.HELPER_STDOUT_LOG_PATH)
    assert plist['StandardErrorPath'] == str(macos_proxy_helper.HELPER_STDERR_LOG_PATH)
    assert plist['StandardOutPath'] != plist['StandardErrorPath']
    assert plist['StandardErrorPath'] != str(macos_proxy_helper.HELPER_LOG_PATH)


def test_frozen_app_uses_framework_helper_not_python_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = tmp_path / 'Fleasion.app' / 'Contents' / 'Resources'
    frameworks = resources.parent / 'Frameworks'
    resources.mkdir(parents=True)
    frameworks.mkdir()
    (resources / 'macos_proxy_helper_daemon.py').write_text('# source fallback\n', encoding='utf-8')
    helper = frameworks / 'fleasion-proxy-helper-arm64'
    helper.write_text('native helper\n', encoding='utf-8')
    helper.chmod(0o755)

    monkeypatch.setattr(macos_proxy_helper.sys, '_MEIPASS', str(resources), raising=False)
    monkeypatch.setattr(macos_proxy_helper.platform, 'machine', lambda: 'arm64')

    assert _source_helper_path() == helper


def test_frozen_app_without_native_helper_does_not_use_python_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = tmp_path / 'Fleasion.app' / 'Contents' / 'Resources'
    resources.mkdir(parents=True)
    (resources / 'macos_proxy_helper_daemon.py').write_text('# source fallback\n', encoding='utf-8')

    monkeypatch.setattr(macos_proxy_helper.sys, '_MEIPASS', str(resources), raising=False)

    assert _source_helper_path() == (
        resources.parent / 'Frameworks' / macos_proxy_helper.HELPER_BUNDLED_EXECUTABLE_NAME
    )


def test_source_run_uses_python_helper_when_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(macos_proxy_helper.sys, '_MEIPASS', raising=False)

    assert _source_helper_path() == (
        Path(macos_proxy_helper.__file__).resolve().parents[1] / 'macos_proxy_helper_daemon.py'
    )


def test_helper_installer_stages_helper_before_privileged_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / 'Documents' / 'Fleasion' / 'macos_proxy_helper_daemon.py'
    source.parent.mkdir(parents=True)
    source.write_text('# helper\n', encoding='utf-8')
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured['args'] = args
        captured['script'] = args[-1]
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(macos_proxy_helper.sys, 'platform', 'darwin')
    monkeypatch.setattr(macos_proxy_helper, '_source_helper_path', lambda: source)
    monkeypatch.setattr(macos_proxy_helper, '_ensure_token', lambda: 'x' * 48)
    monkeypatch.setattr(macos_proxy_helper, '_helper_readiness_diagnostic', lambda: (True, ''))
    monkeypatch.setattr(macos_proxy_helper.subprocess, 'run', fake_run)

    ok, detail = macos_proxy_helper.install_helper()

    assert ok is True, detail
    apple_script = cast('str', captured['script'])
    payload = apple_script.removeprefix('do shell script ').removesuffix(
        ' with administrator privileges'
    )
    script = cast('str', json.loads(payload))
    assert str(source) not in script
    assert '/usr/bin/python3' not in script
    assert 'launchctl bootout' in script
    assert script.index('launchctl bootout') < script.index('/usr/bin/install')
    assert '/usr/bin/xattr -c' in script
    assert script.index('/usr/bin/xattr -c') > script.index('/usr/bin/install')
    assert 'launchctl bootstrap system' in script
    assert 'launchctl load -w' in script
    assert f'launchctl print system/{macos_proxy_helper.HELPER_ID}' in script
    assert f'launchctl kill SIGKILL system/{macos_proxy_helper.HELPER_ID}' in script
    assert 'could not unload existing helper service' in script
    assert (
        f'lsof -nP -iTCP:{macos_proxy_helper.MACOS_PROXY_HELPER_CONTROL_PORT} '
        '-sTCP:LISTEN'
    ) in script
    assert (
        f'lsof -nP -t -iTCP:{macos_proxy_helper.MACOS_PROXY_HELPER_CONTROL_PORT} '
        '-sTCP:LISTEN'
    ) in script
    assert 'lsof -a -p' in script
    assert '$listener_pid' in script
    assert '-d txt -Fn' in script
    assert 'listener_uid=' in script
    assert '/bin/ps -p' in script
    assert '-o uid=' in script
    assert 'listener_command=' in script
    assert '/bin/ps -ww -p' in script
    assert '-o command=' in script
    assert 'listener_is_helper=0' in script
    assert '*/python|*/python[0-9]*|*/Python)' in script
    assert str(macos_proxy_helper.HELPER_INSTALL_PATH) in script
    assert 'listener_is_helper=1' in script
    assert '[ "$listener_uid" = "0" ]' in script
    assert (
        f'control port {macos_proxy_helper.MACOS_PROXY_HELPER_CONTROL_PORT} '
        'is owned by unexpected process'
    ) in script
    assert str(macos_proxy_helper.HELPER_INSTALL_PATH) in script
    assert 'terminating stale Fleasion proxy helper listener pid=$listener_pid' in script
    assert '/bin/kill -KILL' in script
    assert 'exit 43' in script
    assert 'exit 44' in script
    assert f'shasum -a 256 {macos_proxy_helper.HELPER_INSTALL_PATH}' in script
    assert f'file {macos_proxy_helper.HELPER_INSTALL_PATH}' in script
    assert 'helper install diagnostics: service state' in script
    assert 'helper install diagnostics: control-port listener' in script
    assert 'helper install diagnostics: installed executable' in script
    assert 'exit 41' in script
    assert 'exit 42' in script
    for log_path in (
        macos_proxy_helper.HELPER_LOG_PATH,
        macos_proxy_helper.HELPER_STDOUT_LOG_PATH,
        macos_proxy_helper.HELPER_STDERR_LOG_PATH,
    ):
        assert f'/usr/bin/install -o root -g wheel -m 644 /dev/null {log_path}' in script


def test_helper_installer_retries_until_helper_becomes_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / 'macos_proxy_helper_daemon.py'
    source.write_text('# helper\n', encoding='utf-8')
    attempts: list[None] = []

    monkeypatch.setattr(macos_proxy_helper.sys, 'platform', 'darwin')
    monkeypatch.setattr(macos_proxy_helper, '_source_helper_path', lambda: source)
    monkeypatch.setattr(macos_proxy_helper, '_ensure_token', lambda: 'x' * 48)

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(macos_proxy_helper.subprocess, 'run', fake_run)

    def readiness() -> tuple[bool, str]:
        attempts.append(None)
        if len(attempts) < 3:
            return False, 'Could not contact the helper control service: ConnectionRefusedError'
        return True, ''

    monkeypatch.setattr(macos_proxy_helper, '_helper_readiness_diagnostic', readiness)
    monkeypatch.setattr(macos_proxy_helper.time, 'sleep', _no_sleep)

    ok, detail = macos_proxy_helper.install_helper()

    assert ok is True, detail
    assert len(attempts) == 3


def test_helper_readiness_diagnostic_preserves_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        macos_proxy_helper,
        '_request',
        _raise_connection_refused_text,
    )

    ready, detail = _helper_readiness_diagnostic()

    assert ready is False
    assert 'ConnectionRefusedError' in detail
    assert 'connection refused' in detail


def _fake_roblox_resources(tmp_path: Path) -> Path:
    app = tmp_path / 'Roblox.app'
    resources = app / 'Contents' / 'Resources'
    macos = app / 'Contents' / 'MacOS'
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    (macos / 'RobloxPlayer').write_text('#!/bin/sh\n', encoding='utf-8')
    return resources


def _fake_froststrap_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(daemon, '_USERS_ROOT', tmp_path)
    app = (
        tmp_path
        / 'test-user'
        / 'Library'
        / 'Application Support'
        / 'Froststrap'
        / 'Versions'
        / 'version-deadbeef'
        / 'RobloxPlayer.app'
    )
    resources = app / 'Contents' / 'Resources'
    macos = app / 'Contents' / 'MacOS'
    resources.mkdir(parents=True)
    macos.mkdir(parents=True)
    (macos / 'RobloxPlayer').write_text('#!/bin/sh\n', encoding='utf-8')
    return resources


def _make_self_signed_ca_pem(
    common_name: str = 'Fleasion Proxy CA', organization: str = 'Fleasion'
) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        ]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode('utf-8')


def test_helper_patch_ca_writes_only_roblox_cacert_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    resources = _fake_roblox_resources(tmp_path)
    ca_file = resources / 'ssl' / 'cacert.pem'
    ca_file.parent.mkdir()
    old_ca = '-----BEGIN CERTIFICATE-----\nOLD\n-----END CERTIFICATE-----\n'
    current_ca = '-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n'
    ca_file.write_text(f'MOZILLA ROOT\n{old_ca}', encoding='utf-8')

    response = _handle_request(
        {
            'token': token_file.read_text(),
            'action': 'patch_ca',
            'ca_pem': current_ca,
            'installs': [{'resource_dir': str(resources), 'remove_pems': [old_ca]}],
        }
    )

    assert response['ok'] is True
    assert _response_list(response, 'patched')[0]['ca_file'] == str(ca_file)
    assert ca_file.read_text(encoding='utf-8') == f'MOZILLA ROOT\n{current_ca}'
    assert oct(ca_file.stat().st_mode & 0o777) == '0o644'


def test_helper_patch_ca_accepts_froststrap_managed_player_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    resources = _fake_froststrap_resources(tmp_path, monkeypatch)
    ca_file = resources / 'ssl' / 'cacert.pem'
    ca_file.parent.mkdir()
    ca_file.write_text('MOZILLA ROOT\n', encoding='utf-8')
    current_ca = '-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n'

    response = _handle_request(
        {
            'token': token_file.read_text(),
            'action': 'patch_ca',
            'ca_pem': current_ca,
            'installs': [{'resource_dir': str(resources), 'remove_pems': list[str]()}],
        }
    )

    assert response['ok'] is True
    assert _response_list(response, 'patched')[0]['ca_file'] == str(ca_file)
    assert current_ca in ca_file.read_text(encoding='utf-8')


def test_helper_rejects_robloxplayer_bundle_outside_froststrap_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    monkeypatch.setattr(daemon, '_USERS_ROOT', tmp_path)
    app = tmp_path / 'test-user' / 'Downloads' / 'RobloxPlayer.app'
    resources = app / 'Contents' / 'Resources'
    macos = app / 'Contents' / 'MacOS'
    resources.mkdir(parents=True)
    macos.mkdir(parents=True)
    (macos / 'RobloxPlayer').write_text('#!/bin/sh\n', encoding='utf-8')

    response = _handle_request(
        {
            'token': token_file.read_text(),
            'action': 'patch_ca',
            'ca_pem': '-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n',
            'installs': [{'resource_dir': str(resources), 'remove_pems': list[str]()}],
        }
    )

    assert response['ok'] is False
    failure = _response_list(response, 'failed')[0]
    assert 'supported Roblox app bundle' in _response_str(failure, 'error')


def test_helper_patch_ca_strips_all_fleasion_cas_when_requesting_full_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    resources = _fake_roblox_resources(tmp_path)
    ca_file = resources / 'ssl' / 'cacert.pem'
    ca_file.parent.mkdir()
    stale_ca = _make_self_signed_ca_pem()
    unrelated_ca = _make_self_signed_ca_pem(organization='Other Org')
    current_ca = _make_self_signed_ca_pem()
    ca_file.write_text(f'MOZILLA ROOT\n{stale_ca}{unrelated_ca}', encoding='utf-8')

    response = _handle_request(
        {
            'token': token_file.read_text(),
            'action': 'patch_ca',
            'ca_pem': current_ca,
            'installs': [
                {
                    'resource_dir': str(resources),
                    'remove_pems': list[str](),
                    'strip_all_fleasion_ca': True,
                }
            ],
        }
    )

    patched_text = ca_file.read_text(encoding='utf-8')
    assert response['ok'] is True
    assert _response_list(response, 'patched')[0]['ca_file'] == str(ca_file)
    assert stale_ca not in patched_text
    assert unrelated_ca in patched_text
    assert current_ca in patched_text
    assert patched_text.count('-----BEGIN CERTIFICATE-----') == 2


def test_helper_patch_ca_recovers_from_read_only_bundle_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    resources = _fake_roblox_resources(tmp_path)
    ssl_dir = resources / 'ssl'
    ssl_dir.mkdir()
    ca_file = ssl_dir / 'cacert.pem'
    old_ca = '-----BEGIN CERTIFICATE-----\nOLD\n-----END CERTIFICATE-----\n'
    current_ca = '-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n'
    ca_file.write_text(f'MOZILLA ROOT\n{old_ca}', encoding='utf-8')

    resources.chmod(0o555)
    ssl_dir.chmod(0o555)
    ca_file.chmod(0o444)

    response = _handle_request(
        {
            'token': token_file.read_text(),
            'action': 'patch_ca',
            'ca_pem': current_ca,
            'installs': [{'resource_dir': str(resources), 'remove_pems': [old_ca]}],
        }
    )

    assert response['ok'] is True
    assert _response_list(response, 'patched')[0]['ca_file'] == str(ca_file)
    assert ca_file.read_text(encoding='utf-8') == f'MOZILLA ROOT\n{current_ca}'
    assert oct(ca_file.stat().st_mode & 0o777) == '0o644'


def test_helper_patch_ca_rejects_arbitrary_resource_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    arbitrary = tmp_path / 'NotRoblox' / 'Contents' / 'Resources'
    arbitrary.mkdir(parents=True)
    current_ca = '-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n'

    response = _handle_request(
        {
            'token': token_file.read_text(),
            'action': 'patch_ca',
            'ca_pem': current_ca,
            'installs': [{'resource_dir': str(arbitrary), 'remove_pems': list[str]()}],
        }
    )

    assert response['ok'] is False
    assert response['failed']
    assert not (arbitrary / 'ssl' / 'cacert.pem').exists()


def test_helper_patch_ca_rejects_symlinked_cacert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not hasattr(os, 'symlink'):
        pytest.skip('symlink unavailable')
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    resources = _fake_roblox_resources(tmp_path)
    ssl_dir = resources / 'ssl'
    ssl_dir.mkdir()
    outside = tmp_path / 'outside.pem'
    outside.write_text('outside', encoding='utf-8')
    try:
        Path(ssl_dir / 'cacert.pem').symlink_to(outside)
    except OSError as exc:
        pytest.skip(f'symlink unavailable: {exc}')
    current_ca = '-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n'

    response = _handle_request(
        {
            'token': token_file.read_text(),
            'action': 'patch_ca',
            'ca_pem': current_ca,
            'installs': [{'resource_dir': str(resources), 'remove_pems': list[str]()}],
        }
    )

    assert response['ok'] is False
    assert response['failed']
    assert outside.read_text(encoding='utf-8') == 'outside'


def test_helper_readiness_requires_exact_helper_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        macos_proxy_helper,
        'helper_status',
        _status_factory(
            macos_proxy_helper.EXPECTED_HELPER_VERSION - 1,
            ['hosts', 'relay', 'patch_ca', 'probe_backend', 'trust_ca'],
        ),
    )
    assert macos_proxy_helper.helper_is_ready() is False

    monkeypatch.setattr(
        macos_proxy_helper,
        'helper_status',
        _status_factory(
            macos_proxy_helper.EXPECTED_HELPER_VERSION + 1,
            ['hosts', 'relay', 'patch_ca', 'probe_backend', 'trust_ca'],
        ),
    )
    assert macos_proxy_helper.helper_is_ready() is False

    monkeypatch.setattr(
        macos_proxy_helper,
        'helper_status',
        _status_factory(macos_proxy_helper.EXPECTED_HELPER_VERSION, ['hosts', 'relay', 'patch_ca']),
    )
    assert macos_proxy_helper.helper_is_ready() is False

    monkeypatch.setattr(
        macos_proxy_helper,
        'helper_status',
        _status_factory(
            macos_proxy_helper.EXPECTED_HELPER_VERSION,
            ['hosts', 'relay', 'patch_ca', 'probe_backend', 'trust_ca'],
        ),
    )
    assert macos_proxy_helper.helper_is_ready() is True


def test_helper_probe_backend_preserves_control_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_proxy_helper, '_request', _raise_connection_refused)

    response = macos_proxy_helper.helper_probe_backend()

    assert response['ok'] is False
    assert response['reachable'] is False
    assert response['error_type'] == 'ConnectionRefusedError'
    assert response['errno'] == 61
