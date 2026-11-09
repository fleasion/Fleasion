"""Compact table editors must hide the cell text beneath them in every theme."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QWidget

from fleasion.gui.modifications_tab import CompactBooleanComboBox
from fleasion.gui.proxy_tab import _CompactComboBox  # pyright: ignore[reportPrivateUsage]
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from fleasion.appearance import AppearanceStyle, ColorMode


@pytest.mark.parametrize('combo_type', [CompactBooleanComboBox, _CompactComboBox])
@pytest.mark.parametrize(
    ('style', 'mode'),
    [('Fusion', 'Dark'), ('Fusion', 'Light'), ('Glass', 'Dark'), ('Glass', 'Light')],
)
def test_compact_combo_covers_underlying_table_cell_when_popup_takes_focus(
    combo_type: type[QComboBox], style: AppearanceStyle, mode: ColorMode
) -> None:
    application = QApplication.instance()
    app = application if isinstance(application, QApplication) else QApplication([])
    ThemeManager.apply_appearance(style, mode, 'Ice')

    root = QWidget()
    root.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    root.resize(400, 80)
    underneath = QLabel(root)
    underneath.setGeometry(0, 0, 400, 80)
    underneath.setStyleSheet('background: rgb(255, 0, 255);')
    editor = combo_type(root)
    editor.setGeometry(30, 25, 320, 30)
    editor.addItems(['True', 'False'])
    editor.setCurrentIndex(1)

    def background_at_editor_center() -> object:
        # x=190 is well away from either the value text or dropdown arrow
        return root.grab().toImage().pixelColor(190, 40)

    try:
        root.show()
        editor.setFocus()
        app.processEvents()
        assert background_at_editor_center() == editor.palette().alternateBase().color()

        # The menu takes keyboard focus away; the old editor painted no
        # background here, exposing the table's original 'False' underneath
        root.setFocus()
        editor.clearFocus()
        editor.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
        app.sendEvent(editor, QEvent(QEvent.Type.Leave))
        app.processEvents()
        assert not editor.hasFocus()
        assert not editor.underMouse()
        assert background_at_editor_center() == editor.palette().base().color()

        editor.showPopup()
        app.processEvents()
        assert background_at_editor_center() == editor.palette().base().color()
    finally:
        editor.hidePopup()
        root.close()
        root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
