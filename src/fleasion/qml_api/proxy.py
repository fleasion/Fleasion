"""Proxy lifecycle, traffic inspection, and interception bridge for QML."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtQml import QmlElement

from .models import DictListModel
from .tasks import TaskState

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_SENSITIVE_HEADER_PATTERN: Final = re.compile(
    r'(?im)^(authorization|proxy-authorization|cookie|set-cookie|x-csrf-token):[^\r\n]*'
)


def _redact_sensitive_headers(text: str) -> str:
    """Remove session-bearing header values before showing traffic in QML."""
    return _SENSITIVE_HEADER_PATTERN.sub(lambda match: f'{match.group(1)}: <redacted>', text)


_TRAFFIC_ROLES: Final = (
    'key',
    'requestId',
    'timeText',
    'method',
    'host',
    'path',
    'status',
    'sizeText',
    'durationText',
    'pendingStage',
    'pending',
    'dropped',
    'intercepted',
    'searchText',
)
_RULE_ROLES: Final = (
    'key',
    'ruleEnabled',
    'direction',
    'directionLabel',
    'ruleType',
    'typeLabel',
    'matchText',
    'replacement',
    'hostFilter',
    'pathFilter',
)
_RULE_DIRECTIONS: Final = frozenset({'both', 'request', 'response'})
_RULE_TYPES: Final = frozenset({'plain', 'regex', 'json_path', 'query_param', 'header'})
_MAX_IMPORTED_RULES: Final = 10_000
_MAX_RULE_IMPORT_BYTES: Final = 8 * 1024 * 1024
_DIRECTION_LABELS: Final = {
    'both': 'Both',
    'request': 'Request',
    'response': 'Response',
}
_TYPE_LABELS: Final = {
    'plain': 'Plain text',
    'regex': 'Regular expression',
    'json_path': 'JSON path',
    'query_param': 'Query parameter',
    'header': 'Header',
}
_TUNNEL_NOTE: Final = (
    'No preview is available because this TLS connection was tunneled without decryption.'
)


def _time_text(timestamp: Any) -> str:
    try:
        return datetime.fromtimestamp(float(timestamp)).strftime('%H:%M:%S')
    except (TypeError, ValueError, OSError):
        return ''


def _size_text(size_value: Any) -> str:
    try:
        size = max(0, int(size_value or 0))
    except (TypeError, ValueError):
        size = 0
    if size >= 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} MB'
    if size >= 1024:
        return f'{size / 1024:.1f} KB'
    return f'{size} B'


def _payload_signature(value: Any) -> tuple[int, bytes, bytes]:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        return (0, b'', b'')
    return (len(value), bytes(value[:64]), bytes(value[-64:]))


def _status_text(entry: dict[str, Any]) -> str:
    stage = str(entry.get('pending_stage') or '')
    if stage == 'request':
        return 'Held request'
    if stage == 'response':
        return 'Held response'
    if entry.get('dropped_request') or entry.get('dropped_response'):
        return 'Dropped'
    status = entry.get('status')
    if status is not None:
        return str(status)
    if entry.get('method') == 'CONNECT':
        return 'Tunnel'
    return 'Pending'


def _normalized_rule(rule: dict[str, Any]) -> dict[str, Any]:
    direction = str(rule.get('direction') or 'both')
    rule_type = str(rule.get('type') or 'plain')
    return {
        'enabled': bool(rule.get('enabled', True)),
        'direction': direction if direction in _RULE_DIRECTIONS else 'both',
        'type': rule_type if rule_type in _RULE_TYPES else 'plain',
        'match': str(rule.get('match') or ''),
        'replacement': str(rule.get('replacement') or ''),
        'host_filter': str(rule.get('host_filter') or ''),
        'path_filter': str(rule.get('path_filter') or ''),
    }


@QmlElement
class ProxyApi(QObject):
    """Present proxy health, live traffic, and interception tools to QML."""

    modelChanged = Signal()
    rulesChanged = Signal()
    statusChanged = Signal()
    queryChanged = Signal()
    interceptionChanged = Signal()
    lifecycleChanged = Signal()
    errorOccurred = Signal(str)

    def __init__(self, proxy_master: Any | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._shutdown_requested = threading.Event()
        self._lifecycle_gate = threading.Lock()
        self._proxy = proxy_master
        self._model = DictListModel(_TRAFFIC_ROLES, parent=self)
        self._rules_model = DictListModel(_RULE_ROLES, parent=self)
        self._lifecycle_task = TaskState(self)
        self._lifecycle_task.failed.connect(self._lifecycle_failed)
        self._lifecycle_task.succeeded.connect(self._lifecycle_succeeded)
        self._lifecycle_task.busyChanged.connect(self._lifecycle_busy_changed)
        self._query = ''
        self._intercept_match = ''
        self._capture_all_hosts = False
        self._entries_by_id: dict[int, dict[str, Any]] = {}
        self._last_signature: tuple[Any, ...] = ()
        self._last_running = self.running
        self._last_pending_count = 0
        self._force_model_reset = True
        self._lifecycle_action = ''
        self._queued_lifecycle_action = ''
        self._rules = self._load_rules()
        self._refresh_rules_model()
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    @Property(QObject, constant=True)
    def model(self) -> QObject:
        return self._model

    @Property(QObject, constant=True)
    def rulesModel(self) -> QObject:  # noqa: N802
        return self._rules_model

    @Property(QObject, constant=True)
    def lifecycleTask(self) -> QObject:  # noqa: N802
        return self._lifecycle_task

    @Property(bool, notify=statusChanged)
    def running(self) -> bool:
        return bool(self._proxy is not None and getattr(self._proxy, 'is_running', False))

    @Property(str, notify=statusChanged)
    def statusText(self) -> str:  # noqa: N802
        if self._lifecycle_action == 'start':
            return 'Starting'
        if self._lifecycle_action == 'stop':
            return 'Stopping'
        if self._lifecycle_action == 'restart':
            return 'Restarting'
        return 'Connected' if self.running else 'Stopped'

    @Property(str, notify=lifecycleChanged)
    def lifecycleAction(self) -> str:  # noqa: N802
        return self._lifecycle_action

    @Property(int, notify=statusChanged)
    def pendingCount(self) -> int:  # noqa: N802
        return self._last_pending_count

    @Property(str, notify=queryChanged)
    def query(self) -> str:  # pyright: ignore[reportRedeclaration]
        return self._query

    @query.setter  # pyright: ignore[reportRedeclaration]
    def query(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]
        normalized = value.strip().casefold()
        if normalized == self._query:
            return
        self._query = normalized
        self._force_model_reset = True
        self._last_signature = ()
        self.queryChanged.emit()
        self.refresh()

    @Property(str, notify=interceptionChanged)
    def interceptMatch(self) -> str:  # noqa: N802
        return self._intercept_match

    @Property(bool, notify=interceptionChanged)
    def captureAllHosts(self) -> bool:  # noqa: N802
        return self._capture_all_hosts

    @Slot()
    def refresh(self) -> None:
        traffic = self._traffic_snapshot()
        entries_by_id = {
            int(entry.get('id', -1)): entry
            for entry in traffic
            if isinstance(entry.get('id'), int)
        }
        pending_count = sum(1 for entry in traffic if entry.get('pending_stage'))
        signature = (
            self._query,
            tuple(self._entry_signature(entry) for entry in traffic),
        )
        self._entries_by_id = entries_by_id
        if signature != self._last_signature:
            self._last_signature = signature
            rows = [row for entry in traffic if (row := self._traffic_row(entry)) is not None]
            self._sync_traffic_rows(rows)
        running = self.running
        if running != self._last_running or pending_count != self._last_pending_count:
            self._last_running = running
            self._last_pending_count = pending_count
            self.statusChanged.emit()

    def _traffic_snapshot(self) -> list[dict[str, Any]]:
        if self._proxy is None or not hasattr(self._proxy, 'get_env_proxy_traffic'):
            return []
        try:
            return [dict(entry) for entry in self._proxy.get_env_proxy_traffic()]
        except Exception as exc:
            self.errorOccurred.emit(f'Could not read proxy traffic: {exc}')
            return []

    @staticmethod
    def _entry_signature(entry: dict[str, Any]) -> tuple[Any, ...]:
        return (
            entry.get('id'),
            entry.get('method'),
            entry.get('host'),
            entry.get('path'),
            entry.get('status'),
            entry.get('size'),
            entry.get('ms'),
            entry.get('pending_stage'),
            bool(entry.get('dropped_request')),
            bool(entry.get('dropped_response')),
            bool(entry.get('was_intercepted')),
            _payload_signature(entry.get('request_raw')),
            _payload_signature(entry.get('response_raw')),
        )

    def _traffic_row(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        request_id = entry.get('id')
        if not isinstance(request_id, int):
            return None
        method = str(entry.get('method') or '')
        host = str(entry.get('host') or '')
        path = str(entry.get('path') or '')
        status = _status_text(entry)
        search_text = f'{request_id} {method} {host} {path} {status}'
        if self._query and self._query not in search_text.casefold():
            return None
        duration = entry.get('ms')
        pending_stage = str(entry.get('pending_stage') or '')
        return {
            'key': str(request_id),
            'requestId': request_id,
            'timeText': _time_text(entry.get('time')),
            'method': method,
            'host': host,
            'path': path,
            'status': status,
            'sizeText': _size_text(entry.get('size')),
            'durationText': f'{duration} ms' if duration is not None else '',
            'pendingStage': pending_stage,
            'pending': bool(pending_stage),
            'dropped': bool(entry.get('dropped_request') or entry.get('dropped_response')),
            'intercepted': bool(entry.get('was_intercepted')),
            'searchText': search_text,
        }

    def _sync_traffic_rows(self, rows: list[dict[str, Any]]) -> None:
        current = self._model.snapshot()
        if self._force_model_reset or not current or not rows:
            self._model.replace_items(rows)
            self._force_model_reset = False
            self.modelChanged.emit()
            return

        new_keys = [str(row['key']) for row in rows]
        new_key_set = set(new_keys)
        removed = [
            index for index, item in enumerate(current) if str(item.get('key')) not in new_key_set
        ]
        if len(removed) > 64:
            self._model.replace_items(rows)
            self.modelChanged.emit()
            return
        self._model.remove_rows(removed)
        remaining = self._model.snapshot()
        remaining_keys = [str(item.get('key')) for item in remaining]
        if new_keys[: len(remaining_keys)] != remaining_keys:
            self._model.replace_items(rows)
            self.modelChanged.emit()
            return

        changed = bool(removed)
        for index, row in enumerate(rows[: len(remaining)]):
            changed = self._model.update_item(index, row) or changed
        for row in rows[len(remaining) :]:
            self._model.append_item(row)
            changed = True
        if changed:
            self.modelChanged.emit()

    @Slot()
    def clear(self) -> None:
        if self._proxy is not None and hasattr(self._proxy, 'clear_env_proxy_traffic'):
            try:
                self._proxy.clear_env_proxy_traffic()
            except Exception as exc:
                self.errorOccurred.emit(f'Could not clear proxy traffic: {exc}')
                return
        self._last_signature = ()
        self._force_model_reset = True
        self.refresh()

    @Slot(result=bool)
    def start(self) -> bool:
        return self._queue_lifecycle('start')

    @Slot(result=bool)
    def stop(self) -> bool:
        return self._queue_lifecycle('stop')

    @Slot(result=bool)
    def restart(self) -> bool:
        return self._queue_lifecycle('restart')

    def _queue_lifecycle(self, action: str) -> bool:
        if self._shutdown_requested.is_set():
            return False
        if self._proxy is None:
            self.errorOccurred.emit('The proxy service is unavailable.')
            return False
        if self._lifecycle_task.busy:
            self._queued_lifecycle_action = action
            return True
        self._lifecycle_action = action
        self.lifecycleChanged.emit()
        self.statusChanged.emit()
        return self._lifecycle_task.run_cancellable(
            action,
            lambda cancel_event: self._run_lifecycle(action, cancel_event),
        )

    def _run_lifecycle(self, action: str, cancel_event: threading.Event) -> str:
        proxy = self._proxy
        if proxy is None:
            raise RuntimeError('The proxy service is unavailable')
        if action == 'restart':
            proxy.stop()
            self._start_if_active(proxy, cancel_event)
        elif action == 'start':
            self._start_if_active(proxy, cancel_event)
        else:
            proxy.stop()
        return action

    def _start_if_active(self, proxy: Any, cancel_event: threading.Event) -> None:
        with self._lifecycle_gate:
            if self._shutdown_requested.is_set() or cancel_event.is_set():
                return
            proxy.start()

    @Slot(object)
    def _lifecycle_succeeded(self, _result: object) -> None:
        self._finish_lifecycle()

    @Slot(str)
    def _lifecycle_failed(self, message: str) -> None:
        self.errorOccurred.emit(f'Proxy {self._lifecycle_action or "operation"} failed: {message}')
        self._finish_lifecycle()

    def _finish_lifecycle(self) -> None:
        self._lifecycle_action = ''
        self.lifecycleChanged.emit()
        self.refresh()
        self.statusChanged.emit()

    @Slot()
    def _lifecycle_busy_changed(self) -> None:
        if self._lifecycle_task.busy or self._lifecycle_action:
            return
        queued = self._queued_lifecycle_action
        self._queued_lifecycle_action = ''
        if queued:
            self._queue_lifecycle(queued)

    @Slot(str)
    def setInterceptMatch(self, match_text: str) -> None:  # noqa: N802
        normalized = match_text.strip()
        if normalized == self._intercept_match:
            return
        if self._proxy is not None and hasattr(self._proxy, 'set_env_proxy_intercept_match'):
            self._proxy.set_env_proxy_intercept_match(normalized)
        self._intercept_match = normalized
        self.interceptionChanged.emit()

    @Slot(bool)
    def setCaptureAllHosts(self, enabled: bool) -> None:  # noqa: N802
        if enabled == self._capture_all_hosts:
            return
        if self._proxy is not None and hasattr(self._proxy, 'set_env_proxy_intercept_all'):
            self._proxy.set_env_proxy_intercept_all(enabled)
        self._capture_all_hosts = enabled
        self.interceptionChanged.emit()

    @Slot(str, bool)
    def setIntercept(self, match_text: str, capture_all_hosts: bool) -> None:  # noqa: N802
        self.setInterceptMatch(match_text)
        self.setCaptureAllHosts(capture_all_hosts)

    @Slot(int, str, str, str, result=bool)
    def resolve(self, request_id: int, stage: str, action: str, edited_text: str) -> bool:
        if stage not in {'request', 'response'} or action not in {'forward', 'drop'}:
            self.errorOccurred.emit('The held traffic action is invalid.')
            return False
        if self._proxy is None or not hasattr(self._proxy, 'submit_env_proxy_pending'):
            return False
        try:
            resolved = bool(
                self._proxy.submit_env_proxy_pending(request_id, stage, action, edited_text)
            )
        except Exception as exc:
            self.errorOccurred.emit(f'Could not {action} held traffic: {exc}')
            return False
        if not resolved:
            self.errorOccurred.emit('That request is no longer being held.')
            return False
        self._last_signature = ()
        QTimer.singleShot(0, self.refresh)
        return True

    @Slot(str, result=int)
    def resolveAll(self, action: str) -> int:  # noqa: N802
        if action not in {'forward', 'drop'}:
            self.errorOccurred.emit('The held traffic action is invalid.')
            return 0
        if self._proxy is None or not hasattr(self._proxy, 'get_env_proxy_pending_intercepts'):
            return 0
        resolved = 0
        try:
            for request_id, stage in self._proxy.get_env_proxy_pending_intercepts():
                if self._proxy.submit_env_proxy_pending(request_id, stage, action, None):
                    resolved += 1
        except Exception as exc:
            self.errorOccurred.emit(f'Could not {action} held traffic: {exc}')
            return resolved
        self._last_signature = ()
        QTimer.singleShot(0, self.refresh)
        return resolved

    @Slot(int, str, result=bool)
    def replay(self, request_id: int, edited_text: str) -> bool:
        if self._proxy is None or not hasattr(self._proxy, 'replay_env_proxy_request'):
            return False
        try:
            replaying = bool(self._proxy.replay_env_proxy_request(request_id, edited_text))
        except Exception as exc:
            self.errorOccurred.emit(f'Could not replay request: {exc}')
            return False
        if not replaying:
            self.errorOccurred.emit('The selected request cannot be replayed.')
            return False
        self._last_signature = ()
        return True

    @Slot(str, result=dict)
    def trafficEntry(self, key: str) -> dict[str, Any]:  # noqa: N802
        try:
            request_id = int(key)
        except ValueError:
            return {}
        entry = self._entries_by_id.get(request_id)
        if entry is None:
            return {}
        stage = str(entry.get('pending_stage') or '')
        return {
            'requestId': request_id,
            'timeText': _time_text(entry.get('time')),
            'method': str(entry.get('method') or ''),
            'host': str(entry.get('host') or ''),
            'port': int(entry.get('port') or 443),
            'path': str(entry.get('path') or ''),
            'status': _status_text(entry),
            'sizeText': _size_text(entry.get('size')),
            'durationText': (
                f'{entry.get("ms")} ms' if entry.get('ms') is not None else ''
            ),
            'pendingStage': stage,
            'pending': bool(stage),
            'requestText': self._format_preview(entry, 'request'),
            'responseText': self._format_preview(entry, 'response'),
            'responseEditable': stage == 'response',
            'intercepted': bool(entry.get('intercepted')),
            'wasIntercepted': bool(entry.get('was_intercepted')),
            'droppedRequest': bool(entry.get('dropped_request')),
            'droppedResponse': bool(entry.get('dropped_response')),
        }

    def _format_preview(self, entry: dict[str, Any], stage: str) -> str:
        preview_entry = dict(entry)
        if entry.get('pending_stage') == stage and self._proxy is not None:
            getter = getattr(self._proxy, 'get_env_proxy_pending_data', None)
            if callable(getter):
                pending_data = getter(int(entry['id']), stage)
                if pending_data is not None:
                    preview_entry[f'{stage}_raw'] = pending_data
        formatter_name = (
            'format_env_proxy_request_preview'
            if stage == 'request'
            else 'format_env_proxy_response_preview'
        )
        formatter = getattr(self._proxy, formatter_name, None) if self._proxy is not None else None
        try:
            text = str(formatter(preview_entry) or '') if callable(formatter) else ''
        except Exception as exc:
            return f'Preview unavailable: {exc}'
        if not text and entry.get('method') == 'CONNECT':
            return _TUNNEL_NOTE
        return _redact_sensitive_headers(text)

    def _load_rules(self) -> list[dict[str, Any]]:
        if self._proxy is None or not hasattr(self._proxy, 'get_auto_replace_rules'):
            return []
        try:
            return [_normalized_rule(dict(rule)) for rule in self._proxy.get_auto_replace_rules()]
        except Exception as exc:
            self.errorOccurred.emit(f'Could not load auto-replace rules: {exc}')
            return []

    def _refresh_rules_model(self) -> None:
        self._rules_model.replace_items(
            {
                'key': str(index),
                'ruleEnabled': rule['enabled'],
                'direction': rule['direction'],
                'directionLabel': _DIRECTION_LABELS[rule['direction']],
                'ruleType': rule['type'],
                'typeLabel': _TYPE_LABELS[rule['type']],
                'matchText': rule['match'],
                'replacement': rule['replacement'],
                'hostFilter': rule['host_filter'],
                'pathFilter': rule['path_filter'],
            }
            for index, rule in enumerate(self._rules)
        )
        self.rulesChanged.emit()

    def _save_rules(self, replacement: list[dict[str, Any]]) -> bool:
        if self._proxy is None or not hasattr(self._proxy, 'set_auto_replace_rules'):
            self.errorOccurred.emit('Auto-replace rules are unavailable.')
            return False
        try:
            self._proxy.set_auto_replace_rules(replacement)
        except Exception as exc:
            self.errorOccurred.emit(f'Could not save auto-replace rules: {exc}')
            return False
        self._rules = replacement
        self._refresh_rules_model()
        return True

    @Slot(str, result=dict)
    def rule(self, key: str) -> dict[str, Any]:
        try:
            rule = self._rules[int(key)]
        except (ValueError, IndexError):
            return {}
        return {
            'enabled': rule['enabled'],
            'direction': rule['direction'],
            'ruleType': rule['type'],
            'matchText': rule['match'],
            'replacement': rule['replacement'],
            'hostFilter': rule['host_filter'],
            'pathFilter': rule['path_filter'],
        }

    @Slot(bool, str, str, str, str, str, str, result=bool)
    def addRule(  # noqa: N802
        self,
        enabled: bool,
        direction: str,
        rule_type: str,
        match_text: str,
        replacement: str,
        host_filter: str,
        path_filter: str,
    ) -> bool:
        rule = self._build_rule(
            enabled,
            direction,
            rule_type,
            match_text,
            replacement,
            host_filter,
            path_filter,
        )
        return bool(rule) and self._save_rules([*self._rules, rule])

    @Slot(str, bool, str, str, str, str, str, str, result=bool)
    def updateRule(  # noqa: N802
        self,
        key: str,
        enabled: bool,
        direction: str,
        rule_type: str,
        match_text: str,
        replacement: str,
        host_filter: str,
        path_filter: str,
    ) -> bool:
        try:
            index = int(key)
            self._rules[index]
        except (ValueError, IndexError):
            self.errorOccurred.emit('The selected auto-replace rule no longer exists.')
            return False
        rule = self._build_rule(
            enabled,
            direction,
            rule_type,
            match_text,
            replacement,
            host_filter,
            path_filter,
        )
        if not rule:
            return False
        replacement_rules = [dict(item) for item in self._rules]
        replacement_rules[index] = rule
        return self._save_rules(replacement_rules)

    def _build_rule(
        self,
        enabled: bool,
        direction: str,
        rule_type: str,
        match_text: str,
        replacement: str,
        host_filter: str,
        path_filter: str,
    ) -> dict[str, Any]:
        if direction not in _RULE_DIRECTIONS:
            self.errorOccurred.emit('Choose a valid rule direction.')
            return {}
        if rule_type not in _RULE_TYPES:
            self.errorOccurred.emit('Choose a valid auto-replace rule type.')
            return {}
        if not match_text.strip():
            self.errorOccurred.emit('Enter text, a field name, or a path to match.')
            return {}
        return {
            'enabled': enabled,
            'direction': direction,
            'type': rule_type,
            'match': match_text,
            'replacement': replacement,
            'host_filter': host_filter.strip(),
            'path_filter': path_filter.strip(),
        }

    @Slot(str, bool, result=bool)
    def setRuleEnabled(self, key: str, enabled: bool) -> bool:  # noqa: N802
        try:
            index = int(key)
            current = self._rules[index]
        except (ValueError, IndexError):
            return False
        if current['enabled'] == enabled:
            return True
        replacement = [dict(rule) for rule in self._rules]
        replacement[index]['enabled'] = enabled
        return self._save_rules(replacement)

    @Slot(str, result=bool)
    def duplicateRule(self, key: str) -> bool:  # noqa: N802
        try:
            rule = dict(self._rules[int(key)])
        except (ValueError, IndexError):
            return False
        return self._save_rules([*self._rules, rule])

    @Slot(str, result=bool)
    def deleteRule(self, key: str) -> bool:  # noqa: N802
        try:
            index = int(key)
            self._rules[index]
        except (ValueError, IndexError):
            return False
        return self._save_rules(
            [dict(rule) for rule_index, rule in enumerate(self._rules) if rule_index != index]
        )

    @Slot(str, result=bool)
    def importRules(self, value: str) -> bool:  # noqa: N802
        path = self._local_path(value)
        try:
            if path.stat().st_size > _MAX_RULE_IMPORT_BYTES:
                raise ValueError('the rules file exceeds the 8 MiB safety limit')
            payload: object = json.loads(path.read_text(encoding='utf-8'))
            imported = payload.get('rules') if isinstance(payload, dict) else payload
            if not isinstance(imported, list):
                raise ValueError('expected a list of rules')
            if len(imported) > _MAX_IMPORTED_RULES:
                raise ValueError(f'expected at most {_MAX_IMPORTED_RULES} rules')
            replacement = [
                _normalized_rule(rule) for rule in imported if isinstance(rule, dict)
            ]
            if len(replacement) != len(imported):
                raise ValueError('every rule must be a JSON object')
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.errorOccurred.emit(f'Could not import auto-replace rules: {exc}')
            return False
        return self._save_rules(replacement)

    @Slot(str, result=bool)
    def exportRules(self, value: str) -> bool:  # noqa: N802
        path = self._local_path(value)
        if not path.suffix:
            path = path.with_suffix('.json')
        try:
            path.write_text(
                json.dumps({'rules': self._rules}, indent=2, ensure_ascii=False),
                encoding='utf-8',
            )
        except OSError as exc:
            self.errorOccurred.emit(f'Could not export auto-replace rules: {exc}')
            return False
        return True

    @staticmethod
    def _local_path(value: str) -> Path:
        url = QUrl(value)
        return Path(url.toLocalFile()) if url.isLocalFile() else Path(value).expanduser()

    @Slot()
    def shutdown(self) -> None:
        with self._lifecycle_gate:
            if self._shutdown_requested.is_set():
                return
            self._shutdown_requested.set()
            self._queued_lifecycle_action = ''
        self._timer.stop()
        self._lifecycle_task.shutdown(wait=True)
        if self._proxy is not None and hasattr(self._proxy, 'stop'):
            self._proxy.stop()
