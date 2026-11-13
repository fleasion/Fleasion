"""Profile Picture spoofer: thumbnail rewriting and proxy-owned local image URLs."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.addons.profile_picture_spoofer import (
    PFP_ROUTE_HOST,
    PFP_ROUTE_PREFIX,
    ProfilePictureSpoofer,
    clean_local_path,
    clear_local_images,
    is_pfp_route,
    lookup_local_image,
    normalize_entries,
    normalize_target,
)

if TYPE_CHECKING:
    from fleasion.proxy.server import ProxyFlow
    from fleasion.utils.json_types import JsonObject

REMOTE = 'https://example.com/me.png'
AVATAR_URL = 'https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds=5'


def _flow(url: str, payload: object, request_body: object = None) -> ProxyFlow:
    return cast(
        'ProxyFlow',
        SimpleNamespace(
            request=SimpleNamespace(
                pretty_url=url,
                content=b'' if request_body is None else json.dumps(request_body).encode(),
            ),
            response=SimpleNamespace(content=json.dumps(payload).encode()),
        ),
    )


def _spoofer(*entries: dict[str, object], me: str | None = None) -> ProfilePictureSpoofer:
    spoofer = ProfilePictureSpoofer()
    spoofer.set_entries(entries)
    spoofer.set_current_user(me)
    return spoofer


def _result(flow: ProxyFlow) -> dict[str, list[dict[str, object]]]:
    response = flow.response
    assert response is not None
    return json.loads(response.content)


def _image_urls(flow: ProxyFlow) -> list[object]:
    return [entry['imageUrl'] for entry in _result(flow)['data']]


@pytest.fixture(autouse=True)
def reset_local_image_routes() -> None:
    clear_local_images()


def test_normalize_target_accepts_ids_and_me_aliases() -> None:
    assert normalize_target(' 123 ') == '123'
    assert normalize_target('ME') == '__self__'
    assert normalize_target('__self__') == '__self__'
    assert normalize_target('abc') == ''


def test_normalize_entries_drops_unusable_rows() -> None:
    rows = normalize_entries(
        [
            {'enabled': False, 'target': '5', 'image_url': REMOTE},
            {'target': 'me', 'image_url': REMOTE},
            {'target': '', 'image_url': REMOTE},
            {'target': '7', 'image_url': ''},
            'junk',
        ]
    )
    assert rows == [
        {'enabled': False, 'target': '5', 'image_url': REMOTE},
        {'enabled': True, 'target': '__self__', 'image_url': REMOTE},
    ]
    assert normalize_entries('nope') == []


def test_saved_rows_are_read_on_first_use_not_at_construction() -> None:
    class Config:
        reads = 0

        @property
        def profile_picture_entries(self) -> list[JsonObject]:
            Config.reads += 1
            return [{'enabled': True, 'target': '5', 'image_url': REMOTE}]

    spoofer = ProfilePictureSpoofer(Config())
    assert Config.reads == 0
    assert spoofer.is_enabled()
    assert spoofer.is_enabled()
    assert Config.reads == 1


def test_batch_only_rewrites_avatar_entries_for_target_ids() -> None:
    spoofer = _spoofer({'enabled': True, 'target': '111', 'image_url': REMOTE})
    flow = _flow(
        'https://thumbnails.roblox.com/v1/batch',
        {
            'data': [
                {
                    'requestId': '111:undefined:AvatarHeadshot:150x150:png:regular',
                    'targetId': 111,
                    'imageUrl': 'old',
                },
                {'requestId': '111::Asset:150x150:png:regular', 'targetId': 111, 'imageUrl': 'asset'},
                {
                    'requestId': '222:undefined:AvatarHeadshot:150x150:png:regular',
                    'targetId': 222,
                    'imageUrl': 'other',
                },
            ]
        },
    )
    spoofer.response(flow)
    assert _image_urls(flow) == [REMOTE, 'asset', 'other']


def test_batch_uses_request_body_types_when_request_ids_are_opaque() -> None:
    spoofer = _spoofer({'enabled': True, 'target': '111', 'image_url': REMOTE})
    flow = _flow(
        'https://thumbnails.roblox.com/v1/batch',
        {
            'data': [
                {'requestId': 'a', 'targetId': 111, 'imageUrl': 'avatar'},
                {'requestId': 'b', 'targetId': 111, 'imageUrl': 'asset'},
                {'requestId': 'c', 'targetId': 222, 'imageUrl': 'other'},
            ]
        },
        request_body=[
            {'requestId': 'a', 'type': 'AvatarHeadShot', 'targetId': 111, 'size': '150x150'},
            {'requestId': 'b', 'type': 'Asset', 'targetId': 111, 'size': '150x150'},
            {'requestId': 'c', 'type': 'AvatarHeadShot', 'targetId': 222, 'size': '150x150'},
        ],
    )
    spoofer.response(flow)
    assert _image_urls(flow) == [REMOTE, 'asset', 'other']


def test_batch_request_type_wins_over_misleading_request_id() -> None:
    spoofer = _spoofer({'enabled': True, 'target': '111', 'image_url': REMOTE})
    flow = _flow(
        'https://thumbnails.roblox.com/v1/batch',
        {'data': [{'requestId': '111::AvatarHeadshot:x', 'targetId': 111, 'imageUrl': 'asset'}]},
        request_body=[{'requestId': '111::AvatarHeadshot:x', 'type': 'Asset', 'targetId': 111}],
    )
    spoofer.response(flow)
    assert _image_urls(flow) == ['asset']


def test_avatar_bust_endpoint_is_rewritten() -> None:
    spoofer = _spoofer({'enabled': True, 'target': '5', 'image_url': REMOTE})
    flow = _flow(
        'https://thumbnails.roblox.com/v1/users/avatar-bust?userIds=5',
        {'data': [{'targetId': 5, 'imageUrl': 'x'}]},
    )
    spoofer.response(flow)
    assert _image_urls(flow) == [REMOTE]


def test_avatar_headshot_endpoint_rewrites_every_matching_entry() -> None:
    spoofer = _spoofer({'enabled': True, 'target': '5', 'image_url': REMOTE})
    flow = _flow(
        'https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds=5,6&size=150x150',
        {'data': [{'targetId': 5, 'state': 'Pending', 'imageUrl': None}, {'targetId': 6, 'imageUrl': 'x'}]},
    )
    spoofer.response(flow)
    data = _result(flow)['data']
    assert data[0]['imageUrl'] == REMOTE
    assert data[0]['state'] == 'Completed'
    assert data[1]['imageUrl'] == 'x'


def test_disabled_entries_and_other_paths_are_untouched() -> None:
    spoofer = _spoofer({'enabled': False, 'target': '5', 'image_url': REMOTE})
    assert not spoofer.is_enabled()
    flow = _flow(AVATAR_URL, {'data': [{'targetId': 5, 'imageUrl': 'x'}]})
    spoofer.response(flow)
    assert _image_urls(flow) == ['x']

    enabled = _spoofer({'enabled': True, 'target': '5', 'image_url': REMOTE})
    other = _flow(
        'https://thumbnails.roblox.com/v1/assets?assetIds=5',
        {'data': [{'targetId': 5, 'imageUrl': 'x'}]},
    )
    enabled.response(other)
    assert _image_urls(other) == ['x']


def test_self_target_uses_current_user() -> None:
    spoofer = _spoofer({'enabled': True, 'target': 'me', 'image_url': REMOTE}, me='42')
    flow = _flow(
        'https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds=42',
        {'data': [{'targetId': 42, 'imageUrl': 'x'}]},
    )
    spoofer.response(flow)
    assert _image_urls(flow) == [REMOTE]


def test_local_image_becomes_proxy_owned_cdn_url(tmp_path: Path) -> None:
    image = tmp_path / 'pfp.png'
    image.write_bytes(b'\x89PNG')
    spoofer = _spoofer({'enabled': True, 'target': '9', 'image_url': f'"{image}"'})
    flow = _flow(
        'https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds=9',
        {'data': [{'targetId': 9, 'imageUrl': 'x'}]},
    )
    spoofer.response(flow)
    url = str(_image_urls(flow)[0])
    assert url.startswith(f'https://{PFP_ROUTE_HOST}{PFP_ROUTE_PREFIX}')
    assert url.endswith('.png')
    path = url.removeprefix(f'https://{PFP_ROUTE_HOST}')
    assert is_pfp_route(PFP_ROUTE_HOST, path)
    assert lookup_local_image(PFP_ROUTE_HOST, path) == str(image.resolve())
    assert lookup_local_image('example.com', path) is None


def test_missing_local_file_skips_the_entry(tmp_path: Path) -> None:
    spoofer = _spoofer({'enabled': True, 'target': '9', 'image_url': str(tmp_path / 'missing.png')})
    flow = _flow(
        'https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds=9',
        {'data': [{'targetId': 9, 'imageUrl': 'x'}]},
    )
    spoofer.response(flow)
    assert _image_urls(flow) == ['x']


def test_clean_local_path_handles_quotes_and_file_urls() -> None:
    assert clean_local_path('  "C:\\a b\\c.png"  ') == 'C:\\a b\\c.png'
    assert clean_local_path("'/tmp/x.png'") == '/tmp/x.png'
    assert clean_local_path('file:///C:/a%20b/c.png') == 'C:/a b/c.png'
    assert clean_local_path('file:///tmp/x.png') == '/tmp/x.png'
