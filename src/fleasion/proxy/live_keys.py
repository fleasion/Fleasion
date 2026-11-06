"""Read-only TLS 1.2/1.3 secret recovery for the Roblox player process (Windows).

All reads go through NtReadVirtualMemory. No writes, no injection.
The TLS 1.3 layout is validated against Roblox's OpenSSL 3.5.0 build;
TLS 1.2 session-pointer recovery still requires live Windows verification.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hmac
import struct
import sys
import threading
from typing import TYPE_CHECKING, Any, cast

from .tls12_session import WINDOWS_OPENSSL_350, validate_tls12_session
from .tls_records import expand_label

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

type ConnectionSecrets = dict[str, bytes]
type SecretMap = dict[str, ConnectionSecrets]

STRUCT_SIZE = 0x900
TLS12_STRUCT_SIZE = WINDOWS_OPENSSL_350.minimum_size
TLS12 = 0x303
TLS13 = 0x304
TLS12_MASTER_LENGTH = 48
TLS12_SESSION_SECRET_OFFSET = 80
TLS12_SESSION_PREFIX_SIZE = TLS12_SESSION_SECRET_OFFSET + 48
# OpenSSL 3.5.0 SSL_CONNECTION.session lies near 0x900 on x64; probe
# pointer-aligned offsets because optional build features vary by platform
TLS12_SESSION_POINTER_OFFSETS = WINDOWS_OPENSSL_350.session_offsets
MIN_DISTINCT_BYTES = 20
SHA256_SIZE = 32
CHUNK_SIZE = 4 * 1024 * 1024
_NEARBY_SCAN_SIZE = 256 * 1024
_MAX_HINTED_PIDS = 8
_MAX_HINTED_LOCATIONS = 8
_scan_hints: dict[int, list[tuple[int, int, int]]] = {}
# Store validated structure addresses, not TLS secrets or raw memory
_scan_candidate_hints: dict[int, list[tuple[int, int, int]]] = {}
_scan_hints_lock = threading.Lock()


def _prioritize_regions(pid: int, regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
    with _scan_hints_lock:
        hints = tuple(_scan_hints.get(pid, ()))
    live = set(regions)
    prioritized = list(
        dict.fromkeys((start, size) for start, size, _ in hints if (start, size) in live)
    )
    seen = set(prioritized)
    return prioritized + [region for region in regions if region not in seen]


def _prioritize_offsets(pid: int, region: tuple[int, int]) -> Iterator[int]:
    _start, size = region
    with _scan_hints_lock:
        hints = tuple(_scan_hints.get(pid, ()))
    prioritized = list(
        dict.fromkeys(
            offset
            for hint_start, hint_size, offset in hints
            if (hint_start, hint_size) == region and 0 <= offset < size and offset % CHUNK_SIZE == 0
        )
    )
    yield from prioritized
    seen = set(prioritized)
    yield from (offset for offset in range(0, size, CHUNK_SIZE) if offset not in seen)


def _remember_scan_location(pid: int, region: tuple[int, int], offset: int) -> None:
    location = (*region, offset)
    with _scan_hints_lock:
        previous = _scan_hints.pop(pid, ())
        remembered = [location, *(entry for entry in previous if entry != location)]
        _scan_hints[pid] = remembered[:_MAX_HINTED_LOCATIONS]
        if len(_scan_hints) > _MAX_HINTED_PIDS:
            _scan_hints.pop(next(iter(_scan_hints)))


def _remember_candidate_address(pid: int, region: tuple[int, int], base: int) -> None:
    """Remember a verified connection's neighborhood for the next TLS handshake."""
    location = (*region, base)
    with _scan_hints_lock:
        old = _scan_candidate_hints.pop(pid, ())
        _scan_candidate_hints[pid] = [location, *(item for item in old if item != location)][
            :_MAX_HINTED_LOCATIONS
        ]
        if len(_scan_candidate_hints) > _MAX_HINTED_PIDS:
            _scan_candidate_hints.pop(next(iter(_scan_candidate_hints)))


def _nearby_candidate_ranges(
    pid: int, regions: list[tuple[int, int]]
) -> Iterator[tuple[int, int, tuple[int, int]]]:
    """Return small bounds-safe windows around previously validated connections.

    Only targeted client-random searches use these hints. A miss always falls
    back to the complete region scan, so moved OpenSSL allocations still work.
    """
    with _scan_hints_lock:
        hints = tuple(_scan_candidate_hints.get(pid, ()))
    live = set(regions)
    seen: set[tuple[int, int]] = set()
    for region_start, region_size, base in hints:
        if (region_start, region_size) not in live:
            continue
        region_end = region_start + region_size
        if not region_start <= base < region_end:
            continue
        half = _NEARBY_SCAN_SIZE // 2
        window_start = max(region_start, base - half)
        window_size = min(_NEARBY_SCAN_SIZE, region_end - window_start)
        window = (window_start, window_size)
        if window_size and window not in seen:
            seen.add(window)
            yield window_start, window_size, (region_start, region_size)


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE = {0x02, 0x04, 0x20, 0x40, 0x80}

