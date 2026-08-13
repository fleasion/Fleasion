from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

from fleasion.qml_api.account_store import AccountStore
from fleasion.qml_api.reserved_rejoin import ReservedRejoinInterceptor
from fleasion.qml_api.subplaces import (
    RobloxPlacesClient,
    SubplaceSettingsStore,
    SubplacesApi,
    build_place_launch_uri,
    extract_place_id,
)


class _Response:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


def test_subplace_store_migrates_legacy_schema(tmp_path: Path):
    primary = tmp_path / 'subplace_joiner_settings.json'
    legacy = tmp_path / 'subplace' / 'settings.json'
    legacy.parent.mkdir()
    legacy.write_text(
        json.dumps(
            {
                'recent_ids': ['123', '', '123'],
                'favorites': ['456'],
                'custom_names': {'123': 'Main game'},
            }
        ),
        encoding='utf-8',
    )

    store = SubplaceSettingsStore(primary, legacy)
    recent, favorites, names = store.load()

    assert recent == ['123']
    assert favorites == ['456']
    assert names == {'123': 'Main game'}
    assert json.loads(primary.read_text(encoding='utf-8'))['recent_ids'] == ['123']


def test_public_subplace_client_paginates_and_maps_thumbnails():
    requested: list[str] = []

    def fetch(url: str) -> _Response:
        requested.append(url)
        if '/places/' in url and url.endswith('/universe'):
            return _Response({'universeId': 99})
        if 'games?universeIds' in url:
            return _Response({'data': [{'rootPlaceId': 100}]})
        if 'develop.roblox.com' in url:
            query = parse_qs(urlparse(url).query)
            if query.get('cursor') == ['next']:
                return _Response({'data': [{'id': 102, 'name': 'Second'}]})
            return _Response({'data': [{'id': 100, 'name': 'Root'}], 'nextPageCursor': 'next'})
        if 'thumbnails.roblox.com' in url:
            return _Response(
                {
                    'data': [
                        {'targetId': 100, 'imageUrl': 'https://images.invalid/root.png'},
                        {'targetId': 102, 'imageUrl': 'https://images.invalid/second.png'},
                    ]
                }
            )
        raise AssertionError(url)

    result = RobloxPlacesClient(fetch).discover('100')

    assert [row['placeId'] for row in result['places']] == ['100', '102']
    assert result['places'][0]['isRoot'] is True
    assert result['places'][1]['thumbnailUrl'].endswith('second.png')
    assert sum('develop.roblox.com' in url for url in requested) == 2


def test_subplaces_api_filters_favorites_and_launches(tmp_path: Path):
    launched: list[str] = []
    client = SimpleNamespace(
        discover=lambda place_id: {
            'placeId': place_id,
            'places': [
                {'placeId': place_id, 'name': 'Root', 'isRoot': True},
                {'placeId': '202', 'name': 'Dungeon', 'isRoot': False},
            ],
        }
    )
    controller = SubplacesApi(
        client=client,
        settings_store=SubplaceSettingsStore(tmp_path / 'settings.json'),
        launcher=lambda target: launched.append(target) or True,
    )  # pyright: ignore[reportCallIssue, reportArgumentType]
    try:
        controller._apply_search_result(client.discover('101'))
        assert controller.resultCount == 2
        controller.query = 'dungeon'
        assert controller.model.get(0)['placeId'] == '202'
        controller.toggleFavorite('101')
        assert controller.currentIsFavorite
        assert controller.launch('202', 'server-id')
        assert launched == [build_place_launch_uri('202', 'server-id')]
    finally:
        controller.shutdown()


def test_place_id_parser_and_launch_uri_do_not_accept_arbitrary_hosts():
    assert extract_place_id('https://www.roblox.com/games/1818/Classic') == '1818'
    assert extract_place_id('https://example.com/not-a-place') == ''
    assert build_place_launch_uri('1818') == 'roblox://experiences/start?placeId=1818'


def test_account_store_never_persists_plain_cookie(tmp_path: Path):
    store = AccountStore(tmp_path / 'accounts.json', tmp_path / 'accounts.key')
    account = store.create('Builder', 'cookie-secret', '123')
    store.save([account])

    raw = store.path.read_text(encoding='utf-8')
    assert 'cookie-secret' not in raw
    assert store.cookie(store.load()[0]) == 'cookie-secret'


class _Request:
    def __init__(self, url: str, payload: dict[str, Any]) -> None:
        self.url = url
        self.pretty_url = url
        self.raw_content = json.dumps(payload).encode('utf-8')
        self.headers: dict[str, str] = {}

    @property
    def content(self) -> bytes:
        return self.raw_content


class _Flow:
    def __init__(self, url: str, payload: dict[str, Any]) -> None:
        self.request = _Request(url, payload)
        self.response = None


def test_reserved_rejoin_captures_and_redirects_next_join():
    interceptor = ReservedRejoinInterceptor()
    capture = _Flow(
        'https://gamejoin.roblox.com/v1/join-reserved-game',
        {'placeId': 202, 'accessCode': 'access-secret'},
    )
    interceptor.request(capture)
    assert interceptor.snapshot()['placeId'] == '202'
    assert interceptor.arm()

    join = _Flow(
        'https://gamejoin.roblox.com/v1/join-game',
        {'placeId': 101, 'gameJoinAttemptId': 'attempt-1'},
    )
    interceptor.request(join)

    assert join.request.url.endswith('/v1/join-reserved-game')
    assert json.loads(join.request.raw_content) == {
        'placeId': '202',
        'accessCode': 'access-secret',
        'isTeleport': True,
        'isImmersiveAdsTeleport': False,
    }
