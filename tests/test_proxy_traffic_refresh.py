from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication

from fleasion.gui.proxy_tab import ProxyTrafficTab

if TYPE_CHECKING:
    from fleasion.gui.proxy_tab import ProxyTrafficSource
    from fleasion.proxy.types import ProxyRequestLogEntry

_app: QApplication | None = None


class _TrafficSource:
    def __init__(self) -> None:
        self.entries: list[ProxyRequestLogEntry] = [
            {
                'id': 1,
                'time': 1.0,
                'host': 'example.com',
                'port': 443,
                'method': 'GET',
                'path': '/asset',
                'intercepted': True,
                'status': None,
                'size': 0,
                'ms': None,
                'request_raw': None,
                'response_raw': None,
                'pending_stage': None,
                'was_intercepted': False,
            }
        ]

    def get_env_proxy_intercept_match(self) -> str:
        return ''

    def get_env_proxy_intercept_all(self) -> bool:
        return False

    def get_env_proxy_traffic(self) -> list[ProxyRequestLogEntry]:
        return [entry.copy() for entry in self.entries]

    def format_env_proxy_request_preview(self, entry: ProxyRequestLogEntry) -> str:
        return 'request'

    def format_env_proxy_response_preview(self, entry: ProxyRequestLogEntry) -> str:
        return str(entry.get('status'))


def test_unchanged_poll_keeps_items_and_changed_poll_updates_selected_entry() -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    source = _TrafficSource()
    tab = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        tab.show()
        _app.processEvents()
        table = tab.ui.trafficTable
        table.selectRow(0)
        item = table.item(0, 0)
        assert item is not None
        tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
        assert table.item(0, 0) is item
        assert table.currentRow() == 0

        source.entries[0]['status'] = 200
        source.entries[0]['response_raw'] = b'HTTP/1.1 200 OK\r\n\r\n'
        tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
        status_item = table.item(0, 5)
        assert status_item is not None
        assert status_item.text() == '200'
        assert table.currentRow() == 0
        assert tab.ui.responseText.toPlainText() == '200'

        tab.ui.filterEdit.setText('missing')
        assert table.rowCount() == 0
        tab.ui.filterEdit.clear()
        assert table.rowCount() == 1
    finally:
        tab.close()
        tab.deleteLater()
        _app.processEvents()


def test_hidden_tab_defers_render_but_shows_latest_traffic_immediately() -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    source = _TrafficSource()
    tab = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        assert tab.ui.trafficTable.rowCount() == 0
        source.entries[0]['status'] = 200
        tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
        assert tab.ui.trafficTable.rowCount() == 0
        tab.show()
        _app.processEvents()
        assert tab.ui.trafficTable.rowCount() == 1
        status = tab.ui.trafficTable.item(0, 5)
        assert status is not None and status.text() == '200'
    finally:
        tab.close()
        tab.deleteLater()
        _app.processEvents()


def test_growing_mutable_response_updates_the_visible_size() -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    source = _TrafficSource()
    response = bytearray(b'first')
    source.entries[0]['response_raw'] = response
    source.entries[0]['size'] = len(response)
    tab = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        tab.show()
        _app.processEvents()
        response.extend(b' second')
        source.entries[0]['size'] = len(response)
        tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
        size = tab.ui.trafficTable.item(0, 6)
        assert size is not None and size.text() == '12 B'
    finally:
        tab.close()
        tab.deleteLater()
        _app.processEvents()


def test_hidden_poll_skips_snapshot_unless_preservation_is_enabled() -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    source = _TrafficSource()
    tab = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        source.entries[0]['status'] = 200
        tab._poll_traffic()  # pyright: ignore[reportPrivateUsage]
        assert tab._traffic[0]['status'] is None  # pyright: ignore[reportPrivateUsage]
        tab._preserve_enabled = True  # pyright: ignore[reportPrivateUsage]
        tab._poll_traffic()  # pyright: ignore[reportPrivateUsage]
        assert tab._traffic[0]['status'] == 200  # pyright: ignore[reportPrivateUsage]
        tab._preserve_enabled = False  # pyright: ignore[reportPrivateUsage]
        source.entries[0]['status'] = 201
        tab.show()
        _app.processEvents()
        status = tab.ui.trafficTable.item(0, 5)
        assert status is not None and status.text() == '201'
    finally:
        tab.close()
        tab.deleteLater()
        _app.processEvents()


