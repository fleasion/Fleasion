"""Read-only TLS 1.2/1.3 secret recovery for community Android Roblox runtimes on Linux.

Supports Cordial, Nightcap, and Mocktail running x86_64 Android Roblox (libroblox.so).
All reads go through /proc/<pid>/mem. No writes, no code injection.
Layout validated against OpenSSL 3.5.0 in Android NDK x86_64 (LP64).
"""

from __future__ import annotations

import logging
import os
import pathlib
import struct
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.utils.linux_clients import native_nightcap_desktop

from .rosemary_keys import STRUCT_SIZE, validate_openssl_struct
from .tls12_session import LP64_OPENSSL_350, TLS12_VERSION, validate_tls12_session

if TYPE_CHECKING:
    import io
    import threading

log = logging.getLogger(__name__)

type ConnectionSecrets = dict[str, bytes]
type SecretMap = dict[str, ConnectionSecrets]

CHUNK_SIZE = 4 * 1024 * 1024
COMMUNITY_TARGETS = frozenset({'cordial', 'nightcap', 'mocktail'})


def _read_process_ident(entry: Path) -> tuple[str, str, str] | None:
    try:
        cmdline_bytes = (entry / 'cmdline').read_bytes()
    except OSError, PermissionError:
        return None
    cmdline = cmdline_bytes.replace(b'\x00', b' ').decode('utf-8', errors='ignore').lower()

    try:
        exe = (entry / 'exe').resolve().name.lower()
    except OSError, PermissionError:
        exe = ''

    try:
        comm = (entry / 'comm').read_text(encoding='utf-8', errors='ignore').strip().lower()
    except OSError, PermissionError:
        comm = ''

    return cmdline, exe, comm


def _read_process_rss_if_active(entry: Path) -> int | None:
    try:
        status = (entry / 'status').read_text()
        if 'State:\tZ' in status:
            return None
        with (entry / 'maps').open(encoding='utf-8', errors='ignore') as maps_file:
            if not any('libroblox' in line for line in maps_file):
                return None
        statm = (entry / 'statm').read_text().split()
        return int(statm[1]) if len(statm) > 1 else 0
    except OSError, PermissionError, ValueError:
        return None


def _client_identity(  # ruff: ignore[too-many-return-statements]
    entry: Path, ident: tuple[str, str, str]
) -> str | None:
    """Identify the loaded engine, distinguishing Nightcap's mocktail binary."""
    cmdline, exe, _comm = ident
    first_arg = cmdline.split(' ', 1)[0].rsplit('/', 1)[-1]
    if exe == 'cordial-run' and first_arg == 'cordial-run':
        return 'cordial'
    if exe != 'mocktail' or first_arg != 'mocktail':
        return None
    try:
        cgroup = (entry / 'cgroup').read_text(encoding='utf-8', errors='replace')
    except OSError:
        cgroup = ''
    if 'app-flatpak-io.github.CoderDayton.nightcap-' in cgroup:
        return 'nightcap'
    if 'app-flatpak-space.bigrat.mocktail-' in cgroup:
        return 'mocktail'
    if 'app-flatpak-' in cgroup:
        # Unknown Flatpak engines are never silently attributed to Mocktail
        return None
    # Native Nightcap and Mocktail ship the same executable name and cannot
    # coexist as distribution packages. The native desktop identity decides.
    if native_nightcap_desktop(pathlib.Path.home(), os.environ):
        return 'nightcap'
    return 'mocktail'


def find_android_community_pid(client: str | None = None) -> tuple[int, str] | None:
    """Find the primary community Android Roblox process PID on Linux.

    Parameters
    ----------
    client : str, optional
        Specific bootstrapper name ('cordial', 'nightcap', or 'mocktail').
        If omitted, discovers any active community bootstrapper.

    Returns
    -------
    tuple[int, str] | None
        Process ID and matched bootstrapper name, or None if not found.
    """
    if sys.platform != 'linux':
        return None

    proc = Path('/proc')
    try:
        entries = list(proc.iterdir())
    except OSError, PermissionError:
        return None

    targets = frozenset({client.strip().lower()}) if client else COMMUNITY_TARGETS
    if not targets <= COMMUNITY_TARGETS:
        return None
    candidates: list[tuple[int, int, str]] = []

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
        except ValueError:
            continue

        try:
            if entry.stat().st_uid != os.getuid():
                continue
        except OSError:
            continue
        ident = _read_process_ident(entry)
        if ident is None:
            continue
        matched_client = _client_identity(entry, ident)
        if matched_client not in targets:
            continue

        rss = _read_process_rss_if_active(entry)
        if rss is None:
            continue

        candidates.append((rss, pid, matched_client))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1], candidates[0][2]


def _module_bounds(pid: int) -> tuple[int, int]:
    maps_path = Path(f'/proc/{pid}/maps')
    try:
        with maps_path.open(encoding='utf-8', errors='ignore') as source:
            lines = source.readlines()
    except OSError, PermissionError:
        return 0, 0

    min_base = 0
    max_end = 0
    for line in lines:
        parts = line.split()
        if len(parts) >= 6 and 'libroblox' in parts[5].lower():
            addrs = parts[0].split('-')
            start, end = int(addrs[0], 16), int(addrs[1], 16)
            min_base = start if min_base == 0 else min(min_base, start)
            max_end = max(max_end, end)
    return min_base, max_end


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
            # Scan anonymous heap regions or libroblox.so data
            if pathname and not pathname.lower().endswith('libroblox.so'):
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


def scan(
    pid: int,
    *,
    stop: threading.Event | None = None,
    client_random: str | None = None,
) -> SecretMap:
    """Scan community Android Roblox process memory for TLS 1.2/1.3 secrets.

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
            for start, size in _regions(pid):
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
                if matched:
                    return found
    except OSError, PermissionError, ProcessLookupError:
        return found

    return found
