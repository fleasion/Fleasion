import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from fleasion.config import manager as manager_module
from fleasion.config.manager import ConfigManager
from fleasion.gui.replacer_config import ReplacerConfigWindow
from fleasion.localization import DEFAULT_LANGUAGE, available_languages, get_language, set_language

_app: QApplication | None = None
_TRANSLATED_LANGUAGES: list[str] = [
    code for code, _name in available_languages() if code != DEFAULT_LANGUAGE
]


def _qapp() -> QApplication:
    global _app
    app = QApplication.instance()
    _app = cast('QApplication', app) if app is not None else QApplication([])
    return _app


def _window(config_manager: ConfigManager) -> ReplacerConfigWindow:
    factory = cast('Callable[..., ReplacerConfigWindow]', ReplacerConfigWindow)
    return factory(config_manager)


@pytest.mark.parametrize('language', _TRANSLATED_LANGUAGES)
def test_translated_replacer_controls_do_not_clip_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
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
        window = _window(ConfigManager())
        window.resize(900, 750)
        window.show()
        app.processEvents()

        clipped: list[tuple[str, str, int, int]] = []
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
