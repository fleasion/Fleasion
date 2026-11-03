"""Windows TLS scanner reuses validated memory locations without suppressing fallback."""

from __future__ import annotations

import ctypes
import struct
import threading
from collections.abc import Iterator

import pytest

from fleasion.proxy import live_keys
from fleasion.proxy.tls_records import expand_label

REGION_SIZE = 4 * 4096
REGION_BASES = (0x100000, 0x200000, 0x300000)


class FakeWindowsMemory:
    """In-memory Windows process with read-only memory APIs."""

    available = True

    def __init__(self) -> None:
        self.regions = {start: bytearray(REGION_SIZE) for start in REGION_BASES}
        self.read_addresses: list[int] = []
        self.closed_handles: list[int] = []

    def OpenProcess(self, _access: int, _inherit: int, _pid: int) -> int:
        return 55

    def CloseHandle(self, handle: int) -> int:
        self.closed_handles.append(handle)
        return 1

    def NtReadVirtualMemory(
        self,
        _handle: int,
        address: ctypes.c_void_p,
        buffer: ctypes.Array[ctypes.c_char],
        size: int,
        bytes_read: ctypes.c_void_p,
    ) -> int:
        location = int(address.value or 0)
        self.read_addresses.append(location)
        for start, contents in self.regions.items():
            offset = location - start
            if 0 <= offset < len(contents):
                data = bytes(contents[offset : offset + size])
                ctypes.memmove(buffer, data, len(data))
                ctypes.cast(bytes_read, ctypes.POINTER(ctypes.c_size_t)).contents.value = len(data)
                return 0
        return 1


def _connection(client_random: bytes) -> bytes:
    """Construct a cryptographically consistent OpenSSL connection fixture."""
    data = bytearray(live_keys.STRUCT_SIZE)
    struct.pack_into('<Q', data, 0x18, 0x500000)
    struct.pack_into('<I', data, 0x48, live_keys.TLS13)
    data[0x184:0x1A4] = client_random
    data[0x164:0x184] = bytes(range(33, 65))
    n = 32
    finished_hash = bytes(range(10, 42))
    handshake_hash = bytes(range(42, 74))
    data[0x6F4 : 0x6F4 + n] = finished_hash
    data[0x734 : 0x734 + n] = handshake_hash
    handshake_secret = bytes(range(100, 132))
    master_secret = bytes(range(132, 164))
    data[0x5B4 : 0x5B4 + n] = handshake_secret
    data[0x5F4 : 0x5F4 + n] = master_secret
    client_hs = expand_label(handshake_secret, b'c hs traffic', n, 'sha256', handshake_hash)
    server_hs = expand_label(handshake_secret, b's hs traffic', n, 'sha256', handshake_hash)
    data[0x774 : 0x774 + n] = expand_label(
        master_secret, b'c ap traffic', n, 'sha256', finished_hash
    )
    data[0x7B4 : 0x7B4 + n] = expand_label(
        master_secret, b's ap traffic', n, 'sha256', finished_hash
    )
    data[0x7F4 : 0x7F4 + n] = expand_label(master_secret, b'exp master', n, 'sha256', finished_hash)
    data[0x674 : 0x674 + n] = expand_label(client_hs, b'finished', n, 'sha256', b'')
    data[0x6B4 : 0x6B4 + n] = expand_label(server_hs, b'finished', n, 'sha256', b'')
    return bytes(data)


def _install_connection(memory: FakeWindowsMemory, start: int, offset: int, cr: bytes) -> None:
    contents = memory.regions[start]
    contents[offset : offset + live_keys.STRUCT_SIZE] = _connection(cr)


@pytest.fixture(autouse=True)
def clear_scan_hints() -> Iterator[None]:
    with live_keys._scan_hints_lock:  # pyright: ignore[reportPrivateUsage]
        live_keys._scan_hints.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    with live_keys._scan_hints_lock:  # pyright: ignore[reportPrivateUsage]
        live_keys._scan_hints.clear()  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def windows_memory(monkeypatch: pytest.MonkeyPatch) -> FakeWindowsMemory:
    memory = FakeWindowsMemory()
    monkeypatch.setattr(live_keys, 'CHUNK_SIZE', 4096)
    monkeypatch.setattr(live_keys, '_win32', memory)

    def module_bounds(_handle: int) -> tuple[int, int]:
        return (0x400000, 0x600000)

    def regions(_handle: int) -> Iterator[tuple[int, int]]:
        yield from ((start, len(contents)) for start, contents in memory.regions.items())

    monkeypatch.setattr(live_keys, '_module_bounds', module_bounds)
    monkeypatch.setattr(live_keys, '_regions', regions)
    return memory


