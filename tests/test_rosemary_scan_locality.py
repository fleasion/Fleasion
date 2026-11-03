"""Recently validated Rosemary memory regions guide later lookups without hiding fallback regions."""

from __future__ import annotations

from pathlib import Path
import pytest

from fleasion.proxy import rosemary_keys

@pytest.fixture(autouse=True)
def synthetic_regions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rosemary_keys._region_hints.clear()  # pyright: ignore[reportPrivateUsage]
    backing = tmp_path / 'mem'
    backing.write_bytes(bytes(32))
    real_path = Path

    def make_path(value: str) -> Path:
        if str(value).endswith('/mem') and str(value).startswith('/proc/'):
            return backing
        return real_path(value)

    monkeypatch.setattr(rosemary_keys, 'Path', make_path)

    def module_bounds(_pid: int) -> tuple[int, int]:
        return (0, 0)

    monkeypatch.setattr(rosemary_keys, '_module_bounds', module_bounds)
    regions = [(4096, 32), (8192, 32), (12288, 32)]

    def mapped_regions(_pid: int) -> list[tuple[int, int]]:
        return regions

    monkeypatch.setattr(rosemary_keys, '_regions', mapped_regions)

def test_followup_secret_prefers_previously_successful_region(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    first = 'aa' * 32
    second = 'bb' * 32
    observed: list[int] = []

    def mock_scan(_mem: object, start: int, _size: int, *, client_random: str | None,
                  found: dict[str, dict[str, bytes]], **_kwargs: object) -> bool:
        observed.append(start)
        if client_random is not None and start == 12288:
            found[client_random] = {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}
            return True
        return False

    monkeypatch.setattr(rosemary_keys, '_scan_region', mock_scan)
    assert first in rosemary_keys.scan(565656, client_random=first)
    assert observed == [4096, 8192, 12288]
    observed.clear()
    assert second in rosemary_keys.scan(565656, client_random=second)
    assert observed == [12288]


def test_stale_hint_falls_back_and_learns_new_region(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[int] = []
    targets = {'aa' * 32: 12288, 'bb' * 32: 4096, 'cc' * 32: 4096}

    def mock_scan(_mem: object, start: int, _size: int, *, client_random: str | None,
                  found: dict[str, dict[str, bytes]], **_kwargs: object) -> bool:
        observed.append(start)
        if client_random is not None and targets[client_random] == start:
            found[client_random] = {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}
            return True
        return False

    monkeypatch.setattr(rosemary_keys, '_scan_region', mock_scan)
    assert rosemary_keys.scan(123456, client_random='aa' * 32)
    observed.clear()
    assert rosemary_keys.scan(123456, client_random='bb' * 32)
    assert observed == [12288, 4096]
    observed.clear()
    assert rosemary_keys.scan(123456, client_random='cc' * 32)
    assert observed == [4096]


def test_hints_are_scoped_to_process_id(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[int] = []

    def mock_scan(_mem: object, start: int, _size: int, *, client_random: str | None,
                  found: dict[str, dict[str, bytes]], **_kwargs: object) -> bool:
        observed.append(start)
        if client_random is not None and start == 12288:
            found[client_random] = {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}
            return True
        return False

    monkeypatch.setattr(rosemary_keys, '_scan_region', mock_scan)
    assert rosemary_keys.scan(333111, client_random='aa' * 32)
    observed.clear()
    assert rosemary_keys.scan(333222, client_random='bb' * 32)
    assert observed == [4096, 8192, 12288]


def test_missing_cached_region_does_not_hide_live_mappings(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[int] = []
    mapped = [(4096, 32), (8192, 32), (12288, 32)]
    found_in = [12288]

    def live_regions(_pid: int) -> list[tuple[int, int]]:
        return mapped

    def mock_scan(_mem: object, start: int, _size: int, *, client_random: str | None,
                  found: dict[str, dict[str, bytes]], **_kwargs: object) -> bool:
        observed.append(start)
        if client_random is not None and start == found_in[0]:
            found[client_random] = {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}
            return True
        return False

    monkeypatch.setattr(rosemary_keys, '_regions', live_regions)
    monkeypatch.setattr(rosemary_keys, '_scan_region', mock_scan)
    assert rosemary_keys.scan(777111, client_random='aa' * 32)
    mapped[:] = [(4096, 32), (16384, 32)]
    found_in[0] = 16384
    observed.clear()
    assert rosemary_keys.scan(777111, client_random='bb' * 32)
    assert observed == [4096, 16384]


def test_region_affinity_cache_has_bounded_process_and_region_entries(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    many_regions = [(4096 * i, 32) for i in range(1, 17)]

    def live_regions(_pid: int) -> list[tuple[int, int]]:
        return many_regions

    def mock_scan(_mem: object, start: int, _size: int, *, client_random: str | None,
                  found: dict[str, dict[str, bytes]], **_kwargs: object) -> bool:
        if client_random is None:
            found[f'{start:064x}'] = {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}
        elif start == many_regions[-1][0]:
            found[client_random] = {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}
            return True
        return False

    monkeypatch.setattr(rosemary_keys, '_regions', live_regions)
    monkeypatch.setattr(rosemary_keys, '_scan_region', mock_scan)
    assert len(rosemary_keys.scan(888111)) == len(many_regions)
    cached = rosemary_keys._region_hints[888111]  # pyright: ignore[reportPrivateUsage]
    assert len(cached) == 8
    assert cached[0] == many_regions[-1]
    for pid in range(888112, 888126):
        assert rosemary_keys.scan(pid, client_random=f'{pid:064x}')
    assert len(rosemary_keys._region_hints) == 8  # pyright: ignore[reportPrivateUsage]
    assert 888111 not in rosemary_keys._region_hints  # pyright: ignore[reportPrivateUsage]


def test_later_region_read_failure_keeps_previously_validated_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = 'ab' * 32
    visits: list[int] = []

    def mock_scan(_mem: object, start: int, _size: int, *,
                  found: dict[str, dict[str, bytes]], **_kwargs: object) -> bool:
        visits.append(start)
        if start == 4096:
            found[expected] = {'CLIENT_TRAFFIC_SECRET_0': bytes(32)}
        elif start == 8192:
            raise OSError('Simulated mapping disappearance after first valid region')
        return False

    monkeypatch.setattr(rosemary_keys, '_scan_region', mock_scan)
    recovered = rosemary_keys.scan(999111)
    assert visits == [4096, 8192]
    assert expected in recovered
