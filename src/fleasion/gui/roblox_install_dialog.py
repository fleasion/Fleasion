from __future__ import annotations

import sys
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QIcon
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..localization import tr, verbatim
from ..utils import get_icon_path
from ..utils.paths import FORCED_ROBLOX_VERSION
from ..utils.roblox_installer import InstallCancelled, install_forced_roblox_version


class _InstallSignals(QObject):
    progress = pyqtSignal(str, int, int, float)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)


class PatchNoticeDialog(QDialog):
    WAIT_SECONDS = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('ui.gui.roblox_install_dialog.notice_title'))
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint
        )

        icon_path = get_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))

        self._remaining = self.WAIT_SECONDS

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(tr('ui.gui.roblox_install_dialog.notice_title'))
        title.setStyleSheet('font-size: 11pt; font-weight: bold;')
        layout.addWidget(title)

        body = QLabel(tr('ui.gui.roblox_install_dialog.notice_body'))
        body.setWordWrap(True)
        layout.addWidget(body)

        launch_rule = QLabel(tr('ui.gui.roblox_install_dialog.notice_launch'))
        launch_rule.setWordWrap(True)
        launch_rule.setStyleSheet('font-weight: bold;')
        layout.addWidget(launch_rule)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.close_button = QPushButton()
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self._finish)
        button_layout.addWidget(self.close_button)
        layout.addLayout(button_layout)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._update_close_button()
        self._timer.start()

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
        self._update_close_button()

    def _update_close_button(self) -> None:
        if self._remaining > 0:
            self.close_button.setText(
                tr('ui.gui.roblox_install_dialog.notice_close_wait', seconds=self._remaining)
            )
        else:
            self.close_button.setText(tr('ui.gui.replacer_config.close'))
            self.close_button.setEnabled(True)
            self.close_button.setFocus()

    def _finish(self) -> None:
        if self._remaining <= 0:
            super().accept()

    def reject(self) -> None:
        if self._remaining <= 0:
            super().reject()

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        if self._remaining <= 0:
            super().closeEvent(a0)
        elif a0 is not None:
            a0.ignore()


class RobloxInstallDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('ui.gui.roblox_install_dialog.title'))
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint
        )

        icon_path = get_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))

        self._cancel_event = threading.Event()
        self._done = False
        self.installed_exe: Path | None = None
        self._signals = _InstallSignals()
        self._signals.progress.connect(self._on_progress)
        self._signals.finished.connect(self._on_finished)
        self._signals.error.connect(self._on_error)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        if sys.platform == 'darwin':
            self.title_label = QLabel(tr('ui.gui.roblox_install_dialog.heading_macos'))
        else:
            self.title_label = QLabel(
                tr('ui.gui.roblox_install_dialog.heading_version', version=FORCED_ROBLOX_VERSION)
            )
        self.title_label.setStyleSheet('font-size: 11pt; font-weight: bold;')
        layout.addWidget(self.title_label)

        self.status_label = QLabel(tr('ui.gui.roblox_install_dialog.initializing'))
        self.status_label.setStyleSheet('font-size: 9pt;')
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(22)
        layout.addWidget(self.progress_bar)

        self.detail_label = QLabel('')
        self.detail_label.setStyleSheet('color: palette(placeholder-text); font-size: 8.5pt;')
        layout.addWidget(self.detail_label)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)

        self.cancel_button = QPushButton(tr('ui.gui.roblox_install_dialog.cancel'))
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self._on_cancel)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self._thread = threading.Thread(target=self._run_install, daemon=True)
        self._thread.start()

    def _run_install(self) -> None:
        signals = self._signals

        def progress_cb(step: str, current_bytes: int, total_bytes: int, pct: float) -> None:
            signals.progress.emit(step, current_bytes, total_bytes, pct)

        try:
            exe_path = install_forced_roblox_version(
                progress_cb=progress_cb,
                cancel_event=self._cancel_event,
            )
        except InstallCancelled:
            signals.error.emit('')
        except Exception as exc:
            signals.error.emit(str(exc) or exc.__class__.__name__)
        else:
            signals.finished.emit(str(exe_path))

    def _on_progress(self, step: str, current_bytes: int, total_bytes: int, pct: float) -> None:
        if self._cancel_event.is_set():
            return
        self.status_label.setText(step)
        if total_bytes <= 0 and pct <= 0:
            self.progress_bar.setRange(0, 0)
            self.detail_label.setText('')
            return
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(pct))
        if total_bytes > 1024 * 1024:
            cur_mb = current_bytes / (1024 * 1024)
            tot_mb = total_bytes / (1024 * 1024)
            self.detail_label.setText(verbatim(f'{cur_mb:.1f} MB / {tot_mb:.1f} MB ({pct:.1f}%)'))
        else:
            self.detail_label.setText('')

    def _on_finished(self, exe_path: str) -> None:
        self._done = True
        self.installed_exe = Path(exe_path)
        super().accept()

    def _on_error(self, err_msg: str) -> None:
        self._done = True
        if err_msg and not self._cancel_event.is_set():
            QMessageBox.critical(
                self,
                tr('ui.gui.roblox_install_dialog.failed_title'),
                tr('ui.gui.roblox_install_dialog.failed', error=err_msg),
            )
        super().reject()

    def _on_cancel(self) -> None:
        if self._done or self._cancel_event.is_set():
            return
        self._cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.status_label.setText(tr('ui.gui.roblox_install_dialog.cancelling'))

    def reject(self) -> None:
        if self._done:
            super().reject()
        else:
            self._on_cancel()

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        if self._done:
            super().closeEvent(a0)
            return
        self._on_cancel()
        if a0 is not None:
            a0.ignore()
