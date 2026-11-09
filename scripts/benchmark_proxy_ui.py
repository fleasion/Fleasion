"""Measure synthetic traffic-table refreshes with Qt."""

# ruff: file-ignore[private-member-access]

from __future__ import annotations

import argparse
import logging
import time
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from fleasion.gui.proxy_tab import ProxyTrafficTab

if TYPE_CHECKING:
    from fleasion.gui.proxy_tab import ProxyTrafficSource
    from fleasion.proxy.types import ProxyRequestLogEntry

log = logging.getLogger(__name__)


class _Source:
    def __init__(self, rows: int) -> None:
        self.entries: list[ProxyRequestLogEntry] = [
            {
                'id': index,
                'time': 0.0,
                'host': 'assetdelivery.roblox.com',
                'port': 443,
                'method': 'GET',
                'path': f'/asset/{index}',
                'intercepted': True,
                'status': 200,
                'size': 1024,
                'ms': 12,
                'request_raw': None,
                'response_raw': None,
                'pending_stage': None,
                'was_intercepted': False,
            }
            for index in range(rows)
        ]

    @staticmethod
    def get_env_proxy_intercept_match() -> str:
        return ''

    @staticmethod
    def get_env_proxy_intercept_all() -> bool:
        return False

    def get_env_proxy_traffic(self) -> list[ProxyRequestLogEntry]:
        return [entry.copy() for entry in self.entries]


def _measure(tab: ProxyTrafficTab, source: _Source, iterations: int, *, rebuild: bool) -> None:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    for _ in range(iterations):
        entry = source.entries.pop(0)
        entry['id'] += len(source.entries) + 1
        source.entries.append(entry)
        if rebuild:
            tab._entries_by_id = {}  # pyright: ignore[reportPrivateUsage]
        tab._refresh_traffic()  # pyright: ignore[reportPrivateUsage]
    wall = (time.perf_counter() - start_wall) * 1000 / iterations
    cpu = (time.process_time() - start_cpu) * 1000 / iterations
    table = tab.ui.trafficTable
    shown: list[int] = []
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is None:
            msg = 'Traffic table is missing an entry'
            raise RuntimeError(msg)
        shown.append(cast('int', item.data(Qt.ItemDataRole.UserRole)))
    if shown != sorted(entry['id'] for entry in source.entries):
        msg = 'Traffic table contents differ from the input'
        raise ValueError(msg)
    log.info(
        '%s: %.2f ms wall, %.2f ms CPU per refresh',
        'Full rebuild' if rebuild else 'Incremental',
        wall,
        cpu,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=1000)
    parser.add_argument('--iterations', type=int, default=20)
    args = parser.parse_args()
    if args.rows <= 0 or args.iterations <= 0:
        parser.error('--rows and --iterations must be positive')
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    app = QApplication.instance() or QApplication([])
    source = _Source(args.rows)
    tab = ProxyTrafficTab(None, cast('ProxyTrafficSource', source))
    try:
        tab.show()
        app.processEvents()
        _measure(tab, source, args.iterations, rebuild=False)
        _measure(tab, source, args.iterations, rebuild=True)
    finally:
        tab.close()
        tab.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    main()
