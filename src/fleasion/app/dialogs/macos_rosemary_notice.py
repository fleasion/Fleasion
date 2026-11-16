"""Mandatory first-run macOS launch guidance before Fleasion-managed Roblox use."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

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
        button.setDefault(False)
        button.setAutoDefault(False)
        button.setEnabled(False)
        button.setText(tr('macos_rosemary.read_countdown', seconds=READ_SECONDS))

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
