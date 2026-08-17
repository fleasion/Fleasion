from __future__ import annotations

import json
import threading
import time
from typing import Any

from PySide6.QtCore import QCoreApplication, QUrl

from fleasion.qml_api.proxy import ProxyApi


class ProxyStub:
    def __init__(self) -> None:
        self.is_running = False
        self.traffic: list[dict[str, Any]] = [self.entry(1)]
        self.actions: list[str] = []
        self.intercept_match = ''
        self.capture_all = False
        self.pending: dict[tuple[int, str], bytes] = {(1, 'request'): b'GET /one HTTP/1.1'}
        self.rules: list[dict[str, Any]] = []

    @staticmethod
    def entry(request_id: int, *, status: int | None = None) -> dict[str, Any]:
        return {
            'id': request_id,
            'time': 1_660_000_000 + request_id,
            'method': 'GET',
            'host': 'assetdelivery.roblox.com',
            'port': 443,
            'path': f'/v1/asset?id={request_id}',
            'status': status,
            'size': 100,
            'ms': None,
            'request_raw': f'GET /v1/asset?id={request_id} HTTP/1.1'.encode(),
            'response_raw': b'',
            'pending_stage': 'request' if request_id == 1 else None,
            'was_intercepted': request_id == 1,
        }

    def get_env_proxy_traffic(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self.traffic]

    def clear_env_proxy_traffic(self) -> None:
        self.traffic.clear()

    def start(self) -> None:
        self.actions.append('start')
        self.is_running = True

    def stop(self) -> None:
        self.actions.append('stop')
        self.is_running = False

    def set_env_proxy_intercept_match(self, value: str) -> None:
        self.intercept_match = value

    def set_env_proxy_intercept_all(self, enabled: bool) -> None:
        self.capture_all = enabled

    def get_env_proxy_pending_intercepts(self) -> list[tuple[int, str]]:
        return list(self.pending)

    def get_env_proxy_pending_data(self, request_id: int, stage: str) -> bytes | None:
        return self.pending.get((request_id, stage))

    def submit_env_proxy_pending(
        self,
        request_id: int,
        stage: str,
        action: str,
        edited_text: str | None,
    ) -> bool:
        key = (request_id, stage)
        if key not in self.pending:
            return False
        self.actions.append(f'{action}:{request_id}:{stage}:{edited_text or ""}')
        self.pending.pop(key)
        self.traffic[0]['pending_stage'] = None
        return True

    def replay_env_proxy_request(self, request_id: int, edited_text: str | None) -> bool:
        self.actions.append(f'replay:{request_id}:{edited_text or ""}')
        return True

    def format_env_proxy_request_preview(self, entry: dict[str, Any]) -> str:
        request = bytes(entry.get('request_raw') or b'').decode()
        return f'{request}\r\nAuthorization: Bearer secret\r\nCookie: .ROBLOSECURITY=secret'

    def format_env_proxy_response_preview(self, entry: dict[str, Any]) -> str:
        return bytes(entry.get('response_raw') or b'').decode()

    def get_auto_replace_rules(self) -> list[dict[str, Any]]:
        return [dict(rule) for rule in self.rules]

    def set_auto_replace_rules(self, rules: list[dict[str, Any]]) -> None:
        self.rules = [dict(rule) for rule in rules]


class PreserveConfigStub:
    def __init__(self, enabled: bool) -> None:
        self.proxy_traffic_preserve = enabled


def wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        QCoreApplication.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out waiting for Qt task completion')
        time.sleep(0.005)


def test_proxy_traffic_model_diffs_by_stable_request_id():
    proxy = ProxyStub()
    controller = ProxyApi(proxy)  # pyright: ignore[reportCallIssue]
    resets: list[None] = []
    inserts: list[tuple[int, int]] = []
    removals: list[tuple[int, int]] = []
    changes: list[None] = []
    controller.model.modelReset.connect(lambda: resets.append(None))
    controller.model.rowsInserted.connect(
        lambda _parent, first, last: inserts.append((first, last))
    )
    controller.model.rowsRemoved.connect(
        lambda _parent, first, last: removals.append((first, last))
    )
    controller.model.dataChanged.connect(lambda *_args: changes.append(None))

    proxy.traffic.append(proxy.entry(2))
    controller.refresh()
    assert controller.model.count == 2
    assert inserts == [(1, 1)]
    assert resets == []

    proxy.traffic[0]['status'] = 204
    proxy.traffic[0]['pending_stage'] = None
    proxy.pending.clear()
    controller.refresh()
    assert controller.model.get(0)['status'] == '204'
    assert changes
    assert resets == []

    proxy.traffic.pop(0)
    proxy.traffic.append(proxy.entry(3))
    controller.refresh()
    assert [controller.model.get(row)['requestId'] for row in range(2)] == [2, 3]
    assert removals == [(0, 0)]
    assert inserts[-1] == (1, 1)
    assert resets == []
    controller.shutdown()


