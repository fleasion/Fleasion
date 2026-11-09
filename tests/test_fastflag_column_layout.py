"""The FastFlag table uses fixed 3:2 columns and keeps its action bar visible."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHeaderView,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from fleasion.gui.modifications_tab import CollapsibleSection, CustomFFlagEditor

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.master import ProxyMaster


def _app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def _editor(flags: dict[str, str]) -> CustomFFlagEditor:
    config = SimpleNamespace(
        custom_fflags=flags,
        custom_fflags_enabled=True,
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
        custom_fflag_folders={},
        custom_fflag_disabled_folders=[],
        custom_fflag_folder_keybinds={},
        custom_fflag_actions={},
    )
    proxy = cast('ProxyMaster', SimpleNamespace(refresh_custom_fflag_interception=lambda: None))
    return CustomFFlagEditor(cast('ConfigManager', config), proxy)


@pytest.mark.parametrize('virtual', [False, True])
def test_fixed_fastflag_columns_preserve_name_value_ratio_when_resized(
    virtual: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app()
    if virtual:
        monkeypatch.setattr(CustomFFlagEditor, '_VIRTUAL_THRESHOLD', 1)
    editor = _editor({'DFFlagDebugDrawBroadPhaseAABBs': 'True', 'FFlagExample': 'False'})
    table = editor._virtual_table if virtual else editor._table  # pyright: ignore[reportPrivateUsage]
    try:
        editor.resize(1250, 500)
        editor.show()
        app.processEvents()
        assert editor._virtual_active == virtual  # pyright: ignore[reportPrivateUsage]
        assert table.isVisible()
        header = table.horizontalHeader()
        assert not header.sectionsMovable()
        assert not header.stretchLastSection()
        for col in range(table.model().columnCount()):
            assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Fixed

        for width in (1250, 800, 1450):
            editor.resize(width, 500)
            app.processEvents()
            QTest.qWait(1)
            flexible_width = table.columnWidth(0) + table.columnWidth(1)
            assert flexible_width >= 100
            assert abs(table.columnWidth(0) * 2 - table.columnWidth(1) * 3) <= 5
            assert sum(table.columnWidth(i) for i in range(table.model().columnCount())) == (
                table.viewport().width()
            )
            assert table.columnWidth(2) >= 135
            assert table.columnWidth(3) >= 175
            assert table.horizontalScrollBar().maximum() == 0
    finally:
        editor.close()
        editor.deleteLater()


def test_first_expansion_fits_fastflag_action_bar_without_editing() -> None:
    app = _app()
    outer = QWidget()
    outer.resize(1250, 360)
    root_layout = QVBoxLayout(outer)
    scroll = QScrollArea(outer)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    contents = QWidget()
    contents_layout = QVBoxLayout(contents)
    section = CollapsibleSection('FastFlags', expanded=False)
    editor = _editor({'DFFlagDebugDrawBroadPhaseAABBs': 'True'})
    section.add_widget(editor)
    contents_layout.addWidget(section)
    contents_layout.addStretch()
    scroll.setWidget(contents)
    root_layout.addWidget(scroll)
    try:
        outer.show()
        app.processEvents()
        section.toggle()
        QTest.qWait(270)
        app.processEvents()
        action = next(button for button in editor.findChildren(QPushButton) if button.text() == 'Add New…')
        assert action.height() > 0
        section_content = section._content  # pyright: ignore[reportPrivateUsage]
        bottom = action.mapTo(section_content, QPoint(0, action.height())).y()
        assert bottom <= section_content.height()
        assert section_content.height() >= section._content_layout.sizeHint().height()  # pyright: ignore[reportPrivateUsage]

        section.toggle()
        QTest.qWait(270)
        app.processEvents()
        assert section_content.height() == 0
        section.toggle()
        QTest.qWait(270)
        app.processEvents()
        assert action.mapTo(section_content, QPoint(0, action.height())).y() <= section_content.height()
    finally:
        outer.close()
        outer.deleteLater()