_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = -1


class MBI(ctypes.Structure):
    _fields_ = [
        ('BaseAddress', ctypes.c_ulonglong),
        ('AllocationBase', ctypes.c_ulonglong),
        ('AllocationProtect', wt.DWORD),
        ('__a1', wt.DWORD),
        ('RegionSize', ctypes.c_ulonglong),
        ('State', wt.DWORD),
        ('Protect', wt.DWORD),
        ('Type', wt.DWORD),
        ('__a2', wt.DWORD),
    ]


class MODULEINFO(ctypes.Structure):
    _fields_ = [
        ('lpBaseOfDll', ctypes.c_void_p),
        ('SizeOfImage', wt.DWORD),
        ('EntryPoint', ctypes.c_void_p),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wt.DWORD),
        ('cntUsage', wt.DWORD),
        ('th32ProcessID', wt.DWORD),
        ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wt.DWORD),
        ('cntThreads', wt.DWORD),
        ('th32ParentProcessID', wt.DWORD),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', wt.DWORD),
        ('szExeFile', ctypes.c_wchar * 260),
    ]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):  # ruff: ignore[invalid-class-name]
    _fields_ = [
        ('cb', wt.DWORD),
        ('PageFaultCount', wt.DWORD),
        ('PeakWorkingSetSize', ctypes.c_size_t),
        ('WorkingSetSize', ctypes.c_size_t),
        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
        ('PagefileUsage', ctypes.c_size_t),
        ('PeakPagefileUsage', ctypes.c_size_t),
    ]


class _Win32Bindings:
    """Encapsulates Windows API handles without top-level module load failure."""

    available: bool = False
    OpenProcess: Callable[[int, int, int], int]
    CloseHandle: Callable[[int], int]
    VirtualQueryEx: Callable[[int, Any, Any, int], int]
    NtReadVirtualMemory: Callable[[int, Any, Any, int, Any], int]
    EnumProcessModulesEx: Callable[[int, Any, int, Any, int], int]
    GetModuleInformation: Callable[[int, int, Any, int], int]
    CreateToolhelp32Snapshot: Callable[[int, int], int]
    Process32FirstW: Callable[[int, Any], int]
    Process32NextW: Callable[[int, Any], int]
    GetProcessMemoryInfo: Callable[[int, Any, int], int]

    def __init__(self) -> None:
        if sys.platform != 'win32':
            return

        windll_factory = getattr(ctypes, 'WinDLL', ctypes.CDLL)
        kernel32: Any = windll_factory('kernel32', use_last_error=True)
        ntdll: Any = windll_factory('ntdll')
        psapi: Any = windll_factory('psapi')

        kernel32.CloseHandle.restype = wt.BOOL
        kernel32.CloseHandle.argtypes = [wt.HANDLE]

        kernel32.OpenProcess.restype = wt.HANDLE
        kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]

        kernel32.VirtualQueryEx.restype = ctypes.c_size_t
        kernel32.VirtualQueryEx.argtypes = [
            wt.HANDLE,
            ctypes.c_void_p,
            ctypes.POINTER(MBI),
            ctypes.c_size_t,
        ]

        ntdll.NtReadVirtualMemory.restype = ctypes.c_long
        ntdll.NtReadVirtualMemory.argtypes = [
            wt.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]

        psapi.EnumProcessModulesEx.argtypes = [
            wt.HANDLE,
            ctypes.POINTER(wt.HMODULE),
            wt.DWORD,
            ctypes.POINTER(wt.DWORD),
            wt.DWORD,
        ]

        psapi.GetModuleInformation.argtypes = [
            wt.HANDLE,
            wt.HMODULE,
            ctypes.POINTER(MODULEINFO),
            wt.DWORD,
        ]

        kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]

        kernel32.Process32FirstW.restype = wt.BOOL
        kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]

        kernel32.Process32NextW.restype = wt.BOOL
        kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]

        psapi.GetProcessMemoryInfo.restype = wt.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wt.HANDLE,
            ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
            wt.DWORD,
        ]

        self.CloseHandle = cast('Callable[[int], int]', kernel32.CloseHandle)
        self.OpenProcess = cast('Callable[[int, int, int], int]', kernel32.OpenProcess)
        self.VirtualQueryEx = cast('Callable[[int, Any, Any, int], int]', kernel32.VirtualQueryEx)
        self.NtReadVirtualMemory = cast(
            'Callable[[int, Any, Any, int, Any], int]', ntdll.NtReadVirtualMemory
        )
        self.EnumProcessModulesEx = cast(
            'Callable[[int, Any, int, Any, int], int]', psapi.EnumProcessModulesEx
        )
        self.GetModuleInformation = cast(
            'Callable[[int, int, Any, int], int]', psapi.GetModuleInformation
        )
        self.CreateToolhelp32Snapshot = cast(
            'Callable[[int, int], int]', kernel32.CreateToolhelp32Snapshot
        )
        self.Process32FirstW = cast('Callable[[int, Any], int]', kernel32.Process32FirstW)
        self.Process32NextW = cast('Callable[[int, Any], int]', kernel32.Process32NextW)
        self.GetProcessMemoryInfo = cast(
            'Callable[[int, Any, int], int]', psapi.GetProcessMemoryInfo
        )
        self.available = True


