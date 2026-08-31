"""Proxy tab - live view of traffic seen by the Roblox Env Proxy explicit proxy."""

from __future__ import annotations

import base64
import contextlib
import importlib
import json
import pathlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, TypedDict, cast, override

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, QPoint, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QWidget,
)

from fleasion.localization import tr
from fleasion.utils.paths import PROXY_TRAFFIC_FILE

from .proxy_tab_ui import Ui_Form as Ui_ProxyTab
from .rules_dialog_ui import UiDialog as UiRulesDialog

type _ShortcutHandler = Callable[[], object]
type _PendingIntercept = tuple[int, str]
type _PendingResponseKey = tuple[int, str]
type _CompletedResponseKey = tuple[int, bytes | bytearray | None]


class _TrafficEntry(TypedDict):
    id: int
    time: float | None
    host: str
    port: int
    method: str
    path: str
    intercepted: bool
    status: int | None
    size: int
    ms: int | None
    request_raw: bytes | bytearray | None
    response_raw: bytes | bytearray | None
    pending_stage: str | None
    was_intercepted: bool
    dropped_request: bool
    dropped_response: bool


class _AutoReplaceRule(TypedDict, total=False):
    enabled: bool
    direction: str
    type: str
    match: str
    replacement: str
    host_filter: str
    path_filter: str


class _ConfigManager(Protocol):
    settings: dict[str, object]

    def save(self) -> None: ...


def _load_imported_rule_objects(file_path: str) -> list[dict[str, object]]:
    with pathlib.Path(file_path).open(encoding='utf-8') as handle:
        data = cast('object', json.load(handle))
    data_dict = cast('dict[str, object]', data) if isinstance(data, dict) else None
    if data_dict is None:
        imported: object = cast('object', data)
    else:
        imported = cast('object', data_dict.get('rules'))
    if not isinstance(imported, list):
        msg = 'expected a list of rules'
        raise TypeError(msg)
    imported_rules = cast('list[object]', imported)
    return [
        cast('dict[str, object]', rule) for rule in imported_rules if isinstance(rule, dict)
    ]


class _ProxyMaster(Protocol):
    def get_auto_replace_rules(self) -> list[_AutoReplaceRule]: ...

    def set_auto_replace_rules(self, rules: list[_AutoReplaceRule]) -> None: ...

    def get_env_proxy_traffic(self) -> list[_TrafficEntry]: ...

    def clear_env_proxy_traffic(self) -> None: ...

    def format_env_proxy_request_preview(self, entry: _TrafficEntry) -> str: ...

    def format_env_proxy_response_preview(self, entry: _TrafficEntry) -> str: ...

    def set_env_proxy_intercept_match(self, text: str) -> None: ...

    def set_env_proxy_intercept_all(self, enabled: bool) -> None: ...

    def get_env_proxy_pending_intercepts(self) -> list[_PendingIntercept]: ...

    def submit_env_proxy_pending(
        self, entry_id: int, stage: str, action: str, edited_text: str | None = None
    ) -> bool: ...

    def replay_env_proxy_request(self, entry_id: int, edited_text: str | None = None) -> bool: ...


# Stable persistence keys for saved column widths. These intentionally keep the
# pre-localization values; only the displayed headers are translated.
_TABLE_COLUMN_KEYS = ('#', 'Time', 'Method', 'Host', 'Path', 'Status', 'Size', 'ms')
_RULES_TABLE_COLUMN_KEYS = (
    'Enabled',
    'Direction',
    'Type',
    'Match/Path',
    'Replacement',
    'Host filter',
    'Path filter',
)


def _table_headers() -> tuple[str, ...]:
    return (
        tr('proxy.table.number'),
        tr('proxy.table.time'),
        tr('proxy.table.method'),
        tr('proxy.table.host'),
        tr('proxy.table.path'),
        tr('proxy.table.status'),
        tr('proxy.table.size'),
        tr('proxy.table.ms'),
    )


def _direction_options() -> tuple[tuple[str, str], ...]:
    return (
        (tr('proxy.rules.direction.both'), 'both'),
        (tr('proxy.rules.direction.request'), 'request'),
        (tr('proxy.rules.direction.response'), 'response'),
    )


def _type_options() -> tuple[tuple[str, str], ...]:
    return (
        (tr('proxy.rules.type.plain_text'), 'plain'),
        (tr('proxy.rules.type.regex'), 'regex'),
        (tr('proxy.rules.type.json_path'), 'json_path'),
        (tr('proxy.rules.type.query_param'), 'query_param'),
        (tr('proxy.rules.type.header'), 'header'),
    )


# Fields preserved to disk when the "Preserve" checkbox is on - everything
# needed to redraw a row (including its color tint) and its request/response
# preview text, but not anything tied to a live connection (pending_stage
# can't survive a restart, so it's always reset on load).
_PRESERVE_FIELDS = (
    'time',
    'host',
    'port',
    'method',
    'path',
    'intercepted',
    'status',
    'size',
    'ms',
    'was_intercepted',
    'dropped_request',
    'dropped_response',
)


def _entry_to_preserved_dict(entry: _TrafficEntry) -> dict[str, object]:
    data: dict[str, object] = {key: cast('object', entry.get(key)) for key in _PRESERVE_FIELDS}
    for raw_key in ('request_raw', 'response_raw'):
        raw = entry.get(raw_key)
        data[raw_key] = base64.b64encode(bytes(raw)).decode('ascii') if raw else None
    return data


