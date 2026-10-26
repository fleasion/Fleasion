"""Visual/lifecycle checks for Glass native surfaces and compact dashboard controls."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QRegion
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from fleasion.config import manager
from fleasion.gui import glass, replacer_config
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    theme_before = ThemeManager._current_theme  # pyright: ignore[reportPrivateUsage]
    effective_before = ThemeManager._effective_theme  # pyright: ignore[reportPrivateUsage]
    stylesheet_before = application.styleSheet()
    ThemeManager.apply_theme('Light')
    try:
        yield application
    finally:
        ThemeManager.apply_theme('Light')
        ThemeManager.apply_theme(theme_before)
        ThemeManager._effective_theme = effective_before  # pyright: ignore[reportPrivateUsage]
        application.setStyleSheet(stylesheet_before)
        application.processEvents()


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> manager.ConfigManager:
    monkeypatch.setattr(manager, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(manager, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path / 'configs')
    return manager.ConfigManager()


def _no_user_resolution(_tab: replacer_config.SubplaceJoinerTab) -> None:
    return


def test_dashboard_created_while_glass_active_restores_opaque_window(
    app: QApplication, config: manager.ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(replacer_config.SubplaceJoinerTab, '_resolve_current_user', _no_user_resolution)
    ThemeManager.apply_theme('Glass')
    window = replacer_config.ReplacerConfigWindow(config)
    window.resize(900, 730)
    try:
        window.show()
        app.processEvents()
        assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        ThemeManager.apply_theme('System')
        app.processEvents()
        assert not window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert not window.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        assert window.mask().isEmpty()
        image = window.grab().toImage()
        assert image.pixelColor(window.width() // 2, window.height() // 2).alpha() == 255
        assert image.pixelColor(0, 0).alpha() == 255
        for theme in ('Glass', 'Dark', 'Glass', 'Light', 'Glass', 'System'):
            ThemeManager.apply_theme(theme)
            app.processEvents()
            assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) == (
                theme == 'Glass'
            )
            if theme != 'Glass':
                assert window.mask().isEmpty()
                assert window.grab().toImage().pixelColor(0, 0).alpha() == 255
    finally:
        ThemeManager.apply_theme('Light')
        window.close()
        window.deleteLater()
        app.processEvents()


def test_existing_registered_dialog_preserves_custom_mask_after_glass(
    app: QApplication,
) -> None:
    window = QDialog()
    QVBoxLayout(window)
    window.resize(200, 150)
    initial_mask = QRegion(0, 0, 190, 140)
    window.setMask(initial_mask)
    glass.theme.attach(window)
    window.show()
    try:
        ThemeManager.apply_theme('Glass')
        app.processEvents()
        ThemeManager.apply_theme('System')
        app.processEvents()
        assert window.mask() == initial_mask
        assert not window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('popup_type', ['menu', 'combo'])
def test_x11_rounded_popups_use_alpha_and_restore_native_shape(
    app: QApplication, popup_type: str
) -> None:
    if QApplication.platformName() != 'xcb':
        pytest.skip('Actual X11 window shape requires the xcb platform')
    ThemeManager.apply_theme('Glass')
    menu: QMenu | None = None
    combo: QComboBox | None = None
    if popup_type == 'menu':
        menu = QMenu()
        menu.addAction('First')
        menu.addAction('Second')
        menu.popup(QPoint(150, 150))
        window = menu
    else:
        combo = QComboBox()
        combo.addItems(['First', 'Second'])
        combo.move(150, 150)
        combo.show()
        combo.showPopup()
        window = combo.view().window()
    try:
        QTest.qWait(80)
        assert window.isVisible()
        assert window.windowHandle().format().alphaBufferSize() > 0
        assert window.mask().isEmpty()
        ThemeManager.apply_theme('System')
        app.processEvents()
        assert window.mask().isEmpty()
    finally:
        if combo is not None:
            combo.hidePopup()
            combo.close()
            combo.deleteLater()
        if menu is not None:
            menu.close()
            menu.deleteLater()
        app.processEvents()


def test_x11_existing_combo_mask_survives_glass_roundtrip(app: QApplication) -> None:
    if QApplication.platformName() != 'xcb':
        pytest.skip('Actual X11 window shape requires the xcb platform')
    ThemeManager.apply_theme('System')
    combo = QComboBox()
    combo.addItems(['First', 'Second'])
    combo.move(175, 190)
    combo.show()
    combo.showPopup()
    popup = combo.view().window()
    QTest.qWait(40)
    existing_mask = QRegion(0, 0, max(1, popup.width() - 3), max(1, popup.height() - 3))
    popup.setMask(existing_mask)
    try:
        ThemeManager.apply_theme('Glass')
        QTest.qWait(40)
        ThemeManager.apply_theme('System')
        app.processEvents()
        assert popup.mask() == existing_mask
    finally:
        combo.hidePopup()
        combo.close()
        combo.deleteLater()
        app.processEvents()


def test_footer_keybind_help_is_flat_in_all_themes(
    app: QApplication, config: manager.ConfigManager
) -> None:
    window = replacer_config.ReplacerConfigWindow(config)
    try:
        button = next(
            button
            for button in window.findChildren(QPushButton)
            if button.toolTip() == replacer_config.tr('ui.gui.replacer_config.view_keybinds')
        )
        assert button.isFlat()
        assert button.width() <= 24
        ThemeManager.apply_theme('Glass')
        assert button.isFlat()
        assert button.property('glassArrow') is True
        ThemeManager.apply_theme('System')
        assert button.isFlat()
        assert button.property('glassArrow') is None
    finally:
        ThemeManager.apply_theme('Light')
        window.close()
        window.deleteLater()
        app.processEvents()


def test_misc_and_proxy_help_buttons_are_flat_and_small(
    app: QApplication, config: manager.ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(replacer_config.SubplaceJoinerTab, '_resolve_current_user', _no_user_resolution)
    window = replacer_config.ReplacerConfigWindow(config)
    window.resize(950, 750)
    window.show()
    try:
        for _ in range(150):
            app.processEvents()
            if all(window.tab_widget.isTabEnabled(i) for i in range(window.tab_widget.count())):
                break
            QTest.qWait(10)
        tooltips = {
            replacer_config.tr('ui.gui.replacer_config.view_keybinds'),
            replacer_config.tr('ui.gui.rando_stuff_tab.what_is_a_reserved_server'),
            replacer_config.tr('ui.gui.proxy_tab.about_this_tab'),
        }
        buttons = [
            btn for btn in window.findChildren(QPushButton) if btn.toolTip() in tooltips
        ]
        assert len(buttons) == 3
        for theme in ('Dark', 'Glass', 'System'):
            ThemeManager.apply_theme(theme)
            app.processEvents()
            for button in buttons:
                assert button.isFlat()
                assert button.maximumWidth() == 24
                if theme == 'Glass':
                    assert button.property('glassArrow') is True
    finally:
        ThemeManager.apply_theme('Light')
        window.close()
        window.deleteLater()
        app.processEvents()
