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


def test_self_lookup_does_not_block_thumbnail_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cookie/API lookup must run outside the proxy's asyncio response handler."""
    import threading
    import time

    from fleasion.proxy.addons import profile_picture_spoofer as module

    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def authenticated(cookie: str) -> int:
        calls.append(cookie)
        entered.set()
        assert release.wait(timeout=3)
        return 42

    monkeypatch.setattr(module, '_get_roblosecurity', lambda: 'cookie-42')
    monkeypatch.setattr(module, '_authenticated_user_id', authenticated)
    spoofer = _spoofer({'enabled': True, 'target': 'me', 'image_url': REMOTE})
    first = _flow(AVATAR_URL, {'data': [{'targetId': 42, 'imageUrl': 'original'}]})
    try:
        started = time.perf_counter()
        spoofer.response(first)
        elapsed = time.perf_counter() - started
        assert elapsed < 0.2, 'Proxy callback blocked on the authenticated-user request'
        assert entered.wait(timeout=2)
        assert _image_urls(first) == ['original']
    finally:
        release.set()
    assert spoofer.current_user_id() == '42'
    second = _flow(AVATAR_URL, {'data': [{'targetId': 42, 'imageUrl': 'original'}]})
    spoofer.response(second)
    assert _image_urls(second) == [REMOTE]
    assert calls == ['cookie-42']


def test_account_switch_discards_older_inflight_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A late response for account A must not overwrite selected account B."""
    import threading

    from fleasion.proxy.addons import profile_picture_spoofer as module

    old_started = threading.Event()
    release_old = threading.Event()
    new_done = threading.Event()
    cookies: list[str] = []

    def authenticated(cookie: str) -> int:
        cookies.append(cookie)
        if cookie == 'account-a':
            old_started.set()
            assert release_old.wait(timeout=3)
            return 1
        assert cookie == 'account-b'
        new_done.set()
        return 2

    # Discovery must always follow the client's active cookie, not a cookie
    # deliberately selected for a future ticket-based launch
    active = {'cookie': 'account-a'}
    monkeypatch.setattr(module, '_get_roblosecurity', lambda: active['cookie'])
    monkeypatch.setattr(module, '_authenticated_user_id', authenticated)
    spoofer = _spoofer({'enabled': True, 'target': 'me', 'image_url': REMOTE})
    try:
        spoofer.notify_active_account_changed()
        assert old_started.wait(timeout=2)
        active['cookie'] = 'account-b'
        spoofer.notify_active_account_changed()
        assert new_done.wait(timeout=2)
        assert spoofer.current_user_id() == '2'
    finally:
        release_old.set()
    assert spoofer.current_user_id() == '2'
    old_flow = _flow(AVATAR_URL, {'data': [{'targetId': 1, 'imageUrl': 'original'}]})
    new_flow = _flow(AVATAR_URL, {'data': [{'targetId': 2, 'imageUrl': 'original'}]})
    spoofer.response(old_flow)
    spoofer.response(new_flow)
    assert _image_urls(old_flow) == ['original']
    assert _image_urls(new_flow) == [REMOTE]
    assert cookies == ['account-a', 'account-b']


def test_expired_identity_is_not_used_before_revalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An external account change cannot leave a cached self-id valid forever."""
    import threading
    import time

    from fleasion.proxy.addons import profile_picture_spoofer as module

    refresh_started = threading.Event()
    release = threading.Event()

    def authenticated(_cookie: str) -> int:
        refresh_started.set()
        assert release.wait(timeout=3)
        return 2

    discoveries: list[str] = []

    def discover() -> str:
        discoveries.append('new-cookie')
        return 'new-cookie'

    monkeypatch.setattr(module, '_get_roblosecurity', discover)
    monkeypatch.setattr(module, '_authenticated_user_id', authenticated)
    spoofer = _spoofer({'enabled': True, 'target': 'me', 'image_url': REMOTE}, me='1')
    monkeypatch.setattr(spoofer, '_self_id_verified_at', time.monotonic() - 3600)
    old_flow = _flow(AVATAR_URL, {'data': [{'targetId': 1, 'imageUrl': 'original'}]})
    try:
        spoofer.response(old_flow)
        assert refresh_started.wait(timeout=2)
        assert _image_urls(old_flow) == ['original']
    finally:
        release.set()
    assert spoofer.current_user_id() == '2'
    new_flow = _flow(AVATAR_URL, {'data': [{'targetId': 2, 'imageUrl': 'original'}]})
    spoofer.response(new_flow)
    assert _image_urls(new_flow) == [REMOTE]
    assert discoveries == ['new-cookie']


