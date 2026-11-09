"""Sober TLS recovery covers non-contiguous resident memory and chunk boundaries."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from fleasion.proxy import sober_keys

class FakeReader:
    """Model a remote readable mapping without touching another process."""

    def __init__(self, start: int, data: bytes) -> None:
        self.start = start
        self.data = data
        self.reads: list[tuple[int, int]] = []

    def read(self, addr: int, size: int) -> bytes:
        self.reads.append((addr, size))
        if not self.start <= addr < self.start + len(self.data):
            return b''
        offset = addr - self.start
        return self.data[offset : offset + size]

    def close(self) -> None:
        return


def test_smaps_prefix_does_not_define_last_resident_offset(tmp_path: Path) -> None:
    """The bounded fast scan and full targeted fallback refer to the same VMA."""
    region = 0x100000
    size = 4 * 1024 * 1024
    smaps = tmp_path / 'smaps'
    smaps.write_text(
        f'{region:x}-{region + size:x} rw-p 00000000 00:00 0\n'
        'Size:               4096 kB\n'
        'Rss:                1280 kB\n'
    )
    fast = sober_keys._parse_smaps_regions(smaps)  # pyright: ignore[reportPrivateUsage]
    full = sober_keys._parse_smaps_regions(  # pyright: ignore[reportPrivateUsage]
        smaps, full_vmas=True, min_rss_kb=1024
    )
    assert fast == [(region, (1280 + 256) * 1024)]
    assert full == [(region, size)]


def test_targeted_scan_falls_back_to_later_resident_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid connection after the RSS-sized prefix can still be recovered."""
    start = 0x100000
    size = 4 * 1024 * 1024
    prefix = 2 * 1024 * 1024
    needle = bytes(range(1, 33))
    candidate = start + 3 * 1024 * 1024
    content = bytearray(size)
    content[candidate - start + 0x188 : candidate - start + 0x188 + 32] = needle
    reader = FakeReader(start, bytes(content))

    def fast_regions(_pid: int) -> list[tuple[int, int]]:
        return [(start, prefix)]

    def fallback_regions(_pid: int) -> list[tuple[int, int]]:
        return [(start, size)]

    def bounds(_pid: int) -> tuple[int, int]:
        return 0, 0

    def make_reader(_pid: int) -> FakeReader:
        return reader

    monkeypatch.setattr(sober_keys, '_regions', fast_regions)
    monkeypatch.setattr(sober_keys, '_targeted_fallback_regions', fallback_regions)
    monkeypatch.setattr(sober_keys, '_module_bounds', bounds)
    monkeypatch.setattr(sober_keys, '_MemoryReader', make_reader)

    def validate(
        _reader: object, _buf: bytes, *, start: int, offset: int, position: int,
        field_offset: int, module_base: int, module_end: int,
    ) -> tuple[str, dict[str, bytes]] | None:
        del module_base, module_end
        if start + offset + position - field_offset == candidate:
            return needle.hex(), {'CLIENT_TRAFFIC_SECRET_0': b'x' * 32}
        return None

    monkeypatch.setattr(sober_keys, '_scan_candidate', validate)
    result = sober_keys.scan(12345, client_random=needle.hex())
    assert needle.hex() in result
    assert any(addr > start + prefix for addr, _size in reader.reads)


