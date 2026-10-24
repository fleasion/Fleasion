"""The Sober bootstrapper trusts the proxy through an env-var CA bundle."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

pytest.importorskip('pwd', reason='Linux platform tests require the POSIX pwd module')

from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import (
    SOBER_CLIENT,
    LinuxClientDescriptor,
    LinuxClientInstallation,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _prepare_bundle(installation: LinuxClientInstallation, ca_cert_path: Path) -> Path | None:
    callback = cast(
        'Callable[[LinuxClientInstallation, Path], Path | None]',
        platform_linux.__dict__['_prepare_env_ca_bundle'],
    )
    return callback(installation, ca_cert_path)

_FLEASION_CA = (
    '-----BEGIN CERTIFICATE-----\nFLEASIONTESTCERTDATA==\n-----END CERTIFICATE-----\n'
)
_PUBLIC_ROOT = (
    '-----BEGIN CERTIFICATE-----\nPUBLICROOTCERTDATA==\n-----END CERTIFICATE-----\n'
)


def _installation(client: LinuxClientDescriptor, home: Path) -> LinuxClientInstallation:
    return LinuxClientInstallation(
        client=client,
        paths=client.paths(home=home, environ={}),
        executable=home / 'flatpak',
    )


def _public_bundle(tmp_path: Path) -> Path:
    path = tmp_path / 'certifi.pem'
    path.write_text(_PUBLIC_ROOT, encoding='utf-8')
    return path


def _ca_file(tmp_path: Path) -> Path:
    path = tmp_path / 'ca.crt'
    path.write_text(_FLEASION_CA, encoding='utf-8')
    return path


def test_bundle_contains_public_roots_and_the_fleasion_ca(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform_linux.certifi, 'where', lambda: str(_public_bundle(tmp_path)))
    installation = _installation(SOBER_CLIENT, tmp_path)

    bundle = _prepare_bundle(installation, _ca_file(tmp_path))

    assert bundle == installation.paths.data_root / 'fleasion-ca-bundle.pem'
    assert bundle is not None
    text = bundle.read_text(encoding='utf-8')
    assert 'PUBLICROOTCERTDATA' in text
    assert 'FLEASIONTESTCERTDATA' in text
    # The data dir keeps its path inside the Flatpak sandbox
    assert str(bundle).startswith(str(tmp_path / '.var' / 'app' / SOBER_CLIENT.app_id))


def test_a_cert_less_ca_file_yields_no_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform_linux.certifi, 'where', lambda: str(_public_bundle(tmp_path)))
    empty = tmp_path / 'empty.crt'
    empty.write_text('not a certificate', encoding='utf-8')

    assert _prepare_bundle(_installation(SOBER_CLIENT, tmp_path), empty) is None


def _which(home: Path) -> Callable[[str], str]:
    def which(_name: str) -> str:
        return str(home / 'flatpak')

    return which


def _capture_override(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> platform_linux.subprocess.CompletedProcess[str]:
        calls.append(args)
        return platform_linux.subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(platform_linux, 'USER_HOME', home)
    monkeypatch.setattr(platform_linux.shutil, 'which', _which(home))
    monkeypatch.setattr(platform_linux, '_standard_user_run', run)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', home / 'override.json')

    def for_client_key(_key: str | None) -> LinuxClientInstallation:
        return _installation(SOBER_CLIENT, home)

    monkeypatch.setattr(platform_linux, '_installation_for_client_key', for_client_key)
    monkeypatch.setattr(platform_linux.certifi, 'where', lambda: str(_public_bundle(home)))
    return calls


def test_arming_adds_the_ca_bundle_env_and_clearing_removes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture_override(monkeypatch, tmp_path)
    ca = _ca_file(tmp_path)
    bundle = tmp_path / '.var' / 'app' / SOBER_CLIENT.app_id / 'data' / 'sober' / 'fleasion-ca-bundle.pem'

    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:58443', client_key='sober', ca_cert_path=ca
    )
    arm = calls[-1]
    assert f'--env=SSL_CERT_FILE={bundle}' in arm
    assert f'--env=CURL_CA_BUNDLE={bundle}' in arm
    assert bundle.is_file()

    assert platform_linux.clear_linux_client_env_proxy_override(client_key='sober')
    clear = calls[-1]
    assert '--unset-env=SSL_CERT_FILE' in clear
    assert '--unset-env=CURL_CA_BUNDLE' in clear
    assert not bundle.exists()


def test_arming_without_a_ca_path_sets_no_bundle_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture_override(monkeypatch, tmp_path)

    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:58443', client_key='sober'
    )
    arm = calls[-1]
    assert not any('SSL_CERT_FILE' in arg for arg in arm)
    assert any('ALL_PROXY' in arg or 'all_proxy' in arg for arg in arm)
