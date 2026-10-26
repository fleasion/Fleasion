"""Glass headers share the table's outer frame rather than drawing their own."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QRegion
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fleasion.gui.glass.itemview import RoundedItemViewEffect
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    original_theme = ThemeManager._current_theme  # pyright: ignore[reportPrivateUsage]
    original_effective = ThemeManager._effective_theme  # pyright: ignore[reportPrivateUsage]
    original_sheet = application.styleSheet()
    try:
        yield application
    finally:
        ThemeManager.apply_theme('Light')
        ThemeManager.apply_theme(original_theme)
        ThemeManager._effective_theme = original_effective  # pyright: ignore[reportPrivateUsage]
        application.setStyleSheet(original_sheet)
        application.processEvents()


@pytest.mark.parametrize('theme_before_widgets', [True, False])
def test_tree_and_table_headers_have_no_second_rounded_frame(
    app: QApplication, theme_before_widgets: bool
) -> None:
    if theme_before_widgets:
        ThemeManager.apply_theme('Glass')
    else:
        ThemeManager.apply_theme('Dark')

    window = QDialog()
    layout = QVBoxLayout(window)
    tree = QTreeWidget()
    tree.setHeaderLabels(['Status', 'Profile Name', 'Mode'])
    tree.setColumnWidth(0, 90)
    tree.setColumnWidth(1, 160)
    for index in range(35):
        QTreeWidgetItem(tree, ['On', f'Profile {index}', 'Group'])
    layout.addWidget(tree)

    table = QTableWidget()
    table.setObjectName('ScraperAssetsTable')
    table.setColumnCount(3)
    table.setHorizontalHeaderLabels(['Hash/Name', 'Type', 'Updated At'])
    table.setRowCount(30)
    for index in range(30):
        table.setItem(index, 0, QTableWidgetItem(f'Asset {index}'))
    layout.addWidget(table)

    window.resize(830, 660)
    try:
        window.show()
        app.processEvents()
        if not theme_before_widgets:
            ThemeManager.apply_theme('Glass')
        QTest.qWait(25)

        for view, headers in (
            (tree, (tree.header(),)),
            (table, (table.horizontalHeader(), table.verticalHeader())),
        ):
            assert view.frameWidth() == 1
            if QApplication.platformName() in {'xcb', 'offscreen'}:
                # The outside corners must remain rounded; the previous fix
                # accidentally squared them off to hide a header paint issue
                image = view.grab().toImage()
                inset = round(20 * image.devicePixelRatio())
                for x in (0, image.width() - 1):
                    corner = image.pixelColor(x, 0)
                    rim = image.pixelColor(inset if x == 0 else image.width() - inset - 1, 0)
                    assert corner.alpha() + 25 < rim.alpha()
                # Check bottom corners independently so the top stays rounded
                for x in (0, image.width() - 1):
                    corner = image.pixelColor(x, image.height() - 1)
                    rim = image.pixelColor(
                        inset if x == 0 else image.width() - inset - 1, image.height() - 1
                    )
                    assert corner.alpha() + 10 < rim.alpha()
            if isinstance(view, QTreeWidget):
                # Header backgrounds must not fill either rounded outer corner
                pixels = view.grab().toImage()
                for x in (1, 2, 3, pixels.width() - 2, pixels.width() - 3, pixels.width() - 4):
                    edge = 0 if x < pixels.width() // 2 else pixels.width() - 1
                    assert pixels.pixelColor(x, 1) == pixels.pixelColor(edge, 0)
            for header in headers:
                assert header.frameWidth() == 0
                assert header.parentWidget() is view
                _assert_no_inner_corner_cutout(header)
                _assert_glass_header_clip_matches_view(view, header)
            if isinstance(view, QTableWidget):
                corner = next(
                    child
                    for child in view.findChildren(QWidget)
                    if child.metaObject().className() == 'QTableCornerButton'
                )
                _assert_glass_header_clip_matches_view(view, corner)

        # The shared header rule must stay stable when the item views resize
        window.resize(1040, 720)
        QTest.qWait(25)
        for view, header in (
            (tree, tree.header()),
            (table, table.horizontalHeader()),
            (table, table.verticalHeader()),
        ):
            assert header.frameWidth() == 0
            _assert_no_inner_corner_cutout(header)
            _assert_glass_header_clip_matches_view(view, header)

        ThemeManager.apply_theme('System')
        app.processEvents()
        for widget in (tree.header(), table.horizontalHeader(), table.verticalHeader()):
            assert widget.mask().isEmpty()
        ThemeManager.apply_theme('Glass')
        app.processEvents()
        _assert_glass_header_clip_matches_view(tree, tree.header())
    finally:
        ThemeManager.apply_theme('Light')
        window.close()
        window.deleteLater()
        app.processEvents()


def _assert_no_inner_corner_cutout(header: QHeaderView) -> None:
    """The header's first pixel must not reveal a separate rounded mask."""
    image = header.grab().toImage()
    assert image.width() > 25
    assert image.height() > 10
    # With the old inherited border-radius, (0, 0) was completely transparent
    # despite the first section painting its top row
    corner_alpha = image.pixelColor(0, 0).alpha()
    interior_alpha = image.pixelColor(round(20 * image.devicePixelRatio()), 0).alpha()
    assert header.mask().isEmpty()
    assert corner_alpha == interior_alpha
    assert corner_alpha > 0


