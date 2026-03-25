"""Core asyncio TLS proxy server for Fleasion.

Architecture:
  - Hosts file redirects assetdelivery.roblox.com + fts.rbxcdn.com -> 127.0.0.1.
  - We listen on 127.0.0.1:443 as a direct TLS server (NOT a CONNECT proxy).
  - Upstream connections use the REAL CDN IPs (resolved before hosts file is written).
  - SNI callback handles cert selection only; host is read from the HTTP Host: header.

Key design principle - minimal modification:
  CDN responses use zstd/gzip encoding that we should NOT strip unless we are
  actually modifying the body. Stripping content-encoding while leaving the bytes
  compressed causes Roblox to receive compressed bytes it can't interpret.

  For responses we don't modify (most CDN asset bytes): forward raw bytes + raw
  headers completely unchanged. Fast and correct.

  For responses we DO modify (solidmodel injection): decompress, modify, send
  the new bytes without compression (explicit content-length).

  For the batch request body we modify: decompress, modify, send uncompressed.
  Roblox's libcurl handles both compressed and uncompressed request bodies.
"""

import asyncio
import gzip
import logging
import ssl
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from .addons.cache_scraper import CacheScraper
    from .addons.texture_stripper import TextureStripper

logger = logging.getLogger(__name__)

INTERCEPT_HOSTS: frozenset = frozenset({'assetdelivery.roblox.com', 'fts.rbxcdn.com'})

_ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'
_GZIP_MAGIC  = b'\x1f\x8b'


def _decompress_body(body: bytes, headers: Dict[bytes, bytes]) -> bytes:
    """Decompress gzip or zstd body. Used only when we need to READ content."""
    ce = headers.get(b'content-encoding', b'').lower()
    if not body:
        return body
    if ce == b'gzip' or body[:2] == _GZIP_MAGIC:
        try:
            return gzip.decompress(body)
        except Exception:
            return body
    if ce == b'zstd' or body[:4] == _ZSTD_MAGIC:
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(body, max_output_size=64 * 1024 * 1024)
        except Exception:
            return body
    return body


def _build_modified_response(status_line: bytes, headers: Dict[bytes, bytes], body: bytes) -> bytes:
    """Build an HTTP response with a MODIFIED body (uncompressed, explicit content-length).
    Only used when we actually change the response bytes.
    """
    lines = [status_line]
    skip = {b'transfer-encoding', b'content-length', b'content-encoding',
            b'proxy-connection', b'proxy-authenticate', b'proxy-authorization'}
    for k, v in headers.items():
        if k not in skip:
            lines.append(k + b': ' + v)
    lines.append(b'content-length: ' + str(len(body)).encode())
    return b'\r\n'.join(lines) + b'\r\n\r\n' + body


def _build_modified_request(req_line: bytes, headers: Dict[bytes, bytes], body: bytes) -> bytes:
    """Build an HTTP request with a MODIFIED body (always uncompressed JSON for batch)."""
    lines = [req_line]
    skip = {b'transfer-encoding', b'content-length', b'content-encoding',
            b'proxy-connection', b'proxy-authenticate', b'proxy-authorization'}
    for k, v in headers.items():
        if k not in skip:
            lines.append(k + b': ' + v)
    lines.append(b'content-length: ' + str(len(body)).encode())
    return b'\r\n'.join(lines) + b'\r\n\r\n' + body


async def _read_headers(reader: asyncio.StreamReader) -> Optional[Tuple[bytes, Dict[bytes, bytes]]]:
    """Read one HTTP header block. Returns (first_line, lowercase_headers) or None."""
    first_line: Optional[bytes] = None
    headers: Dict[bytes, bytes] = {}
    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=15.0)
        except Exception:
            return None
        if not line:
            return None
        stripped = line.rstrip(b'\r\n')
        if stripped == b'':
            break
        if first_line is None:
            first_line = stripped
        elif b':' in stripped:
            k, _, v = stripped.partition(b':')
            headers[k.strip().lower()] = v.strip()
    if first_line is None:
        return None
    return first_line, headers


async def _read_body_raw(reader: asyncio.StreamReader, headers: Dict[bytes, bytes]) -> bytes:
    """Read HTTP body, returning raw (still-compressed) bytes."""
    te = headers.get(b'transfer-encoding', b'').lower()
    cl_raw = headers.get(b'content-length', b'')

    if b'chunked' in te:
        body = bytearray()
        while True:
            try:
                size_line = await reader.readline()
            except Exception:
                break
            if not size_line:
                break
            size_str = size_line.strip().split(b';')[0]
            try:
                chunk_size = int(size_str, 16)
            except ValueError:
                break
            if chunk_size == 0:
                await reader.readline()
                break
            try:
                chunk = await reader.readexactly(chunk_size)
            except asyncio.IncompleteReadError as exc:
                body += exc.partial
                break
            await reader.readline()  # CRLF after chunk data
            body += chunk
        return bytes(body)

    if cl_raw:
        try:
            length = int(cl_raw)
        except ValueError:
            return b''
        if length <= 0:
            return b''
        try:
            return await reader.readexactly(length)
        except asyncio.IncompleteReadError as exc:
            return exc.partial

    return b''


