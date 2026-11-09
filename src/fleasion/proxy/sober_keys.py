"""Read-only TLS 1.2/1.3 secret recovery for Sober Android runtime on Linux.

Supports Sober (org.vinegarhq.Sober) running containerized x86_64 Android Roblox.
Reads process memory via process_vm_readv or /proc/<pid>/mem without code injection.
Layout validated against OpenSSL 3.5.0 in Android NDK x86_64 (SysV LP64).
"""

from __future__ import annotations

import ctypes
import logging
import os
import struct
import sys
import threading
from ctypes import POINTER, Structure, byref, c_int, c_size_t, c_ssize_t, c_ulong, c_void_p, cast
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from .rosemary_keys import STRUCT_SIZE, validate_openssl_struct
from .tls12_session import LP64_OPENSSL_350, TLS12_VERSION, validate_tls12_session

log = logging.getLogger(__name__)

type ConnectionSecrets = dict[str, bytes]
type SecretMap = dict[str, ConnectionSecrets]

CHUNK_SIZE = 1024 * 1024
# Include a whole TLS client random at each chunk boundary for process_vm_readv
_SCAN_OVERLAP = 31
_PAGE_SIZE = os.sysconf('SC_PAGESIZE') if sys.platform == 'linux' else 4096
# Limit per-MiB sparse probing on large Android memory reservations
_LARGE_VMA_ZERO_PAGE_PROBES = 16
# A VMA's total resident bytes are not necessarily a populated prefix of the VMA
# Only expand sufficiently populated mappings on targeted fallback scans
_FALLBACK_MIN_RSS_KB = 1024
_FALLBACK_SMALL_VMA_MAX = 8 * 1024 * 1024
_FALLBACK_MAX_VIRTUAL_BYTES = 8 * 1024 * 1024 * 1024
_NEARBY_SCAN_SIZE = 512 * 1024
_MAX_HINTED_PROCESSES = 8
_MAX_HINTED_ADDRESSES = 12


# Key by PID and Linux starttime to prevent reused process IDs inheriting hints
@dataclass(frozen=True, slots=True)
class _ValidatedLocation:
    region_start: int
    region_size: int
    address: int
    module_base: int
    module_end: int


_validated_addresses: dict[tuple[int, int], list[_ValidatedLocation]] = {}
_validated_addresses_lock = threading.Lock()
SOBER_CGROUP_SUBSTRING = 'org.vinegarhq.sober'
LARGE_MEMFD_MIN_SIZE = 10 * 1024 * 1024
_SCANNABLE_PATHNAMES = frozenset({'', '[heap]', '/memfd: (deleted)'})


class _Iovec(Structure):
    _fields_ = [('iov_base', c_void_p), ('iov_len', c_size_t)]


_libc: ctypes.CDLL | None = None
_pvr_func: Any = None


def _init_pvr_func(libc: ctypes.CDLL) -> Any:
    func = getattr(libc, 'process_vm_readv', None)
    if func is not None:
        func.argtypes = [
            c_int,
            POINTER(_Iovec),
            c_ulong,
            POINTER(_Iovec),
            c_ulong,
            c_ulong,
        ]
        func.restype = c_ssize_t
    return func


def _get_process_vm_readv() -> Any:
    global _libc, _pvr_func  # ruff: ignore[global-statement]
    if _pvr_func is not None:
        return _pvr_func
    try:
        _libc = ctypes.CDLL(None, use_errno=True)
        _pvr_func = _init_pvr_func(_libc)
    except AttributeError, OSError:
        _pvr_func = None
    return _pvr_func


