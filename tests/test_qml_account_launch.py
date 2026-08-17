from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import unquote

import pytest
from PySide6.QtCore import QCoreApplication

from fleasion.qml_api.account_store import AccountStore
from fleasion.qml_api.roblox_launch import (
    AccountLaunchCancelled,
    AccountLauncher,
    ResolvedLaunchRequest,
    RobloxAccountClient,
    RobloxTarget,
    normalize_job_id,
    parse_account_launch_request,
    parse_roblox_target,
)
from fleasion.qml_api.subplace_join import SubplaceJoinCoordinator
from fleasion.qml_api.utilities import UtilitiesApi


class _CookieJar:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def set(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


class _Response:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        text: str = '',
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload or {}
        self.content = text.encode() if text else json.dumps(self._payload).encode()
        self.closed = False

    def iter_content(self, chunk_size: int) -> list[bytes]:
        return [
            self.content[index : index + chunk_size]
            for index in range(0, len(self.content), chunk_size)
        ]

    def json(self) -> dict[str, object]:
        return self._payload

    def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self) -> None:
        self.trust_env = True
        self.proxies: dict[str, str] = {'https': 'environment'}
        self.headers: dict[str, str] = {}
        self.cookies = _CookieJar()
        self.post_responses: list[_Response] = []
        self.get_responses: list[_Response] = []
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(('POST', url, kwargs))
        return self.post_responses.pop(0)

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(('GET', url, kwargs))
        return self.get_responses.pop(0)


def test_account_target_parser_accepts_place_game_private_and_share_links() -> None:
    assert parse_roblox_target('001818') == RobloxTarget(place_id='1818')
    assert parse_roblox_target('www.roblox.com/games/1818/Classic-Crossroads') == RobloxTarget(
        place_id='1818'
    )
    assert parse_roblox_target(
        'https://www.roblox.com/games/1818/Game?privateServerLinkCode=link-123'
    ) == RobloxTarget(place_id='1818', link_code='link-123')
    assert parse_roblox_target(
        'https://www.roblox.com/share?code=share-123&type=Server'
    ) == RobloxTarget(share_code='share-123', share_type='Server')
    assert normalize_job_id('prefix 00000000-0000-0000-0000-000000000001 suffix') == (
        '00000000-0000-0000-0000-000000000001'
    )


@pytest.mark.parametrize(
    'target',
    (
        'http://www.roblox.com/games/1818/Game',
        'https://roblox.com.evil.example/games/1818/Game',
        'https://user@www.roblox.com/games/1818/Game',
        'https://www.roblox.com:444/games/1818/Game',
    ),
)
def test_account_target_parser_rejects_nonofficial_boundaries(target: str) -> None:
    with pytest.raises(ValueError):
        parse_roblox_target(target)


def test_account_launch_request_supports_root_subplace_and_rejects_ambiguous_combos() -> None:
    request = parse_account_launch_request(
        'https://www.roblox.com/games/537413528/Build-A-Boat',
        'job-id',
        'https://www.roblox.com/games/1930863474/Subplace',
    )

    assert request.target.place_id == '537413528'
    assert request.subplace_id == '1930863474'
    assert request.job_id == 'job-id'
    with pytest.raises(ValueError, match='cannot be combined'):
        parse_account_launch_request(
            'https://www.roblox.com/games/1818/Game?privateServerLinkCode=link',
            'job-id',
        )


def test_share_resolution_is_bounded_to_official_https_without_redirects() -> None:
    session = _Session()
    session.post_responses.extend(
        [
            _Response(403, headers={'x-csrf-token': 'csrf'}),
            _Response(
                200,
                payload={
                    'privateServerInviteData': {
                        'placeId': 1818,
                        'privateServerLinkCode': 'link-123',
                    }
                },
            ),
        ]
    )
    client = RobloxAccountClient(session_factory=lambda: session)  # pyright: ignore[reportArgumentType]

    resolved = client.resolve_share_target(
        'cookie-secret',
        RobloxTarget(share_code='share-123'),
    )

    assert resolved == RobloxTarget(place_id='1818', link_code='link-123')
    assert session.trust_env is False
    assert session.proxies == {}
    assert session.cookies.calls == [
        (
            ('.ROBLOSECURITY', 'cookie-secret'),
            {'domain': '.roblox.com', 'secure': True},
        )
    ]
    assert [call[1] for call in session.calls] == [
        'https://apis.roblox.com/sharelinks/v1/resolve-link',
        'https://apis.roblox.com/sharelinks/v1/resolve-link',
    ]
    assert all(call[2]['allow_redirects'] is False for call in session.calls)


