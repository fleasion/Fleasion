"""Profile Picture spoofer: thumbnail rewriting and proxy-owned local image URLs."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.addons import profile_picture_spoofer as pfp_module
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
            response=SimpleNamespace(content=json.dumps(payload).encode(), status_code=200),
        ),
    )


def _spoofer(*entries: dict[str, object], me: str | None = None) -> ProfilePictureSpoofer:
    spoofer = ProfilePictureSpoofer()
    spoofer.set_entries(entries)
    if me is not None:
        spoofer.set_current_user(me)
    return spoofer


def _result(flow: ProxyFlow) -> dict[str, list[dict[str, object]]]:
    response = flow.response
    assert response is not None
    return json.loads(response.content)


def _image_urls(flow: ProxyFlow) -> list[object]:
    return [entry['imageUrl'] for entry in _result(flow)['data']]


@pytest.fixture(autouse=True)
def reset_local_image_routes(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_me_uses_explicit_account_manager_selection_without_gamejoin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chosen account is authoritative, regardless of Player process state."""
    import threading

    finished = threading.Event()

    def resolve(cookie: str) -> str:
        assert cookie == 'selected-cookie-B'
        finished.set()
        return '222'

    monkeypatch.setattr(pfp_module, '_selected_account_user_id', resolve)
    spoofer = _spoofer({'enabled': True, 'target': 'me', 'image_url': REMOTE})
    spoofer.select_account_cookie('selected-cookie-B')
    assert finished.wait(2)
    assert spoofer.current_user_id() == '222'
    other = _flow(AVATAR_URL, {'data': [{'targetId': 222, 'imageUrl': 'original'}]})
    spoofer.response(other)
    assert _image_urls(other) == [REMOTE]


def test_switch_account_ignores_old_lookup_after_newer_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    old_entered = threading.Event()
    release_old = threading.Event()
    new_complete = threading.Event()

    def resolve(cookie: str) -> str:
        if cookie == 'A':
            old_entered.set()
            assert release_old.wait(3)
            return '1'
        assert cookie == 'B'
        new_complete.set()
        return '2'

    monkeypatch.setattr(pfp_module, '_selected_account_user_id', resolve)
    spoofer = _spoofer({'enabled': True, 'target': 'me', 'image_url': REMOTE})
    try:
        spoofer.select_account_cookie('A')
        assert old_entered.wait(2)
        spoofer.select_account_cookie('B')
        assert new_complete.wait(2)
        for _ in range(50):
            if spoofer.current_user_id() == '2':
                break
            time.sleep(0.005)
    finally:
        release_old.set()
    assert spoofer.current_user_id() == '2'


def test_selection_invalidates_previous_id_and_notifies_gui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    entered = threading.Event()
    release = threading.Event()

    def resolve(_cookie: str) -> str:
        entered.set()
        assert release.wait(2)
        return '2'

    monkeypatch.setattr(pfp_module, '_selected_account_user_id', resolve)
    spoofer = _spoofer({'enabled': True, 'target': 'me', 'image_url': REMOTE}, me='1')
    transitions: list[str | None] = []

    def listener() -> None:
        transitions.append(spoofer.current_user_id())

    spoofer.add_identity_listener(listener)
    try:
        spoofer.select_account_cookie('B')
        assert entered.wait(2)
        assert spoofer.current_user_id() is None
        assert transitions == [None]
    finally:
        release.set()
    for _ in range(1000):
        if spoofer.current_user_id() == '2':
            break
        time.sleep(0.001)
    assert spoofer.current_user_id() == '2'
    assert transitions == [None, '2']
    spoofer.remove_identity_listener(listener)


def test_startup_identity_never_overwrites_explicit_selection() -> None:
    spoofer = _spoofer({'enabled': True, 'target': 'me', 'image_url': REMOTE})
    spoofer.adopt_initial_account('1')
    assert spoofer.current_user_id() == '1'
    spoofer.set_current_user('2')
    spoofer.adopt_initial_account('1')
    assert spoofer.current_user_id() == '2'