class _MemoryReader:
    """Read remote process virtual memory using process_vm_readv or /proc/pid/mem."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._buf = ctypes.create_string_buffer(CHUNK_SIZE + _SCAN_OVERLAP)
        buf_addr = cast(self._buf, c_void_p).value
        self._local_iov = _Iovec(buf_addr, CHUNK_SIZE + _SCAN_OVERLAP)
        self._remote_iov = _Iovec(0, 0)
        self._pvr = _get_process_vm_readv()
        self._mem_file = None
        try:
            self._mem_file = Path(f'/proc/{pid}/mem').open('rb')  # ruff: ignore[open-file-with-context-handler]
        except OSError, PermissionError:
            pass

    def read(self, addr: int, size: int) -> bytes:
        """Read bytes at remote virtual address."""
        if self._mem_file is not None:
            try:
                self._mem_file.seek(addr)
                data = self._mem_file.read(size)
                if data:
                    return data
            except OSError, PermissionError:
                pass

        if self._pvr is None:
            return b''

        to_read = min(size, CHUNK_SIZE + _SCAN_OVERLAP)
        self._remote_iov.iov_base = addr
        self._remote_iov.iov_len = to_read
        self._local_iov.iov_len = to_read
        res = self._pvr(self.pid, byref(self._local_iov), 1, byref(self._remote_iov), 1, 0)
        if res <= 0:
            return b''
        return ctypes.string_at(self._buf, min(res, ctypes.sizeof(self._buf)))

    def close(self) -> None:
        """Close opened resources."""
        if self._mem_file is not None:
            try:
                self._mem_file.close()
            except OSError:
                pass
            self._mem_file = None


def _is_sober_entry(entry: Path) -> bool:
    try:
        cgroup = (entry / 'cgroup').read_text(encoding='utf-8', errors='ignore')
        if SOBER_CGROUP_SUBSTRING in cgroup.lower():
            return True
    except OSError, PermissionError:
        pass

    try:
        exe = (entry / 'exe').resolve().name.lower()
        if exe == 'sober':
            return True
    except OSError, PermissionError:
        pass

    return False


def _read_process_score(entry: Path) -> int | None:
    try:
        status = (entry / 'status').read_text(encoding='utf-8', errors='ignore')
        if 'State:\tZ' in status:
            return None
        comm = (entry / 'comm').read_text(encoding='utf-8', errors='ignore').strip()
        statm = (entry / 'statm').read_text(encoding='utf-8', errors='ignore').split()
        rss = int(statm[1]) if len(statm) > 1 else 0
        return rss + (1_000_000_000 if comm.lower() == 'main' else 0)
    except OSError, PermissionError, ValueError:
        return None


def find_sober_pid() -> int | None:
    """Locate the active Sober Roblox engine process identifier.

    Returns
    -------
    int | None
        Process ID of the Sober Roblox engine if running, else None.
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
        except ValueError:
            continue

        if not _is_sober_entry(entry):
            continue

        score = _read_process_score(entry)
        if score is not None:
            candidates.append((score, pid))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def _module_bounds(pid: int) -> tuple[int, int]:
    maps_path = Path(f'/proc/{pid}/maps')
    try:
        with maps_path.open(encoding='utf-8', errors='ignore') as source:
            lines = source.readlines()
    except OSError, PermissionError:
        return 0, 0

    target_inode: str | None = None
    for line in lines:
        parts = line.split()
        if len(parts) >= 6 and parts[5].startswith('/memfd:'):
            addrs = parts[0].split('-')
            size = int(addrs[1], 16) - int(addrs[0], 16)
            if size >= LARGE_MEMFD_MIN_SIZE:
                target_inode = parts[4]
                break

    min_base = 0
    max_end = 0
    for line in lines:
        parts = line.split()
        if len(parts) < 6:
            continue
        pathname = parts[5].lower()
        inode = parts[4]
        matches = (
            (target_inode is not None and inode == target_inode)
            or 'libroblox' in pathname
            or 'libssl' in pathname
        )
        if matches:
            addrs = parts[0].split('-')
            start, end = int(addrs[0], 16), int(addrs[1], 16)
            min_base = start if min_base == 0 else min(min_base, start)
            max_end = max(max_end, end)

    return min_base, max_end


def _parse_smaps_regions(
    smaps_path: Path, *, full_vmas: bool = False, min_rss_kb: int = 0
) -> list[tuple[int, int]]:
    try:
        lines = smaps_path.read_text(encoding='utf-8', errors='ignore').splitlines()
    except OSError, PermissionError:
        return []

    regions: list[tuple[int, int]] = []
    current_start = 0
    current_vsize = 0
    is_candidate = False
    for line in lines:
        if '-' in line and ':' not in line.split()[0]:
            parts = line.split()
            pathname = parts[5] if len(parts) > 5 else ''
            is_candidate = (
                len(parts) >= 2
                and parts[1].startswith('rw')
                and 'p' in parts[1]
                and pathname in _SCANNABLE_PATHNAMES
            )
            if is_candidate:
                addrs = parts[0].split('-')
                current_start = int(addrs[0], 16)
                current_vsize = int(addrs[1], 16) - current_start
        elif line.startswith('Rss:') and is_candidate:
            rss_kb = int(line.split()[1])
            if rss_kb > 0 and rss_kb >= min_rss_kb:
                scan_len = current_vsize if full_vmas else min(current_vsize, (rss_kb + 256) * 1024)
                regions.append((current_start, scan_len))
            is_candidate = False
    return regions