def _assert_glass_header_clip_matches_view(view: QWidget, child: QWidget) -> None:
    """The whole view is clipped smoothly without binary child masks."""
    assert isinstance(view.graphicsEffect(), RoundedItemViewEffect)
    assert child.mask().isEmpty()


@pytest.mark.parametrize('view_type', ['list', 'list_widget', 'tree', 'table'])
def test_glass_clips_headerless_and_model_backed_item_views_on_x11(
    app: QApplication, view_type: str
) -> None:
    """The clipping is shared by every Qt view type, including list viewports."""
    if QApplication.platformName() != 'xcb':
        pytest.skip('Requires native X11 widget lifecycle')

    from PySide6.QtCore import QStringListModel
    from PySide6.QtGui import QStandardItemModel
    from PySide6.QtWidgets import QListView, QListWidget, QTableView, QTreeView

    ThemeManager.apply_theme('System')
    window = QDialog()
    layout = QVBoxLayout(window)
    if view_type == 'list':
        view = QListView(window)
        view.setModel(QStringListModel(['entry one', 'entry two', 'entry three'], view))
    elif view_type == 'list_widget':
        view = QListWidget(window)
        view.addItems(['entry one', 'entry two', 'entry three'])
    elif view_type == 'tree':
        view = QTreeView(window)
        view.setModel(QStandardItemModel(12, 3, view))
    else:
        view = QTableView(window)
        view.setModel(QStandardItemModel(12, 3, view))
    layout.addWidget(view)
    window.resize(420, 340)
    try:
        window.show()
        QTest.qWait(20)
        original_masks = {
            child: QRegion(child.mask())
            for child in view.findChildren(QWidget)
            if child.parentWidget() is view
        }
        ThemeManager.apply_theme('Glass')
        QTest.qWait(30)
        children = [
            child
            for child in view.findChildren(QWidget)
            if child.parentWidget() is view
            and (
                child is view.viewport()
                or isinstance(child, QHeaderView)
                or child.metaObject().className() == 'QTableCornerButton'
            )
        ]
        assert view.viewport() in children
        assert isinstance(view.graphicsEffect(), RoundedItemViewEffect)
        for child in children:
            _assert_glass_header_clip_matches_view(view, child)

        # Viewport geometry changes without changing the item-view type
        window.resize(740, 520)
        QTest.qWait(40)
        for child in children:
            _assert_glass_header_clip_matches_view(view, child)

        ThemeManager.apply_theme('System')
        app.processEvents()
        assert view.graphicsEffect() is None
        for child in children:
            assert child.mask() == original_masks[child]

        ThemeManager.apply_theme('Glass')
        QTest.qWait(30)
        for child in children:
            _assert_glass_header_clip_matches_view(view, child)
    finally:
        ThemeManager.apply_theme('Light')
        window.close()
        window.deleteLater()
        app.processEvents()
