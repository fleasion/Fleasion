"""Certificate generation and management using the cryptography library.

Generates a local CA and per-host leaf certificates for TLS MITM interception.
No openssl binary required - everything is done in-process.
"""

import datetime
import ipaddress
import logging
from pathlib import Path
from typing import Iterable, Tuple

logger = logging.getLogger(__name__)

# Hosts we intercept - certs are pre-generated for these at startup
INTERCEPTED_HOSTS = (
    'assetdelivery.roblox.com',
    'fts.rbxcdn.com',
    'contentdelivery.roblox.com',
    'gamejoin.roblox.com',
    'apis.roblox.com',
)

# Regenerate certs slightly before hard expiry so users do not hit sudden TLS failures.
CA_MIN_REMAINING_DAYS = 30
LEAF_MIN_REMAINING_DAYS = 7
LEAF_CERT_VALIDITY_DAYS = 825
NOT_VALID_BEFORE_SKEW_MINUTES = 5


def _crypto():
    """Lazy import of cryptography modules."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    return x509, NameOID, hashes, serialization, rsa


def _as_utc(dt: datetime.datetime) -> datetime.datetime:
    """Normalize X.509 datetime values to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _cert_valid_for(cert, min_remaining_days: int) -> bool:
    """Return True if cert is currently valid and has enough time remaining."""
    now = datetime.datetime.now(datetime.timezone.utc)

    not_before_utc = getattr(cert, 'not_valid_before_utc', None)
    not_after_utc = getattr(cert, 'not_valid_after_utc', None)
    if not_before_utc is None:
        not_before_utc = _as_utc(cert.not_valid_before)
    if not_after_utc is None:
        not_after_utc = _as_utc(cert.not_valid_after)

    if not (not_before_utc <= now <= not_after_utc):
        return False

    min_remaining = datetime.timedelta(days=min_remaining_days)
    return (not_after_utc - now) > min_remaining


def _cert_matches_private_key(cert, private_key, serialization) -> bool:
    """Return True if *private_key* matches *cert*'s public key."""
    cert_pub = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_pub = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return cert_pub == key_pub


def _leaf_signed_by_ca(leaf_cert, ca_cert, rsa_mod) -> bool:
    """Return True if *leaf_cert* verifies with *ca_cert*'s public key."""
    from cryptography.hazmat.primitives.asymmetric import ec as ec_mod, padding

    ca_public_key = ca_cert.public_key()
    if isinstance(ca_public_key, rsa_mod.RSAPublicKey):
        ca_public_key.verify(
            leaf_cert.signature,
            leaf_cert.tbs_certificate_bytes,
            padding.PKCS1v15(),
            leaf_cert.signature_hash_algorithm,
        )
        return True

    if isinstance(ca_public_key, ec_mod.EllipticCurvePublicKey):
        ca_public_key.verify(
            leaf_cert.signature,
            leaf_cert.tbs_certificate_bytes,
            ec_mod.ECDSA(leaf_cert.signature_hash_algorithm),
        )
        return True

    # Unsupported key types are treated as invalid for reuse checks.
    return False


def _safe_cert_filename(name: str) -> str:
    safe = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in name.strip())
    return safe or 'certificate'


def _normalise_hosts(hosts: Iterable[str]) -> list[str]:
    normalized = sorted({str(host).strip().lower() for host in hosts if str(host).strip()})
    if not normalized:
        raise ValueError('at least one host is required')
    return normalized


def _san_entries_for_hosts(hosts: Iterable[str]):
    x509, _, _, _, _ = _crypto()
    san_entries = []
    for host in _normalise_hosts(hosts):
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            san_entries.append(x509.DNSName(host))
    return san_entries


def _cert_san_names(cert) -> tuple[set[str], set[str]]:
    x509, _, _, _, _ = _crypto()
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        san_dns = {name.lower() for name in san.get_values_for_type(x509.DNSName)}
        san_ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
        return san_dns, san_ips
    except x509.ExtensionNotFound:
        return set(), set()