def _parse_maps_regions(maps_path: Path) -> list[tuple[int, int]]:
    try:
        lines = maps_path.read_text(encoding='utf-8', errors='ignore').splitlines()
    except OSError, PermissionError:
        return []

    regions: list[tuple[int, int]] = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith('rw') and 'p' in parts[1]:
            pathname = parts[5] if len(parts) > 5 else ''
            if pathname not in _SCANNABLE_PATHNAMES:
                continue
            addrs = parts[0].split('-')
            start, end = int(addrs[0], 16), int(addrs[1], 16)
            regions.append((start, end - start))
    return regions


def _regions(pid: int) -> list[tuple[int, int]]:
    regions = _parse_smaps_regions(Path(f'/proc/{pid}/smaps'))
    if regions:
        return regions
    return _parse_maps_regions(Path(f'/proc/{pid}/maps'))


def _targeted_fallback_regions(pid: int) -> list[tuple[int, int]]:
    """Expand likely heap arenas and small sparse mappings within a scan budget."""
    smaps_path = Path(f'/proc/{pid}/smaps')
    regions = _parse_smaps_regions(
        smaps_path,
        full_vmas=True,
        min_rss_kb=_FALLBACK_MIN_RSS_KB,
    )
    # Android uses sparse reservations: an SSL object may live on a single
    # resident page of a small VMA. Include those cheap regions even when
    # total RSS is below the large-arena threshold
    large_starts = {start for start, _ in regions}
    small_resident_regions = _parse_smaps_regions(
        smaps_path,
        full_vmas=True,
        min_rss_kb=1,
    )
    regions.extend(
        (start, size)
        for start, size in small_resident_regions
        if start not in large_starts and size <= _FALLBACK_SMALL_VMA_MAX
    )
    total = 0
    selected: list[tuple[int, int]] = []
    for start, size in regions:
        if total + size > _FALLBACK_MAX_VIRTUAL_BYTES:
            continue
        selected.append((start, size))
        total += size
    return selected


def _process_start_ticks(pid: int) -> int | None:
    """Identify the running engine generation without assuming PID uniqueness."""
    try:
        status = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8')
        return int(status.rsplit(')', 1)[1].split()[19])
    except IndexError, OSError, ValueError:
        return None


def _remember_validated_address(  # ruff: ignore[too-many-positional-arguments]
    pid: int,
    start_ticks: int | None,
    address: int,
    region_start: int,
    region_size: int,
    module_base: int,
    module_end: int,
) -> None:
    """Cache only validated allocations and their mapping, never secret bytes."""
    if start_ticks is None or not region_start <= address < region_start + region_size:
        return
    identity = (pid, start_ticks)
    location = _ValidatedLocation(region_start, region_size, address, module_base, module_end)
    with _validated_addresses_lock:
        prior = _validated_addresses.pop(identity, ())
        _validated_addresses[identity] = [
            location,
            *(previous for previous in prior if previous != location),
        ][:_MAX_HINTED_ADDRESSES]
        if len(_validated_addresses) > _MAX_HINTED_PROCESSES:
            _validated_addresses.pop(next(iter(_validated_addresses)))


def _nearby_validated_regions(
    pid: int,
    start_ticks: int | None,
) -> list[tuple[int, int, _ValidatedLocation]]:
    """Use verified neighborhoods without an expensive smaps read on a hit."""
    if start_ticks is None:
        return []
    with _validated_addresses_lock:
        hints = tuple(_validated_addresses.get((pid, start_ticks), ()))
    windows: list[tuple[int, int, _ValidatedLocation]] = []
    seen: set[tuple[int, int]] = set()
    for hint in hints:
        length = min(_NEARBY_SCAN_SIZE, hint.region_size)
        start = max(
            hint.region_start,
            min(
                hint.address - length // 2,
                hint.region_start + hint.region_size - length,
            ),
        )
        window = (start, length)
        if window not in seen:
            windows.append((start, length, hint))
            seen.add(window)
    return windows


