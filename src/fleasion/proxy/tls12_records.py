"""TLS 1.2 authenticated application records from a known session master secret."""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .tls_records import MAX_PLAINTEXT, TLSRecordError

TLS12_VERSION = b'\x03\x03'
TAG_SIZE = 16
MAX_SEQUENCE = (1 << 64) - 1


@dataclass(frozen=True, slots=True)
class TLS12Suite:
    """TLS 1.2 PRF hash and authenticated record parameters."""

    hash_name: str
    key_size: int


TLS12_SUITES = {
    0x009C: TLS12Suite('sha256', 16),
    0x009D: TLS12Suite('sha384', 32),
    0x009E: TLS12Suite('sha256', 16),
    0x009F: TLS12Suite('sha384', 32),
    0xC02B: TLS12Suite('sha256', 16),
    0xC02C: TLS12Suite('sha384', 32),
    0xC02F: TLS12Suite('sha256', 16),
    0xC030: TLS12Suite('sha384', 32),
}


def _tls12_prf(secret: bytes, label: bytes, seed: bytes, length: int, hash_name: str) -> bytes:
    """TLS 1.2 P_SHA256 or P_SHA384 PRF (RFC 5246 section 5)."""
    data = label + seed
    a = data
    result = bytearray()
    while len(result) < length:
        a = hmac.digest(secret, a, hash_name)
        result.extend(hmac.digest(secret, a + data, hash_name))
    return bytes(result[:length])


def derive_tls12_keys(
    master: bytes, cipher: int, client_random: bytes, server_random: bytes
) -> tuple[bytes, bytes, bytes, bytes]:
    """Return client/server write keys followed by their fixed write IVs."""
    suite = TLS12_SUITES[cipher]
    if len(master) != 48 or len(client_random) != 32 or len(server_random) != 32:
        msg = 'TLS 1.2 master secret and hello randoms have invalid lengths'
        raise ValueError(msg)
    iv_length = 4
    size = 2 * (suite.key_size + iv_length)
    block = _tls12_prf(
        master, b'key expansion', server_random + client_random, size, suite.hash_name
    )
    n = suite.key_size
    return block[:n], block[n : n * 2], block[n * 2 : n * 2 + iv_length], block[n * 2 + iv_length :]


class TLS12RecordCipher:
    """One AEAD record direction, including independent incoming/outgoing sequence."""

    def __init__(self, key: bytes, iv: bytes, cipher: int, *, sequence: int = 0) -> None:
        suite = TLS12_SUITES[cipher]
        if len(key) != suite.key_size or len(iv) != 4:
            msg = 'TLS 1.2 traffic key/IV lengths do not match the negotiated suite'
            raise ValueError(msg)
        self.suite = suite
        self.iv = iv
        self.sequence = sequence
        self._aead = AESGCM(key)
        # A distinct, unpredictable explicit nonce domain avoids reusing the
        # original client's AES-GCM nonce when forwarding changed plaintext
        self._next_explicit_nonce = secrets.randbits(64)

    def _aad(self, kind: int, content_size: int) -> bytes:
        return (
            self.sequence.to_bytes(8, 'big')
            + bytes((kind,))
            + TLS12_VERSION
            + content_size.to_bytes(2, 'big')
        )

    def _nonce(self, explicit: bytes) -> bytes:
        return self.iv + explicit

    def _check_sequence(self) -> None:
        if self.sequence < 0 or self.sequence > MAX_SEQUENCE:
            msg = 'TLS 1.2 record sequence number exhausted'
            raise TLSRecordError(msg)

    def decrypt(self, record: bytes) -> tuple[int, bytes]:
        """Authenticate one TLS 1.2 record and advance its input sequence."""
        self._check_sequence()
        if len(record) < 5 or record[1:3] != TLS12_VERSION or record[0] not in {21, 22, 23}:
            msg = 'Invalid TLS 1.2 encrypted record header'
            raise TLSRecordError(msg)
        if int.from_bytes(record[3:5], 'big') != len(record) - 5:
            msg = 'TLS 1.2 encrypted record length is inconsistent'
            raise TLSRecordError(msg)
        explicit_len = 8
        payload = record[5:]
        if len(payload) < explicit_len + TAG_SIZE:
            msg = 'Truncated TLS 1.2 AEAD ciphertext'
            raise TLSRecordError(msg)
        explicit = payload[:explicit_len]
        encrypted = payload[explicit_len:]
        kind = record[0]
        plaintext = self._aead.decrypt(
            self._nonce(explicit), encrypted, self._aad(kind, len(encrypted) - TAG_SIZE)
        )
        self.sequence += 1
        return kind, plaintext

    def encrypt(self, content: bytes, content_type: int = 23, padding: int = 0) -> bytes:
        """Encrypt an authenticated TLS 1.2 record with a fresh explicit IV."""
        self._check_sequence()
        if padding or len(content) > MAX_PLAINTEXT or content_type not in {21, 22, 23}:
            msg = 'Invalid TLS 1.2 record content, type or padding'
            raise TLSRecordError(msg)
        if self._next_explicit_nonce == MAX_SEQUENCE:
            msg = 'TLS 1.2 explicit nonce space exhausted'
            raise TLSRecordError(msg)
        explicit = self._next_explicit_nonce.to_bytes(8, 'big')
        self._next_explicit_nonce += 1
        encrypted = self._aead.encrypt(
            self._nonce(explicit), content, self._aad(content_type, len(content))
        )
        result = explicit + encrypted
        self.sequence += 1
        return bytes((content_type,)) + TLS12_VERSION + len(result).to_bytes(2, 'big') + result
