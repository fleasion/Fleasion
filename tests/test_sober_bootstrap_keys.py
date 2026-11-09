"""Safety and restoration of Sober's native TLS key-log override."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleasion.proxy.splice_keys import KeyProvider
from fleasion.utils import platform_linux, sober_bootstrap_keys


def _flatpak_path(_name: str) -> str:
    return '/usr/bin/flatpak'


def _private_files(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / 'runtime'
    runtime.mkdir(mode=0o700)
    return runtime / '.fleasion-native-tls.keys', tmp_path / 'state.json'


def test_native_sober_keylog_lifecycle_does_not_touch_unrelated_files(tmp_path: Path) -> None:
    keylog, marker = _private_files(tmp_path)
    unrelated = tmp_path / 'user-file'
    unrelated.write_text('keep')
    prior: sober_bootstrap_keys.KeylogOverrideState = {
        'previous_present': True, 'previous_value': '/old/keys',
    }
    assert sober_bootstrap_keys.arm(keylog, marker, prior)
    assert keylog.is_file()
    assert keylog.stat().st_mode & 0o777 == 0o600
    assert marker.stat().st_mode & 0o777 == 0o600
    assert sober_bootstrap_keys.restore_option(marker) == '--env=SSLKEYLOGFILE=/old/keys'
    keylog.write_text('synthetic-keylog-only')
    assert sober_bootstrap_keys.clear(keylog, marker)
    assert not keylog.exists()
    assert not marker.exists()
    assert unrelated.read_text() == 'keep'


def test_native_sober_keylog_rejects_symlinks_and_wrong_ownership(tmp_path: Path) -> None:
    keylog, marker = _private_files(tmp_path)
    user_file = tmp_path / 'user-file'
    user_file.write_text('must-not-truncate')
    keylog.symlink_to(user_file)
    assert not sober_bootstrap_keys.arm(
        keylog, marker, {'previous_present': False, 'previous_value': ''}
    )
    assert user_file.read_text() == 'must-not-truncate'
    assert not marker.exists()
    keylog.unlink()
    keylog.parent.chmod(0o755)
    assert not sober_bootstrap_keys.arm(
        keylog, marker, {'previous_present': False, 'previous_value': ''}
    )
    assert not marker.exists()


def test_native_sober_keylog_refuses_malformed_ownership_state(tmp_path: Path) -> None:
    keylog, marker = _private_files(tmp_path)
    marker.write_text('not-json')
    with pytest.raises(ValueError):
        sober_bootstrap_keys.restore_option(marker)
    assert not sober_bootstrap_keys.clear(keylog, marker)
    assert marker.exists()


def test_native_sober_keylog_restores_explicit_empty_value(tmp_path: Path) -> None:
    keylog, marker = _private_files(tmp_path)
    previous = sober_bootstrap_keys.previous_keylog_override(
        '[Environment]\nSSLKEYLOGFILE=\n'
    )
    assert previous == {'previous_present': True, 'previous_value': ''}
    assert sober_bootstrap_keys.arm(keylog, marker, previous)
    assert sober_bootstrap_keys.restore_option(marker) == '--env=SSLKEYLOGFILE='
    assert sober_bootstrap_keys.clear(keylog, marker)
    assert sober_bootstrap_keys.previous_keylog_override('[Context]\nfoo=bar\n') == {
        'previous_present': False, 'previous_value': '',
    }


def test_platform_sober_native_keylog_arm_restore_and_crash_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    keylog, marker = _private_files(tmp_path)
    proxy_owner = tmp_path / 'proxy-owner.json'
    calls: list[list[str]] = []
    monkeypatch.setattr(platform_linux, 'SOBER_NATIVE_KEYLOG_OVERRIDE_STATE', marker)
    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', proxy_owner)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(sober_bootstrap_keys, 'native_keylog_path', lambda: keylog)
    monkeypatch.setattr(platform_linux.shutil, 'which', _flatpak_path)

    def fake_run(command: list[str], **_kwargs: object) -> object:
        calls.append(command)
        output = '[Environment]\nSSLKEYLOGFILE=/existing/user/keylog\n' if '--show' in command else ''
        return platform_linux.subprocess.CompletedProcess(command, 0, output, '')

    monkeypatch.setattr(platform_linux, '_standard_user_run', fake_run)
    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:58443', client_key='sober', sober_native_keylog=True
    )
    assert keylog.stat().st_mode & 0o777 == 0o600
    assert marker.exists()
    assert f'--env=SSLKEYLOGFILE={keylog}' in calls[1]
    assert json.loads(proxy_owner.read_text())['client'] == 'sober'
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    assert platform_linux.recover_stale_linux_client_env_proxy_override()
    assert '--env=SSLKEYLOGFILE=/existing/user/keylog' in calls[-1]
    assert not keylog.exists()
    assert not marker.exists()
    assert not proxy_owner.exists()


def test_platform_reuses_existing_sober_keylog_without_changing_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    keylog, marker = _private_files(tmp_path)
    keylog.write_text('existing-user-log\n', encoding='ascii')
    keylog.chmod(0o644)
    previous_bytes = keylog.read_bytes()
    owner_state = tmp_path / 'proxy-owner.json'
    calls: list[list[str]] = []
    monkeypatch.setattr(platform_linux, 'SOBER_NATIVE_KEYLOG_OVERRIDE_STATE', marker)
    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', owner_state)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(sober_bootstrap_keys, 'native_keylog_path', lambda: keylog)
    monkeypatch.setattr(platform_linux.shutil, 'which', _flatpak_path)

    def fake_run(command: list[str], **_kwargs: object) -> object:
        calls.append(command)
        output = f'[Environment]\nSSLKEYLOGFILE={keylog}\n' if '--show' in command else ''
        return platform_linux.subprocess.CompletedProcess(command, 0, output, '')

    monkeypatch.setattr(platform_linux, '_standard_user_run', fake_run)
    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:58443', client_key='sober', sober_native_keylog=True
    )
    assert f'--env=SSLKEYLOGFILE={keylog}' in calls[1]
    assert keylog.read_bytes() == previous_bytes
    assert keylog.stat().st_mode & 0o777 == 0o644
    assert not marker.exists()
    assert platform_linux.clear_linux_client_env_proxy_override(client_key='sober')
    assert keylog.read_bytes() == previous_bytes
    assert not marker.exists()
    assert not owner_state.exists()
    assert not any('SSLKEYLOGFILE' in option for option in calls[-1])


def test_existing_sober_keylog_requires_matching_override_and_secure_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    keylog, marker = _private_files(tmp_path)
    keylog.write_text('existing-user-log\n', encoding='ascii')
    keylog.chmod(0o644)
    monkeypatch.setattr(platform_linux, 'SOBER_NATIVE_KEYLOG_OVERRIDE_STATE', marker)
    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', tmp_path / 'owner.json')
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(sober_bootstrap_keys, 'native_keylog_path', lambda: keylog)
    monkeypatch.setattr(platform_linux.shutil, 'which', _flatpak_path)

    def different_override(command: list[str], **_kwargs: object) -> object:
        output = '[Environment]\nSSLKEYLOGFILE=/another/keylog\n' if '--show' in command else ''
        return platform_linux.subprocess.CompletedProcess(command, 0, output, '')

    monkeypatch.setattr(platform_linux, '_standard_user_run', different_override)
    assert not platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:58443', client_key='sober', sober_native_keylog=True
    )
    assert keylog.read_text(encoding='ascii') == 'existing-user-log\n'
    assert not marker.exists()

    assert sober_bootstrap_keys.reusable_preconfigured_keylog(keylog, marker)
    keylog.parent.chmod(0o755)
    assert not sober_bootstrap_keys.reusable_preconfigured_keylog(keylog, marker)
    keylog.parent.chmod(0o700)
    keylog.unlink()
    keylog.symlink_to(tmp_path / 'other')
    assert not sober_bootstrap_keys.reusable_preconfigured_keylog(keylog, marker)


def test_platform_sober_keylog_override_failure_cleans_private_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    keylog, marker = _private_files(tmp_path)
    monkeypatch.setattr(platform_linux, 'SOBER_NATIVE_KEYLOG_OVERRIDE_STATE', marker)
    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', tmp_path / 'proxy-owner.json')
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(sober_bootstrap_keys, 'native_keylog_path', lambda: keylog)
    monkeypatch.setattr(platform_linux.shutil, 'which', _flatpak_path)

    def fake_run(command: list[str], **_kwargs: object) -> object:
        return platform_linux.subprocess.CompletedProcess(
            command, 0 if '--show' in command else 1, '', 'failed'
        )

    monkeypatch.setattr(platform_linux, '_standard_user_run', fake_run)
    assert not platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:58443', client_key='sober', sober_native_keylog=True
    )
    assert not keylog.exists()
    assert not marker.exists()


def test_native_keylog_splice_provider_reads_bootstrap_and_android_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.proxy import splice_keys

    native = 'a1' * 32
    android = 'b2' * 32
    label = 'CLIENT_TRAFFIC_SECRET_0'
    path = tmp_path / 'sober.keylog'
    path.write_text(f'{label} {native} {"c3" * 32}\n')
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(splice_keys, 'find_sober_pid', lambda: calls.append(('find', None)) or 23)

    def read_android(pid: int, **kwargs: object) -> dict[str, dict[str, bytes]]:
        calls.append(('scan', pid))
        assert kwargs['client_random'] == android
        return {android: {label: bytes.fromhex('d4' * 32)}}

    monkeypatch.setattr(splice_keys, 'scan_sober', read_android)
    provider = KeyProvider(linux_client_key='sober', sober_native_keylog=path)
    assert provider._read(native)[native][label] == bytes.fromhex('c3' * 32)  # pyright: ignore[reportPrivateUsage]
    assert calls == []
    assert provider._read(android)[android][label] == bytes.fromhex('d4' * 32)  # pyright: ignore[reportPrivateUsage]
    assert calls == [('find', None), ('scan', 23)]
    provider.clear()