def test_private_server_access_resolution_keeps_link_code_out_of_url_host() -> None:
    session = _Session()
    session.get_responses.append(_Response(200, {'accessCode': 'access-123'}))
    client = RobloxAccountClient(session_factory=lambda: session)  # pyright: ignore[reportArgumentType]

    access_code = client.private_server_access_code('cookie-secret', '1818', 'link-123')

    assert access_code == 'access-123'
    method, url, options = session.calls[0]
    assert method == 'GET'
    assert url == 'https://games.roblox.com/v1/private-servers'
    assert options['params'] == {'serverLinkCode': 'link-123'}
    assert options['allow_redirects'] is False


class _ResolvedClient:
    def __init__(self, resolved: ResolvedLaunchRequest) -> None:
        self.resolved = resolved
        self.calls: list[str] = []

    def resolve_launch_request(
        self,
        _cookie: str,
        _request: object,
        _cancel_event: object,
    ) -> ResolvedLaunchRequest:
        self.calls.append('resolve')
        return self.resolved

    def private_server_access_code(
        self,
        _cookie: str,
        _place_id: str,
        _link_code: str,
        _cancel_event: object,
    ) -> str:
        self.calls.append('access')
        return 'access-123'

    def authentication_ticket(self, _cookie: str) -> str:
        self.calls.append('ticket')
        return 'ticket-123'


class _Coordinator:
    def __init__(self) -> None:
        self.prepared: list[tuple[str, str, str, str | None]] = []
        self.cancelled = False

    def prepare(
        self,
        place_id: str,
        root_place_id: str,
        job_id: str,
        cookie: str | None,
    ) -> bool:
        self.prepared.append((place_id, root_place_id, job_id, cookie))
        return True

    def cancel(self) -> None:
        self.cancelled = True


def test_account_launcher_preserves_job_and_distinct_subplace_handoff() -> None:
    client = _ResolvedClient(
        ResolvedLaunchRequest(
            root_place_id='101',
            launch_place_id='202',
            job_id='00000000-0000-0000-0000-000000000001',
        )
    )
    coordinator = _Coordinator()
    launched: list[str] = []
    launcher = AccountLauncher(
        cast('Any', client),
        lambda target: launched.append(target) or True,
    )

    assert launcher.launch_request(
        'cookie-secret',
        parse_account_launch_request('101', '00000000-0000-0000-0000-000000000001', '202'),
        join_coordinator=coordinator,
    )

    assert coordinator.prepared == [
        ('202', '101', '00000000-0000-0000-0000-000000000001', 'cookie-secret')
    ]
    decoded = unquote(launched[0])
    assert 'request=RequestGameJob' in decoded
    assert 'placeId=202' in decoded
    assert 'gameId=00000000-0000-0000-0000-000000000001' in decoded


def test_account_launcher_preserves_private_server_subplace_handoff() -> None:
    client = _ResolvedClient(
        ResolvedLaunchRequest(
            root_place_id='101',
            launch_place_id='202',
            link_code='link-123',
        )
    )
    coordinator = _Coordinator()
    launched: list[str] = []
    launcher = AccountLauncher(
        cast('Any', client),
        lambda target: launched.append(target) or True,
    )

    assert launcher.launch_request(
        'cookie-secret',
        parse_account_launch_request('101', subplace='202'),
        join_coordinator=coordinator,
    )

    assert coordinator.prepared == [('202', '101', '', 'cookie-secret')]
    decoded = unquote(launched[0])
    assert 'request=RequestPrivateGame' in decoded
    assert 'placeId=202' in decoded
    assert 'accessCode=access-123' in decoded
    assert 'linkCode=link-123' in decoded


def _flow(path: str, place_id: object, attempt_id: str = 'attempt') -> Any:
    body = {'placeId': place_id, 'gameJoinAttemptId': attempt_id}
    request = SimpleNamespace(
        url=f'https://gamejoin.roblox.com{path}',
        pretty_url=f'https://gamejoin.roblox.com{path}',
        raw_content=json.dumps(body).encode(),
    )
    request.content = request.raw_content
    return SimpleNamespace(request=request, response=None)