def test_shutdown_invalidates_inflight_identity_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from fleasion.proxy.addons import profile_picture_spoofer as module

    entered = threading.Event()
    release = threading.Event()

    def authenticated(_cookie: str) -> int:
        entered.set()
        assert release.wait(timeout=3)
        return 1

    monkeypatch.setattr(module, '_get_roblosecurity', lambda: 'cookie')
    monkeypatch.setattr(module, '_authenticated_user_id', authenticated)
    spoofer = _spoofer({'enabled': True, 'target': 'me', 'image_url': REMOTE})
    spoofer.response(_flow(AVATAR_URL, {'data': [{'targetId': 1, 'imageUrl': 'original'}]}))
    assert entered.wait(timeout=2)
    workers = [
        worker for worker in threading.enumerate()
        if worker.name == 'FleasionPfpCurrentUser' and worker.is_alive()
    ]
    spoofer.shutdown()
    release.set()
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert spoofer._current_user_id is None  # pyright: ignore[reportPrivateUsage]


def test_spoofer_only_rewrites_real_thumbnails_host() -> None:
    spoofer = _spoofer({'enabled': True, 'target': '5', 'image_url': REMOTE})
    for host in ('thumbnails.roblox.com.evil.example', 'evil-thumbnails.roblox.com'):
        flow = _flow(
            f'https://{host}/v1/users/avatar-headshot?userIds=5',
            {'data': [{'targetId': 5, 'imageUrl': 'original'}]},
        )
        spoofer.response(flow)
        assert _image_urls(flow) == ['original']


def test_thumbnails_logging_does_not_expose_signed_image_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.proxy.addons import profile_picture_spoofer as module

    seen: list[str] = []

    def record(_category: str, message: str) -> None:
        seen.append(message)

    monkeypatch.setattr(module.log_buffer, 'log', record)
    spoofer = _spoofer({'enabled': True, 'target': '5', 'image_url': REMOTE})
    flow = _flow(
        AVATAR_URL,
        {
            'data': [
                {
                    'targetId': 5,
                    'imageUrl': 'https://tr.rbxcdn.com/picture?auth=PRIVATE-SIGNED-VALUE',
                }
            ]
        },
    )
    spoofer.response(flow)
    assert _image_urls(flow) == [REMOTE]
    assert seen
    assert all('PRIVATE-SIGNED-VALUE' not in message for message in seen)
    assert any('image_host=tr.rbxcdn.com' in message for message in seen)


def test_local_image_size_limit_applies_at_registration_and_after_growth(
    tmp_path: Path,
) -> None:
    from fleasion.proxy.addons.profile_picture_spoofer import (
        PFP_MAX_IMAGE_BYTES,
        register_local_image,
    )

    image = tmp_path / 'picture.png'
    image.write_bytes(b'png')
    url = register_local_image(image)
    path = url.removeprefix(f'https://{PFP_ROUTE_HOST}')
    assert lookup_local_image(PFP_ROUTE_HOST, path) == str(image.resolve())

    with image.open('r+b') as file:
        file.truncate(PFP_MAX_IMAGE_BYTES + 1)
    assert lookup_local_image(PFP_ROUTE_HOST, path) is None
    with pytest.raises(ValueError, match='10 MiB'):
        register_local_image(image)

    spoofer = _spoofer({'enabled': True, 'target': '5', 'image_url': str(image)})
    flow = _flow(AVATAR_URL, {'data': [{'targetId': 5, 'imageUrl': 'original'}]})
    spoofer.response(flow)
    assert _image_urls(flow) == ['original']


@pytest.mark.parametrize('method', ['GET', 'HEAD', 'RANGE'])
def test_profile_image_serving_bounds_actual_file_bytes(
    tmp_path: Path, method: str
) -> None:
    from fleasion.proxy import server

    image = tmp_path / 'image.png'
    image.write_bytes(b'123456')
    response = server._serve_local_file(  # pyright: ignore[reportPrivateUsage]
        str(image),
        range_header=b'bytes=0-2' if method == 'RANGE' else None,
        head_only=method == 'HEAD',
        max_bytes=5,
    )
    assert response.startswith(b'HTTP/1.1 413 ')
    assert response.endswith(b'\r\n\r\n')
    image.write_bytes(b'1234')
    response = server._serve_local_file(  # pyright: ignore[reportPrivateUsage]
        str(image),
        range_header=b'bytes=0-2' if method == 'RANGE' else None,
        head_only=method == 'HEAD',
        max_bytes=5,
    )
    assert response.startswith(b'HTTP/1.1 200 ') or response.startswith(b'HTTP/1.1 206 ')


def test_local_picture_rejects_non_image_file_even_when_small(tmp_path: Path) -> None:
    from fleasion.proxy.addons.profile_picture_spoofer import register_local_image

    script = tmp_path / 'not-an-image.obj'
    script.write_bytes(b'v 0 0 0')
    with pytest.raises(ValueError, match='supported image'):
        register_local_image(script)
    spoofer = _spoofer({'enabled': True, 'target': '5', 'image_url': str(script)})
    flow = _flow(AVATAR_URL, {'data': [{'targetId': 5, 'imageUrl': 'original'}]})
    spoofer.response(flow)
    assert _image_urls(flow) == ['original']
