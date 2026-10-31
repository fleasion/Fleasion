"""Controlled TLS 1.3 post-handshake message fragmentation and key epoch tests."""

from __future__ import annotations

import pytest

from fleasion.proxy.tls_records import (
    CipherSuite,
    TLS13RecordCipher,
    TLS13RecordSession,
    TLSRecordError,
)

_SUITE = CipherSuite.AES_128_GCM_SHA256
_SECRET = b'Z' * 32


def _ticket(*, serial: int = 4) -> bytes:
    """Encode a synthetic NewSessionTicket containing no real resumption secret."""
    body = (
        (7200).to_bytes(4, 'big')
        + serial.to_bytes(4, 'big')
        + b'\x01\x09'
        + b'\x00\x04fake'
        + b'\x00\x00'
    )
    return b'\x04' + len(body).to_bytes(3, 'big') + body


def _pair() -> tuple[TLS13RecordCipher, TLS13RecordCipher]:
    return TLS13RecordCipher(_SECRET, _SUITE), TLS13RecordCipher(_SECRET, _SUITE)


def test_complete_new_session_ticket_followed_by_application_record() -> None:
    sender, receiver = _pair()
    ticket = _ticket()
    result = receiver.decrypt_record(sender.encrypt_record(ticket, content_type=22))
    assert result.content_type == 22
    assert result.content == ticket
    assert not result.key_update_requested
    assert receiver.generation == 0
    assert receiver.decrypt_record(sender.encrypt_record(b'next')).content == b'next'


@pytest.mark.parametrize('split', [1, 2, 3, 4, 7, 9, 14])
def test_fragmented_new_session_ticket_can_span_records(split: int) -> None:
    sender, receiver = _pair()
    ticket = _ticket()
    first = sender.encrypt_record(ticket[:split], content_type=22)
    second = sender.encrypt_record(ticket[split:], content_type=22)
    assert receiver.decrypt_record(first).content == ticket[:split]
    assert receiver.decrypt_record(second).content == ticket[split:]
    assert receiver.decrypt_record(sender.encrypt_record(b'ok')).content == b'ok'
    assert receiver.generation == 0


def test_multiple_tickets_coalesced_in_one_record() -> None:
    sender, receiver = _pair()
    both = _ticket() + _ticket(serial=5)
    assert receiver.decrypt_record(sender.encrypt_record(both, content_type=22)).content == both
    assert receiver.generation == 0


def test_ticket_and_key_update_coalesced_at_record_boundary() -> None:
    sender, receiver = _pair()
    wire = sender.encrypt_record(_ticket() + b'\x18\x00\x00\x01\x00', content_type=22)
    result = receiver.decrypt_record(wire)
    assert result.content_type == 22
    assert receiver.generation == 1
    assert receiver.sequence == 0


def test_fragmented_ticket_preceding_key_update() -> None:
    sender, receiver = _pair()
    ticket = _ticket()
    receiver.decrypt_record(sender.encrypt_record(ticket[:8], content_type=22))
    result = receiver.decrypt_record(
        sender.encrypt_record(ticket[8:] + b'\x18\x00\x00\x01\x01', content_type=22)
    )
    assert result.key_update_requested
    assert receiver.generation == 1


def test_fragmented_key_update_is_rejected() -> None:
    sender, receiver = _pair()
    with pytest.raises(TLSRecordError, match='KeyUpdate.*record'):
        receiver.decrypt_record(sender.encrypt_record(b'\x18\x00', content_type=22))
    assert receiver.failed


def test_ticket_fragment_cannot_be_interleaved_with_application() -> None:
    sender, receiver = _pair()
    receiver.decrypt_record(sender.encrypt_record(_ticket()[:8], content_type=22))
    with pytest.raises(TLSRecordError, match='fragmented handshake'):
        receiver.decrypt_record(sender.encrypt_record(b'interleaved'))
    assert receiver.failed


def test_unsupported_post_handshake_message_rejected() -> None:
    sender, receiver = _pair()
    with pytest.raises(TLSRecordError, match='post-handshake'):
        receiver.decrypt_record(sender.encrypt_record(b'\x0d\x00\x00\x00', content_type=22))
    assert receiver.failed


def test_post_handshake_buffer_limit_rejects_large_declared_length() -> None:
    sender, receiver = _pair()
    too_large = b'\x04' + (1 << 20).to_bytes(3, 'big')
    with pytest.raises(TLSRecordError, match='exceeds'):
        receiver.decrypt_record(sender.encrypt_record(too_large, content_type=22))
    assert receiver.failed