def test_recent_validated_location_is_scanned_first(windows_memory: FakeWindowsMemory) -> None:
    pid = 101
    hot = REGION_BASES[-1]
    offset = 2 * 4096
    first = bytes(range(1, 33))
    second = bytes(range(65, 97))
    _install_connection(windows_memory, hot, offset, first)
    assert first.hex() in live_keys.scan(pid, client_random=first.hex())
    assert windows_memory.read_addresses[0] == REGION_BASES[0]
    assert windows_memory.closed_handles == [55]

    windows_memory.regions[hot] = bytearray(REGION_SIZE)
    _install_connection(windows_memory, hot, offset, second)
    windows_memory.read_addresses.clear()
    assert second.hex() in live_keys.scan(pid, client_random=second.hex())
    assert windows_memory.read_addresses[0] == hot + offset
    assert windows_memory.closed_handles == [55, 55]


def test_stale_hint_falls_back_and_learns_new_location(
    windows_memory: FakeWindowsMemory,
) -> None:
    pid = 102
    first = bytes(range(1, 33))
    second = bytes(range(65, 97))
    third = bytes(range(97, 129))
    hot = REGION_BASES[-1]
    cold = REGION_BASES[0]
    _install_connection(windows_memory, hot, 2 * 4096, first)
    assert first.hex() in live_keys.scan(pid, client_random=first.hex())

    _install_connection(windows_memory, cold, 3 * 4096, second)
    windows_memory.read_addresses.clear()
    assert second.hex() in live_keys.scan(pid, client_random=second.hex())
    assert windows_memory.read_addresses[0] == hot + 2 * 4096
    assert cold + 3 * 4096 in windows_memory.read_addresses

    windows_memory.regions[cold] = bytearray(REGION_SIZE)
    _install_connection(windows_memory, cold, 3 * 4096, third)
    windows_memory.read_addresses.clear()
    assert third.hex() in live_keys.scan(pid, client_random=third.hex())
    assert windows_memory.read_addresses[0] == cold + 3 * 4096


def test_disappeared_region_is_ignored_without_losing_fallback(
    windows_memory: FakeWindowsMemory,
) -> None:
    pid = 103
    first = bytes(range(1, 33))
    second = bytes(range(65, 97))
    _install_connection(windows_memory, REGION_BASES[-1], 4096, first)
    assert first.hex() in live_keys.scan(pid, client_random=first.hex())

    del windows_memory.regions[REGION_BASES[-1]]
    _install_connection(windows_memory, REGION_BASES[1], 4096, second)
    windows_memory.read_addresses.clear()
    assert second.hex() in live_keys.scan(pid, client_random=second.hex())
    assert windows_memory.read_addresses[0] == REGION_BASES[0]


def test_scan_location_hints_are_pid_scoped_and_bounded(
    windows_memory: FakeWindowsMemory,
) -> None:
    first = bytes(range(1, 33))
    second = bytes(range(65, 97))
    _install_connection(windows_memory, REGION_BASES[-1], 4096, first)
    assert first.hex() in live_keys.scan(104, client_random=first.hex())

    _install_connection(windows_memory, REGION_BASES[0], 0, second)
    windows_memory.read_addresses.clear()
    assert second.hex() in live_keys.scan(105, client_random=second.hex())
    assert windows_memory.read_addresses[0] == REGION_BASES[0]

    region = (REGION_BASES[0], REGION_SIZE)
    for i in range(16):
        live_keys._remember_scan_location(104, region, i * 4096)  # pyright: ignore[reportPrivateUsage]
    assert len(live_keys._scan_hints[104]) == 8  # pyright: ignore[reportPrivateUsage]
    for pid in range(200, 220):
        live_keys._remember_scan_location(pid, region, 0)  # pyright: ignore[reportPrivateUsage]
    assert len(live_keys._scan_hints) == 8  # pyright: ignore[reportPrivateUsage]
    assert 104 not in live_keys._scan_hints  # pyright: ignore[reportPrivateUsage]


def test_invalid_connection_is_not_remembered(windows_memory: FakeWindowsMemory) -> None:
    client_random = bytes(range(1, 33))
    _install_connection(windows_memory, REGION_BASES[-1], 4096, client_random)
    struct.pack_into('<Q', windows_memory.regions[REGION_BASES[-1]], 4096 + 0x18, 0x900000)
    assert not live_keys.scan(106, client_random=client_random.hex())
    assert 106 not in live_keys._scan_hints  # pyright: ignore[reportPrivateUsage]


def test_cancelled_scans_do_not_read_process_pages(windows_memory: FakeWindowsMemory) -> None:
    stopped = threading.Event()
    stopped.set()
    assert not live_keys.scan(107, stop=stopped, client_random='ab' * 32)
    assert windows_memory.read_addresses == []
