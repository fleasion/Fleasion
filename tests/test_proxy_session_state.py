"""Interception settings survive dashboard recreation without showing stale state."""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from typing import TYPE_CHECKING, cast

from PySide6.QtWidgets import QApplication

from fleasion.gui.proxy_tab import ProxyTrafficTab
from fleasion.proxy.master import ProxyMaster

if TYPE_CHECKING:
    from fleasion.proxy.server import FleasionProxy


class _Backend:
    def __init__(self) -> None:
        self.enabled = False
        self.match = ''

    def set_intercept_all_hosts(self, enabled: bool) -> None:
        self.enabled = enabled

    def set_intercept_match(self, value: str) -> None:
        self.match = value

    @staticmethod
    def get_request_log() -> list[object]:
        return []


_app: QApplication | None = None


def _qapp() -> QApplication:
    global _app
    instance = QApplication.instance()
    _app = cast('QApplication', instance) if instance is not None else QApplication([])
    return _app


def test_dashboard_recreation_reflects_current_backend_intercept_all() -> None:
    app = _qapp()
    master = ProxyMaster.__new__(ProxyMaster)
    setattr(master, '_env_proxy_intercept_all', False)
    setattr(master, '_env_proxy_intercept_match', '')
    backend = _Backend()
    setattr(master, '_proxy', cast('FleasionProxy', backend))

    first = ProxyTrafficTab(None, proxy_master=master)
    try:
        assert first.ui.enableCheckBox.isChecked() is False
        first.ui.enableCheckBox.setChecked(True)
        first.ui.interceptEdit.setText('assetdelivery.roblox.com')
        assert master.get_env_proxy_intercept_all() is True
        assert master.get_env_proxy_intercept_match() == 'assetdelivery.roblox.com'
        assert backend.match == 'assetdelivery.roblox.com'
        assert backend.enabled is True
    finally:
        first.close()
        first.deleteLater()
        app.processEvents()

    # The same app keeps ProxyMaster running while its dashboard is recreated
    second = ProxyTrafficTab(None, proxy_master=master)
    try:
        assert second.ui.enableCheckBox.isChecked() is True
        assert second.ui.interceptEdit.text() == 'assetdelivery.roblox.com'
        second.ui.enableCheckBox.setChecked(False)
        second.ui.interceptEdit.clear()
        assert master.get_env_proxy_intercept_match() == ''
        assert backend.match == ''
        assert master.get_env_proxy_intercept_all() is False
        assert backend.enabled is False
    finally:
        second.close()
        second.deleteLater()
        app.processEvents()

    third = ProxyTrafficTab(None, proxy_master=master)
    try:
        assert third.ui.enableCheckBox.isChecked() is False
        assert third.ui.interceptEdit.text() == ''
    finally:
        third.close()
        third.deleteLater()
        app.processEvents()
