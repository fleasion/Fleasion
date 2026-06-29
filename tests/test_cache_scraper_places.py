import json

from Fleasion.proxy.addons.cache_scraper import CacheScraper


class _CacheManager:
    pass


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
