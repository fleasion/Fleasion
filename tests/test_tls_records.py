"""TLS 1.3 application record conformance with public and synthetic test inputs."""

from __future__ import annotations

import itertools

import pytest

from fleasion.proxy.tls_records import (
    CipherSuite,
    EncryptedRecordFramer,
    TLS13RecordCipher,
    TLS13RecordSession,
    TLSRecordError,
)

_TEST_SECRET_256 = bytes(range(32))
_TEST_SECRET_384 = bytes(range(48))


def _pair(suite: CipherSuite) -> tuple[TLS13RecordCipher, TLS13RecordCipher]:
    secret = _TEST_SECRET_384 if suite is CipherSuite.AES_256_GCM_SHA384 else _TEST_SECRET_256
    return TLS13RecordCipher(secret, suite), TLS13RecordCipher(secret, suite)


@pytest.mark.parametrize('suite', list(CipherSuite))
def test_roundtrip_across_cipher_suites(suite: CipherSuite) -> None:
    sender, receiver = _pair(suite)
    for data in (b'', b'hello', bytes(range(256)) * 4):
        encrypted = sender.encrypt_record(data, padding=12)
        assert encrypted[:3] == b'\x17\x03\x03'
        assert encrypted[5:] != data
        decrypted = receiver.decrypt_record(encrypted)
        assert decrypted.content_type == 23
        assert decrypted.content == data
    assert sender.sequence == receiver.sequence == 3


def test_rfc8448_client_application_key_and_iv() -> None:
    # RFC 8448 section 3: client application-data traffic secret and expand values
    traffic_secret = bytes.fromhex(
        '9e40646ce79a7f9dc05af8889bce6552875afa0b06df0087f792ebb7c17504a5'
    )
    cipher = TLS13RecordCipher(traffic_secret, CipherSuite.AES_128_GCM_SHA256)
    assert cipher._expand_label(b'key', 16) == bytes.fromhex(  # pyright: ignore[reportPrivateUsage]
        '17422dda596ed5d9acd890e3c63f5051'
    )
    assert cipher._expand_label(b'iv', 12) == bytes.fromhex(  # pyright: ignore[reportPrivateUsage]
        '5b78923dee08579033e523d9'
    )


@pytest.mark.parametrize('suite', list(CipherSuite))
def test_tcp_framer_handles_every_split_position(suite: CipherSuite) -> None:
    sender, _receiver = _pair(suite)
    first = sender.encrypt_record(b'one')
    second = sender.encrypt_record(b'two', padding=17)
    combined = first + second
    for split in range(len(combined) + 1):
        framer = EncryptedRecordFramer()
        records = framer.feed(combined[:split]) + framer.feed(combined[split:])
        assert records == [first, second]
        assert framer.buffered_bytes == 0
        framer.finish()


def test_arbitrary_tcp_fragmentation_reassembles_ordered_records() -> None:
    sender, receiver = _pair(CipherSuite.AES_256_GCM_SHA384)
    payloads = [bytes([n]) * (n * 13) for n in range(1, 16)]
    wire = b''.join(sender.encrypt_record(payload) for payload in payloads)
    framer = EncryptedRecordFramer()
    results: list[bytes] = []
    sizes = itertools.cycle((1, 2, 7, 19, 3, 41, 5))
    position = 0
    while position < len(wire):
        width = next(sizes)
        results.extend(framer.feed(wire[position : position + width]))
        position += width
    assert [receiver.decrypt_record(record).content for record in results] == payloads
    framer.finish()


def test_large_application_data_splits_on_record_boundary() -> None:
    sender, receiver = _pair(CipherSuite.AES_128_GCM_SHA256)
    original = b'abcd' * 20000
    wire = sender.encrypt_application(original)
    records = EncryptedRecordFramer().feed(wire)
    assert len(records) == 5
    assert b''.join(receiver.decrypt_record(record).content for record in records) == original


def test_tampered_ciphertext_fails_closed_and_never_advances() -> None:
    sender, receiver = _pair(CipherSuite.CHACHA20_POLY1305_SHA256)
    record = sender.encrypt_record(b'authentic')
    tampered = bytearray(record)
    tampered[-1] ^= 1
    with pytest.raises(TLSRecordError, match='authentication'):
        receiver.decrypt_record(bytes(tampered))
    assert receiver.sequence == 0
    assert receiver.failed
    with pytest.raises(TLSRecordError, match='cannot be reused'):
        receiver.decrypt_record(record)


