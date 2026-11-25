import os
import stat
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import ExtensionOID

from fleasion import linux_proxy_helper_daemon, macos_proxy_helper_daemon
from fleasion.proxy.server import CLIENT_SETTINGS_HOSTS, INTERCEPT_HOSTS
from fleasion.utils.certs import generate_ca, generate_host_cert, generate_multi_host_cert


def test_all_hosts_file_proxy_hosts_have_certificate_coverage(tmp_path: Path) -> None:
    """Keep proxy-host allowlists and the fallback TLS certificate in sync."""
    assert CLIENT_SETTINGS_HOSTS <= INTERCEPT_HOSTS
    assert set(INTERCEPT_HOSTS) == macos_proxy_helper_daemon.ALLOWED_HOSTS
    assert linux_proxy_helper_daemon.ALLOWED_PROXY_HOSTS == INTERCEPT_HOSTS

    ca_cert_path, ca_key_path = generate_ca(tmp_path)
    cert_path, _key_path = generate_multi_host_cert(
        'intercept-default',
        INTERCEPT_HOSTS,
        ca_cert_path,
        ca_key_path,
        tmp_path,
    )
    certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = certificate.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
    assert isinstance(san, x509.SubjectAlternativeName)
    san_hosts = {name.lower() for name in san.get_values_for_type(x509.DNSName)}

    assert san_hosts == set(INTERCEPT_HOSTS)


def test_generated_private_keys_are_owner_only_and_cached_keys_are_repaired(
    tmp_path: Path,
) -> None:
    if os.name == 'nt':
        return

    ca_cert_path, ca_key_path = generate_ca(tmp_path)
    _host_cert, host_key_path = generate_host_cert(
        'assetdelivery.roblox.com',
        ca_cert_path,
        ca_key_path,
        tmp_path,
    )
    _multi_cert, multi_key_path = generate_multi_host_cert(
        'intercept-default',
        INTERCEPT_HOSTS,
        ca_cert_path,
        ca_key_path,
        tmp_path,
    )

    for key_path in (ca_key_path, host_key_path, multi_key_path):
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
        key_path.chmod(0o644)

    generate_ca(tmp_path)
    generate_host_cert(
        'assetdelivery.roblox.com',
        ca_cert_path,
        ca_key_path,
        tmp_path,
    )
    generate_multi_host_cert(
        'intercept-default',
        INTERCEPT_HOSTS,
        ca_cert_path,
        ca_key_path,
        tmp_path,
    )

    for key_path in (ca_key_path, host_key_path, multi_key_path):
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
