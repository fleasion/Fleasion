"""Glass presentation details for item views: clipping, focus, headers and scrollbars."""

from __future__ import annotations

from contextlib import suppress
from math import ceil
from typing import TYPE_CHECKING, cast, override

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QAbstractItemView, QGraphicsEffect, QTableView, QTreeView, QWidget
from shiboken6 import isValid

from .scrollbars import BELOW_HEADER
from .style import RADIUS, glass_colors

if TYPE_CHECKING:
    from PySide6.QtCore import QAbstractItemModel, SignalInstance
    from PySide6.QtWidgets import QHeaderView, QStyleOption


# Object name the stylesheet gives the header-coloured cap above a view's scrollbar
SCROLLBAR_HEADER_CAP = 'GlassScrollBarHeaderCap'

# View property holding how far the rounded frame stops above the view's bottom
FRAME_TRIM = 'glassFrameTrim'

# Current-item highlight, inset from the cell so it floats over the selection band
_FOCUS_INSET_X = 3.5
_FOCUS_INSET_Y = 2.5
_FOCUS_RADIUS = 6.0


def paint_item_focus(painter: QPainter, option: QStyleOption, view: QAbstractItemView) -> None:
    """Paint the current item as a small glass pill tinted with the accent."""
    # Qt passes the text area; the whole cell keeps the pill clear of the text
    current = view.currentIndex()
    cell = view.visualRect(current) if current.isValid() else QRect()
    target = cell if cell.isValid() and cell.intersects(option.rect) else option.rect
    rect = QRectF(target).adjusted(_FOCUS_INSET_X, _FOCUS_INSET_Y, -_FOCUS_INSET_X, -_FOCUS_INSET_Y)
    if rect.width() < 4 or rect.height() < 4:
        return
    radius = min(_FOCUS_RADIUS, rect.height() / 2)
    colors = glass_colors()
    pill = QPainterPath()
    pill.addRoundedRect(rect, radius, radius)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Accent tint, a little denser at the top like the glass buttons
    tint = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    accent = QColor(colors.accent)
    accent.setAlphaF(0.24)
    tint.setColorAt(0, accent)
    accent.setAlphaF(0.14)
    tint.setColorAt(1, accent)
    painter.fillPath(pill, tint)

    # Sheen across the upper half
    sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    ink = QColor(colors.ink)
    ink.setAlphaF(0.08)
    sheen.setColorAt(0, ink)
    ink.setAlphaF(0)
    sheen.setColorAt(0.55, ink)
    painter.fillPath(pill, sheen)

    rim = QColor(colors.accent)
    rim.setAlphaF(0.70)
    painter.setPen(QPen(rim, 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(pill)
    painter.restore()


class ItemViewChrome(QObject):
    """
    Header-aware extras for one table or tree while Glass is active.

    The vertical scrollbar starts below the column header, behind a cap painted
    like the header, and a table's row numbers carry its alternating row colours.
    The rounded frame also ends on a whole row, so no sliver of a further stripe
    shows against its bottom edge.
    """

    def __init__(self, view: QTableView | QTreeView) -> None:
        super().__init__(view)
        self._view = view
        self._header = view.horizontalHeader() if isinstance(view, QTableView) else view.header()
        self._rows = view.verticalHeader() if isinstance(view, QTableView) else None

        # Kept so the filter compares identities without calling into Qt, which
        # may already have destroyed these children while the view tears down
        self._rows_viewport = self._rows.viewport() if self._rows is not None else None
        self._viewport = view.viewport()

        # Pixels the rounded frame stops short of the view's bottom so the rows fill
        # it exactly; Qt lays a stylesheet-styled view out from the stylesheet
        # alone, so the view keeps its geometry and only the clip moves up
        self._trim = 0
        self._model: QAbstractItemModel | None = None
        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.setInterval(0)
        self._snap_timer.timeout.connect(self._snap_to_rows)

        # Qt lays scrollbar widgets out above the bar inside the scrollbar column
        self._cap = QWidget()
        self._cap.setObjectName(SCROLLBAR_HEADER_CAP)
        self._cap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        view.addScrollBarWidget(self._cap, Qt.AlignmentFlag.AlignTop)

        # The matching gap below the bar keeps it clear of the raised bottom corners
        self._foot = QWidget()
        self._foot.setFixedHeight(0)
        view.addScrollBarWidget(self._foot, Qt.AlignmentFlag.AlignBottom)

        self._header.installEventFilter(self)
        self._viewport.installEventFilter(self)
        if self._rows_viewport is not None:
            self._rows_viewport.installEventFilter(self)
        self._sync_cap()
        self._watch_model()
        self._snap_timer.start()

    @staticmethod
    def attach(view: QWidget) -> None:
        """Give a table or tree its chrome once."""
        if not isinstance(view, QTableView | QTreeView):
            return
        if view.findChild(ItemViewChrome, options=Qt.FindChildOption.FindDirectChildrenOnly):
            return
        ItemViewChrome(view)

    @staticmethod
    def detach_all(views: list[QWidget]) -> None:
        for view in views:
            if not isValid(view):
                continue
            for chrome in view.findChildren(
                ItemViewChrome, options=Qt.FindChildOption.FindDirectChildrenOnly
            ):
                chrome.detach()

    def detach(self) -> None:
        # Nothing may snap again before the deferred deletion
        self._snap_timer.stop()
        self._watch_model(stop=True)

        # Reparenting first lets the scrollbar reclaim the space immediately
        for spacer in (self._cap, self._foot):
            if isValid(spacer):
                spacer.setParent(None)  # pyright: ignore[reportArgumentType]
                spacer.deleteLater()
        if isValid(self._header):
            self._header.removeEventFilter(self)
        if isValid(self._viewport):
            self._viewport.removeEventFilter(self)
        if isValid(self._view):
            self._mark_scrollbar(below_header=None)
            self._set_trim(0)
        if self._rows_viewport is not None and isValid(self._rows_viewport):
            self._rows_viewport.removeEventFilter(self)
            self._rows_viewport.update()
        self.deleteLater()

    def _sync_cap(self) -> None:
        visible = self._header.isVisibleTo(self._view)
        self._cap.setFixedHeight(self._header.height() if visible else 0)
        self._cap.setVisible(visible)

        # Headerless bars need the corner clearance at the top as well
        self._mark_scrollbar(below_header=visible or None)

    def _mark_scrollbar(self, *, below_header: bool | None) -> None:
        bar = self._view.verticalScrollBar()
        if bar.property(BELOW_HEADER) == below_header:
            return
        bar.setProperty(BELOW_HEADER, below_header)
        bar.update()

    def _current_model(self) -> QAbstractItemModel | None:
        # Qt returns no model for a view that has not been given one yet
        return cast('QAbstractItemModel | None', self._view.model())

    def _watch_model(self, *, stop: bool = False) -> None:
        model = None if stop else self._current_model()
        if model is self._model:
            return
        if self._model is not None and isValid(self._model):
            for signal in self._row_signals(self._model):
                with suppress(RuntimeError, TypeError):
                    signal.disconnect(self._snap_timer.start)
        self._model = model
        if model is not None:
            for signal in self._row_signals(model):
                signal.connect(self._snap_timer.start)

    @staticmethod
    def _row_signals(model: QAbstractItemModel) -> tuple[SignalInstance, ...]:
        return (model.rowsInserted, model.rowsRemoved, model.modelReset, model.layoutChanged)

    def _row_height(self) -> int:
        model = self._current_model()
        root = self._view.rootIndex()
        if model is None or model.rowCount(root) == 0:
            return 0
        if isinstance(self._view, QTableView):
            return self._view.rowHeight(0)
        return self._view.visualRect(model.index(0, 0, root)).height()

    def _snap_to_rows(self) -> None:
        """End the rounded frame on a row boundary, giving the spare pixels to the window."""
        self._watch_model()
        height = self._viewport.height()
        row = self._row_height()

        # A horizontal scrollbar already separates the rows from the bottom edge
        flush = 0 < row < height and not self._view.horizontalScrollBar().isVisible()
        self._set_trim(height % row if flush else 0)

    def _set_trim(self, trim: int) -> None:
        if trim == self._trim:
            return
        self._trim = trim
        self._view.setProperty(FRAME_TRIM, trim or None)
        if isValid(self._foot):
            self._foot.setFixedHeight(trim)
        if (effect := self._view.graphicsEffect()) is not None:
            effect.update()

    def _paint_row_stripes(self, rows: QHeaderView) -> None:
        if not self._view.alternatingRowColors():
            return
        color = self._view.palette().color(QPalette.ColorRole.AlternateBase)
        viewport = rows.viewport()
        first = rows.visualIndexAt(0)
        last = rows.visualIndexAt(viewport.height() - 1)
        if first < 0:
            return
        if last < 0:
            last = rows.count() - 1

        # QTableView stripes odd visual rows; the header sections paint over this
        painter = QPainter(viewport)
        for visual in range(first | 1, last + 1, 2):
            logical = rows.logicalIndex(visual)
            if rows.isSectionHidden(logical):
                continue
            top = rows.sectionViewportPosition(logical)
            painter.fillRect(QRect(0, top, viewport.width(), rows.sectionSize(logical)), color)
        painter.end()

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if watched is self._header and kind in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
        }:
            self._sync_cap()
        elif watched is self._viewport and kind == QEvent.Type.Resize:
            self._snap_timer.start()
        elif (
            watched is self._rows_viewport and self._rows is not None and kind == QEvent.Type.Paint
        ):
            self._paint_row_stripes(self._rows)
        return False


class RoundedItemViewEffect(QGraphicsEffect):
    """Clips an item view to a rounded rectangle and draws its rim on top."""

    def __init__(self, parent: QWidget, *, radius: int = RADIUS, popup: bool = False) -> None:
        super().__init__(parent)
        self._radius = radius
        self._popup = popup

        # Supersampled rim corners, rebuilt when size, scale or colour changes
        self._rim_key: tuple[QSize, float, int, int, int, int] | None = None
        self._rim_corners: list[tuple[QPointF, QImage]] = []

    @override
    def draw(self, painter: QPainter) -> None:
        offset = QPoint()
        pixmap = self.sourcePixmap(
            Qt.CoordinateSystem.LogicalCoordinates, offset, self.PixmapPadMode.NoPad
        )
        if pixmap.isNull():
            return

        whole = QRectF(pixmap.rect())
        whole.setSize(whole.size() / pixmap.devicePixelRatio())

        # A view may end its frame short of its bottom edge, see ItemViewChrome
        view = self.parent()
        trim = view.property(FRAME_TRIM) if isinstance(view, QAbstractItemView) else None
        rect = whole.adjusted(0, 0, 0, -trim) if isinstance(trim, int) else whole

        # Remove the outside with fractional coverage instead of a binary widget mask
        frame = QPainterPath()
        frame.addRoundedRect(rect, self._radius, self._radius)
        outside = QPainterPath()
        outside.addRect(whole)

        clip = QPainter(pixmap)
        clip.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        clip.fillPath(outside.subtracted(frame), Qt.GlobalColor.black)
        clip.end()
        painter.drawPixmap(offset, pixmap)

        if not isinstance(view, QAbstractItemView) or (width := view.frameWidth()) <= 0:
            return

        # Paint the rim once, after clipping the rectangular child backgrounds.
        # Popups get a brighter rim to stand out from what they cover
        inset = width / 2
        rim_rect = rect.adjusted(inset, inset, -inset, -inset)
        rim = QLinearGradient(rim_rect.topLeft(), rim_rect.bottomLeft())
        color = QColor(glass_colors().ink)
        color.setAlphaF(0.28 if self._popup else 0.18)
        rim.setColorAt(0, color)
        color.setAlphaF(0.14 if self._popup else 0.08)
        rim.setColorAt(1, color)

        outline = QPainterPath()
        radius = max(0.0, self._radius - inset)
        outline.addRoundedRect(rim_rect, radius, radius)

        painter.save()
        painter.translate(offset)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(rim, width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Size of each corner tile in device pixels, capped to half the frame
        ratio = pixmap.devicePixelRatio()
        frame_width = round(rect.width() * ratio)
        frame_height = round(rect.height() * ratio)
        extent = min(
            ceil((self._radius + width) * ratio),
            frame_width // 2,
            frame_height // 2,
        )
        if extent < 1:
            painter.restore()
            return
        corner_size = extent / ratio

        # Straight edges are drawn directly, at native resolution
        painter.drawLine(QPointF(corner_size, inset), QPointF(rect.width() - corner_size, inset))
        painter.drawLine(
            QPointF(corner_size, rect.height() - inset),
            QPointF(rect.width() - corner_size, rect.height() - inset),
        )
        painter.drawLine(QPointF(inset, corner_size), QPointF(inset, rect.height() - corner_size))
        painter.drawLine(
            QPointF(rect.width() - inset, corner_size),
            QPointF(rect.width() - inset, rect.height() - corner_size),
        )

        # Corners are rendered at 4x and scaled down, so the curve stays smooth
        key = (pixmap.size(), ratio, width, glass_colors().ink.rgba(), frame_width, frame_height)
        if key != self._rim_key:
            self._rim_corners = []
            for x in (0, frame_width - extent):
                for y in (0, frame_height - extent):
                    corner = QImage(
                        extent * 4, extent * 4, QImage.Format.Format_ARGB32_Premultiplied
                    )
                    corner.fill(Qt.GlobalColor.transparent)

                    sampling = QPainter(corner)
                    sampling.setRenderHint(QPainter.RenderHint.Antialiasing)
                    sampling.scale(ratio * 4, ratio * 4)
                    sampling.translate(-x / ratio, -y / ratio)
                    sampling.setPen(pen)
                    sampling.setBrush(Qt.BrushStyle.NoBrush)
                    sampling.drawPath(outline)
                    sampling.end()

                    corner = corner.scaled(
                        extent,
                        extent,
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    corner.setDevicePixelRatio(ratio)
                    self._rim_corners.append((QPointF(x / ratio, y / ratio), corner))
            self._rim_key = key

        for position, corner in self._rim_corners:
            painter.drawImage(position, corner)
        painter.restore()
