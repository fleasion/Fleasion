"""Relay real TLS handshakes and expose application streams to Fleasion's HTTP engine."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidTag

from fleasion.utils.logging import log_buffer

from .tls12_records import TLS12_SUITES, TLS12RecordCipher, derive_tls12_keys
from .tls_records import CIPHERS, MAX_PLAINTEXT, TrafficKeys, read_record
from .upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .server import FleasionProxy
    from .splice_keys import KeyProvider

log = logging.getLogger(__name__)

HANDSHAKE_HEADER_SIZE = 4
HANDSHAKE = 22
APPLICATION = 23
ALERT = 21
SERVER_HELLO = 2
KEY_UPDATE = 24
HELLO_PREFIX_SIZE = 35
SNI_PREFIX_SIZE = 5
_HTTP_DRAIN_GRACE_SECONDS = 1.0


def _server_name(hello: bytes) -> str:
    # RFC 8446 ClientHello vectors: session ID, cipher suites, compression, extensions
    offset = 34
    for size_width in (1, 2, 1):
        size = int.from_bytes(hello[offset : offset + size_width], 'big')
        offset += size_width + size
    end = offset + 2 + int.from_bytes(hello[offset : offset + 2], 'big')
    offset += 2
    while offset + 4 <= min(end, len(hello)):
        kind = int.from_bytes(hello[offset : offset + 2], 'big')
        size = int.from_bytes(hello[offset + 2 : offset + 4], 'big')
        value = hello[offset + 4 : offset + 4 + size]
        if kind == 0 and len(value) >= SNI_PREFIX_SIZE and value[2] == 0:
            name_size = int.from_bytes(value[3:5], 'big')
            return value[5 : 5 + name_size].decode('ascii').lower().rstrip('.')
        offset += 4 + size
    msg = 'Direct TLS splice requires a visible server name in ClientHello'
    raise ValueError(msg)


async def read_client_hello(reader: asyncio.StreamReader) -> tuple[str, list[bytes]]:
    """Read TLS records until a ClientHello message is parsed.

    Parameters
    ----------
    reader : asyncio.StreamReader
        Stream receiving the initial TLS records.

    Returns
    -------
    tuple[str, list[bytes]]
        Server name from SNI and list of raw record bytes read.
    """
    messages = HandshakeMessages()
    records: list[bytes] = []
    while True:
        record = await read_record(reader)
        if record[0] != HANDSHAKE:
            msg = 'Expected a TLS ClientHello on the direct splice listener'
            raise ValueError(msg)
        records.append(record)
        for kind, body in messages.feed(record[5:]):
            if kind != 1 or len(body) < HELLO_PREFIX_SIZE:
                msg = 'Invalid TLS ClientHello'
                raise ValueError(msg)
            return _server_name(body), records


class HandshakeMessages:
    """Frame fragmented TLS handshake messages."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, content: bytes) -> list[tuple[int, bytes]]:
        """Feed record body bytes and extract completed messages.

        Parameters
        ----------
        content : bytes
            Raw handshake record body bytes.

        Returns
        -------
        list[tuple[int, bytes]]
            Pairs of (handshake_type, body).
        """
        self.buffer.extend(content)
        if len(self.buffer) > 1024 * 1024:
            msg = 'TLS handshake message exceeds the splice buffer limit'
            raise ValueError(msg)
        messages: list[tuple[int, bytes]] = []
        while len(self.buffer) >= HANDSHAKE_HEADER_SIZE:
            size = int.from_bytes(self.buffer[1:4], 'big')
            if len(self.buffer) < size + 4:
                break
            messages.append((self.buffer[0], bytes(self.buffer[4 : size + 4])))
            del self.buffer[: size + 4]
        return messages


