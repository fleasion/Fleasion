"""Mandatory first-run macOS launch guidance before Fleasion-managed Roblox use."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast, override

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fleasion.localization import tr

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent, QScreen, QShowEvent

    from fleasion.config.manager import ConfigManager


READ_SECONDS = 20


class MacOSRosemaryNoticeDialog(QDialog):
    """Compact instructions that require a timed, explicit acknowledgment."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._can_accept = False
        self.setWindowTitle(tr('macos_rosemary.title'))
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, on=False)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, on=False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        heading = QLabel(tr('macos_rosemary.notice_heading'), self)
        heading.setObjectName('MacOSRosemaryNoticeHeadline')
        heading.setWordWrap(True)
        heading_font = heading.font()
        heading_font.setBold(True)
        heading_font.setPointSize(max(heading_font.pointSize() + 2, 13))
        heading.setFont(heading_font)
        layout.addWidget(heading)

        body = QLabel(tr('macos_rosemary.notice'), self)
        body.setObjectName('MacOSRosemaryNoticeBody')
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body)
        self._heading = heading
        self._body = body

        actions = QHBoxLayout()
        actions.addStretch()
        self.ok_button = QPushButton(
            tr('macos_rosemary.read_countdown', seconds=READ_SECONDS), self
        )
        self.ok_button.setObjectName('MacOSRosemaryNoticeAcknowledge')
        self.ok_button.setEnabled(False)
        self.ok_button.setDefault(False)
        self.ok_button.setAutoDefault(False)
        self.ok_button.clicked.connect(self.accept)
        actions.addWidget(self.ok_button)
        layout.addLayout(actions)

        self._elapsed = QElapsedTimer()
        self._countdown = QTimer(self)
        self._countdown.setInterval(200)
        self._countdown.timeout.connect(self._update_countdown)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        screen = cast('QScreen | None', self.screen() or QApplication.primaryScreen())
        if screen is not None:
            available = screen.availableGeometry()
            self.setFixedWidth(min(650, max(1, available.width() - 48)))
        super().showEvent(event)
        # Qt's size hint uses the unwrapped label width, which otherwise gives
        # the heading surplus height and vertically centers its text on macOS
        layout = self.layout()
        if isinstance(layout, QVBoxLayout):
            margins = layout.contentsMargins()
            text_width = max(1, self.width() - margins.left() - margins.right())
            for label in (self._heading, self._body):
                label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                label.setFixedHeight(label.heightForWidth(text_width))
            self.adjustSize()
        if screen is not None:
            frame = self.frameGeometry()
            frame.moveCenter(screen.availableGeometry().center())
            self.move(frame.topLeft())
        if not self._elapsed.isValid():
            self._elapsed.start()
            self._countdown.start()

    def _update_countdown(self) -> None:
        if not self._elapsed.isValid():
            return
        remaining_ms = max(0, READ_SECONDS * 1000 - self._elapsed.elapsed())
        remaining = math.ceil(remaining_ms / 1000)
        if remaining:
            self.ok_button.setText(tr('macos_rosemary.read_countdown', seconds=remaining))
            return
        self._countdown.stop()
        self._can_accept = True
        self.ok_button.setText(tr('macos_rosemary.read_button'))
        self.ok_button.setEnabled(True)

    @override
    def accept(self) -> None:
        if self._can_accept:
            super().accept()

    @override
    def reject(self) -> None:
        return

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()


def show_macos_rosemary_notice(
    config_manager: ConfigManager, parent: QWidget | None = None
) -> bool:
    """Persist the existing one-time flag only after explicit acknowledgment."""
    dialog = MacOSRosemaryNoticeDialog(parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    config_manager.settings['macos_rosemary_notice_shown'] = True
    config_manager.save()
    return True