def test_proxy_inspector_actions_and_interception_controls():
    proxy = ProxyStub()
    controller = ProxyApi(proxy)  # pyright: ignore[reportCallIssue]

    details = controller.trafficEntry('1')
    assert details['pendingStage'] == 'request'
    assert details['requestText'] == (
        'GET /one HTTP/1.1\r\nAuthorization: <redacted>\r\nCookie: <redacted>'
    )
    assert controller.pendingCount == 1

    controller.setIntercept('asset', True)
    assert proxy.intercept_match == 'asset'
    assert proxy.capture_all
    assert controller.resolve(1, 'request', 'forward', 'GET /edited HTTP/1.1')
    assert 'forward:1:request:GET /edited HTTP/1.1' in proxy.actions
    assert controller.replay(1, 'GET /replay HTTP/1.1')
    assert 'replay:1:GET /replay HTTP/1.1' in proxy.actions
    controller.shutdown()


def test_proxy_auto_replace_rule_crud_uses_backend_schema():
    proxy = ProxyStub()
    controller = ProxyApi(proxy)  # pyright: ignore[reportCallIssue]

    assert controller.addRule(
        True,
        'request',
        'header',
        'x-test',
        'enabled',
        'apis.roblox.com',
        '/v1/',
    )
    assert proxy.rules == [
        {
            'enabled': True,
            'direction': 'request',
            'type': 'header',
            'match': 'x-test',
            'replacement': 'enabled',
            'host_filter': 'apis.roblox.com',
            'path_filter': '/v1/',
        }
    ]
    assert controller.rulesModel.get(0)['typeLabel'] == 'Header'
    assert controller.rulesModel.get(0)['ruleEnabled'] is True

    assert controller.setRuleEnabled('0', False)
    assert not proxy.rules[0]['enabled']
    assert controller.duplicateRule('0')
    assert len(proxy.rules) == 2
    assert controller.updateRule('1', True, 'both', 'plain', 'before', 'after', '', '')
    assert proxy.rules[1]['match'] == 'before'
    assert controller.deleteRule('0')
    assert len(proxy.rules) == 1
    controller.shutdown()


def test_proxy_lifecycle_operations_run_without_blocking_qt_thread():
    proxy = ProxyStub()
    controller = ProxyApi(proxy)  # pyright: ignore[reportCallIssue]

    assert controller.start()
    wait_until(lambda: not controller.lifecycleTask.busy)
    assert controller.running

    assert controller.restart()
    wait_until(lambda: not controller.lifecycleTask.busy)
    assert controller.running

    assert controller.stop()
    wait_until(lambda: not controller.lifecycleTask.busy)
    assert not controller.running
    assert proxy.actions == ['start', 'stop', 'start', 'stop']
    controller.shutdown()


def test_proxy_lifecycle_queue_keeps_only_the_latest_requested_action():
    class BlockingStartProxy(ProxyStub):
        def __init__(self) -> None:
            super().__init__()
            self.start_entered = threading.Event()
            self.allow_start = threading.Event()

        def start(self) -> None:
            self.actions.append('start')
            self.start_entered.set()
            assert self.allow_start.wait(1.0)
            self.is_running = True

    proxy = BlockingStartProxy()
    controller = ProxyApi(proxy)  # pyright: ignore[reportCallIssue]
    assert controller.start()
    assert proxy.start_entered.wait(1.0)

    assert controller.restart()
    assert controller.stop()
    proxy.allow_start.set()
    wait_until(lambda: not controller.lifecycleTask.busy and not controller.lifecycleAction)

    assert proxy.actions == ['start', 'stop']
    assert not proxy.is_running
    controller.shutdown()


