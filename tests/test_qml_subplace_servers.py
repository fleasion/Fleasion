from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QCoreApplication, QObject, QUrl, Slot
from PySide6.QtQml import QQmlComponent, QQmlEngine

from fleasion.qml_api.subplaces import (
    RobloxPlacesClient,
    SubplaceSettingsStore,
    SubplacesApi,
    build_place_launch_uri,
)


class Response:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class AppController(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.copied: list[str] = []

    @Slot(str)
    def copyText(self, value: str) -> None:  # noqa: N802
        self.copied.append(value)


def wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        QCoreApplication.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out waiting for public server request')
        time.sleep(0.005)


def test_public_server_client_builds_paginated_roblox_request():
    requested: list[str] = []

    def fetch(url: str) -> Response:
        requested.append(url)
        return Response(
            {
                'data': [
                    {
                        'id': 'job-1',
                        'playing': 4,
                        'maxPlayers': 12,
                        'ping': 38,
                        'fps': 59.9,
                    }
                ],
                'nextPageCursor': 'cursor-2',
            }
        )

    page = RobloxPlacesClient(fetch).public_servers(
        '101', cursor='cursor-1', sort_order='Desc', limit=25
    )
    query = parse_qs(urlparse(requested[0]).query)

    assert query == {
        'limit': ['25'],
        'sortOrder': ['Desc'],
        'excludeFullGames': ['false'],
        'cursor': ['cursor-1'],
    }
    assert page['nextCursor'] == 'cursor-2'
    assert page['servers'][0]['id'] == 'job-1'


def test_public_server_client_surfaces_rate_limiting_without_blocking():
    client = RobloxPlacesClient(lambda _url: Response({}, status_code=429))

    try:
        client.public_servers('101')
    except RuntimeError as exc:
        assert 'rate-limiting' in str(exc)
    else:
        raise AssertionError('Expected Roblox rate limiting to be reported')


class ServerClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int]] = []

    def public_servers(
        self,
        place_id: str,
        *,
        cursor: str,
        sort_order: str,
        limit: int,
    ) -> dict[str, Any]:
        self.calls.append((place_id, cursor, sort_order, limit))
        if sort_order == 'Desc':
            return {
                'placeId': place_id,
                'servers': [
                    {
                        'id': 'job-busy',
                        'playing': 10,
                        'maxPlayers': 12,
                        'ping': 55,
                        'fps': 60,
                    }
                ],
                'nextCursor': '',
            }
        if cursor == 'next':
            return {
                'placeId': place_id,
                'servers': [
                    {'id': 'job-b', 'playing': 1, 'maxPlayers': 10, 'ping': 20},
                    {'id': 'job-c', 'playing': 5, 'maxPlayers': 10, 'ping': 30},
                ],
                'nextCursor': '',
            }
        return {
            'placeId': place_id,
            'servers': [
                {'id': 'job-a', 'playing': 2, 'maxPlayers': 10, 'ping': 80},
                {'id': 'job-b', 'playing': 1, 'maxPlayers': 10, 'ping': 20},
            ],
            'nextCursor': 'next',
        }


def test_subplaces_api_paginates_sorts_and_joins_public_server(tmp_path: Path):
    client = ServerClient()
    launched: list[str] = []
    controller = SubplacesApi(
        client=client,
        settings_store=SubplaceSettingsStore(tmp_path / 'settings.json'),
        launcher=lambda target: launched.append(target) or True,
    )  # pyright: ignore[reportCallIssue, reportArgumentType]
    try:
        assert controller.openServers('101', 'Dungeon')
        wait_until(lambda: not controller.serverTask.busy)
        assert controller.serverPlaceName == 'Dungeon'
        assert controller.serverHasMore
        assert controller.serverPageCount == 1
        assert [controller.serverModel.get(index)['jobId'] for index in range(2)] == [
            'job-b',
            'job-a',
        ]

        controller.setServerSortMode('pingDescending')
        assert [controller.serverModel.get(index)['jobId'] for index in range(2)] == [
            'job-a',
            'job-b',
        ]
        assert controller.loadMoreServers()
        wait_until(lambda: not controller.serverTask.busy)
        assert controller.serverCount == 3
        assert controller.serverPageCount == 2
        assert not controller.serverHasMore
        assert [controller.serverModel.get(index)['jobId'] for index in range(3)] == [
            'job-a',
            'job-c',
            'job-b',
        ]

        assert controller.joinServer('job-c')
        assert launched == [build_place_launch_uri('101', 'job-c')]

        controller.setServerSortMode('playersDescending')
        wait_until(lambda: not controller.serverTask.busy)
        assert controller.serverModel.get(0)['jobId'] == 'job-busy'
        assert client.calls[-1] == ('101', '', 'Desc', 25)
    finally:
        controller.shutdown()


def test_subplaces_api_retains_server_error_for_empty_state(tmp_path: Path):
    class FailingClient:
        @staticmethod
        def public_servers(*_args, **_kwargs):
            raise RuntimeError('service unavailable')

    controller = SubplacesApi(
        client=FailingClient(),
        settings_store=SubplaceSettingsStore(tmp_path / 'settings.json'),
        launcher=lambda _target: True,
    )  # pyright: ignore[reportCallIssue, reportArgumentType]
    try:
        assert controller.openServers('101', 'Dungeon')
        wait_until(lambda: not controller.serverTask.busy)
        assert controller.serverCount == 0
        assert 'service unavailable' in controller.serverError
        assert controller.serverError == controller.serverStatusText
    finally:
        controller.shutdown()


def test_public_servers_qml_dialog_instantiates_populated_delegate(tmp_path: Path):
    client = ServerClient()
    controller = SubplacesApi(
        client=client,
        settings_store=SubplaceSettingsStore(tmp_path / 'settings.json'),
        launcher=lambda _target: True,
    )  # pyright: ignore[reportCallIssue, reportArgumentType]
    app_controller = AppController()
    qml_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion' / 'qml'
    engine = QQmlEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    engine.addImportPath(str(qml_root))
    try:
        controller._server_place_id = '101'
        controller._server_place_name = 'Dungeon'
        controller._apply_server_result(
            {
                'page': {
                    'placeId': '101',
                    'servers': [
                        {
                            'id': 'job-1',
                            'playing': 4,
                            'maxPlayers': 12,
                            'ping': 38,
                            'fps': 60,
                        }
                    ],
                    'nextCursor': '',
                },
                'reset': True,
                'sortOrder': 'Asc',
            }
        )
        component = QQmlComponent(
            engine,
            QUrl.fromLocalFile(
                str(qml_root / 'screens' / 'subplaces' / 'PublicServersDialog.qml')
            ),
        )
        instance = component.createWithInitialProperties(
            {'controller': controller, 'appController': app_controller}
        )
        QCoreApplication.processEvents()

        assert instance is not None, '\n'.join(error.toString() for error in component.errors())
        assert not [warning for warning in warnings if '/src/fleasion/qml/' in warning]
        instance.deleteLater()
    finally:
        controller.shutdown()
        engine.deleteLater()
        QCoreApplication.processEvents()
