"""Nonblocking Linux-client mismatch monitoring for the tray UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from fleasion.utils import log_buffer, run_in_thread
from fleasion.utils.linux_clients import LINUX_CLIENTS_BY_KEY
from fleasion.utils.platform_linux import detect_running_linux_client_mismatch

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.master import ProxyMaster


class LinuxClientMismatchWatcher(QObject):
    """Warn once per explicit-selection/running-client pair, never change it."""

    mismatch_detected = Signal(str, str)
    _scan_complete = Signal(str, str, str)

    def __init__(
        self, config_manager: ConfigManager, proxy_master: ProxyMaster, parent: QObject
    ) -> None:
        super().__init__(parent)
        self._config = config_manager
        self._proxy = proxy_master
        self._scanning = False
        self._stopped = False
        self._seen: set[tuple[str, str]] = set()
        self._scan_complete.connect(self._on_scan_complete)
        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self.check)
        self._timer.start()

    def stop(self) -> None:
        self._stopped = True
        self._timer.stop()

    def check(self) -> None:
        """Start at most one background process scan; never block the GUI."""
        selected = str(getattr(self._config, 'linux_client', 'auto'))
        if (
            self._stopped
            or self._scanning
            or not self._proxy.is_running
            or not self._config.proxy_features_enabled
            or selected not in LINUX_CLIENTS_BY_KEY
        ):
            return
        self._scanning = True

        @run_in_thread
        def _scan() -> None:
            try:
                result = detect_running_linux_client_mismatch(selected)
            except (OSError, ValueError) as exc:
                log_buffer.log('Launcher', f'Linux client mismatch check failed: {exc}')
                result = None
            if self._stopped:
                return
            if result is None:
                self._scan_complete.emit(selected, '', '')
            else:
                self._scan_complete.emit(selected, *result)

        _scan()

    def _on_scan_complete(self, selected: str, expected: str, actual: str) -> None:
        self._scanning = False
        if not expected or not actual:
            return
        if (
            self._stopped
            or str(getattr(self._config, 'linux_client', 'auto')) != selected
            or not self._proxy.is_running
            or not self._config.proxy_features_enabled
            or QApplication.activeModalWidget() is not None
        ):
            return
        pair = expected, actual
        if pair in self._seen:
            return
        self._seen.add(pair)
        self.mismatch_detected.emit(expected, actual)
