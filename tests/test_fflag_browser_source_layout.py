"""Platform-appropriate FastFlag browser source labels and compact controls."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
)

from fleasion.gui import modifications_tab
from fleasion.gui.glass import style as glass_style
from fleasion.gui.glass.state import state as glass_state
from fleasion.gui.modifications_tab import FFlagBrowserDialog, FFlagSection
from fleasion.modifications.fflag_static import ClientBinary

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager


@pytest.fixture
def app() -> QApplication:
    """Provide the existing Qt application, or create one for the test."""
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _no_refresh(self: FFlagBrowserDialog, force: bool = False) -> None:
    del self, force


def _browser_controls(
    dialog: FFlagBrowserDialog,
) -> tuple[QComboBox, QComboBox, QLineEdit]:
    """Inspect GUI widgets without depending on private implementation typing."""
    state = dialog.__dict__
    return (
        cast('QComboBox', state['_source_mode']),
        cast('QComboBox', state['_family_filter']),
        cast('QLineEdit', state['_search']),
    )


def test_linux_client_source_only_and_content_widths(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = ClientBinary('sober', 'Sober', tmp_path / 'libroblox.so')
    monkeypatch.setattr(modifications_tab, 'detect_client_binary', lambda: client)
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _no_refresh)
    monkeypatch.setattr(
        FFlagBrowserDialog, '_client_source_mode', staticmethod(lambda: 'installed')
    )
    dialog = FFlagBrowserDialog()
    try:
        source, family, search = _browser_controls(dialog)
        assert source.count() == 2
        assert [
            (source.itemText(i), source.itemData(i)) for i in range(source.count())
        ] == [
            ('Public catalog', 'public'),
            ('Client - Sober', 'installed'),
        ]
        assert source.findData('windows') == -1
        assert source.sizeAdjustPolicy() == QComboBox.SizeAdjustPolicy.AdjustToContents
        assert family.sizeAdjustPolicy() == QComboBox.SizeAdjustPolicy.AdjustToContents
        assert source.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
        assert family.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
        example_flags: dict[str, str | None] = {
            'DFFlagExample': 'True',
            'DFIntExample': '3',
            'FStringOther': None,
        }
        dialog._apply_flags(example_flags)  # pyright: ignore[reportPrivateUsage]
        dialog.resize(1150, 700)
        dialog.show()
        app.processEvents()
        assert (
            source.width()
            == max(
                source.fontMetrics().horizontalAdvance(source.itemText(i))
                for i in range(source.count())
            )
            + 40
        )
        assert (
            family.width()
            == max(
                family.fontMetrics().horizontalAdvance(family.itemText(i))
                for i in range(family.count())
            )
            + 40
        )
        assert source.width() < 250
        assert family.width() < 250
        assert search.width() > 400
    finally:
        dialog.close()


def test_source_selection_restores_across_dialog_instances(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = ClientBinary('sober', 'Sober', tmp_path / 'libroblox.so')
    monkeypatch.setattr(modifications_tab, 'detect_client_binary', lambda: client)
    calls: list[str] = []

    def record_refresh(dialog: FFlagBrowserDialog, force: bool = False) -> None:
        del force
        calls.append(str(dialog._source_mode.currentData()))  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', record_refresh)
    preference = cast('ConfigManager', SimpleNamespace(fflag_browser_source='public'))
    first = FFlagBrowserDialog(config_manager=preference)
    try:
        source, _, _ = _browser_controls(first)
        assert source.currentData() == 'public'
        source.setCurrentIndex(1)
        assert preference.fflag_browser_source == 'client'
    finally:
        first.close()

    second = FFlagBrowserDialog(config_manager=preference)
    try:
        source, _, _ = _browser_controls(second)
        assert source.currentData() == 'installed'
        assert calls == ['public', 'installed', 'installed']
        source.setCurrentIndex(0)
        assert preference.fflag_browser_source == 'public'
    finally:
        second.close()
    third = FFlagBrowserDialog(config_manager=preference)
    try:
        assert _browser_controls(third)[0].currentData() == 'public'
    finally:
        third.close()


def test_saved_client_mode_initializes_windows_header_and_choose_button(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _no_refresh)
    monkeypatch.setattr(
        FFlagBrowserDialog, '_client_source_mode', staticmethod(lambda: 'windows')
    )
    preference = cast('ConfigManager', SimpleNamespace(fflag_browser_source='client'))
    dialog = FFlagBrowserDialog(config_manager=preference)
    try:
        source, _, _ = _browser_controls(dialog)
        assert source.currentData() == 'windows'
        heading = dialog._table.horizontalHeaderItem(1)  # pyright: ignore[reportPrivateUsage]
        assert heading is not None and heading.text() == 'Initial value'
        dialog.show()
        app.processEvents()
        assert dialog._choose_exe_button.isVisible()  # pyright: ignore[reportPrivateUsage]
        assert preference.fflag_browser_source == 'client'
    finally:
        dialog.close()


def test_option_width_recalculates_after_family_change_and_client_switch(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = [ClientBinary('sober', 'Sober', tmp_path / 'libroblox.so')]
    monkeypatch.setattr(modifications_tab, 'detect_client_binary', lambda: current[0])
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _no_refresh)
    dialog = FFlagBrowserDialog()
    try:
        source, family, search = _browser_controls(dialog)
        dialog._apply_flags({'DFFlagOne': None, 'FFlagTwo': None})  # pyright: ignore[reportPrivateUsage]
        dialog.resize(1150, 650)
        dialog.show()
        app.processEvents()

        def widest(combo: QComboBox) -> int:
            return max(
                combo.fontMetrics().horizontalAdvance(combo.itemText(i))
                for i in range(combo.count())
            )

        assert family.width() == widest(family) + 40
        smaller = family.width()
        many_flags: dict[str, str | None] = {
            f'DFStringExample{i}': None for i in range(12345)
        }
        dialog._apply_flags(many_flags)  # pyright: ignore[reportPrivateUsage]
        app.processEvents()
        assert family.width() == widest(family) + 40
        assert family.width() > smaller
        current[0] = ClientBinary('nightcap', 'Nightcap', tmp_path / 'nightcap.so')
        source.setCurrentIndex(1)
        app.processEvents()
        assert source.itemText(1) == 'Client - Nightcap'
        assert source.width() == widest(source) + 40
        assert search.width() > 400
    finally:
        dialog.close()


def test_glass_theme_compact_dropdowns_fit_the_full_longest_option(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(glass_style, 'ICON_DIR', tmp_path / 'icons')
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _no_refresh)
    dialog = FFlagBrowserDialog()
    try:
        public: dict[str, str | None] = FFlagBrowserDialog._read_cache() or {}  # pyright: ignore[reportPrivateUsage]
        if not public:
            public = {f'DFStringOption{i}': None for i in range(1000)}
        dialog._apply_flags(public)  # pyright: ignore[reportPrivateUsage]
        dialog.setStyleSheet(glass_style.stylesheet(glass_state.settings))
        dialog.resize(1150, 700)
        dialog.show()
        app.processEvents()
        source, family, search = _browser_controls(dialog)
        for combo in (source, family):
            option = QStyleOptionComboBox()
            option.initFrom(combo)
            option.currentText = combo.currentText()
            option.editable = combo.isEditable()
            rect = combo.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option,
                QStyle.SubControl.SC_ComboBoxEditField,
                combo,
            )
            maximum_text = max(
                combo.fontMetrics().horizontalAdvance(combo.itemText(i))
                for i in range(combo.count())
            )
            assert rect.width() >= maximum_text
            assert combo.width() == maximum_text + 40
            assert combo.width() < combo.sizeHint().width()
        assert search.width() > 400
    finally:
        dialog.close()


def test_source_label_tracks_newly_selected_client(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = [ClientBinary('sober', 'Sober', tmp_path / 'sober.so')]
    monkeypatch.setattr(modifications_tab, 'detect_client_binary', lambda: current[0])
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _no_refresh)
    monkeypatch.setattr(
        FFlagBrowserDialog, '_client_source_mode', staticmethod(lambda: 'installed')
    )
    dialog = FFlagBrowserDialog()
    try:
        source, _family, _search = _browser_controls(dialog)
        assert source.itemText(1) == 'Client - Sober'
        current[0] = ClientBinary('nightcap', 'Nightcap', tmp_path / 'nightcap.so')
        source.setCurrentIndex(1)
        app.processEvents()
        assert source.itemText(1) == 'Client - Nightcap'
    finally:
        dialog.close()


def test_windows_client_uses_existing_local_exe_scanner_not_a_third_source(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _no_refresh)
    monkeypatch.setattr(
        FFlagBrowserDialog, '_client_source_mode', staticmethod(lambda: 'windows')
    )
    dialog = FFlagBrowserDialog()
    try:
        source, _family, _search = _browser_controls(dialog)
        assert source.count() == 2
        assert source.itemText(1) == 'Client - Windows'
        assert source.itemData(1) == 'windows'
        dialog._local_exe = tmp_path / 'RobloxPlayerBeta.exe'  # pyright: ignore[reportPrivateUsage]
        source.setCurrentIndex(1)
        app.processEvents()
        heading = dialog._table.horizontalHeaderItem(1)  # pyright: ignore[reportPrivateUsage]
        assert heading is not None
        assert heading.text() == 'Initial value'
        assert dialog._choose_exe_button.isVisible() is False  # pyright: ignore[reportPrivateUsage]
        dialog.show()
        app.processEvents()
        assert dialog._choose_exe_button.isVisible()  # pyright: ignore[reportPrivateUsage]
    finally:
        dialog.close()


def test_macos_client_label_is_short_not_internal_scanner_name(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = ClientBinary('macos', 'Roblox for macOS', tmp_path / 'RobloxPlayer')
    monkeypatch.setattr(modifications_tab, 'detect_client_binary', lambda: client)
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _no_refresh)
    monkeypatch.setattr(
        FFlagBrowserDialog, '_client_source_mode', staticmethod(lambda: 'installed')
    )
    dialog = FFlagBrowserDialog()
    try:
        source, _family, _search = _browser_controls(dialog)
        assert source.itemText(1) == 'Client - macOS'
    finally:
        dialog.close()


def test_bootstrapper_warning_does_not_take_up_a_second_row(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_initial_load(_section: FFlagSection) -> None:
        return None

    monkeypatch.setattr(FFlagSection, '_load_from_manager', no_initial_load)
    section = FFlagSection(None)  # pyright: ignore[reportArgumentType]
    try:
        layout = section.layout()
        assert layout is not None
        assert layout.count() == 4
        first_item = layout.itemAt(0)
        assert first_item is not None
        warning = first_item.widget()
        assert isinstance(warning, QLabel)
        assert 'ClientAppSettings.json' in warning.text()
        assert all(
            'synchroniz' not in label.text().casefold()
            for label in section.findChildren(QLabel)
        )
    finally:
        section.close()
