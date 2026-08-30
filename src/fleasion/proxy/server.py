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

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from .addons.cache_scraper import CacheScraper
    from .addons.custom_fflags import CustomFFlagModifier
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
CLIENT_SETTINGS_HOSTS: frozenset[str] = frozenset(
    {'clientsettingscdn.roblox.com', 'clientsettings.roblox.com'}
)
CDN_HOSTS: frozenset[str] = frozenset({'fts.rbxcdn.com', 'contentdelivery.roblox.com'})
BASE_INTERCEPT_HOSTS: frozenset[str] = frozenset({ASSET_DELIVERY_HOST, GAMEJOIN_HOST, *CDN_HOSTS})
USERNAME_SPOOFER_INTERCEPT_HOSTS: frozenset[str] = frozenset({PROFILE_API_HOST})
CUSTOM_FFLAGS_INTERCEPT_HOSTS: frozenset[str] = CLIENT_SETTINGS_HOSTS
INTERCEPT_HOSTS: frozenset[str] = (
    BASE_INTERCEPT_HOSTS | USERNAME_SPOOFER_INTERCEPT_HOSTS | CUSTOM_FFLAGS_INTERCEPT_HOSTS
)
ASSET_TRAFFIC_MISSING_DIAGNOSTIC_SECONDS = 20.0
UPSTREAM_ENDPOINT_REFRESH_COOLDOWN_SECONDS = 5.0
UPSTREAM_ENDPOINT_REFRESH_RETRY_TIMEOUT = 3.0
# Temporary Windows compatibility cap for the TLS endpoint that Roblox talks
# to locally.  Keep the upstream client context unrestricted; this only
# isolates the local interception handshake while diagnosing Windows/OpenSSL
# middlebox failures.
PROXY_TLS_MAX_VERSION = ssl.TLSVersion.TLSv1_2

_ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'
_GZIP_MAGIC = b'\x1f\x8b'
_DCZ_DICTIONARY_PATH_RE = re.compile(r'/([0-9a-f]{64})\.dcz(?:$|[?])', re.IGNORECASE)


type _JsonScalar = str | int | float | bool | None
type _JsonValue = _JsonScalar | list[_JsonValue] | dict[str, _JsonValue]
type _JsonPathTarget = tuple[list[_JsonValue], int] | tuple[dict[str, _JsonValue], str]
type _AnimRequiredRig = str | frozenset[str]
type _AnimPendingValue = tuple[str, _AnimRequiredRig]
type _ReplacementKey = int | str
type _ReplacementMaps = tuple[
    dict[_ReplacementKey, int],
    set[_ReplacementKey],
    dict[_ReplacementKey, str],
    dict[_ReplacementKey, str],
]

_CREATE_PENDING_ATTR = '_create_pending'
_RESOLVE_PENDING_ATTR = '_resolve_pending'
_DETECT_REPL_RIG_ATTR = '_detect_repl_rig'
_GET_CONVERTED_CURVE_ATTR = '_get_or_create_converted_curve'
_GET_CONVERTED_ATTR = '_get_or_create_converted'
_GET_MODIFIED_FIRST_LINE_ATTR = '_get_modified_first_line'


class _AutoReplaceRule(TypedDict, total=False):
    enabled: bool
    direction: str
    host_filter: str
    path_filter: str
    type: str
    match: str
    replacement: str


class _RequestLogEntry(TypedDict):
    id: int
    time: float
    host: str
    port: int
    method: str
    path: str
    intercepted: bool
    status: int | None
    size: int
    ms: int | None
    request_raw: bytes | bytearray | None
    response_raw: bytes | bytearray | None
    pending_stage: str | None
    was_intercepted: bool
    dropped_request: NotRequired[bool]
    dropped_response: NotRequired[bool]


class _ModuleInterceptor(Protocol):
    def request(self, flow: ProxyFlow) -> None: ...

    def response(self, flow: ProxyFlow) -> None: ...


class _TextureBatchProcessor(Protocol):
    def process_batch_request(
        self,
        body: bytes,
        req_headers: dict[bytes, bytes],
        replacements_tuple: _ReplacementMaps,
        batch_id: str = '',
    ) -> tuple[bytes, bytes]: ...

    def process_batch_response(
        self,
        req_body: bytes,
        resp_body: bytes,
        req_headers: dict[bytes, bytes],
        batch_id: str = '',
    ) -> None: ...


def _texture_detect_repl_rig(texture_stripper: TextureStripper, repl_path: str) -> str:
    detector = cast('Callable[[str], str]', getattr(texture_stripper, _DETECT_REPL_RIG_ATTR))
    return detector(repl_path)


def _texture_get_converted_curve(
    texture_stripper: TextureStripper, repl_path: str, target_rig: str
) -> str | None:
    converter = cast(
        'Callable[[str, str], str | None]',
        getattr(texture_stripper, _GET_CONVERTED_CURVE_ATTR),
    )
    return converter(repl_path, target_rig)


def _texture_get_converted(
    texture_stripper: TextureStripper, repl_path: str, target_rig: str
) -> str | None:
    converter = cast(
        'Callable[[str, str], str | None]', getattr(texture_stripper, _GET_CONVERTED_ATTR)
    )
    return converter(repl_path, target_rig)


def _flow_request_modified_first_line(request: _FlowRequest, original: bytes) -> bytes:
    getter = cast('Callable[[bytes], bytes]', getattr(request, _GET_MODIFIED_FIRST_LINE_ATTR))
    return getter(original)


@dataclass
class RawHeaders:
    first_line: bytes
    headers: dict[bytes, bytes]
    raw_header_block: bytes


@dataclass
class RawBody:
    wire: bytes
    payload: bytes
    was_chunked: bool


def _decompress_body(body: bytes, headers: dict[bytes, bytes]) -> bytes:
    """Decompress gzip or zstd body. Used only when we need to READ content."""
    ce = headers.get(b'content-encoding', b'').lower()
    if not body:
        return body
    if ce == b'gzip' or body[:2] == _GZIP_MAGIC:
        try:
            return gzip.decompress(body)
        except Exception:  # ruff: ignore[blind-except]
            return body
    if ce == b'zstd' or body[:4] == _ZSTD_MAGIC:
        try:
            import zstandard  # ruff: ignore[import-outside-top-level]

            return zstandard.ZstdDecompressor().decompress(body, max_output_size=64 * 1024 * 1024)
        except Exception:  # ruff: ignore[blind-except]
            return body
    return body


_PREVIEW_CAPTURE_CAP = 512 * 1024  # per direction, per request-log entry


def _looks_binary(text: str) -> bool:
    if '\x00' in text:
        return True
    sample = text[:2048]
    if not sample:
        return False
    printable = sum(1 for ch in sample if ch in '\t\r\n' or 32 <= ord(ch) < 127 or ord(ch) > 159)  # ruff: ignore[magic-value-comparison]
    return (printable / len(sample)) < 0.85  # ruff: ignore[magic-value-comparison]


def _pretty_body_text(body: bytes) -> str:
    if not body:
        return ''
    try:
        return json.dumps(json.loads(body), indent=2, ensure_ascii=False)
    except Exception:  # ruff: ignore[blind-except, try-except-pass]
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
    except Exception:  # ruff: ignore[blind-except]
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
        line = line.strip('\r')  # ruff: ignore[redefined-loop-name]
        if not line or ':' not in line:
            continue
        key, _, value = line.partition(':')
        if key.strip().lower() in ('content-length', 'transfer-encoding', 'content-encoding'):  # ruff: ignore[literal-membership]
            continue
        out_lines.append((key.strip() + ': ' + value.strip()).encode('utf-8', errors='replace'))
    out_lines.append(b'content-length: ' + str(len(body)).encode())
    return b'\r\n'.join(out_lines) + b'\r\n\r\n' + body


async def _reparse_request_bytes(raw: bytes) -> tuple[RawHeaders, RawBody] | None:
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
    except Exception:  # ruff: ignore[blind-except]
        body = RawBody(wire=b'', payload=b'', was_chunked=False)
    return parsed, body


class PendingIntercept:
    """A request or response held open, awaiting a forward/drop decision from the GUI."""

    __slots__ = ('action', 'data', 'entry_id', 'event', 'stage')

    def __init__(self, entry_id: int, stage: str, data: bytes) -> None:
        self.entry_id = entry_id
        self.stage = stage
        self.data = bytearray(data)
        self.event = threading.Event()
        self.action: str | None = None


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
        import zstandard  # ruff: ignore[import-outside-top-level]

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
    except Exception:  # ruff: ignore[blind-except]
        return None


def _compress_dcz(body: bytes, dictionary: bytes) -> bytes | None:
    """Encode a modified ClientSettings document using the client's ``dcz`` dictionary."""
    try:
        import zstandard  # ruff: ignore[import-outside-top-level]

        zstd_dictionary = zstandard.ZstdCompressionDict(
            dictionary,
            dict_type=zstandard.DICT_TYPE_RAWCONTENT,
        )
        return zstandard.ZstdCompressor(dict_data=zstd_dictionary).compress(body)
    except Exception:  # ruff: ignore[blind-except]
        return None


