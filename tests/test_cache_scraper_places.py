import json
import http.client
import socket
import struct
from types import SimpleNamespace

from fleasion.cache import cache_manager as cache_manager_module
from fleasion.proxy.addons.cache_scraper import CacheScraper, _ktx2_pack_index


class _CacheManager:
    def __init__(self):
        self.clear_count = 0
        self.stored = []

    def clear_memory_cache(self):
        self.clear_count += 1

    def store_asset(self, **kwargs):
        self.stored.append(kwargs)
        return True


def _make_scraper():
    CacheScraper._creator_place_cache.clear()
    CacheScraper._creator_last_success.clear()
    return CacheScraper(_CacheManager())


def test_user_place_lookup_uses_supported_limits_and_falls_back():
    scraper = _make_scraper()
    calls = []

    def fake_https_get(host, path, extra_headers=None):
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

    scraper._https_get = fake_https_get

    try:
        assert scraper._fetch_place_ids_for_creator(53537032, 1) == [155615604, 332857185]
    finally:
        scraper._executor.shutdown(wait=False, cancel_futures=True)

    assert any('limit=50' in path for _host, path, _headers in calls)
    assert any('limit=25' in path for _host, path, _headers in calls)


def test_user_place_lookup_finds_prison_life_places_with_limit_50():
    scraper = _make_scraper()
    calls = []

    def fake_https_get(host, path, extra_headers=None):
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

    scraper._https_get = fake_https_get

    try:
        assert scraper._fetch_place_ids_for_creator(53537032, 1) == [
            454002598,
            155615604,
            332857185,
        ]
    finally:
        scraper._executor.shutdown(wait=False, cancel_futures=True)

    assert calls


def test_reset_for_cache_clear_invalidates_old_background_work():
    scraper = _make_scraper()
    manager = scraper.cache_manager
    scraper.cache_logs['old'] = {'assetTypeId': 1}
    scraper._url_to_asset['old-url'] = ['old']
    old_generation = scraper._work_generation
    old_executor = scraper._executor

    try:
        scraper.reset_for_cache_clear()

        assert scraper._work_generation == old_generation + 1
        assert scraper.cache_logs == {}
        assert scraper._url_to_asset == {}
        assert manager.clear_count == 1
        assert not scraper._store_asset_if_current(
            old_generation,
            asset_id='old',
            asset_type=1,
            data=b'stale',
        )
        assert manager.stored == []
        assert scraper._executor is not old_executor
    finally:
        scraper._executor.shutdown(wait=False, cancel_futures=True)


def test_https_get_status_failure_always_returns_unpackable_tuple(monkeypatch):
    scraper = _make_scraper()

    def fail_connect(*_args, **_kwargs):
        raise OSError('offline')

    monkeypatch.setattr(socket, 'create_connection', fail_connect)
    try:
        assert scraper._https_get(
            'assetdelivery.roblox.com',
            '/v1/asset/?id=123',
            return_status=True,
        ) == (None, None)
    finally:
        scraper._executor.shutdown(wait=False, cancel_futures=True)


def test_https_get_tries_the_next_cached_endpoint_after_a_connect_failure(monkeypatch):
    scraper = _make_scraper()
    attempts = []

    class _FakeSocket:
        def close(self):
            return None

    class _FakeContext:
        check_hostname = True
        verify_mode = None

        def wrap_socket(self, raw_sock, server_hostname):
            assert server_hostname == 'assetdelivery.roblox.com'
            return raw_sock

    def fake_connect(address, timeout):
        attempts.append((address, timeout))
        if address[0] == '198.51.100.1':
            raise OSError('first route timed out')
        return _FakeSocket()

    monkeypatch.setattr(socket, 'create_connection', fake_connect)
    monkeypatch.setattr('ssl.create_default_context', lambda: _FakeContext())
    monkeypatch.setattr(http.client.HTTPConnection, '__init__', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(http.client.HTTPConnection, 'request', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        http.client.HTTPConnection,
        'getresponse',
        lambda _self: SimpleNamespace(status=404, read=lambda: b'', headers={}),
    )
    scraper.set_real_ips({'assetdelivery.roblox.com': ['198.51.100.1', '93.184.216.34']})

    try:
        assert scraper._https_get(
            'assetdelivery.roblox.com',
            '/v1/asset/?id=123',
            return_status=True,
        ) == (None, 404)
    finally:
        scraper._executor.shutdown(wait=False, cancel_futures=True)

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


def test_ktx2_pack_index_reads_roblox_kvd():
    assert _ktx2_pack_index(_fake_roblox_ktx2(512, 2, b'x')) == 2


def test_texturepack_slot_cache_preserves_every_mip_pack_and_highest_canonical(
    tmp_path, monkeypatch
):
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
            scraper._store_texpack_slot_ktx2_async(
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
        scraper._executor.shutdown(wait=False, cancel_futures=True)


def test_texturepack_slot_archive_never_overwrites_distinct_equal_metadata_payloads(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager()
    scraper = CacheScraper(manager)

    first = _fake_roblox_ktx2(512, 2, b'first')
    second = _fake_roblox_ktx2(512, 2, b'second')
    try:
        scraper._store_texpack_slot_ktx2_async(7551983669, 2, 3, first)
        scraper._store_texpack_slot_ktx2_async(7551983669, 2, 3, second)

        archived = manager.get_texturepack_slot_pack_paths(7551983669, 2)
        assert len(archived) == 2
        assert {path.read_bytes() for path in archived} == {first, second}
        assert manager.get_texturepack_slot_path(7551983669, 2).read_bytes() == second
    finally:
        scraper._executor.shutdown(wait=False, cancel_futures=True)


def test_texturepack_shared_cdn_response_is_archived_for_every_logical_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager()
    scraper = CacheScraper(manager)
    scraper.enabled = True
    base_url = 'https://fts.rbxcdn.com/shared-texpack'
    payload = _fake_roblox_ktx2(256, 1, b'shared')
    scraper._url_to_texpack_slot[base_url] = {
        (9920600052, 0, 2),
        (7551983669, 2, 2),
    }

    def run_now(func, *args, generation=None):
        return func(*args, generation=generation)

    monkeypatch.setattr(scraper, '_submit_background', run_now)
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
        scraper._executor.shutdown(wait=False, cancel_futures=True)


def test_texturepack_canonical_never_downgrades_when_lower_pack_arrives_late(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager()
    scraper = CacheScraper(manager)
    high = _fake_roblox_ktx2(1024, 3, b'high')
    low = _fake_roblox_ktx2(32, 0, b'low')

    try:
        scraper._store_texpack_slot_ktx2_async(9920625499, 1, 0, high)
        scraper._store_texpack_slot_ktx2_async(9920625499, 1, 1, low)

        assert len(manager.get_texturepack_slot_pack_paths(9920625499, 1)) == 2
        assert manager.get_texturepack_slot_path(9920625499, 1).read_bytes() == high
    finally:
        scraper._executor.shutdown(wait=False, cancel_futures=True)
