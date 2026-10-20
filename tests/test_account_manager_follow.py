"""Following another user from the account manager."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

import pytest

from fleasion.gui import rando_stuff_tab as tab


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self) -> dict:
        return self._payload


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
def test_profile_links_and_ids_resolve_without_a_lookup(monkeypatch, target, expected):
    monkeypatch.setattr(tab._requests, 'post', lambda *_a, **_k: pytest.fail('no lookup'))

    assert tab._resolve_user_id(target) == expected


def test_usernames_are_looked_up(monkeypatch):
    calls = []

    def post(url, json, timeout):
        calls.append((url, json))
        return _Response(200, {'data': [{'id': 1, 'name': 'Roblox'}]})

    monkeypatch.setattr(tab._requests, 'post', post)

    assert tab._resolve_user_id('@roblox') == (1, 'Roblox')
    assert calls == [
        (
            'https://users.roblox.com/v1/usernames/users',
            {'usernames': ['roblox'], 'excludeBannedUsers': True},
        )
    ]


def test_unknown_usernames_do_not_resolve(monkeypatch):
    monkeypatch.setattr(tab._requests, 'post', lambda *_a, **_k: _Response(200, {'data': []}))

    assert tab._resolve_user_id('nobody') is None
    assert tab._resolve_user_id('  ') is None


def test_presence_retries_with_the_csrf_token(monkeypatch):
    sent_headers = []
    responses = [
        _Response(403, headers={'x-csrf-token': 'token'}),
        _Response(200, {'userPresences': [{'userPresenceType': 2}]}),
    ]

    def post(_url, headers, json, timeout):
        sent_headers.append(dict(headers))
        assert json == {'userIds': [42]}
        return responses.pop(0)

    monkeypatch.setattr(tab._requests, 'post', post)

    assert tab._get_user_presence_type(42, 'cookie') == tab._PRESENCE_IN_GAME
    assert 'X-CSRF-TOKEN' not in sent_headers[0]
    assert sent_headers[1]['X-CSRF-TOKEN'] == 'token'


def test_follow_uri_requests_follow_user():
    uri = tab._build_auth_ticket_follow_uri(
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