def _build_modified_response(
    status_line: bytes,
    headers: dict[bytes, bytes],
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


def _build_modified_request(req_line: bytes, headers: dict[bytes, bytes], body: bytes) -> bytes:
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


def _auto_replace_rule_applies(
    rule: _AutoReplaceRule, direction: str, host: str, path: str
) -> bool:
    if not rule.get('enabled', True):
        return False
    rule_direction = rule.get('direction') or 'both'
    if rule_direction != 'both' and rule_direction != direction:  # ruff: ignore[repeated-equality-comparison]
        return False
    if not _auto_replace_filter_matches(host, rule.get('host_filter', '')):
        return False
    if not _auto_replace_filter_matches(path, rule.get('path_filter', '')):
        return False
    return bool(rule.get('match'))


def _resolve_json_path(data: _JsonValue, path_expr: str) -> _JsonPathTarget | None:  # ruff: ignore[too-many-return-statements]
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


def _coerce_replacement_value(replacement: str) -> _JsonValue:
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
    if stripped.lower() in ('true', 'false'):  # ruff: ignore[literal-membership]
        return stripped.lower() == 'true'
    if stripped.lower() == 'null':
        return None
    return replacement


def apply_auto_replace_rules(  # ruff: ignore[complex-structure]
    rules: Iterable[_AutoReplaceRule], direction: str, host: str, path: str, body: bytes
) -> tuple[bytes, bool]:
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
    from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

    changed = False
    result = body
    for rule in rules:
        rule_type = rule.get('type') or 'plain'
        if rule_type not in ('plain', 'regex', 'json_path'):  # ruff: ignore[literal-membership]
            continue
        if not _auto_replace_rule_applies(rule, direction, host, path):
            continue
        match = rule.get('match') or ''
        replacement = rule.get('replacement') or ''
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            if rule_type == 'regex':
                new_result = re.sub(
                    match, replacement, result.decode('utf-8', errors='replace')
                ).encode('utf-8')
            elif rule_type == 'json_path':
                data = cast('_JsonValue', json.loads(result))
                resolved = _resolve_json_path(data, match)
                if resolved is None:
                    continue
                container, key = resolved
                if isinstance(container, list):
                    container[cast('int', key)] = _coerce_replacement_value(replacement)
                else:
                    container[cast('str', key)] = _coerce_replacement_value(replacement)
                new_result = json.dumps(data).encode('utf-8')
            else:
                new_result = result.replace(
                    match.encode('utf-8', errors='replace'),
                    replacement.encode('utf-8', errors='replace'),
                )
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('AutoReplace', f'Rule {match!r} -> {replacement!r} failed: {exc}')
            continue
        if new_result != result:
            changed = True
            result = new_result
    return result, changed


def apply_auto_replace_header_rules(
    rules: Iterable[_AutoReplaceRule],
    direction: str,
    host: str,
    path: str,
    headers: dict[bytes, bytes],
) -> tuple[dict[bytes, bytes], bool]:
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
    rules: Iterable[_AutoReplaceRule], host: str, path: str
) -> tuple[str, bool]:
    """Run 'Query param' type Auto Replace rules: sets a query string
    parameter's value (matched by name) in a request's path - adds it
    (appended) if it wasn't already there. Only meaningful for requests -
    there's no equivalent concept on a response.
    """
    if not rules:
        return path, False
    from urllib.parse import (  # ruff: ignore[import-outside-top-level]
        parse_qsl,
        urlencode,
        urlsplit,
        urlunsplit,
    )

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
        new_pairs: list[tuple[str, str]] = []
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


@dataclass
class _ExplicitTunnelConnectResult:
    reader: asyncio.StreamReader | None
    writer: asyncio.StreamWriter | None
    endpoint: str | None = None
    error: str | None = None


_EXPLICIT_TUNNEL_CONNECT_TIMEOUT = 10.0
_EXPLICIT_TUNNEL_MAX_CANDIDATES = 3


async def _open_explicit_proxy_tunnel(  # ruff: ignore[complex-structure, too-many-locals]
    host: str,
    port: int,
    *,
    timeout: float = _EXPLICIT_TUNNEL_CONNECT_TIMEOUT,  # ruff: ignore[async-function-with-timeout]
) -> _ExplicitTunnelConnectResult:
    """Open a plain-TCP upstream for a CONNECT tunnel.

    The client, not Fleasion, performs the TLS handshake after it receives the
    CONNECT 200 response. Resolve here so transient/broken IPv6 routes do not
    consume the entire connection budget before a usable IPv4 candidate is
    tried.
    """
    started = time.monotonic()
    loop = asyncio.get_running_loop()
    try:
        addr_info = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=timeout
        )
    except Exception as exc:  # ruff: ignore[blind-except]
        elapsed_ms = round((time.monotonic() - started) * 1000)
        return _ExplicitTunnelConnectResult(
            reader=None,
            writer=None,
            error=(
                f'phase=dns host={host} port={port} elapsed_ms={elapsed_ms} '
                f'exception={_format_exc(exc)} repr={exc!r}'
            ),
        )

    candidates: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for family, socktype, _protocol, _canonname, sockaddr in addr_info:
        if family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM:  # ruff: ignore[literal-membership]
            continue
        address = cast('str', sockaddr[0])
        key = (family, address)
        if key not in seen:
            seen.add(key)
            candidates.append(key)
    candidates.sort(key=lambda candidate: candidate[0] != socket.AF_INET)

    if not candidates:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        return _ExplicitTunnelConnectResult(
            reader=None,
            writer=None,
            error=f'phase=dns host={host} port={port} elapsed_ms={elapsed_ms} no_stream_candidates',
        )

    attempt_candidates = candidates[:_EXPLICIT_TUNNEL_MAX_CANDIDATES]
    if attempt_candidates and not any(
        family == socket.AF_INET6 for family, _address in attempt_candidates
    ):
        ipv6_candidate = next(
            (candidate for candidate in candidates if candidate[0] == socket.AF_INET6),
            None,
        )
        if ipv6_candidate is not None:
            # Preserve the IPv4 preference without allowing a large A-record
            # set to consume every bounded attempt before IPv6 is considered.
            attempt_candidates[-1] = ipv6_candidate

    failures: list[str] = []
    for candidate_index, (family, address) in enumerate(attempt_candidates):
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            failures.append('phase=connect budget_exhausted')
            break
        attempt_started = time.monotonic()
        # Share the remaining total budget between the candidates still to be
        # tried. Most Roblox names expose only one usable address; the old
        # fixed three-second cap discarded seven seconds of the ten-second
        # budget and produced avoidable Error 279/asset failures on lossy Wi-Fi.
        candidates_left = len(attempt_candidates) - candidate_index
        attempt_timeout = remaining / candidates_left
        family_name = 'IPv4' if family == socket.AF_INET else 'IPv6'
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(address, port, family=family), timeout=attempt_timeout
            )
            return _ExplicitTunnelConnectResult(
                reader=reader,
                writer=writer,
                endpoint=f'{family_name} {address}',
            )
        except Exception as exc:  # ruff: ignore[blind-except]
            elapsed_ms = round((time.monotonic() - attempt_started) * 1000)
            failures.append(
                f'phase=connect family={family_name} ip={address} elapsed_ms={elapsed_ms} '
                f'exception={_format_exc(exc)} repr={exc!r}'
            )

    total_elapsed_ms = round((time.monotonic() - started) * 1000)
    return _ExplicitTunnelConnectResult(
        reader=None,
        writer=None,
        error=(
            f'host={host} port={port} total_elapsed_ms={total_elapsed_ms} '
            f'candidates={len(candidates)} attempts=[{"; ".join(failures)}]'
        ),
    )


def _parse_status_code(status_line: bytes) -> int:
    try:
        return int(status_line.split(b' ', 2)[1])
    except Exception:  # ruff: ignore[blind-except]
        return 0


class _ResponseTrackingWriter:
    """Wraps a client-facing StreamWriter to tally status/size/duration onto a
    live request-log entry, without having to touch every response branch in
    ``_http_session`` (there are many, and they all end up calling ``write``).
    """

    def __init__(self, writer: asyncio.StreamWriter, proxy: FleasionProxy) -> None:
        self._writer = writer
        self._proxy = proxy
        self._entry: _RequestLogEntry | None = None
        self._start = 0.0
        self._status_captured = False
        self._hold = False
        self._held_buffer: bytearray | None = None
        self._delivery_ack: Callable[[], None] | None = None
        self._delivery_ack_expected: bytes | None = None

    def begin(self, entry: _RequestLogEntry | None, hold: bool = False) -> None:  # ruff: ignore[boolean-default-value-positional-argument, boolean-type-hint-positional-argument]
        self._entry = entry
        self._start = time.time()
        self._status_captured = False
        if entry is not None:
            entry['response_raw'] = bytearray()
        self._hold = hold
        self._held_buffer = bytearray() if hold else None
        self._delivery_ack = None
        self._delivery_ack_expected = None

    def defer_delivery_acknowledgement(self, callback: Callable[[], None]) -> bool:
        """Defer one acknowledgement until an unedited held response is drained.

        Returns False when the current response is not being held.  If the user
        drops or edits a held response, the acknowledgement is discarded so
        callers can keep any delivery-dependent retry state armed.
        """
        if not self._hold or self._held_buffer is None:
            return False
        self._delivery_ack = callback
        self._delivery_ack_expected = bytes(self._held_buffer)
        return True

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
        response_buffer = cast('bytearray', buf)
        if len(response_buffer) < _PREVIEW_CAPTURE_CAP:
            response_buffer.extend(data[: _PREVIEW_CAPTURE_CAP - len(response_buffer)])

        if self._hold:
            cast('bytearray', self._held_buffer).extend(data)
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
        delivery_ack = self._delivery_ack
        delivery_ack_expected = self._delivery_ack_expected
        self._hold = False
        self._held_buffer = None
        self._delivery_ack = None
        self._delivery_ack_expected = None

        def _ack_if_unchanged(final_bytes: bytes) -> None:
            if delivery_ack is None or final_bytes != delivery_ack_expected:
                return
            try:
                delivery_ack()
            except Exception as exc:  # ruff: ignore[blind-except]
                logger.debug('Deferred response delivery acknowledgement failed: %s', exc)

        if entry is None:
            self._writer.write(held)
            await self._writer.drain()
            _ack_if_unchanged(held)
            return
        create_pending = cast(
            'Callable[[_RequestLogEntry, str, bytes], PendingIntercept]',
            getattr(self._proxy, _CREATE_PENDING_ATTR),
        )
        resolve_pending = cast(
            'Callable[[_RequestLogEntry, str], None]',
            getattr(self._proxy, _RESOLVE_PENDING_ATTR),
        )
        pending = create_pending(entry, 'response', held)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, pending.event.wait)
        resolve_pending(entry, 'response')
        if pending.action == 'drop':
            entry['response_raw'] = bytearray(pending.data)
            entry['dropped_response'] = True
            try:  # ruff: ignore[suppressible-exception]
                self._writer.close()
            except Exception:  # ruff: ignore[blind-except, try-except-pass]
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
        _ack_if_unchanged(final_bytes)

    async def drain(self) -> None:
        await self._writer.drain()

    def close(self) -> None:
        self._writer.close()

    def is_closing(self) -> bool:
        return self._writer.is_closing()

    def __getattr__(self, name: str) -> object:
        return getattr(self._writer, name)


def _without_conditional_client_settings_headers(headers: dict[bytes, bytes]) -> dict[bytes, bytes]:
    """Make one ClientSettings request fetch a current body instead of HTTP 304."""
    conditional_headers = {b'if-none-match', b'if-modified-since'}
    return {key: value for key, value in headers.items() if key not in conditional_headers}