def _preserved_dict_to_entry(data: dict[str, object], synthetic_id: int) -> _TrafficEntry:
    entry = {key: data.get(key) for key in _PRESERVE_FIELDS}
    entry['id'] = synthetic_id
    entry['pending_stage'] = None
    for raw_key in ('request_raw', 'response_raw'):
        encoded = data.get(raw_key)
        entry[raw_key] = base64.b64decode(cast('str | bytes', encoded)) if encoded else None
    return cast('_TrafficEntry', entry)


def _load_preserved_traffic() -> list[_TrafficEntry]:
    """Load previously-preserved rows from disk, if any. Assigned negative,
    synthetic ids (in chronological order) so they can never collide with a
    live proxy log entry's id, which always starts back at 0 on every launch.
    """
    try:
        raw = PROXY_TRAFFIC_FILE.read_text(encoding='utf-8')
    except OSError:
        return []
    try:
        saved = cast('object', json.loads(raw))
    except ValueError:
        return []
    saved_dict = cast('dict[str, object]', saved) if isinstance(saved, dict) else None
    entries = saved_dict.get('entries') if saved_dict is not None else None
    if not isinstance(entries, list):
        return []
    entries_list = cast('list[object]', entries)
    result: list[_TrafficEntry] = []
    for offset, data in enumerate(entries_list):
        if not isinstance(data, dict):
            continue
        result.append(
            _preserved_dict_to_entry(
                cast('dict[str, object]', data), synthetic_id=-(len(entries_list) - offset)
            )
        )
    return result


def _save_preserved_traffic(entries: list[_TrafficEntry]) -> None:
    try:
        PROXY_TRAFFIC_FILE.parent.mkdir(parents=True, exist_ok=True)
        with PROXY_TRAFFIC_FILE.open('w', encoding='utf-8') as handle:
            json.dump({'entries': [_entry_to_preserved_dict(e) for e in entries]}, handle)
    except OSError:
        pass


def _clear_preserved_traffic_file() -> None:
    with contextlib.suppress(OSError):
        PROXY_TRAFFIC_FILE.unlink(missing_ok=True)


def _format_timestamp(value: float | None) -> str:
    if not value:
        return ''
    try:
        return datetime.fromtimestamp(value, tz=UTC).astimezone().strftime('%H:%M:%S.%f')[:-3]
    except OSError, OverflowError, ValueError:
        return ''


def _format_status(entry: _TrafficEntry) -> str:
    status = entry.get('status')
    if status is not None:
        return str(status)
    # Tunneled (non-intercepted) hosts are an opaque TLS pipe to us - we relay
    # bytes without decrypting them, so there's no HTTP status to read off the
    # wire. That's expected, not a dropped/failed request.
    if entry.get('method') == 'CONNECT':
        return 'TUNNEL'
    return '-'


def _format_size(value: float | None) -> str:
    if value is None:
        return ''
    if value == 0:
        return '0 B'
    size = float(value)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{int(size)} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} GB'


def _format_ms(value: float | None) -> str:
    return '' if value is None else f'{value} ms'


class _NumericSortItem(QTableWidgetItem):
    """Table item that sorts on a numeric value instead of its display text."""

    def __init__(self, numeric_val: float, text: str) -> None:
        super().__init__(text)
        self.numeric_val = numeric_val

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _NumericSortItem):
            return self.numeric_val < other.numeric_val
        return super().__lt__(other)


def _status_sort_value(entry: _TrafficEntry) -> int:
    status = entry.get('status')
    return status if status is not None else -1


_HIGHLIGHT_BRUSH = QBrush(QColor(255, 235, 59, 60))
_INTERCEPT_BRUSH = QBrush(QColor(33, 150, 243, 70))
_DROPPED_BRUSH = QBrush(QColor(244, 67, 54, 70))
_NO_HIGHLIGHT_BRUSH = QBrush()


def _host_path_matches(entry: _TrafficEntry, text: str) -> bool:
    if not text:
        return False
    return text in entry.get('host', '').lower() or text in entry.get('path', '').lower()


def _row_brush(entry: _TrafficEntry, highlight_text: str) -> QBrush:
    if entry.get('dropped_request') or entry.get('dropped_response'):
        return _DROPPED_BRUSH
    if entry.get('was_intercepted'):
        return _INTERCEPT_BRUSH
    if _host_path_matches(entry, highlight_text):
        return _HIGHLIGHT_BRUSH
    return _NO_HIGHLIGHT_BRUSH


def _set_text_preserving_scroll(text_edit: QTextEdit, text: str) -> None:
    """setPlainText() always snaps scroll to the top - skip the no-op case and
    restore the scroll position on real updates so a growing/streaming
    response doesn't yank the view back to the top while you're reading it.
    """
    if text_edit.toPlainText() == text:
        return
    scrollbar = text_edit.verticalScrollBar()
    pos = scrollbar.value()
    text_edit.setPlainText(text)
    scrollbar.setValue(pos)


