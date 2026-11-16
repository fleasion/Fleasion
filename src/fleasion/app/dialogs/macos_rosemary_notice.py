"""Mandatory first-run macOS launch guidance before Fleasion-managed Roblox use."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEasingCurve, QElapsedTimer, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QDialog, QGraphicsDropShadowEffect, QLabel, QVBoxLayout, QWidget

from fleasion.app.dialogs.common import FirstTimeSetupDialog
from fleasion.localization import tr

if TYPE_CHECKING:
    from PySide6.QtGui import QShowEvent

    from fleasion.config.manager import ConfigManager


READ_SECONDS = 20


class MacOSRosemaryNoticeDialog(FirstTimeSetupDialog):
    """Show a persistent launch warning with a timed explicit acknowledgment."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('macos_rosemary.title'))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, on=False)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, on=False)
        self.set_text(tr('macos_rosemary.notice'))

        headline = QLabel(tr('macos_rosemary.notice_heading'), self)
        headline.setObjectName('MacOSRosemaryNoticeHeadline')
        headline.setWordWrap(True)
        headline_font = headline.font()
        headline_font.setBold(True)
        headline_font.setPointSize(max(headline_font.pointSize() + 3, 14))
        headline.setFont(headline_font)
        layout = self.layout()
        if isinstance(layout, QVBoxLayout):
            layout.insertWidget(0, headline)

        button = self.ok_button
        button.setObjectName('MacOSRosemaryNoticeAcknowledge')
        button.setEnabled(False)
        button.setMinimumSize(240, 54)
        button.setText(tr('macos_rosemary.read_countdown', seconds=READ_SECONDS))
        palette = button.palette()
        highlight = palette.color(QPalette.ColorRole.Highlight)
        text = palette.color(QPalette.ColorRole.HighlightedText)
        disabled = palette.color(QPalette.ColorRole.Mid)
        base = palette.color(QPalette.ColorRole.Window)
        button.setStyleSheet(
            'QPushButton#MacOSRosemaryNoticeAcknowledge {'
            f'background-color: {highlight.name()}; color: {text.name()};'
            f'border: 2px solid {highlight.name()};'
            'border-radius: 9px; padding: 10px 20px; font-size: 14px; font-weight: 800;'
            '}'
            'QPushButton#MacOSRosemaryNoticeAcknowledge:disabled {'
            f'background-color: {base.name()}; color: {disabled.name()};'
            f'border: 2px solid {disabled.name()};'
            '}'
        )
        glow = QGraphicsDropShadowEffect(button)
        glow.setBlurRadius(17)
        glow.setOffset(0, 0)
        glow.setColor(highlight)
        button.setGraphicsEffect(glow)
        self._attention = QPropertyAnimation(glow, b'blurRadius', self)
        self._attention.setDuration(1000)
        self._attention.setStartValue(17.0)
        self._attention.setKeyValueAt(0.5, 34.0)
        self._attention.setEndValue(17.0)
        self._attention.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._attention.setLoopCount(4)

        self._elapsed = QElapsedTimer()
        self._countdown = QTimer(self)
        self._countdown.setInterval(200)
        self._countdown.timeout.connect(self._update_countdown)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
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
        self.allow_accept()
        self.ok_button.setText(tr('macos_rosemary.read_button'))
        self.ok_button.setEnabled(True)
        self.ok_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._attention.start()


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
