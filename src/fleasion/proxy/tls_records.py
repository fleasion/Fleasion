"""TLS 1.3 application-record primitives for controlled, authenticated sessions.

Operates on caller-provided test traffic secrets. Does not perform a TLS
handshake, obtain process secrets, or attach to any production transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

_TLS_HEADER_SIZE = 5
_TLS_LEGACY_VERSION = b'\x03\x03'
_TLS_OUTER_APPLICATION_DATA = 23
_TLS_INNER_HANDSHAKE = 22
_TLS_INNER_ALERT = 21
_TLS_INNER_APPLICATION_DATA = 23
_TLS_MAX_CONTENT = 1 << 14
_TLS_MAX_INNER = _TLS_MAX_CONTENT + 1
_TLS_MAX_CIPHERTEXT = _TLS_MAX_CONTENT + 256
_TLS_TAG_LENGTH = 16
_TLS_MAX_SEQUENCE = 1 << 64
_TLS_KEY_UPDATE = b'\x18\x00\x00\x01'
_TLS_NEW_SESSION_TICKET = 4
_TLS_MAX_POST_HANDSHAKE_MESSAGE = 256 * 1024


class TLSRecordError(ValueError):
    """A record cannot be framed, authenticated or decoded safely."""


class CipherSuite(StrEnum):
    AES_128_GCM_SHA256 = 'TLS_AES_128_GCM_SHA256'
    AES_256_GCM_SHA384 = 'TLS_AES_256_GCM_SHA384'
    CHACHA20_POLY1305_SHA256 = 'TLS_CHACHA20_POLY1305_SHA256'

    @property
    def key_length(self) -> int:
        return 32 if self is not CipherSuite.AES_128_GCM_SHA256 else 16

    @property
    def hash_algorithm(self) -> hashes.HashAlgorithm:
        if self is CipherSuite.AES_256_GCM_SHA384:
            return hashes.SHA384()
        return hashes.SHA256()


@dataclass(frozen=True, slots=True)
class DecryptedRecord:
    content_type: int
    content: bytes
    key_update_requested: bool = False


class EncryptedRecordFramer:
    """Extract complete TLSCiphertext records from fragmented TCP chunks."""

    def __init__(self) -> None:
        self._pending = bytearray()
        self._failed = False

    @property
    def buffered_bytes(self) -> int:
        return len(self._pending)

    def feed(self, data: bytes) -> list[bytes]:
        if self._failed:
            msg = 'TLS record framer cannot be reused after malformed input'
            raise TLSRecordError(msg)
        self._pending.extend(data)
        result: list[bytes] = []
        while len(self._pending) >= _TLS_HEADER_SIZE:
            header = bytes(self._pending[:_TLS_HEADER_SIZE])
            length = int.from_bytes(header[3:5], 'big')
            if (
                header[0] != _TLS_OUTER_APPLICATION_DATA
                or header[1:3] != _TLS_LEGACY_VERSION
                or not _TLS_TAG_LENGTH < length <= _TLS_MAX_CIPHERTEXT
            ):
                self._pending.clear()
                self._failed = True
                msg = 'Invalid TLS 1.3 encrypted record header'
                raise TLSRecordError(msg)
            end = _TLS_HEADER_SIZE + length
            if len(self._pending) < end:
                break
            result.append(bytes(self._pending[:end]))
            del self._pending[:end]
        return result

    def finish(self) -> None:
        if self._pending:
            self._pending.clear()
            self._failed = True
            msg = 'TLS record stream ended with a truncated record'
            raise TLSRecordError(msg)


class TLS13RecordCipher:
    """One sending or receiving direction of TLS 1.3 application traffic.

    The caller must provide an already-authenticated, matching application
    traffic secret for this direction. Handshake and early-data epochs are
    intentionally outside this codec. Each instance owns its sequence numbers.
    """

    def __init__(self, traffic_secret: bytes, suite: CipherSuite) -> None:
        self.suite = suite
        self._traffic_secret = bytes(traffic_secret)
        if len(self._traffic_secret) != suite.hash_algorithm.digest_size:
            msg = 'TLS 1.3 traffic secret does not match cipher-suite hash length'
            raise ValueError(msg)
        self.sequence = 0
        self.generation = 0
        self.failed = False
        self._post_handshake_pending = bytearray()
        self._derive_keys()

    def _expand_label(self, label: bytes, length: int) -> bytes:
        full_label = b'tls13 ' + label
        info = length.to_bytes(2, 'big') + bytes([len(full_label)]) + full_label + b'\x00'
        return HKDFExpand(
            algorithm=self.suite.hash_algorithm,
            length=length,
            info=info,
        ).derive(self._traffic_secret)

    def _derive_keys(self) -> None:
        key = self._expand_label(b'key', self.suite.key_length)
        self._iv = self._expand_label(b'iv', 12)
        self._aead = (
            ChaCha20Poly1305(key)
            if self.suite is CipherSuite.CHACHA20_POLY1305_SHA256
            else AESGCM(key)
        )

    def _nonce(self) -> bytes:
        if self.failed:
            msg = 'TLS record cipher cannot be reused after a terminal failure'
            raise TLSRecordError(msg)
        if self.sequence >= _TLS_MAX_SEQUENCE:
            msg = 'TLS record sequence number exhausted'
            raise TLSRecordError(msg)
        sequence_bytes = self.sequence.to_bytes(12, 'big')
        return bytes(iv ^ value for iv, value in zip(self._iv, sequence_bytes, strict=True))

    def encrypt_record(
        self, content: bytes, *, content_type: int = _TLS_INNER_APPLICATION_DATA, padding: int = 0
    ) -> bytes:
        if content_type not in {
            _TLS_INNER_APPLICATION_DATA,
            _TLS_INNER_HANDSHAKE,
            _TLS_INNER_ALERT,
        }:
            msg = 'Unsupported TLS inner content type'
            raise ValueError(msg)
        if content_type in {_TLS_INNER_HANDSHAKE, _TLS_INNER_ALERT} and not content:
            msg = 'TLS handshake and alert records cannot have empty content'
            raise ValueError(msg)
        if content_type == _TLS_INNER_ALERT and len(content) != 2:
            msg = 'TLS alert must contain exactly two bytes'
            raise ValueError(msg)
        if not 0 <= padding <= 255 or len(content) + 1 + padding > _TLS_MAX_INNER:
            msg = 'TLS plaintext record length exceeds supported limit'
            raise ValueError(msg)
        nonce = self._nonce()
        length = len(content) + 1 + padding + _TLS_TAG_LENGTH
        header = (
            bytes([_TLS_OUTER_APPLICATION_DATA]) + _TLS_LEGACY_VERSION + length.to_bytes(2, 'big')
        )
        payload = content + bytes([content_type]) + bytes(padding)
        ciphertext = self._aead.encrypt(nonce, payload, header)
        self.sequence += 1
        return header + ciphertext

    def encrypt_application(self, content: bytes) -> bytes:
        """Split a complete test message into TLS records without message changes."""
        if not content:
            return self.encrypt_record(b'')
        chunk_size = _TLS_MAX_CONTENT
        return b''.join(
            self.encrypt_record(content[offset : offset + chunk_size])
            for offset in range(0, len(content), chunk_size)
        )

    def decrypt_record(self, record: bytes) -> DecryptedRecord:
        if self.failed:
            msg = 'TLS record cipher cannot be reused after a terminal failure'
            raise TLSRecordError(msg)
        if len(record) < _TLS_HEADER_SIZE:
            self.failed = True
            msg = 'Incomplete TLS record header'
            raise TLSRecordError(msg)
        header = record[:_TLS_HEADER_SIZE]
        size = int.from_bytes(header[3:], 'big')
        if (
            header[0] != _TLS_OUTER_APPLICATION_DATA
            or header[1:3] != _TLS_LEGACY_VERSION
            or not _TLS_TAG_LENGTH < size <= _TLS_MAX_CIPHERTEXT
            or size != len(record) - _TLS_HEADER_SIZE
        ):
            self.failed = True
            msg = 'Invalid TLS 1.3 record framing'
            raise TLSRecordError(msg)
        nonce = self._nonce()
        try:
            plaintext = self._aead.decrypt(nonce, record[_TLS_HEADER_SIZE:], header)
        except InvalidTag as exc:
            self.failed = True
            msg = 'TLS record authentication failed'
            raise TLSRecordError(msg) from exc
        self.sequence += 1
        if len(plaintext) > _TLS_MAX_INNER:
            self.failed = True
            msg = 'TLS inner plaintext exceeds protocol limit'
            raise TLSRecordError(msg)
        unpadded = plaintext.rstrip(b'\x00')
        if not unpadded:
            self.failed = True
            msg = 'TLS record has no inner content type'
            raise TLSRecordError(msg)
        content_type = unpadded[-1]
        if content_type not in {
            _TLS_INNER_APPLICATION_DATA,
            _TLS_INNER_ALERT,
            _TLS_INNER_HANDSHAKE,
        }:
            self.failed = True
            msg = 'Invalid TLS inner content type'
            raise TLSRecordError(msg)
        content = unpadded[:-1]
        if content_type != _TLS_INNER_HANDSHAKE and self._post_handshake_pending:
            self.failed = True
            msg = 'TLS application data interleaved with fragmented handshake message'
            raise TLSRecordError(msg)
        if content_type in {_TLS_INNER_HANDSHAKE, _TLS_INNER_ALERT} and not content:
            self.failed = True
            msg = 'Empty TLS handshake or alert record'
            raise TLSRecordError(msg)
        if content_type == _TLS_INNER_ALERT and len(content) != 2:
            self.failed = True
            msg = 'Invalid TLS alert length'
            raise TLSRecordError(msg)
        update_requested = False
        if content_type == _TLS_INNER_HANDSHAKE:
            update_requested = self._receive_post_handshake(content)
        return DecryptedRecord(content_type, content, key_update_requested=update_requested)

    def _receive_post_handshake(self, fragment: bytes) -> bool:
        """Bounded reconstruction of ticket messages and record-aligned KeyUpdate."""
        pending = self._post_handshake_pending
        pending.extend(fragment)
        if len(pending) > _TLS_MAX_POST_HANDSHAKE_MESSAGE + 4:
            self.failed = True
            pending.clear()
            msg = 'TLS post-handshake message exceeds size limit'
            raise TLSRecordError(msg)
        offset = 0
        requested = False
        while offset < len(pending):
            remaining = len(pending) - offset
            kind = pending[offset]
            if kind not in {_TLS_NEW_SESSION_TICKET, _TLS_KEY_UPDATE[0]}:
                self.failed = True
                pending.clear()
                msg = 'Unsupported TLS post-handshake message type'
                raise TLSRecordError(msg)
            if remaining < 4:
                if kind == _TLS_KEY_UPDATE[0]:
                    self.failed = True
                    pending.clear()
                    msg = 'KeyUpdate must fit entirely within its final record'
                    raise TLSRecordError(msg)
                break
            length = int.from_bytes(pending[offset + 1 : offset + 4], 'big')
            if length > _TLS_MAX_POST_HANDSHAKE_MESSAGE:
                self.failed = True
                pending.clear()
                msg = 'TLS post-handshake message exceeds size limit'
                raise TLSRecordError(msg)
            end = offset + 4 + length
            if end > len(pending):
                if kind == _TLS_KEY_UPDATE[0]:
                    self.failed = True
                    pending.clear()
                    msg = 'KeyUpdate must fit entirely within its final record'
                    raise TLSRecordError(msg)
                break
            if kind == _TLS_NEW_SESSION_TICKET:
                ticket = bytes(pending[offset + 4 : end])
                if not self._valid_ticket(ticket):
                    self.failed = True
                    pending.clear()
                    msg = 'Invalid TLS NewSessionTicket structure'
                    raise TLSRecordError(msg)
            if kind == _TLS_KEY_UPDATE[0]:
                if length != 1 or pending[offset + 4] not in {0, 1}:
                    self.failed = True
                    pending.clear()
                    msg = 'Invalid TLS KeyUpdate request byte'
                    raise TLSRecordError(msg)
                if end != len(pending):
                    self.failed = True
                    pending.clear()
                    msg = 'KeyUpdate must end at a TLS record boundary'
                    raise TLSRecordError(msg)
                requested = pending[offset + 4] == 1
                self._next_generation()
            offset = end
        if offset:
            del pending[:offset]
        return requested

    @staticmethod
    def _valid_ticket(data: bytes) -> bool:
        # Ticket lifetime, age, nonce, identity, and extensions are length-prefixed
        if len(data) < 14 or int.from_bytes(data[:4], 'big') > 604800:
            return False
        index = 9 + data[8]
        if index + 2 > len(data):
            return False
        identity_length = int.from_bytes(data[index : index + 2], 'big')
        if not identity_length:
            return False
        index += 2 + identity_length
        if index + 2 > len(data):
            return False
        extensions_length = int.from_bytes(data[index : index + 2], 'big')
        return index + 2 + extensions_length == len(data)

    def finish(self) -> None:
        """Reject a final incomplete post-handshake message at connection EOF."""
        if self._post_handshake_pending:
            self._post_handshake_pending.clear()
            self.failed = True
            msg = 'TLS stream ended with unfinished post-handshake message'
            raise TLSRecordError(msg)

    def _next_generation(self) -> None:
        self._traffic_secret = self._expand_label(
            b'traffic upd', self.suite.hash_algorithm.digest_size
        )
        self.generation += 1
        self.sequence = 0
        self._derive_keys()

    def send_key_update(self, *, request_peer_update: bool = False) -> bytes:
        record = self.encrypt_record(
            _TLS_KEY_UPDATE + bytes([int(request_peer_update)]),
            content_type=_TLS_INNER_HANDSHAKE,
        )
        self._next_generation()
        return record


class TLS13RecordSession:
    """A controlled duplex application epoch with independent traffic keys.

    Read and write secrets MUST be different directions of the same
    authenticated session. This wrapper does not negotiate or authenticate TLS.
    """

    def __init__(
        self, *, inbound_secret: bytes, outbound_secret: bytes, suite: CipherSuite
    ) -> None:
        self.inbound = TLS13RecordCipher(inbound_secret, suite)
        self.outbound = TLS13RecordCipher(outbound_secret, suite)
        self.framer = EncryptedRecordFramer()
        self.key_update_response_pending = False
        self.inbound_closed = False
        self.outbound_closed = False
        self.failed = False

    def _require_healthy(self) -> None:
        if self.failed or self.inbound.failed or self.outbound.failed:
            self.failed = True
            msg = 'TLS record session has failed and cannot continue'
            raise TLSRecordError(msg)

    def receive(self, chunk: bytes) -> list[DecryptedRecord]:
        self._require_healthy()
        try:
            return self._receive_records(chunk)
        except TLSRecordError:
            self.failed = True
            self.inbound.failed = True
            raise

    def _receive_records(self, chunk: bytes) -> list[DecryptedRecord]:
        if self.inbound_closed and chunk:
            msg = 'TLS inbound direction already closed'
            raise TLSRecordError(msg)
        results: list[DecryptedRecord] = []
        for wire_record in self.framer.feed(chunk):
            if self.inbound_closed:
                msg = 'TLS record received after close_notify'
                raise TLSRecordError(msg)
            decoded = self.inbound.decrypt_record(wire_record)
            if decoded.key_update_requested:
                self.key_update_response_pending = True
            if decoded.content_type == _TLS_INNER_ALERT:
                if decoded.content != b'\x01\x00':
                    msg = 'TLS peer sent a fatal or unsupported alert'
                    raise TLSRecordError(msg)
                self.inbound_closed = True
            results.append(decoded)
        return results

    def send_application(self, data: bytes) -> bytes:
        self._require_healthy()
        if self.outbound_closed:
            msg = 'TLS outbound direction already closed'
            raise TLSRecordError(msg)
        if self.key_update_response_pending:
            msg = 'Peer requested KeyUpdate before further application data'
            raise TLSRecordError(msg)
        return self.outbound.encrypt_application(data)

    def send_key_update(self, *, request_peer_update: bool = False) -> bytes:
        self._require_healthy()
        if self.outbound_closed:
            msg = 'TLS outbound direction already closed'
            raise TLSRecordError(msg)
        if self.key_update_response_pending and request_peer_update:
            msg = 'KeyUpdate response must not request another update'
            raise TLSRecordError(msg)
        record = self.outbound.send_key_update(request_peer_update=request_peer_update)
        self.key_update_response_pending = False
        return record

    def send_close_notify(self) -> bytes:
        """Close the sending direction with an authenticated TLS alert."""
        self._require_healthy()
        if self.outbound_closed:
            msg = 'TLS outbound direction already closed'
            raise TLSRecordError(msg)
        if self.key_update_response_pending:
            msg = 'TLS KeyUpdate response must precede close_notify'
            raise TLSRecordError(msg)
        record = self.outbound.encrypt_record(b'\x01\x00', content_type=_TLS_INNER_ALERT)
        self.outbound_closed = True
        return record

    def finish(self) -> None:
        self._require_healthy()
        try:
            self.framer.finish()
            self.inbound.finish()
        except TLSRecordError:
            self.failed = True
            self.inbound.failed = True
            raise
