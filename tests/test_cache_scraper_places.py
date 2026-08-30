import http.client
import json
import socket
import struct
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Never, cast

import pytest

from fleasion.cache import cache_manager as cache_manager_module
from fleasion.cache.cache_manager import CacheManager
from fleasion.proxy.addons import cache_scraper as cache_scraper_module
from fleasion.proxy.addons.cache_scraper import CacheLogEntry, CacheScraper


def _ktx2_pack_index(data: bytes) -> int | None:
    callback = cast(
        'Callable[[bytes], int | None]',
        cache_scraper_module.__dict__['_ktx2_pack_index'],
    )
    return callback(data)


def _clear_creator_caches() -> None:
    place_cache = cast('dict[int, list[int] | int]', CacheScraper.__dict__['_creator_place_cache'])
    last_success = cast('dict[int, int]', CacheScraper.__dict__['_creator_last_success'])
    place_cache.clear()
    last_success.clear()


def _fetch_place_ids(scraper: CacheScraper, creator_id: int, creator_type: int | None) -> list[int]:
    callback = cast(
        'Callable[[CacheScraper, int, int | None], list[int]]',
        CacheScraper.__dict__['_fetch_place_ids_for_creator'],
    )
    return callback(scraper, creator_id, creator_type)


def _executor(scraper: CacheScraper) -> ThreadPoolExecutor:
    return cast(ThreadPoolExecutor, scraper.__dict__['_executor'])


def _work_generation(scraper: CacheScraper) -> int:
    return cast(int, scraper.__dict__['_work_generation'])


def _url_to_asset(scraper: CacheScraper) -> dict[str, list[str]]:
    return cast('dict[str, list[str]]', scraper.__dict__['_url_to_asset'])


def _url_to_texpack_slot(scraper: CacheScraper) -> dict[str, set[tuple[int, int, int]]]:
    return cast(
        'dict[str, set[tuple[int, int, int]]]',
        scraper.__dict__['_url_to_texpack_slot'],
    )


def _store_asset_if_current(
    scraper: CacheScraper,
    generation: int | None,
    *,
    asset_id: str,
    asset_type: int,
    data: bytes,
) -> bool:
    callback = cast(
        'Callable[..., bool]',
        CacheScraper.__dict__['_store_asset_if_current'],
    )
    return callback(
        scraper,
        generation,
        asset_id=asset_id,
        asset_type=asset_type,
        data=data,
    )


def _store_texpack_slot(
    scraper: CacheScraper,
    parent_id: int,
    slot: int,
    quality: int,
    data: bytes,
) -> None:
    callback = cast(
        'Callable[[CacheScraper, int, int, int, bytes], None]',
        CacheScraper.__dict__['_store_texpack_slot_ktx2_async'],
    )
    callback(scraper, parent_id, slot, quality, data)


def _https_get_status(
    scraper: CacheScraper, hostname: str, path: str
) -> tuple[bytes | None, int | None]:
    callback = cast('Callable[..., object]', CacheScraper.__dict__['_https_get'])
    return cast(
        'tuple[bytes | None, int | None]',
        callback(scraper, hostname, path, return_status=True),
    )


def _set_https_get(scraper: CacheScraper, callback: Callable[..., object]) -> None:
    scraper.__dict__['_https_get'] = callback


def _set_submit_background(scraper: CacheScraper, callback: Callable[..., object]) -> None:
    scraper.__dict__['_submit_background'] = callback


class _CacheManager:
    def __init__(self) -> None:
        self.clear_count = 0
        self.stored: list[dict[str, object]] = []

    def clear_memory_cache(self) -> None:
        self.clear_count += 1

    def store_asset(self, **kwargs: object) -> bool:
        self.stored.append(kwargs)
        return True


def _make_scraper() -> CacheScraper:
    _clear_creator_caches()
    manager = _CacheManager()
    return CacheScraper(cast(CacheManager, manager))


def test_user_place_lookup_uses_supported_limits_and_falls_back() -> None:
    scraper = _make_scraper()
    calls: list[tuple[str, str, dict[str, str] | None]] = []

    def fake_https_get(
        host: str, path: str, extra_headers: dict[str, str] | None = None, **_kwargs: object
    ) -> bytes | None:
        calls.append((host, path, extra_headers))
        assert host == 'games.roblox.com'
        assert 'limit=100' not in path
        if 'limit=50' in path:
            return None
        if 'limit=25' in path:
            return json.dumps(
                {
                    'data': [
                        {'rootPlace': {'id': 155615604}},
                        {'rootPlace': {'id': 332857185}},
                    ],
                    'nextPageCursor': None,
                }
            ).encode()
        raise AssertionError(f'unexpected path: {path}')

    _set_https_get(scraper, fake_https_get)

    try:
        assert _fetch_place_ids(scraper, 53537032, 1) == [155615604, 332857185]
    finally:
        _executor(scraper).shutdown(wait=False, cancel_futures=True)

    assert any('limit=50' in path for _host, path, _headers in calls)
    assert any('limit=25' in path for _host, path, _headers in calls)


