"""Core asyncio TLS proxy server for Fleasion.

Architecture:
  - Hosts file redirects assetdelivery.roblox.com + Roblox CDN hosts -> 127.0.0.1.
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
import hashlib
import json
import logging
import re
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    from .addons.cache_scraper import CacheScraper
    from .addons.texture_stripper import TextureStripper

from .roblox_metadata import strip_roblox_metadata
from .upstream import (
    AutoConnector,
    BaseUpstreamConnector,
    DirectIpConnector,
    HttpConnectConnector,
    HttpProxyConfig,
    Socks5Connector,
    Socks5ProxyConfig,
    UnavailableConnector,
    UpstreamConnectResult,
    UpstreamEndpoint,
    UpstreamMode,
    normalize_endpoints,
    normalize_upstream_mode,
)

logger = logging.getLogger(__name__)

ASSET_DELIVERY_HOST = 'assetdelivery.roblox.com'
GAMEJOIN_HOST = 'gamejoin.roblox.com'
PROFILE_API_HOST = 'apis.roblox.com'
PROFILE_API_PATH_FRAGMENT = '/v1/user/profiles/get-profiles'
CLIENT_SETTINGS_HOSTS: frozenset = frozenset(
    {'clientsettingscdn.roblox.com', 'clientsettings.roblox.com'}
)
CDN_HOSTS: frozenset = frozenset({'fts.rbxcdn.com', 'contentdelivery.roblox.com'})
BASE_INTERCEPT_HOSTS: frozenset = frozenset({ASSET_DELIVERY_HOST, GAMEJOIN_HOST, *CDN_HOSTS})
USERNAME_SPOOFER_INTERCEPT_HOSTS: frozenset = frozenset({PROFILE_API_HOST})
CUSTOM_FFLAGS_INTERCEPT_HOSTS: frozenset = CLIENT_SETTINGS_HOSTS
INTERCEPT_HOSTS: frozenset = (
    BASE_INTERCEPT_HOSTS | USERNAME_SPOOFER_INTERCEPT_HOSTS | CUSTOM_FFLAGS_INTERCEPT_HOSTS
)
ASSET_TRAFFIC_MISSING_DIAGNOSTIC_SECONDS = 20.0

_ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'
_GZIP_MAGIC = b'\x1f\x8b'
_DCZ_DICTIONARY_PATH_RE = re.compile(r'/([0-9a-f]{64})\.dcz(?:$|[?])', re.IGNORECASE)


@dataclass
class RawHeaders:
    first_line: bytes
    headers: Dict[bytes, bytes]
    raw_header_block: bytes


@dataclass
class RawBody:
    wire: bytes
    payload: bytes
    was_chunked: bool


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


_PREVIEW_CAPTURE_CAP = 512 * 1024  # per direction, per request-log entry


def _looks_binary(text: str) -> bool:
    if '\x00' in text:
        return True
    sample = text[:2048]
    if not sample:
        return False
    printable = sum(1 for ch in sample if ch in '\t\r\n' or 32 <= ord(ch) < 127 or ord(ch) > 159)
    return (printable / len(sample)) < 0.85


def _pretty_body_text(body: bytes) -> str:
    if not body:
        return ''
    try:
        return json.dumps(json.loads(body), indent=2, ensure_ascii=False)
    except Exception:
        pass
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        return f'<binary body, {len(body)} bytes>'
    if _looks_binary(text):
        return f'<binary body, {len(body)} bytes>'
    return text


async def _format_raw_http_message(raw: bytes) -> str:
    """Re-parse a captured raw HTTP message (headers + body, as actually sent
    on the wire) into human-readable text: header block as-is, body dechunked/
    decompressed and pretty-printed if it's JSON. Reuses the same header/body
    wire parsers the live proxy path uses, fed from an in-memory buffer instead
    of a live socket, so behavior (chunked framing, etc.) stays consistent.
    """
    if not raw:
        return ''
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()
    parsed = await _read_headers_raw(reader)
    if parsed is None:
        return raw.decode('utf-8', errors='replace')
    header_text = parsed.raw_header_block.decode('ascii', errors='replace').rstrip('\r\n')
    try:
        body_wire = await _read_body_wire(reader, parsed.headers)
    except Exception:
        return header_text + '\r\n'
    body = _decompress_body(body_wire.payload, parsed.headers)
    body_text = _pretty_body_text(body)
    if not body_text:
        return header_text + '\r\n'
    return f'{header_text}\r\n\r\n{body_text}'


def rebuild_edited_message(text: str) -> bytes:
    """Convert hand-edited preview text (the same header+pretty-body layout
    ``_format_raw_http_message`` produces) back into self-consistent HTTP wire
    bytes.

    A GUI text box only ever gives us ``\\n`` line endings (Qt normalizes them),
    so headers get rejoined with ``\\r\\n``. The body may have been edited
    without the user touching Content-Length, and the original body may no
    longer be validly compressed/chunked after editing - so this always drops
    Content-Length/Transfer-Encoding/Content-Encoding from what the user typed
    and recomputes a correct Content-Length for the edited (uncompressed)
    body, matching how the rest of this module already sends any modified
    body (see ``_build_modified_response``/``_build_modified_request``).
    """
    header_block, sep, body_text = text.partition('\n\n')
    if not sep:
        header_block, body_text = text, ''
    body = body_text.encode('utf-8', errors='replace')
    raw_lines = header_block.split('\n')
    first_line = raw_lines[0].strip('\r').encode('utf-8', errors='replace') if raw_lines else b''
    out_lines = [first_line]
    for line in raw_lines[1:]:
        line = line.strip('\r')
        if not line or ':' not in line:
            continue
        key, _, value = line.partition(':')
        if key.strip().lower() in ('content-length', 'transfer-encoding', 'content-encoding'):
            continue
        out_lines.append((key.strip() + ': ' + value.strip()).encode('utf-8', errors='replace'))
    out_lines.append(b'content-length: ' + str(len(body)).encode())
    return b'\r\n'.join(out_lines) + b'\r\n\r\n' + body


async def _reparse_request_bytes(raw: bytes):
    """Turn edited-and-rebuilt request wire bytes back into (RawHeaders, RawBody)
    so every downstream branch in ``_http_session`` - including the
    wire-preserving passthrough ones that read ``req_raw``/``req_body``
    directly - sees the edit, not just the branches that reconstruct from the
    parsed parts.
    """
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()
    parsed = await _read_headers_raw(reader)
    if parsed is None:
        return None
    try:
        body = await _read_body_wire(reader, parsed.headers)
    except Exception:
        body = RawBody(wire=b'', payload=b'', was_chunked=False)
    return parsed, body


class PendingIntercept:
    """A request or response held open, awaiting a forward/drop decision from the GUI."""

    __slots__ = ('entry_id', 'stage', 'data', 'event', 'action')

    def __init__(self, entry_id: int, stage: str, data: bytes):
        self.entry_id = entry_id
        self.stage = stage
        self.data = bytearray(data)
        self.event = threading.Event()
        self.action: Optional[str] = None


def _dcz_dictionary_sha256(path: str) -> str | None:
    """Return the public Roblox compression-dictionary hash from a ``.dcz`` URL."""
    match = _DCZ_DICTIONARY_PATH_RE.search(str(path or ''))
    return match.group(1).lower() if match else None


def _decompress_dcz(body: bytes, dictionary: bytes) -> bytes | None:
    """Decode a Compression Dictionary Transport Zstandard payload.

    ``dcz`` is regular Zstandard encoded against a dictionary advertised by the
    client.  Roblox publishes these dictionaries under ClientSettings, so a
    proxy can safely preserve the representation rather than returning an
    incompatible identity response to a client that requested ``dcz``.
    """
    try:
        import zstandard

        # Roblox publishes raw dictionary content rather than a serialized
        # full-dictionary frame.  Auto-detection can misclassify newer
        # dictionaries, causing otherwise valid Player ClientSettings bodies
        # to fail decompression.
        zstd_dictionary = zstandard.ZstdCompressionDict(
            dictionary,
            dict_type=zstandard.DICT_TYPE_RAWCONTENT,
        )
        return zstandard.ZstdDecompressor(dict_data=zstd_dictionary).decompress(
            body, max_output_size=64 * 1024 * 1024
        )
    except Exception:
        return None


def _compress_dcz(body: bytes, dictionary: bytes) -> bytes | None:
    """Encode a modified ClientSettings document using the client's ``dcz`` dictionary."""
    try:
        import zstandard

        zstd_dictionary = zstandard.ZstdCompressionDict(
            dictionary,
            dict_type=zstandard.DICT_TYPE_RAWCONTENT,
        )
        return zstandard.ZstdCompressor(dict_data=zstd_dictionary).compress(body)
    except Exception:
        return None


def _build_modified_response(
    status_line: bytes,
    headers: Dict[bytes, bytes],
    body: bytes,
    content_encoding: bytes | None = None,
) -> bytes:
    """Build a response with a modified body and an explicit content length.

    Bodies are normally served without a content encoding after modification.
    A ``dcz`` ClientSettings body is the exception: retaining that encoding is
    required because the requester advertised a shared Zstandard dictionary.
    """
    lines = [status_line]
    skip = {
        b'transfer-encoding',
        b'content-length',
        b'content-encoding',
        b'content-md5',
        b'etag',
        b'x-signature-ed25519',
        b'proxy-connection',
        b'proxy-authenticate',
        b'proxy-authorization',
    }
    for k, v in headers.items():
        if k not in skip:
            lines.append(k + b': ' + v)
    if content_encoding is not None:
        lines.append(b'content-encoding: ' + content_encoding)
    lines.append(b'content-length: ' + str(len(body)).encode())
    return b'\r\n'.join(lines) + b'\r\n\r\n' + body


def _build_modified_request(req_line: bytes, headers: Dict[bytes, bytes], body: bytes) -> bytes:
    """Build an HTTP request with a MODIFIED body (always uncompressed JSON for batch)."""
    lines = [req_line]
    skip = {
        b'transfer-encoding',
        b'content-length',
        b'content-encoding',
        b'proxy-connection',
        b'proxy-authenticate',
        b'proxy-authorization',
    }
    for k, v in headers.items():
        if k not in skip:
            lines.append(k + b': ' + v)
    lines.append(b'content-length: ' + str(len(body)).encode())
    return b'\r\n'.join(lines) + b'\r\n\r\n' + body


def _auto_replace_filter_matches(value: str, filter_text: str) -> bool:
    """Host/path filter for an Auto Replace rule. Empty means unrestricted.
    A ``!=needle`` prefix negates: the rule applies when the value does NOT
    contain ``needle`` (case-insensitive substring match either way).
    """
    filter_text = (filter_text or '').strip()
    if not filter_text:
        return True
    negate = filter_text.startswith('!=')
    needle = (filter_text[2:] if negate else filter_text).strip().lower()
    if not needle:
        return True
    contains = needle in (value or '').lower()
    return (not contains) if negate else contains


def _auto_replace_rule_applies(rule: dict, direction: str, host: str, path: str) -> bool:
    if not rule.get('enabled', True):
        return False
    rule_direction = rule.get('direction') or 'both'
    if rule_direction != 'both' and rule_direction != direction:
        return False
    if not _auto_replace_filter_matches(host, rule.get('host_filter', '')):
        return False
    if not _auto_replace_filter_matches(path, rule.get('path_filter', '')):
        return False
    return bool(rule.get('match'))