def _reassemble_raw_response(status_line: bytes, headers: Dict[bytes, bytes], body_raw: bytes) -> bytes:
    """Reconstruct an HTTP response forwarding the ORIGINAL body bytes.
    Strips only hop-by-hop headers but preserves content-encoding and content-length.
    """
    lines = [status_line]
    hop_by_hop = {b'proxy-connection', b'proxy-authenticate', b'proxy-authorization',
                  b'transfer-encoding'}  # we already dechunked, switch to content-length
    for k, v in headers.items():
        if k not in hop_by_hop:
            lines.append(k + b': ' + v)
    # Replace/add content-length (body_raw is already dechunked)
    if b'content-length' not in headers:
        lines.append(b'content-length: ' + str(len(body_raw)).encode())
    return b'\r\n'.join(lines) + b'\r\n\r\n' + body_raw


def _keep_alive(first_line: bytes, headers: Dict[bytes, bytes]) -> bool:
    conn = headers.get(b'connection', b'').lower()
    if b'close' in conn:
        return False
    if b'http/1.0' in first_line.lower() and b'keep-alive' not in conn:
        return False
    return True


def _read_local_bytes(local_path: str) -> bytes:
    """Read the actual (possibly converted) bytes for caching purposes."""
    path = Path(local_path)
    if path.suffix.lower() == '.obj':
        try:
            from ..cache.tools.solidmodel_converter.obj_to_mesh import get_or_create_mesh_from_obj
            path = get_or_create_mesh_from_obj(path)
        except Exception:
            pass
    return path.read_bytes() if path.exists() else b''


def _serve_local_file(local_path: str) -> bytes:
    path = Path(local_path)
    if path.suffix.lower() == '.obj':
        try:
            from ..cache.tools.solidmodel_converter.obj_to_mesh import get_or_create_mesh_from_obj
            path = get_or_create_mesh_from_obj(path)
        except Exception as exc:
            logger.debug('OBJ->mesh conversion failed: %s', exc)
    if not path.exists():
        return b'HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\nconnection: keep-alive\r\n\r\n'
    content = path.read_bytes()
    ext = path.suffix.lower()
    ct_map = {
        '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.gif': 'image/gif', '.webp': 'image/webp', '.ogg': 'audio/ogg',
        '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
        '.rbxm': 'application/octet-stream', '.rbxmx': 'application/xml',
        '.mesh': 'application/octet-stream',
    }
    ct = ct_map.get(ext, 'application/octet-stream')
    return (
        f'HTTP/1.1 200 OK\r\nContent-Type: {ct}\r\n'
        f'Content-Length: {len(content)}\r\nConnection: keep-alive\r\n\r\n'
    ).encode() + content


def _make_redirect(target_url: str) -> bytes:
    return (
        b'HTTP/1.1 302 Found\r\nLocation: ' + target_url.encode() +
        b'\r\nContent-Length: 0\r\nConnection: keep-alive\r\n\r\n'
    )