class _TableColumnResizer(QObject):
    """Makes every column of a QTableWidget Interactive-resizable except the
    last one, which has no drag handle and always fills whatever's left -
    and persists the resizable widths to config_manager.settings[settings_key].

    The last column deliberately doesn't use Qt's own Stretch resize mode:
    that doesn't reliably shrink back down once the other columns already
    overflow the viewport, which is exactly what let a table's header run
    off its own right edge. This does the same job by hand instead, and
    clamps any drag that would squeeze the last column below a sane minimum.
    """

    def __init__(
        self,
        table: QTableWidget,
        headers: tuple[str, ...],
        config_manager: _ConfigManager | None,
        settings_key: str,
    ) -> None:
        super().__init__(table)
        self._table = table
        self._headers = headers
        self._config = config_manager
        self._settings_key = settings_key
        self._last_col = len(headers) - 1
        self._resizing = False

        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        self._resizing = True
        widths = self._load_widths()
        for col, label in enumerate(headers):
            if col == self._last_col:
                continue
            width = widths.get(label)
            if width:
                table.setColumnWidth(col, width)
            header.setSectionResizeMode(col, header.ResizeMode.Interactive)
        header.setSectionResizeMode(self._last_col, header.ResizeMode.Fixed)
        self._recompute_last()
        self._resizing = False

        header.sectionResized.connect(self._on_resized)
        table.installEventFilter(self)

    def _load_widths(self) -> dict[str, int]:
        if self._config is None:
            return {}
        saved = cast('dict[str, object]', self._config.settings.get(self._settings_key, {}))
        return {k: int(v) for k, v in saved.items() if isinstance(v, (int, float)) and v > 0}

    def _save_widths(self, widths: dict[str, int]) -> None:
        if self._config is None:
            return
        self._config.settings[self._settings_key] = dict(widths)
        self._config.save()

    def _recompute_last(self) -> None:
        table = self._table
        header = table.horizontalHeader()
        sum_others = sum(
            table.columnWidth(c) for c in range(len(self._headers)) if c != self._last_col
        )
        width = max(header.minimumSectionSize(), table.viewport().width() - sum_others)
        was_resizing = self._resizing
        self._resizing = True
        table.setColumnWidth(self._last_col, width)
        self._resizing = was_resizing

    def _on_resized(self, logical_index: int, _old_size: int, new_size: int) -> None:
        if self._resizing or logical_index == self._last_col:
            return
        if logical_index >= len(self._headers):
            return
        table = self._table
        header = table.horizontalHeader()
        min_last_width = header.minimumSectionSize()
        sum_others = sum(
            table.columnWidth(c) for c in range(len(self._headers)) if c != self._last_col
        )
        overflow = min_last_width - (table.viewport().width() - sum_others)
        if overflow > 0:
            clamped = max(header.minimumSectionSize(), new_size - overflow)
            if clamped != new_size:
                self._resizing = True
                table.setColumnWidth(logical_index, clamped)
                self._resizing = False
                new_size = clamped
        self._recompute_last()

        widths = self._load_widths()
        widths[self._headers[logical_index]] = new_size
        self._save_widths(widths)

    @override
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._table and event.type() == QEvent.Type.Resize:
            # The table's own resize event fires before its viewport's size
            # has actually caught up - defer to the next event-loop tick.
            QTimer.singleShot(0, self._recompute_last)
        return False