def _resolve_json_path(data, path_expr: str):
    """Navigate a dot/bracket path expression (e.g. ``assets[0].id``) through
    nested dict/list JSON data. Returns (container, key) for the final
    segment so the caller can overwrite it, or None if the path doesn't
    resolve (missing key, out-of-range index, wrong container type, etc).
    """
    tokens = re.findall(r'[^.\[\]]+|\[\d+\]', path_expr)
    if not tokens:
        return None
    container = data
    for token in tokens[:-1]:
        if token.startswith('['):
            idx = int(token[1:-1])
            if not isinstance(container, list) or idx >= len(container):
                return None
            container = container[idx]
        else:
            if not isinstance(container, dict) or token not in container:
                return None
            container = container[token]
    last = tokens[-1]
    if last.startswith('['):
        idx = int(last[1:-1])
        if not isinstance(container, list) or idx >= len(container):
            return None
        return container, idx
    if not isinstance(container, dict):
        return None
    return container, last


def _coerce_replacement_value(replacement: str):
    """A JSON path replacement is typed as plain text in the GUI - coerce it
    back to a JSON-native type so the rewritten body stays valid JSON with
    the field's original *kind* preserved (e.g. a numeric id stays a number,
    not a quoted string).
    """
    stripped = replacement.strip()
    if re.fullmatch(r'-?\d+', stripped):
        return int(stripped)
    if re.fullmatch(r'-?\d+\.\d+', stripped):
        return float(stripped)
    if stripped.lower() in ('true', 'false'):
        return stripped.lower() == 'true'
    if stripped.lower() == 'null':
        return None
    return replacement


def apply_auto_replace_rules(
    rules: Iterable[dict], direction: str, host: str, path: str, body: bytes
) -> Tuple[bytes, bool]:
    """Run the body-affecting Auto Replace rules (plain text / regex / JSON
    path) against a decompressed request/response body. Rules are applied in
    order; each matching rule (enabled, direction, host/path filters)
    transforms the OUTPUT of the previous one. A rule that raises (bad
    regex, invalid JSON, path that doesn't resolve) is logged and skipped -
    one broken rule never blocks the others or breaks the traffic it's
    applied to. Header/query-param rules are handled separately (see
    apply_auto_replace_header_rules / apply_auto_replace_query_rules) since
    they target headers/the URL, not the body.
    """
    if not rules or not body:
        return body, False
    from ..utils import log_buffer

    changed = False
    result = body
    for rule in rules:
        rule_type = rule.get('type') or 'plain'
        if rule_type not in ('plain', 'regex', 'json_path'):
            continue
        if not _auto_replace_rule_applies(rule, direction, host, path):
            continue
        match = rule.get('match') or ''
        replacement = rule.get('replacement') or ''
        try:
            if rule_type == 'regex':
                new_result = re.sub(
                    match, replacement, result.decode('utf-8', errors='replace')
                ).encode('utf-8')
            elif rule_type == 'json_path':
                data = json.loads(result)
                resolved = _resolve_json_path(data, match)
                if resolved is None:
                    continue
                container, key = resolved
                container[key] = _coerce_replacement_value(replacement)
                new_result = json.dumps(data).encode('utf-8')
            else:
                new_result = result.replace(
                    match.encode('utf-8', errors='replace'),
                    replacement.encode('utf-8', errors='replace'),
                )
        except Exception as exc:
            log_buffer.log('AutoReplace', f'Rule {match!r} -> {replacement!r} failed: {exc}')
            continue
        if new_result != result:
            changed = True
            result = new_result
    return result, changed


def apply_auto_replace_header_rules(
    rules: Iterable[dict], direction: str, host: str, path: str, headers: Dict[bytes, bytes]
) -> Tuple[Dict[bytes, bytes], bool]:
    """Run 'Header' type Auto Replace rules: sets a header's value (matched
    by name, case-insensitive) - adds it if it wasn't already there.
    """
    if not rules:
        return headers, False
    changed = False
    result = dict(headers)
    for rule in rules:
        if (rule.get('type') or 'plain') != 'header':
            continue
        if not _auto_replace_rule_applies(rule, direction, host, path):
            continue
        header_name = (rule.get('match') or '').strip().lower().encode('ascii', errors='replace')
        if not header_name:
            continue
        new_value = (rule.get('replacement') or '').encode('utf-8', errors='replace')
        if result.get(header_name) != new_value:
            result[header_name] = new_value
            changed = True
    return result, changed


def apply_auto_replace_query_rules(
    rules: Iterable[dict], host: str, path: str
) -> Tuple[str, bool]:
    """Run 'Query param' type Auto Replace rules: sets a query string
    parameter's value (matched by name) in a request's path - adds it
    (appended) if it wasn't already there. Only meaningful for requests -
    there's no equivalent concept on a response.
    """
    if not rules:
        return path, False
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    changed = False
    result_path = path
    for rule in rules:
        if (rule.get('type') or 'plain') != 'query_param':
            continue
        if not _auto_replace_rule_applies(rule, 'request', host, result_path):
            continue
        param_name = (rule.get('match') or '').strip()
        if not param_name:
            continue
        replacement_value = rule.get('replacement') or ''
        split = urlsplit(result_path)
        pairs = parse_qsl(split.query, keep_blank_values=True)
        found = False
        new_pairs = []
        for k, v in pairs:
            if k == param_name:
                new_pairs.append((k, replacement_value))
                found = True
            else:
                new_pairs.append((k, v))
        if not found:
            new_pairs.append((param_name, replacement_value))
        if new_pairs != pairs:
            result_path = urlunsplit(('', '', split.path, urlencode(new_pairs), split.fragment))
            changed = True
    return result_path, changed


def _format_exc(exc: Exception) -> str:
    text = str(exc)
    return f'{type(exc).__name__}: {text}' if text else type(exc).__name__


def _parse_status_code(status_line: bytes) -> int:
    try:
        return int(status_line.split(b' ', 2)[1])
    except Exception:
        return 0


class _ResponseTrackingWriter:
    """Wraps a client-facing StreamWriter to tally status/size/duration onto a
    live request-log entry, without having to touch every response branch in
    ``_http_session`` (there are many, and they all end up calling ``write``).
    """

    def __init__(self, writer, proxy: 'FleasionProxy'):
        self._writer = writer
        self._proxy = proxy
        self._entry: Optional[dict] = None
        self._start = 0.0
        self._status_captured = False
        self._hold = False
        self._held_buffer: Optional[bytearray] = None

    def begin(self, entry: Optional[dict], hold: bool = False) -> None:
        self._entry = entry
        self._start = time.time()
        self._status_captured = False
        if entry is not None:
            entry['response_raw'] = bytearray()
        self._hold = hold
        self._held_buffer = bytearray() if hold else None

    def write(self, data: bytes) -> None:
        entry = self._entry
        if entry is None or not data:
            if not self._hold:
                self._writer.write(data)
            return
        entry['size'] = entry.get('size', 0) + len(data)
        if not self._status_captured:
            status = _parse_status_code(data.split(b'\r\n', 1)[0])
            if status:
                entry['status'] = status
                self._status_captured = True
        entry['ms'] = round((time.time() - self._start) * 1000)
        buf = entry.get('response_raw')
        if buf is None:
            buf = bytearray()
            entry['response_raw'] = buf
        if len(buf) < _PREVIEW_CAPTURE_CAP:
            buf.extend(data[: _PREVIEW_CAPTURE_CAP - len(buf)])

        if self._hold:
            self._held_buffer.extend(data)
        else:
            self._writer.write(data)

    async def flush_pending_response(self) -> None:
        """Release a held response: pause for the GUI's forward/drop decision,
        then send whatever was decided (edited or as-is) - or nothing, on drop.

        Called once per request cycle (top of the next iteration, and once
        more when the connection ends) so it works no matter which of
        ``_http_session``'s many response branches produced the bytes.
        """
        if not self._hold or self._held_buffer is None:
            return
        entry = self._entry
        held = bytes(self._held_buffer)
        self._hold = False
        self._held_buffer = None
        if entry is None:
            self._writer.write(held)
            return
        pending = self._proxy._create_pending(entry, 'response', held)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, pending.event.wait)
        self._proxy._resolve_pending(entry, 'response')
        if pending.action == 'drop':
            entry['response_raw'] = bytearray(pending.data)
            entry['dropped_response'] = True
            try:
                self._writer.close()
            except Exception:
                pass
            return
        final_bytes = bytes(pending.data)
        entry['response_raw'] = bytearray(final_bytes)
        entry['size'] = len(final_bytes)
        status = _parse_status_code(final_bytes.split(b'\r\n', 1)[0])
        if status:
            entry['status'] = status
        self._writer.write(final_bytes)
        await self._writer.drain()

    def __getattr__(self, name):
        return getattr(self._writer, name)


def _without_conditional_client_settings_headers(headers: Dict[bytes, bytes]) -> Dict[bytes, bytes]:
    """Make one ClientSettings request fetch a current body instead of HTTP 304."""
    conditional_headers = {b'if-none-match', b'if-modified-since'}
    return {key: value for key, value in headers.items() if key not in conditional_headers}


_BROWSER_BYPASS_CUSTOM_FFLAGS_HEADER = b'x-fleasion-bypass-custom-fflags'


def _without_internal_client_settings_headers(headers: Dict[bytes, bytes]) -> Dict[bytes, bytes]:
    """Remove Fleasion-only ClientSettings headers before contacting Roblox."""
    return {
        key: value
        for key, value in headers.items()
        if key != _BROWSER_BYPASS_CUSTOM_FFLAGS_HEADER
    }


def _body_log_snippet(body: bytes, limit: int = 256) -> str:
    if not body:
        return ''
    text = body[:limit].decode('utf-8', errors='replace')
    text = text.replace('\r', '\\r').replace('\n', '\\n')
    if len(body) > limit:
        text += '…'
    return text


def _is_empty_json_array(body: bytes) -> bool:
    return body.strip() == b'[]'


def _make_proxy_error_response(status_code: int, message: str) -> bytes:
    reason_map = {
        400: 'Bad Request',
        403: 'Forbidden',
        404: 'Not Found',
        502: 'Bad Gateway',
        503: 'Service Unavailable',
        504: 'Gateway Timeout',
    }
    reason = reason_map.get(status_code, 'Proxy Error')
    body = message.encode('utf-8', errors='replace')
    return (
        f'HTTP/1.1 {status_code} {reason}\r\n'
        'Content-Type: text/plain; charset=utf-8\r\n'
        f'Content-Length: {len(body)}\r\n'
        'Connection: close\r\n'
        '\r\n'
    ).encode('ascii') + body


async def _read_headers_raw(reader: asyncio.StreamReader) -> Optional[RawHeaders]:
    """Read one HTTP header block, preserving the exact wire header bytes."""
    raw = bytearray()

    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=15.0)
        except Exception:
            return None
        if not line:
            return None

        raw += line
        if line in (b'\r\n', b'\n'):
            break
        if len(raw) > 1024 * 1024:
            raise ValueError('HTTP header block too large')

    lines = bytes(raw).splitlines()
    if not lines:
        return None

    first_line = lines[0].rstrip(b'\r\n')
    headers: Dict[bytes, bytes] = {}
    for line in lines[1:]:
        stripped = line.rstrip(b'\r\n')
        if not stripped or b':' not in stripped:
            continue
        k, _, v = stripped.partition(b':')
        headers[k.strip().lower()] = v.strip()

    return RawHeaders(
        first_line=first_line,
        headers=headers,
        raw_header_block=bytes(raw),
    )


async def _read_headers(
    reader: asyncio.StreamReader,
) -> Optional[Tuple[bytes, Dict[bytes, bytes]]]:
    """Compatibility wrapper returning (first_line, lowercase_headers)."""
    raw = await _read_headers_raw(reader)
    if raw is None:
        return None
    return raw.first_line, raw.headers


