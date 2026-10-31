"""Terminal TLS record failures must never permit the same session to resume."""

from __future__ import annotations

import pytest

from fleasion.proxy.tls_records import (
    CipherSuite,
    TLS13RecordCipher,
    TLS13RecordSession,
    TLSRecordError,
)

_SUITE = CipherSuite.AES_128_GCM_SHA256
_CLIENT = b'C' * 32
_SERVER = b'S' * 32


def _sessions() -> tuple[TLS13RecordSession, TLS13RecordSession]:
    return (
        TLS13RecordSession(inbound_secret=_SERVER, outbound_secret=_CLIENT, suite=_SUITE),
        TLS13RecordSession(inbound_secret=_CLIENT, outbound_secret=_SERVER, suite=_SUITE),
    )


@pytest.mark.parametrize('change', ['type', 'version', 'truncated', 'length'])
def test_direct_record_framing_failure_is_terminal(change: str) -> None:
    sender = TLS13RecordCipher(_CLIENT, _SUITE)
    receiver = TLS13RecordCipher(_CLIENT, _SUITE)
    original = sender.encrypt_record(b'message')
    if change == 'type':
        bad = b'\x16' + original[1:]
    elif change == 'version':
        bad = original[:2] + b'\x02' + original[3:]
    elif change == 'length':
        bad = original[:3] + b'\x00\x11' + original[5:]
    else:
        bad = original[:4]
    with pytest.raises(TLSRecordError):
        receiver.decrypt_record(bad)
    assert receiver.failed
    with pytest.raises(TLSRecordError, match='cannot be reused'):
        receiver.decrypt_record(original)


def test_fatal_alert_disables_both_sending_and_receiving() -> None:
    client, server = _sessions()
    fatal = client.outbound.encrypt_record(b'\x02\x28', content_type=21)
    with pytest.raises(TLSRecordError, match='alert'):
        server.receive(fatal)
    with pytest.raises(TLSRecordError, match='failed|terminated'):
        server.send_application(b'nope')
    with pytest.raises(TLSRecordError, match='failed|terminated'):
        server.send_key_update()
    with pytest.raises(TLSRecordError, match='failed|terminated'):
        server.send_close_notify()


def test_authentication_error_disables_both_sending_and_receiving() -> None:
    client, server = _sessions()
    encrypted = bytearray(client.send_application(b'safe'))
    encrypted[-1] ^= 1
    with pytest.raises(TLSRecordError, match='authentication'):
        server.receive(bytes(encrypted))
    with pytest.raises(TLSRecordError, match='failed|terminated'):
        server.send_application(b'cannot continue')


def test_truncated_tcp_record_at_eof_disables_subsequent_session_operations() -> None:
    client, server = _sessions()
    encrypted = client.send_application(b'data')
    assert server.receive(encrypted[:-1]) == []
    with pytest.raises(TLSRecordError, match='truncated'):
        server.finish()
    with pytest.raises(TLSRecordError, match='failed|terminated'):
        server.send_application(b'no continuation')
