from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from fleasion.gui import logs
from fleasion.utils import logging as fleasion_logging

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_app: QApplication | None = None


def test_log_viewer_appends_new_entries_without_replacing_existing_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global _app
    _app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    monkeypatch.setattr(fleasion_logging, 'LOGS_DIR', tmp_path)
    monkeypatch.setattr(fleasion_logging, 'LOG_FILE', tmp_path / 'fleasion.log')
    buffer = fleasion_logging.LogBuffer()
    monkeypatch.setattr(logs, 'log_buffer', buffer)
    buffer.log('Test', 'first')
    window = logs.LogsWindow()
    try:
        before = window.text_edit.toPlainText()
        assert 'first' in before
        window._search_input.setText('Test')  # pyright: ignore[reportPrivateUsage]
        assert len(window.text_edit.extraSelections()) == 1
        window._update_logs()  # pyright: ignore[reportPrivateUsage]
        assert window.text_edit.toPlainText() == before
        buffer.log('Test', 'second')
        window._update_logs()  # pyright: ignore[reportPrivateUsage]
        assert window.text_edit.toPlainText() == before + '\n' + buffer.get_all()[-1]
        assert len(window.text_edit.extraSelections()) == 2
    finally:
        window.close()
        window.deleteLater()
        _app.processEvents()
