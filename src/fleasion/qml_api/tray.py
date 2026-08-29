"""Native system-tray bridge for the QML desktop runtime.

Qt 6.9's ``Qt.labs.platform`` QML menu wrapper can crash the Linux DBusMenu
backend when dynamically-created menu items change after publication. Fleasion's
profile submenu is dynamic, so keep the visual application in QML while using
Qt Widgets' stable QSystemTrayIcon/QMenu implementation for the native tray.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..localization import tr
from ..translations.qml_sources import QML_SOURCE_IDS
from ..utils import get_icon_path
from .replacer import ReplacerApi


def _qml_text(source: str) -> str:
    """Translate a string already cataloged by the QML localization bridge."""
    return tr(QML_SOURCE_IDS[source])


class TrayApi(QObject):
    """Own Fleasion's native tray icon and dynamically-updated profile menu."""

    dashboardRequested = Signal()
    pageRequested = Signal(str)
    aboutRequested = Signal()
    cacheCleanupRequested = Signal()
    quitRequested = Signal()

    def __init__(self, replacer: ReplacerApi, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._replacer = replacer
        self._profile_actions: dict[str, QAction] = {}
        self._menu = QMenu()
        self._tray = QSystemTrayIcon(self)

        if icon_path := get_icon_path():
            self._tray.setIcon(QIcon(str(icon_path)))
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)
        self._replacer.configsChanged.connect(self._sync_profiles)

        self._build_menu()
        self._tray.show()

    @Property(bool)
    def available(self) -> bool:
        """Return whether the current desktop advertises a system tray host."""
        return bool(QSystemTrayIcon.isSystemTrayAvailable())

    def _add_action(
        self,
        text: str,
        callback: Callable[[], None],
        *,
        menu: QMenu | None = None,
    ) -> QAction:
        action = QAction(text, menu or self._menu)
        action.triggered.connect(lambda _checked=False: callback())
        (menu or self._menu).addAction(action)
        return action

    def _build_menu(self) -> None:
        self._menu.clear()
        self._profile_actions.clear()

        self._open_action = self._add_action(
            _qml_text('Open dashboard'), self.dashboardRequested.emit
        )
        self._menu.addSeparator()

        self._profiles_menu = self._menu.addMenu(_qml_text('Replacement profiles'))
        self._sync_profiles()

        self._menu.addSeparator()
        self._clear_cache_action = self._add_action(
            _qml_text('Clear Roblox cache'), self.cacheCleanupRequested.emit
        )
        self._logs_action = self._add_action(
            _qml_text('Open logs'), lambda: self._open_page('logs')
        )
        self._settings_action = self._add_action(
            _qml_text('Settings'), lambda: self._open_page('settings')
        )
        self._about_action = self._add_action(
            _qml_text('About Fleasion'), self.aboutRequested.emit
        )

        self._menu.addSeparator()
        self._exit_action = self._add_action(
            _qml_text('Exit Fleasion'), self.quitRequested.emit
        )
        self._tray.setToolTip(_qml_text('Fleasion · Roblox asset tools'))

    def _open_page(self, page: str) -> None:
        self.pageRequested.emit(page)
        self.dashboardRequested.emit()

    @Slot()
    def _sync_profiles(self) -> None:
        names = list(self._replacer.configs)
        enabled = set(self._replacer.enabledConfigs)

        if list(self._profile_actions) != names:
            self._profiles_menu.clear()
            self._profile_actions.clear()
            for name in names:
                action = QAction(name, self._profiles_menu)
                action.setCheckable(True)
                action.triggered.connect(
                    lambda checked=False, profile=name: self._replacer.setConfigEnabled(
                        profile, bool(checked)
                    )
                )
                self._profiles_menu.addAction(action)
                self._profile_actions[name] = action

        for name, action in self._profile_actions.items():
            blocked = action.blockSignals(True)
            action.setChecked(name in enabled)
            action.blockSignals(blocked)
        self._profiles_menu.setEnabled(bool(names))

    @Slot()
    def retranslate(self) -> None:
        """Refresh static tray labels after an in-session first-run language change."""
        self._open_action.setText(_qml_text('Open dashboard'))
        self._profiles_menu.setTitle(_qml_text('Replacement profiles'))
        self._clear_cache_action.setText(_qml_text('Clear Roblox cache'))
        self._logs_action.setText(_qml_text('Open logs'))
        self._settings_action.setText(_qml_text('Settings'))
        self._about_action.setText(_qml_text('About Fleasion'))
        self._exit_action.setText(_qml_text('Exit Fleasion'))
        self._tray.setToolTip(_qml_text('Fleasion · Roblox asset tools'))

    @Slot(int)
    def _on_activated(self, reason: int) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.dashboardRequested.emit()

    @Slot()
    def shutdown(self) -> None:
        """Withdraw the tray before Qt tears down DBus/native-menu objects."""
        self._replacer.configsChanged.disconnect(self._sync_profiles)
        self._tray.hide()
        self._tray.setContextMenu(None)
        self._menu.clear()
        self._menu.deleteLater()
