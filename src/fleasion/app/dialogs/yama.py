"""Main-thread Yama restriction notice for Rosemary TLS memory scanning."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMessageBox

from fleasion.app.dialogs.common import visible_parent_widget
from fleasion.localization import tr
from fleasion.utils import get_icon_path


class YamaWarningInvoker(QObject):
    """Queue worker-thread Yama diagnostics into a single visible GUI popup."""

    show_warning = Signal(int, str)

    def __init__(self) -> None:
        super().__init__()
        self._dialog: QMessageBox | None = None
        self.show_warning.connect(self._show_warning)

    @Slot(int, str)
    def _show_warning(self, scope: int, reason: str) -> None:
        if scope not in {1, 2, 3}:
            return
        if self._dialog is not None:
            # Do not stack multiple modal windows for one Player's TLS attempts
            self._dialog.raise_()
            self._dialog.activateWindow()
            return

        dialog = QMessageBox(visible_parent_widget())
        self._dialog = dialog
        dialog.setWindowTitle(tr('app.yama.title'))
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(
            tr('app.yama.access_denied' if reason == 'access_denied' else 'app.yama.startup')
        )
        instructions = tr(
            'app.yama.options',
            scope=scope,
            via_fleasion=tr(
                'app.yama.launch_works' if scope == 1 else 'app.yama.launch_may_not_work'
            ),
            reboot=tr('app.yama.scope3_reboot') if scope == 3 else '',
        )
        dialog.setInformativeText(instructions)
        dialog.setDetailedText(tr('app.yama.technical'))
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        dialog.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if icon_path := get_icon_path():
            dialog.setWindowIcon(QIcon(str(icon_path)))
        dialog.finished.connect(self._on_closed)
        dialog.open()

    @Slot(int)
    def _on_closed(self, _code: int) -> None:
        if self._dialog is not None:
            self._dialog.deleteLater()
            self._dialog = None