def test_proxy_shutdown_cannot_restart_service_after_stop_begins():
    class BlockingProxy(ProxyStub):
        def __init__(self) -> None:
            super().__init__()
            self.stop_entered = threading.Event()
            self.allow_stop = threading.Event()
            self.starts_after_stop = 0

        def stop(self) -> None:
            self.actions.append('stop')
            self.stop_entered.set()
            assert self.allow_stop.wait(1.0)
            self.is_running = False

        def start(self) -> None:
            self.starts_after_stop += 1
            super().start()

    proxy = BlockingProxy()
    controller = ProxyApi(proxy)  # pyright: ignore[reportCallIssue]
    assert controller.restart()
    assert proxy.stop_entered.wait(1.0)
    assert controller.start()
    threading.Timer(0.05, proxy.allow_stop.set).start()

    controller.shutdown()

    assert proxy.starts_after_stop == 0
    assert not proxy.is_running
    assert not controller.start()


def test_proxy_shutdown_linearizes_with_restart_start_gate():
    class ContentionGate:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.contended = threading.Event()

        def __enter__(self) -> ContentionGate:
            if not self._lock.acquire(blocking=False):
                self.contended.set()
                assert self._lock.acquire(timeout=1.0)
            return self

        def __exit__(self, *_args: object) -> None:
            self._lock.release()

    class LookupBlockingProxy(ProxyStub):
        def __init__(self) -> None:
            super().__init__()
            self.block_start_lookup = False
            self.start_lookup_entered = threading.Event()
            self.shutdown_active = lambda: False
            self.gate_contended = threading.Event()
            self.starts_during_shutdown = 0

        def __getattribute__(self, name: str) -> Any:
            if name == 'start' and object.__getattribute__(self, 'block_start_lookup'):
                object.__setattr__(self, 'block_start_lookup', False)
                object.__getattribute__(self, 'start_lookup_entered').set()
                deadline = time.monotonic() + 1.0
                shutdown_active = object.__getattribute__(self, 'shutdown_active')
                gate_contended = object.__getattribute__(self, 'gate_contended')
                while not shutdown_active() and not gate_contended.wait(0.005):
                    assert time.monotonic() < deadline
            return super().__getattribute__(name)

        def start(self) -> None:
            if self.shutdown_active():
                self.starts_during_shutdown += 1
            super().start()

    proxy = LookupBlockingProxy()
    controller = ProxyApi(proxy)  # pyright: ignore[reportCallIssue]
    gate = ContentionGate()
    controller._lifecycle_gate = gate
    proxy.shutdown_active = controller._shutdown_requested.is_set
    proxy.gate_contended = gate.contended
    proxy.block_start_lookup = True

    assert controller.restart()
    assert proxy.start_lookup_entered.wait(1.0)
    controller.shutdown()

    assert gate.contended.is_set()
    assert proxy.starts_during_shutdown == 0
    assert proxy.actions == ['stop', 'start', 'stop']
    assert not proxy.is_running


def test_proxy_preserve_restores_sanitized_read_only_traffic(tmp_path):
    archive_path = tmp_path / 'traffic.json'
    config = PreserveConfigStub(True)
    proxy = ProxyStub()
    proxy.traffic[0]['request_raw'] = (
        b'GET /one?accessCode=private-code HTTP/1.1\r\n'
        b'Authorization: Bearer bearer-secret\r\n\r\n'
    )
    controller = ProxyApi(  # pyright: ignore[reportCallIssue]
        proxy,
        config_manager=config,  # pyright: ignore[reportArgumentType]
        traffic_archive_path=archive_path,
    )
    controller.shutdown()

    restored_proxy = ProxyStub()
    restored_proxy.traffic = []
    restored = ProxyApi(  # pyright: ignore[reportCallIssue]
        restored_proxy,
        config_manager=config,  # pyright: ignore[reportArgumentType]
        traffic_archive_path=archive_path,
    )
    assert restored.trafficPreserve
    assert restored.preservedCount == 1
    assert restored.model.count == 1
    row = restored.model.get(0)
    assert row['requestId'] == -1
    assert row['archived'] is True
    details = restored.trafficEntry('-1')
    assert details['archived'] is True
    assert details['pending'] is False
    assert 'private-code' not in details['requestText']
    assert 'bearer-secret' not in details['requestText']
    assert not restored.replay(-1, details['requestText'])
    restored.shutdown()


