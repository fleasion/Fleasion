from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

from fleasion.qml_api.subplace_join import SubplaceJoinCoordinator
from fleasion.qml_api.subplaces import (
    SubplaceSettingsStore,
    SubplacesApi,
    build_place_launch_uri,
)


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


def test_subplace_launch_preparation_does_not_block_qml_thread(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    prepared: list[tuple[str, str, str]] = []
    launched: list[str] = []

    class CoordinatorStub:
        def prepare(
            self,
            place_id: str,
            root_place_id: str,
            job_id: str,
            _cookie: str | None,
        ) -> bool:
            prepared.append((place_id, root_place_id, job_id))
            entered.set()
            release.wait(1)
            return True

        def cancel(self) -> None:
            release.set()

    controller = SubplacesApi(
        client=SimpleNamespace(),
        settings_store=SubplaceSettingsStore(tmp_path / 'settings.json'),
        launcher=lambda target: launched.append(target) or True,
        join_coordinator=CoordinatorStub(),
    )  # pyright: ignore[reportArgumentType, reportCallIssue]
    try:
        assert controller.launch('202', 'job-id', '101')
        assert entered.wait(0.5)
        assert controller.launchTask.busy
        assert launched == []

        release.set()
        deadline = time.monotonic() + 1
        while controller.launchTask.busy and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.005)

        assert not controller.launchTask.busy
        assert prepared == [('202', '101', 'job-id')]
        assert launched == [build_place_launch_uri('202')]
    finally:
        controller.shutdown()