class _PlainWriter(asyncio.StreamWriter):
    """A StreamWriter whose writes become TLS application records."""

    def __init__(self, wire: asyncio.StreamWriter) -> None:
        self.wire = wire
        self.keys: TrafficKeys | TLS12RecordCipher | None = None
        self.closed = False

    def write(self, data: bytes | bytearray | memoryview) -> None:
        if self.is_closing():
            msg = 'Splice application stream is closed'
            raise ConnectionError(msg)
        if self.keys is None:
            msg = 'Splice application keys are not ready'
            raise RuntimeError(msg)
        content = bytes(data)
        for offset in range(0, len(content), MAX_PLAINTEXT):
            self.wire.write(self.keys.encrypt(content[offset : offset + MAX_PLAINTEXT]))

    async def drain(self) -> None:
        await self.wire.drain()

    def close(self) -> None:
        # The connection owns the wire; an HTTP session must not close it before flushing
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed or self.wire.is_closing()

    async def wait_closed(self) -> None:
        await self.wire.drain()

    def get_extra_info(self, name: str, default: object = None) -> object:
        return self.wire.get_extra_info(name, default)

    def __del__(self, _warnings: object = None) -> None:
        # No base StreamWriter transport is allocated by this adapter
        pass


class _FeedTransport(asyncio.Transport):
    """Transport controlling flow-control backpressure for plain StreamReader."""

    def __init__(self) -> None:
        super().__init__()
        self.ready = asyncio.Event()
        self.ready.set()

    def pause_reading(self) -> None:
        self.ready.clear()

    def resume_reading(self) -> None:
        self.ready.set()


class _Direction:
    """Connection state for one stream direction."""

    def __init__(
        self,
        name: str,
        source: asyncio.StreamReader,
        wire: asyncio.StreamWriter,
    ) -> None:
        self.name = name
        self.source = source
        self.wire = wire
        self.plain = asyncio.StreamReader(limit=1024 * 1024)
        self.flow = _FeedTransport()
        self.plain.set_transport(self.flow)
        self.writer = _PlainWriter(wire)
        self.handshake_sequence = 0
        self.handshake_keys: TrafficKeys | None = None
        self.input_keys: TrafficKeys | TLS12RecordCipher | None = None
        self.ccs_relayed = False
        self.handshake = HandshakeMessages()
        self.post_handshake = HandshakeMessages()
        self.close_alert: bytes | None = None
        self.key_lock = asyncio.Lock()