def test_empty_handshake_and_alert_payloads_are_rejected() -> None:
    sender, receiver = _pair()
    with pytest.raises(ValueError, match='empty'):
        sender.encrypt_record(b'', content_type=22)
    with pytest.raises(ValueError, match='empty'):
        sender.encrypt_record(b'', content_type=21)
    assert sender.sequence == receiver.sequence == 0


def test_duplex_session_responds_to_key_update_after_ticket() -> None:
    client = TLS13RecordSession(inbound_secret=_SECRET, outbound_secret=b'X' * 32, suite=_SUITE)
    server = TLS13RecordCipher(_SECRET, _SUITE)
    ticket = server.encrypt_record(_ticket(), content_type=22)
    assert client.receive(ticket)[0].content_type == 22
    update = server.send_key_update(request_peer_update=True)
    assert client.receive(update)[0].key_update_requested
    assert client.key_update_response_pending
    assert client.send_key_update()
    assert not client.key_update_response_pending


@pytest.mark.parametrize('split', [1, 5, 9, 14])
def test_incomplete_ticket_is_rejected_at_stream_eof(split: int) -> None:
    sender = TLS13RecordCipher(_SECRET, _SUITE)
    session = TLS13RecordSession(
        inbound_secret=_SECRET,
        outbound_secret=b'O' * 32,
        suite=_SUITE,
    )
    assert session.receive(sender.encrypt_record(_ticket()[:split], content_type=22))
    with pytest.raises(TLSRecordError, match='unfinished.*handshake'):
        session.finish()
    assert session.inbound.failed


def test_invalid_ticket_with_truncated_identity_rejected() -> None:
    sender, receiver = _pair()
    ticket = _ticket()
    malformed = ticket[:-5]
    malformed = malformed[:1] + (len(malformed) - 4).to_bytes(3, 'big') + malformed[4:]
    with pytest.raises(TLSRecordError, match='NewSessionTicket'):
        receiver.decrypt_record(sender.encrypt_record(malformed, content_type=22))
    assert receiver.failed


def test_ticket_with_excessive_lifetime_rejected() -> None:
    sender, receiver = _pair()
    ticket = bytearray(_ticket())
    ticket[4:8] = (604801).to_bytes(4, 'big')
    with pytest.raises(TLSRecordError, match='NewSessionTicket'):
        receiver.decrypt_record(sender.encrypt_record(bytes(ticket), content_type=22))
    assert receiver.failed


def test_session_framer_handles_fragmented_ticket_and_key_update_in_one_tcp_chunk() -> None:
    sender = TLS13RecordCipher(_SECRET, _SUITE)
    session = TLS13RecordSession(
        inbound_secret=_SECRET,
        outbound_secret=b'O' * 32,
        suite=_SUITE,
    )
    ticket = _ticket()
    chunks = (
        sender.encrypt_record(ticket[:5], content_type=22)
        + sender.encrypt_record(ticket[5:], content_type=22)
        + sender.send_key_update(request_peer_update=True)
        + sender.encrypt_record(b'after-key-update')
    )
    results = session.receive(chunks)
    assert len(results) == 4
    assert results[2].key_update_requested
    assert results[3].content == b'after-key-update'
    assert session.inbound.generation == 1
    assert session.key_update_response_pending
    assert session.send_key_update()
    session.finish()


def test_ticket_extension_vector_length_must_match_message_size() -> None:
    sender, receiver = _pair()
    ticket = bytearray(_ticket())
    ticket[-2:] = b'\x00\x01'
    with pytest.raises(TLSRecordError, match='NewSessionTicket'):
        receiver.decrypt_record(sender.encrypt_record(bytes(ticket), content_type=22))
    assert receiver.failed


def test_key_update_cannot_precede_another_handshake_message_in_same_record() -> None:
    sender, receiver = _pair()
    key_update = b'\x18\x00\x00\x01\x00'
    with pytest.raises(TLSRecordError, match='record boundary'):
        receiver.decrypt_record(sender.encrypt_record(key_update + _ticket(), content_type=22))
    assert receiver.failed


def test_post_handshake_ticket_can_end_at_record_boundary_before_key_update() -> None:
    sender, receiver = _pair()
    receiver.decrypt_record(sender.encrypt_record(_ticket(), content_type=22))
    update = sender.send_key_update(request_peer_update=True)
    result = receiver.decrypt_record(update)
    assert result.key_update_requested
    assert receiver.generation == 1