def test_hidden_traffic_update_preserves_selected_request_edits() -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    source = _TrafficSource()
    tab = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        tab.show()
        _app.processEvents()
        tab.ui.trafficTable.selectRow(0)
        tab.ui.requestText.setPlainText('edited request')
        tab.hide()
        source.entries[0]['pending_stage'] = 'response'
        source.entries[0]['status'] = 200
        source.entries[0]['response_raw'] = b'HTTP/1.1 200 OK\r\n\r\n'
        tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
        tab.show()
        _app.processEvents()
        assert tab.ui.trafficTable.currentRow() == 0
        assert tab.ui.requestText.toPlainText() == 'edited request'
        assert tab.ui.responseText.toPlainText() == '200'
        assert not tab.ui.responseText.isReadOnly()
    finally:
        tab.close()
        tab.deleteLater()
        _app.processEvents()


def test_changed_row_updates_without_rebuilding_other_sorted_selected_rows() -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    source = _TrafficSource()
    source.entries.extend([source.entries[0].copy(), source.entries[0].copy()])
    for index, entry in enumerate(source.entries):
        entry['id'] = index + 1
        entry['host'] = f'host-{index}.test'
    tab = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        tab.show()
        _app.processEvents()
        table = tab.ui.trafficTable
        table.sortItems(3, Qt.SortOrder.DescendingOrder)
        tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
        model = table.selectionModel()
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        model.select(table.model().index(0, 0), flags)
        model.select(table.model().index(1, 0), flags)
        table.setCurrentCell(1, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        tab._on_selection_changed()  # pyright: ignore[reportPrivateUsage]
        tab.ui.requestText.setPlainText('edited')
        unchanged = table.item(0, 0)
        assert unchanged is not None
        unchanged_id = unchanged.data(Qt.ItemDataRole.UserRole)
        source.entries[1]['status'] = 201
        source.entries[1]['size'] = 123
        tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
        selected: set[int] = set()
        for index in model.selectedRows():
            item = table.item(index.row(), 0)
            assert item is not None
            selected.add(cast('int', item.data(Qt.ItemDataRole.UserRole)))
        assert selected == {2, 3}
        assert table.item(0, 0) is unchanged
        assert unchanged.data(Qt.ItemDataRole.UserRole) == unchanged_id
        status = table.item(1, 5)
        assert status is not None and status.text() == '201'
        assert tab.ui.requestText.toPlainText() == 'edited'
    finally:
        tab.close()
        tab.deleteLater()
        _app.processEvents()


def test_incremental_sorted_rows_match_full_rebuild() -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    source = _TrafficSource()
    source.entries.extend(source.entries[0].copy() for _ in range(4))
    for index, entry in enumerate(source.entries):
        entry['id'] = index + 1
        entry['status'] = 200 + index
        entry['host'] = f'host-{index}.test'
    incremental = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    rebuilt = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        incremental.show()
        rebuilt.show()
        _app.processEvents()
        for tab in (incremental, rebuilt):
            tab.ui.trafficTable.sortItems(5, Qt.SortOrder.DescendingOrder)
            tab.ui.highlightEdit.setText('host-2')
        for index, new_status in ((0, 210), (3, 190), (2, 220)):
            source.entries[index]['status'] = new_status
            source.entries[index]['size'] += 99
            incremental._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
            rebuilt._entries_by_id = {}  # pyright: ignore[reportPrivateUsage]
            rebuilt._refresh_traffic()  # pyright: ignore[reportPrivateUsage]

            def rows(tab: ProxyTrafficTab) -> list[list[tuple[str, str]]]:
                table = tab.ui.trafficTable
                result: list[list[tuple[str, str]]] = []
                for row in range(table.rowCount()):
                    cells: list[tuple[str, str]] = []
                    for column in range(table.columnCount()):
                        item = table.item(row, column)
                        assert item is not None
                        cells.append((item.text(), item.background().color().name()))
                    result.append(cells)
                return result

            assert rows(incremental) == rows(rebuilt)
    finally:
        for tab in (incremental, rebuilt):
            tab.close()
            tab.deleteLater()
        _app.processEvents()


def test_changed_sort_key_restores_source_order_for_equal_values() -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    source = _TrafficSource()
    source.entries.extend(source.entries[0].copy() for _ in range(2))
    for index, entry in enumerate(source.entries):
        entry['id'] = index + 1
        entry['status'] = 201 if index == 1 else 200
    tab = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        tab.show()
        _app.processEvents()
        tab.ui.trafficTable.sortItems(5, Qt.SortOrder.AscendingOrder)
        source.entries[1]['status'] = 200
        tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
        table = tab.ui.trafficTable
        ids: list[object] = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            assert item is not None
            ids.append(item.data(Qt.ItemDataRole.UserRole))
        assert ids == [1, 2, 3]
    finally:
        tab.close()
        tab.deleteLater()
        _app.processEvents()


def test_poll_after_sort_change_restores_source_order_for_equal_values() -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    source = _TrafficSource()
    source.entries.extend(source.entries[0].copy() for _ in range(2))
    for index, entry in enumerate(source.entries):
        entry['id'] = index + 1
        entry['status'] = 200
        entry['host'] = f'host-{index}'
    tab = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        tab.show()
        _app.processEvents()
        table = tab.ui.trafficTable
        table.sortItems(3, Qt.SortOrder.DescendingOrder)
        table.sortItems(5, Qt.SortOrder.AscendingOrder)
        tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
        ids: list[object] = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            assert item is not None
            ids.append(item.data(Qt.ItemDataRole.UserRole))
        assert ids == [1, 2, 3]
    finally:
        tab.close()
        tab.deleteLater()
        _app.processEvents()


@pytest.mark.parametrize('order', [Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder])
def test_incremental_row_add_and_evict_match_full_rebuild(order: Qt.SortOrder) -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    source = _TrafficSource()
    source.entries.extend(source.entries[0].copy() for _ in range(4))
    for index, entry in enumerate(source.entries):
        entry['id'] = index + 1
        entry['path'] = f'/asset/{index}'
    incremental = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    rebuilt = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        for tab in (incremental, rebuilt):
            tab.show()
            tab.ui.trafficTable.sortItems(0, order)
        _app.processEvents()
        for tab in (incremental, rebuilt):
            tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]

        for tab in (incremental, rebuilt):
            table = tab.ui.trafficTable
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == 3:
                    table.selectRow(row)
                    break
            tab.ui.requestText.setPlainText('edited request')

        added = source.entries[0].copy()
        added['id'] = 6
        added['path'] = '/asset/new'
        source.entries.append(added)
        source.entries.pop(0)
        source.entries[1]['status'] = 201
        incremental._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
        rebuilt._entries_by_id = {}  # pyright: ignore[reportPrivateUsage]
        rebuilt._refresh_traffic()  # pyright: ignore[reportPrivateUsage]

        def table_rows(tab: ProxyTrafficTab) -> list[list[str]]:
            table = tab.ui.trafficTable
            result: list[list[str]] = []
            for row in range(table.rowCount()):
                values: list[str] = []
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    assert item is not None
                    values.append(item.text())
                result.append(values)
            return result

        assert table_rows(incremental) == table_rows(rebuilt)
        for tab in (incremental, rebuilt):
            table = tab.ui.trafficTable
            current = table.item(table.currentRow(), 0)
            assert current is not None and current.data(Qt.ItemDataRole.UserRole) == 3
            assert tab.ui.requestText.toPlainText() == 'edited request'
    finally:
        for tab in (incremental, rebuilt):
            tab.close()
            tab.deleteLater()
        _app.processEvents()


def test_evicted_current_row_clears_the_request_and_response() -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    source = _TrafficSource()
    second = source.entries[0].copy()
    second['id'] = 2
    source.entries.append(second)
    tab = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        tab.show()
        _app.processEvents()
        tab.ui.trafficTable.selectRow(0)
        tab.ui.requestText.setPlainText('edited request')
        source.entries.pop(0)
        tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
        assert tab.ui.requestText.toPlainText() == ''
        assert tab.ui.responseText.toPlainText() == ''
        assert tab.ui.trafficTable.rowCount() == 1
    finally:
        tab.close()
        tab.deleteLater()
        _app.processEvents()
