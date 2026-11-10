"""User-consented, non-blocking macOS Rosemary launch from the dashboard."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QMessageBox, QWidget

from fleasion.localization import tr
from fleasion.utils import macos_rosemary_gate, platform_macos
from fleasion.utils.logging import log_buffer
from fleasion.utils.macos_rosemary_installer import (
    PINNED_BUILD,
    PinnedRuntimeError,
    host_arch,
    install_root,
    installed_binary,
    release_url,
)
from fleasion.utils.macos_rosemary_memory import active_session

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.master import ProxyMaster


class ManagedRobloxLaunch(QObject):
    """Keep prompts and status updates on Qt's thread, and network I/O off it."""

    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(
        self,
        parent: QWidget,
        config_manager: ConfigManager,
        proxy_master: ProxyMaster | None,
    ) -> None:
        super().__init__(parent)
        self._window = parent
        self._config_manager = config_manager
        self._proxy_master = proxy_master
        self._busy = False
        self.progress.connect(self._show_progress)
        self.finished.connect(self._on_finished)

    @property
    def busy(self) -> bool:
        return self._busy

    @Slot()
    def launch(self) -> None:  # ruff: ignore[too-many-return-statements]
        if not macos_rosemary_gate.MACOS_ROSEMARY_AVAILABLE:
            return
        if self._busy:
            return
        if (session := active_session()) is not None:
            QMessageBox.information(
                self._window,
                tr('macos_rosemary.title'),
                tr('macos_rosemary.already_running', pid=session.process.pid),
            )
            return
        if self._proxy_master is None:
            QMessageBox.warning(
                self._window,
                tr('macos_rosemary.title'),
                tr('macos_rosemary.proxy_missing'),
            )
            return
        if installed_binary() is None:
            if not PINNED_BUILD.available:
                QMessageBox.warning(
                    self._window,
                    tr('macos_rosemary.title'),
                    tr('macos_rosemary.not_available'),
                )
                return
            consent = QMessageBox.question(
                self._window,
                tr('macos_rosemary.consent_title'),
                tr(
                    'macos_rosemary.consent_message',
                    source=PINNED_BUILD.source_commit,
                    url=release_url(PINNED_BUILD, host_arch()),
                    destination=install_root(),
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if consent != QMessageBox.StandardButton.Yes:
                return
        if platform_macos.is_roblox_running():
            choice = QMessageBox.question(
                self._window,
                tr('macos_rosemary.close_native_title'),
                tr('macos_rosemary.close_native_message'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        self._busy = True
        self.progress.emit(tr('macos_rosemary.starting'))
        threading.Thread(
            target=self._launch_in_worker, name='FleasionMacOSRosemary', daemon=True
        ).start()

    def _launch_in_worker(self) -> None:
        from fleasion.utils.macos_rosemary_launcher import launch_managed

        try:
            proxy_master = self._proxy_master
            if proxy_master is None:
                msg = 'The Fleasion proxy is unavailable'
                raise PinnedRuntimeError(msg)
            launch_managed(self._config_manager, proxy_master, announce=self.progress.emit)
        except (OSError, RuntimeError, ValueError, PinnedRuntimeError) as error:
            log_buffer.log('Launcher', f'Managed macOS Rosemary launch failed: {error}')
            self.finished.emit(False, str(error))  # ruff: ignore[boolean-positional-value-in-call]
        else:
            self.finished.emit(True, tr('macos_rosemary.success'))  # ruff: ignore[boolean-positional-value-in-call]

    @Slot(str)
    def _show_progress(self, message: str) -> None:
        log_buffer.log('Launcher', message)

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str) -> None:
        self._busy = False
        if success:
            QMessageBox.information(self._window, tr('macos_rosemary.title'), message)
        else:
            QMessageBox.warning(self._window, tr('macos_rosemary.launch_failed'), message)