def test_distinct_traffic_secrets_cannot_decrypt_same_record() -> None:
    sender, _receiver = _pair(CipherSuite.AES_128_GCM_SHA256)
    receiver = TLS13RecordCipher(b'x' * 32, CipherSuite.AES_128_GCM_SHA256)
    with pytest.raises(TLSRecordError, match='authentication'):
        receiver.decrypt_record(sender.encrypt_record(b'private'))


def test_replayed_tls_record_is_rejected() -> None:
    sender, receiver = _pair(CipherSuite.AES_128_GCM_SHA256)
    record = sender.encrypt_record(b'first')
    assert receiver.decrypt_record(record).content == b'first'
    with pytest.raises(TLSRecordError, match='authentication'):
        receiver.decrypt_record(record)


@pytest.mark.parametrize('mutated', [b'\x16', b'\x15', b'\x14'])
def test_non_application_outer_type_rejected(mutated: bytes) -> None:
    sender, receiver = _pair(CipherSuite.AES_128_GCM_SHA256)
    record = sender.encrypt_record(b'ciphertext')
    malformed = mutated + record[1:]
    with pytest.raises(TLSRecordError, match='framing'):
        receiver.decrypt_record(malformed)
    with pytest.raises(TLSRecordError, match='header'):
        EncryptedRecordFramer().feed(malformed)


def test_invalid_legacy_version_and_length_are_rejected() -> None:
    sender, receiver = _pair(CipherSuite.AES_128_GCM_SHA256)
    record = bytearray(sender.encrypt_record(b'test'))
    record[2] ^= 1
    with pytest.raises(TLSRecordError, match='framing'):
        receiver.decrypt_record(bytes(record))
    too_large = b'\x17\x03\x03\xff\xff'
    framer = EncryptedRecordFramer()
    with pytest.raises(TLSRecordError, match='header'):
        framer.feed(too_large)
    with pytest.raises(TLSRecordError, match='cannot be reused'):
        framer.feed(sender.encrypt_record(b'next'))


def test_partial_record_is_rejected_at_eof() -> None:
    sender, _receiver = _pair(CipherSuite.AES_128_GCM_SHA256)
    wire = sender.encrypt_record(b'longer-data')
    framer = EncryptedRecordFramer()
    assert framer.feed(wire[:-2]) == []
    with pytest.raises(TLSRecordError, match='truncated'):
        framer.finish()


def test_traffic_secret_wrong_length_fails_early() -> None:
    with pytest.raises(ValueError, match='hash length'):
        TLS13RecordCipher(b'bad', CipherSuite.AES_256_GCM_SHA384)


def test_body_padding_and_maximum_length_validation() -> None:
    sender, receiver = _pair(CipherSuite.AES_128_GCM_SHA256)
    record = sender.encrypt_record(b'content', padding=255)
    assert receiver.decrypt_record(record).content == b'content'
    with pytest.raises(ValueError, match='length'):
        sender.encrypt_record(b'x' * ((1 << 14) + 1))
    with pytest.raises(ValueError, match='length'):
        sender.encrypt_record(b'', padding=256)


def test_sequence_overflow_fails_closed() -> None:
    sender, _receiver = _pair(CipherSuite.AES_128_GCM_SHA256)
    sender.sequence = 1 << 64
    with pytest.raises(TLSRecordError, match='exhausted'):
        sender.encrypt_record(b'too-late')


@pytest.mark.parametrize('suite', list(CipherSuite))
def test_post_handshake_key_update_resets_record_sequence(suite: CipherSuite) -> None:
    sender, receiver = _pair(suite)
    first = sender.encrypt_record(b'epoch-0')
    assert receiver.decrypt_record(first).content == b'epoch-0'
    update = sender.send_key_update()
    assert sender.generation == 1 and sender.sequence == 0
    result = receiver.decrypt_record(update)
    assert result.content_type == 22
    assert result.content == b'\x18\x00\x00\x01\x00'
    assert receiver.generation == 1 and receiver.sequence == 0
    second = sender.encrypt_record(b'epoch-1')
    assert receiver.decrypt_record(second).content == b'epoch-1'
    assert sender.sequence == receiver.sequence == 1