_BROWSER_BYPASS_CUSTOM_FFLAGS_HEADER = b'x-fleasion-bypass-custom-fflags'


def _without_internal_client_settings_headers(headers: dict[bytes, bytes]) -> dict[bytes, bytes]:
    """Remove Fleasion-only ClientSettings headers before contacting Roblox."""
    return {
        key: value for key, value in headers.items() if key != _BROWSER_BYPASS_CUSTOM_FFLAGS_HEADER
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


async def _read_headers_raw(reader: asyncio.StreamReader) -> RawHeaders | None:
    """Read one HTTP header block, preserving the exact wire header bytes."""
    raw = bytearray()

    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=15.0)
        except Exception:  # ruff: ignore[blind-except]
            return None
        if not line:
            return None

        raw += line
        if line in (b'\r\n', b'\n'):  # ruff: ignore[literal-membership]
            break
        if len(raw) > 1024 * 1024:
            msg = 'HTTP header block too large'
            raise ValueError(msg)

    lines = bytes(raw).splitlines()
    if not lines:
        return None

    first_line = lines[0].rstrip(b'\r\n')
    headers: dict[bytes, bytes] = {}
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


async def _read_headers(  # pyright: ignore[reportUnusedFunction]
    reader: asyncio.StreamReader,
) -> tuple[bytes, dict[bytes, bytes]] | None:
    """Compatibility wrapper returning (first_line, lowercase_headers)."""
    raw = await _read_headers_raw(reader)
    if raw is None:
        return None
    return raw.first_line, raw.headers


async def _read_body_wire(reader: asyncio.StreamReader, headers: dict[bytes, bytes]) -> RawBody:  # ruff: ignore[complex-structure, too-many-branches]
    """Read an HTTP body, preserving wire bytes and exposing dechunked payload."""
    te = headers.get(b'transfer-encoding', b'').lower()
    cl_raw = headers.get(b'content-length', b'')

    if b'chunked' in te:
        wire = bytearray()
        payload = bytearray()
        while True:
            try:
                size_line = await reader.readline()
            except Exception:  # ruff: ignore[blind-except]
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
                    if trailer_line in (b'\r\n', b'\n'):  # ruff: ignore[literal-membership]
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


async def _read_body_raw(  # pyright: ignore[reportUnusedFunction]
    reader: asyncio.StreamReader, headers: dict[bytes, bytes]
) -> bytes:
    """Compatibility wrapper returning the dechunked, still-compressed payload."""
    return (await _read_body_wire(reader, headers)).payload