_win32 = _Win32Bindings()


def _read(handle: int, addr: int, size: int) -> bytes:
    if not _win32.available:
        return b''
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t()
    if _win32.NtReadVirtualMemory(handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)) != 0:
        return b''
    return buf.raw[: got.value]


def find_roblox_pid() -> int | None:
    """Find the Roblox player process PID on Windows without spawning processes.

    Returns
    -------
    int | None
        Process ID of the primary RobloxPlayerBeta instance, or None if not found.
    """
    if not _win32.available:
        return None

    snap = _win32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snap == _INVALID_HANDLE_VALUE or not snap:
        return None

    target = 'robloxplayerbeta.exe'
    matching_pids: list[int] = []

    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        if _win32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                if entry.szExeFile.casefold() == target:
                    matching_pids.append(int(entry.th32ProcessID))
                if not _win32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        _win32.CloseHandle(snap)

    if not matching_pids:
        return None
    if len(matching_pids) == 1:
        return matching_pids[0]

    # Pick the instance with the largest working set size
    best_pid = matching_pids[0]
    best_working_set = 0

    for pid in matching_pids:
        handle = _win32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, 0, pid)
        if not handle:
            continue
        try:
            mem = _PROCESS_MEMORY_COUNTERS()
            mem.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
            if (
                _win32.GetProcessMemoryInfo(handle, ctypes.byref(mem), ctypes.sizeof(mem))
                and mem.WorkingSetSize > best_working_set
            ):
                best_working_set = mem.WorkingSetSize
                best_pid = pid
        finally:
            _win32.CloseHandle(handle)

    return best_pid


