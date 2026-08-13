from __future__ import annotations

import json
from types import SimpleNamespace

from fleasion.qml_api.subplace_join import SubplaceJoinCoordinator


class SessionStub:
    def __init__(self) -> None:
        self.trust_env = True
        self.headers: dict[str, str] = {}
        self.cookies = SimpleNamespace(set=self._set_cookie)
        self.cookie_args: tuple[object, ...] = ()
        self.post_call: tuple[str, dict[str, object], int, bool] | None = None

    def _set_cookie(self, *args: object, **kwargs: object) -> None:
        self.cookie_args = (*args, kwargs)

    def post(self, url: str, **kwargs: object) -> SimpleNamespace:
        payload = kwargs['json']
        assert isinstance(payload, dict)
        timeout = kwargs['timeout']
        allow_redirects = kwargs['allow_redirects']
        assert isinstance(timeout, int)
        assert isinstance(allow_redirects, bool)
        self.post_call = (url, payload, timeout, allow_redirects)
        return SimpleNamespace(status_code=200, json=lambda: {'status': 2})


class RequestStub:
    def __init__(self) -> None:
        self.url = 'https://gamejoin.roblox.com/v1/join-game'
        self.raw_content = json.dumps(
            {'placeId': 202, 'gameJoinAttemptId': 'attempt'}
        ).encode()

    @property
    def pretty_url(self) -> str:
        return self.url

    @property
    def content(self) -> bytes:
        return self.raw_content


def test_subplace_join_preseeds_root_and_rewrites_job_instance() -> None:
    session = SessionStub()
    coordinator = SubplaceJoinCoordinator(session_factory=lambda: session)

    assert coordinator.prepare('202', '101', 'job-id', 'cookie')
    assert session.trust_env is False
    assert session.cookie_args[0:2] == ('.ROBLOSECURITY', 'cookie')
    assert session.post_call is not None
    assert session.post_call[0] == 'https://gamejoin.roblox.com/v1/join-game'
    assert session.post_call[1]['placeId'] == 101
    assert session.post_call[1]['isTeleport'] is True
    assert session.post_call[3] is False

    flow = SimpleNamespace(request=RequestStub())
    coordinator.request(flow)
    payload = json.loads(flow.request.content)
    assert flow.request.url == 'https://gamejoin.roblox.com/v1/join-game-instance'
    assert payload['gameId'] == 'job-id'
    assert payload['isTeleport'] is True


def test_subplace_join_ignores_unrelated_attempt_after_first_request() -> None:
    coordinator = SubplaceJoinCoordinator()
    coordinator.arm('job-id')
    first = SimpleNamespace(request=RequestStub())
    coordinator.request(first)
    second_request = RequestStub()
    second_request.raw_content = json.dumps(
        {'placeId': 202, 'gameJoinAttemptId': 'other'}
    ).encode()
    second = SimpleNamespace(request=second_request)

    coordinator.request(second)

    assert second.request.url.endswith('/v1/join-game')
    assert 'gameId' not in json.loads(second.request.content)
