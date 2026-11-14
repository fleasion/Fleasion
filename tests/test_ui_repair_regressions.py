"""Regression coverage for the shared Fusion and Glass UI repairs."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Literal

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QLabel,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from fleasion.appearance import accent_name
from fleasion.gui.appearance_controls import AccentSelector
from fleasion.gui.command_palette import fade_tabs
from fleasion.gui.glass import chrome
from fleasion.gui.modifications_tab import CollapsibleSection
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fleasion.config.manager import ConfigManager


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
    yield application
    ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def test_interrupted_section_reopen_does_not_squash_children(app: QApplication) -> None:
    window = QWidget()
    window.resize(460, 500)
    layout = QVBoxLayout(window)
    section = CollapsibleSection('Fast Flags', expanded=True)
    labels = [QLabel(f'Flag {i}') for i in range(12)]
    for label in labels:
        section.add_widget(label)
    layout.addWidget(section)
    layout.addStretch()
    window.show()
    QTest.qWait(30)
    expected = [label.height() for label in labels]
    section.toggle()
    QTest.qWait(70)
    section.toggle()
    QTest.qWait(75)
    assert [label.height() for label in labels] == expected
    QTest.qWait(200)
    assert section._expanded  # pyright: ignore[reportPrivateUsage]
    assert section._content.height() > 0  # pyright: ignore[reportPrivateUsage]
    window.close()


def test_custom_and_system_accent_selection(app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    selector = AccentSelector(custom_color='#aa22bb')
    assert selector.accent() == 'Theme'
    selector.set_accent('System')
    assert selector.accent() == 'System'
    selector.set_accent('#ABCDEF')
    assert selector.accent() == '#abcdef'
    assert accent_name('#ABCDEF') == '#abcdef'
    assert accent_name('#abcdef00') == 'Theme'

    emitted: list[str] = []
    selector.accent_changed.connect(emitted.append)
    def chosen_color(*_args: object, **_kwargs: object) -> QColor:
        return QColor('#bada55')

    monkeypatch.setattr(QColorDialog, 'getColor', chosen_color)
    selector._buttons['Custom'].click()  # pyright: ignore[reportPrivateUsage]
    assert emitted == ['#bada55']
    assert selector.accent() == '#bada55'
    selector.deleteLater()


def test_merged_glass_navigation_preserves_active_tab(app: QApplication) -> None:
    window = QDialog()
    layout = QVBoxLayout(window)
    tabs = QTabWidget()
    tabs.setObjectName('DashboardTabs')
    for name in ('Replacer', 'Modifications', 'Settings'):
        tabs.addTab(QLabel(f'{name} content'), name)
    tabs.setTabEnabled(1, False)  # ruff: ignore[boolean-positional-value-in-call]
    tabs.setCurrentIndex(2)
    layout.addWidget(tabs)
    window.show()
    chrome.register(window)
    try:
        chrome.apply_chrome(window, enabled=True)
        app.processEvents()
        navigation = window.findChild(QTabBar, 'GlassDashboardNavigation')
        assert navigation is not None
        assert navigation.currentIndex() == 2
        assert not navigation.isTabEnabled(1)
        tabs.setTabEnabled(1, True)  # ruff: ignore[boolean-positional-value-in-call]
        chrome.sync_dashboard_tabs(window)
        assert navigation.isTabEnabled(1)
        assert tabs.currentIndex() == 2

        chrome.apply_chrome(window, enabled=False)
        assert tabs.tabBar().isVisible()
        assert not navigation.isVisible()
        assert tabs.currentIndex() == 2
        chrome.apply_chrome(window, enabled=True)
        app.processEvents()
        restored = window.findChild(QTabBar, 'GlassDashboardNavigation')
        assert restored is not None
        assert restored.currentIndex() == 2
        assert tabs.currentIndex() == 2
        assert restored.isTabEnabled(1)
    finally:
        chrome.apply_chrome(window, enabled=False)
        window.close()
        window.deleteLater()


def test_tab_transition_fades_complete_frame_without_altering_live_widgets(
    app: QApplication,
) -> None:
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem

    from fleasion.gui.command_palette import _TabFade, _TabTransitionController  # pyright: ignore[reportPrivateUsage]

    tabs = QTabWidget()
    tabs.resize(640, 420)
    first = QWidget()
    tabs.addTab(first, 'First')
    second = QWidget()
    layout = QVBoxLayout(second)
    tree = QTreeWidget()
    tree.setHeaderLabels(['Profile', 'Replacement'])
    tree.addTopLevelItem(QTreeWidgetItem(['Test profile', 'test_file.webp']))
    table = QTableWidget(2, 2)
    table.setItem(0, 0, QTableWidgetItem('Asset 123'))
    layout.addWidget(tree)
    layout.addWidget(table)
    tabs.addTab(second, 'Second')
    fade_tabs(tabs)
    tabs.show()
    app.processEvents()
    controller = tabs.findChild(_TabTransitionController)
    assert controller is not None
    try:
        tabs.setCurrentIndex(1)
        overlay = tabs.findChild(_TabFade)
        assert overlay is not None and overlay.isVisible()
        assert overlay._frame.width() == second.width()  # pyright: ignore[reportPrivateUsage]
        assert not overlay._frame.isNull()  # pyright: ignore[reportPrivateUsage]
        assert second.graphicsEffect() is None
        assert tree.graphicsEffect() is None
        assert table.graphicsEffect() is None
        # The live page stays hidden while its complete captured image fades
        assert not tree.isVisible() and not table.isVisible()

        # During the transition, the old page is gone; the full new frame fades
        QTest.qWait(70)
        assert 0.0 < overlay._opacity < 1.0  # pyright: ignore[reportPrivateUsage]
        assert second.graphicsEffect() is None
        QTest.qWait(180)
        assert controller._active is None or not controller._active.isVisible()  # pyright: ignore[reportPrivateUsage]
        assert tree.isVisible() and table.isVisible()
    finally:
        tabs.close()
        tabs.deleteLater()


def test_tab_transition_survives_rapid_switch_and_theme_change(app: QApplication) -> None:
    from fleasion.gui.command_palette import _TabFade, _TabTransitionController  # pyright: ignore[reportPrivateUsage]

    tabs = QTabWidget()
    tabs.resize(520, 300)
    for name in ('First', 'Second', 'Third'):
        page = QWidget()
        inner = QVBoxLayout(page)
        inner.addWidget(QLabel(f'{name} page text'))
        tabs.addTab(page, name)
    fade_tabs(tabs)
    tabs.show()
    app.processEvents()
    try:
        tabs.setCurrentIndex(1)
        first_overlay = tabs.findChild(_TabFade)
        assert first_overlay is not None and first_overlay.isVisible()
        tabs.setCurrentIndex(2)
        assert not first_overlay.isVisible()
        second_overlay = tabs.findChild(_TabTransitionController)
        assert second_overlay is not None
        assert second_overlay._active is not None  # pyright: ignore[reportPrivateUsage]
        ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
        assert second_overlay._active is None  # pyright: ignore[reportPrivateUsage]
        ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
        tabs.setCurrentIndex(0)
        # Qt's animation driver can fall behind under a loaded full-suite run;
        # wait for the actual completion instead of assuming 240 ms is enough
        from time import monotonic

        deadline = monotonic() + 2.0
        while second_overlay._active is not None and monotonic() < deadline:  # pyright: ignore[reportPrivateUsage]
            QTest.qWait(20)
        assert tabs.currentIndex() == 0
        assert tabs.currentWidget().graphicsEffect() is None
        assert second_overlay._active is None  # pyright: ignore[reportPrivateUsage]
    finally:
        tabs.close()
        tabs.deleteLater()


def test_tab_fade_cancels_on_resize(app: QApplication) -> None:
    from fleasion.gui.command_palette import _TabTransitionController  # pyright: ignore[reportPrivateUsage]

    tabs = QTabWidget()
    tabs.resize(480, 300)
    tabs.addTab(QLabel('First'), 'First')
    tabs.addTab(QLabel('Second'), 'Second')
    fade_tabs(tabs)
    tabs.show()
    app.processEvents()
    try:
        tabs.setCurrentIndex(1)
        controller = tabs.findChild(_TabTransitionController)
        assert controller is not None
        assert controller._active is not None  # pyright: ignore[reportPrivateUsage]
        tabs.resize(650, 380)
        app.processEvents()
        assert controller._active is None  # pyright: ignore[reportPrivateUsage]
    finally:
        tabs.close()
        tabs.deleteLater()


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    from fleasion.config import manager

    monkeypatch.setattr(manager, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(manager, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path / 'configs')
    return manager.ConfigManager()


@pytest.mark.parametrize('mode', ['Fusion', 'Glass'])
def test_combo_popup_starts_below_selector(
    app: QApplication, mode: str
) -> None:
    from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox

    ThemeManager.apply_appearance('Glass' if mode == 'Glass' else 'Fusion', 'Dark', 'Ice')
    window = QDialog()
    window.resize(430, 330)
    layout = QVBoxLayout(window)
    combo = QComboBox()
    combo.addItems(['First', 'Second', 'Third'])
    layout.addWidget(combo)
    layout.addStretch()
    if mode == 'Glass':
        from fleasion.gui import glass

        glass.theme.attach(window)
    window.show()
    app.processEvents()
    option = QStyleOptionComboBox()
    combo.initStyleOption(option)
    assert combo.style().styleHint(QStyle.StyleHint.SH_ComboBox_Popup, option, combo) == 0
    try:
        combo.showPopup()
        # The popup already has its final position immediately after showPopup
        # returns, before the event loop paints its first frame
        popup = combo.view().window()
        button_bottom = combo.mapToGlobal(combo.rect().bottomLeft()).y()
        assert popup.geometry().top() >= button_bottom
        app.processEvents()
        assert popup.geometry().top() >= button_bottom
    finally:
        combo.hidePopup()
        window.close()
        window.deleteLater()


@pytest.mark.parametrize('mode', ['Fusion', 'Glass'])
def test_dashboard_restores_original_zero_outer_margins(
    app: QApplication, isolated_config: ConfigManager, mode: Literal['Fusion', 'Glass']
) -> None:
    from fleasion.gui.replacer_config import ReplacerConfigWindow

    isolated_config.appearance_style = mode
    ThemeManager.apply_from_config(isolated_config)
    window = ReplacerConfigWindow(isolated_config)
    window.show()
    try:
        layout = window.layout()
        assert layout is not None
        margins = layout.contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
            0, 0, 0, 0
        )
    finally:
        window.close()
        window.deleteLater()


def test_accent_is_offered_for_both_styles(
    app: QApplication, isolated_config: ConfigManager,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMenu

    from fleasion.app.tray import SystemTray
    from fleasion.gui.replacer_config import ReplacerConfigWindow
    from fleasion.gui.settings_tab import SettingsTab
    from fleasion.localization import tr

    isolated_config.appearance_style = 'Fusion'
    ThemeManager.apply_from_config(isolated_config)
    tab = SettingsTab(isolated_config)
    tab.show()
    tray = SystemTray.__new__(SystemTray)
    tray.config_manager = isolated_config
    tray.menu = QMenu()
    monkeypatch.setattr(tray, '_is_cache_scraper_enabled', lambda: False)
    tray._create_settings_menu()  # pyright: ignore[reportPrivateUsage]
    window = ReplacerConfigWindow(isolated_config)
    accent_menu = tray._accent_menu  # pyright: ignore[reportPrivateUsage]
    assert accent_menu is not None
    try:
        app.processEvents()
        assert tab._accent_selector.isVisible()  # pyright: ignore[reportPrivateUsage]
        assert accent_menu.menuAction().isVisible()
        assert any(
            command.kind == tr('settings.appearance.accent')
            for command in window._palette_commands()  # pyright: ignore[reportPrivateUsage]
        )

        ThemeManager.set_preference(isolated_config, 'style', 'Glass')
        tray._sync_appearance_actions()  # pyright: ignore[reportPrivateUsage]
        app.processEvents()
        assert tab._accent_selector.isVisible()  # pyright: ignore[reportPrivateUsage]
        assert accent_menu.menuAction().isVisible()
        assert any(
            command.kind == tr('settings.appearance.accent')
            for command in window._palette_commands()  # pyright: ignore[reportPrivateUsage]
        )

        ThemeManager.set_preference(isolated_config, 'style', 'Fusion')
        tray._sync_appearance_actions()  # pyright: ignore[reportPrivateUsage]
        app.processEvents()
        assert tab._accent_selector.isVisible()  # pyright: ignore[reportPrivateUsage]
        assert accent_menu.menuAction().isVisible()
    finally:
        window.close()
        tab.close()
        tray.menu.deleteLater()
        window.deleteLater()
        tab.deleteLater()


def test_replacer_border_margins_match_historical_layout_and_glass_inset(
    app: QApplication, isolated_config: ConfigManager
) -> None:
    from fleasion.gui.replacer_config import ReplacerConfigWindow
    from fleasion.widgets.spacing import page_content_margins

    ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
    window = ReplacerConfigWindow(isolated_config)
    window.show()
    try:
        layout = window._replacer_content_layout  # pyright: ignore[reportPrivateUsage]
        assert layout.getContentsMargins() == page_content_margins(glass=False)
        ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
        assert layout.getContentsMargins() == page_content_margins(glass=True)
        ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
        assert layout.getContentsMargins() == page_content_margins(glass=False)
    finally:
        window.close()
        window.deleteLater()


def test_scraper_border_margins_match_replacer_and_follow_style(app: QApplication) -> None:
    from fleasion.cache import CacheManager
    from fleasion.cache.cache_viewer import CacheViewerTab
    from fleasion.widgets.spacing import page_content_margins

    ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
    scraper = CacheViewerTab(CacheManager(), defer_setup=True)
    builder = scraper._build_ui()  # pyright: ignore[reportPrivateUsage]
    try:
        next(builder)
        layout = scraper._page_content_layout  # pyright: ignore[reportPrivateUsage]
        assert layout is not None
        assert layout.getContentsMargins() == page_content_margins(glass=False)
        ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
        assert layout.getContentsMargins() == page_content_margins(glass=True)
        ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
        assert layout.getContentsMargins() == page_content_margins(glass=False)
    finally:
        builder.close()
        scraper.shutdown()
        scraper.close()
        scraper.deleteLater()


def test_glass_tab_fade_captures_window_background(
    app: QApplication, isolated_config: ConfigManager
) -> None:
    from fleasion.gui.command_palette import _TabFade, _TabTransitionController  # pyright: ignore[reportPrivateUsage]
    from fleasion.gui.replacer_config import ReplacerConfigWindow

    ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
    window = ReplacerConfigWindow(isolated_config)
    window.show()
    app.processEvents()
    tabs = window.tab_widget
    index = window._tab_indices['settings']  # pyright: ignore[reportPrivateUsage]
    try:
        tabs.setTabEnabled(index, True)  # ruff: ignore[boolean-positional-value-in-call]
        tabs.setCurrentIndex(index)
        transition = tabs.findChild(_TabTransitionController)
        assert transition is not None
        frame = transition._active  # pyright: ignore[reportPrivateUsage]
        assert isinstance(frame, _TabFade)
        assert not frame._frame.isNull()  # pyright: ignore[reportPrivateUsage]
        # The live page is hidden so its contents cannot bleed through Glass
        assert tabs.currentWidget() is not None and tabs.currentWidget().isHidden()
        assert frame._opacity == 0.0  # pyright: ignore[reportPrivateUsage]
        window_image = window.grab().toImage()
        color = window_image.pixelColor(window_image.width() // 2, window_image.height() // 2)
        assert max(color.red(), color.green(), color.blue()) > 0
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize('mode', ['Fusion', 'Glass'])
def test_replacer_headings_and_tree_indent_align_with_section_borders(
    app: QApplication, isolated_config: ConfigManager, mode: Literal['Fusion', 'Glass']
) -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QGroupBox, QStyle, QStyleOptionGroupBox

    from fleasion.gui.replacer_config import ReplacerConfigWindow
    from fleasion.localization import tr

    isolated_config.appearance_style = mode
    ThemeManager.apply_from_config(isolated_config)
    window = ReplacerConfigWindow(isolated_config)
    window.show()
    app.processEvents()
    try:
        tree = window.tree
        left = tree.mapTo(window, QPoint()).x()
        assert tree.indentation() == 12
        profiles = next(
            label for label in window.findChildren(QLabel)
            if label.text() == tr('ui.gui.replacer_config.replacement_profiles')
        )
        assert profiles.mapTo(window, QPoint()).x() == left
        for name in ('ReplacerConfigurationSection', 'ReplacerEditSection'):
            group = window.findChild(QGroupBox, name)
            assert group is not None
            assert group.mapTo(window, QPoint()).x() == left
            option = QStyleOptionGroupBox()
            group.initStyleOption(option)
            title = group.style().subControlRect(
                QStyle.ComplexControl.CC_GroupBox,
                option,
                QStyle.SubControl.SC_GroupBoxLabel,
                group,
            )
            assert title.left() == 0
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize('mode', ['Fusion', 'Glass'])
def test_collapsible_arrow_and_title_do_not_move_when_toggled(
    app: QApplication, mode: Literal['Fusion', 'Glass']
) -> None:
    ThemeManager.apply_appearance(mode, 'Dark', 'Ice')
    window = QWidget()
    window.resize(450, 320)
    layout = QVBoxLayout(window)
    section = CollapsibleSection('Fast Flags', expanded=True)
    section.add_widget(QLabel('A modification setting'))
    layout.addWidget(section)
    layout.addStretch()
    window.show()
    app.processEvents()
    arrow = section._arrow  # pyright: ignore[reportPrivateUsage]
    title = section._title_label  # pyright: ignore[reportPrivateUsage]
    initial_arrow = arrow.geometry()
    initial_title = title.geometry()
    try:
        assert arrow.size().width() == 14
        assert arrow.size().height() == 18
        for expected in (False, True, False, True):
            arrow.click()
            app.processEvents()
            assert section._expanded is expected  # pyright: ignore[reportPrivateUsage]
            assert arrow.isChecked() is expected
            assert arrow.geometry() == initial_arrow
            assert title.geometry() == initial_title
            QTest.qWait(60)
            assert arrow.geometry() == initial_arrow
            assert title.geometry() == initial_title
    finally:
        window.close()
        window.deleteLater()
