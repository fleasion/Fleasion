"""Shared startup dialog widgets and window helpers."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, cast, override

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyle,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from fleasion.localization import tr
from fleasion.utils import (
    get_icon_path,
    log_buffer,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent, QScreen, QShowEvent

    from fleasion.app.tray import SystemTray


class FirstTimeSetupDialog(QDialog):
    """Scrollable first-run guide that always keeps its acknowledgement visible."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._can_accept = False
        self.setModal(True)

        layout = QVBoxLayout(self)
        content_layout = QHBoxLayout()

        icon_label = QLabel(self)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        icon_label.setPixmap(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation).pixmap(32, 32)
        )
        content_layout.addWidget(icon_label, 0)

        self._body = QTextBrowser(self)
        self._body.setReadOnly(True)
        self._body.setOpenExternalLinks(False)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setMinimumSize(0, 0)
        content_layout.addWidget(self._body, 1)
        layout.addLayout(content_layout, 1)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.ok_button = QPushButton(self)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        button_layout.addWidget(self.ok_button)
        layout.addLayout(button_layout)

    def set_text(self, text: str) -> None:
        self._body.setPlainText(text)

    def setText(self, text: str) -> None:  # ruff: ignore[invalid-function-name]
        self.set_text(text)

    def allow_accept(self) -> None:
        self._can_accept = True

    def _fit_to_available_screen(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        screen = cast('QScreen | None', screen)
        if screen is None:
            return

        available = screen.availableGeometry()
        max_width = max(1, int(available.width() * 0.90))
        max_height = max(1, int(available.height() * 0.85))
        self.setMaximumSize(max_width, max_height)
        self.resize(min(680, max_width), min(620, max_height))

        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._fit_to_available_screen()

    def accept(self) -> None:
        if self._can_accept:
            super().accept()

    def reject(self) -> None:
        return

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()


class ForcedAcknowledgeMessageBox(QMessageBox):
    """Message box that cannot be dismissed until explicitly allowed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._can_close = False

    def allow_close(self) -> None:
        self._can_close = True

    def done(self, result: int) -> None:
        if self._can_close:
            super().done(result)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._can_close:
            event.accept()
        else:
            event.ignore()


class MacOSAuthSourceDialog(QDialog):
    """Browser-token startup prompt that only closes through explicit choices."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.allow_reject = False

    def reject(self) -> None:
        if self.allow_reject:
            super().reject()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.allow_reject:
            event.accept()
        else:
            event.ignore()


def quit_after_modal_closes(
    modal: MacOSAuthSourceDialog | ForcedAcknowledgeMessageBox,
    tray: SystemTray | None = None,
    selected: dict[str, str] | None = None,
) -> None:
    """Close the active modal first, then run the normal quit path."""
    if QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()
    if selected is not None:
        selected['exit'] = '1'
    if isinstance(modal, MacOSAuthSourceDialog):
        modal.allow_reject = True
    if isinstance(modal, ForcedAcknowledgeMessageBox):
        modal.allow_close()
    try:
        modal.reject()
    except RuntimeError as exc:
        log_buffer.log('App', f'Could not close modal before quit: {exc}')

    def _quit() -> None:
        if tray is not None:
            tray.exit_app()
        else:
            QApplication.quit()

    QTimer.singleShot(0, _quit)


def visible_parent_widget() -> QWidget | None:
    """Return the best visible Qt parent for startup dialogs."""
    top = QApplication.topLevelWidgets()
    return next((w for w in top if w.isVisible()), QApplication.activeWindow())


def window_handle(widget: QWidget | None) -> int | None:
    """Return a native window handle for ShellExecuteExW, if Qt has one."""
    if widget is None:
        return None
    try:
        return int(widget.winId())
    except OverflowError, RuntimeError, TypeError, ValueError:
        return None


def show_admin_required_dialog(parent: QWidget | None = None) -> None:
    """Warn that the non-elevated instance cannot provide Fleasion's core behavior."""
    top = QApplication.topLevelWidgets()
    parent_ = parent or visible_parent_widget()
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )

    msg = QMessageBox(parent_)
    if on_top:
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    msg.setWindowTitle(tr('app.administrator_mode_required'))
    msg.setIcon(QMessageBox.Icon.Warning)
    if sys.platform == 'darwin':
        msg.setText(tr('app.fleasion_needs_its_macos_proxy_helper_before'))
        msg.setInformativeText(tr('app.run_fleasion_as_your_normal_macos_user'))
    elif sys.platform.startswith('linux'):
        msg.setText(tr('app.fleasion_needs_administrator_permission_for_linux_interception'))
        msg.setInformativeText(tr('app.linux_support_targets_the_sober_flatpak_client'))
    else:
        msg.setText(tr('app.fleasion_won_t_work_unless_you_re'))
        msg.setInformativeText(tr('app.windows_did_not_start_fleasion_with_administrator'))
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))
    msg.exec()