def test_proxy_preserve_checkpoints_live_rows_across_service_restart(tmp_path):
    archive_path = tmp_path / 'traffic.json'
    config = PreserveConfigStub(True)
    proxy = ProxyStub()
    controller = ProxyApi(  # pyright: ignore[reportCallIssue]
        proxy,
        config_manager=config,  # pyright: ignore[reportArgumentType]
        traffic_archive_path=archive_path,
    )

    assert controller.restart()
    wait_until(lambda: not controller.lifecycleTask.busy)
    assert controller.model.count == 1
    assert controller.model.get(0)['requestId'] == -1
    assert controller.model.get(0)['archived'] is True

    proxy.traffic.append(proxy.entry(0, status=201))
    controller.refresh()
    assert [controller.model.get(index)['requestId'] for index in range(2)] == [-1, 0]
    assert controller.model.get(1)['archived'] is False

    assert controller.restart()
    wait_until(lambda: not controller.lifecycleTask.busy)
    assert [controller.model.get(index)['requestId'] for index in range(2)] == [-1, -2]
    assert all(controller.model.get(index)['archived'] for index in range(2))
    controller.shutdown()


def test_proxy_preserve_checkpoint_does_not_block_qt_thread(tmp_path):
    archive_path = tmp_path / 'traffic.json'
    config = PreserveConfigStub(True)
    proxy = ProxyStub()
    controller = ProxyApi(  # pyright: ignore[reportCallIssue]
        proxy,
        config_manager=config,  # pyright: ignore[reportArgumentType]
        traffic_archive_path=archive_path,
    )
    checkpoint_entered = threading.Event()
    allow_checkpoint = threading.Event()
    original_checkpoint = controller._traffic_archive.checkpoint

    def blocking_checkpoint(entries: list[dict[str, Any]]) -> bool:
        checkpoint_entered.set()
        assert allow_checkpoint.wait(1.0)
        return original_checkpoint(entries)

    controller._traffic_archive.checkpoint = blocking_checkpoint  # pyright: ignore[reportAttributeAccessIssue]

    assert controller.restart()
    assert checkpoint_entered.wait(1.0)
    assert controller.lifecycleTask.busy
    controller.refresh()
    assert controller.model.get(0)['requestId'] == 1

    allow_checkpoint.set()
    wait_until(lambda: not controller.lifecycleTask.busy)
    assert controller.model.get(0)['requestId'] == -1
    controller.shutdown()


def test_proxy_preserve_toggle_updates_config_and_removes_archive(tmp_path):
    archive_path = tmp_path / 'traffic.json'
    config = PreserveConfigStub(False)
    proxy = ProxyStub()
    controller = ProxyApi(  # pyright: ignore[reportCallIssue]
        proxy,
        config_manager=config,  # pyright: ignore[reportArgumentType]
        traffic_archive_path=archive_path,
    )

    controller.setTrafficPreserve(True)
    assert config.proxy_traffic_preserve
    assert archive_path.exists()

    controller.setTrafficPreserve(False)
    assert not config.proxy_traffic_preserve
    assert not archive_path.exists()
    assert controller.model.count == 1
    controller.shutdown()


def test_proxy_rules_round_trip_json_file(tmp_path):
    proxy = ProxyStub()
    controller = ProxyApi(proxy)  # pyright: ignore[reportCallIssue]
    source = tmp_path / 'rules.json'
    source.write_text(
        json.dumps(
            {
                'rules': [
                    {
                        'enabled': True,
                        'direction': 'request',
                        'type': 'header',
                        'match': 'X-Test',
                        'replacement': 'ready',
                    }
                ]
            }
        ),
        encoding='utf-8',
    )

    assert controller.importRules(QUrl.fromLocalFile(str(source)).toString())
    assert proxy.rules[0]['type'] == 'header'
    assert proxy.rules[0]['host_filter'] == ''

    destination = tmp_path / 'exported'
    assert controller.exportRules(QUrl.fromLocalFile(str(destination)).toString())
    exported = json.loads(destination.with_suffix('.json').read_text(encoding='utf-8'))
    assert exported == {'rules': proxy.rules}
    controller.shutdown()


def test_proxy_rule_import_rejects_non_object_entries(tmp_path):
    proxy = ProxyStub()
    controller = ProxyApi(proxy)  # pyright: ignore[reportCallIssue]
    source = tmp_path / 'rules.json'
    source.write_text('[{"match": "ok"}, 2]', encoding='utf-8')

    assert not controller.importRules(str(source))
    assert proxy.rules == []
    controller.shutdown()
