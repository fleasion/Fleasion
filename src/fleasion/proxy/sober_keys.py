"""Read-only TLS 1.2/1.3 secret recovery for Sober Android runtime on Linux.

Supports Sober (org.vinegarhq.Sober) running containerized x86_64 Android Roblox.
Reads process memory via process_vm_readv or /proc/<pid>/mem without code injection.
Layout validated against OpenSSL 3.5.0 in Android NDK x86_64 (SysV LP64).
"""

from __future__ import annotations

import ctypes
import logging
import struct
import sys
from ctypes import POINTER, Structure, byref, c_int, c_size_t, c_ssize_t, c_ulong, c_void_p, cast
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .rosemary_keys import STRUCT_SIZE, validate_openssl_struct
from .tls12_session import LP64_OPENSSL_350, TLS12_VERSION, validate_tls12_session

if TYPE_CHECKING:
    import threading

log = logging.getLogger(__name__)

type ConnectionSecrets = dict[str, bytes]
type SecretMap = dict[str, ConnectionSecrets]

CHUNK_SIZE = 1024 * 1024
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
        self._buf = ctypes.create_string_buffer(CHUNK_SIZE)
        buf_addr = cast(self._buf, c_void_p).value
        self._local_iov = _Iovec(buf_addr, CHUNK_SIZE)
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

        to_read = min(size, CHUNK_SIZE)
        self._remote_iov.iov_base = addr
        self._remote_iov.iov_len = to_read
        self._local_iov.iov_len = to_read
        res = self._pvr(self.pid, byref(self._local_iov), 1, byref(self._remote_iov), 1, 0)
        if res <= 0:
            return b''
        return self._buf.raw[:res]

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


def _parse_smaps_regions(smaps_path: Path) -> list[tuple[int, int]]:
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
            if rss_kb > 0:
                scan_len = min(current_vsize, (rss_kb + 256) * 1024)
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
) -> bool:
    for offset in range(0, size, CHUNK_SIZE):
        if stop is not None and stop.is_set():
            return True
        chunk_len = min(CHUNK_SIZE + len(needle) - 1, size - offset)
        buf = reader.read(start + offset, chunk_len)
        if not buf:
            continue
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

    module_base, module_end = _module_bounds(pid)

    reader = _MemoryReader(pid)
    try:
        for start, size in _regions(pid):
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
            )
            if matched:
                return found
    finally:
        reader.close()

    return found
