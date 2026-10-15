"""Detecting Roblox refusing Fleasion's certificate."""

import datetime
import socket
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from fleasion.proxy import master as proxy_master
from fleasion.proxy.ca_rejection import CertificateRejectionMonitor, is_certificate_rejection


def _self_signed(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'assetdelivery.roblox.com')])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName('assetdelivery.roblox.com')]), False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def test_real_client_refusal_is_recognised(tmp_path):
    cert_path, key_path = _self_signed(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_path, key_path)
    listener = socket.create_server(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server_error: list[BaseException] = []

    def serve():
        conn, _addr = listener.accept()
        try:
            server_ctx.wrap_socket(conn, server_side=True)
        except ssl.SSLError as exc:
            server_error.append(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=serve)
    thread.start()
    # Like Roblox with its embedded bundle: a client that does not trust our CA
    client_ctx = ssl.create_default_context()
    with socket.create_connection(('127.0.0.1', port)) as raw:
        try:
            client_ctx.wrap_socket(raw, server_hostname='assetdelivery.roblox.com')
        except ssl.SSLCertVerificationError:
            pass
    thread.join(timeout=5)
    listener.close()

    assert server_error, 'server handshake should fail when the client refuses the cert'
    assert is_certificate_rejection(server_error[0])


def test_other_failures_are_not_rejections():
    assert not is_certificate_rejection(ConnectionResetError())
    assert not is_certificate_rejection(OSError('broken pipe'))
    assert not is_certificate_rejection(
        ssl.SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version')
    )


def test_monitor_needs_several_rejections_and_reports_once():
    now = [0.0]
    reports = []
    monitor = CertificateRejectionMonitor(
        reports.append, threshold=3, window=60, clock=lambda: now[0]
    )

    monitor.record('assetdelivery.roblox.com')
    now[0] = 30
    monitor.record('fts.rbxcdn.com')
    assert reports == []
    now[0] = 50
    monitor.record('assetdelivery.roblox.com')
    assert reports == [['assetdelivery.roblox.com', 'fts.rbxcdn.com']]

    monitor.record('assetdelivery.roblox.com')
    assert len(reports) == 1
    assert monitor.reported


def test_monitor_forgets_old_rejections():
    now = [0.0]
    reports = []
    monitor = CertificateRejectionMonitor(
        reports.append, threshold=2, window=10, clock=lambda: now[0]
    )
    monitor.record('a')
    now[0] = 30
    monitor.record('b')
    assert reports == []
    now[0] = 35
    monitor.record('c')
    assert reports == [['b', 'c']]


def test_proxy_only_watches_on_windows_and_macos(monkeypatch):
    events = []
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    monkeypatch.setattr(
        proxy, '_emit_proxy_start_error', lambda code, details: events.append((code, details))
    )
    monkeypatch.setattr(proxy_master.log_buffer, 'log', lambda *_args: None)

    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', False)
    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    assert proxy._certificate_rejection_recorder() is None

    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    record = proxy._certificate_rejection_recorder()
    for _ in range(3):
        record('assetdelivery.roblox.com')
    assert events == [('roblox_rejected_certificate', {'hosts': ['assetdelivery.roblox.com']})]