class SpliceConnection:
    """Duplex TLS splice connection relaying handshakes and decrypting streams."""

    def __init__(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
        *,
        keys: KeyProvider,
        client_records: Sequence[bytes] = (),
    ) -> None:
        self.client = _Direction('CLIENT', client_reader, upstream_writer)
        self.server = _Direction('SERVER', upstream_reader, client_writer)
        self.keys = keys
        self.client_records = client_records
        self.client_random = ''
        self.server_random = b''
        self.cipher: int | None = None
        self._tls12_lock = asyncio.Lock()
        self.client_finished_relayed = False
        self._application_ready = asyncio.Event()
        self._ready = asyncio.Event()
        self._transparent_passthrough = False
        self._passthrough_reason = ''
        self._tls12_authenticated_application = False
        self._started_at = time.monotonic()
        self._http_started = False
        self._plain_bytes = {'CLIENT': 0, 'SERVER': 0}

    def _hello(self, direction: _Direction, record: bytes) -> None:
        for kind, body in direction.handshake.feed(record[5:]):
            if kind in {1, SERVER_HELLO} and len(body) < HELLO_PREFIX_SIZE:
                msg = 'Truncated TLS hello message'
                raise ValueError(msg)
            if kind == 1 and direction is self.client:
                self.client_random = body[2:34].hex()
            elif kind == SERVER_HELLO and direction is self.server:
                offset = 35 + body[34]
                if len(body) < offset + 2:
                    msg = 'Truncated TLS ServerHello cipher suite'
                    raise ValueError(msg)
                self.cipher = int.from_bytes(body[offset : offset + 2], 'big')
                self.server_random = body[2:34]
                if self.cipher not in CIPHERS and self.cipher not in TLS12_SUITES:
                    # Relay the already-started handshake unchanged. Reconnecting
                    # would break the client's TLS transcript and certificates
                    self._transparent_passthrough = True
                    self._passthrough_reason = 'unsupported_cipher'
                    self._ready.set()

    async def _prepare_tls12_keys(self) -> None:
        """Install the authenticated TLS 1.2 AEAD keys for both wire directions."""
        async with self._tls12_lock:
            if self._transparent_passthrough or self.client.input_keys is not None:
                return
            if self.cipher not in TLS12_SUITES or len(self.server_random) != 32:
                msg = 'Missing TLS 1.2 hello or supported cipher suite'
                raise ValueError(msg)
            master = await self.keys.get(self.client_random, 'CLIENT_RANDOM')
            client_key, server_key, client_iv, server_iv = derive_tls12_keys(
                master, self.cipher, bytes.fromhex(self.client_random), self.server_random
            )
            for direction, key, iv in (
                (self.client, client_key, client_iv),
                (self.server, server_key, server_iv),
            ):
                direction.input_keys = TLS12RecordCipher(
                    key, iv, self.cipher, sequence=direction.handshake_sequence
                )
                direction.writer.keys = TLS12RecordCipher(
                    key, iv, self.cipher, sequence=direction.handshake_sequence
                )

    async def _prepare_keys(self, direction: _Direction) -> None:
        async with direction.key_lock:
            if direction.input_keys is not None:
                return
            if not self.client_random or self.cipher is None:
                msg = 'Missing TLS ClientHello or ServerHello'
                raise ValueError(msg)
            secret = await self.keys.get(self.client_random, f'{direction.name}_TRAFFIC_SECRET_0')
            handshake = await self.keys.get(
                self.client_random, f'{direction.name}_HANDSHAKE_TRAFFIC_SECRET'
            )
            direction.input_keys = TrafficKeys(secret, self.cipher)
            direction.writer.keys = TrafficKeys(secret, self.cipher)
            direction.handshake_keys = TrafficKeys(handshake, self.cipher)
            direction.handshake_keys.sequence = direction.handshake_sequence

    async def _tls12_record(self, direction: _Direction, record: bytes) -> bool:
        """Relay the clear handshake and decrypt post-CCS TLS 1.2 records."""
        kind = record[0]
        if kind == 20 and not direction.ccs_relayed:
            direction.ccs_relayed = True
            direction.wire.write(record)
            await direction.wire.drain()
            return True
        if not direction.ccs_relayed:
            if kind == HANDSHAKE:
                self._hello(direction, record)
            direction.wire.write(record)
            await direction.wire.drain()
            return True
        if kind == HANDSHAKE and direction.input_keys is None:
            # Finished, and possibly a post-CCS encrypted ticket: both peers
            # already agree on this ciphertext, so preserve it bit-for-bit
            direction.handshake_sequence += 1
            if direction is self.client:
                self.client_finished_relayed = True
            direction.wire.write(record)
            await direction.wire.drain()
            return True
        if kind == ALERT and direction.input_keys is None:
            direction.handshake_sequence += 1
            direction.wire.write(record)
            await direction.wire.drain()
            return False
        if kind not in {HANDSHAKE, APPLICATION, ALERT}:
            msg = 'Unexpected TLS 1.2 encrypted record type'
            raise ValueError(msg)
        if kind == HANDSHAKE:
            # TLS 1.2 renegotiation requires a fresh master secret and key epoch
            msg = 'TLS 1.2 renegotiation is not supported by the splice transport'
            raise ValueError(msg)
        return await self._application_record(direction, record)

    async def _application_record(  # ruff: ignore[too-many-return-statements]
        self, direction: _Direction, record: bytes
    ) -> bool:
        is_tls12 = self.cipher in TLS12_SUITES
        if is_tls12:
            try:
                await self._prepare_tls12_keys()
            except TimeoutError:
                # No application bytes have been altered yet. Preserve the
                # established TLS handshake instead of disconnecting the app
                self._transparent_passthrough = True
                self._passthrough_reason = 'tls12_master_secret_unavailable'
                self._ready.set()
            if self._transparent_passthrough:
                direction.wire.write(record)
                await direction.wire.drain()
                return True
        else:
            try:
                await self._prepare_keys(direction)
            except TimeoutError:
                # Keep the authenticated peer-to-peer handshake intact if
                # key discovery fails before Fleasion has modified any TLS
                # application bytes. After decryption begins, failing closed
                # is essential because transparent relay cannot be resumed
                if (
                    self.client.input_keys is None
                    and self.server.input_keys is None
                    and not self._application_ready.is_set()
                    and not self._http_started
                    and not any(self._plain_bytes.values())
                ):
                    self._transparent_passthrough = True
                    self._passthrough_reason = 'tls13_traffic_secret_unavailable'
                    self._ready.set()
                    direction.wire.write(record)
                    await direction.wire.drain()
                    return True
                raise
        incoming = direction.input_keys
        outgoing = direction.writer.keys
        handshake = direction.handshake_keys
        if incoming is None or outgoing is None or (not is_tls12 and handshake is None):
            msg = 'Splice traffic keys were not initialized'
            raise RuntimeError(msg)
        try:
            content_type, content = incoming.decrypt(record)
        except InvalidTag:
            if is_tls12:
                if not self._tls12_authenticated_application:
                    # A plausible OpenSSL session pointer is not proof of
                    # matching TLS keys; restore the untouched encrypted wire
                    # only while no application plaintext has been processed
                    self._transparent_passthrough = True
                    self._passthrough_reason = 'tls12_master_secret_auth_failed'
                    self._ready.set()
                    direction.wire.write(record)
                    await direction.wire.drain()
                    return True
                msg = 'TLS 1.2 record failed authentication after interception began'
                raise ValueError(msg) from None
            # A fragmented client Finished may occupy more than one record
            if handshake is None:
                msg = 'Missing TLS 1.3 handshake keys'
                raise RuntimeError(msg) from None
            try:
                handshake.decrypt(record)
            except InvalidTag:
                msg = 'TLS record failed authentication with the validated connection keys'
                raise ValueError(msg) from None
            direction.wire.write(record)
            await direction.wire.drain()
            return True
        if direction is self.client:
            await self._prepare_keys(self.server)
            self._application_ready.set()
            self._ready.set()
        if content_type == APPLICATION:
            if is_tls12:
                self._tls12_authenticated_application = True
            self._plain_bytes[direction.name] += len(content)
            direction.plain.feed_data(content)
            await direction.flow.ready.wait()
        elif content_type == ALERT:
            direction.close_alert = content
            return False
        else:
            direction.wire.write(outgoing.encrypt(content, HANDSHAKE))
            if is_tls12:
                await direction.wire.drain()
                return True
            for kind, body in direction.post_handshake.feed(content):
                if kind == KEY_UPDATE:
                    if body not in {b'\x00', b'\x01'}:
                        msg = 'Malformed TLS KeyUpdate'
                        raise ValueError(msg)
                    if not isinstance(incoming, TrafficKeys) or not isinstance(
                        outgoing, TrafficKeys
                    ):
                        msg = 'TLS 1.3 KeyUpdate received without TLS 1.3 cipher state'
                        raise ValueError(msg)
                    incoming.update()
                    outgoing.update()
            await direction.wire.drain()
        return True

    async def _record(self, direction: _Direction, record: bytes) -> bool:
        if self._transparent_passthrough:
            direction.wire.write(record)
            await direction.wire.drain()
            return True
        if self.cipher in TLS12_SUITES:
            return await self._tls12_record(direction, record)
        if record[0] != APPLICATION:
            if record[0] == HANDSHAKE:
                self._hello(direction, record)
            direction.wire.write(record)
            await direction.wire.drain()
            return True
        # The client derives secrets only after accepting the server handshake flight
        if not self.client_finished_relayed:
            direction.wire.write(record)
            direction.handshake_sequence += 1
            if direction is self.client:
                self.client_finished_relayed = True
            await direction.wire.drain()
            return True
        return await self._application_record(direction, record)

    async def _pump(self, direction: _Direction) -> None:
        try:
            await self._read_direction(direction)
        except asyncio.IncompleteReadError as exc:
            if exc.partial:
                msg = 'Peer closed with a truncated TLS record'
                raise ConnectionError(msg) from exc
        finally:
            direction.plain.feed_eof()

    async def _read_direction(self, direction: _Direction) -> None:
        if direction is self.client:
            for record in self.client_records:
                await self._record(direction, record)
        while True:
            record = await read_record(direction.source)
            if not await self._record(direction, record):
                break

    @property
    def passthrough_reason(self) -> str:
        """Indicate why this TLS connection remained opaque to Fleasion."""
        return self._passthrough_reason

    @property
    def transparent_passthrough(self) -> bool:
        """Whether this connection switched to encrypted wire-only relay."""
        return self._transparent_passthrough

    def diagnostic_state(self) -> str:
        """Summarize connection phases without logging traffic or TLS secrets."""
        return (
            f'elapsed_ms={int((time.monotonic() - self._started_at) * 1000)} '
            f'client_hello={bool(self.client_random)} server_hello={self.cipher is not None} '
            f'client_finished={self.client_finished_relayed} '
            f'client_keys={self.client.input_keys is not None} '
            f'server_keys={self.server.input_keys is not None} '
            f'client_plain_bytes={self._plain_bytes["CLIENT"]} '
            f'server_plain_bytes={self._plain_bytes["SERVER"]} '
            f'http_started={self._http_started} '
            f'transparent_passthrough={self._transparent_passthrough} '
            f'passthrough_reason={self._passthrough_reason or "none"}'
        )

    async def run(self, proxy: FleasionProxy, host: str) -> None:
        """Run the duplex TLS splice and dispatch plaintext to the proxy engine.

        Parameters
        ----------
        proxy : FleasionProxy
            The proxy server instance.
        host : str
            Requested hostname.
        """

        async def session() -> None:
            await asyncio.wait_for(self._ready.wait(), timeout=20.0)
            if self._transparent_passthrough:
                # Incompatible TLS sessions are relayed by the two wire pumps
                # and must never attempt to decrypt or start an HTTP parser
                if self._passthrough_reason.endswith('_unavailable'):
                    log_buffer.log(
                        'TLS',
                        f'Encrypted TLS passthrough for {host}: '
                        f'{self._passthrough_reason}; interception unavailable for this connection',
                    )
                await asyncio.Future()
                return
            self._http_started = True
            if proxy.mark_splice_decrypted(host):
                log_buffer.log(
                    'TLS',
                    f'Live TLS splice decrypted application traffic for {host}; '
                    'HTTP interception is active',
                )
            upstream = UpstreamConnectResult(
                reader=self.server.plain,
                writer=self.client.writer,
                method='tls_splice',
                endpoint=host,
            )
            await proxy.serve_spliced_session(self.client.plain, self.server.writer, host, upstream)

        pumps = [
            asyncio.create_task(self._pump(self.client)),
            asyncio.create_task(self._pump(self.server)),
        ]
        http = asyncio.create_task(session())
        try:
            pending: set[asyncio.Task[None]] = {*pumps, http}
            while http in pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                if http not in done and any(pump in done for pump in pumps):
                    # A closed TLS direction cannot deliver additional records
                    # Give an active HTTP session a short window to flush its response
                    if self._http_started:
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(http), timeout=_HTTP_DRAIN_GRACE_SECONDS
                            )
                        except TimeoutError:
                            pass
                        else:
                            http.result()
                    break
            if not http.done():
                return
            http.result()
            # Deliver a TLS close_notify after HTTP has flushed, in both directions
            # Both peers otherwise see an abrupt EOF when the feature session ends
            for direction in (self.server, self.client):
                keys = direction.writer.keys
                if keys is not None and not direction.wire.is_closing():
                    alert = direction.close_alert or b'\x01\x00'
                    direction.wire.write(keys.encrypt(alert, ALERT))
                    await direction.wire.drain()
        finally:
            # Close wires before awaiting cancelled children
            for direction in (self.client, self.server):
                direction.wire.close()
            for task in [*pumps, http]:
                task.cancel()
            await asyncio.gather(*pumps, http, return_exceptions=True)
            await asyncio.gather(
                self.client.wire.wait_closed(),
                self.server.wire.wait_closed(),
                return_exceptions=True,
            )