def validate_openssl_struct(raw: bytes) -> tuple[str, dict[str, bytes]] | None:
    """Validate an OpenSSL 3.5.0 SSL_CONNECTION struct in memory.

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
    if struct.unpack_from('<I', raw, 0x78)[0] != 0:
        return None
    cr, sr = raw[0x184:0x1A4], raw[0x164:0x184]
    if len(set(cr)) < MIN_DISTINCT_BYTES or len(set(sr)) < MIN_DISTINCT_BYTES:
        return None
    cb, sb = raw[0x6F4:0x734], raw[0x734:0x774]
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
    hs = raw[0x5B4 : 0x5B4 + n]
    master = raw[0x5F4 : 0x5F4 + n]
    hh = raw[0x734 : 0x734 + n]
    fh = raw[0x6F4 : 0x6F4 + n]
    ch = expand_label(hs, b'c hs traffic', n, hn, hh)
    sh = expand_label(hs, b's hs traffic', n, hn, hh)
    client = raw[0x774 : 0x774 + n]
    server = raw[0x7B4 : 0x7B4 + n]
    exporter = raw[0x7F4 : 0x7F4 + n]
    ok = (
        hmac.compare_digest(client, expand_label(master, b'c ap traffic', n, hn, fh))
        and hmac.compare_digest(server, expand_label(master, b's ap traffic', n, hn, fh))
        and hmac.compare_digest(raw[0x674 : 0x674 + n], expand_label(ch, b'finished', n, hn, b''))
        and hmac.compare_digest(raw[0x6B4 : 0x6B4 + n], expand_label(sh, b'finished', n, hn, b''))
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


def validate_tls12_openssl_struct(
    raw: bytes,
    read_memory: Callable[[int, int], bytes],
) -> tuple[str, dict[str, bytes]] | None:
    """Validate a Windows OpenSSL 3.5 TLS 1.2 session candidate."""
    return validate_tls12_session(raw, read_memory, layout=WINDOWS_OPENSSL_350)


_validate = validate_openssl_struct


def _module_bounds(handle: int) -> tuple[int, int]:
    if not _win32.available:
        return 0, 0
    modules = (wt.HMODULE * 1024)()
    needed = wt.DWORD()
    if _win32.EnumProcessModulesEx(
        handle, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3
    ):
        info = MODULEINFO()
        if _win32.GetModuleInformation(handle, modules[0], ctypes.byref(info), ctypes.sizeof(info)):
            base = int(info.lpBaseOfDll or 0)
            return base, base + int(info.SizeOfImage)
    return 0, 0


def _regions(handle: int) -> Iterator[tuple[int, int]]:
    if not _win32.available:
        return
    info = MBI()
    address = 0
    while _win32.VirtualQueryEx(
        handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)
    ):
        next_address = info.BaseAddress + info.RegionSize
        if next_address <= address:
            break
        address = next_address
        if (
            info.State == MEM_COMMIT
            and info.Type == MEM_PRIVATE
            and not info.Protect & (PAGE_GUARD | PAGE_NOACCESS)
            and info.Protect & 0xFF in READABLE
        ):
            yield int(info.BaseAddress), int(info.RegionSize)


def _candidates(
    handle: int,
    pid: int,
    stop: threading.Event | None = None,
    client_random: bytes | None = None,
) -> Iterator[tuple[int, tuple[int, int], int]]:
    needle = client_random or b'\x04\x03\x00\x00'
    field_offset = 0x184 if client_random is not None else 0x48
    read_buf = ctypes.create_string_buffer(CHUNK_SIZE + len(needle))
    got = ctypes.c_size_t()
    regions = list(_regions(handle))

    if client_random is not None:
        for start, size, region in _nearby_candidate_ranges(pid, regions):
            if stop is not None and stop.is_set():
                return
            status = _win32.NtReadVirtualMemory(
                handle, ctypes.c_void_p(start), read_buf, size, ctypes.byref(got)
            )
            if status != 0:
                continue
            data = read_buf.raw[: got.value]
            position = data.find(needle)
            while position >= 0:
                candidate = start + position - field_offset
                if candidate >= start:
                    offset = ((candidate - region[0]) // CHUNK_SIZE) * CHUNK_SIZE
                    yield candidate, region, offset
                position = data.find(needle, position + 1)

    for start, size in _prioritize_regions(pid, regions):
        region = (start, size)
        for offset in _prioritize_offsets(pid, region):
            if stop is not None and stop.is_set():
                return
            to_read = min(CHUNK_SIZE + len(needle) - 1, size - offset)
            if (
                _win32.NtReadVirtualMemory(
                    handle,
                    ctypes.c_void_p(start + offset),
                    read_buf,
                    to_read,
                    ctypes.byref(got),
                )
                != 0
            ):
                continue
            data = read_buf.raw[: got.value]
            position = data.find(needle)
            while position >= 0:
                candidate = start + offset + position - field_offset
                if candidate >= start:
                    yield candidate, region, offset
                position = data.find(needle, position + 1)


def _scan_handle(
    handle: int,
    pid: int,
    module_base: int,
    module_end: int,
    *,
    stop: threading.Event | None,
    wanted: bytes | None,
    client_random: str | None,
) -> SecretMap:
    found: SecretMap = {}
    for base, region, offset in _candidates(handle, pid, stop, wanted):
        raw = _read(handle, base, STRUCT_SIZE)
        if len(raw) < STRUCT_SIZE:
            continue
        if struct.unpack_from('<I', raw, 0x48)[0] == TLS12:
            raw = _read(handle, base, TLS12_STRUCT_SIZE)
            if len(raw) < TLS12_STRUCT_SIZE:
                continue
        method = struct.unpack_from('<Q', raw, 0x18)[0]
        if module_base <= method < module_end:
            validated = validate_openssl_struct(raw)
            if validated is None and len(raw) >= TLS12_STRUCT_SIZE:
                validated = validate_tls12_openssl_struct(
                    raw, lambda addr, length: _read(handle, addr, length)
                )
            if validated is not None:
                found[validated[0]] = validated[1]
                _remember_candidate_address(pid, region, base)
                _remember_scan_location(pid, region, offset)
                if client_random is not None and validated[0] == client_random:
                    break
    return found


def scan(
    pid: int,
    *,
    stop: threading.Event | None = None,
    client_random: str | None = None,
) -> SecretMap:
    """Scan process memory for TLS 1.3 secrets or a TLS 1.2 master secret.

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
        Validated TLS 1.3 traffic secrets or TLS 1.2 session master secrets.
    """
    wanted: bytes | None = None
    if client_random is not None:
        try:
            wanted = bytes.fromhex(client_random) if len(client_random) == 64 else None
        except ValueError:
            wanted = None
        if wanted is None:
            return {}

    if not _win32.available:
        return {}

    handle: int = _win32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, 0, pid)
    if not handle:
        return {}

    try:
        module_base, module_end = _module_bounds(handle)
        if not module_base:
            return {}
        return _scan_handle(
            handle,
            pid,
            module_base,
            module_end,
            stop=stop,
            wanted=wanted,
            client_random=client_random,
        )
    finally:
        _win32.CloseHandle(handle)
