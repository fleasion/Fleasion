"""Read-only OpenSSL 3.5 TLS 1.2 SSL_SESSION master-secret lookup."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

TLS12_VERSION = 0x303
TLS12_MASTER_LENGTH = 48
SESSION_MASTER_OFFSET = 80
SESSION_READ_SIZE = SESSION_MASTER_OFFSET + TLS12_MASTER_LENGTH
MIN_DISTINCT_BYTES = 20


@dataclass(frozen=True)
class TLS12ConnectionLayout:
    """Build-specific SSL_CONNECTION offsets, separately validated per target."""

    flag_offset: int
    client_random_offset: int
    server_random_offset: int
    session_offsets: tuple[int, ...]

    @property
    def minimum_size(self) -> int:
        return max(
            0x4C,
            self.flag_offset + 4,
            self.client_random_offset + 32,
            self.server_random_offset + 32,
            *(value + 8 for value in self.session_offsets),
        )


# The first pointer was verified against live Roblox Studio 0.746 in Vinegar
# The second is a fallback for differently configured Windows SSL builds
WINDOWS_OPENSSL_350 = TLS12ConnectionLayout(0x78, 0x184, 0x164, (0x8F8, 0x900))
# TLS 1.3 session offset 0x900 is confirmed in live Mocktail, Sober and
# Rosemary processes; their TLS 1.2 master-secret layout remains unverified
LP64_OPENSSL_350 = TLS12ConnectionLayout(0x7C, 0x188, 0x168, (0x900, 0x908))


def validate_tls12_session(
    raw: bytes,
    read_memory: Callable[[int, int], bytes],
    *,
    layout: TLS12ConnectionLayout,
) -> tuple[str, dict[str, bytes]] | None:
    """Find a structurally credible TLS 1.2 master secret in an SSL session.

    The returned candidate is not trusted for interception until the
    corresponding TLS 1.2 record passes AES-GCM authentication
    """
    if len(raw) < layout.minimum_size or struct.unpack_from('<I', raw, 0x48)[0] != TLS12_VERSION:
        return None
    if struct.unpack_from('<I', raw, layout.flag_offset)[0] != 0:
        return None
    client_random = raw[layout.client_random_offset : layout.client_random_offset + 32]
    server_random = raw[layout.server_random_offset : layout.server_random_offset + 32]
    if len(set(client_random)) < MIN_DISTINCT_BYTES or len(set(server_random)) < MIN_DISTINCT_BYTES:
        return None

    for offset in layout.session_offsets:
        pointer = struct.unpack_from('<Q', raw, offset)[0]
        if pointer < 0x10000 or pointer >= 0x0000800000000000 or pointer & 7:
            continue
        session = read_memory(pointer, SESSION_READ_SIZE)
        if len(session) != SESSION_READ_SIZE:
            continue
        version = struct.unpack_from('<I', session, 0)[0]
        key_length = struct.unpack_from('<Q', session, 8)[0]
        if version != TLS12_VERSION or key_length != TLS12_MASTER_LENGTH:
            continue
        master = session[SESSION_MASTER_OFFSET:SESSION_READ_SIZE]
        if len(set(master)) < MIN_DISTINCT_BYTES:
            continue
        return client_random.hex(), {'CLIENT_RANDOM': master}
    return None
