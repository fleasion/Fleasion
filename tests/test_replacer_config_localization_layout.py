import os
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from fleasion.config import manager as manager_module
from fleasion.config.manager import ConfigManager
from fleasion.gui.replacer_config import ReplacerConfigWindow
from fleasion.localization import get_language, set_language


_APP = None


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.mark.parametrize('language', ['es', 'pt'])
def test_translated_replacer_controls_do_not_clip_text(tmp_path, monkeypatch, language):
    app = _qapp()
    config_dir = Path(tmp_path) / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    previous_language = get_language()
    set_language(language)
    window = None
    try:
        window = ReplacerConfigWindow(ConfigManager())
        window.resize(900, 750)
        window.show()
        app.processEvents()

        clipped = []
        for label in window.findChildren(QLabel):
            if label.isVisible() and label.text() and label.sizeHint().width() > label.width():
                clipped.append(('label', label.text(), label.width(), label.sizeHint().width()))

        for button in window.findChildren(QPushButton):
            if not button.isVisible() or not button.text() or button.text() == '?':
                continue
            if button.sizeHint().width() > button.width():
                clipped.append(('button', button.text(), button.width(), button.sizeHint().width()))

        for line_edit in window.findChildren(QLineEdit):
            placeholder = line_edit.placeholderText()
            if not line_edit.isVisible() or not placeholder:
                continue
            required_width = line_edit.fontMetrics().horizontalAdvance(placeholder) + 12
            if required_width > line_edit.width():
                clipped.append(('line edit', placeholder, line_edit.width(), required_width))

        assert clipped == []
    finally:
        if window is not None:
            window.enabled_menu.hide()
            window.close()
        set_language(previous_language)
        app.processEvents()