async def _read_body_wire(reader: asyncio.StreamReader, headers: Dict[bytes, bytes]) -> RawBody:
    """Read an HTTP body, preserving wire bytes and exposing dechunked payload."""
    te = headers.get(b'transfer-encoding', b'').lower()
    cl_raw = headers.get(b'content-length', b'')

    if b'chunked' in te:
        wire = bytearray()
        payload = bytearray()
        while True:
            try:
                size_line = await reader.readline()
            except Exception:
                break
            if not size_line:
                break
            wire += size_line
            size_str = size_line.strip().split(b';')[0]
            try:
                chunk_size = int(size_str, 16)
            except ValueError:
                break
            if chunk_size == 0:
                while True:
                    trailer_line = await reader.readline()
                    if not trailer_line:
                        break
                    wire += trailer_line
                    if trailer_line in (b'\r\n', b'\n'):
                        break
                break
            try:
                chunk = await reader.readexactly(chunk_size)
            except asyncio.IncompleteReadError as exc:
                wire += exc.partial
                payload += exc.partial
                break
            try:
                crlf = await reader.readexactly(2)
            except asyncio.IncompleteReadError as exc:
                crlf = exc.partial
            wire += chunk + crlf
            payload += chunk
        return RawBody(wire=bytes(wire), payload=bytes(payload), was_chunked=True)

    if cl_raw:
        try:
            length = int(cl_raw)
        except ValueError:
            return RawBody(wire=b'', payload=b'', was_chunked=False)
        if length <= 0:
            return RawBody(wire=b'', payload=b'', was_chunked=False)
        try:
            body = await reader.readexactly(length)
        except asyncio.IncompleteReadError as exc:
            body = exc.partial
        return RawBody(wire=body, payload=body, was_chunked=False)

    return RawBody(wire=b'', payload=b'', was_chunked=False)


async def _read_body_raw(reader: asyncio.StreamReader, headers: Dict[bytes, bytes]) -> bytes:
    """Compatibility wrapper returning the dechunked, still-compressed payload."""
    return (await _read_body_wire(reader, headers)).payload


def _reassemble_raw_response(
    status_line: bytes, headers: Dict[bytes, bytes], body_raw: bytes
) -> bytes:
    """Reconstruct an HTTP response forwarding the ORIGINAL body bytes.
    Strips only hop-by-hop headers but preserves content-encoding and content-length.
    """
    lines = [status_line]
    hop_by_hop = {
        b'proxy-connection',
        b'proxy-authenticate',
        b'proxy-authorization',
        b'transfer-encoding',
    }  # we already dechunked, switch to content-length
    for k, v in headers.items():
        if k not in hop_by_hop:
            lines.append(k + b': ' + v)
    # Replace/add content-length (body_raw is already dechunked)
    if b'content-length' not in headers:
        lines.append(b'content-length: ' + str(len(body_raw)).encode())
    return b'\r\n'.join(lines) + b'\r\n\r\n' + body_raw


def _reassemble_raw_request(req_line: bytes, headers: Dict[bytes, bytes], body_raw: bytes) -> bytes:
    """Reconstruct an HTTP request after reading/dechunking its body.

    For bodyless requests, do not inject Content-Length: 0 unless the client
    originally sent a body framing header. Roblox/libcurl is usually fine either
    way, but preserving request shape reduces edge-case behavior.
    """
    lines = [req_line]

    hop_by_hop = {
        b'proxy-connection',
        b'proxy-authenticate',
        b'proxy-authorization',
        b'transfer-encoding',
    }

    had_body_framing = b'content-length' in headers or b'transfer-encoding' in headers

    for k, v in headers.items():
        if k in hop_by_hop:
            continue
        if k == b'content-length':
            continue
        lines.append(k + b': ' + v)

    if body_raw or had_body_framing:
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
            from ..cache.tools.solidmodel_converter.obj_to_mesh import (
                get_or_create_mesh_from_obj,
            )

            path = get_or_create_mesh_from_obj(path)
        except Exception:
            pass
    return strip_roblox_metadata(path, path.read_bytes()) if path.exists() else b''


def _serve_local_file(local_path: str) -> bytes:
    path = Path(local_path)
    if path.suffix.lower() == '.obj':
        try:
            from ..cache.tools.solidmodel_converter.obj_to_mesh import (
                get_or_create_mesh_from_obj,
            )

            path = get_or_create_mesh_from_obj(path)
        except Exception as exc:
            logger.debug('OBJ->mesh conversion failed: %s', exc)
    if not path.exists():
        return b'HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\nconnection: keep-alive\r\n\r\n'
    content = strip_roblox_metadata(path, path.read_bytes())
    ext = path.suffix.lower()
    ct_map = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.ogg': 'audio/ogg',
        '.mp3': 'audio/mpeg',
        '.wav': 'audio/wav',
        '.rbxm': 'application/octet-stream',
        '.rbxmx': 'application/xml',
        '.mesh': 'application/octet-stream',
    }
    ct = ct_map.get(ext, 'application/octet-stream')
    return (
        f'HTTP/1.1 200 OK\r\nContent-Type: {ct}\r\n'
        f'Content-Length: {len(content)}\r\nConnection: keep-alive\r\n\r\n'
    ).encode() + content


def _make_redirect(target_url: str) -> bytes:
    return (
        b'HTTP/1.1 302 Found\r\nLocation: '
        + target_url.encode()
        + b'\r\nContent-Length: 0\r\nConnection: keep-alive\r\n\r\n'
    )


def _make_local_response(status_code: int = 204, body: bytes = b'') -> bytes:
    reason_map = {
        200: 'OK',
        204: 'No Content',
        400: 'Bad Request',
        403: 'Forbidden',
        404: 'Not Found',
        500: 'Internal Server Error',
    }
    reason = reason_map.get(status_code, 'OK')
    base = f'HTTP/1.1 {status_code} {reason}\r\n'.encode('ascii')
    if body:
        return (
            base
            + b'Content-Type: application/json\r\n'
            + f'Content-Length: {len(body)}\r\n'.encode('ascii')
            + b'Connection: keep-alive\r\n\r\n'
            + body
        )
    return base + b'Content-Length: 0\r\nConnection: keep-alive\r\n\r\n'


# ProxyFlow: lightweight mock flow object passed to module interceptors


class _FlowHeaders:
    """Minimal case-insensitive header accessor for module interceptors."""

    def __init__(self, headers: Dict[bytes, bytes]) -> None:
        self._h: Dict[bytes, bytes] = {k.lower(): v for k, v in headers.items()}

    def get(self, key: str, default: str = '') -> str:
        v = self._h.get(key.lower().encode('ascii', errors='replace'))
        if v is None:
            return default
        return v.decode('ascii', errors='replace')

    def __setitem__(self, key: str, value: str) -> None:
        self._h[key.lower().encode('ascii', errors='replace')] = (
            value.encode('ascii', errors='replace') if isinstance(value, str) else value
        )

    def __getitem__(self, key: str) -> str:
        v = self._h[key.lower().encode('ascii', errors='replace')]
        return v.decode('ascii', errors='replace')

    def to_bytes_dict(self) -> Dict[bytes, bytes]:
        return dict(self._h)


class _FlowRequest:
    def __init__(
        self, first_line: bytes, headers: Dict[bytes, bytes], body: bytes, host: str
    ) -> None:
        parts = first_line.split(b' ', 2)
        self._method: bytes = parts[0] if parts else b'POST'
        self._original_path: str = (
            parts[1].decode('ascii', errors='replace') if len(parts) > 1 else '/'
        )
        self._path: str = self._original_path
        self._host: str = host
        self._body: bytes = body
        self.headers: _FlowHeaders = _FlowHeaders(headers)

    @property
    def content(self) -> bytes:
        return self._body

    @property
    def raw_content(self) -> bytes:
        return self._body

    @raw_content.setter
    def raw_content(self, value: bytes) -> None:
        self._body = value

    @property
    def pretty_url(self) -> str:
        return f'https://{self._host}{self._path}'

    @property
    def url(self) -> str:
        return f'https://{self._host}{self._path}'

    @url.setter
    def url(self, value: str) -> None:
        from urllib.parse import urlparse as _urlparse

        self._path = _urlparse(value).path

    def _get_modified_first_line(self, original: bytes) -> bytes:
        if self._path == self._original_path:
            return original
        parts = original.split(b' ', 2)
        if len(parts) >= 3:
            return parts[0] + b' ' + self._path.encode('ascii') + b' ' + parts[2]
        return original


class _FlowResponse:
    def __init__(self, status_line: bytes, body: bytes) -> None:
        parts = status_line.split(b' ', 2)
        try:
            self.status_code: int = int(parts[1])
        except IndexError, ValueError:
            self.status_code = 200
        self.content: bytes = body

    def json(self):
        import json as _json

        return _json.loads(self.content)


class ProxyFlow:
    """Minimal flow object passed to module interceptors (request + response hooks)."""

    def __init__(
        self, req_first: bytes, req_headers: Dict[bytes, bytes], body: bytes, host: str
    ) -> None:
        self.request: _FlowRequest = _FlowRequest(req_first, req_headers, body, host)
        self.response: Optional[_FlowResponse] = None
        self.drop_request: bool = False
        self.drop_status_code: int = 204
        self.drop_body: bytes = b''


