"""Platform-appropriate FastFlag browser source labels and compact controls."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QSizePolicy

from fleasion.gui import modifications_tab
from fleasion.gui.modifications_tab import FFlagBrowserDialog, FFlagSection
from fleasion.modifications.fflag_static import ClientBinary


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
        assert source.width() == source.sizeHint().width()
        assert family.width() == family.sizeHint().width()
        assert source.width() < 250
        assert family.width() < 250
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
