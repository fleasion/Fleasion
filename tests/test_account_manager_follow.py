"""Following another user from the account manager."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from fleasion.gui import rando_stuff_tab as tab

if TYPE_CHECKING:
    from collections.abc import Callable


def _resolve_user_id(target: str) -> tuple[int, str] | None:
    callback = cast('Callable[[str], tuple[int, str] | None]', tab.__dict__['_resolve_user_id'])
    return callback(target)


def _get_user_presence_type(user_id: int, cookie: str) -> int | None:
    callback = cast('Callable[[int, str], int | None]', tab.__dict__['_get_user_presence_type'])
    return callback(user_id, cookie)


def _build_follow_uri(ticket: str, user_id: int, **kwargs: object) -> str:
    callback = cast('Callable[..., str]', tab.__dict__['_build_auth_ticket_follow_uri'])
    return callback(ticket, user_id, **kwargs)


def _patch_post(monkeypatch: pytest.MonkeyPatch, post: Callable[..., _Response]) -> None:
    monkeypatch.setattr(tab.__dict__['_requests'], 'post', post)


class _Response:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self) -> dict[str, object]:
        return self._payload


def _no_lookup(*_args: object, **_kwargs: object) -> _Response:
    pytest.fail('no lookup')


def _no_users(*_args: object, **_kwargs: object) -> _Response:
    return _Response(200, {'data': []})


@pytest.mark.parametrize(
    ('target', 'expected'),
    [
        (
            'https://www.roblox.com/users/156/profile',
            (156, 'https://www.roblox.com/users/156/profile'),
        ),
        ('261', (261, '261')),
    ],
)
def test_profile_links_and_ids_resolve_without_a_lookup(
    monkeypatch: pytest.MonkeyPatch, target: str, expected: tuple[int, str]
) -> None:
    _patch_post(monkeypatch, _no_lookup)

    assert _resolve_user_id(target) == expected


def test_usernames_are_looked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def post(url: str, json: dict[str, object], timeout: float) -> _Response:
        del timeout
        calls.append((url, json))
        return _Response(200, {'data': [{'id': 1, 'name': 'Roblox'}]})

    _patch_post(monkeypatch, post)

    assert _resolve_user_id('@roblox') == (1, 'Roblox')
    assert calls == [
        (
            'https://users.roblox.com/v1/usernames/users',
            {'usernames': ['roblox'], 'excludeBannedUsers': True},
        )
    ]


def test_unknown_usernames_do_not_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, _no_users)

    assert _resolve_user_id('nobody') is None
    assert _resolve_user_id('  ') is None


def test_presence_retries_with_the_csrf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_headers: list[dict[str, str]] = []
    responses = [
        _Response(403, headers={'x-csrf-token': 'token'}),
        _Response(200, {'userPresences': [{'userPresenceType': 2}]}),
    ]

    def post(
        _url: str, headers: dict[str, str], json: dict[str, object], timeout: float
    ) -> _Response:
        del timeout
        sent_headers.append(dict(headers))
        assert json == {'userIds': [42]}
        return responses.pop(0)

    _patch_post(monkeypatch, post)

    assert _get_user_presence_type(42, 'cookie') == tab.__dict__['_PRESENCE_IN_GAME']
    assert 'X-CSRF-TOKEN' not in sent_headers[0]
    assert sent_headers[1]['X-CSRF-TOKEN'] == 'token'


def test_follow_uri_requests_follow_user() -> None:
    uri = _build_follow_uri(
        'ticket', 42, tracker_id=12345678901, join_attempt_id='attempt', launch_time_ms=1
    )

    parts = dict(part.split(':', 1) for part in uri.split('+') if ':' in part)
    assert parts['launchmode'] == 'play'
    assert parts['gameinfo'] == 'ticket'
    launcher = urlparse(unquote(parts['placelauncherurl']))
    assert launcher.path == '/Game/PlaceLauncher.ashx'
    assert parse_qs(launcher.query) == {
        'request': ['RequestFollowUser'],
        'browserTrackerId': ['12345678901'],
        'userId': ['42'],
        'joinAttemptId': ['attempt'],
        'joinAttemptOrigin': ['followUser'],
    }
