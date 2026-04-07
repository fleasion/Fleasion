"""Certificate generation and management using the cryptography library.

Generates a local CA and per-host leaf certificates for TLS MITM interception.
No openssl binary required - everything is done in-process.
"""

import datetime
import ipaddress
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# Hosts we intercept - certs are pre-generated for these at startup
INTERCEPTED_HOSTS = ('assetdelivery.roblox.com', 'fts.rbxcdn.com')


def _crypto():
    """Lazy import of cryptography modules."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    return x509, NameOID, hashes, serialization, rsa


def generate_ca(ca_dir: Path) -> Tuple[Path, Path]:
    """Generate a CA key + self-signed cert and save to ca_dir.

    Returns (ca_cert_path, ca_key_path).  Skips generation if both files
    already exist (so Roblox doesn't need to re-import the cert on every run).
    """
    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_cert_path = ca_dir / 'ca.crt'
    ca_key_path = ca_dir / 'ca.key'

    if ca_cert_path.exists() and ca_key_path.exists():
        return ca_cert_path, ca_key_path

    x509, NameOID, hashes, serialization, rsa = _crypto()

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'Fleasion Proxy CA'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Fleasion'),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    ca_cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    ca_key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    logger.info('Generated new Fleasion CA cert at %s', ca_cert_path)
    return ca_cert_path, ca_key_path


def generate_host_cert(host: str, ca_cert_path: Path, ca_key_path: Path, ca_dir: Path) -> Tuple[Path, Path]:
    """Generate a leaf certificate for *host* signed by our CA.

    Returns (cert_path, key_path).  Uses cached files if they already exist.
    """
    ca_dir.mkdir(parents=True, exist_ok=True)
    safe_host = host.replace('*', '_wildcard_')
    cert_path = ca_dir / f'{safe_host}.crt'
    key_path = ca_dir / f'{safe_host}.key'

    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    x509, NameOID, hashes, serialization, rsa = _crypto()

    # Load CA
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    ca_key = load_pem_private_key(ca_key_path.read_bytes(), password=None)
    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())

    # Generate a fresh key for this leaf cert
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)

    san_entries = []
    try:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        san_entries.append(x509.DNSName(host))

    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    cert_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    logger.debug('Generated leaf cert for %s', host)
    return cert_path, key_path


def get_ca_pem(ca_cert_path: Path) -> str:
    """Return the CA certificate as a PEM string."""
    return ca_cert_path.read_text(encoding='utf-8')