def _scan_candidate(
    reader: _MemoryReader,
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
        raw = reader.read(candidate, STRUCT_SIZE)

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
    raw = reader.read(candidate, LP64_OPENSSL_350.minimum_size)
    if len(raw) != LP64_OPENSSL_350.minimum_size:
        return None
    return validate_tls12_session(raw, reader.read, layout=LP64_OPENSSL_350)


def _scan_region(
    reader: _MemoryReader,
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
    on_valid_candidate: Callable[[int], None] | None = None,
) -> bool:
    offset = 0
    zero_page_probes = 0
    while offset < size:
        if stop is not None and stop.is_set():
            return True
        chunk_len = min(CHUNK_SIZE + len(needle) - 1, size - offset)
        buf = reader.read(start + offset, chunk_len)
        if not buf:
            # Sober's Android memory mappings can have an unreadable first
            # page followed immediately by live TLS allocations. A 1 MiB
            # stride would silently discard the entire small VMA
            if size <= _FALLBACK_SMALL_VMA_MAX or zero_page_probes < _LARGE_VMA_ZERO_PAGE_PROBES:
                zero_page_probes += 1
                next_page = ((start + offset) // _PAGE_SIZE + 1) * _PAGE_SIZE
                offset = next_page - start
            else:
                # Huge sparse guest reservations must remain bounded: skip
                # the remainder of this MiB after a limited page probe
                offset = (offset // CHUNK_SIZE + 1) * CHUNK_SIZE
                zero_page_probes = 0
            continue
        zero_page_probes = 0
        position = buf.find(needle)
        while position >= 0:
            res = _scan_candidate(
                reader,
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
                if on_valid_candidate is not None:
                    on_valid_candidate(start + offset + position - field_offset)
                if client_random is not None and cr_hex == client_random:
                    return True
            position = buf.find(needle, position + 1)
        if len(buf) < chunk_len:
            # A short process_vm_readv read reaches a hole before the MiB
            # boundary. Revisit its final overlap to catch straddling randoms
            offset += max(1, len(buf) - _SCAN_OVERLAP)
        else:
            offset += min(len(buf), CHUNK_SIZE)
    return False


def scan(
    pid: int,
    *,
    stop: threading.Event | None = None,
    client_random: str | None = None,
) -> SecretMap:
    """Scan Sober Android Roblox process memory for TLS 1.2/1.3 secrets.

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

    start_ticks = _process_start_ticks(pid)
    reader = _MemoryReader(pid)
    try:
        if client_random is not None:
            for start, size, hint in _nearby_validated_regions(pid, start_ticks):
                if _scan_region(
                    reader,
                    start,
                    size,
                    needle=needle,
                    field_offset=field_offset,
                    module_base=hint.module_base,
                    module_end=hint.module_end,
                    client_random=client_random,
                    stop=stop,
                    found=found,
                    on_valid_candidate=lambda addr, hint=hint: _remember_validated_address(
                        pid,
                        start_ticks,
                        addr,
                        hint.region_start,
                        hint.region_size,
                        hint.module_base,
                        hint.module_end,
                    ),
                ):
                    return found
        # Only consult expensive /proc/<pid>/smaps and module mappings when
        # this process generation has no nearby validated connection
        module_base, module_end = _module_bounds(pid)
        fast_regions = _regions(pid)
        for start, size in fast_regions:
            matched = _scan_region(
                reader,
                start,
                size,
                needle=needle,
                field_offset=field_offset,
                module_base=module_base,
                module_end=module_end,
                client_random=client_random,
                stop=stop,
                found=found,
                on_valid_candidate=lambda addr, start=start, size=size: _remember_validated_address(
                    pid,
                    start_ticks,
                    addr,
                    start,
                    size,
                    module_base,
                    module_end,
                ),
            )
            if matched:
                return found

        if client_random is not None and (stop is None or not stop.is_set()):
            # smaps Rss is an aggregate, not the offset of the last resident
            # page. A validated SSL connection can be beyond the first Rss
            # bytes; scan those tails only after the fast search misses
            fast_lengths = dict(fast_regions)
            for start, full_size in _targeted_fallback_regions(pid):
                already_scanned = min(fast_lengths.get(start, 0), full_size)
                if already_scanned >= full_size:
                    continue
                # Include candidates whose 32-byte client random straddles
                # the boundary between the fast prefix and full-VMA fallback
                resume = max(0, already_scanned - _SCAN_OVERLAP)
                if _scan_region(
                    reader,
                    start + resume,
                    full_size - resume,
                    needle=needle,
                    field_offset=field_offset,
                    module_base=module_base,
                    module_end=module_end,
                    client_random=client_random,
                    stop=stop,
                    found=found,
                    on_valid_candidate=lambda addr, start=start, size=full_size: (
                        _remember_validated_address(
                            pid,
                            start_ticks,
                            addr,
                            start,
                            size,
                            module_base,
                            module_end,
                        )
                    ),
                ):
                    return found
    finally:
        reader.close()

    return found
