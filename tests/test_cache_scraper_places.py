import json
import socket

from fleasion.proxy.addons.cache_scraper import CacheScraper


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
            return json.dumps({
                'data': [
                    {'rootPlace': {'id': 155615604}},
                    {'rootPlace': {'id': 332857185}},
                ],
                'nextPageCursor': None,
            }).encode()
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
        return json.dumps({
            'data': [
                {'name': '[Closed] Prison Life v2.0 Beta', 'rootPlace': {'id': 454002598}},
                {'name': 'Prison Life', 'rootPlace': {'id': 155615604}},
                {'name': 'FE PL', 'rootPlace': {'id': 332857185}},
            ],
            'nextPageCursor': None,
        }).encode()

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