def test_user_place_lookup_finds_prison_life_places_with_limit_50() -> None:
    scraper = _make_scraper()
    calls: list[str] = []

    def fake_https_get(
        host: str, path: str, extra_headers: dict[str, str] | None = None, **_kwargs: object
    ) -> bytes:
        del host, extra_headers
        calls.append(path)
        assert 'limit=100' not in path
        if 'limit=50' not in path:
            raise AssertionError(f'unexpected fallback after successful limit=50: {path}')
        return json.dumps(
            {
                'data': [
                    {'name': '[Closed] Prison Life v2.0 Beta', 'rootPlace': {'id': 454002598}},
                    {'name': 'Prison Life', 'rootPlace': {'id': 155615604}},
                    {'name': 'FE PL', 'rootPlace': {'id': 332857185}},
                ],
                'nextPageCursor': None,
            }
        ).encode()

    _set_https_get(scraper, fake_https_get)

    try:
        assert _fetch_place_ids(scraper, 53537032, 1) == [
            454002598,
            155615604,
            332857185,
        ]
    finally:
        _executor(scraper).shutdown(wait=False, cancel_futures=True)

    assert calls


def test_reset_for_cache_clear_invalidates_old_background_work() -> None:
    scraper = _make_scraper()
    manager = cast(_CacheManager, scraper.cache_manager)
    scraper.cache_logs['old'] = cast(CacheLogEntry, {'assetTypeId': 1})
    _url_to_asset(scraper)['old-url'] = ['old']
    old_generation = _work_generation(scraper)
    old_executor = _executor(scraper)

    try:
        scraper.reset_for_cache_clear()

        assert _work_generation(scraper) == old_generation + 1
        assert scraper.cache_logs == {}
        assert _url_to_asset(scraper) == {}
        assert manager.clear_count == 1
        assert not _store_asset_if_current(
            scraper,
            old_generation,
            asset_id='old',
            asset_type=1,
            data=b'stale',
        )
        assert manager.stored == []
        assert _executor(scraper) is not old_executor
    finally:
        _executor(scraper).shutdown(wait=False, cancel_futures=True)


def test_https_get_status_failure_always_returns_unpackable_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = _make_scraper()

    def fail_connect(*_args: object, **_kwargs: object) -> Never:
        raise OSError('offline')

    monkeypatch.setattr(socket, 'create_connection', fail_connect)
    try:
        assert _https_get_status(scraper, 'assetdelivery.roblox.com', '/v1/asset/?id=123') == (
            None,
            None,
        )
    finally:
        _executor(scraper).shutdown(wait=False, cancel_futures=True)


def test_https_get_tries_the_next_cached_endpoint_after_a_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = _make_scraper()
    attempts: list[tuple[tuple[str, int], float]] = []

    class _FakeSocket:
        def close(self) -> None:
            return None

    class _FakeContext:
        check_hostname = True
        verify_mode: object | None = None

        def wrap_socket(self, raw_sock: _FakeSocket, server_hostname: str) -> _FakeSocket:
            assert server_hostname == 'assetdelivery.roblox.com'
            return raw_sock

    def fake_connect(address: tuple[str, int], timeout: float) -> _FakeSocket:
        attempts.append((address, timeout))
        if address[0] == '198.51.100.1':
            raise OSError('first route timed out')
        return _FakeSocket()

    def create_context() -> _FakeContext:
        return _FakeContext()

    def connection_init(*_args: object, **_kwargs: object) -> None:
        return None

    def request(*_args: object, **_kwargs: object) -> None:
        return None

    def read() -> bytes:
        return b''

    def getresponse(_self: object) -> SimpleNamespace:
        return SimpleNamespace(status=404, read=read, headers={})

    monkeypatch.setattr(socket, 'create_connection', fake_connect)
    monkeypatch.setattr('ssl.create_default_context', create_context)
    monkeypatch.setattr(http.client.HTTPConnection, '__init__', connection_init)
    monkeypatch.setattr(http.client.HTTPConnection, 'request', request)
    monkeypatch.setattr(http.client.HTTPConnection, 'getresponse', getresponse)
    scraper.set_real_ips({'assetdelivery.roblox.com': ['198.51.100.1', '93.184.216.34']})

    try:
        assert _https_get_status(scraper, 'assetdelivery.roblox.com', '/v1/asset/?id=123') == (
            None,
            404,
        )
    finally:
        _executor(scraper).shutdown(wait=False, cancel_futures=True)

    assert [address[0] for address, _timeout in attempts] == ['198.51.100.1', '93.184.216.34']


