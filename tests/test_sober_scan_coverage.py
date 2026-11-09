"""Sober TLS recovery covers non-contiguous resident memory and chunk boundaries."""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any

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