class FleasionProxy:
    """Direct TLS-terminating asyncio proxy for Roblox asset hosts."""

    def __init__(
        self,
        texture_stripper: 'TextureStripper',
        cache_scraper: 'CacheScraper',
        host_certs: Dict[str, Tuple[Path, Path]],
        upstream_endpoints: Optional[Dict[str, Sequence[UpstreamEndpoint | str]]] = None,
        default_cert: Optional[Tuple[Path, Path]] = None,
        port: int = 443,
        max_workers: int = 8,
        upstream_ips: Optional[Dict[str, List[str]]] = None,
        upstream_mode: str | UpstreamMode = UpstreamMode.AUTO,
        system_http_proxy: Optional[HttpProxyConfig] = None,
        manual_http_proxy: Optional[HttpProxyConfig] = None,
        manual_socks5_proxy: Optional[Socks5ProxyConfig] = None,
        wire_preserving_passthrough: bool = False,
        explicit_proxy: bool = False,
        intercept_hosts: Optional[Iterable[str]] = None,
        vpn_compat_max_assetdelivery_connections: int = 16,
        vpn_compat_max_cdn_connections: int = 32,
        custom_fflag_modifier=None,
        on_upstream_connect_failure: Optional[Callable[[str, str], None]] = None,
        ca_cert_path: Optional[Path] = None,
        ca_key_path: Optional[Path] = None,
        cert_cache_dir: Optional[Path] = None,
        intercept_all_hosts: bool = False,
        intercept_excluded_hosts: Optional[Iterable[str]] = None,
        auto_replace_rules: Optional[Iterable[dict]] = None,
    ) -> None:
        self.texture_stripper = texture_stripper
        self.cache_scraper = cache_scraper
        self.custom_fflag_modifier = custom_fflag_modifier
        self.port = port
        self._module_interceptors: List = []
        if upstream_endpoints is None:
            upstream_endpoints = upstream_ips or {}
        self._upstream_endpoints = normalize_endpoints(upstream_endpoints)
        self._server: Optional[asyncio.Server] = None
        self._servers: List[asyncio.Server] = []
        self._listening_loopbacks: set[str] = set()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix='fleasion-cpu'
        )
        self._sni_diagnostics_seen: set[str] = set()
        self._fallback_diagnostics_seen: set[tuple[str, str]] = set()
        self._client_settings_dictionary_cache: Dict[str, bytes] = {}
        self._wire_preserving_passthrough = bool(wire_preserving_passthrough)
        self._explicit_proxy = bool(explicit_proxy)
        self._intercept_hosts: frozenset = (
            frozenset(intercept_hosts) if intercept_hosts is not None else INTERCEPT_HOSTS
        )
        self._intercept_all_hosts = bool(intercept_all_hosts)
        self._intercept_excluded_hosts = frozenset(
            str(host).strip().lower().rstrip('.')
            for host in (intercept_excluded_hosts or ())
            if str(host).strip()
        )
        self._auto_replace_rules: List[dict] = list(auto_replace_rules) if auto_replace_rules else []
        self._ca_cert_path = ca_cert_path
        self._ca_key_path = ca_key_path
        self._cert_cache_dir = cert_cache_dir
        self._request_log_lock = threading.Lock()
        self._request_log: List[dict] = []
        self._request_log_max = 4000
        self._next_entry_id = 0
        self._intercept_match_text = ''
        self._pending_lock = threading.Lock()
        self._pending: Dict[Tuple[int, str], PendingIntercept] = {}
        self._last_gamejoin_time: float = 0.0
        self._last_asset_traffic_time: float = 0.0
        self._asset_diag_generation: int = 0
        self._on_upstream_connect_failure = on_upstream_connect_failure
        self._upstream_connect_failure_notified = False

        asset_limit = max(1, int(vpn_compat_max_assetdelivery_connections or 16))
        cdn_limit = max(1, int(vpn_compat_max_cdn_connections or 32))
        self._upstream_host_limits = {
            ASSET_DELIVERY_HOST: asyncio.Semaphore(asset_limit),
            PROFILE_API_HOST: asyncio.Semaphore(asset_limit),
            'clientsettingscdn.roblox.com': asyncio.Semaphore(asset_limit),
            'clientsettings.roblox.com': asyncio.Semaphore(asset_limit),
            'contentdelivery.roblox.com': asyncio.Semaphore(asset_limit),
            'fts.rbxcdn.com': asyncio.Semaphore(cdn_limit),
        }

        self._direct_connector = DirectIpConnector()
        self._system_http_connector: Optional[BaseUpstreamConnector] = (
            HttpConnectConnector(system_http_proxy, method='system_http_connect')
            if system_http_proxy is not None
            else None
        )
        self._manual_http_connector: Optional[BaseUpstreamConnector] = (
            HttpConnectConnector(manual_http_proxy) if manual_http_proxy is not None else None
        )
        self._manual_socks5_connector: Optional[BaseUpstreamConnector] = (
            Socks5Connector(manual_socks5_proxy) if manual_socks5_proxy is not None else None
        )
        self._upstream_mode = normalize_upstream_mode(upstream_mode)
        self._connector = self._build_connector()

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

        if default_cert is None:
            raise ValueError('default_cert is required')
        default_cert_path, default_key_path = default_cert
        self._server_ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._server_ssl_ctx.load_cert_chain(str(default_cert_path), str(default_key_path))
        self._server_ssl_ctx.verify_mode = ssl.CERT_NONE
        self._server_ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self._server_ssl_ctx.set_alpn_protocols(['http/1.1'])
        self._server_ssl_ctx.set_servername_callback(self._sni_callback)

    def _log_sni_once(self, key: str, message: str) -> None:
        if key in self._sni_diagnostics_seen:
            return
        self._sni_diagnostics_seen.add(key)
        try:
            from ..utils import log_buffer

            log_buffer.log('TLS', message)
        except Exception:
            logger.debug(message)

    def _notify_upstream_connect_failure_once(self, host: str, error: str) -> None:
        if self._upstream_connect_failure_notified or self._on_upstream_connect_failure is None:
            return
        self._upstream_connect_failure_notified = True
        try:
            self._on_upstream_connect_failure(host, error)
        except Exception as exc:
            try:
                from ..utils import log_buffer

                log_buffer.log('Proxy', f'Failed to report upstream connection failure: {exc}')
            except Exception:
                logger.debug('Failed to report upstream connection failure: %s', exc)

    def _sni_callback(
        self, ssl_obj, server_name: Optional[str], initial_ctx: ssl.SSLContext
    ) -> None:
        name = (server_name or '').lower()
        if name in self._intercept_excluded_hosts:
            self._log_sni_once(
                f'excluded:{name}',
                f'SNI {name} is excluded from interception; using tunnel passthrough',
            )
            return
        if name in self._host_ssl_ctxs:
            ssl_obj.context = self._host_ssl_ctxs[name]
            self._log_sni_once(
                f'known:{name}', f'SNI matched {name}; using host-specific certificate'
            )
            return
        if name and self._intercept_all_hosts:
            ctx = self._get_or_generate_host_ctx(name)
            if ctx is not None:
                ssl_obj.context = ctx
                self._log_sni_once(
                    f'generated:{name}', f'Generated an on-the-fly certificate for {name}'
                )
                return
        if name:
            self._log_sni_once(
                f'unknown:{name}',
                f'SNI {name} is not intercepted; using default multi-host certificate',
            )
        else:
            self._log_sni_once(
                'missing',
                'Client connected without SNI; using default multi-host certificate',
            )

    def _get_or_generate_host_ctx(self, host: str) -> Optional[ssl.SSLContext]:
        """Generate (and cache) a leaf cert signed by our own CA for a host we
        weren't pre-provisioned for, so intercept-all mode can TLS-terminate
        literally anything Roblox connects to, not just the fixed feature set.
        """
        if self._ca_cert_path is None or self._ca_key_path is None or self._cert_cache_dir is None:
            return None
        try:
            from ..utils.certs import generate_host_cert

            cert_path, key_path = generate_host_cert(
                host, self._ca_cert_path, self._ca_key_path, self._cert_cache_dir
            )
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cert_path), str(key_path))
            ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.set_alpn_protocols(['http/1.1'])
        except Exception as exc:
            try:
                from ..utils import log_buffer

                log_buffer.log('TLS', f'Could not generate a certificate for {host}: {exc}')
            except Exception:
                logger.debug('Could not generate a certificate for %s: %s', host, exc)
            return None
        self._host_ssl_ctxs[host] = ctx
        return ctx

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            host='127.0.0.1',
            port=self.port,
            ssl=None if self._explicit_proxy else self._server_ssl_ctx,
            backlog=256,
            reuse_address=True,
        )
        # Port 0 is used by the Windows Env Proxy fallback. Capture the
        # kernel-assigned port before opening the IPv6 companion listener.
        self.port = int(self._server.sockets[0].getsockname()[1])
        self._servers = [self._server]
        self._listening_loopbacks = {'127.0.0.1'}
        mode = 'HTTP CONNECT' if self._explicit_proxy else 'TLS'
        logger.info('Fleasion proxy listening on 127.0.0.1:%d (%s)', self.port, mode)

        try:
            ipv6_server = await asyncio.start_server(
                self._handle_client,
                host='::1',
                port=self.port,
                ssl=None if self._explicit_proxy else self._server_ssl_ctx,
                backlog=256,
                reuse_address=True,
            )
            self._servers.append(ipv6_server)
            self._listening_loopbacks.add('::1')
            logger.info('Fleasion proxy listening on [::1]:%d (%s)', self.port, mode)
        except OSError as exc:
            try:
                from ..utils import log_buffer

                log_buffer.log(
                    'Proxy',
                    f'IPv6 loopback listener unavailable on [::1]:{self.port}: {exc}',
                )
            except Exception:
                logger.debug('IPv6 loopback listener unavailable on [::1]:%d: %s', self.port, exc)

    async def serve_forever(self) -> None:
        if not self._servers:
            return
        await asyncio.gather(*(server.serve_forever() for server in self._servers))

    def set_module_interceptors(self, interceptors: List) -> None:
        """Set the list of module interceptors for gamejoin traffic hooks."""
        self._module_interceptors = list(interceptors)

    def set_upstream_endpoints(
        self, endpoints: Dict[str, Sequence[UpstreamEndpoint | str]]
    ) -> None:
        self._upstream_endpoints = normalize_endpoints(endpoints)

    def set_intercept_hosts(self, hosts: Iterable[str]) -> None:
        """Update the feature-gated host set used by explicit-proxy CONNECT handling."""
        self._intercept_hosts = frozenset(hosts)

    def _record_request(
        self, host: str, port: int, method: str, path: str, intercepted: bool
    ) -> dict:
        """Append one row per request/tunnel seen through the explicit proxy.

        Intercepted (TLS-terminated) hosts get one entry per actual HTTP request.
        Tunneled hosts can't be decrypted, so they get one entry per CONNECT
        (i.e. per tunneled connection) instead. The returned dict is the live
        entry itself, so callers can fill in status/size/ms as they become
        known instead of having to touch every response code path.
        """
        with self._request_log_lock:
            entry_id = self._next_entry_id
            self._next_entry_id += 1
            entry = {
                'id': entry_id,
                'time': time.time(),
                'host': host,
                'port': port,
                'method': method,
                'path': path,
                'intercepted': intercepted,
                'status': None,
                'size': 0,
                'ms': None,
                'request_raw': None,
                'response_raw': None,
                'pending_stage': None,
                'was_intercepted': False,
            }
            self._request_log.append(entry)
            overflow = len(self._request_log) - self._request_log_max
            if overflow > 0:
                del self._request_log[:overflow]
        return entry

    def get_request_log(self) -> List[dict]:
        """Return a snapshot of every request/tunnel the explicit proxy has logged."""
        with self._request_log_lock:
            return [dict(entry) for entry in self._request_log]

    def clear_request_log(self) -> None:
        with self._request_log_lock:
            self._request_log.clear()

    def format_request_preview(self, entry: dict) -> str:
        """Human-readable request text for a request-log entry, for the Proxy tab."""
        raw = entry.get('request_raw')
        if not raw:
            return ''
        return asyncio.run(_format_raw_http_message(bytes(raw)))

    def format_response_preview(self, entry: dict) -> str:
        """Human-readable response text for a request-log entry, for the Proxy tab."""
        raw = entry.get('response_raw')
        if not raw:
            return ''
        return asyncio.run(_format_raw_http_message(bytes(raw)))

    async def replay_request(self, entry_id: int, raw_request: bytes, host: str) -> Optional[dict]:
        """Resend a captured (or edited) request to *host* fresh, overwriting
        the SAME log entry's request/response fields in place - no new row.
        Must be scheduled onto this proxy's own event loop (e.g. via
        ``run_coroutine_threadsafe``), since it shares upstream connection
        limits/state with the live traffic path.
        """
        entry = None
        with self._request_log_lock:
            for candidate in self._request_log:
                if candidate['id'] == entry_id:
                    entry = candidate
                    break
        if entry is None:
            return None

        reparsed = await _reparse_request_bytes(raw_request)
        if reparsed is None:
            entry['response_raw'] = b'<replay failed: could not parse the request>'
            return entry

        req_raw, req_body = reparsed
        parts = req_raw.first_line.split(b' ', 2)
        entry['method'] = parts[0].decode('ascii', errors='replace') if parts else entry['method']
        entry['path'] = parts[1].decode('ascii', errors='replace') if len(parts) > 1 else entry['path']
        entry['request_raw'] = raw_request[:_PREVIEW_CAPTURE_CAP]
        entry['status'] = None
        entry['size'] = 0
        entry['ms'] = None

        start = time.time()
        try:
            connect_result = await self._connect_upstream(host)
            if connect_result.writer is None:
                entry['response_raw'] = (
                    f'<replay failed: {connect_result.error or "no upstream reachable"}>'.encode()
                )
                return entry
            up_reader, up_writer = connect_result.reader, connect_result.writer
            try:
                up_writer.write(req_raw.raw_header_block + req_body.wire)
                await up_writer.drain()
                resp_headers = await asyncio.wait_for(_read_headers_raw(up_reader), timeout=15.0)
                if resp_headers is None:
                    entry['response_raw'] = b'<replay: upstream sent no response>'
                    return entry
                resp_body = await _read_body_wire(up_reader, resp_headers.headers)
                full_response = resp_headers.raw_header_block + resp_body.wire
                entry['response_raw'] = full_response[:_PREVIEW_CAPTURE_CAP]
                entry['size'] = len(full_response)
                entry['status'] = _parse_status_code(resp_headers.first_line)
                entry['ms'] = round((time.time() - start) * 1000)
            finally:
                try:
                    up_writer.close()
                except Exception:
                    pass
        except Exception as exc:
            entry['response_raw'] = f'<replay error: {exc}>'.encode()
        return entry

    def set_intercept_match(self, text: str) -> None:
        """Set the host/path substring that pauses matching traffic for edit/forward/drop.

        Interception is armed purely by this being non-empty - nothing else.
        """
        self._intercept_match_text = (text or '').strip().lower()

    def set_intercept_all_hosts(self, enabled: bool) -> None:
        """Toggle whether traffic to hosts OUTSIDE Fleasion's own feature set
        (texture stripper/custom FastFlags/username spoofer/etc) also gets
        decrypted and logged. Those feature hosts always work either way -
        this only widens or narrows what ELSE is visible/interceptable.
        """
        self._intercept_all_hosts = bool(enabled)

    def set_intercept_excluded_hosts(self, hosts: Iterable[str]) -> None:
        """Update hosts that must remain CONNECT tunnels in explicit-proxy mode."""
        self._intercept_excluded_hosts = frozenset(
            str(host).strip().lower().rstrip('.')
            for host in hosts
            if str(host).strip()
        )

    def _should_intercept_explicit_host(self, host: str, port: int) -> bool:
        """Return whether an explicit-proxy CONNECT should be TLS-terminated."""
        normalized_host = (host or '').strip().lower().rstrip('.')
        return (
            port == 443
            and normalized_host not in self._intercept_excluded_hosts
            and (self._intercept_all_hosts or normalized_host in self._intercept_hosts)
        )

    def set_auto_replace_rules(self, rules: Iterable[dict]) -> None:
        """Replace the live set of Auto Replace rules (see apply_auto_replace_rules)."""
        self._auto_replace_rules = list(rules) if rules else []

    def _intercept_matches(self, host: str, path: str) -> bool:
        if not self._intercept_match_text:
            return False
        text = self._intercept_match_text
        return text in host.lower() or text in path.lower()

    def _create_pending(self, entry: dict, stage: str, data: bytes) -> PendingIntercept:
        pending = PendingIntercept(entry['id'], stage, data)
        with self._pending_lock:
            self._pending[(entry['id'], stage)] = pending
        entry['pending_stage'] = stage
        entry['was_intercepted'] = True
        return pending

    def _resolve_pending(self, entry: dict, stage: str) -> None:
        with self._pending_lock:
            self._pending.pop((entry['id'], stage), None)
        if entry.get('pending_stage') == stage:
            entry['pending_stage'] = None

    def get_pending_data(self, entry_id: int, stage: str) -> Optional[bytes]:
        """Current (possibly not-yet-submitted) editable bytes for a held request/response."""
        with self._pending_lock:
            pending = self._pending.get((entry_id, stage))
        return bytes(pending.data) if pending is not None else None

    def get_pending_intercepts(self) -> List[Tuple[int, str]]:
        """(entry_id, stage) pairs currently held open awaiting a forward/drop decision."""
        with self._pending_lock:
            return list(self._pending.keys())

    def submit_pending(
        self, entry_id: int, stage: str, action: str, edited_text: Optional[str] = None
    ) -> bool:
        """Resolve a held request/response. edited_text, if given, is the (possibly
        hand-edited) preview text - rebuilt into wire bytes here so the caller
        never has to deal with HTTP framing directly. None means "use whatever
        was already pending" (e.g. a bulk 'forward all' that didn't touch this one).
        """
        with self._pending_lock:
            pending = self._pending.get((entry_id, stage))
        if pending is None:
            return False
        if edited_text is not None:
            pending.data = bytearray(rebuild_edited_message(edited_text))
        pending.action = action
        pending.event.set()
        return True

    def _preserve_unmodified_wire_for_host(self, host: str) -> bool:
        """Whether untouched traffic for *host* must retain its original wire form.

        Browser calls to ``apis.roblox.com`` include CORS preflights and can
        depend on repeated response headers.  Reassembling an untouched API
        response from the normalized header map drops repeated headers, which
        makes the browser report a CORS failure even when Roblox accepted the
        request.  The Username Spoofer still receives and can modify its one
        profile endpoint; only untouched API traffic gets raw passthrough.
        """
        return self._wire_preserving_passthrough or host == PROFILE_API_HOST

    def upstream_endpoints_for_hosts(
        self, hosts: Sequence[str],
    ) -> Dict[str, List[UpstreamEndpoint]]:
        """Return a copy of the already-resolved routes for *hosts*.

        Hosts intercepted through the local proxy cannot safely be resolved via
        the OS resolver again: they deliberately resolve to loopback.  Keeping
        these routes available lets optional intercepts be added without
        replacing working upstream CDN/API routes with public-DNS fallbacks.
        """
        return {
            host: list(self._upstream_endpoints[host])
            for host in hosts
            if host in self._upstream_endpoints
        }

    def _build_connector(self) -> BaseUpstreamConnector:
        if self._upstream_mode == UpstreamMode.DIRECT_IP:
            return self._direct_connector
        if self._upstream_mode == UpstreamMode.SYSTEM_PROXY:
            return self._system_http_connector or UnavailableConnector(
                'system_http_connect',
                'no system HTTP proxy detected',
            )
        if self._upstream_mode == UpstreamMode.HTTP_CONNECT:
            return self._manual_http_connector or UnavailableConnector(
                UpstreamMode.HTTP_CONNECT.value,
                'manual HTTP CONNECT proxy is not configured',
            )
        if self._upstream_mode == UpstreamMode.SOCKS5:
            return self._manual_socks5_connector or UnavailableConnector(
                UpstreamMode.SOCKS5.value,
                'manual SOCKS5 proxy is not configured',
            )
        return AutoConnector(
            direct=self._direct_connector,
            system_http_proxy=self._system_http_connector,
            manual_http_proxy=self._manual_http_connector,
            manual_socks5=self._manual_socks5_connector,
        )

    async def stop(self) -> None:
        # Wake up any request/response held open awaiting a forward/drop
        # decision so its coroutine (and the executor thread it's parked on)
        # doesn't block shutdown indefinitely.
        with self._pending_lock:
            pending_items = list(self._pending.values())
            self._pending.clear()
        for pending in pending_items:
            pending.action = 'drop'
            pending.event.set()

        servers = list(self._servers)
        if self._server is not None and self._server not in servers:
            servers.append(self._server)
        for server in servers:
            server.close()
        for server in servers:
            try:
                await asyncio.wait_for(server.wait_closed(), timeout=3.0)
            except Exception:
                pass
        self._servers = []
        self._server = None
        self._listening_loopbacks = set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def loopback_ips_for_hosts(self) -> tuple[str, ...]:
        ordered = []
        for ip in ('127.0.0.1', '::1'):
            if ip in self._listening_loopbacks:
                ordered.append(ip)
        return tuple(ordered) or ('127.0.0.1',)

    def _note_asset_traffic(self) -> None:
        self._last_asset_traffic_time = time.monotonic()

    def _note_gamejoin_traffic(self) -> None:
        self._last_gamejoin_time = time.monotonic()
        self._asset_diag_generation += 1
        generation = self._asset_diag_generation
        asyncio.create_task(
            self._warn_if_asset_traffic_missing(generation, self._last_gamejoin_time)
        )

    async def _warn_if_asset_traffic_missing(self, generation: int, gamejoin_time: float) -> None:
        await asyncio.sleep(ASSET_TRAFFIC_MISSING_DIAGNOSTIC_SECONDS)
        if generation != self._asset_diag_generation:
            return
        if self._last_asset_traffic_time >= gamejoin_time:
            return
        try:
            from ..utils import log_buffer

            log_buffer.log(
                'ProxyDiag',
                'Game join traffic was intercepted, but no assetdelivery/CDN requests reached Fleasion '
                f'within {ASSET_TRAFFIC_MISSING_DIAGNOSTIC_SECONDS:.0f}s. '
                'Possible asset traffic bypass: IPv6 loopback, stale DNS cache, hosts-file protection, '
                'or security/VPN filtering.',
            )
        except Exception:
            logger.debug('No assetdelivery/CDN traffic observed after gamejoin')

    def _endpoints_for_host(
        self,
        host: str,
        max_targets: Optional[int] = None,
    ) -> list[UpstreamEndpoint]:
        endpoints = self._upstream_endpoints.get(host, []) or [UpstreamEndpoint(host=host)]
        if max_targets is not None:
            endpoints = endpoints[:max_targets]
        return endpoints

    async def _connect_upstream(
        self,
        host: str,
        *,
        timeout: float = 10.0,
        max_targets: Optional[int] = None,
    ) -> UpstreamConnectResult:
        endpoints = self._endpoints_for_host(host, max_targets=max_targets)
        sem = self._upstream_host_limits.get(host)
        if sem is None:
            return await self._connector.connect(host, endpoints, self._upstream_ssl_ctx, timeout)
        async with sem:
            return await self._connector.connect(host, endpoints, self._upstream_ssl_ctx, timeout)

    async def _open_upstream(
        self,
        host: str,
        *,
        timeout: float = 10.0,
        max_targets: Optional[int] = None,
    ) -> Tuple[
        Optional[asyncio.StreamReader],
        Optional[asyncio.StreamWriter],
        Optional[str],
        List[str],
    ]:
        result = await self._connect_upstream(host, timeout=timeout, max_targets=max_targets)
        if result.writer is not None:
            return (
                result.reader,
                result.writer,
                result.endpoint,
                list(result.prior_errors),
            )
        return None, None, None, [result.error or 'upstream connect failed']

    async def log_upstream_self_test(self, hosts: Optional[set] = None) -> None:
        from ..utils import log_buffer

        hosts_to_test = sorted(hosts or set(self._upstream_endpoints.keys()))

        matrix: list[BaseUpstreamConnector] = [self._direct_connector]
        if self._system_http_connector is not None:
            matrix.append(self._system_http_connector)
        if self._manual_http_connector is not None:
            matrix.append(self._manual_http_connector)
        if self._manual_socks5_connector is not None:
            matrix.append(self._manual_socks5_connector)

        async def probe(host: str) -> None:
            endpoints = self._endpoints_for_host(host, max_targets=3)
            first_ok_method: Optional[str] = None
            direct_failed = False

            for connector in matrix:
                result = await connector.connect(
                    host, endpoints, self._upstream_ssl_ctx, timeout=3.0
                )
                if result.writer is not None:
                    log_buffer.log(
                        'ProxyDiag',
                        f'{host} {result.method}: OK via {result.endpoint}',
                    )
                    if first_ok_method is None:
                        first_ok_method = result.method
                    try:
                        result.writer.close()
                    except Exception:
                        pass
                else:
                    log_buffer.log(
                        'ProxyDiag',
                        f'{host} {result.method}: FAILED {result.error or "unknown error"}',
                    )
                    if result.method == UpstreamMode.DIRECT_IP.value:
                        direct_failed = True

            if first_ok_method is not None:
                log_buffer.log('ProxyDiag', f'selected upstream mode for {host}: {first_ok_method}')
                if (
                    first_ok_method != UpstreamMode.DIRECT_IP.value
                    and direct_failed
                    and isinstance(self._connector, AutoConnector)
                ):
                    self._connector.prime_host(host, first_ok_method)
            elif len(matrix) == 1:
                log_buffer.log(
                    'ProxyDiag',
                    'No proxy-capable upstream transport is configured. '
                    'VPN may not route Fleasion direct-IP sockets.',
                )

        await asyncio.gather(*(probe(host) for host in hosts_to_test))

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._explicit_proxy:
            await self._handle_explicit_proxy_client(reader, writer)
            return

        from ..utils import log_buffer

        try:
            result = await asyncio.wait_for(_read_headers_raw(reader), timeout=15.0)
        except asyncio.TimeoutError:
            writer.close()
            return
        if result is None:
            writer.close()
            return
        req_first, req_headers = result.first_line, result.headers

        host_hdr = req_headers.get(b'host', b'').decode('ascii', errors='replace').lower()
        host = host_hdr.split(':')[0].strip()

        if host not in INTERCEPT_HOSTS:
            writer.close()
            return

        try:
            await self._http_session(result, reader, writer, host)
        except ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError:
            pass
        except Exception as exc:
            log_buffer.log('Proxy', f'Session error for {host}: {exc}')
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_explicit_proxy_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        from ..utils import log_buffer

        try:
            connect_headers = await asyncio.wait_for(_read_headers_raw(reader), timeout=15.0)
        except asyncio.TimeoutError:
            writer.close()
            return
        if connect_headers is None:
            writer.close()
            return

        parts = connect_headers.first_line.split()
        method = parts[0].upper() if parts else b''
        target = parts[1].decode('ascii', errors='replace') if len(parts) >= 2 else ''
        host, _sep, port_text = target.rpartition(':')
        host = host.strip('[]').lower()
        try:
            port = int(port_text)
        except ValueError:
            port = 0

        if method != b'CONNECT' or not host or not 0 < port <= 65535:
            writer.write(b'HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n')
            try:
                await writer.drain()
            finally:
                writer.close()
            return

        should_intercept = self._should_intercept_explicit_host(host, port)
        if not should_intercept:
            # Feature hosts (self._intercept_hosts) always work regardless of
            # this toggle - that's Fleasion's own texture stripper/custom
            # FastFlags/username spoofer/etc, and none of those ever reach
            # this branch (they're already covered by `should_intercept`
            # above). This branch only ever sees traffic UNRELATED to those
            # features, so it's the one gated by "log/intercept everything":
            # when off, don't even record that this connection happened.
            entry = (
                self._record_request(host, port, 'CONNECT', '', intercepted=False)
                if self._intercept_all_hosts
                else None
            )
            await self._tunnel_explicit_proxy_connection(reader, writer, host, port, log_entry=entry)
            return

        writer.write(b'HTTP/1.1 200 Connection Established\r\nProxy-Agent: Fleasion\r\n\r\n')
        await writer.drain()

        try:
            loop = asyncio.get_running_loop()
            protocol = getattr(writer, '_protocol')
            transport = writer.transport
            tls_transport = await loop.start_tls(
                transport,
                protocol,
                self._server_ssl_ctx,
                server_side=True,
                ssl_handshake_timeout=15.0,
            )
            if tls_transport is None:
                writer.close()
                return
            writer._transport = tls_transport
            if hasattr(protocol, '_over_ssl'):
                protocol._over_ssl = True
        except ConnectionResetError, BrokenPipeError, OSError:
            return
        except Exception as exc:
            log_buffer.log('Proxy', f'Explicit proxy TLS upgrade failed for {host}: {exc}')
            writer.close()
            return

        try:
            first_tls_request = await asyncio.wait_for(_read_headers_raw(reader), timeout=15.0)
        except asyncio.TimeoutError:
            writer.close()
            return
        if first_tls_request is None:
            writer.close()
            return

        try:
            await self._http_session(first_tls_request, reader, writer, host)
        except ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError:
            pass
        except Exception as exc:
            log_buffer.log('Proxy', f'Explicit proxy session error for {host}: {exc}')
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _tunnel_explicit_proxy_connection(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        host: str,
        port: int,
        log_entry: Optional[dict] = None,
    ) -> None:
        from ..utils import log_buffer

        start = time.time()

        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=10.0,
            )
        except Exception as exc:
            log_buffer.log('Proxy', f'Explicit proxy tunnel failed for {host}:{port}: {exc}')
            client_writer.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n')
            if log_entry is not None:
                log_entry['status'] = 502
                log_entry['ms'] = round((time.time() - start) * 1000)
            try:
                await client_writer.drain()
            finally:
                client_writer.close()
            return

        client_writer.write(b'HTTP/1.1 200 Connection Established\r\nProxy-Agent: Fleasion\r\n\r\n')
        await client_writer.drain()

        async def _pipe(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter, track: bool = False
        ) -> None:
            try:
                while True:
                    data = await reader.read(64 * 1024)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
                    if track and log_entry is not None:
                        log_entry['size'] = log_entry.get('size', 0) + len(data)
                        log_entry['ms'] = round((time.time() - start) * 1000)
            except ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError, OSError:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        try:
            await asyncio.gather(
                _pipe(client_reader, upstream_writer),
                _pipe(upstream_reader, client_writer, track=True),
            )
        finally:
            for tunnel_writer in (upstream_writer, client_writer):
                try:
                    tunnel_writer.close()
                except Exception:
                    pass

    async def _http_session(
        self,
        first_req: RawHeaders,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host: str,
    ) -> None:
        from ..utils import log_buffer

        writer = _ResponseTrackingWriter(writer, self)

        replacements_tuple = self.texture_stripper.config_manager.get_all_replacements()
        pending_req: Optional[RawHeaders] = first_req
        up_reader: Optional[asyncio.StreamReader] = None
        up_writer: Optional[asyncio.StreamWriter] = None
        upstream_failure_hint_logged = False

        async def ensure_upstream(path_for_log: str) -> bool:
            nonlocal up_reader, up_writer, upstream_failure_hint_logged

            if up_reader is not None and up_writer is not None and not up_writer.is_closing():
                return True

            connect_result = await self._connect_upstream(host)
            up_reader = connect_result.reader
            up_writer = connect_result.writer

            if up_reader is not None and up_writer is not None:
                if (
                    connect_result.method != UpstreamMode.DIRECT_IP.value
                    and connect_result.prior_errors
                ):
                    key = (host, connect_result.method)
                    if key not in self._fallback_diagnostics_seen:
                        self._fallback_diagnostics_seen.add(key)
                        log_buffer.log(
                            'Proxy',
                            f'Upstream direct_ip failed for {host}; using '
                            f'{connect_result.method} via {connect_result.endpoint}',
                        )
                return True

            failure_text = connect_result.error or 'no targets attempted'
            log_buffer.log(
                'Proxy',
                f'Upstream connect failed for {host}{path_for_log[:180]}; tried {failure_text}',
            )

            if host in {ASSET_DELIVERY_HOST, *CDN_HOSTS} and not upstream_failure_hint_logged:
                upstream_failure_hint_logged = True
                log_buffer.log(
                    'Proxy',
                    f'Asset delivery path is blocked: Fleasion cannot open outbound TLS to {host}. '
                    'Hosts/TLS interception may be working locally, but firewall, AV, VPN, or WFP filtering '
                    'may be blocking Fleasion.exe/Python outbound traffic.',
                )
                self._notify_upstream_connect_failure_once(host, failure_text)

            writer.write(
                _make_proxy_error_response(
                    502,
                    f'Fleasion could not connect upstream to {host}. See Fleasion logs for details.',
                )
            )
            try:
                await writer.drain()
            except Exception:
                pass
            return False

        async def fetch_client_settings_dictionary(dictionary_sha256: str) -> bytes | None:
            """Fetch and cache a public Roblox shared-compression dictionary."""
            if not re.fullmatch(r'[0-9a-f]{64}', dictionary_sha256):
                return None
            cached = self._client_settings_dictionary_cache.get(dictionary_sha256)
            if cached is not None:
                return cached

            dictionary_host = 'clientsettings.roblox.com'
            connection = await self._connect_upstream(dictionary_host)
            dictionary_reader, dictionary_writer = connection.reader, connection.writer
            if dictionary_reader is None or dictionary_writer is None:
                log_buffer.log(
                    'CustomFFlags',
                    'Could not retrieve the Roblox compression dictionary: '
                    f'{connection.error or "upstream connection failed"}',
                )
                return None

            try:
                request = (
                    f'GET /v2/compression-dictionaries/{dictionary_sha256} HTTP/1.1\r\n'
                    f'Host: {dictionary_host}\r\n'
                    'Accept: application/octet-stream\r\n'
                    'Accept-Encoding: identity\r\n'
                    'User-Agent: Fleasion/1.0\r\n'
                    'Connection: close\r\n\r\n'
                ).encode('ascii')
                dictionary_writer.write(request)
                await dictionary_writer.drain()
                response = await _read_headers_raw(dictionary_reader)
                if response is None:
                    log_buffer.log('CustomFFlags', 'Roblox compression dictionary returned no response')
                    return None
                status_code = _parse_status_code(response.first_line)
                if not 200 <= status_code < 300:
                    log_buffer.log(
                        'CustomFFlags',
                        f'Roblox compression dictionary returned HTTP {status_code}',
                    )
                    return None
                dictionary = _decompress_body(
                    (await _read_body_wire(dictionary_reader, response.headers)).payload,
                    response.headers,
                )
                if hashlib.sha256(dictionary).hexdigest() != dictionary_sha256:
                    log_buffer.log(
                        'CustomFFlags',
                        'Roblox compression dictionary integrity check failed; preserving original response',
                    )
                    return None
                self._client_settings_dictionary_cache[dictionary_sha256] = dictionary
                return dictionary
            except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError, OSError) as exc:
                log_buffer.log('CustomFFlags', f'Roblox compression dictionary fetch failed: {exc}')
                return None
            finally:
                try:
                    dictionary_writer.close()
                    await dictionary_writer.wait_closed()
                except Exception:
                    pass

        try:
            while True:
                # Release the previous iteration's held response (if any) before
                # moving on - this is the one choke point every response branch
                # below eventually passes through, on its way back here.
                await writer.flush_pending_response()

                # ── Read request ─────────────────────────────────────────────
                if pending_req is not None:
                    req_raw = pending_req
                    pending_req = None
                else:
                    result = await _read_headers_raw(reader)
                    if result is None:
                        break
                    req_raw = result

                req_first, req_headers = req_raw.first_line, req_raw.headers

                # Read request body. payload is dechunked; wire preserves chunk framing.
                req_body = await _read_body_wire(reader, req_headers)
                req_body_raw = req_body.payload

                parts = req_first.split(b' ', 2)
                method = parts[0].decode('ascii', errors='replace') if parts else ''
                path = parts[1].decode('ascii', errors='replace') if len(parts) > 1 else '/'
                # Proxy-tab visibility (logging + the pause/edit/forward/drop
                # machinery, which only makes sense on a visible row) is
                # gated by the "log/intercept all APIs" toggle - including
                # for Fleasion's own feature hosts. The feature processing
                # itself (texture stripper, custom FastFlags, gamejoin, etc.)
                # further down in this function doesn't depend on
                # _log_entry, so it keeps working either way.
                _log_entry = (
                    self._record_request(host, 443, method, path, intercepted=True)
                    if self._intercept_all_hosts
                    else None
                )
                if _log_entry is not None:
                    _log_entry['request_raw'] = (req_raw.raw_header_block + req_body_raw)[
                        :_PREVIEW_CAPTURE_CAP
                    ]

                    if self._intercept_matches(host, path):
                        _req_pending = self._create_pending(
                            _log_entry, 'request', bytes(_log_entry['request_raw'])
                        )
                        await asyncio.get_event_loop().run_in_executor(None, _req_pending.event.wait)
                        self._resolve_pending(_log_entry, 'request')
                        _edited_request = bytes(_req_pending.data)
                        _log_entry['request_raw'] = _edited_request
                        if _req_pending.action == 'drop':
                            _log_entry['dropped_request'] = True
                            _log_entry['status'] = None
                            try:
                                writer.close()
                            except Exception:
                                pass
                            break
                        _reparsed = await _reparse_request_bytes(_edited_request)
                        if _reparsed is not None:
                            req_raw, req_body = _reparsed
                            req_first, req_headers = req_raw.first_line, req_raw.headers
                            req_body_raw = req_body.payload
                            _rparts = req_first.split(b' ', 2)
                            method = (
                                _rparts[0].decode('ascii', errors='replace') if _rparts else method
                            )
                            path = (
                                _rparts[1].decode('ascii', errors='replace')
                                if len(_rparts) > 1
                                else path
                            )
                            _log_entry['method'] = method
                            _log_entry['path'] = path

                # Auto Replace runs independently of the Proxy tab's own
                # logging/intercept toggles - it's a separate, always-on
                # feature. Query-param/header/body rules are collected
                # first, then rebuilt through _reparse_request_bytes ONCE
                # (the same path a manual pending-edit takes) if any of them
                # actually changed something - that keeps every downstream
                # branch, including wire-preserving passthrough (which reads
                # req_raw/req_body directly), in sync with the replacement.
                if self._auto_replace_rules:
                    _ar_path, _query_changed = apply_auto_replace_query_rules(
                        self._auto_replace_rules, host, path
                    )
                    _ar_headers, _header_changed = apply_auto_replace_header_rules(
                        self._auto_replace_rules, 'request', host, _ar_path, req_headers
                    )
                    _req_plain = _decompress_body(req_body_raw, _ar_headers)
                    _req_replaced, _body_changed = apply_auto_replace_rules(
                        self._auto_replace_rules, 'request', host, _ar_path, _req_plain
                    )
                    _req_changed = _query_changed or _header_changed or _body_changed
                    if _req_changed:
                        _ar_req_line = req_first
                        if _query_changed:
                            _line_parts = req_first.split(b' ', 2)
                            _line_parts[1] = _ar_path.encode('ascii', errors='replace')
                            _ar_req_line = b' '.join(_line_parts)
                        _rebuilt_request = _build_modified_request(
                            _ar_req_line, _ar_headers, _req_replaced
                        )
                        _reparsed_ar = await _reparse_request_bytes(_rebuilt_request)
                        if _reparsed_ar is not None:
                            req_raw, req_body = _reparsed_ar
                            req_first, req_headers = req_raw.first_line, req_raw.headers
                            req_body_raw = req_body.payload
                            _ar_parts = req_first.split(b' ', 2)
                            method = (
                                _ar_parts[0].decode('ascii', errors='replace') if _ar_parts else method
                            )
                            path = (
                                _ar_parts[1].decode('ascii', errors='replace')
                                if len(_ar_parts) > 1
                                else path
                            )
                            if _log_entry is not None:
                                _log_entry['request_raw'] = (
                                    req_raw.raw_header_block + req_body_raw
                                )[:_PREVIEW_CAPTURE_CAP]
                                _log_entry['method'] = method
                                _log_entry['path'] = path

                writer.begin(
                    _log_entry,
                    hold=self._intercept_all_hosts and self._intercept_matches(host, path),
                )
                is_batch = host == ASSET_DELIVERY_HOST and b'/v1/assets/batch' in req_first
                bypass_custom_fflags = (
                    req_headers.get(_BROWSER_BYPASS_CUSTOM_FFLAGS_HEADER, b'').strip() == b'1'
                )
                _gamejoin_flow: Optional[ProxyFlow] = None
                _profile_flow: Optional[ProxyFlow] = None
                upstream_req_first = req_first
                upstream_req_headers = (
                    _without_internal_client_settings_headers(req_headers)
                    if bypass_custom_fflags
                    else req_headers
                )

                if (
                    host in CLIENT_SETTINGS_HOSTS
                    and self.custom_fflag_modifier is not None
                    and self.custom_fflag_modifier.is_enabled()
                    and self.custom_fflag_modifier.handles_path(path)
                    and not bypass_custom_fflags
                ):
                    encoding = req_headers.get(b'accept-encoding', b'').decode(
                        'ascii', errors='replace'
                    )
                    log_buffer.log(
                        'CustomFFlags',
                        f'Processing ClientSettings response for {path[:160]} (accept={encoding or "identity"})',
                    )
                    if self.custom_fflag_modifier.requires_fresh_response():
                        upstream_req_headers = _without_conditional_client_settings_headers(
                            req_headers
                        )
                        log_buffer.log(
                            'CustomFFlags',
                            'Requesting one fresh ClientSettings response for updated custom FastFlags',
                        )

                if host == ASSET_DELIVERY_HOST or host in CDN_HOSTS:
                    self._note_asset_traffic()

                # ── TextureStripper: CDN short-circuit (replace before upstream) ──
                # Race condition fix: the batch-request coroutine (on the assetdelivery
                # connection) and this CDN coroutine run concurrently.
                # The CDN request may arrive before the batch response has been processed
                # and its CDN URL registered in _solidmodel_injections / _local_redirects.
                # If there are pending req_ids in flight, yield briefly to the event loop
                # so the batch-response coroutine can complete its registration, then retry.
                # Without this, unreplaced assets pass through and Roblox caches them,
                # requiring multiple rejoins to achieve full replacement coverage.
                short_circuit = None
                if host in CDN_HOSTS:
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
                            _serve_path = Path(str(value))
                            _serve_exists = _serve_path.exists()
                            _serve_size = _serve_path.stat().st_size if _serve_exists else 0
                            _serve_category = (
                                'TexPackTrace'
                                if _serve_path.suffix.lower() in ('.ktx', '.ktx2')
                                else 'Local'
                            )
                            log_buffer.log(
                                _serve_category,
                                f'CDN local serve start: host={host} path={path[:160]} '
                                f'file={_serve_path.name} exists={_serve_exists} bytes={_serve_size}',
                            )
                            response = await asyncio.get_event_loop().run_in_executor(
                                self._executor, _serve_local_file, value
                            )
                            _status_line = (
                                response.split(b'\r\n', 1)[0].decode('ascii', errors='replace')
                                if response
                                else 'empty'
                            )
                            log_buffer.log(
                                _serve_category,
                                f'CDN local serve complete: host={host} path={path[:160]} '
                                f'file={_serve_path.name} status={_status_line} response_bytes={len(response)}',
                            )
                            writer.write(response)
                            await writer.drain()
                            # Cache our own served file so it appears in the scraper viewer
                            if self.cache_scraper.enabled:
                                try:
                                    _file_bytes = await asyncio.get_event_loop().run_in_executor(
                                        self._executor, _read_local_bytes, value
                                    )
                                    if _file_bytes:
                                        full_url = f'https://{host}{path}'
                                        _cache_hash = path.rsplit('/', 1)[-1].split('?')[0]
                                        self.cache_scraper.process_cdn_response(
                                            full_url,
                                            path,
                                            _file_bytes,
                                            'application/octet-stream',
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
                        # 'solid', 'solid_v3', and 'anim_rig' fall through - need upstream response

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
                        req_body_plain,
                        req_headers,
                        replacements_tuple,
                        batch_id,
                    )
                    if _is_empty_json_array(req_body_modified) and not _is_empty_json_array(
                        req_body_plain
                    ):
                        writer.write(_make_local_response(200, b'[]'))
                        await writer.drain()
                        if not _keep_alive(req_first, req_headers):
                            break
                        continue
                    if not await ensure_upstream(path):
                        break
                    up_writer.write(
                        _build_modified_request(req_first, req_headers, req_body_modified)
                    )
                elif host == GAMEJOIN_HOST:
                    # Module interceptors: allow request body/URL modification for gamejoin traffic
                    _req_body_plain = _decompress_body(req_body_raw, req_headers)
                    if self._module_interceptors:
                        _gamejoin_flow = ProxyFlow(req_first, req_headers, _req_body_plain, host)
                        for _interceptor in list(self._module_interceptors):
                            try:
                                _interceptor.request(_gamejoin_flow)
                            except Exception as _exc:
                                logger.debug('Module interceptor request error: %s', _exc)
                        if _gamejoin_flow.drop_request:
                            _drop_body = _gamejoin_flow.drop_body
                            if isinstance(_drop_body, str):
                                _drop_body = _drop_body.encode('utf-8', errors='replace')
                            writer.write(
                                _make_local_response(_gamejoin_flow.drop_status_code, _drop_body)
                            )
                            await writer.drain()
                            if not _keep_alive(req_first, req_headers):
                                break
                            continue
                        _new_first = _gamejoin_flow.request._get_modified_first_line(req_first)
                        _new_body = _gamejoin_flow.request.raw_content
                        if not await ensure_upstream(path):
                            break
                        if _new_first != req_first or _new_body != _req_body_plain:
                            up_writer.write(
                                _build_modified_request(
                                    _new_first,
                                    _gamejoin_flow.request.headers.to_bytes_dict(),
                                    _new_body,
                                )
                            )
                        else:
                            if self._wire_preserving_passthrough:
                                up_writer.write(req_raw.raw_header_block + req_body.wire)
                            else:
                                up_writer.write(
                                    _reassemble_raw_request(req_first, req_headers, req_body_raw)
                                )
                    else:
                        if not await ensure_upstream(path):
                            break
                        if self._wire_preserving_passthrough:
                            up_writer.write(req_raw.raw_header_block + req_body.wire)
                        else:
                            up_writer.write(
                                _reassemble_raw_request(req_first, req_headers, req_body_raw)
                            )
                elif (
                    host == PROFILE_API_HOST
                    and PROFILE_API_PATH_FRAGMENT in path
                    and self._module_interceptors
                ):
                    _req_body_plain = _decompress_body(req_body_raw, req_headers)
                    if not await ensure_upstream(path):
                        break
                    _profile_flow = ProxyFlow(req_first, req_headers, _req_body_plain, host)
                    if self._preserve_unmodified_wire_for_host(host):
                        up_writer.write(req_raw.raw_header_block + req_body.wire)
                    else:
                        up_writer.write(
                            _reassemble_raw_request(req_first, req_headers, req_body_raw)
                        )
                else:
                    # Forward request as-is.
                    if not await ensure_upstream(path):
                        break
                    if (
                        self._preserve_unmodified_wire_for_host(host)
                        and upstream_req_first == req_first
                        and upstream_req_headers is req_headers
                    ):
                        up_writer.write(req_raw.raw_header_block + req_body.wire)
                    else:
                        up_writer.write(
                            _reassemble_raw_request(
                                upstream_req_first, upstream_req_headers, req_body_raw
                            )
                        )

                try:
                    await up_writer.drain()
                except ConnectionResetError, BrokenPipeError, OSError:
                    break

                # ── Read upstream response ────────────────────────────────────
                resp_result = await _read_headers_raw(up_reader)
                if resp_result is None:
                    break
                resp_raw = resp_result
                resp_first, resp_headers = resp_raw.first_line, resp_raw.headers
                resp_body = await _read_body_wire(up_reader, resp_headers)
                resp_body_raw = resp_body.payload

                status_code = _parse_status_code(resp_first)
                if host == GAMEJOIN_HOST and 200 <= status_code < 400:
                    self._note_gamejoin_traffic()
                if status_code in (400, 429) and host in {
                    ASSET_DELIVERY_HOST,
                    *CDN_HOSTS,
                }:
                    ct = resp_headers.get(b'content-type', b'').decode('ascii', errors='replace')
                    retry_after = resp_headers.get(b'retry-after', b'').decode(
                        'ascii', errors='replace'
                    )
                    preview = resp_body_raw[:300].decode('utf-8', errors='replace')
                    preview = preview.replace('\r', ' ').replace('\n', ' ')
                    log_buffer.log(
                        'Proxy',
                        f'Upstream HTTP {status_code} from {host}{path[:180]} '
                        f'content-type={ct or "unknown"} body={len(resp_body_raw)} bytes '
                        f'retry-after={retry_after or "none"} preview={preview!r}',
                    )
                elif status_code >= 400 and host in {
                    ASSET_DELIVERY_HOST,
                    GAMEJOIN_HOST,
                    *CDN_HOSTS,
                }:
                    ct = resp_headers.get(b'content-type', b'').decode('ascii', errors='replace')
                    snippet = _body_log_snippet(resp_body_raw)
                    snippet_text = f' snippet={snippet}' if snippet else ''
                    log_buffer.log(
                        'Proxy',
                        f'Upstream HTTP {status_code} from {host}{path[:180]} '
                        f'content-type={ct or "unknown"} body={len(resp_body_raw)} bytes{snippet_text}',
                    )

                # ── Determine if we need to modify the response body ──────────
                # We only modify if: solidmodel injection is requested.
                # All other responses are forwarded raw (preserving content-encoding).
                response_modified = False
                modified_content_encoding: bytes | None = None

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

                elif host == ASSET_DELIVERY_HOST and not is_batch:
                    # Non-batch assetdelivery response (confirmed rare/non-existent
                    # in practice for TexturePack sub-assets after dedup fix).
                    # Still wire up the scraper hook as a fallback.
                    if self.cache_scraper.enabled:
                        resp_body_plain_nb = _decompress_body(resp_body_raw, resp_headers)
                        resp_status_code = _parse_status_code(resp_first)
                        resp_location = resp_headers.get(b'location', b'').decode(
                            'ascii', errors='replace'
                        )
                        if resp_body_plain_nb:
                            self.cache_scraper.process_direct_asset_response(
                                path,
                                resp_status_code,
                                resp_location,
                                resp_body_plain_nb,
                                resp_headers.get(b'content-type', b'').decode(
                                    'ascii', errors='replace'
                                ),
                            )

                elif host in CDN_HOSTS:
                    full_url = f'https://{host}{path}'

                    if short_circuit is not None and short_circuit[0] in (
                        'solid',
                        'solid_v3',
                    ):
                        # SolidModel injection - we MUST modify the body
                        resp_body_plain = _decompress_body(resp_body_raw, resp_headers)
                        _cdn_base_url = full_url.split('?')[0]
                        _prefer_v3 = short_circuit[0] == 'solid_v3'
                        resp_body_raw = await asyncio.get_event_loop().run_in_executor(
                            self._executor,
                            self.texture_stripper.process_solidmodel_response,
                            resp_body_plain,
                            short_circuit[1],
                            _cdn_base_url,
                            _prefer_v3,
                        )
                        response_modified = True

                    elif short_circuit is not None and short_circuit[0] == 'anim_rig':
                        # Auto-convert rig: read the original CDN bytes to detect the rig,
                        # then serve the rig-matched local replacement (or a converted copy).
                        _anim_repl_path, _required_rig = short_circuit[1]
                        _orig_bytes = _decompress_body(resp_body_raw, resp_headers)

                        def _pick_rig_matched_file(
                            orig_bytes: bytes, repl_path: str, required_rig: str = 'any'
                        ) -> bytes:
                            from ..utils import log_buffer as _lb
                            from ..utils.anim_converter import (
                                detect_player_rig,
                                detect_rig,
                                is_curve_animation,
                            )

                            orig_rig = detect_rig(orig_bytes)
                            # If this rule only targets specific rig types, skip if it doesn't match
                            if required_rig != 'any' and orig_rig not in required_rig:
                                _lb.log(
                                    'AnimConv',
                                    f'Skipping replacement: original rig={orig_rig}, required={required_rig}',
                                )
                                return orig_bytes
                            if is_curve_animation(orig_bytes):
                                # Must serve back a CurveAnimation regardless of replacement format.
                                # For non-player animations (unknown rig) use the replacement's own
                                # rig so no unwanted rig conversion is applied.
                                if orig_rig == 'unknown':
                                    target_rig = self.texture_stripper._detect_repl_rig(repl_path)
                                    if target_rig == 'unknown':
                                        target_rig = 'R15'  # last resort default
                                else:
                                    target_rig = orig_rig
                                repl_p = Path(repl_path)
                                if not repl_p.exists():
                                    _lb.log(
                                        'AnimConv',
                                        f'Replacement file not found: {repl_p.name}',
                                    )
                                    return orig_bytes
                                conv_path = self.texture_stripper._get_or_create_converted_curve(
                                    repl_path, target_rig
                                )
                                if conv_path:
                                    _lb.log(
                                        'AnimConv',
                                        f'Serving {target_rig} CurveAnimation replacement ({Path(conv_path).name})',
                                    )
                                    return Path(conv_path).read_bytes()
                                _lb.log(
                                    'AnimConv',
                                    f'CurveAnimation conversion failed for {repl_p.name} → {target_rig}',
                                )
                                return orig_bytes
                            # KeyframeSequence path: serve rig-matched replacement.
                            final_path = repl_path
                            # For non-player / mixed animations orig_rig is 'unknown' —
                            # use detect_player_rig to find which player rig they target
                            # (e.g. gun anim that moves Left Arm → R6) so we can still
                            # serve the right converted version of the replacement.
                            conv_rig = (
                                orig_rig
                                if orig_rig != 'unknown'
                                else (detect_player_rig(orig_bytes))
                            )
                            if conv_rig != 'unknown':
                                repl_rig = self.texture_stripper._detect_repl_rig(repl_path)
                                if repl_rig == 'unknown':
                                    _lb.log(
                                        'AnimConv',
                                        f'Rig detection unknown for replacement: {Path(repl_path).name}',
                                    )
                                elif repl_rig != conv_rig:
                                    conv = self.texture_stripper._get_or_create_converted(
                                        repl_path, conv_rig
                                    )
                                    if conv:
                                        final_path = conv
                            p = Path(final_path)
                            return (
                                strip_roblox_metadata(p, p.read_bytes())
                                if p.exists()
                                else orig_bytes
                            )

                        resp_body_raw = await asyncio.get_event_loop().run_in_executor(
                            self._executor,
                            _pick_rig_matched_file,
                            _orig_bytes,
                            _anim_repl_path,
                            _required_rig,
                        )
                        response_modified = True

                    if self.cache_scraper.enabled:
                        # Cache the decompressed bytes for storage
                        resp_body_for_cache = (
                            _decompress_body(resp_body_raw, resp_headers)
                            if not response_modified
                            else resp_body_raw
                        )
                        ct = resp_headers.get(b'content-type', b'').decode(
                            'ascii', errors='replace'
                        )
                        self.cache_scraper.process_cdn_response(
                            full_url, path, resp_body_for_cache, ct
                        )

                elif (
                    host in CLIENT_SETTINGS_HOSTS
                    and self.custom_fflag_modifier is not None
                    and self.custom_fflag_modifier.is_enabled()
                    and self.custom_fflag_modifier.handles_path(path)
                    and not bypass_custom_fflags
                    and 200 <= status_code < 300
                    and resp_body_raw
                ):
                    content_encoding = resp_headers.get(b'content-encoding', b'').lower()
                    if content_encoding == b'dcz':
                        dictionary_sha256 = _dcz_dictionary_sha256(path)
                        dictionary = (
                            await fetch_client_settings_dictionary(dictionary_sha256)
                            if dictionary_sha256 is not None
                            else None
                        )
                        resp_body_plain = (
                            _decompress_dcz(resp_body_raw, dictionary)
                            if dictionary is not None
                            else None
                        )
                        if resp_body_plain is None:
                            log_buffer.log(
                                'CustomFFlags',
                                'Could not decode dictionary-compressed ClientSettings; preserving original response',
                            )
                        else:
                            modified_settings = self.custom_fflag_modifier.modify_response(
                                path, resp_body_plain
                            )
                            if modified_settings != resp_body_plain:
                                recompressed = _compress_dcz(modified_settings, dictionary)
                                if recompressed is None:
                                    log_buffer.log(
                                        'CustomFFlags',
                                        'Could not re-encode dictionary-compressed ClientSettings; preserving original response',
                                    )
                                else:
                                    resp_body_raw = recompressed
                                    modified_content_encoding = b'dcz'
                                    response_modified = True
                                    log_buffer.log(
                                        'CustomFFlags',
                                        'Re-encoded custom FastFlags with the Roblox dcz dictionary',
                                    )
                    else:
                        resp_body_plain = _decompress_body(resp_body_raw, resp_headers)
                        modified_settings = self.custom_fflag_modifier.modify_response(
                            path, resp_body_plain
                        )
                        if modified_settings != resp_body_plain:
                            resp_body_raw = modified_settings
                            response_modified = True

                if (
                    host == GAMEJOIN_HOST
                    and _gamejoin_flow is not None
                    and self._module_interceptors
                ):
                    _resp_body_plain = _decompress_body(resp_body_raw, resp_headers)
                    _gamejoin_flow.response = _FlowResponse(resp_first, _resp_body_plain)
                    for _interceptor in list(self._module_interceptors):
                        try:
                            _interceptor.response(_gamejoin_flow)
                        except Exception as _exc:
                            logger.debug('Module interceptor response error: %s', _exc)
                    if (
                        _gamejoin_flow.response is not None
                        and _gamejoin_flow.response.content != _resp_body_plain
                    ):
                        resp_body_raw = _gamejoin_flow.response.content
                        response_modified = True
                elif (
                    host == PROFILE_API_HOST
                    and _profile_flow is not None
                    and self._module_interceptors
                ):
                    _resp_body_plain = _decompress_body(resp_body_raw, resp_headers)
                    _profile_flow.response = _FlowResponse(resp_first, _resp_body_plain)
                    for _interceptor in list(self._module_interceptors):
                        try:
                            _interceptor.response(_profile_flow)
                        except Exception as _exc:
                            logger.debug('Module interceptor response error: %s', _exc)
                    if (
                        _profile_flow.response is not None
                        and _profile_flow.response.content != _resp_body_plain
                    ):
                        resp_body_raw = _profile_flow.response.content
                        response_modified = True

                # Auto Replace runs last, on whatever body is about to be
                # sent (Fleasion's own feature processing above included) -
                # skipped for a dcz-recompressed ClientSettings body since
                # that's already a specific binary encoding, not something a
                # text/regex replace should touch.
                if self._auto_replace_rules and modified_content_encoding != b'dcz':
                    resp_headers, _resp_header_changed = apply_auto_replace_header_rules(
                        self._auto_replace_rules, 'response', host, path, resp_headers
                    )
                    _resp_plain = (
                        resp_body_raw
                        if response_modified
                        else _decompress_body(resp_body_raw, resp_headers)
                    )
                    _resp_replaced, _resp_body_changed = apply_auto_replace_rules(
                        self._auto_replace_rules, 'response', host, path, _resp_plain
                    )
                    if _resp_header_changed or _resp_body_changed:
                        # Whenever EITHER changed, resp_body_raw must end up
                        # as plain (decompressed) bytes - modified_content_encoding
                        # is about to be cleared, so whatever's sent must
                        # actually match "no content-encoding" being true.
                        resp_body_raw = _resp_replaced if _resp_body_changed else _resp_plain
                        response_modified = True
                        modified_content_encoding = None

                # ── Forward response to Roblox ────────────────────────────────
                if response_modified:
                    writer.write(
                        _build_modified_response(
                            resp_first,
                            resp_headers,
                            resp_body_raw,
                            content_encoding=modified_content_encoding,
                        )
                    )
                else:
                    if self._preserve_unmodified_wire_for_host(host):
                        writer.write(resp_raw.raw_header_block + resp_body.wire)
                    else:
                        writer.write(
                            _reassemble_raw_response(resp_first, resp_headers, resp_body_raw)
                        )

                try:
                    await writer.drain()
                except ConnectionResetError, BrokenPipeError, OSError:
                    break

                if not _keep_alive(req_first, req_headers) or not _keep_alive(
                    resp_first, resp_headers
                ):
                    break
        finally:
            try:
                await writer.flush_pending_response()
            except Exception:
                pass
            if up_writer is not None:
                try:
                    up_writer.close()
                except Exception:
                    pass