def test_failed_selected_cookie_lookup_leaves_me_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    resolved = threading.Event()

    def resolve(_cookie: str) -> str | None:
        resolved.set()
        return None

    monkeypatch.setattr(pfp_module, '_selected_account_user_id', resolve)
    spoofer = _spoofer({'enabled': True, 'target': 'me', 'image_url': REMOTE}, me='1')
    spoofer.select_account_cookie('expired')
    assert resolved.wait(2)
    assert spoofer.current_user_id() is None


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


def test_large_import_resolves_only_advertised_avatar_routes(tmp_path: Path) -> None:
    """1,025 imported images must not evict the first advertised URL."""
    from fleasion.proxy.addons import profile_picture_spoofer as module

    entries: list[dict[str, object]] = []
    for user_id in range(1, 1026):
        path = tmp_path / f'picture-{user_id}.png'
        path.write_bytes(b'png')
        entries.append({'enabled': True, 'target': str(user_id), 'image_url': str(path)})
    spoofer = ProfilePictureSpoofer()
    spoofer.set_entries(entries)
    flow = _flow(AVATAR_URL, {'data': [{'targetId': 1, 'imageUrl': 'original'}]})
    spoofer.response(flow)
    url = str(_image_urls(flow)[0])
    assert url.startswith(f'https://{PFP_ROUTE_HOST}{PFP_ROUTE_PREFIX}')
    assert lookup_local_image(PFP_ROUTE_HOST, url.removeprefix(f'https://{PFP_ROUTE_HOST}')) == str(
        (tmp_path / 'picture-1.png').resolve()
    )
    assert len(module.__dict__['_routes']) == 1


def test_full_route_registry_never_evicts_fresh_advertised_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fleasion.proxy.addons import profile_picture_spoofer as module

    monkeypatch.setattr(module, '_PFP_ROUTE_LIMIT', 4)
    entries: list[dict[str, object]] = []
    for user_id in range(1, 6):
        path = tmp_path / f'{user_id}.png'
        path.write_bytes(b'img')
        entries.append({'enabled': True, 'target': str(user_id), 'image_url': str(path)})
    spoofer = ProfilePictureSpoofer()
    spoofer.set_entries(entries)
    advertised: list[str] = []
    for user_id in range(1, 5):
        flow = _flow(AVATAR_URL, {'data': [{'targetId': user_id, 'imageUrl': 'original'}]})
        spoofer.response(flow)
        advertised.append(str(_image_urls(flow)[0]))
    fifth = _flow(AVATAR_URL, {'data': [{'targetId': 5, 'imageUrl': 'original'}]})
    spoofer.response(fifth)
    assert _image_urls(fifth) == ['original']
    assert len(module.__dict__['_routes']) == 4
    for url in advertised:
        path = url.removeprefix(f'https://{PFP_ROUTE_HOST}')
        assert lookup_local_image(PFP_ROUTE_HOST, path) is not None


def test_selected_account_lookup_uses_only_the_provided_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selected credentials, not browser discovery, resolve me."""
    class Cookies:
        def __init__(self) -> None:
            self.values: list[tuple[str, str, str]] = []

        def set(self, name: str, value: str, *, domain: str) -> None:
            self.values.append((name, value, domain))

    class Session:
        trust_env = True
        proxies: dict[str, str] = {'http': 'proxy'}
        cookies = Cookies()

        def __enter__(self) -> Session:
            return self

        def __exit__(self, *_args: object) -> None:
            return

        def get(self, url: str, *, timeout: int) -> object:
            assert url == 'https://users.roblox.com/v1/users/authenticated'
            assert timeout == 10
            assert self.trust_env is False
            assert self.proxies == {}
            assert self.cookies.values == [('.ROBLOSECURITY', 'chosen-token', '.roblox.com')]
            return SimpleNamespace(status_code=200, json=lambda: {'id': 987654})

    monkeypatch.setattr(pfp_module.requests, 'Session', Session)
    assert pfp_module._selected_account_user_id('chosen-token') == '987654'  # pyright: ignore[reportPrivateUsage]