def test_join_coordinator_matches_place_before_consuming_private_server_arm() -> None:
    coordinator = SubplaceJoinCoordinator()
    coordinator.arm(place_id='202')
    unrelated = _flow('/v1/join-private-game', 999)
    coordinator.request(unrelated)
    matching = _flow('/v1/join-private-game', '0202')
    coordinator.request(matching)

    assert 'isTeleport' not in json.loads(unrelated.request.raw_content)
    assert json.loads(matching.request.raw_content)['isTeleport'] is True
    assert matching.request.url.endswith('/v1/join-private-game')


class _Config:
    multi_instance_launching = False
    username_spoofer: dict[str, object] = {}
    subplace_blacklist: list[str] = []
    subplace_blacklist_mode = 'block'


class _MultiInstance:
    supported = False

    def start(self) -> bool:
        return False

    def stop(self) -> None:
        return


class _AccountClient:
    def validate(self, _cookie: str) -> dict[str, str]:
        return {'username': 'Builder', 'userId': '42', 'cookie': 'cookie-secret'}


class _BlockingLauncher:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.cookie = ''

    def launch_request(
        self,
        cookie: str,
        _request: object,
        *,
        join_coordinator: object,
        cancel_event: threading.Event,
    ) -> bool:
        del join_coordinator
        self.cookie = cookie
        self.entered.set()
        while not cancel_event.wait(0.005):
            pass
        raise AccountLaunchCancelled('Account launch cancelled')


def test_utilities_account_launch_is_async_cancellable_and_cookie_safe(tmp_path: Path) -> None:
    application = QCoreApplication.instance() or QCoreApplication([])
    store = AccountStore(tmp_path / 'accounts.json', tmp_path / 'accounts.key')
    store.save([store.create('Builder', 'cookie-secret', '42')])
    launcher = _BlockingLauncher()
    notifications: list[tuple[str, str, str]] = []
    errors: list[str] = []
    api = UtilitiesApi(  # pyright: ignore[reportArgumentType, reportCallIssue]
        _Config(),
        account_store=store,
        account_client=_AccountClient(),
        account_launcher=launcher,
        multi_instance=_MultiInstance(),
    )
    api.notificationRequested.connect(
        lambda title, message, status: notifications.append((title, message, status))
    )
    api.errorOccurred.connect(errors.append)
    try:
        started_at = time.monotonic()
        assert api.launchAccount(
            0,
            'https://www.roblox.com/games/101/Game',
            'job-id',
            '202',
        )
        assert time.monotonic() - started_at < 0.2
        assert launcher.entered.wait(0.5)
        assert api.launchTask.busy

        api.cancelAccountLaunch()
        deadline = time.monotonic() + 1
        while api.launchTask.busy and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.005)
        application.processEvents()

        assert not api.launchTask.busy
        assert errors == []
        assert notifications[-1][0] == 'Launch cancelled'
        assert 'cookie-secret' not in repr(notifications)
        role_names = {
            bytes(name).decode() for name in api.accountsModel.roleNames().values()  # type: ignore[attr-defined]
        }
        assert 'cookie' not in role_names
        assert launcher.cookie == 'cookie-secret'
    finally:
        api.shutdown()


def test_account_validation_task_never_emits_plaintext_cookie(tmp_path: Path) -> None:
    application = QCoreApplication.instance() or QCoreApplication([])
    store = AccountStore(tmp_path / 'accounts.json', tmp_path / 'accounts.key')
    api = UtilitiesApi(  # pyright: ignore[reportArgumentType, reportCallIssue]
        _Config(),
        account_store=store,
        account_client=_AccountClient(),
        account_launcher=_BlockingLauncher(),
        multi_instance=_MultiInstance(),
    )
    results: list[object] = []
    api.accountTask.succeeded.connect(results.append)
    try:
        assert api.addAccount('cookie-secret')
        deadline = time.monotonic() + 1
        while api.accountTask.busy and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.005)
        application.processEvents()

        assert results
        assert 'cookie-secret' not in repr(results)
        stored = store.load()
        assert len(stored) == 1
        assert store.cookie(stored[0]) == 'cookie-secret'
    finally:
        api.shutdown()