def _fake_roblox_ktx2(
    width: int,
    pack_index: int,
    marker: bytes,
    *,
    height: int | None = None,
    level_count: int = 1,
) -> bytes:
    height = width if height is None else height
    kv_value = f'{pack_index}'.encode('ascii') + b'\x00'
    kv_entry = b'packIndex\x00' + kv_value
    kvd = struct.pack('<I', len(kv_entry)) + kv_entry
    kvd += b'\x00' * ((-len(kvd)) % 4)
    data = bytearray(80 + len(kvd) + len(marker))
    data[:12] = b'\xabKTX 20\xbb\r\n\x1a\n'
    struct.pack_into('<II', data, 20, width, height)
    struct.pack_into('<I', data, 40, level_count)
    struct.pack_into('<IIIIQQ', data, 48, 0, 0, 80, len(kvd), 0, 0)
    data[80 : 80 + len(kvd)] = kvd
    data[80 + len(kvd) :] = marker
    return bytes(data)


def test_ktx2_pack_index_reads_roblox_kvd() -> None:
    assert _ktx2_pack_index(_fake_roblox_ktx2(512, 2, b'x')) == 2


def test_texturepack_slot_cache_preserves_every_mip_pack_and_highest_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager()
    scraper = CacheScraper(manager)
    packs = [
        (1, 32, 0, b'low-tail'),
        (2, 256, 1, b'mid'),
        (3, 512, 2, b'high'),
        (0, 1024, 3, b'base'),
    ]

    try:
        for quality, width, pack_index, marker in packs:
            _store_texpack_slot(
                scraper,
                9920600052,
                0,
                quality,
                _fake_roblox_ktx2(width, pack_index, marker),
            )

        archived = manager.get_texturepack_slot_pack_paths(9920600052, 0)
        assert len(archived) == len(packs)
        for quality, width, pack_index, marker in packs:
            matching = [
                path
                for path in archived
                if f'_pack{pack_index}_q{quality}_{width}x{width}_mips1_' in path.name
            ]
            assert len(matching) == 1
            assert matching[0].read_bytes().endswith(marker)

        canonical = manager.get_texturepack_slot_path(9920600052, 0)
        assert canonical.exists()
        assert struct.unpack_from('<I', canonical.read_bytes(), 20)[0] == 1024
        assert canonical.read_bytes().endswith(b'base')
    finally:
        _executor(scraper).shutdown(wait=False, cancel_futures=True)


def test_texturepack_slot_archive_never_overwrites_distinct_equal_metadata_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager()
    scraper = CacheScraper(manager)

    first = _fake_roblox_ktx2(512, 2, b'first')
    second = _fake_roblox_ktx2(512, 2, b'second')
    try:
        _store_texpack_slot(scraper, 7551983669, 2, 3, first)
        _store_texpack_slot(scraper, 7551983669, 2, 3, second)

        archived = manager.get_texturepack_slot_pack_paths(7551983669, 2)
        assert len(archived) == 2
        assert {path.read_bytes() for path in archived} == {first, second}
        assert manager.get_texturepack_slot_path(7551983669, 2).read_bytes() == second
    finally:
        _executor(scraper).shutdown(wait=False, cancel_futures=True)


def test_texturepack_shared_cdn_response_is_archived_for_every_logical_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager()
    scraper = CacheScraper(manager)
    scraper.enabled = True
    base_url = 'https://fts.rbxcdn.com/shared-texpack'
    payload = _fake_roblox_ktx2(256, 1, b'shared')
    _url_to_texpack_slot(scraper)[base_url] = {
        (9920600052, 0, 2),
        (7551983669, 2, 2),
    }

    def run_now(
        func: Callable[..., object], *args: object, generation: int | None = None
    ) -> object:
        return func(*args, generation=generation)

    _set_submit_background(scraper, run_now)
    try:
        scraper.process_cdn_response(
            base_url,
            '/shared-texpack',
            payload,
            'application/octet-stream',
        )

        assert len(manager.get_texturepack_slot_pack_paths(9920600052, 0)) == 1
        assert len(manager.get_texturepack_slot_pack_paths(7551983669, 2)) == 1
        assert manager.get_texturepack_slot_pack_paths(9920600052, 0)[0].read_bytes() == payload
        assert manager.get_texturepack_slot_pack_paths(7551983669, 2)[0].read_bytes() == payload
    finally:
        _executor(scraper).shutdown(wait=False, cancel_futures=True)


def test_texturepack_canonical_never_downgrades_when_lower_pack_arrives_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager()
    scraper = CacheScraper(manager)
    high = _fake_roblox_ktx2(1024, 3, b'high')
    low = _fake_roblox_ktx2(32, 0, b'low')

    try:
        _store_texpack_slot(scraper, 9920625499, 1, 0, high)
        _store_texpack_slot(scraper, 9920625499, 1, 1, low)

        assert len(manager.get_texturepack_slot_pack_paths(9920625499, 1)) == 2
        assert manager.get_texturepack_slot_path(9920625499, 1).read_bytes() == high
    finally:
        _executor(scraper).shutdown(wait=False, cancel_futures=True)
