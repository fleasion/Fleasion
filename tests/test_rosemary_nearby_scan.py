"""Recent validated Rosemary OpenSSL allocations accelerate targeted TLS lookups."""

from __future__ import annotations

import io
import threading
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

from fleasion.proxy import rosemary_keys


def _mapped_path(pid: int, backing: Path) -> Callable[[str | Path], Path]:
    def mapped(path: str | Path) -> Path:
        return backing if str(path) == f'/proc/{pid}/mem' else Path(path)

    return mapped


def _no_bounds(_pid: int) -> tuple[int, int]:
    return 0, 0


@pytest.fixture(autouse=True)
def reset_hints() -> Generator[None]:
    with rosemary_keys._region_hints_lock:  # pyright: ignore[reportPrivateUsage]
        rosemary_keys._region_hints.clear()  # pyright: ignore[reportPrivateUsage]
        rosemary_keys._candidate_hints.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    with rosemary_keys._region_hints_lock:  # pyright: ignore[reportPrivateUsage]
        rosemary_keys._region_hints.clear()  # pyright: ignore[reportPrivateUsage]
        rosemary_keys._candidate_hints.clear()  # pyright: ignore[reportPrivateUsage]


def test_neighbor_secret_uses_narrow_window_with_full_scan_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A new nearby connection is found quickly, while a moved connection still works."""
    pid = 771234
    start = 0x100000
    size = 20 * 1024 * 1024
    first_base = start + 8 * 1024 * 1024
    nearby_base = first_base + 8192
    moved_base = start + 18 * 1024 * 1024
    randoms = [bytes(range(i, i + 32)) for i in (1, 40, 80)]
    lookup: dict[int, bytes] = dict(
        zip((first_base, nearby_base, moved_base), randoms, strict=True)
    )
    backing = tmp_path / 'mem'
    with backing.open('wb') as mem:
        mem.truncate(start + size)
        for base, secret_random in lookup.items():
            mem.seek(base + 0x188)
            mem.write(secret_random)

    def mapped_regions(_pid: int) -> list[tuple[int, int]]:
        return [(start, size)]

    monkeypatch.setattr(rosemary_keys, 'Path', _mapped_path(pid, backing))
    monkeypatch.setattr(rosemary_keys, '_regions', mapped_regions)
    monkeypatch.setattr(rosemary_keys, '_module_bounds', _no_bounds)

    def validate_synthetic_candidate(
        _mem: io.BufferedReader,
        _buf: bytes,
        *,
        start: int,
        offset: int,
        position: int,
        field_offset: int,
        module_base: int,
        module_end: int,
    ) -> tuple[str, dict[str, bytes]] | None:
        del module_base, module_end
        candidate = start + offset + position - field_offset
        random = lookup.get(candidate)
        return (random.hex(), {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}) if random else None

    monkeypatch.setattr(rosemary_keys, '_scan_candidate', validate_synthetic_candidate)
    scan_original = rosemary_keys._scan_region  # pyright: ignore[reportPrivateUsage]
    calls: list[tuple[int, int]] = []

    def record_scan(
        mem: io.BufferedReader,
        region_start: int,
        region_size: int,
        *,
        needle: bytes,
        field_offset: int,
        module_base: int,
        module_end: int,
        client_random: str | None,
        stop: threading.Event | None,
        found: rosemary_keys.SecretMap,
        on_valid_candidate: Callable[[int], None] | None = None,
    ) -> bool:
        calls.append((region_start, region_size))
        return scan_original(
            mem,
            region_start,
            region_size,
            needle=needle,
            field_offset=field_offset,
            module_base=module_base,
            module_end=module_end,
            client_random=client_random,
            stop=stop,
            found=found,
            on_valid_candidate=on_valid_candidate,
        )

    monkeypatch.setattr(rosemary_keys, '_scan_region', record_scan)
    assert randoms[0].hex() in rosemary_keys.scan(pid, client_random=randoms[0].hex())
    assert calls == [(start, size)]
    assert rosemary_keys._candidate_hints[pid][0] == (  # pyright: ignore[reportPrivateUsage]
        start, size, first_base
    )

    calls.clear()
    assert randoms[1].hex() in rosemary_keys.scan(pid, client_random=randoms[1].hex())
    assert len(calls) == 1
    assert calls[0][1] == 256 * 1024
    assert calls[0][0] <= nearby_base < calls[0][0] + calls[0][1]

    calls.clear()
    assert randoms[2].hex() in rosemary_keys.scan(pid, client_random=randoms[2].hex())
    assert calls[-1] == (start, size)
    assert all(length < size for _, length in calls[:-1])
    assert rosemary_keys._candidate_hints[pid][0][2] == moved_base  # pyright: ignore[reportPrivateUsage]


def test_stale_mapping_and_pid_hints_never_hide_full_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = 557711
    old = (0x200000, 16 * 1024 * 1024)
    new = (0x4000000, 16 * 1024 * 1024)
    target = 'ae' * 32
    rosemary_keys._remember_candidate(pid, old, old[0] + 4096)  # pyright: ignore[reportPrivateUsage]
    observed: list[tuple[int, int]] = []

    def scan_stub(
        _mem: io.BufferedReader,
        start: int,
        size: int,
        *,
        client_random: str | None,
        found: rosemary_keys.SecretMap,
        on_valid_candidate: Callable[[int], None] | None = None,
        **_kwargs: object,
    ) -> bool:
        observed.append((start, size))
        if client_random == target and (start, size) == new:
            found[target] = {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}
            if on_valid_candidate:
                on_valid_candidate(new[0] + 8192)
            return True
        return False

    def mapped_regions(_pid: int) -> list[tuple[int, int]]:
        return [new]

    monkeypatch.setattr(rosemary_keys, '_scan_region', scan_stub)
    monkeypatch.setattr(rosemary_keys, '_regions', mapped_regions)
    monkeypatch.setattr(rosemary_keys, '_module_bounds', _no_bounds)
    monkeypatch.setattr(rosemary_keys, 'Path', _mapped_path(pid, Path('/dev/null')))
    assert target in rosemary_keys.scan(pid, client_random=target)
    assert observed == [new]
    assert rosemary_keys._candidate_hints[pid][0] == (  # pyright: ignore[reportPrivateUsage]
        new[0], new[1], new[0] + 8192
    )
    assert rosemary_keys._nearby_ranges(pid + 1, [new]) == []  # pyright: ignore[reportPrivateUsage]


def test_nearby_windows_clamp_to_live_regions_and_cache_is_bounded() -> None:
    pid = 66112
    region = (0x300000, 1024 * 1024)
    start, size = region
    remember = rosemary_keys._remember_candidate  # pyright: ignore[reportPrivateUsage]
    nearby = rosemary_keys._nearby_ranges  # pyright: ignore[reportPrivateUsage]
    remember(pid, region, start + 1)
    assert nearby(pid, [region]) == [(start, 256 * 1024, region)]
    remember(pid, region, start + size - 1)
    assert nearby(pid, [region])[0] == (start + size - 256 * 1024, 256 * 1024, region)
    assert nearby(pid, [(start + size, size)]) == []
    # A bogus position outside the region is not remembered
    remember(pid, region, start + size)
    assert len(nearby(pid, [region])) == 2
    for i in range(20):
        remember(pid, region, start + i * 4096)
    assert len(rosemary_keys._candidate_hints[pid]) == 8  # pyright: ignore[reportPrivateUsage]
    for other_pid in range(pid + 1, pid + 20):
        remember(other_pid, region, start + 8192)
    assert len(rosemary_keys._candidate_hints) == 8  # pyright: ignore[reportPrivateUsage]
    assert pid not in rosemary_keys._candidate_hints  # pyright: ignore[reportPrivateUsage]


def test_unvalidated_candidate_does_not_create_locality_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pid = 67112
    start = 0x10000
    backing = tmp_path / 'memory'
    needle = bytes(range(1, 33))
    with backing.open('wb') as mem:
        mem.truncate(start + 1024 * 1024)
        mem.seek(start + 8192 + 0x188)
        mem.write(needle)
    def mapped_regions(_pid: int) -> list[tuple[int, int]]:
        return [(start, 1024 * 1024)]

    def reject_candidate(
        _mem: io.BufferedReader,
        _buf: bytes,
        *,
        start: int,
        offset: int,
        position: int,
        field_offset: int,
        module_base: int,
        module_end: int,
    ) -> None:
        return None

    monkeypatch.setattr(rosemary_keys, 'Path', _mapped_path(pid, backing))
    monkeypatch.setattr(rosemary_keys, '_regions', mapped_regions)
    monkeypatch.setattr(rosemary_keys, '_module_bounds', _no_bounds)
    monkeypatch.setattr(rosemary_keys, '_scan_candidate', reject_candidate)
    assert rosemary_keys.scan(pid, client_random=needle.hex()) == {}
    assert pid not in rosemary_keys._candidate_hints  # pyright: ignore[reportPrivateUsage]
