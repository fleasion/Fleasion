"""Regression checks for very large, scrollable custom FastFlag collections."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from fleasion.gui.modifications_tab import CustomFFlagEditor

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.master import ProxyMaster


def _app() -> QApplication:
    instance = QApplication.instance()
    return cast('QApplication', instance) if instance is not None else QApplication([])


def _editor(flags: dict[str, str], *, folders: dict[str, list[str]] | None = None) -> tuple[CustomFFlagEditor, SimpleNamespace]:
    config = SimpleNamespace(
        custom_fflags=flags,
        custom_fflags_enabled=False,
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
        custom_fflag_folders=folders or {},
        custom_fflag_disabled_folders=[],
        custom_fflag_folder_keybinds={},
        custom_fflag_actions={},
    )
    proxy = cast('ProxyMaster', SimpleNamespace(refresh_custom_fflag_interception=lambda: None))
    return CustomFFlagEditor(cast('ConfigManager', config), proxy), config


def test_virtual_table_keeps_its_viewport_bounded_and_data_editable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    flags = {f'DFIntStressFlag{i:06d}': str(i) for i in range(10_050)}
    editor, config = _editor(flags)
    assert editor._virtual_active  # pyright: ignore[reportPrivateUsage]
    assert editor._table.rowCount() == 0  # pyright: ignore[reportPrivateUsage]
    model = editor._virtual_model  # pyright: ignore[reportPrivateUsage]
    table = editor._virtual_table  # pyright: ignore[reportPrivateUsage]
    assert model.rowCount() == 10_050
    assert table.height() <= 420
    editor.show()
    app.processEvents()
    scroll = table.verticalScrollBar()
    scroll.setValue(scroll.maximum())
    app.processEvents()
    assert scroll.value() == scroll.maximum()
    assert model.index(10_049, 0).data(Qt.ItemDataRole.DisplayRole) == 'DFIntStressFlag010049'

    editor._search.setText('StressFlag000123')  # pyright: ignore[reportPrivateUsage]
    app.processEvents()
    assert model.rowCount() == 1
    assert model.index(0, 1).data(Qt.ItemDataRole.UserRole) == '123'
    assert model.setData(model.index(0, 1), '456', Qt.ItemDataRole.EditRole)
    app.processEvents()
    assert config.custom_fflags['DFIntStressFlag000123'] == '456'

    assert model.setData(model.index(0, 2), Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
    assert config.custom_fflag_disabled == ['DFIntStressFlag000123']

    assert model.setData(model.index(0, 0), 'DFIntRenamed', Qt.ItemDataRole.EditRole)
    assert config.custom_fflags['DFIntRenamed'] == '456'
    assert 'DFIntStressFlag000123' not in config.custom_fflags

    editor._search.clear()  # pyright: ignore[reportPrivateUsage]
    app.processEvents()
    assert model.rowCount() == 10_050
    editor._sort_rows(1)  # pyright: ignore[reportPrivateUsage]
    assert model.rowCount() == 10_050

    def approve_clear(*_args: object, **_kwargs: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, 'question', approve_clear)
    editor._clear_all_flags()  # pyright: ignore[reportPrivateUsage]
    assert not config.custom_fflags
    assert not editor._virtual_active  # pyright: ignore[reportPrivateUsage]
    assert editor._table.rowCount() == 0  # pyright: ignore[reportPrivateUsage]
    editor.close()


def test_virtual_table_preserves_folder_and_selected_row_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    monkeypatch.setattr(CustomFFlagEditor, '_VIRTUAL_THRESHOLD', 2)
    editor, config = _editor(
        {'DFIntFirst': '1', 'DFIntSecond': '2', 'DFIntThird': '3'},
        folders={'Folder': ['DFIntFirst']},
    )
    model = editor._virtual_model  # pyright: ignore[reportPrivateUsage]
    assert model.rowCount() == 4
    assert model.index(0, 0).data(Qt.ItemDataRole.DisplayRole) == 'Folder'
    assert model.index(1, 0).data(Qt.ItemDataRole.DisplayRole) == '    DFIntFirst'
    assert model.index(0, 1).data(Qt.ItemDataRole.DisplayRole) == '1 FastFlag'
    assert model.setData(model.index(0, 2), Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
    assert config.custom_fflag_disabled_folders == ['Folder']
    assert model.setData(model.index(1, 0), 'DFIntRenamed', Qt.ItemDataRole.EditRole)
    assert model.index(1, 0).data(Qt.ItemDataRole.DisplayRole) == '    DFIntRenamed'
    assert config.custom_fflag_folders == {'Folder': ['DFIntRenamed']}

    editor._virtual_table.selectRow(2)  # pyright: ignore[reportPrivateUsage]
    assert editor._selected_flag_names() == ['DFIntSecond']  # pyright: ignore[reportPrivateUsage]
    editor._delete_selected()  # pyright: ignore[reportPrivateUsage]
    assert config.custom_fflags == {'DFIntRenamed': '1', 'DFIntThird': '3'}
    assert config.custom_fflag_folders == {'Folder': ['DFIntRenamed']}
    app.processEvents()
    editor.close()


def test_ctrl_a_delete_clears_full_virtual_selection_without_materializing_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    flags = {f'DFIntBulk{i:06d}': '1' for i in range(12_000)}
    editor, config = _editor(flags)
    table = editor._virtual_table  # pyright: ignore[reportPrivateUsage]
    editor.show()
    table.setFocus()
    app.processEvents()
    QTest.keyClick(table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    ranges = table.selectionModel().selection()
    assert ranges.count() == 1
    assert ranges.at(0).top() == 0
    assert ranges.at(0).bottom() == 11_999

    confirmed: list[bool] = []

    def approve(*_args: object, **_kwargs: object) -> QMessageBox.StandardButton:
        confirmed.append(True)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, 'question', approve)
    QTest.keyClick(table, Qt.Key.Key_Delete)
    app.processEvents()
    assert confirmed == [True]
    assert config.custom_fflags == {}
    assert not editor._virtual_active  # pyright: ignore[reportPrivateUsage]
    editor.close()


@pytest.mark.skipif(
    os.environ.get('FLEASION_STRESS_FFLAGS') != '1',
    reason='Opt-in 956,000-row stress test (FLEASION_STRESS_FFLAGS=1)',
)
def test_956000_flags_render_and_scroll_without_allocating_cells() -> None:
    app = _app()
    editor, _config = _editor(
        {f'DFIntStressFlag{i:07d}': str(i) for i in range(956_000)}
    )
    model = editor._virtual_model  # pyright: ignore[reportPrivateUsage]
    assert model.rowCount() == 956_000
    assert editor._table.rowCount() == 0  # pyright: ignore[reportPrivateUsage]
    editor.show()
    app.processEvents()
    scroll = editor._virtual_table.verticalScrollBar()  # pyright: ignore[reportPrivateUsage]
    scroll.setValue(scroll.maximum())
    app.processEvents()
    assert scroll.value() == scroll.maximum()
    assert model.index(955_999, 0).data(Qt.ItemDataRole.DisplayRole) == 'DFIntStressFlag0955999'
    editor.close()
