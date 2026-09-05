"""Background startup checks and launch integration maintenance."""

from __future__ import annotations

from threading import Thread
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Slot

from fleasion.app.qt_runtime import linux_gui_dependency_packages
from fleasion.proxy.master import find_roblox_dirs
from fleasion.utils.autostart import sync_autostart
from fleasion.utils.desktop_integration import sync_desktop_integration
from fleasion.utils.logging import log_buffer
from fleasion.utils.paths import CONFIG_DIR

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager


class StartupTasks(QObject):
    """Keep blocking probes off Qt and finish file writes before shutdown."""

    completed = Signal()

    def __init__(self, config: ConfigManager, *, autostart: bool, parent: QObject) -> None:
        super().__init__(parent)
        self._desktop = config.first_time_setup_complete and config.desktop_integration
        self._autostart = config.first_time_setup_complete and autostart
        self._proxy_mode = config.proxy_mode
        self._thread: Thread | None = None
        self.missing_packages: list[str] = []
        self.desktop_failed = False
        self.autostart_failed = False
        self.roblox_found = True
        self.stopping = False

    @Slot()
    def start(self) -> None:
        if self._thread is not None or self.stopping:
            return
        self._thread = Thread(target=self._run, name='fleasion-startup', daemon=False)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.missing_packages = linux_gui_dependency_packages()
            self.roblox_found = bool(find_roblox_dirs())
            self._sync_integrations()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            log_buffer.log('Startup', f'Environment check failed: {exc}')
        finally:
            self.completed.emit()

    def _sync_integrations(self) -> None:
        if self._desktop:
            try:
                self.desktop_failed = not sync_desktop_integration(enabled=True)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                self.desktop_failed = True
                log_buffer.log('DesktopIntegration', f'Launch integration sync failed: {exc}')
        if self._autostart:
            try:
                self.autostart_failed = not sync_autostart(
                    enabled=True, config_dir=CONFIG_DIR, proxy_mode=self._proxy_mode
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                self.autostart_failed = True
                log_buffer.log('Autostart', f'Launch autostart sync failed: {exc}')

    @Slot()
    def shutdown(self) -> None:
        self.stopping = True
        if self._thread is not None:
            self._thread.join()
