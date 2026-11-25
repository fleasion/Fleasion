"""Fail-closed alert and close-notify behavior for synthetic TLS sessions."""

from __future__ import annotations

import pytest

from fleasion.proxy.tls_records import (
    CipherSuite,
    TLS13RecordCipher,
    TLS13RecordSession,
    TLSRecordError,
)

_SUITE = CipherSuite.AES_128_GCM_SHA256
_CLIENT_SECRET = b'C' * 32
_SERVER_SECRET = b'S' * 32


def _peers() -> tuple[TLS13RecordSession, TLS13RecordSession]:
    return (
        TLS13RecordSession(
            inbound_secret=_SERVER_SECRET, outbound_secret=_CLIENT_SECRET, suite=_SUITE
        ),
        TLS13RecordSession(
            inbound_secret=_CLIENT_SECRET, outbound_secret=_SERVER_SECRET, suite=_SUITE
        ),
    )


@pytest.mark.parametrize('invalid_alert', [b'', b'\x01', b'\x01\x00\x00'])
def test_invalid_alert_length_rejected_at_encryption(invalid_alert: bytes) -> None:
    cipher = TLS13RecordCipher(_CLIENT_SECRET, _SUITE)
    with pytest.raises(ValueError, match='alert'):
        cipher.encrypt_record(invalid_alert, content_type=21)
    assert cipher.sequence == 0


def test_close_notify_blocks_further_application_from_that_direction() -> None:
    client, server = _peers()
    wire = client.send_close_notify()
    received = server.receive(wire)
    assert received[0].content == b'\x01\x00'
    assert server.inbound_closed
    assert client.outbound_closed
    with pytest.raises(TLSRecordError, match='closed'):
        client.send_application(b'invalid-after-close')
    with pytest.raises(TLSRecordError, match='closed'):
        client.send_key_update()
    response = server.send_close_notify()
    assert client.receive(response)[0].content == b'\x01\x00'
    assert client.inbound_closed
    with pytest.raises(TLSRecordError, match='closed'):
        server.receive(client.outbound.encrypt_record(b'bad-after-close'))
    with pytest.raises(TLSRecordError, match='failed'):
        server.send_application(b'cannot-recover-after-close-violation')
    with pytest.raises(TLSRecordError, match='closed'):
        client.receive(server.outbound.encrypt_record(b'bad-after-close'))


def test_fatal_alert_terminates_the_receiving_session() -> None:
    client, server = _peers()
    alert = client.outbound.encrypt_record(b'\x02\x28', content_type=21)
    with pytest.raises(TLSRecordError, match='alert'):
        server.receive(alert)
    assert server.inbound.failed


def test_close_notify_and_following_application_in_same_tcp_packet_is_rejected() -> None:
    client, server = _peers()
    wire = client.send_close_notify() + client.outbound.encrypt_record(b'after-close')
    with pytest.raises(TLSRecordError, match='close_notify'):
        server.receive(wire)
    assert server.inbound_closed


def test_close_notify_waits_for_key_update_response() -> None:
    client, server = _peers()
    update = client.send_key_update(request_peer_update=True)
    assert server.receive(update)[0].key_update_requested
    with pytest.raises(TLSRecordError, match='KeyUpdate'):
        server.send_close_notify()
    response = server.send_key_update()
    assert client.receive(response)[0].content_type == 22
    assert server.send_close_notify()


def test_peer_responds_to_close_notify_without_forcing_full_connection_close() -> None:
    client, server = _peers()
    assert server.receive(client.send_close_notify())[0].content_type == 21
    assert server.inbound_closed and not server.outbound_closed
    assert (
        client.receive(server.send_application(b'already-queued'))[0].content == b'already-queued'
    )
    assert client.receive(server.send_close_notify())[0].content_type == 21
    assert client.inbound_closed
    client.finish()
    server.finish()