def test_targeted_scan_recovers_boundary_straddling_random(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client random split across the prefix/fallback boundary must be found."""
    start = 0x100000
    size = 4 * 1024 * 1024
    prefix = 2 * 1024 * 1024
    needle = bytes(range(33, 65))
    candidate = start + prefix - 0x188 - 15
    content = bytearray(size)
    content[candidate - start + 0x188 : candidate - start + 0x188 + 32] = needle
    reader = FakeReader(start, bytes(content))

    def fast_regions(_pid: int) -> list[tuple[int, int]]:
        return [(start, prefix)]

    def fallback_regions(_pid: int) -> list[tuple[int, int]]:
        return [(start, size)]

    def module_bounds(_pid: int) -> tuple[int, int]:
        return 0, 0

    monkeypatch.setattr(sober_keys, '_regions', fast_regions)
    monkeypatch.setattr(sober_keys, '_targeted_fallback_regions', fallback_regions)
    monkeypatch.setattr(sober_keys, '_module_bounds', module_bounds)
    def make_reader(_pid: int) -> FakeReader:
        return reader

    monkeypatch.setattr(sober_keys, '_MemoryReader', make_reader)

    def validate(
        _reader: object, _buf: bytes, *, start: int, offset: int, position: int,
        field_offset: int, module_base: int, module_end: int,
    ) -> tuple[str, dict[str, bytes]] | None:
        del module_base, module_end
        if start + offset + position - field_offset == candidate:
            return needle.hex(), {'CLIENT_TRAFFIC_SECRET_0': b'y' * 32}
        return None

    monkeypatch.setattr(sober_keys, '_scan_candidate', validate)
    assert needle.hex() in sober_keys.scan(12345, client_random=needle.hex())


def test_process_vm_readv_preserves_chunk_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live Sober fallback must not truncate requested overlap bytes."""
    reader = sober_keys._MemoryReader(0)  # pyright: ignore[reportPrivateUsage]
    data = bytes(range(32))
    read_sizes: list[int] = []

    def fake_readv(
        _pid: int, local: Any, _local_count: int,
        remote: Any, _remote_count: int, _flags: int,
    ) -> int:
        local_iov = ctypes.cast(local, ctypes.POINTER(sober_keys._Iovec)).contents  # pyright: ignore[reportPrivateUsage]
        remote_iov = ctypes.cast(remote, ctypes.POINTER(sober_keys._Iovec)).contents  # pyright: ignore[reportPrivateUsage]
        requested = int(remote_iov.iov_len)
        read_sizes.append(requested)
        assert requested <= int(local_iov.iov_len)
        assert local_iov.iov_base is not None
        ctypes.memset(local_iov.iov_base, 0, requested)
        ctypes.memmove(local_iov.iov_base, data, len(data))
        return requested

    try:
        reader._pvr = fake_readv  # pyright: ignore[reportPrivateUsage]
        reader._mem_file = None  # pyright: ignore[reportPrivateUsage]
        payload = reader.read(0x100000, sober_keys.CHUNK_SIZE + 31)
        assert len(payload) == sober_keys.CHUNK_SIZE + 31
        assert payload[:32] == data
        assert read_sizes == [sober_keys.CHUNK_SIZE + 31]
    finally:
        reader.close()


def test_fallback_region_selection_is_budgeted_and_requires_real_rss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not turn sparse Android virtual reservations into unbounded scans."""
    root = tmp_path / 'proc' / '123'
    root.mkdir(parents=True)
    smaps = root / 'smaps'
    gb = 1024 * 1024 * 1024
    smaps.write_text(
        f'100000-{0x100000 + 2 * gb:x} rw-p 00000000 00:00 0\n'
        'Rss:                   4 kB\n'
        f'900000000-{0x900000000 + 3 * gb:x} rw-p 00000000 00:00 0\n'
        'Rss:                4096 kB\n'
        f'c00000000-{0xc00000000 + 7 * gb:x} rw-p 00000000 00:00 0\n'
        'Rss:                4096 kB\n'
    )
    real_path = Path

    def path(p: str | Path) -> Path:
        return smaps if str(p) == '/proc/123/smaps' else real_path(p)

    monkeypatch.setattr(sober_keys, 'Path', path)
    assert sober_keys._targeted_fallback_regions(123) == [  # pyright: ignore[reportPrivateUsage]
        (0x900000000, 3 * gb),
    ]


def test_validated_nearby_allocation_accelerates_next_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second TLS connection 8 KiB away avoids the large first-pass scan."""
    start = 0x100000
    size = 8 * 1024 * 1024
    first_address = start + 5 * 1024 * 1024
    next_address = first_address + 8192
    far_address = start + 7 * 1024 * 1024
    randoms = [bytes(range(i, i + 32)) for i in (1, 40, 80)]
    locations: dict[int, bytes] = dict(
        zip((first_address, next_address, far_address), randoms, strict=True)
    )
    content = bytearray(size)
    for address, cr in locations.items():
        content[address - start + 0x188 : address - start + 0x188 + 32] = cr
    reader = FakeReader(start, bytes(content))
    scan_original = sober_keys._scan_region  # pyright: ignore[reportPrivateUsage]
    calls: list[tuple[int, int]] = []
    metadata_reads: list[str] = []

    def record_scan(
        reader: FakeReader, region_start: int, region_size: int, *, needle: bytes,
        field_offset: int, module_base: int, module_end: int, client_random: str | None,
        stop: threading.Event | None, found: sober_keys.SecretMap,
        on_valid_candidate: Callable[[int], None] | None = None,
    ) -> bool:
        calls.append((region_start, region_size))
        return scan_original(
            cast('sober_keys._MemoryReader', reader), region_start, region_size, needle=needle,  # pyright: ignore[reportPrivateUsage]
            field_offset=field_offset, module_base=module_base, module_end=module_end,
            client_random=client_random, stop=stop, found=found,
            on_valid_candidate=on_valid_candidate,
        )

    def validate(
        _reader: object, _buf: bytes, *, start: int, offset: int, position: int,
        field_offset: int, module_base: int, module_end: int,
    ) -> tuple[str, dict[str, bytes]] | None:
        del module_base, module_end
        cr = locations.get(start + offset + position - field_offset)
        return (cr.hex(), {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}) if cr else None

    def mapped_regions(_pid: int) -> list[tuple[int, int]]:
        metadata_reads.append('regions')
        return [(start, size)]

    def make_reader(_pid: int) -> FakeReader:
        return reader

    def generation(_pid: int) -> int:
        return 100

    def no_bounds(_pid: int) -> tuple[int, int]:
        metadata_reads.append('bounds')
        return 0, 0

    def no_fallback(_pid: int) -> list[tuple[int, int]]:
        return []

    monkeypatch.setattr(sober_keys, '_MemoryReader', make_reader)
    monkeypatch.setattr(sober_keys, '_module_bounds', no_bounds)
    monkeypatch.setattr(sober_keys, '_regions', mapped_regions)
    monkeypatch.setattr(sober_keys, '_targeted_fallback_regions', no_fallback)
    monkeypatch.setattr(sober_keys, '_process_start_ticks', generation)
    monkeypatch.setattr(sober_keys, '_scan_candidate', validate)
    monkeypatch.setattr(sober_keys, '_scan_region', record_scan)
    with sober_keys._validated_addresses_lock:  # pyright: ignore[reportPrivateUsage]
        sober_keys._validated_addresses.clear()  # pyright: ignore[reportPrivateUsage]
    try:
        assert randoms[0].hex() in sober_keys.scan(123, client_random=randoms[0].hex())
        assert calls == [(start, size)]
        assert metadata_reads == ['bounds', 'regions']
        calls.clear()
        metadata_reads.clear()
        assert randoms[1].hex() in sober_keys.scan(123, client_random=randoms[1].hex())
        assert len(calls) == 1
        assert calls[0][1] == 512 * 1024
        assert not metadata_reads  # No /proc/<pid>/smaps or /proc/<pid>/maps on warm hit
        calls.clear()
        assert randoms[2].hex() in sober_keys.scan(123, client_random=randoms[2].hex())
        assert calls[-1] == (start, size)
        assert metadata_reads == ['bounds', 'regions']
        assert all(length < size for _, length in calls[:-1])

        # A recycled PID is not allowed to inherit its predecessor's addresses
        def changed_generation(_pid: int) -> int:
            return 101

        calls.clear()
        metadata_reads.clear()
        monkeypatch.setattr(sober_keys, '_process_start_ticks', changed_generation)
        assert randoms[1].hex() in sober_keys.scan(123, client_random=randoms[1].hex())
        assert calls == [(start, size)]
        assert metadata_reads == ['bounds', 'regions']
    finally:
        with sober_keys._validated_addresses_lock:  # pyright: ignore[reportPrivateUsage]
            sober_keys._validated_addresses.clear()  # pyright: ignore[reportPrivateUsage]


def test_validation_hints_bound_process_count_and_addresses() -> None:
    """Remember no secret contents, and never retain unbounded process history."""
    remember = sober_keys._remember_validated_address  # pyright: ignore[reportPrivateUsage]
    hints = sober_keys._validated_addresses  # pyright: ignore[reportPrivateUsage]
    lock = sober_keys._validated_addresses_lock  # pyright: ignore[reportPrivateUsage]
    with lock:
        hints.clear()
    try:
        for i in range(32):
            remember(4001, 10, 0x100000 + i * 4096, 0x100000, 1024 * 1024, 0, 0)
        assert len(hints[(4001, 10)]) == 12
        remember(4001, None, 0xABC000, 0x100000, 1024 * 1024, 0, 0)
        assert all(location.address != 0xABC000 for location in hints[(4001, 10)])
        for i in range(20):
            remember(5000 + i, 10, 0x200000, 0x200000, 1024 * 1024, 0, 0)
        assert len(hints) == 8
        assert (4001, 10) not in hints
    finally:
        with lock:
            hints.clear()


def test_sparse_small_android_vmas_join_targeted_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A single resident page can contain a TLS object in a small mapping."""
    smaps = tmp_path / 'smaps'
    small_start = 0x100000
    huge_start = 0x900000000
    small_size = 4 * 1024 * 1024
    huge_size = 2 * 1024 * 1024 * 1024
    smaps.write_text(
        f'{small_start:x}-{small_start + small_size:x} rw-p 00000000 00:00 0\n'
        'Rss:                   4 kB\n'
        f'{huge_start:x}-{huge_start + huge_size:x} rw-p 00000000 00:00 0\n'
        'Rss:                   4 kB\n',
    )
    real_path = Path

    def map_path(path: str | Path) -> Path:
        return smaps if str(path) == '/proc/111/smaps' else real_path(path)

    monkeypatch.setattr(sober_keys, 'Path', map_path)
    assert sober_keys._targeted_fallback_regions(111) == [  # pyright: ignore[reportPrivateUsage]
        (small_start, small_size),
    ]


@pytest.mark.parametrize('targeted', [True, False])
def test_unreadable_first_page_does_not_hide_sober_tls_structure(
    monkeypatch: pytest.MonkeyPatch, targeted: bool,
) -> None:
    """Sober has small rw-p VMAs whose first page is unreadable but later pages work."""
    start = 0x300000
    size = 384 * 1024
    cr = bytes(range(1, 33))
    candidate = start + 16 * 1024
    contents = bytearray(size)
    contents[candidate - start + (0x188 if targeted else 0x48):
             candidate - start + (0x188 if targeted else 0x48) + (32 if targeted else 4)] = (
        cr if targeted else b'\x04\x03\x00\x00'
    )
    reader = FakeReader(start, bytes(contents))
    original = reader.read
    probed_addresses: list[int] = []

    def read_after_unreadable_first_page(addr: int, length: int) -> bytes:
        probed_addresses.append(addr)
        if addr < start + 4096:
            return b''
        return original(addr, length)

    monkeypatch.setattr(reader, 'read', read_after_unreadable_first_page)
    field_offset = 0x188 if targeted else 0x48

    def fake_candidate(
        _reader: object, _buf: bytes, *, start: int, offset: int, position: int,
        field_offset: int, module_base: int, module_end: int,
    ) -> tuple[str, dict[str, bytes]] | None:
        del module_base, module_end
        if start + offset + position - field_offset == candidate:
            return cr.hex(), {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}
        return None

    monkeypatch.setattr(sober_keys, '_scan_candidate', fake_candidate)
    results: sober_keys.SecretMap = {}
    assert sober_keys._scan_region(  # pyright: ignore[reportPrivateUsage]
        cast('sober_keys._MemoryReader', reader),  # pyright: ignore[reportPrivateUsage]
        start, size, needle=cr if targeted else b'\x04\x03\x00\x00',
        field_offset=field_offset, module_base=0, module_end=0,
        client_random=cr.hex() if targeted else None, stop=None, found=results,
    ) is targeted
    assert cr.hex() in results
    assert probed_addresses[:2] == [start, start + 4096]


def test_short_process_memory_read_does_not_skip_later_live_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = 0x800000
    size = 2 * 1024 * 1024
    needle = bytes(range(32, 64))
    candidate = start + 16 * 1024
    contents = bytearray(size)
    contents[candidate - start + 0x188:candidate - start + 0x188 + 32] = needle
    reader = FakeReader(start, bytes(contents))
    original = reader.read
    calls = 0

    def short_read_once(addr: int, length: int) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(addr, min(length, 8 * 1024))
        return original(addr, length)

    monkeypatch.setattr(reader, 'read', short_read_once)

    def fake_candidate(
        _reader: object, _buf: bytes, *, start: int, offset: int, position: int,
        field_offset: int, module_base: int, module_end: int,
    ) -> tuple[str, dict[str, bytes]] | None:
        del module_base, module_end
        if start + offset + position - field_offset == candidate:
            return needle.hex(), {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}
        return None

    monkeypatch.setattr(sober_keys, '_scan_candidate', fake_candidate)
    found: sober_keys.SecretMap = {}
    assert sober_keys._scan_region(  # pyright: ignore[reportPrivateUsage]
        cast('sober_keys._MemoryReader', reader),  # pyright: ignore[reportPrivateUsage]
        start, size, needle=needle, field_offset=0x188,
        module_base=0, module_end=0, client_random=needle.hex(), stop=None, found=found,
    )
    assert needle.hex() in found


def test_large_sparse_sober_mapping_keeps_zero_page_probes_bounded() -> None:
    """A 2 GiB guest reservation cannot turn into millions of page reads."""
    class ZeroReader:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, _addr: int, _size: int) -> bytes:
            self.calls += 1
            return b''

    reader = ZeroReader()
    size = 32 * 1024 * 1024
    found: sober_keys.SecretMap = {}
    assert not sober_keys._scan_region(  # pyright: ignore[reportPrivateUsage]
        cast('sober_keys._MemoryReader', reader),  # pyright: ignore[reportPrivateUsage]
        0x100000, size, needle=bytes(range(1, 33)), field_offset=0x188,
        module_base=0, module_end=0, client_random=None, stop=None, found=found,
    )
    assert 32 < reader.calls <= 32 * 18