def test_key_update_request_requires_response_before_new_application_data() -> None:
    outbound, inbound = _pair(CipherSuite.AES_128_GCM_SHA256)
    message = outbound.send_key_update(request_peer_update=True)
    decoded = inbound.decrypt_record(message)
    assert decoded.content.endswith(b'\x01')
    assert decoded.key_update_requested
    # The response belongs to the endpoint's independent sending direction
    response_sender = TLS13RecordCipher(b'z' * 32, CipherSuite.AES_128_GCM_SHA256)
    update = response_sender.send_key_update()
    assert response_sender.generation == 1
    assert len(update) > 20


def test_unsupported_post_handshake_message_fails_closed() -> None:
    sender, receiver = _pair(CipherSuite.AES_128_GCM_SHA256)
    record = sender.encrypt_record(b'\x04\x00\x00\x00', content_type=22)
    with pytest.raises(TLSRecordError, match='Only complete'):
        receiver.decrypt_record(record)
    assert receiver.failed


def test_invalid_key_update_byte_fails_closed() -> None:
    sender, receiver = _pair(CipherSuite.AES_128_GCM_SHA256)
    record = sender.encrypt_record(b'\x18\x00\x00\x01\x02', content_type=22)
    with pytest.raises(TLSRecordError, match='KeyUpdate request byte'):
        receiver.decrypt_record(record)
    assert receiver.failed


def test_duplex_key_update_response_uses_opposite_direction() -> None:
    suite = CipherSuite.AES_128_GCM_SHA256
    client_secret, server_secret = b'c' * 32, b's' * 32
    client = TLS13RecordSession(
        inbound_secret=server_secret, outbound_secret=client_secret, suite=suite
    )
    server = TLS13RecordSession(
        inbound_secret=client_secret, outbound_secret=server_secret, suite=suite
    )
    wire = client.send_application(b'hello')
    assert [item.content for item in server.receive(wire)] == [b'hello']
    key_update = client.send_key_update(request_peer_update=True)
    assert server.receive(key_update)[0].content == b'\x18\x00\x00\x01\x01'
    assert server.key_update_response_pending
    with pytest.raises(TLSRecordError, match='KeyUpdate'):
        server.send_application(b'not yet')
    with pytest.raises(TLSRecordError, match='must not request'):
        server.send_key_update(request_peer_update=True)
    response = server.send_key_update()
    assert not server.key_update_response_pending
    assert client.receive(response)[0].content == b'\x18\x00\x00\x01\x00'
    assert client.inbound.generation == client.outbound.generation == 1
    assert server.inbound.generation == server.outbound.generation == 1
    assert [item.content for item in client.receive(server.send_application(b'back'))] == [b'back']
    assert [item.content for item in server.receive(client.send_application(b'forward'))] == [
        b'forward'
    ]
    client.finish()
    server.finish()


def test_maximum_tls_plaintext_record_is_supported_without_padding() -> None:
    sender, receiver = _pair(CipherSuite.AES_128_GCM_SHA256)
    data = b'a' * (1 << 14)
    wire = sender.encrypt_record(data)
    assert len(wire) == 5 + len(data) + 1 + 16
    assert receiver.decrypt_record(wire).content == data
    with pytest.raises(ValueError, match='length'):
        sender.encrypt_record(data, padding=1)


def test_received_key_update_is_independent_of_outbound_secret() -> None:
    inbound_secret = b'i' * 32
    outbound_secret = b'o' * 32
    session = TLS13RecordSession(
        inbound_secret=inbound_secret,
        outbound_secret=outbound_secret,
        suite=CipherSuite.AES_128_GCM_SHA256,
    )
    sender = TLS13RecordCipher(inbound_secret, CipherSuite.AES_128_GCM_SHA256)
    session.receive(sender.send_key_update())
    assert session.inbound.generation == 1
    assert session.outbound.generation == 0
    assert not session.key_update_response_pending
    assert session.outbound.sequence == 0


def test_record_session_rejects_truncated_ciphertext_on_finish() -> None:
    sender = TLS13RecordCipher(_TEST_SECRET_256, CipherSuite.AES_128_GCM_SHA256)
    session = TLS13RecordSession(
        inbound_secret=_TEST_SECRET_256,
        outbound_secret=b'o' * 32,
        suite=CipherSuite.AES_128_GCM_SHA256,
    )
    record = sender.encrypt_record(b'record-cut')
    assert session.receive(record[:-1]) == []
    with pytest.raises(TLSRecordError, match='truncated'):
        session.finish()