class _CompactComboBox(QComboBox):
    """A borderless combo box that blends into a table cell, instead of the
    raw OS-styled QComboBox default - same look already used elsewhere in
    the app for compact in-cell dropdowns (e.g. FastFlag True/False editing).
    """

    @override
    def wheelEvent(self, e: QWheelEvent) -> None:
        e.ignore()

    @override
    def paintEvent(self, e: QPaintEvent) -> None:
        painter = QPainter(self)
        if self.hasFocus() or self.underMouse():
            painter.fillRect(self.rect(), self.palette().alternateBase())
        painter.setPen(self.palette().text().color())
        painter.drawText(
            self.rect().adjusted(6, 0, -18, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.currentText(),
        )
        arrow_x = self.width() - 12
        arrow_y = self.height() // 2
        painter.drawLine(arrow_x - 3, arrow_y - 1, arrow_x, arrow_y + 2)
        painter.drawLine(arrow_x, arrow_y + 2, arrow_x + 3, arrow_y - 1)


class AutoReplaceRulesDialog(QDialog):
    """Dialog shown by the 'Auto replace' button - a rule table for
    search/replace transforms applied to request/response bodies (or
    headers/query params) as they pass through the proxy (see
    apply_auto_replace_rules/_header_rules/_query_rules in proxy/server.py).
    """

    def __init__(
        self,
        proxy_master: _ProxyMaster | None = None,
        config_manager: _ConfigManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._proxy_master = proxy_master
        self._config = config_manager
        self.ui = UiRulesDialog()
        self.ui.setup_ui(self)
        self.setWindowTitle(tr('ui.gui.proxy_tab.auto_replace_rules'))
        self.resize(950, 500)

        self.ui.rulesTable.setEditTriggers(
            self.ui.rulesTable.EditTrigger.DoubleClicked
            | self.ui.rulesTable.EditTrigger.EditKeyPressed
        )
        self.ui.rulesTable.setSelectionBehavior(self.ui.rulesTable.SelectionBehavior.SelectRows)
        self.ui.rulesTable.setSelectionMode(self.ui.rulesTable.SelectionMode.ExtendedSelection)
        self.ui.rulesTable.verticalHeader().setVisible(False)
        self._col_resizer = _TableColumnResizer(
            self.ui.rulesTable,
            _RULES_TABLE_COLUMN_KEYS,
            self._config,
            'auto_replace_rules_column_widths',
        )

        self._rules: list[_AutoReplaceRule] = (
            list(proxy_master.get_auto_replace_rules())
            if proxy_master is not None and hasattr(proxy_master, 'get_auto_replace_rules')
            else []
        )
        self._loading = False
        self._render_rules()

        self.ui.rulesTable.itemChanged.connect(self._on_item_changed)
        self.ui.addRuleButton.clicked.connect(self._add_rule)
        self.ui.duplicateRuleButton.clicked.connect(self._duplicate_selected)
        self.ui.deleteRuleButton.clicked.connect(self._delete_selected)
        self.ui.importButton.clicked.connect(self._import_rules)
        self.ui.exportButton.clicked.connect(self._export_rules)

    @staticmethod
    def _default_rule() -> _AutoReplaceRule:
        return {
            'enabled': True,
            'direction': 'both',
            'type': 'plain',
            'match': '',
            'replacement': '',
            'host_filter': '',
            'path_filter': '',
        }

    def _render_rules(self) -> None:
        table = self.ui.rulesTable
        self._loading = True
        table.setRowCount(len(self._rules))
        for row, rule in enumerate(self._rules):
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            enabled_item.setCheckState(
                Qt.CheckState.Checked if rule.get('enabled', True) else Qt.CheckState.Unchecked
            )
            table.setItem(row, 0, enabled_item)

            direction_box = _CompactComboBox()
            for label, value in _direction_options():
                direction_box.addItem(label, value)
            direction_index = direction_box.findData(rule.get('direction', 'both'))
            direction_box.setCurrentIndex(max(0, direction_index))

            def _direction_changed(_index: int, r: int = row) -> None:
                self._on_direction_changed(r)

            direction_box.currentIndexChanged.connect(_direction_changed)
            table.setCellWidget(row, 1, direction_box)

            type_box = _CompactComboBox()
            for label, value in _type_options():
                type_box.addItem(label, value)
            type_index = type_box.findData(rule.get('type', 'plain'))
            type_box.setCurrentIndex(max(0, type_index))

            def _type_changed(_index: int, r: int = row) -> None:
                self._on_type_changed(r)

            type_box.currentIndexChanged.connect(_type_changed)
            table.setCellWidget(row, 2, type_box)

            for col, key in (
                (3, 'match'),
                (4, 'replacement'),
                (5, 'host_filter'),
                (6, 'path_filter'),
            ):
                table.setItem(row, col, QTableWidgetItem(str(rule.get(key, ''))))
        self._loading = False

    def _save(self) -> None:
        if self._proxy_master is not None and hasattr(self._proxy_master, 'set_auto_replace_rules'):
            self._proxy_master.set_auto_replace_rules(self._rules)

    def _on_direction_changed(self, row: int) -> None:
        if self._loading or row >= len(self._rules):
            return
        widget = cast('_CompactComboBox', self.ui.rulesTable.cellWidget(row, 1))
        self._rules[row]['direction'] = str(cast('object', widget.currentData()) or 'both')
        self._save()

    def _on_type_changed(self, row: int) -> None:
        if self._loading or row >= len(self._rules):
            return
        widget = cast('_CompactComboBox', self.ui.rulesTable.cellWidget(row, 2))
        self._rules[row]['type'] = str(cast('object', widget.currentData()) or 'plain')
        self._save()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        row, col = item.row(), item.column()
        if row >= len(self._rules):
            return
        if col == 0:
            self._rules[row]['enabled'] = item.checkState() == Qt.CheckState.Checked
            self._save()
            return
        key = {3: 'match', 4: 'replacement', 5: 'host_filter', 6: 'path_filter'}.get(col)
        if key is None:
            return
        self._rules[row][key] = item.text()
        self._save()

    def _selected_rows(self) -> list[int]:
        return sorted({idx.row() for idx in self.ui.rulesTable.selectionModel().selectedRows()})

    def _add_rule(self) -> None:
        self._rules.append(self._default_rule())
        self._render_rules()
        self._save()

    def _duplicate_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        for row in rows:
            self._rules.append(cast('_AutoReplaceRule', dict(self._rules[row])))
        self._render_rules()
        self._save()

    def _delete_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        for row in sorted(rows, reverse=True):
            del self._rules[row]
        self._render_rules()
        self._save()

    def _import_rules(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            tr('ui.gui.proxy_tab.import_auto_replace_rules'),
            '',
            tr('ui.gui.proxy_tab.json_files_json_all_files'),
        )
        if not file_path:
            return
        try:
            imported_rules = _load_imported_rule_objects(file_path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(
                self,
                tr('ui.gui.proxy_tab.import_failed'),
                tr('ui.gui.proxy_tab.could_not_import_rules_value', value0=exc),
            )
            return
        self._rules = [
            cast('_AutoReplaceRule', self._default_rule() | rule) for rule in imported_rules
        ]
        self._render_rules()
        self._save()

    def _export_rules(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            tr('ui.gui.proxy_tab.export_auto_replace_rules'),
            'auto_replace_rules.json',
            tr('ui.gui.proxy_tab.json_files_json'),
        )
        if not file_path:
            return
        try:
            with pathlib.Path(file_path).open('w', encoding='utf-8') as handle:
                json.dump({'rules': self._rules}, handle, indent=2)
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr('ui.gui.proxy_tab.export_failed'),
                tr('ui.gui.proxy_tab.could_not_export_rules_value', value0=exc),
            )


class ProxyTrafficTab(QWidget):
    """Shows every request the Roblox Env Proxy has seen, not just the intercepted hosts.

    'Intercepted' has multiple unrelated meanings in this codebase: whether
    Fleasion TLS-terminated a host at all (``entry['intercepted']``, an
    architectural detail), whether a request/response matches this tab's own
    intercept-and-edit field and got paused for you to act on
    (``entry['pending_stage']``, driving the blue highlight and pause/edit/
    forward/drop flow), and separately, ``enableCheckBox`` here decides
    whether hosts OUTSIDE Fleasion's own feature set get decrypted/logged at
    all - Fleasion's own features work either way.
    """

    def __init__(
        self,
        config_manager: _ConfigManager | None,
        proxy_master: _ProxyMaster | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager
        self._proxy_master = proxy_master
        self._rules_dialog: AutoReplaceRulesDialog | None = None

        self.ui = Ui_ProxyTab()
        self.ui.setupUi(self)
        # The .ui-generated root layout never had its margins zeroed, unlike
        # every other tab (e.g. RandoStuffTab's root layout explicitly sets
        # (0, 0, 0, 0)) - left at Qt's default (9, 9, 9, 9), the whole tab's
        # content (including the footer button below) sat inset from every
        # edge instead of flush like the other tabs.
        self.ui.verticalLayout_3.setContentsMargins(0, 0, 0, 0)

        self.ui.trafficTable.setColumnCount(len(_TABLE_COLUMN_KEYS))
        self.ui.trafficTable.setHorizontalHeaderLabels(_table_headers())
        self.ui.trafficTable.verticalHeader().setVisible(False)
        self.ui.trafficTable.setEditTriggers(self.ui.trafficTable.EditTrigger.NoEditTriggers)
        self.ui.trafficTable.setSelectionBehavior(self.ui.trafficTable.SelectionBehavior.SelectRows)
        self.ui.trafficTable.setSelectionMode(self.ui.trafficTable.SelectionMode.ExtendedSelection)
        self.ui.trafficTable.setSortingEnabled(True)
        header = self.ui.trafficTable.horizontalHeader()
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        # Column widths are user-resizable and persisted the same way the
        # scraper's cache table does it - see _TableColumnResizer for why the
        # last column doesn't just use Qt's own Stretch resize mode.
        self._col_resizer = _TableColumnResizer(
            self.ui.trafficTable,
            _TABLE_COLUMN_KEYS,
            self._config,
            'proxy_traffic_column_widths',
        )

        self.ui.filterEdit.textChanged.connect(self._apply_filter)
        self.ui.highlightEdit.textChanged.connect(self._apply_highlight)
        self.ui.interceptEdit.textChanged.connect(self._on_intercept_text_changed)
        # Deliberately not persisted anywhere (no config_manager read/write) -
        # always starts unchecked on every launch, regardless of what it was
        # last set to.
        self.ui.enableCheckBox.setChecked(False)
        self.ui.enableCheckBox.toggled.connect(self._on_intercept_all_toggled)

        help_btn = QPushButton(tr('ui.gui.proxy_tab.text'))
        # Match the native height of the neighboring buttons.  This is the same
        # approach used by the replacer footer help button: constrain width only
        # and let the active Qt style choose the vertical geometry.
        help_btn.setMaximumWidth(25)
        help_btn.setToolTip(tr('ui.gui.proxy_tab.about_this_tab'))
        help_btn.clicked.connect(self._show_help)
        self.ui.horizontalLayout_3.insertWidget(
            self.ui.horizontalLayout_3.indexOf(self.ui.clearButton), help_btn
        )

        self.ui.clearButton.clicked.connect(self._clear_traffic)
        self.ui.autoReplace.clicked.connect(self._show_rules_dialog)
        self.ui.trafficTable.itemSelectionChanged.connect(self._on_selection_changed)
        self.ui.trafficTable.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ui.trafficTable.customContextMenuRequested.connect(self._show_table_context_menu)
        # The request box is always editable - not just while held - so an
        # already-completed request can be tweaked and re-sent with R.
        self.ui.requestText.setReadOnly(False)

        forward_menu = QMenu(self)
        forward_menu.addAction(
            tr('ui.gui.proxy_tab.forward_selected'),
            lambda: self._resolve_action('forward', all_rows=False),
        )
        forward_menu.addAction(
            tr('ui.gui.proxy_tab.forward_all'),
            lambda: self._resolve_action('forward', all_rows=True),
        )
        self.ui.forwardButton.setMenu(forward_menu)

        drop_menu = QMenu(self)
        drop_menu.addAction(
            tr('ui.gui.proxy_tab.drop_selected'),
            lambda: self._resolve_action('drop', all_rows=False),
        )
        drop_menu.addAction(
            tr('ui.gui.proxy_tab.drop_all'),
            lambda: self._resolve_action('drop', all_rows=True),
        )
        self.ui.dropButton.setMenu(drop_menu)

        # A/D/R act on the selected row - forward/drop only apply while it's
        # actually held, replay works on any row. Guarded so typing those
        # letters into the filter/highlight/intercept fields or the request/
        # response boxes never triggers them.
        self._make_shortcut('A', lambda: self._resolve_action('forward', all_rows=False))
        self._make_shortcut('D', lambda: self._resolve_action('drop', all_rows=False))
        self._make_shortcut('R', self._replay_selected)

        self._traffic: list[_TrafficEntry] = []
        self._entries_by_id: dict[int, _TrafficEntry] = {}
        self._displayed_entry_id: int | None = None
        # The request box is editable for whichever entry is displayed, held
        # or not - this is the id of the entry currently loaded into it, so a
        # poll refresh only re-fetches the preview when you've switched to a
        # different entry, never while you're mid-edit on this one (R replays
        # whatever's in the box, so edits on an already-completed request
        # need to survive polling too).
        self._loaded_request_entry_id: int | None = None
        # (entry_id, stage) of a held response whose raw editable bytes are
        # currently loaded into the response box - same reload-avoidance idea
        # as above, but only for the response side while it's actually held.
        self._loaded_pending_response_key: _PendingResponseKey | None = None
        self._loaded_completed_response_key: _CompletedResponseKey | None = None

        # Unlike enableCheckBox, "Preserve" DOES persist across launches -
        # it decides whether traffic (rows, requests, responses, and their
        # color tint) gets saved to disk and reloaded on the next launch.
        # Set the checkbox and load any previously-saved rows BEFORE wiring
        # the toggled signal, so restoring the saved state doesn't get
        # mistaken for the user flipping it (which would re-save/clear it).
        preserve_enabled = (
            bool(self._config.settings.get('proxy_traffic_preserve', False))
            if self._config is not None
            else False
        )
        self._preserve_enabled = preserve_enabled
        self._preserved_traffic = _load_preserved_traffic() if preserve_enabled else []
        # Preserved rows use negative synthetic ids (-N..-1) so they can
        # never collide with a live proxy log id, which always restarts at 0
        # on every launch. This offset shifts the DISPLAYED '#' so the two
        # ranges read as one continuous sequence instead of jumping to
        # negative numbers: preserved rows show as 0..N-1, and newly-captured
        # live rows continue right on from N - with no preserved rows (the
        # common case), it's just entry_id unchanged, exactly as before.
        self._traffic_id_display_offset = len(self._preserved_traffic)
        self._last_saved_preserve_fingerprint: tuple[tuple[object, ...], ...] | None = None
        self.ui.preserveCheckBox.setChecked(preserve_enabled)
        self.ui.preserveCheckBox.toggled.connect(self._on_preserve_toggled)

        # Interception pauses a live connection, so poll faster than the
        # plain-traffic-log case to keep the wait for a human to notice small.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._refresh_traffic)
        self._poll_timer.start()

        # Saving to disk on every 400ms poll would mean rewriting the entire
        # (potentially large, request/response bodies included) traffic log
        # that often - a separate, much slower timer keeps disk writes rare
        # while still saving promptly after real changes (see the fingerprint
        # check in _maybe_save_preserved_traffic).
        self._preserve_save_timer = QTimer(self)
        self._preserve_save_timer.setInterval(3000)
        self._preserve_save_timer.timeout.connect(self._maybe_save_preserved_traffic)
        self._preserve_save_timer.start()

        # Same bottom-right footer pattern used by every other tab's own
        # "Clear Cache" button (settings/subplace/modifications/rando_stuff
        # tabs) - a Fixed-height footer row, right-aligned via addStretch(),
        # appended as the LAST item of the tab's root layout.
        footer_widget = QWidget()
        footer_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(8, 4, 8, 4)
        warning_label = QLabel(tr('ui.gui.proxy_tab.warning_this_tab_may_expose_your_roblox'))
        warning_label.setStyleSheet('color: #ffcc66;')
        footer_layout.addWidget(warning_label)
        footer_layout.addStretch()
        clear_cache_btn = QPushButton(tr('ui.gui.proxy_tab.clear_cache'))
        clear_cache_btn.clicked.connect(self._clear_roblox_cache)
        footer_layout.addWidget(clear_cache_btn)
        self.ui.verticalLayout_3.addWidget(footer_widget)

        self._refresh_traffic()

    def _refresh_traffic(self) -> None:
        if self._proxy_master is None or not hasattr(self._proxy_master, 'get_env_proxy_traffic'):
            live = []
        else:
            live = self._proxy_master.get_env_proxy_traffic()
        self._traffic = self._preserved_traffic + live
        self._render_table()

    def _row_entry_id(self, row: int) -> int | None:
        item = self.ui.trafficTable.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _display_row_number(self, entry_id: int) -> int:
        return entry_id + self._traffic_id_display_offset

    def _render_table(self) -> None:
        filter_text = self.ui.filterEdit.text().strip().lower()
        highlight_text = self.ui.highlightEdit.text().strip().lower()
        table = self.ui.trafficTable

        # Table gets fully torn down and rebuilt every poll (rows are
        # re-created, not just updated), which wipes Qt's own selection
        # state - so the FULL multi-row selection has to be captured by
        # entry id here and explicitly restored after rebuilding, not just
        # the single "current" row.
        selected_ids: set[int] = set()
        for idx in table.selectionModel().selectedRows():
            entry_id = self._row_entry_id(idx.row())
            if entry_id is not None:
                selected_ids.add(entry_id)
        current_id = self._row_entry_id(table.currentRow())

        rows = [
            entry
            for entry in self._traffic
            if not filter_text
            or filter_text in entry.get('host', '').lower()
            or filter_text in entry.get('path', '').lower()
            or filter_text in entry.get('method', '').lower()
        ]
        self._entries_by_id = {entry['id']: entry for entry in rows}

        scrollbar = table.verticalScrollBar()
        saved_scroll_value = scrollbar.value()
        was_at_bottom = saved_scroll_value >= scrollbar.maximum() - 2

        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            entry_id = entry['id']
            display_number = self._display_row_number(entry_id)
            items = (
                _NumericSortItem(display_number, str(display_number)),
                _NumericSortItem(entry.get('time') or 0, _format_timestamp(entry.get('time'))),
                QTableWidgetItem(entry.get('method', '')),
                QTableWidgetItem(entry.get('host', '')),
                QTableWidgetItem(entry.get('path', '')),
                _NumericSortItem(_status_sort_value(entry), _format_status(entry)),
                _NumericSortItem(entry.get('size') or 0, _format_size(entry.get('size'))),
                _NumericSortItem(entry.get('ms') or -1, _format_ms(entry.get('ms'))),
            )
            brush = _row_brush(entry, highlight_text)
            for col, item in enumerate(items):
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry_id)
                item.setBackground(brush)
                table.setItem(row, col, item)
        table.setSortingEnabled(True)

        blocker = QSignalBlocker(table)
        table.clearSelection()
        selection_model = table.selectionModel()
        select_flags = (
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        )
        current_row = -1
        for row in range(table.rowCount()):
            entry_id = self._row_entry_id(row)
            if entry_id in selected_ids:
                # selectRow() clears any prior selection instead of adding to
                # it (despite what the Qt docs imply) - selecting through the
                # model directly with explicit Select|Rows flags is what
                # actually accumulates a multi-row selection.
                selection_model.select(table.model().index(row, 0), select_flags)
            if entry_id == current_id:
                current_row = row
        if current_row >= 0:
            # NoUpdate: setCurrentCell()'s 2-arg form clears the selection to
            # just this cell by default, which would blow away the multi-row
            # selection just restored above.
            table.setCurrentCell(current_row, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        del blocker

        if current_row >= 0:
            self._show_entry(self._entries_by_id[cast('int', current_id)])
        elif current_id is not None:
            # The previously current request scrolled out of the log's cap.
            self._displayed_entry_id = None
            self._loaded_request_entry_id = None
            self._loaded_pending_response_key = None
            self._loaded_completed_response_key = None
            self.ui.requestText.clear()
            self.ui.responseText.clear()
            self.ui.requestGroup.setTitle(tr('ui.gui.proxy_tab.request'))
            self.ui.responseGroup.setTitle(tr('ui.gui.proxy_tab.response'))

        # setCurrentCell() auto-scrolls to keep the current cell visible, which
        # would yank the view to wherever the selected row landed. Force the
        # scroll position back to whatever it actually was (or the new bottom,
        # if that's where the user already was) so selecting/refreshing never
        # moves the viewport on its own.
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(saved_scroll_value)

    def _on_selection_changed(self) -> None:
        entry_id = self._row_entry_id(self.ui.trafficTable.currentRow())
        entry = self._entries_by_id.get(entry_id) if entry_id is not None else None
        if entry is not None:
            self._show_entry(entry)
        else:
            self._displayed_entry_id = None
            self._loaded_request_entry_id = None
            self._loaded_pending_response_key = None
            self._loaded_completed_response_key = None
            self.ui.requestText.clear()
            self.ui.responseText.clear()
            self.ui.requestGroup.setTitle(tr('ui.gui.proxy_tab.request'))
            self.ui.responseGroup.setTitle(tr('ui.gui.proxy_tab.response'))

    def _show_entry(self, entry: _TrafficEntry) -> None:
        # Never inject any note/annotation into the box text itself - it's
        # editable, and whatever's in there gets submitted verbatim as the
        # actual request/response body (held) or as what R replays (not
        # held). "Held" is signaled entirely outside the editable content:
        # the group box title and the response box's read-only state.
        self._displayed_entry_id = entry['id']
        pending_stage = entry.get('pending_stage')

        # The request box is always editable, whether this entry is
        # currently held or already completed - R replays whatever's in it.
        # Only (re)load it from the live preview the moment you switch to a
        # DIFFERENT entry; while it's still this same entry, leave the box
        # alone so a poll refresh never overwrites an in-progress edit or
        # resets your caret. Load the same pretty-printed/decompressed text
        # the read-only preview uses (not the raw wire bytes) so it's
        # actually readable while editing; rebuild_edited_message() on
        # submit/replay turns it back into valid wire bytes regardless of
        # how it's formatted here.
        if self._loaded_request_entry_id != entry['id']:
            _set_text_preserving_scroll(
                self.ui.requestText, self._format_preview('format_env_proxy_request_preview', entry)
            )
            self._loaded_request_entry_id = entry['id']

        # The response box only becomes editable while actually held for the
        # response stage - same "load once, then leave it alone" idea as the
        # request box used to have, scoped to just that case now.
        pending_response_key = (entry['id'], 'response') if pending_stage == 'response' else None
        if pending_response_key != self._loaded_pending_response_key:
            if pending_stage == 'response':
                _set_text_preserving_scroll(
                    self.ui.responseText,
                    self._format_preview('format_env_proxy_response_preview', entry),
                )
            self._loaded_pending_response_key = pending_response_key

        if pending_stage != 'response':
            completed_response_key = (entry['id'], entry.get('response_raw'))
            if completed_response_key != self._loaded_completed_response_key:
                _set_text_preserving_scroll(
                    self.ui.responseText,
                    self._format_preview('format_env_proxy_response_preview', entry),
                )
                self._loaded_completed_response_key = completed_response_key

        self.ui.responseText.setReadOnly(pending_stage != 'response')
        self.ui.requestGroup.setTitle(
            tr('ui.gui.proxy_tab.request_held_editable_then_forward_drop')
            if pending_stage == 'request'
            else tr('ui.gui.proxy_tab.request')
        )
        self.ui.responseGroup.setTitle(
            tr('ui.gui.proxy_tab.response_held_editable_then_forward_drop')
            if pending_stage == 'response'
            else tr('ui.gui.proxy_tab.response')
        )

    def _format_preview(self, method_name: str, entry: _TrafficEntry) -> str:
        if self._proxy_master is None:
            return ''
        fmt = cast(
            'Callable[[_TrafficEntry], str] | None',
            getattr(self._proxy_master, method_name, None),
        )
        text = fmt(entry) if callable(fmt) else ''
        if not text and entry.get('method') == 'CONNECT':
            return tr('proxy.tunnel_note')
        return text

    def _resolve_action(self, action: str, all_rows: bool) -> None:
        if self._proxy_master is None:
            return
        if all_rows:
            for entry_id, stage in self._proxy_master.get_env_proxy_pending_intercepts():
                self._proxy_master.submit_env_proxy_pending(entry_id, stage, action, None)
        else:
            entry = self._entries_by_id.get(cast('int', self._displayed_entry_id))
            stage = entry.get('pending_stage') if entry is not None else None
            if entry is None or not stage:
                return
            text_widget = self.ui.requestText if stage == 'request' else self.ui.responseText
            self._proxy_master.submit_env_proxy_pending(
                entry['id'], stage, action, text_widget.toPlainText()
            )
        self._refresh_traffic()

    def _make_shortcut(self, key: str, handler: _ShortcutHandler) -> QShortcut:
        shortcut = QShortcut(QKeySequence(key), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(lambda: self._run_if_not_typing(handler))
        return shortcut

    def _run_if_not_typing(self, handler: _ShortcutHandler) -> None:
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QTextEdit)):
            return
        handler()

    def _replay_selected(self) -> None:
        """Resend the selected row's request fresh, overwriting that row's
        own response in place (no new row) - works on any row, not just
        currently-held ones. The request box is always editable now, so this
        always sends whatever's currently in it, edited or not.
        """
        if self._proxy_master is None or not hasattr(
            self._proxy_master, 'replay_env_proxy_request'
        ):
            return
        entry = self._entries_by_id.get(cast('int', self._displayed_entry_id))
        if entry is None:
            return
        self._proxy_master.replay_env_proxy_request(entry['id'], self.ui.requestText.toPlainText())

    def _show_table_context_menu(self, pos: QPoint) -> None:
        # When multiple rows are selected, correlate to whichever one is
        # already driving the request/response boxes (same row the A/D/R
        # shortcuts act on) - no separate "which row did you right-click"
        # logic needed.
        entry = self._entries_by_id.get(cast('int', self._displayed_entry_id))
        if entry is None:
            return
        menu = QMenu(self)
        menu.addAction(
            tr('ui.gui.proxy_tab.copy_request'),
            lambda: self._copy_to_clipboard(self.ui.requestText.toPlainText()),
        )
        menu.addAction(
            tr('ui.gui.proxy_tab.copy_response'),
            lambda: self._copy_to_clipboard(self.ui.responseText.toPlainText()),
        )
        menu.exec(self.ui.trafficTable.viewport().mapToGlobal(pos))

    def _copy_to_clipboard(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    def _apply_filter(self, _text: str) -> None:
        self._render_table()

    def _on_intercept_text_changed(self, text: str) -> None:
        """Interception is armed purely by this field being non-empty - nothing else gates it.

        This field only decides what gets held going forward; it does not
        control the blue highlight - that tracks entries that were ACTUALLY
        paused (``entry['was_intercepted']``), not a live text re-match.
        """
        if self._proxy_master is not None and hasattr(
            self._proxy_master, 'set_env_proxy_intercept_match'
        ):
            self._proxy_master.set_env_proxy_intercept_match(text)

    def _on_intercept_all_toggled(self, checked: bool) -> None:
        """Widen/narrow decryption+logging to hosts beyond Fleasion's own
        feature set. Those feature hosts (texture stripper, custom FastFlags,
        username spoofer, etc.) keep working regardless of this toggle.
        """
        if self._proxy_master is not None and hasattr(
            self._proxy_master, 'set_env_proxy_intercept_all'
        ):
            self._proxy_master.set_env_proxy_intercept_all(checked)

    def _on_preserve_toggled(self, checked: bool) -> None:
        """Unlike enableCheckBox, this setting DOES persist across launches."""
        self._preserve_enabled = checked
        if self._config is not None:
            self._config.settings['proxy_traffic_preserve'] = checked
            self._config.save()
        if checked:
            self._maybe_save_preserved_traffic(force=True)
        else:
            # Preserve is off - don't keep stale rows around for a future
            # re-enable, and stop showing the ones already loaded this run.
            _clear_preserved_traffic_file()
            self._preserved_traffic = []
            self._last_saved_preserve_fingerprint = None
            self._refresh_traffic()

    def _maybe_save_preserved_traffic(self, force: bool = False) -> None:
        if not self._preserve_enabled:
            return
        fingerprint = tuple(
            (
                entry.get('id'),
                entry.get('status'),
                entry.get('size'),
                entry.get('dropped_request'),
                entry.get('dropped_response'),
                entry.get('was_intercepted'),
            )
            for entry in self._traffic
        )
        if not force and fingerprint == self._last_saved_preserve_fingerprint:
            return
        self._last_saved_preserve_fingerprint = fingerprint
        _save_preserved_traffic(self._traffic)

    def _apply_highlight(self, _text: str = '') -> None:
        self._recolor_rows()

    def _recolor_rows(self) -> None:
        """Recolor existing rows in place - no need to refetch traffic just to recolor."""
        table = self.ui.trafficTable
        highlight_text = self.ui.highlightEdit.text().strip().lower()
        for row in range(table.rowCount()):
            entry_id = self._row_entry_id(row)
            entry = self._entries_by_id.get(entry_id) if entry_id is not None else None
            brush = _row_brush(entry, highlight_text) if entry is not None else _NO_HIGHLIGHT_BRUSH
            for col in range(table.columnCount()):
                item = table.item(row, col)
                if item is not None:
                    item.setBackground(brush)

    def _clear_traffic(self) -> None:
        if self._proxy_master is not None and hasattr(
            self._proxy_master, 'clear_env_proxy_traffic'
        ):
            self._proxy_master.clear_env_proxy_traffic()
        self._preserved_traffic = []
        self._last_saved_preserve_fingerprint = None
        if self._preserve_enabled:
            _clear_preserved_traffic_file()
        self._traffic = []
        self._render_table()

    def _clear_roblox_cache(self) -> None:
        delete_cache = importlib.import_module('.delete_cache', __package__)
        window = delete_cache.DeleteCacheWindow()
        window.show()

    def _show_rules_dialog(self) -> None:
        if self._rules_dialog is None:
            self._rules_dialog = AutoReplaceRulesDialog(self._proxy_master, self._config, self)
        self._rules_dialog.show()
        self._rules_dialog.raise_()
        self._rules_dialog.activateWindow()

    def _show_help(self) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle(tr('ui.gui.proxy_tab.proxy_tab_help'))
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(tr('ui.gui.proxy_tab.this_tab_shows_all_network_traffic_coming'))
        msg.exec()
