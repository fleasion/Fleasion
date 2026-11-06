"""Read-only TLS 1.2/1.3 secret recovery for macOS Roblox Player under Rosemary on Linux.

All reads go through /proc/<pid>/mem. No writes, no code injection.
Layout validated against OpenSSL 3.5.0 as shipped in x86_64 macOS Roblox.
"""

from __future__ import annotations

import hmac
import logging
import struct
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from .tls12_session import LP64_OPENSSL_350, TLS12_VERSION, validate_tls12_session
from .tls_records import expand_label

if TYPE_CHECKING:
    import io

log = logging.getLogger(__name__)

type ConnectionSecrets = dict[str, bytes]
type SecretMap = dict[str, ConnectionSecrets]

STRUCT_SIZE = 0x900
TLS13 = 0x304
MIN_DISTINCT_BYTES = 20
SHA256_SIZE = 32
CHUNK_SIZE = 4 * 1024 * 1024
_MAX_HINTED_PIDS = 8
_MAX_HINTED_REGIONS = 8
_region_hints: dict[int, list[tuple[int, int]]] = {}
_region_hints_lock = threading.Lock()


def _prioritize_regions(pid: int, regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
    with _region_hints_lock:
        preferred = _region_hints.get(pid, ())
        active = set(regions)
        ordered = [region for region in preferred if region in active]
    seen = set(ordered)
    return ordered + [region for region in regions if region not in seen]


def _remember_region(pid: int, region: tuple[int, int]) -> None:
    with _region_hints_lock:
        previous = _region_hints.pop(pid, ())
        remembered = [region, *(entry for entry in previous if entry != region)]
        _region_hints[pid] = remembered[:_MAX_HINTED_REGIONS]
        if len(_region_hints) > _MAX_HINTED_PIDS:
            _region_hints.pop(next(iter(_region_hints)))


def find_rosemary_pid() -> int | None:
    """Find the Roblox player process PID under Rosemary on Linux.

    Returns
    -------
    int | None
        Process ID of the primary Rosemary RobloxPlayer instance, or None if not found.
    """
    if sys.platform != 'linux':
        return None

    proc = Path('/proc')
    try:
        entries = list(proc.iterdir())
    except OSError, PermissionError:
        return None

    candidates: list[tuple[int, int]] = []

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            cmdline_bytes = (entry / 'cmdline').read_bytes()
            cmdline = cmdline_bytes.replace(b'\x00', b' ').decode('utf-8', errors='ignore').lower()
        except OSError, PermissionError, ValueError:
            continue

        if 'rosemary' not in cmdline:
            continue

        # Fleasion must never intercept or modify Roblox Studio
        if 'studio' in cmdline:
            continue

        if 'roblox.app' in cmdline or 'robloxplayer' in cmdline:
            try:
                status = (entry / 'status').read_text()
                if 'State:\tZ' in status:
                    continue
                statm = (entry / 'statm').read_text().split()
                rss = int(statm[1]) if len(statm) > 1 else 0
            except OSError, PermissionError, ValueError:
                continue
            candidates.append((rss, pid))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def validate_openssl_struct(raw: bytes) -> tuple[str, dict[str, bytes]] | None:
    """Validate an OpenSSL 3.5.0 SSL_CONNECTION struct in macOS memory layout.

    Parameters
    ----------
    raw : bytes
        Raw memory bytes of at least STRUCT_SIZE (0x900) bytes.

    Returns
    -------
    tuple[str, dict[str, bytes]] | None
        Client random hex string and extracted secret dictionary,
        or None if validation failed.
    """
    if len(raw) < STRUCT_SIZE or struct.unpack_from('<I', raw, 0x48)[0] != TLS13:
        return None
    if struct.unpack_from('<I', raw, 0x7C)[0] != 0:
        return None

    cr, sr = raw[0x188:0x1A8], raw[0x168:0x188]
    if len(set(cr)) < MIN_DISTINCT_BYTES or len(set(sr)) < MIN_DISTINCT_BYTES:
        return None

    cb, sb = raw[0x6FC:0x73C], raw[0x73C:0x77C]
    n: int | None = None
    for cand in (32, 48, 64):
        if (
            len(set(cb[:cand])) >= MIN_DISTINCT_BYTES
            and len(set(sb[:cand])) >= MIN_DISTINCT_BYTES
            and not any(cb[cand:])
            and not any(sb[cand:])
        ):
            n = cand
            break

    if n not in {32, 48}:
        return None

    hn = 'sha256' if n == SHA256_SIZE else 'sha384'
    hs = raw[0x5BC : 0x5BC + n]
    master = raw[0x5FC : 0x5FC + n]
    hh = raw[0x73C : 0x73C + n]
    fh = raw[0x6FC : 0x6FC + n]
    ch = expand_label(hs, b'c hs traffic', n, hn, hh)
    sh = expand_label(hs, b's hs traffic', n, hn, hh)
    client = raw[0x77C : 0x77C + n]
    server = raw[0x7BC : 0x7BC + n]
    exporter = raw[0x7FC : 0x7FC + n]

    ok = (
        hmac.compare_digest(client, expand_label(master, b'c ap traffic', n, hn, fh))
        and hmac.compare_digest(server, expand_label(master, b's ap traffic', n, hn, fh))
        and hmac.compare_digest(raw[0x67C : 0x67C + n], expand_label(ch, b'finished', n, hn, b''))
        and hmac.compare_digest(raw[0x6BC : 0x6BC + n], expand_label(sh, b'finished', n, hn, b''))
        and hmac.compare_digest(exporter, expand_label(master, b'exp master', n, hn, fh))
    )
    if not ok:
        return None

    return cr.hex(), {
        'CLIENT_HANDSHAKE_TRAFFIC_SECRET': ch,
        'SERVER_HANDSHAKE_TRAFFIC_SECRET': sh,
        'CLIENT_TRAFFIC_SECRET_0': client,
        'SERVER_TRAFFIC_SECRET_0': server,
        'EXPORTER_SECRET': exporter,
    }


def _module_bounds(pid: int) -> tuple[int, int]:
    maps_path = Path(f'/proc/{pid}/maps')
    try:
        with maps_path.open(encoding='utf-8', errors='ignore') as source:
            for line in source:
                parts = line.split()
                if len(parts) >= 6 and 'robloxplayer' in parts[5].lower():
                    addrs = parts[0].split('-')
                    return int(addrs[0], 16), int(addrs[1], 16) + 0x20000000
    except OSError, PermissionError:
        pass
    return 0, 0


def _regions(pid: int) -> list[tuple[int, int]]:
    maps_path = Path(f'/proc/{pid}/maps')
    regions: list[tuple[int, int]] = []
    try:
        with maps_path.open(encoding='utf-8', errors='ignore') as source:
            lines = source.readlines()
    except OSError, PermissionError:
        return regions

    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith('rw') and 'p' in parts[1]:
            pathname = parts[5] if len(parts) > 5 else ''
            if pathname and not pathname.lower().endswith('robloxplayer'):
                continue
            addrs = parts[0].split('-')
            start, end = int(addrs[0], 16), int(addrs[1], 16)
            regions.append((start, end - start))
    return regions


def _scan_candidate(  # ruff: ignore[too-many-return-statements]
    mem: io.BufferedReader,
    buf: bytes,
    *,
    start: int,
    offset: int,
    position: int,
    field_offset: int,
    module_base: int,
    module_end: int,
) -> tuple[str, dict[str, bytes]] | None:
    cand_offset = position - field_offset
    if cand_offset >= 0 and cand_offset + STRUCT_SIZE <= len(buf):
        raw = buf[cand_offset : cand_offset + STRUCT_SIZE]
    else:
        candidate = start + offset + position - field_offset
        try:
            mem.seek(candidate)
            raw = mem.read(STRUCT_SIZE)
        except OSError, PermissionError:
            return None

    if len(raw) != STRUCT_SIZE:
        return None
    method = struct.unpack_from('<Q', raw, 0x18)[0]
    if module_base != 0 and not (module_base <= method < module_end):
        return None
    if struct.unpack_from('<I', raw, 0x48)[0] != TLS12_VERSION:
        return validate_openssl_struct(raw)

    # TLS 1.2 adds a session-pointer read. Keep the TLS 1.3 fast path
    # unchanged and ask for more bytes only for a plausible TLS 1.2 struct
    candidate = start + offset + position - field_offset
    try:
        mem.seek(candidate)
        raw = mem.read(LP64_OPENSSL_350.minimum_size)
    except OSError, PermissionError:
        return None
    if len(raw) != LP64_OPENSSL_350.minimum_size:
        return None

    def read_session(addr: int, length: int) -> bytes:
        try:
            mem.seek(addr)
            return mem.read(length)
        except OSError, PermissionError, ValueError:
            return b''

    return validate_tls12_session(raw, read_session, layout=LP64_OPENSSL_350)


def _scan_region(
    mem: io.BufferedReader,
    start: int,
    size: int,
    *,
    needle: bytes,
    field_offset: int,
    module_base: int,
    module_end: int,
    client_random: str | None,
    stop: threading.Event | None,
    found: SecretMap,
) -> bool:
    for offset in range(0, size, CHUNK_SIZE):
        if stop is not None and stop.is_set():
            return True
        chunk_len = min(CHUNK_SIZE + len(needle) - 1, size - offset)
        try:
            mem.seek(start + offset)
            buf = mem.read(chunk_len)
        except OSError, PermissionError:
            continue
        position = buf.find(needle)
        while position >= 0:
            res = _scan_candidate(
                mem,
                buf,
                start=start,
                offset=offset,
                position=position,
                field_offset=field_offset,
                module_base=module_base,
                module_end=module_end,
            )
            if res is not None:
                cr_hex, secrets = res
                found[cr_hex] = secrets
                if client_random is not None and cr_hex == client_random:
                    return True
            position = buf.find(needle, position + 1)
    return False


def _scan_ordered_regions(
    mem: io.BufferedReader,
    pid: int,
    *,
    needle: bytes,
    field_offset: int,
    module_base: int,
    module_end: int,
    client_random: str | None,
    stop: threading.Event | None,
    found: SecretMap,
) -> SecretMap:
    for region in _prioritize_regions(pid, _regions(pid)):
        start, size = region
        previously_found = len(found)
        matched = _scan_region(
            mem,
            start,
            size,
            needle=needle,
            field_offset=field_offset,
            module_base=module_base,
            module_end=module_end,
            client_random=client_random,
            stop=stop,
            found=found,
        )
        if len(found) > previously_found:
            _remember_region(pid, region)
        if matched:
            break
    return found


def scan(
    pid: int,
    *,
    stop: threading.Event | None = None,
    client_random: str | None = None,
) -> SecretMap:
    """Scan Rosemary Roblox process memory for TLS 1.2/1.3 secrets.

    Parameters
    ----------
    pid : int
        Target process identifier.
    stop : threading.Event, optional
        Cancellation event to abort memory scanning early.
    client_random : str, optional
        Specific 32-byte client random in hex to look for.

    Returns
    -------
    SecretMap
        Recovered TLS 1.3 traffic secrets keyed by client random.
    """
    wanted: bytes | None = None
    if client_random is not None:
        try:
            wanted = bytes.fromhex(client_random) if len(client_random) == 64 else None
        except ValueError:
            wanted = None
        if wanted is None:
            return {}

    needle = wanted or b'\x04\x03\x00\x00'
    field_offset = 0x188 if wanted is not None else 0x48
    found: SecretMap = {}
    module_base, module_end = _module_bounds(pid)
    mem_path = Path(f'/proc/{pid}/mem')
    try:
        with mem_path.open('rb') as mem:
            return _scan_ordered_regions(
                mem,
                pid,
                needle=needle,
                field_offset=field_offset,
                module_base=module_base,
                module_end=module_end,
                client_random=client_random,
                stop=stop,
                found=found,
            )
    except OSError, PermissionError, ProcessLookupError:
        return found
