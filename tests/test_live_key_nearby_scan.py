"""Nearby Windows OpenSSL allocation hints accelerate TLS lookups without false misses."""

from __future__ import annotations

import ctypes
from collections.abc import Iterator
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy import live_keys

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize('target_is_nearby', [True, False])
def test_targeted_tls_scan_uses_nearby_hint_before_full_region(
    monkeypatch: pytest.MonkeyPatch, target_is_nearby: bool
) -> None:
    pid = 97531
    region_start = 0x1000000
    region_size = live_keys.CHUNK_SIZE * 2
    known_base = region_start + 0x120000
    target_base = known_base + 4096 if target_is_nearby else region_start + 0x700000
    random = bytes(range(1, 33))
    reads: list[tuple[int, int]] = []

    def regions(_handle: int) -> Iterator[tuple[int, int]]:
        yield region_start, region_size

    def read(
        _handle: int, address: ctypes.c_void_p, buf: ctypes.Array[ctypes.c_char],
        size: int, received: ctypes.c_void_p,
    ) -> int:
        addr = int(address.value or 0)
        reads.append((addr, size))
        data = bytearray(size)
        needle_start = target_base + 0x184
        if addr <= needle_start and needle_start + len(random) <= addr + size:
            offset = needle_start - addr
            data[offset:offset + len(random)] = random
        ctypes.memmove(buf, bytes(data), size)
        ctypes.cast(received, ctypes.POINTER(ctypes.c_size_t)).contents.value = size
        return 0

    monkeypatch.setattr(live_keys, '_regions', regions)
    monkeypatch.setattr(getattr(live_keys, '_win32'), 'NtReadVirtualMemory', read, raising=False)
    with getattr(live_keys, '_scan_hints_lock'):
        monkeypatch.setitem(getattr(live_keys, '_scan_candidate_hints'), pid, [
            (region_start, region_size, known_base)
        ])

    candidates = cast('Callable[..., Iterator[tuple[int, tuple[int, int], int]]]',
                      getattr(live_keys, '_candidates'))
    matches = candidates(123, pid, client_random=random)
    assert next(matches)[0] == target_base
    if target_is_nearby:
        assert len(reads) == 1
        assert reads[0][1] == getattr(live_keys, '_NEARBY_SCAN_SIZE')
    else:
        assert len(reads) >= 2
        assert reads[0][1] == getattr(live_keys, '_NEARBY_SCAN_SIZE')
        assert any(size >= live_keys.CHUNK_SIZE for _, size in reads[1:])


def test_hint_window_bounds_and_stale_region() -> None:
    pid = 97532
    region = (0x100000, 200_000)
    with getattr(live_keys, '_scan_hints_lock'):
        getattr(live_keys, '_scan_candidate_hints')[pid] = [(region[0], region[1], region[0] + 100)]
    windows = list(getattr(live_keys, '_nearby_candidate_ranges')(pid, [region]))
    assert windows == [(region[0], region[1], region)]
    assert list(getattr(live_keys, '_nearby_candidate_ranges')(pid, [(0x200000, 200_000)])) == []
