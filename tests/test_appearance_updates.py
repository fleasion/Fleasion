"""Appearance updates avoid repeated application and widget-tree setup."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, override

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fleasion.appearance.colors import contrast
from fleasion.config.manager import ConfigManager
from fleasion.gui import glass
from fleasion.gui.settings_tab import SettingsTab
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fleasion.appearance import AppearanceStyle, ColorMode
    from fleasion.utils.json_types import JsonObject


@pytest.fixture
def app() -> Iterator[QApplication]:
    instance = QApplication.instance()
    application = instance if isinstance(instance, QApplication) else QApplication([])
    ThemeManager.apply_theme('Light')
    original = application.styleSheet()
    yield application
    ThemeManager.apply_theme('Light')
    application.setStyleSheet(original)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def _unexpected_setup(*_args: object) -> None:
    pytest.fail('A color update repeated application style setup or scanned every widget')


@pytest.mark.parametrize('style', ['Fusion', 'Glass'])
@pytest.mark.parametrize('app_stylesheet', ['', 'QDialogButtonBox { button-layout: 0; }'])
def test_recoloring_keeps_widget_style_and_application_rules(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, style: AppearanceStyle, app_stylesheet: str
) -> None:
    app.setStyleSheet(app_stylesheet)
    window = QDialog()
    button = QPushButton('Control')
    button.setStyleSheet('padding: 2px;')
    QVBoxLayout(window).addWidget(button)
    try:
        ThemeManager.apply_appearance(style, 'Dark', 'Ice')
        window.ensurePolished()
        app.processEvents()
        with monkeypatch.context() as scoped:
            scoped.setattr(app, 'setStyle', _unexpected_setup)
            scoped.setattr(app, 'setStyleSheet', _unexpected_setup)
            scoped.setattr(app, 'allWidgets', _unexpected_setup)
            changes: tuple[tuple[ColorMode, str], ...] = (('Light', 'Rose'), ('Dark', 'Teal'))
            for mode, accent in changes:
                ThemeManager.apply_appearance(style, mode, accent)
                app.processEvents()
                assert button.palette().color(QPalette.ColorRole.Accent) == app.palette().color(
                    QPalette.ColorRole.Accent
                )
                assert (
                    contrast(button.palette().buttonText().color(), app.palette().window().color())
                    >= 4.5
                )
    finally:
        window.deleteLater()


def test_reselecting_current_preference_does_no_work(monkeypatch: pytest.MonkeyPatch) -> None:
    config = ConfigManager.__new__(ConfigManager)
    settings: JsonObject = {
        'appearance_style': 'Glass',
        'color_mode': 'Dark',
        'accent_color': 'Rose',
    }
    config.settings = settings
    monkeypatch.setattr(config, '_save_settings', _unexpected_setup)
    monkeypatch.setattr(ThemeManager, 'apply_from_config', _unexpected_setup)
    ThemeManager.set_preference(config, 'style', 'Glass')
    ThemeManager.set_preference(config, 'color_mode', 'Dark')
    ThemeManager.set_preference(config, 'accent', 'Rose')


def test_glass_bevel_colors_follow_selected_mode_instead_of_previous_palette(
    app: QApplication,
) -> None:
    changes: tuple[tuple[ColorMode, str], ...] = (
        ('Dark', '#4b4b4b'),
        ('Light', '#ffffff'),
        ('Dark', '#4b4b4b'),
    )
    for mode, expected in changes:
        ThemeManager.apply_appearance('Glass', mode, 'Rose')
        assert app.palette().color(QPalette.ColorRole.Light) == QColor(expected)


def test_native_palette_updates_reach_settings_background_and_controls(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr('fleasion.gui.theme.sys.platform', 'linux')
    config = ConfigManager.__new__(ConfigManager)
    config.settings = {}
    monkeypatch.setattr(config, '_save_settings', lambda: None)
    window = QDialog()
    settings = SettingsTab(config)
    QVBoxLayout(window).addWidget(settings)
    window.show()
    try:
        for color, accent in (('#123456', 'Rose'), ('#876543', 'Teal')):
            native = QPalette(app.palette())
            native.setColor(QPalette.ColorRole.Window, QColor(color))
            monkeypatch.setattr(ThemeManager, '_system_palette', native)
            ThemeManager.apply_appearance('Fusion', 'System', accent)
            app.processEvents()
            container = settings._settings_container  # pyright: ignore[reportPrivateUsage]
            assert container.grab().toImage().pixelColor(0, 0) == QColor(color)
            checkbox = settings.findChildren(QCheckBox)[0]
            assert checkbox.palette().accent().color() == app.palette().accent().color()
    finally:
        window.deleteLater()


def test_late_windows_inherit_recolor_and_restore_local_rules(app: QApplication) -> None:
    window = QWidget()
    original = 'QLabel#CustomLabel { font-weight: 700; }'
    window.setStyleSheet(original)
    child = QDialog(window)
    label = QLabel('Inherited text', child)
    late: QDialog | None = None
    try:
        ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
        late = QDialog()
        late_label = QLabel('New window', late)
        late.ensurePolished()
        assert '#GlassTitleBar' in late.styleSheet()
        assert child.styleSheet() == ''
        assert window.styleSheet().endswith(original)
        for mode in ('Light', 'Dark'):
            ThemeManager.apply_appearance('Glass', mode, 'Rose')
            app.processEvents()
            for widget in (label, late_label):
                assert widget.palette().windowText().color() == app.palette().windowText().color()
        ThemeManager.apply_appearance('Fusion', 'Light', 'Rose')
        assert window.styleSheet() == original
        assert late.styleSheet() == ''
    finally:
        window.deleteLater()
        if late is not None:
            late.deleteLater()


class _StyleChanges(QObject):
    def __init__(self, widgets: set[QWidget]) -> None:
        super().__init__()
        self.widgets = widgets
        self.count = 0

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.StyleChange and watched in self.widgets:
            self.count += 1
        return False


def test_glass_recolor_does_not_revisit_every_ancestor_subtree(app: QApplication) -> None:
    window = QDialog()
    widgets: set[QWidget] = {window}
    parent: QWidget = window
    for _ in range(20):
        layout = QVBoxLayout(parent)
        for _ in range(3):
            button = QPushButton('Control')
            layout.addWidget(button)
            widgets.add(button)
        child = QWidget()
        layout.addWidget(child)
        widgets.add(child)
        parent = child
    try:
        ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
        for widget in widgets:
            widget.ensurePolished()
        app.processEvents()
        counter = _StyleChanges(widgets)
        app.installEventFilter(counter)
        try:
            ThemeManager.apply_appearance('Glass', 'Light', 'Rose')
        finally:
            app.removeEventFilter(counter)
        assert 0 < counter.count <= len(widgets) * 4
    finally:
        window.deleteLater()


def test_system_restores_native_style_hidden_by_startup_stylesheet(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr('fleasion.gui.theme.sys.platform', 'linux')
    app.setStyle('Windows')
    app.setStyleSheet('QDialogButtonBox, QMessageBox { button-layout: 0; }')
    monkeypatch.setattr(ThemeManager, '_system_style_name', None)
    ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
    assert glass.theme.active
    ThemeManager.apply_appearance('Fusion', 'System', 'Ice')
    assert ThemeManager._widget_style_name(app).casefold() == 'windows'  # pyright: ignore[reportPrivateUsage]
