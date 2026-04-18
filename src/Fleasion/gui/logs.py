"""Logs window."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QKeySequence, QShortcut, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..utils import APP_NAME, get_icon_path, log_buffer, time_tracker


class LogsWindow(QDialog):
    """Logs viewer window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'{APP_NAME} - Logs')
        self.resize(600, 400)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )

        self._last_count = 0
        self._setup_ui()
        self._set_icon()
        self._start_updates()

    def _set_icon(self):
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(self._get_monospace_font())
        layout.addWidget(self.text_edit)

        bottom = QHBoxLayout()
        bottom.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        copy_btn = QPushButton("Copy All")
        copy_btn.setFixedSize(80, 22)
        copy_btn.clicked.connect(self._copy_all)
        bottom.addWidget(copy_btn)

        bottom.addSpacing(6)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search…")
        self._search_input.setFixedHeight(22)
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search)
        bottom.addWidget(self._search_input, 1)

        bottom.addSpacing(6)

        self.time_label = QLabel()
        self.time_label.setStyleSheet('color: #888; font-size: 9pt;')
        self._refresh_time_label()
        bottom.addWidget(self.time_label)

        layout.addLayout(bottom)
        self.setLayout(layout)

        # Ctrl+F focuses the search bar
        shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        shortcut.activated.connect(self._focus_search)

        # Escape clears + unfocuses search
        esc = QShortcut(QKeySequence("Escape"), self._search_input)
        esc.activated.connect(self._clear_search)

    def _get_monospace_font(self):
        from PyQt6.QtGui import QFont
        font = QFont('Consolas', 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        return font

    def _focus_search(self):
        self._search_input.setFocus()
        self._search_input.selectAll()

    def _clear_search(self):
        self._search_input.clear()
        self.text_edit.setFocus()

    def _on_search(self, text: str):
        """Highlight all occurrences of the search text."""
        extra_selections = []

        if text:
            highlight_fmt = QTextCharFormat()
            highlight_fmt.setBackground(QColor("#f5c518"))
            highlight_fmt.setForeground(QColor("#000000"))

            doc = self.text_edit.document()
            cursor = doc.find(text)
            first_cursor = None
            while not cursor.isNull():
                if first_cursor is None:
                    first_cursor = cursor
                sel = QTextEdit.ExtraSelection()
                sel.format = highlight_fmt
                sel.cursor = cursor
                extra_selections.append(sel)
                cursor = doc.find(text, cursor)

            if first_cursor:
                self.text_edit.setTextCursor(first_cursor)
                self.text_edit.ensureCursorVisible()

        self.text_edit.setExtraSelections(extra_selections)

    def _start_updates(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_logs)
        self.timer.start(250)
        self._update_logs()

        self.time_timer = QTimer()
        self.time_timer.timeout.connect(self._refresh_time_label)
        self.time_timer.start(1000)

    def showEvent(self, a0):
        if not self.timer.isActive():
            self.timer.start(250)
        if not self.time_timer.isActive():
            self.time_timer.start(1000)

        self._update_logs()
        self._refresh_time_label()
        super().showEvent(a0)

    def _update_logs(self):
        logs = log_buffer.get_all()
        count = len(logs)
        if count != self._last_count:
            scrollbar = self.text_edit.verticalScrollBar()
            was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 4

            new_text = '\n'.join(logs[self._last_count:])
            prefix = '\n' if self._last_count > 0 else ''

            cursor = QTextCursor(self.text_edit.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(prefix + new_text)

            self._last_count = count

            if was_at_bottom:
                scrollbar.setValue(scrollbar.maximum())

            # Re-apply search highlights if a search is active
            if self._search_input.text():
                self._on_search(self._search_input.text())

    def _copy_all(self):
        QApplication.clipboard().setText(self.text_edit.toPlainText())

    def _refresh_time_label(self):
        total = time_tracker.get_total_seconds()
        self.time_label.setText(f'Time wasted: {time_tracker.format_duration(total)}')

    def closeEvent(self, a0):
        self.timer.stop()
        self.time_timer.stop()
        super().closeEvent(a0)