def _cert_allows_server_auth(cert) -> bool:
    x509, _, _, _, _ = _crypto()
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        return x509.ExtendedKeyUsageOID.SERVER_AUTH in eku
    except x509.ExtensionNotFound:
        # Absence of EKU is less restrictive than a wrong EKU.
        return True


def _cert_has_authority_key_identifier(cert) -> bool:
    x509, _, _, _, _ = _crypto()
    try:
        cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier)
        return True
    except x509.ExtensionNotFound:
        return False


def _cert_has_subject_key_identifier(cert) -> bool:
    x509, _, _, _, _ = _crypto()
    try:
        cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
        return True
    except x509.ExtensionNotFound:
        return False


def _cert_covers_hosts(cert, hosts: Iterable[str]) -> bool:
    san_dns, san_ips = _cert_san_names(cert)
    for host in _normalise_hosts(hosts):
        try:
            ip = str(ipaddress.ip_address(host))
        except ValueError:
            if host not in san_dns:
                return False
        else:
            if ip not in san_ips:
                return False
    return True


def generate_ca(ca_dir: Path) -> Tuple[Path, Path]:
    """Generate a CA key + self-signed cert and save to ca_dir.

    Returns (ca_cert_path, ca_key_path).  Skips generation if both files
    already exist (so Roblox doesn't need to re-import the cert on every run).
    """
    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_cert_path = ca_dir / 'ca.crt'
    ca_key_path = ca_dir / 'ca.key'

    if ca_cert_path.exists() and ca_key_path.exists():
        try:
            x509, _, _, serialization, _ = _crypto()
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            existing_ca = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
            existing_key = load_pem_private_key(ca_key_path.read_bytes(), password=None)

            key_ok = _cert_matches_private_key(existing_ca, existing_key, serialization)
            time_ok = _cert_valid_for(existing_ca, CA_MIN_REMAINING_DAYS)
            ski_ok = _cert_has_subject_key_identifier(existing_ca)
            if key_ok and time_ok and ski_ok:
                return ca_cert_path, ca_key_path

            logger.warning('Existing Fleasion CA is stale or mismatched; regenerating')
        except Exception as exc:
            logger.warning('Failed to load existing Fleasion CA; regenerating (%s)', exc)

    x509, NameOID, hashes, serialization, rsa = _crypto()

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'Fleasion Proxy CA'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Fleasion'),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    not_valid_before = now - datetime.timedelta(minutes=NOT_VALID_BEFORE_SKEW_MINUTES)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_valid_before)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
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

    x509, NameOID, hashes, serialization, rsa = _crypto()
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    # Load CA once here so we can validate cached leaf cert issuer before reusing it.
    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())

    if cert_path.exists() and key_path.exists():
        try:
            cached_leaf = x509.load_pem_x509_certificate(cert_path.read_bytes())
            cached_key = load_pem_private_key(key_path.read_bytes(), password=None)

            cn_values = cached_leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            cn_ok = bool(cn_values and cn_values[0].value == host)

            try:
                san = cached_leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                san_dns = set(san.get_values_for_type(x509.DNSName))
                san_ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
            except x509.ExtensionNotFound:
                san_dns = set()
                san_ips = set()

            san_ok = host in san_dns or host in san_ips
            issuer_ok = cached_leaf.issuer == ca_cert.subject
            time_ok = _cert_valid_for(cached_leaf, LEAF_MIN_REMAINING_DAYS)
            key_ok = _cert_matches_private_key(cached_leaf, cached_key, serialization)
            ski_ok = _cert_has_subject_key_identifier(cached_leaf)
            aki_ok = _cert_has_authority_key_identifier(cached_leaf)

            try:
                signature_ok = _leaf_signed_by_ca(cached_leaf, ca_cert, rsa)
            except Exception:
                signature_ok = False

            if cn_ok and san_ok and issuer_ok and time_ok and key_ok and ski_ok and aki_ok and signature_ok:
                return cert_path, key_path

            logger.warning('Cached leaf cert for %s is stale or mismatched; regenerating', host)
        except Exception as exc:
            logger.warning('Failed to validate cached leaf cert for %s; regenerating (%s)', host, exc)

    # Load CA
    ca_key = load_pem_private_key(ca_key_path.read_bytes(), password=None)

    if not _cert_matches_private_key(ca_cert, ca_key, serialization):
        raise ValueError('CA cert/key pair is mismatched')

    # Generate a fresh key for this leaf cert
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    not_valid_before = now - datetime.timedelta(minutes=NOT_VALID_BEFORE_SKEW_MINUTES)

    san_entries = _san_entries_for_hosts([host])

    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_valid_before)
        .not_valid_after(now + datetime.timedelta(days=LEAF_CERT_VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=False,
                crl_sign=False,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
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


def generate_multi_host_cert(
    cert_name: str,
    hosts: Iterable[str],
    ca_cert_path: Path,
    ca_key_path: Path,
    ca_dir: Path,
) -> Tuple[Path, Path]:
    """Generate a reusable leaf certificate whose SAN covers every host."""
    ca_dir.mkdir(parents=True, exist_ok=True)
    normalized_hosts = _normalise_hosts(hosts)
    safe_name = _safe_cert_filename(cert_name)
    cert_path = ca_dir / f'{safe_name}.crt'
    key_path = ca_dir / f'{safe_name}.key'

    x509, NameOID, hashes, serialization, rsa = _crypto()
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())

    if cert_path.exists() and key_path.exists():
        try:
            cached_leaf = x509.load_pem_x509_certificate(cert_path.read_bytes())
            cached_key = load_pem_private_key(key_path.read_bytes(), password=None)

            cn_values = cached_leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            cn_ok = bool(cn_values and cn_values[0].value == cert_name)
            issuer_ok = cached_leaf.issuer == ca_cert.subject
            time_ok = _cert_valid_for(cached_leaf, LEAF_MIN_REMAINING_DAYS)
            key_ok = _cert_matches_private_key(cached_leaf, cached_key, serialization)
            san_ok = _cert_covers_hosts(cached_leaf, normalized_hosts)
            eku_ok = _cert_allows_server_auth(cached_leaf)
            ski_ok = _cert_has_subject_key_identifier(cached_leaf)
            aki_ok = _cert_has_authority_key_identifier(cached_leaf)

            try:
                signature_ok = _leaf_signed_by_ca(cached_leaf, ca_cert, rsa)
            except Exception:
                signature_ok = False

            if cn_ok and issuer_ok and time_ok and key_ok and san_ok and eku_ok and ski_ok and aki_ok and signature_ok:
                return cert_path, key_path

            logger.warning('Cached multi-host cert %s is stale or mismatched; regenerating', cert_name)
        except Exception as exc:
            logger.warning('Failed to validate cached multi-host cert %s; regenerating (%s)', cert_name, exc)

    ca_key = load_pem_private_key(ca_key_path.read_bytes(), password=None)
    if not _cert_matches_private_key(ca_cert, ca_key, serialization):
        raise ValueError('CA cert/key pair is mismatched')

    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    not_valid_before = now - datetime.timedelta(minutes=NOT_VALID_BEFORE_SKEW_MINUTES)

    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cert_name)]))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_valid_before)
        .not_valid_after(now + datetime.timedelta(days=LEAF_CERT_VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName(_san_entries_for_hosts(normalized_hosts)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=False,
                crl_sign=False,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
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
    logger.debug('Generated multi-host cert %s for %s', cert_name, ', '.join(normalized_hosts))
    return cert_path, key_path


def get_ca_pem(ca_cert_path: Path) -> str:
    """Return the CA certificate as a PEM string."""
    return ca_cert_path.read_text(encoding='utf-8')