def _reassemble_raw_response(
    status_line: bytes, headers: dict[bytes, bytes], body_raw: bytes
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


def _reassemble_raw_request(req_line: bytes, headers: dict[bytes, bytes], body_raw: bytes) -> bytes:
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


def _keep_alive(first_line: bytes, headers: dict[bytes, bytes]) -> bool:
    conn = headers.get(b'connection', b'').lower()
    if b'close' in conn:
        return False
    if b'http/1.0' in first_line.lower() and b'keep-alive' not in conn:  # ruff: ignore[needless-bool]
        return False
    return True


def _read_local_bytes(local_path: str) -> bytes:
    """Read the actual (possibly converted) bytes for caching purposes."""
    path = Path(local_path)
    if path.suffix.lower() == '.obj':
        try:
            from fleasion.cache.tools.solidmodel_converter.obj_to_mesh import (  # ruff: ignore[import-outside-top-level]
                get_or_create_mesh_from_obj,
            )

            path = get_or_create_mesh_from_obj(path)
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass
    return strip_roblox_metadata(path, path.read_bytes()) if path.exists() else b''


def _serve_local_file(local_path: str) -> bytes:
    path = Path(local_path)
    if path.suffix.lower() == '.obj':
        try:
            from fleasion.cache.tools.solidmodel_converter.obj_to_mesh import (  # ruff: ignore[import-outside-top-level]
                get_or_create_mesh_from_obj,
            )

            path = get_or_create_mesh_from_obj(path)
        except Exception as exc:  # ruff: ignore[blind-except]
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

    def __init__(self, headers: dict[bytes, bytes]) -> None:
        self._h: dict[bytes, bytes] = {k.lower(): v for k, v in headers.items()}

    def get(self, key: str, default: str = '') -> str:
        v = self._h.get(key.lower().encode('ascii', errors='replace'))
        if v is None:
            return default
        return v.decode('ascii', errors='replace')

    def __setitem__(self, key: str, value: str | bytes) -> None:
        self._h[key.lower().encode('ascii', errors='replace')] = (
            value.encode('ascii', errors='replace') if isinstance(value, str) else value
        )

    def __getitem__(self, key: str) -> str:
        v = self._h[key.lower().encode('ascii', errors='replace')]
        return v.decode('ascii', errors='replace')

    def to_bytes_dict(self) -> dict[bytes, bytes]:
        return dict(self._h)


class _FlowRequest:
    def __init__(
        self, first_line: bytes, headers: dict[bytes, bytes], body: bytes, host: str
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
        from urllib.parse import urlparse as _urlparse  # ruff: ignore[import-outside-top-level]

        self._path = _urlparse(value).path

    def _get_modified_first_line(self, original: bytes) -> bytes:
        if self._path == self._original_path:
            return original
        parts = original.split(b' ', 2)
        if len(parts) >= 3:  # ruff: ignore[magic-value-comparison]
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

    def json(self) -> _JsonValue:
        import json as _json  # ruff: ignore[import-outside-top-level]

        return cast('_JsonValue', _json.loads(self.content))


class ProxyFlow:
    """Minimal flow object passed to module interceptors (request + response hooks)."""

    def __init__(
        self, req_first: bytes, req_headers: dict[bytes, bytes], body: bytes, host: str
    ) -> None:
        self.request: _FlowRequest = _FlowRequest(req_first, req_headers, body, host)
        self.response: _FlowResponse | None = None
        self.drop_request: bool = False
        self.drop_status_code: int = 204
        self.drop_body: bytes = b''


def _flow_response_after_callbacks(flow: ProxyFlow) -> _FlowResponse | None:
    return flow.response


class FleasionProxy:  # ruff: ignore[too-many-public-methods]
    """Direct TLS-terminating asyncio proxy for Roblox asset hosts."""

    def __init__(  # ruff: ignore[too-many-arguments, too-many-positional-arguments, too-many-statements]
        self,
        texture_stripper: TextureStripper,
        cache_scraper: CacheScraper,
        host_certs: dict[str, tuple[Path, Path]],
        upstream_endpoints: dict[str, Sequence[UpstreamEndpoint | str]] | None = None,
        default_cert: tuple[Path, Path] | None = None,
        port: int = 443,
        max_workers: int = 8,
        upstream_ips: dict[str, list[str]] | None = None,
        upstream_mode: str | UpstreamMode = UpstreamMode.AUTO,
        system_http_proxy: HttpProxyConfig | None = None,
        manual_http_proxy: HttpProxyConfig | None = None,
        manual_socks5_proxy: Socks5ProxyConfig | None = None,
        wire_preserving_passthrough: bool = False,  # ruff: ignore[boolean-default-value-positional-argument, boolean-type-hint-positional-argument]
        explicit_proxy: bool = False,  # ruff: ignore[boolean-default-value-positional-argument, boolean-type-hint-positional-argument]
        intercept_hosts: Iterable[str] | None = None,
        vpn_compat_max_assetdelivery_connections: int = 16,
        vpn_compat_max_cdn_connections: int = 32,
        custom_fflag_modifier: CustomFFlagModifier | None = None,
        on_upstream_connect_failure: Callable[[str, str], None] | None = None,
        upstream_endpoint_refresher: Callable[[str], Sequence[UpstreamEndpoint | str]]
        | None = None,
        ca_cert_path: Path | None = None,
        ca_key_path: Path | None = None,
        cert_cache_dir: Path | None = None,
        intercept_all_hosts: bool = False,  # ruff: ignore[boolean-default-value-positional-argument, boolean-type-hint-positional-argument]
        intercept_excluded_hosts: Iterable[str] | None = None,
        auto_replace_rules: Iterable[_AutoReplaceRule] | None = None,
    ) -> None:
        self.texture_stripper = texture_stripper
        self.cache_scraper = cache_scraper
        self.custom_fflag_modifier = custom_fflag_modifier
        self.port = port
        self._module_interceptors: list[_ModuleInterceptor] = []
        if upstream_endpoints is None:
            upstream_endpoints = cast(
                'dict[str, Sequence[UpstreamEndpoint | str]]', upstream_ips or {}
            )
        self._upstream_endpoints = normalize_endpoints(upstream_endpoints)
        self._server: asyncio.Server | None = None
        self._servers: list[asyncio.Server] = []
        self._listening_loopbacks: set[str] = set()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix='fleasion-cpu'
        )
        self._sni_diagnostics_seen: set[str] = set()
        self._fallback_diagnostics_seen: set[tuple[str, str]] = set()
        self._client_settings_dictionary_cache: dict[str, bytes] = {}
        self._wire_preserving_passthrough = bool(wire_preserving_passthrough)
        self._explicit_proxy = bool(explicit_proxy)
        self._intercept_hosts: frozenset[str] = (
            frozenset(intercept_hosts) if intercept_hosts is not None else INTERCEPT_HOSTS
        )
        self._intercept_all_hosts = bool(intercept_all_hosts)
        self._intercept_excluded_hosts = frozenset(
            str(host).strip().lower().rstrip('.')
            for host in (intercept_excluded_hosts or ())
            if str(host).strip()
        )
        self._auto_replace_rules: list[_AutoReplaceRule] = (
            list(auto_replace_rules) if auto_replace_rules else []
        )
        self._ca_cert_path = ca_cert_path
        self._ca_key_path = ca_key_path
        self._cert_cache_dir = cert_cache_dir
        self._request_log_lock = threading.Lock()
        self._request_log: list[_RequestLogEntry] = []
        self._request_log_max = 4000
        self._next_entry_id = 0
        self._intercept_match_text = ''
        self._pending_lock = threading.Lock()
        self._pending: dict[tuple[int, str], PendingIntercept] = {}
        self._last_gamejoin_time: float = 0.0
        self._last_asset_traffic_time: float = 0.0
        self._asset_diag_generation: int = 0
        self._on_upstream_connect_failure = on_upstream_connect_failure
        self._upstream_connect_failure_notified = False
        self._upstream_endpoint_refresher = upstream_endpoint_refresher
        self._last_upstream_endpoint_refresh: dict[str, float] = {}

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
        self._system_http_connector: BaseUpstreamConnector | None = (
            HttpConnectConnector(system_http_proxy, method='system_http_connect')
            if system_http_proxy is not None
            else None
        )
        self._manual_http_connector: BaseUpstreamConnector | None = (
            HttpConnectConnector(manual_http_proxy) if manual_http_proxy is not None else None
        )
        self._manual_socks5_connector: BaseUpstreamConnector | None = (
            Socks5Connector(manual_socks5_proxy) if manual_socks5_proxy is not None else None
        )
        self._upstream_mode = normalize_upstream_mode(upstream_mode)
        self._connector = self._build_connector()

        self._local_tls_max_version = PROXY_TLS_MAX_VERSION
        self._host_ssl_ctxs: dict[str, ssl.SSLContext] = {}
        for host, (cert_path, key_path) in host_certs.items():
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cert_path), str(key_path))
            ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.maximum_version = self._local_tls_max_version
            ctx.set_alpn_protocols(['http/1.1'])
            self._host_ssl_ctxs[host] = ctx

        # Upstream: no cert verify, force HTTP/1.1 (we don't implement h2)
        self._upstream_ssl_ctx = ssl.create_default_context()
        self._upstream_ssl_ctx.check_hostname = False
        self._upstream_ssl_ctx.verify_mode = ssl.CERT_NONE
        self._upstream_ssl_ctx.set_alpn_protocols(['http/1.1'])

        if default_cert is None:
            msg = 'default_cert is required'
            raise ValueError(msg)
        default_cert_path, default_key_path = default_cert
        self._server_ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._server_ssl_ctx.load_cert_chain(str(default_cert_path), str(default_key_path))
        self._server_ssl_ctx.verify_mode = ssl.CERT_NONE
        self._server_ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self._server_ssl_ctx.maximum_version = self._local_tls_max_version
        self._server_ssl_ctx.set_alpn_protocols(['http/1.1'])
        self._server_ssl_ctx.set_servername_callback(self._sni_callback)

    def set_local_tls_max_version(self, version: ssl.TLSVersion) -> None:
        """Update the TLS ceiling used by local client-facing contexts.

        Fleasion normally caps local interception at TLS 1.2 for Windows
        compatibility.  Startup diagnostics may relax that ceiling when a
        machine cannot complete the capped loopback handshake.
        """
        self._local_tls_max_version = version
        self._server_ssl_ctx.maximum_version = version
        for ctx in self._host_ssl_ctxs.values():
            ctx.maximum_version = version

    def _log_sni_once(self, key: str, message: str) -> None:
        if key in self._sni_diagnostics_seen:
            return
        self._sni_diagnostics_seen.add(key)
        try:
            from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

            log_buffer.log('TLS', message)
        except Exception:  # ruff: ignore[blind-except]
            logger.debug(message)

    def _notify_upstream_connect_failure_once(self, host: str, error: str) -> None:
        if self._upstream_connect_failure_notified or self._on_upstream_connect_failure is None:
            return
        self._upstream_connect_failure_notified = True
        try:
            self._on_upstream_connect_failure(host, error)
        except Exception as exc:  # ruff: ignore[blind-except]
            try:
                from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

                log_buffer.log('Proxy', f'Failed to report upstream connection failure: {exc}')
            except Exception:  # ruff: ignore[blind-except]
                logger.debug('Failed to report upstream connection failure: %s', exc)

    def _sni_callback(
        self,
        ssl_obj: ssl.SSLSocket | ssl.SSLObject,
        server_name: str | None,
        _initial_ctx: ssl.SSLSocket,
    ) -> None:
        name = (server_name or '').lower()
        if name in self._intercept_excluded_hosts:
            self._log_sni_once(
                f'excluded:{name}',
                f'SNI {name} is excluded from interception; using tunnel passthrough',
            )
            return
        if name in self._host_ssl_ctxs:
            self._log_sni_once(
                f'known:{name}',
                f'SNI matched {name}; using default multi-host certificate',
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
        return

    def _get_or_generate_host_ctx(self, host: str) -> ssl.SSLContext | None:
        """Generate (and cache) a leaf cert signed by our own CA for a host we
        weren't pre-provisioned for, so intercept-all mode can TLS-terminate
        literally anything Roblox connects to, not just the fixed feature set.
        """
        if self._ca_cert_path is None or self._ca_key_path is None or self._cert_cache_dir is None:
            return None
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            from fleasion.utils.certs import (  # ruff: ignore[import-outside-top-level]
                generate_host_cert,
            )

            cert_path, key_path = generate_host_cert(
                host, self._ca_cert_path, self._ca_key_path, self._cert_cache_dir
            )
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cert_path), str(key_path))
            ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.maximum_version = self._local_tls_max_version
            ctx.set_alpn_protocols(['http/1.1'])
        except Exception as exc:  # ruff: ignore[blind-except]
            try:
                from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

                log_buffer.log('TLS', f'Could not generate a certificate for {host}: {exc}')
            except Exception:  # ruff: ignore[blind-except]
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
                from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

                log_buffer.log(
                    'Proxy',
                    f'IPv6 loopback listener unavailable on [::1]:{self.port}: {exc}',
                )
            except Exception:  # ruff: ignore[blind-except]
                logger.debug('IPv6 loopback listener unavailable on [::1]:%d: %s', self.port, exc)

    async def serve_forever(self) -> None:
        if not self._servers:
            return
        await asyncio.gather(*(server.serve_forever() for server in self._servers))

    def set_module_interceptors(self, interceptors: Iterable[_ModuleInterceptor]) -> None:
        """Set the list of module interceptors for gamejoin traffic hooks."""
        self._module_interceptors = list(interceptors)

    def set_upstream_endpoints(
        self, endpoints: dict[str, Sequence[UpstreamEndpoint | str]]
    ) -> None:
        self._upstream_endpoints = normalize_endpoints(endpoints)

    def set_intercept_hosts(self, hosts: Iterable[str]) -> None:
        """Update the feature-gated host set used by explicit-proxy CONNECT handling."""
        self._intercept_hosts = frozenset(hosts)

    def _record_request(
        self,
        host: str,
        port: int,
        method: str,
        path: str,
        intercepted: bool,  # ruff: ignore[boolean-type-hint-positional-argument]
    ) -> _RequestLogEntry:
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
            entry: _RequestLogEntry = {
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

    def get_request_log(self) -> list[_RequestLogEntry]:
        """Return a snapshot of every request/tunnel the explicit proxy has logged."""
        with self._request_log_lock:
            return [entry.copy() for entry in self._request_log]

    def clear_request_log(self) -> None:
        with self._request_log_lock:
            self._request_log.clear()

    def format_request_preview(self, entry: _RequestLogEntry) -> str:  # ruff: ignore[no-self-use]
        """Human-readable request text for a request-log entry, for the Proxy tab."""
        raw = entry.get('request_raw')
        if not raw:
            return ''
        return asyncio.run(_format_raw_http_message(bytes(raw)))

    def format_response_preview(self, entry: _RequestLogEntry) -> str:  # ruff: ignore[no-self-use]
        """Human-readable response text for a request-log entry, for the Proxy tab."""
        raw = entry.get('response_raw')
        if not raw:
            return ''
        return asyncio.run(_format_raw_http_message(bytes(raw)))

    async def replay_request(
        self, entry_id: int, raw_request: bytes, host: str
    ) -> _RequestLogEntry | None:
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
        entry['path'] = (
            parts[1].decode('ascii', errors='replace') if len(parts) > 1 else entry['path']
        )
        entry['request_raw'] = raw_request[:_PREVIEW_CAPTURE_CAP]
        entry['status'] = None
        entry['size'] = 0
        entry['ms'] = None

        start = time.time()
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            connect_result = await self._connect_upstream(host)
            if connect_result.writer is None:
                entry['response_raw'] = (
                    f'<replay failed: {connect_result.error or "no upstream reachable"}>'.encode()
                )
                return entry
            up_reader = cast('asyncio.StreamReader', connect_result.reader)
            up_writer = connect_result.writer
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
                try:  # ruff: ignore[suppressible-exception]
                    up_writer.close()
                except Exception:  # ruff: ignore[blind-except, try-except-pass]
                    pass
        except Exception as exc:  # ruff: ignore[blind-except]
            entry['response_raw'] = f'<replay error: {exc}>'.encode()
        return entry

    def set_intercept_match(self, text: str) -> None:
        """Set the host/path substring that pauses matching traffic for edit/forward/drop.

        Interception is armed purely by this being non-empty - nothing else.
        """
        self._intercept_match_text = (text or '').strip().lower()

    def set_intercept_all_hosts(self, enabled: bool) -> None:  # ruff: ignore[boolean-type-hint-positional-argument]
        """Toggle whether traffic to hosts OUTSIDE Fleasion's own feature set
        (texture stripper/custom FastFlags/username spoofer/etc) also gets
        decrypted and logged. Those feature hosts always work either way -
        this only widens or narrows what ELSE is visible/interceptable.
        """
        self._intercept_all_hosts = bool(enabled)

    def set_intercept_excluded_hosts(self, hosts: Iterable[str]) -> None:
        """Update hosts that must remain CONNECT tunnels in explicit-proxy mode."""
        self._intercept_excluded_hosts = frozenset(
            str(host).strip().lower().rstrip('.') for host in hosts if str(host).strip()
        )

    def _should_intercept_explicit_host(self, host: str, port: int) -> bool:
        """Return whether an explicit-proxy CONNECT should be TLS-terminated."""
        normalized_host = (host or '').strip().lower().rstrip('.')
        return (
            port == 443  # ruff: ignore[magic-value-comparison]
            and normalized_host not in self._intercept_excluded_hosts
            and (self._intercept_all_hosts or normalized_host in self._intercept_hosts)
        )

    def set_auto_replace_rules(self, rules: Iterable[_AutoReplaceRule]) -> None:
        """Replace the live set of Auto Replace rules (see apply_auto_replace_rules)."""
        self._auto_replace_rules = list(rules) if rules else []

    def _intercept_matches(self, host: str, path: str) -> bool:
        if not self._intercept_match_text:
            return False
        text = self._intercept_match_text
        return text in host.lower() or text in path.lower()

    def _create_pending(self, entry: _RequestLogEntry, stage: str, data: bytes) -> PendingIntercept:
        pending = PendingIntercept(entry['id'], stage, data)
        with self._pending_lock:
            self._pending[entry['id'], stage] = pending
        entry['pending_stage'] = stage
        entry['was_intercepted'] = True
        return pending

    def _resolve_pending(self, entry: _RequestLogEntry, stage: str) -> None:
        with self._pending_lock:
            self._pending.pop((entry['id'], stage), None)
        if entry.get('pending_stage') == stage:
            entry['pending_stage'] = None

    def get_pending_data(self, entry_id: int, stage: str) -> bytes | None:
        """Current (possibly not-yet-submitted) editable bytes for a held request/response."""
        with self._pending_lock:
            pending = self._pending.get((entry_id, stage))
        return bytes(pending.data) if pending is not None else None

    def get_pending_intercepts(self) -> list[tuple[int, str]]:
        """(entry_id, stage) pairs currently held open awaiting a forward/drop decision."""
        with self._pending_lock:
            return list(self._pending.keys())

    def submit_pending(
        self, entry_id: int, stage: str, action: str, edited_text: str | None = None
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
        self,
        hosts: Sequence[str],
    ) -> dict[str, list[UpstreamEndpoint]]:
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
            try:  # ruff: ignore[suppressible-exception]
                await asyncio.wait_for(server.wait_closed(), timeout=3.0)
            except Exception:  # ruff: ignore[blind-except, try-except-pass]
                pass
        self._servers = []
        self._server = None
        self._listening_loopbacks = set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def loopback_ips_for_hosts(self) -> tuple[str, ...]:
        ordered: list[str] = []
        for ip in ('127.0.0.1', '::1'):
            if ip in self._listening_loopbacks:
                ordered.append(ip)  # ruff: ignore[manual-list-comprehension]
        return tuple(ordered) or ('127.0.0.1',)

    def _note_asset_traffic(self) -> None:
        self._last_asset_traffic_time = time.monotonic()

    def _note_gamejoin_traffic(self) -> None:
        self._last_gamejoin_time = time.monotonic()
        self._asset_diag_generation += 1
        generation = self._asset_diag_generation
        asyncio.create_task(  # ruff: ignore[asyncio-dangling-task]
            self._warn_if_asset_traffic_missing(generation, self._last_gamejoin_time)
        )

    async def _warn_if_asset_traffic_missing(self, generation: int, gamejoin_time: float) -> None:
        await asyncio.sleep(ASSET_TRAFFIC_MISSING_DIAGNOSTIC_SECONDS)
        if generation != self._asset_diag_generation:
            return
        if self._last_asset_traffic_time >= gamejoin_time:
            return
        try:
            from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

            log_buffer.log(
                'ProxyDiag',
                'Game join traffic was intercepted, but no assetdelivery/CDN requests reached Fleasion '  # ruff: ignore[line-too-long]
                f'within {ASSET_TRAFFIC_MISSING_DIAGNOSTIC_SECONDS:.0f}s. '
                'Possible asset traffic bypass: IPv6 loopback, stale DNS cache, hosts-file protection, '  # ruff: ignore[line-too-long]
                'or security/VPN filtering.',
            )
        except Exception:  # ruff: ignore[blind-except]
            logger.debug('No assetdelivery/CDN traffic observed after gamejoin')

    def _endpoints_for_host(
        self,
        host: str,
        max_targets: int | None = None,
    ) -> list[UpstreamEndpoint]:
        endpoints = self._upstream_endpoints.get(host, []) or [UpstreamEndpoint(host=host)]
        if max_targets is not None:
            endpoints = endpoints[:max_targets]
        return endpoints

    def _uses_direct_path_without_fallback(self) -> bool:
        """Whether a failed request has no transport other than direct IP."""
        if self._upstream_mode == UpstreamMode.DIRECT_IP:
            return True
        return (
            self._upstream_mode == UpstreamMode.AUTO
            and self._system_http_connector is None
            and self._manual_http_connector is None
            and self._manual_socks5_connector is None
        )

    async def _refresh_upstream_endpoints_after_failure(self, host: str) -> list[UpstreamEndpoint]:
        """Ask the lifecycle layer for fresh DNS-bypass endpoints, at most once per host.

        Intercepted hosts resolve to loopback while Fleasion is active, so the
        server cannot safely use the normal resolver itself.  The master owns
        a resolver that bypasses that redirect.  A short refresh cooldown
        prevents a burst of client retries from repeatedly querying DNS.
        """
        refresher = self._upstream_endpoint_refresher
        if refresher is None:
            return []

        now = time.monotonic()
        last_attempt = self._last_upstream_endpoint_refresh.get(host, 0.0)
        if now - last_attempt < UPSTREAM_ENDPOINT_REFRESH_COOLDOWN_SECONDS:
            return []
        self._last_upstream_endpoint_refresh[host] = now

        try:
            refreshed_raw = await asyncio.to_thread(refresher, host)
        except Exception as exc:  # ruff: ignore[blind-except]
            from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

            log_buffer.log('Proxy', f'Runtime endpoint refresh failed for {host}: {exc}')
            return []

        refreshed = normalize_endpoints({host: refreshed_raw}).get(host, [])
        if not refreshed:
            return []

        previous = self._upstream_endpoints.get(host, [])
        self._upstream_endpoints[host] = refreshed

        candidate_ips = [endpoint.ip for endpoint in refreshed if endpoint.ip]
        update_ips = getattr(self.cache_scraper, 'update_real_ips', None)
        if callable(update_ips) and candidate_ips:
            try:
                update_ips({host: candidate_ips})
            except Exception as exc:  # ruff: ignore[blind-except]
                from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

                log_buffer.log('Cache', f'Could not refresh API bypass endpoints for {host}: {exc}')

        old_ips = ', '.join(endpoint.ip or endpoint.host for endpoint in previous)
        new_ips = ', '.join(endpoint.ip or endpoint.host for endpoint in refreshed)
        from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

        log_buffer.log(
            'Proxy',
            f'Refreshed upstream endpoints for {host}: {old_ips or "none"} -> {new_ips}',
        )
        return refreshed

    async def _connect_upstream(
        self,
        host: str,
        *,
        timeout: float = 10.0,  # ruff: ignore[async-function-with-timeout]
        max_targets: int | None = None,
    ) -> UpstreamConnectResult:
        endpoints = self._endpoints_for_host(host, max_targets=max_targets)
        sem = self._upstream_host_limits.get(host)
        if sem is None:
            return await self._connect_upstream_with_recovery(host, endpoints, timeout)
        async with sem:
            return await self._connect_upstream_with_recovery(host, endpoints, timeout)

    async def _connect_upstream_with_recovery(
        self,
        host: str,
        endpoints: Sequence[UpstreamEndpoint],
        timeout: float,  # ruff: ignore[async-function-with-timeout]
    ) -> UpstreamConnectResult:
        result = await self._connector.connect(host, endpoints, self._upstream_ssl_ctx, timeout)

        if result.writer is not None or not self._uses_direct_path_without_fallback():
            return result

        refreshed = await self._refresh_upstream_endpoints_after_failure(host)
        if not refreshed:
            return result

        retry = await self._direct_connector.connect(
            host,
            refreshed,
            self._upstream_ssl_ctx,
            timeout=min(timeout, UPSTREAM_ENDPOINT_REFRESH_RETRY_TIMEOUT),
        )
        if retry.writer is None:
            if retry.error:
                result.error = (
                    f'{result.error or "direct path failed"} | refreshed direct_ip: {retry.error}'
                )
            return result

        if isinstance(self._connector, AutoConnector):
            self._connector.note_direct_success(host)
        from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

        log_buffer.log(
            'Proxy',
            f'Upstream recovered for {host} after endpoint refresh via {retry.endpoint}',
        )
        return retry

    async def _open_upstream(
        self,
        host: str,
        *,
        timeout: float = 10.0,  # ruff: ignore[async-function-with-timeout]
        max_targets: int | None = None,
    ) -> tuple[
        asyncio.StreamReader | None,
        asyncio.StreamWriter | None,
        str | None,
        list[str],
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

    async def log_upstream_self_test(self, hosts: set[str] | None = None) -> None:  # ruff: ignore[complex-structure]
        from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

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
            first_ok_method: str | None = None
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
                        wait_closed = getattr(result.writer, 'wait_closed', None)
                        if callable(wait_closed):
                            await cast('Callable[[], Awaitable[None]]', wait_closed)()
                    except Exception:  # ruff: ignore[blind-except, try-except-pass]
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

        # Several Roblox hostnames commonly resolve to the same edge IP. Probe
        # them serially so startup does not look like a connection burst to
        # fragile home-router flood protection, and fully close each probe
        # before opening the next one.
        for host in hosts_to_test:
            await probe(host)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._explicit_proxy:
            await self._handle_explicit_proxy_client(reader, writer)
            return

        from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

        try:
            result = await asyncio.wait_for(_read_headers_raw(reader), timeout=15.0)
        except asyncio.TimeoutError:  # ruff: ignore[timeout-error-alias]
            writer.close()
            return
        if result is None:
            writer.close()
            return
        req_headers = result.headers

        host_hdr = req_headers.get(b'host', b'').decode('ascii', errors='replace').lower()
        host = host_hdr.split(':')[0].strip()

        if host not in INTERCEPT_HOSTS:
            writer.close()
            return

        try:
            await self._http_session(result, reader, writer, host)
        except ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError:
            pass
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('Proxy', f'Session error for {host}: {exc}')
        finally:
            try:  # ruff: ignore[suppressible-exception]
                writer.close()
            except Exception:  # ruff: ignore[blind-except, try-except-pass]
                pass

    async def _handle_explicit_proxy_client(  # ruff: ignore[complex-structure, too-many-branches, too-many-return-statements, too-many-statements]
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

        try:
            connect_headers = await asyncio.wait_for(_read_headers_raw(reader), timeout=15.0)
        except asyncio.TimeoutError:  # ruff: ignore[timeout-error-alias]
            writer.close()
            return
        if connect_headers is None:
            writer.close()
            return

        parts = connect_headers.first_line.split()
        method = parts[0].upper() if parts else b''
        target = parts[1].decode('ascii', errors='replace') if len(parts) >= 2 else ''  # ruff: ignore[magic-value-comparison]
        host, _sep, port_text = target.rpartition(':')
        host = host.strip('[]').lower()
        try:
            port = int(port_text)
        except ValueError:
            port = 0

        if method != b'CONNECT' or not host or not 0 < port <= 65535:  # ruff: ignore[magic-value-comparison]
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
            await self._tunnel_explicit_proxy_connection(
                reader, writer, host, port, log_entry=entry
            )
            return

        writer.write(b'HTTP/1.1 200 Connection Established\r\nProxy-Agent: Fleasion\r\n\r\n')
        await writer.drain()

        try:
            await writer.start_tls(
                self._server_ssl_ctx,
                ssl_handshake_timeout=15.0,
            )
        except (ConnectionResetError, BrokenPipeError) as exc:
            log_buffer.log('TLS', f'Explicit proxy TLS upgrade connection closed for {host}: {exc}')
            return
        except OSError as exc:
            log_buffer.log('TLS', f'Explicit proxy TLS upgrade socket error for {host}: {exc!r}')
            return
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('Proxy', f'Explicit proxy TLS upgrade failed for {host}: {exc}')
            writer.close()
            return

        try:
            first_tls_request = await asyncio.wait_for(_read_headers_raw(reader), timeout=15.0)
        except asyncio.TimeoutError:  # ruff: ignore[timeout-error-alias]
            writer.close()
            return
        if first_tls_request is None:
            writer.close()
            return

        try:
            await self._http_session(first_tls_request, reader, writer, host)
        except ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError:
            pass
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('Proxy', f'Explicit proxy session error for {host}: {exc}')
        finally:
            try:  # ruff: ignore[suppressible-exception]
                writer.close()
            except Exception:  # ruff: ignore[blind-except, try-except-pass]
                pass

    async def _tunnel_explicit_proxy_connection(  # ruff: ignore[complex-structure, no-self-use, too-many-statements]
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        host: str,
        port: int,
        log_entry: _RequestLogEntry | None = None,
    ) -> None:
        from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

        start = time.time()

        result = await _open_explicit_proxy_tunnel(host, port)
        if result.writer is None or result.reader is None:
            log_buffer.log(
                'Proxy',
                f'Explicit proxy tunnel failed for {host}:{port}: {result.error or "unknown error"}',  # ruff: ignore[line-too-long]
            )
            client_writer.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n')
            if log_entry is not None:
                log_entry['status'] = 502
                log_entry['ms'] = round((time.time() - start) * 1000)
            try:
                await client_writer.drain()
            except ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError, OSError:
                pass
            finally:
                client_writer.close()
            return
        upstream_reader = result.reader
        upstream_writer = result.writer

        client_writer.write(b'HTTP/1.1 200 Connection Established\r\nProxy-Agent: Fleasion\r\n\r\n')
        try:
            await client_writer.drain()
        except ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError, OSError:
            # Roblox can close a CONNECT socket while the response is in flight
            # during a failed launch. This is a client disconnect, not a proxy
            # crash, so do not emit an unhandled callback traceback.
            upstream_writer.close()
            client_writer.close()
            return

        async def _pipe(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            track: bool = False,  # ruff: ignore[boolean-default-value-positional-argument, boolean-type-hint-positional-argument]
        ) -> None:
            try:  # ruff: ignore[too-many-statements-in-try-clause]
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
                try:  # ruff: ignore[suppressible-exception]
                    writer.close()
                except Exception:  # ruff: ignore[blind-except, try-except-pass]
                    pass

        try:
            await asyncio.gather(
                _pipe(client_reader, upstream_writer),
                _pipe(upstream_reader, client_writer, track=True),
            )
        finally:
            for tunnel_writer in (upstream_writer, client_writer):
                try:  # ruff: ignore[suppressible-exception]
                    tunnel_writer.close()
                except Exception:  # ruff: ignore[blind-except, try-except-pass]
                    pass

    async def _http_session(  # ruff: ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
        self,
        first_req: RawHeaders,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host: str,
    ) -> None:
        from fleasion.utils import log_buffer  # ruff: ignore[import-outside-top-level]

        response_writer = _ResponseTrackingWriter(writer, self)
        custom_fflag_modifier_present = self.custom_fflag_modifier is not None
        custom_fflag_modifier = cast('CustomFFlagModifier', self.custom_fflag_modifier)
        texture_batch_processor = cast('_TextureBatchProcessor', self.texture_stripper)

        replacements_tuple = self.texture_stripper.config_manager.get_all_replacements()
        pending_req: RawHeaders | None = first_req
        up_reader: asyncio.StreamReader | None = None
        up_writer: asyncio.StreamWriter | None = None
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
                    'Hosts/TLS interception may be working locally, but firewall, AV, VPN, or WFP filtering '  # ruff: ignore[line-too-long]
                    'may be blocking Fleasion.exe/Python outbound traffic.',
                )
                self._notify_upstream_connect_failure_once(host, failure_text)

            response_writer.write(
                _make_proxy_error_response(
                    502,
                    f'Fleasion could not connect upstream to {host}. See Fleasion logs for details.',  # ruff: ignore[line-too-long]
                )
            )
            try:  # ruff: ignore[suppressible-exception]
                await response_writer.drain()
            except Exception:  # ruff: ignore[blind-except, try-except-pass]
                pass
            return False

        async def fetch_client_settings_dictionary(dictionary_sha256: str) -> bytes | None:  # ruff: ignore[too-many-return-statements]
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

            try:  # ruff: ignore[too-many-statements-in-try-clause]
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
                    log_buffer.log(
                        'CustomFFlags', 'Roblox compression dictionary returned no response'
                    )
                    return None
                status_code = _parse_status_code(response.first_line)
                if not 200 <= status_code < 300:  # ruff: ignore[magic-value-comparison]
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
                        'Roblox compression dictionary integrity check failed; preserving original response',  # ruff: ignore[line-too-long]
                    )
                    return None
                self._client_settings_dictionary_cache[dictionary_sha256] = dictionary
                return dictionary  # ruff: ignore[try-consider-else]
            except (
                ConnectionResetError,
                BrokenPipeError,
                asyncio.IncompleteReadError,
                OSError,
            ) as exc:
                log_buffer.log('CustomFFlags', f'Roblox compression dictionary fetch failed: {exc}')
                return None
            finally:
                try:
                    dictionary_writer.close()
                    await dictionary_writer.wait_closed()
                except Exception:  # ruff: ignore[blind-except, try-except-pass]
                    pass

        try:  # ruff: ignore[too-many-nested-blocks]
            while True:
                # Release the previous iteration's held response (if any) before
                # moving on - this is the one choke point every response branch
                # below eventually passes through, on its way back here.
                await response_writer.flush_pending_response()

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
                log_entry = (
                    self._record_request(host, 443, method, path, intercepted=True)
                    if self._intercept_all_hosts
                    else None
                )
                if log_entry is not None:
                    log_entry['request_raw'] = (req_raw.raw_header_block + req_body_raw)[
                        :_PREVIEW_CAPTURE_CAP
                    ]

                    if self._intercept_matches(host, path):
                        req_pending = self._create_pending(
                            log_entry, 'request', bytes(log_entry['request_raw'])
                        )
                        await asyncio.get_event_loop().run_in_executor(None, req_pending.event.wait)
                        self._resolve_pending(log_entry, 'request')
                        edited_request = bytes(req_pending.data)
                        log_entry['request_raw'] = edited_request
                        if req_pending.action == 'drop':
                            log_entry['dropped_request'] = True
                            log_entry['status'] = None
                            try:  # ruff: ignore[suppressible-exception]
                                response_writer.close()
                            except Exception:  # ruff: ignore[blind-except, try-except-pass]
                                pass
                            break
                        reparsed = await _reparse_request_bytes(edited_request)
                        if reparsed is not None:
                            req_raw, req_body = reparsed
                            req_first, req_headers = req_raw.first_line, req_raw.headers
                            req_body_raw = req_body.payload
                            rparts = req_first.split(b' ', 2)
                            method = (
                                rparts[0].decode('ascii', errors='replace') if rparts else method
                            )
                            path = (
                                rparts[1].decode('ascii', errors='replace')
                                if len(rparts) > 1
                                else path
                            )
                            log_entry['method'] = method
                            log_entry['path'] = path

                # Auto Replace runs independently of the Proxy tab's own
                # logging/intercept toggles - it's a separate, always-on
                # feature. Query-param/header/body rules are collected
                # first, then rebuilt through _reparse_request_bytes ONCE
                # (the same path a manual pending-edit takes) if any of them
                # actually changed something - that keeps every downstream
                # branch, including wire-preserving passthrough (which reads
                # req_raw/req_body directly), in sync with the replacement.
                if self._auto_replace_rules:
                    ar_path, query_changed = apply_auto_replace_query_rules(
                        self._auto_replace_rules, host, path
                    )
                    ar_headers, header_changed = apply_auto_replace_header_rules(
                        self._auto_replace_rules, 'request', host, ar_path, req_headers
                    )
                    req_plain = _decompress_body(req_body_raw, ar_headers)
                    req_replaced, body_changed = apply_auto_replace_rules(
                        self._auto_replace_rules, 'request', host, ar_path, req_plain
                    )
                    req_changed = query_changed or header_changed or body_changed
                    if req_changed:
                        ar_req_line = req_first
                        if query_changed:
                            line_parts = req_first.split(b' ', 2)
                            line_parts[1] = ar_path.encode('ascii', errors='replace')
                            ar_req_line = b' '.join(line_parts)
                        rebuilt_request = _build_modified_request(
                            ar_req_line, ar_headers, req_replaced
                        )
                        reparsed_ar = await _reparse_request_bytes(rebuilt_request)
                        if reparsed_ar is not None:
                            req_raw, req_body = reparsed_ar
                            req_first, req_headers = req_raw.first_line, req_raw.headers
                            req_body_raw = req_body.payload
                            ar_parts = req_first.split(b' ', 2)
                            method = (
                                ar_parts[0].decode('ascii', errors='replace')
                                if ar_parts
                                else method
                            )
                            path = (
                                ar_parts[1].decode('ascii', errors='replace')
                                if len(ar_parts) > 1
                                else path
                            )
                            if log_entry is not None:
                                log_entry['request_raw'] = (
                                    req_raw.raw_header_block + req_body_raw
                                )[:_PREVIEW_CAPTURE_CAP]
                                log_entry['method'] = method
                                log_entry['path'] = path

                response_writer.begin(
                    log_entry,
                    hold=self._intercept_all_hosts and self._intercept_matches(host, path),
                )
                is_batch = host == ASSET_DELIVERY_HOST and b'/v1/assets/batch' in req_first
                batch_id = ''
                req_body_modified = req_body_raw
                scraper_body = req_body_raw
                bypass_custom_fflags = (
                    req_headers.get(_BROWSER_BYPASS_CUSTOM_FFLAGS_HEADER, b'').strip() == b'1'
                )
                gamejoin_flow: ProxyFlow | None = None
                profile_flow: ProxyFlow | None = None
                upstream_req_first = req_first
                upstream_req_headers = (
                    _without_internal_client_settings_headers(req_headers)
                    if bypass_custom_fflags
                    else req_headers
                )

                custom_fflag_request = (
                    host in CLIENT_SETTINGS_HOSTS
                    and custom_fflag_modifier_present
                    and custom_fflag_modifier.handles_path(path)
                    and not bypass_custom_fflags
                )
                custom_fflag_request_generation = (
                    custom_fflag_modifier.delivery_generation() if custom_fflag_request else None
                )
                custom_fflag_request_enabled = (
                    custom_fflag_request and custom_fflag_modifier.is_enabled()
                )
                if custom_fflag_request_enabled and custom_fflag_modifier.requires_fresh_response():
                    upstream_req_headers = _without_conditional_client_settings_headers(req_headers)

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
                            local_value = cast('str', value)
                            serve_path = Path(local_value)
                            serve_exists = serve_path.exists()  # ruff: ignore[blocking-path-method-in-async-function]
                            serve_size = serve_path.stat().st_size if serve_exists else 0  # ruff: ignore[blocking-path-method-in-async-function]
                            serve_category = (
                                'TexPackTrace'
                                if serve_path.suffix.lower() in ('.ktx', '.ktx2')  # ruff: ignore[literal-membership]
                                else 'Local'
                            )
                            log_buffer.log(
                                serve_category,
                                f'CDN local serve start: host={host} path={path[:160]} '
                                f'file={serve_path.name} exists={serve_exists} bytes={serve_size}',
                            )
                            response = await asyncio.get_event_loop().run_in_executor(
                                self._executor, _serve_local_file, local_value
                            )
                            status_line = (
                                response.split(b'\r\n', 1)[0].decode('ascii', errors='replace')
                                if response
                                else 'empty'
                            )
                            log_buffer.log(
                                serve_category,
                                f'CDN local serve complete: host={host} path={path[:160]} '
                                f'file={serve_path.name} status={status_line} response_bytes={len(response)}',  # ruff: ignore[line-too-long]
                            )
                            response_writer.write(response)
                            await response_writer.drain()
                            # Cache our own served file so it appears in the scraper viewer
                            if self.cache_scraper.enabled:
                                try:
                                    file_bytes = await asyncio.get_event_loop().run_in_executor(
                                        self._executor, _read_local_bytes, local_value
                                    )
                                    if file_bytes:
                                        full_url = f'https://{host}{path}'
                                        _cache_hash = path.rsplit('/', 1)[-1].split('?')[0]
                                        self.cache_scraper.process_cdn_response(
                                            full_url,
                                            path,
                                            file_bytes,
                                            'application/octet-stream',
                                        )
                                except Exception:  # ruff: ignore[blind-except, try-except-pass]
                                    pass
                            if not _keep_alive(req_first, req_headers):
                                break
                            continue
                        if action == 'cdn':
                            response_writer.write(_make_redirect(cast('str', value)))
                            await response_writer.drain()
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
                    import uuid as _uuid  # ruff: ignore[import-outside-top-level]

                    batch_id = _uuid.uuid4().hex
                    # Run synchronously — process_batch_request is pure Python (JSON parse +
                    # dict ops), not I/O bound. Using run_in_executor here introduced a gap:
                    # the await released the event loop, the CDN coroutine ran, saw empty
                    # _pending, skipped the wait, and forwarded unreplaced assets. Running
                    # synchronously ensures _pending is populated before any CDN coroutine
                    # can check has_pending().
                    req_body_modified, scraper_body = texture_batch_processor.process_batch_request(
                        req_body_plain,
                        req_headers,
                        replacements_tuple,
                        batch_id,
                    )
                    if _is_empty_json_array(req_body_modified) and not _is_empty_json_array(
                        req_body_plain
                    ):
                        response_writer.write(_make_local_response(200, b'[]'))
                        await response_writer.drain()
                        if not _keep_alive(req_first, req_headers):
                            break
                        continue
                    if not await ensure_upstream(path):
                        break
                    cast('asyncio.StreamWriter', up_writer).write(
                        _build_modified_request(req_first, req_headers, req_body_modified)
                    )
                elif host == GAMEJOIN_HOST:
                    # Module interceptors: allow request body/URL modification for gamejoin traffic
                    req_body_plain_ = _decompress_body(req_body_raw, req_headers)
                    if self._module_interceptors:
                        gamejoin_flow = ProxyFlow(req_first, req_headers, req_body_plain_, host)
                        for interceptor in list(self._module_interceptors):
                            try:
                                interceptor.request(gamejoin_flow)
                            except Exception as exc:  # ruff: ignore[blind-except]
                                logger.debug('Module interceptor request error: %s', exc)
                        if gamejoin_flow.drop_request:
                            drop_body = gamejoin_flow.drop_body
                            if isinstance(drop_body, str):
                                drop_body = drop_body.encode('utf-8', errors='replace')
                            response_writer.write(
                                _make_local_response(gamejoin_flow.drop_status_code, drop_body)
                            )
                            await response_writer.drain()
                            if not _keep_alive(req_first, req_headers):
                                break
                            continue
                        new_first = _flow_request_modified_first_line(
                            gamejoin_flow.request, req_first
                        )
                        new_body = gamejoin_flow.request.raw_content
                        if not await ensure_upstream(path):
                            break
                        if new_first != req_first or new_body != req_body_plain_:
                            cast('asyncio.StreamWriter', up_writer).write(
                                _build_modified_request(
                                    new_first,
                                    gamejoin_flow.request.headers.to_bytes_dict(),
                                    new_body,
                                )
                            )
                        elif self._wire_preserving_passthrough:
                            cast('asyncio.StreamWriter', up_writer).write(
                                req_raw.raw_header_block + req_body.wire
                            )
                        else:
                            cast('asyncio.StreamWriter', up_writer).write(
                                _reassemble_raw_request(req_first, req_headers, req_body_raw)
                            )
                    else:
                        if not await ensure_upstream(path):
                            break
                        if self._wire_preserving_passthrough:
                            cast('asyncio.StreamWriter', up_writer).write(
                                req_raw.raw_header_block + req_body.wire
                            )
                        else:
                            cast('asyncio.StreamWriter', up_writer).write(
                                _reassemble_raw_request(req_first, req_headers, req_body_raw)
                            )
                elif (
                    host == PROFILE_API_HOST
                    and PROFILE_API_PATH_FRAGMENT in path
                    and self._module_interceptors
                ):
                    req_body_plain_ = _decompress_body(req_body_raw, req_headers)
                    if not await ensure_upstream(path):
                        break
                    profile_flow = ProxyFlow(req_first, req_headers, req_body_plain_, host)
                    if self._preserve_unmodified_wire_for_host(host):
                        cast('asyncio.StreamWriter', up_writer).write(
                            req_raw.raw_header_block + req_body.wire
                        )
                    else:
                        cast('asyncio.StreamWriter', up_writer).write(
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
                        cast('asyncio.StreamWriter', up_writer).write(
                            req_raw.raw_header_block + req_body.wire
                        )
                    else:
                        cast('asyncio.StreamWriter', up_writer).write(
                            _reassemble_raw_request(
                                upstream_req_first, upstream_req_headers, req_body_raw
                            )
                        )

                try:
                    await cast('asyncio.StreamWriter', up_writer).drain()
                except ConnectionResetError, BrokenPipeError, OSError:
                    break

                # ── Read upstream response ────────────────────────────────────
                resp_result = await _read_headers_raw(cast('asyncio.StreamReader', up_reader))
                if resp_result is None:
                    break
                resp_raw = resp_result
                resp_first, resp_headers = resp_raw.first_line, resp_raw.headers
                resp_body = await _read_body_wire(
                    cast('asyncio.StreamReader', up_reader), resp_headers
                )
                resp_body_raw = resp_body.payload

                status_code = _parse_status_code(resp_first)
                custom_fflag_response_enabled = (
                    custom_fflag_request and custom_fflag_modifier.is_enabled()
                )
                if custom_fflag_response_enabled and status_code >= 400:  # ruff: ignore[magic-value-comparison]
                    custom_fflag_modifier.log_response_failure(
                        'upstream-http',
                        f'ClientSettings upstream returned HTTP {status_code}; response left unchanged',  # ruff: ignore[line-too-long]
                    )
                elif (
                    custom_fflag_response_enabled and 200 <= status_code < 300 and not resp_body_raw  # ruff: ignore[magic-value-comparison]
                ):
                    custom_fflag_modifier.log_response_failure(
                        'empty-success',
                        f'ClientSettings upstream returned HTTP {status_code} with an empty body; '
                        'response left unchanged',
                    )
                if host == GAMEJOIN_HOST and 200 <= status_code < 400:  # ruff: ignore[magic-value-comparison]
                    self._note_gamejoin_traffic()
                if status_code in (400, 429) and host in {  # ruff: ignore[literal-membership]
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
                elif status_code >= 400 and host in {  # ruff: ignore[magic-value-comparison]
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
                        f'content-type={ct or "unknown"} body={len(resp_body_raw)} bytes{snippet_text}',  # ruff: ignore[line-too-long]
                    )

                # ── Determine if we need to modify the response body ──────────
                # We only modify if: solidmodel injection is requested.
                # All other responses are forwarded raw (preserving content-encoding).
                response_modified = False
                modified_content_encoding: bytes | None = None
                custom_fflag_delivered_signature: tuple[tuple[str, str], ...] | None = None

                if is_batch:
                    # Batch response: forward raw to Roblox, decompress only for addon hooks
                    resp_body_plain = _decompress_body(resp_body_raw, resp_headers)
                    # Addon hooks must use req_body_modified (what we actually sent to
                    # upstream), NOT req_body_raw. The upstream response is index-aligned
                    # with the modified request. If assets were removed by process_batch_request
                    # (strip_textures, removal rules), using req_body_raw causes every index
                    # after a removed item to map to the wrong response item, producing wrong
                    # assetTypeId values (the root cause of SolidModel/Mesh being typed as Image).
                    texture_batch_processor.process_batch_response(
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

                    if short_circuit is not None and short_circuit[0] in (  # ruff: ignore[literal-membership]
                        'solid',
                        'solid_v3',
                    ):
                        # SolidModel injection - we MUST modify the body
                        resp_body_plain = _decompress_body(resp_body_raw, resp_headers)
                        cdn_base_url = full_url.split('?')[0]
                        prefer_v3 = cast('str', short_circuit[0]) == 'solid_v3'
                        resp_body_raw = await asyncio.get_event_loop().run_in_executor(
                            self._executor,
                            self.texture_stripper.process_solidmodel_response,
                            resp_body_plain,
                            cast('str', short_circuit[1]),
                            cdn_base_url,
                            prefer_v3,
                        )
                        response_modified = True

                    elif short_circuit is not None and short_circuit[0] == 'anim_rig':
                        # Auto-convert rig: read the original CDN bytes to detect the rig,
                        # then serve the rig-matched local replacement (or a converted copy).
                        anim_repl_path, required_rig = cast('_AnimPendingValue', short_circuit[1])
                        orig_bytes = _decompress_body(resp_body_raw, resp_headers)

                        def _pick_rig_matched_file(  # ruff: ignore[complex-structure]
                            orig_bytes: bytes,
                            repl_path: str,
                            required_rig: _AnimRequiredRig = 'any',
                        ) -> bytes:
                            from fleasion.utils import (  # ruff: ignore[import-outside-top-level]
                                log_buffer as _lb,
                            )
                            from fleasion.utils.anim_converter import (  # ruff: ignore[import-outside-top-level]
                                detect_player_rig,
                                detect_rig,
                                is_curve_animation,
                            )

                            orig_rig = detect_rig(orig_bytes)
                            # If this rule only targets specific rig types, skip if it doesn't match
                            if required_rig != 'any' and orig_rig not in required_rig:
                                _lb.log(
                                    'AnimConv',
                                    f'Skipping replacement: original rig={orig_rig}, required={required_rig}',  # ruff: ignore[line-too-long]
                                )
                                return orig_bytes
                            if is_curve_animation(orig_bytes):
                                # Must serve back a CurveAnimation regardless of replacement format.
                                # For non-player animations (unknown rig) use the replacement's own
                                # rig so no unwanted rig conversion is applied.
                                if orig_rig == 'unknown':
                                    target_rig = _texture_detect_repl_rig(
                                        self.texture_stripper, repl_path
                                    )
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
                                conv_path = _texture_get_converted_curve(
                                    self.texture_stripper, repl_path, target_rig
                                )
                                if conv_path:
                                    _lb.log(
                                        'AnimConv',
                                        f'Serving {target_rig} CurveAnimation replacement ({Path(conv_path).name})',  # ruff: ignore[line-too-long]
                                    )
                                    return Path(conv_path).read_bytes()
                                _lb.log(
                                    'AnimConv',
                                    f'CurveAnimation conversion failed for {repl_p.name} → {target_rig}',  # ruff: ignore[line-too-long]
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
                                repl_rig = _texture_detect_repl_rig(
                                    self.texture_stripper, repl_path
                                )
                                if repl_rig == 'unknown':
                                    _lb.log(
                                        'AnimConv',
                                        f'Rig detection unknown for replacement: {Path(repl_path).name}',  # ruff: ignore[line-too-long]
                                    )
                                elif repl_rig != conv_rig:
                                    conv = _texture_get_converted(
                                        self.texture_stripper, repl_path, conv_rig
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
                            orig_bytes,
                            anim_repl_path,
                            required_rig,
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

                elif custom_fflag_response_enabled and 200 <= status_code < 300 and resp_body_raw:  # ruff: ignore[magic-value-comparison]
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
                            custom_fflag_modifier.log_response_failure(
                                'dcz-decode',
                                'Could not decode dictionary-compressed ClientSettings; preserving original response',  # ruff: ignore[line-too-long]
                            )
                        else:
                            (
                                modified_settings,
                                delivered_signature,
                            ) = custom_fflag_modifier.modify_response_with_delivery(
                                path, resp_body_plain
                            )
                            if delivered_signature is not None:
                                if modified_settings != resp_body_plain:
                                    recompressed = _compress_dcz(
                                        modified_settings, cast('bytes', dictionary)
                                    )
                                    if recompressed is None:
                                        custom_fflag_modifier.log_response_failure(
                                            'dcz-encode',
                                            'Could not re-encode dictionary-compressed ClientSettings; preserving original response',  # ruff: ignore[line-too-long]
                                        )
                                    else:
                                        resp_body_raw = recompressed
                                        modified_content_encoding = b'dcz'
                                        response_modified = True
                                        custom_fflag_delivered_signature = delivered_signature
                                else:
                                    custom_fflag_delivered_signature = delivered_signature
                    else:
                        resp_body_plain = _decompress_body(resp_body_raw, resp_headers)
                        (
                            modified_settings,
                            delivered_signature,
                        ) = custom_fflag_modifier.modify_response_with_delivery(
                            path, resp_body_plain
                        )
                        if delivered_signature is not None:
                            custom_fflag_delivered_signature = delivered_signature
                            if modified_settings != resp_body_plain:
                                resp_body_raw = modified_settings
                                response_modified = True

                if (
                    host == GAMEJOIN_HOST
                    and gamejoin_flow is not None
                    and self._module_interceptors
                ):
                    resp_body_plain_ = _decompress_body(resp_body_raw, resp_headers)
                    gamejoin_flow.response = _FlowResponse(resp_first, resp_body_plain_)
                    for interceptor in list(self._module_interceptors):
                        try:
                            interceptor.response(gamejoin_flow)
                        except Exception as exc:  # ruff: ignore[blind-except]
                            logger.debug('Module interceptor response error: %s', exc)
                    gamejoin_response = _flow_response_after_callbacks(gamejoin_flow)
                    if (
                        gamejoin_response is not None
                        and gamejoin_response.content != resp_body_plain_
                    ):
                        resp_body_raw = gamejoin_response.content
                        response_modified = True
                elif (
                    host == PROFILE_API_HOST
                    and profile_flow is not None
                    and self._module_interceptors
                ):
                    resp_body_plain_ = _decompress_body(resp_body_raw, resp_headers)
                    profile_flow.response = _FlowResponse(resp_first, resp_body_plain_)
                    for interceptor in list(self._module_interceptors):
                        try:
                            interceptor.response(profile_flow)
                        except Exception as exc:  # ruff: ignore[blind-except]
                            logger.debug('Module interceptor response error: %s', exc)
                    profile_response = _flow_response_after_callbacks(profile_flow)
                    if (
                        profile_response is not None
                        and profile_response.content != resp_body_plain_
                    ):
                        resp_body_raw = profile_response.content
                        response_modified = True

                # Auto Replace runs last, on whatever body is about to be
                # sent (Fleasion's own feature processing above included) -
                # skipped for a dcz-recompressed ClientSettings body since
                # that's already a specific binary encoding, not something a
                # text/regex replace should touch.
                if self._auto_replace_rules and modified_content_encoding != b'dcz':
                    resp_headers, resp_header_changed = apply_auto_replace_header_rules(
                        self._auto_replace_rules, 'response', host, path, resp_headers
                    )
                    resp_plain = (
                        resp_body_raw
                        if response_modified
                        else _decompress_body(resp_body_raw, resp_headers)
                    )
                    resp_replaced, resp_body_changed = apply_auto_replace_rules(
                        self._auto_replace_rules, 'response', host, path, resp_plain
                    )
                    if resp_header_changed or resp_body_changed:
                        # Whenever EITHER changed, resp_body_raw must end up
                        # as plain (decompressed) bytes - modified_content_encoding
                        # is about to be cleared, so whatever's sent must
                        # actually match "no content-encoding" being true.
                        resp_body_raw = resp_replaced if resp_body_changed else resp_plain
                        response_modified = True
                        modified_content_encoding = None
                        if (
                            custom_fflag_delivered_signature is not None
                            and not custom_fflag_modifier.body_carries_signature(
                                resp_body_raw, custom_fflag_delivered_signature
                            )
                        ):
                            custom_fflag_delivered_signature = None

                # ── Forward response to Roblox ────────────────────────────────
                if response_modified:
                    outgoing_response = _build_modified_response(
                        resp_first,
                        resp_headers,
                        resp_body_raw,
                        content_encoding=modified_content_encoding,
                    )
                elif self._preserve_unmodified_wire_for_host(host):
                    outgoing_response = resp_raw.raw_header_block + resp_body.wire
                else:
                    outgoing_response = _reassemble_raw_response(
                        resp_first, resp_headers, resp_body_raw
                    )
                response_writer.write(outgoing_response)

                delivery_ack_deferred = False
                if custom_fflag_delivered_signature is not None:
                    delivered_signature = custom_fflag_delivered_signature
                    delivery_generation = custom_fflag_request_generation

                    def _ack_custom_fflag_delivery(
                        signature: tuple[tuple[str, str], ...] = delivered_signature,
                        generation: int | None = delivery_generation,
                    ) -> None:
                        custom_fflag_modifier.note_response_success(
                            signature,
                            generation=generation,
                        )

                    delivery_ack_deferred = response_writer.defer_delivery_acknowledgement(
                        _ack_custom_fflag_delivery
                    )

                try:
                    await response_writer.drain()
                except ConnectionResetError, BrokenPipeError, OSError:
                    break

                if custom_fflag_delivered_signature is not None and not delivery_ack_deferred:
                    custom_fflag_modifier.note_response_success(
                        custom_fflag_delivered_signature,
                        generation=custom_fflag_request_generation,
                    )

                if not _keep_alive(req_first, req_headers) or not _keep_alive(
                    resp_first, resp_headers
                ):
                    break
        finally:
            try:  # ruff: ignore[suppressible-exception]
                await response_writer.flush_pending_response()
            except Exception:  # ruff: ignore[blind-except, try-except-pass]
                pass
            if up_writer is not None:
                try:  # ruff: ignore[suppressible-exception]
                    up_writer.close()
                except Exception:  # ruff: ignore[blind-except, try-except-pass]
                    pass