class FleasionProxy:
    """Direct TLS-terminating asyncio proxy for Roblox asset hosts."""

    def __init__(
        self,
        texture_stripper: 'TextureStripper',
        cache_scraper: 'CacheScraper',
        host_certs: Dict[str, Tuple[Path, Path]],
        upstream_ips: Dict[str, List[str]],
        port: int = 443,
        max_workers: int = 8,
    ) -> None:
        self.texture_stripper = texture_stripper
        self.cache_scraper = cache_scraper
        self.port = port
        self._upstream_ips = upstream_ips
        self._server: Optional[asyncio.Server] = None
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='fleasion-cpu')

        self._host_ssl_ctxs: Dict[str, ssl.SSLContext] = {}
        for host, (cert_path, key_path) in host_certs.items():
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cert_path), str(key_path))
            ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.set_alpn_protocols(['http/1.1'])
            self._host_ssl_ctxs[host] = ctx

        # Upstream: no cert verify, force HTTP/1.1 (we don't implement h2)
        self._upstream_ssl_ctx = ssl.create_default_context()
        self._upstream_ssl_ctx.check_hostname = False
        self._upstream_ssl_ctx.verify_mode = ssl.CERT_NONE
        self._upstream_ssl_ctx.set_alpn_protocols(['http/1.1'])

        first_host = next(iter(host_certs))
        first_cert, first_key = host_certs[first_host]
        self._server_ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._server_ssl_ctx.load_cert_chain(str(first_cert), str(first_key))
        self._server_ssl_ctx.verify_mode = ssl.CERT_NONE
        self._server_ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self._server_ssl_ctx.set_alpn_protocols(['http/1.1'])
        self._server_ssl_ctx.set_servername_callback(self._sni_callback)

    def _sni_callback(self, ssl_obj, server_name: Optional[str], initial_ctx: ssl.SSLContext) -> None:
        name = (server_name or '').lower()
        if name in self._host_ssl_ctxs:
            ssl_obj.context = self._host_ssl_ctxs[name]

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            host='127.0.0.1',
            port=self.port,
            ssl=self._server_ssl_ctx,
            backlog=256,
            reuse_address=True,
        )
        logger.info('Fleasion proxy listening on 127.0.0.1:%d (TLS)', self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=3.0)
            except Exception:
                pass
            self._server = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        from ..utils import log_buffer

        try:
            result = await asyncio.wait_for(_read_headers(reader), timeout=15.0)
        except asyncio.TimeoutError:
            writer.close()
            return
        if result is None:
            writer.close()
            return
        req_first, req_headers = result

        host_hdr = req_headers.get(b'host', b'').decode('ascii', errors='replace').lower()
        host = host_hdr.split(':')[0].strip()

        if host not in INTERCEPT_HOSTS:
            writer.close()
            return



        real_ips = self._upstream_ips.get(host, [])
        upstream_target = real_ips[0] if real_ips else host
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(
                    upstream_target, 443,
                    ssl=self._upstream_ssl_ctx,
                    server_hostname=host,
                ),
                timeout=10.0,
            )
        except Exception as exc:
            log_buffer.log('Proxy', f'Upstream connect failed for {host} ({upstream_target}): {exc}')
            writer.close()
            return

        try:
            await self._http_session(req_first, req_headers, reader, writer, up_reader, up_writer, host)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:
            log_buffer.log('Proxy', f'Session error for {host}: {exc}')
        finally:
            for w in (up_writer, writer):
                try:
                    w.close()
                except Exception:
                    pass

    async def _http_session(
        self,
        first_req_line: bytes,
        first_req_headers: Dict[bytes, bytes],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
        host: str,
    ) -> None:
        from ..utils import log_buffer

        replacements_tuple = self.texture_stripper.config_manager.get_all_replacements()
        pending_req: Optional[Tuple[bytes, Dict]] = (first_req_line, first_req_headers)

        while True:
            # ── Read request ─────────────────────────────────────────────
            if pending_req is not None:
                req_first, req_headers = pending_req
                pending_req = None
            else:
                result = await _read_headers(reader)
                if result is None:
                    break
                req_first, req_headers = result

            # Read raw request body (may be compressed)
            req_body_raw = await _read_body_raw(reader, req_headers)

            parts = req_first.split(b' ', 2)
            path = parts[1].decode('ascii', errors='replace') if len(parts) > 1 else '/'
            is_batch = (host == 'assetdelivery.roblox.com' and b'/v1/assets/batch' in req_first)

            # ── TextureStripper: CDN short-circuit (replace before upstream) ──
            # Race condition fix: the batch-request coroutine (on the assetdelivery
            # connection) and this CDN coroutine (on fts.rbxcdn.com) run concurrently.
            # The CDN request may arrive before the batch response has been processed
            # and its CDN URL registered in _solidmodel_injections / _local_redirects.
            # If there are pending req_ids in flight, yield briefly to the event loop
            # so the batch-response coroutine can complete its registration, then retry.
            # Without this, unreplaced assets pass through and Roblox caches them,
            # requiring multiple rejoins to achieve full replacement coverage.
            short_circuit = None
            if host == 'fts.rbxcdn.com':
                short_circuit = self.texture_stripper.check_cdn_request(host, path)
                if short_circuit is None and self.texture_stripper.has_pending():
                    # Yield to event loop in short increments, retrying up to ~600ms.
                    # 600ms is generous: batch req→resp RTT is typically <100ms.
                    for _wait_i in range(12):
                        await asyncio.sleep(0.05)  # 50ms per retry
                        short_circuit = self.texture_stripper.check_cdn_request(host, path)
                        if short_circuit is not None:
                            break
                        if not self.texture_stripper.has_pending():
                            break  # all pending resolved, this URL just isn't ours

                if short_circuit is not None:
                    action, value = short_circuit
                    if action == 'local':
                        response = await asyncio.get_event_loop().run_in_executor(
                            self._executor, _serve_local_file, value)
                        writer.write(response)
                        await writer.drain()
                        # Cache our own served file so it appears in the scraper viewer
                        if self.cache_scraper.enabled:
                            try:
                                _file_bytes = await asyncio.get_event_loop().run_in_executor(
                                    self._executor, _read_local_bytes, value)
                                if _file_bytes:
                                    full_url = f'https://{host}{path}'
                                    _cache_hash = path.rsplit('/', 1)[-1].split('?')[0]
                                    self.cache_scraper.process_cdn_response(
                                        full_url, path, _file_bytes, 'application/octet-stream',
                                    )
                            except Exception:
                                pass
                        if not _keep_alive(req_first, req_headers):
                            break
                        continue
                    elif action == 'cdn':
                        writer.write(_make_redirect(value))
                        await writer.drain()
                        if not _keep_alive(req_first, req_headers):
                            break
                        continue
                    # 'solid' falls through - needs upstream response to inject into

            # ── Modify batch request body if needed ───────────────────────
            if is_batch:
                # Decompress for reading/modifying, send uncompressed to upstream
                req_body_plain = _decompress_body(req_body_raw, req_headers)
                # Unique ID for this specific batch request/response pair.
                # Keyed into _pending as f'{batch_id}_{req_id}' so parallel
                # connections using the same req_id integers don't collide —
                # the same root cause mitmproxy solved with its flow_id prefix.
                import uuid as _uuid
                batch_id = _uuid.uuid4().hex
                # Run synchronously — process_batch_request is pure Python (JSON parse +
                # dict ops), not I/O bound. Using run_in_executor here introduced a gap:
                # the await released the event loop, the CDN coroutine ran, saw empty
                # _pending, skipped the wait, and forwarded unreplaced assets. Running
                # synchronously ensures _pending is populated before any CDN coroutine
                # can check has_pending().
                req_body_modified, scraper_body = self.texture_stripper.process_batch_request(
                    req_body_plain, req_headers, replacements_tuple, batch_id,
                )
                up_writer.write(_build_modified_request(req_first, req_headers, req_body_modified))
            else:
                # Forward request as-is (raw bytes, original headers)
                up_writer.write(_reassemble_raw_response(req_first, req_headers, req_body_raw))

            try:
                await up_writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                break

            # ── Read upstream response ────────────────────────────────────
            resp_result = await _read_headers(up_reader)
            if resp_result is None:
                break
            resp_first, resp_headers = resp_result
            resp_body_raw = await _read_body_raw(up_reader, resp_headers)

            # ── Determine if we need to modify the response body ──────────
            # We only modify if: solidmodel injection is requested.
            # All other responses are forwarded raw (preserving content-encoding).
            response_modified = False

            if is_batch:
                # Batch response: forward raw to Roblox, decompress only for addon hooks
                resp_body_plain = _decompress_body(resp_body_raw, resp_headers)
                # Addon hooks must use req_body_modified (what we actually sent to
                # upstream), NOT req_body_raw. The upstream response is index-aligned
                # with the modified request. If assets were removed by process_batch_request
                # (strip_textures, removal rules), using req_body_raw causes every index
                # after a removed item to map to the wrong response item, producing wrong
                # assetTypeId values (the root cause of SolidModel/Mesh being typed as Image).
                self.texture_stripper.process_batch_response(
                    req_body_modified,
                    resp_body_plain,
                    req_headers,
                    batch_id,
                )
                if self.cache_scraper.enabled:
                    self.cache_scraper.process_batch_response(
                        scraper_body,
                        resp_body_plain,
                    )

            elif host == 'fts.rbxcdn.com':
                full_url = f'https://{host}{path}'

                if short_circuit is not None and short_circuit[0] == 'solid':
                    # SolidModel injection - we MUST modify the body
                    resp_body_plain = _decompress_body(resp_body_raw, resp_headers)
                    _cdn_base_url = full_url.split('?')[0]
                    resp_body_raw = await asyncio.get_event_loop().run_in_executor(
                        self._executor,
                        self.texture_stripper.process_solidmodel_response,
                        resp_body_plain, short_circuit[1], _cdn_base_url,
                    )
                    response_modified = True

                if self.cache_scraper.enabled:
                    # Cache the decompressed bytes for storage
                    resp_body_for_cache = _decompress_body(resp_body_raw, resp_headers) \
                        if not response_modified else resp_body_raw
                    ct = resp_headers.get(b'content-type', b'').decode('ascii', errors='replace')
                    self.cache_scraper.process_cdn_response(full_url, path, resp_body_for_cache, ct)

            # ── Forward response to Roblox ────────────────────────────────
            if response_modified:
                # We changed the bytes, send as uncompressed with new content-length
                writer.write(_build_modified_response(resp_first, resp_headers, resp_body_raw))
            else:
                # Raw passthrough - Roblox handles decompression itself
                writer.write(_reassemble_raw_response(resp_first, resp_headers, resp_body_raw))

            try:
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                break

            if not _keep_alive(req_first, req_headers) or not _keep_alive(resp_first, resp_headers):
                break
