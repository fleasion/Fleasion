"""A titled card whose content collapses and expands with an animation."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fleasion.localization import tr
from fleasion.widgets.spacing import GAP, PANEL_MARGINS, PANEL_PADDING

from . import glass
from .theme import ThemeManager

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent


class _SectionArrowButton(QPushButton):
    """Draw both disclosure directions around an identical fixed center."""

    def __init__(self, *, expanded: bool) -> None:
        super().__init__()
        self._expanded = expanded
        self.setCheckable(True)
        self.setChecked(expanded)
        self.setFlat(True)
        # A single stylesheet prevents state-dependent font metrics from
        # changing the button width and shifting the adjacent section title
        self.setStyleSheet(
            'border: 0; background: transparent; padding: 0; '
            'min-width: 14px; max-width: 14px; min-height: 18px; max-height: 18px;'
        )
        self.setFixedSize(14, 18)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.setChecked(expanded)
        self.update()

    @override
    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().buttonText())
        # Widget dimensions are pixel counts; QRectF(self.rect()).center()
        # would place the center half a pixel off for these even dimensions
        x, y = self.width() / 2, self.height() / 2
        if self._expanded:
            points = [QPointF(x - 5, y - 3), QPointF(x + 5, y - 3), QPointF(x, y + 6)]
        else:
            points = [QPointF(x - 3, y - 5), QPointF(x + 6, y), QPointF(x - 3, y + 5)]
        painter.drawPolygon(QPolygonF(points))
        painter.end()


class CollapsibleSection(QWidget):
    """A section with a clickable header that collapses/expands its content."""

    WINDOWS_COLLAPSED_ARROW_SIZE = 19

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        expanded: bool = True,
        header_widgets: list[QWidget] | None = None,
        *,
        show_separator: bool = True,
    ) -> None:
        super().__init__(parent)
        # A section should never absorb spare vertical space from its parent.
        # Its height is determined by the header plus the currently visible
        # content; any remaining viewport height belongs outside the card.
        policy = self.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Policy.Maximum)
        self.setSizePolicy(policy)

        self._expanded = expanded
        self._animation: QPropertyAnimation | None = None

        # Header row
        # Keep the header in its own fixed-height widget.  During a collapse,
        # the content's maximumHeight changes before the parent layout has
        # necessarily applied the section's new sizeHint.  A bare QHBoxLayout
        # can absorb that transient spare height and visibly push the header
        # separator downward for a paint frame.  A fixed-height wrapper keeps
        # the header geometry stable while the content viewport clips closed.
        self._header_widget = QWidget()
        self._header_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        header_layout = QHBoxLayout(self._header_widget)
        header_layout.setContentsMargins(PANEL_PADDING, GAP, PANEL_PADDING, GAP)
        header_layout.setSpacing(GAP)

        self._arrow = _SectionArrowButton(expanded=expanded)
        self._arrow.clicked.connect(self.toggle)
        header_layout.addWidget(self._arrow)

        self._title_label = QLabel(tr('ui.gui.modifications_tab.b_value_b', value0=title))
        self._title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title_label.mousePressEvent = lambda _: self.toggle()
        header_layout.addWidget(self._title_label)

        header_layout.addStretch()

        if header_widgets:
            for w in header_widgets:
                header_layout.addWidget(w)

        # --- Content container ---
        self._content = QWidget()
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(*PANEL_MARGINS)
        self._content_layout.setSpacing(GAP)
        self._content.setLayout(self._content_layout)

        if not expanded:
            self._content.setMaximumHeight(0)

        # --- Assemble ---
        main = QVBoxLayout()
        # Keep the outer border clear of the header and its separator,
        # including when the content viewport is completely collapsed
        main.setContentsMargins(1, 1, 1, 1)
        main.setSpacing(0)
        main.addWidget(self._header_widget)
        if show_separator:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFrameShadow(QFrame.Shadow.Sunken)
            main.addWidget(sep)
        main.addWidget(self._content)
        # Absorb any transient excess section height below the content.  This
        # is important during collapse because the child maximumHeight can be
        # updated one event-loop turn before the parent geometry catches up.
        main.addStretch()
        self.setLayout(main)

    @override
    def paintEvent(self, a0: QPaintEvent) -> None:
        """Draw a rounded-rect card that adapts to dark and light themes."""
        colors = ThemeManager.panel_colors(self.palette())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)
        painter.fillPath(path, colors.section_background)
        if glass.theme.active:
            # Match the table header overlay in both light and dark Glass
            header = QRectF(rect.left(), rect.top(), rect.width(), self._header_widget.height())
            header_path = QPainterPath()
            header_path.addRect(header)
            header_color = QColor(colors.section_border)
            header_color.setAlpha(11)
            painter.fillPath(path.intersected(header_path), header_color)
        painter.setPen(colors.section_border)
        painter.drawPath(path)
        painter.end()

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def add_widget(self, widget: QWidget) -> None:
        self._content_layout.addWidget(widget)

    def toggle(self) -> None:
        self._expanded = not self._expanded
        if not self._expanded:
            self._content.setMinimumHeight(0)
        self._arrow.set_expanded(self._expanded)

        # Restart from the currently visible height, not the previous target.
        # Keep child geometry frozen for BOTH directions: enabling the layout
        # midway through a reopen squashes its children into the clipped area.
        current_height = min(self._content.height(), self._content.maximumHeight())
        if self._animation is not None:
            self._animation.stop()
            self._animation.deleteLater()

        full_height = self._content_layout.sizeHint().height()
        if self._expanded and current_height == 0:
            # Populate natural child geometry before opening from zero height,
            # including sections constructed initially collapsed
            self._content.setMaximumHeight(full_height)
            self._content.resize(self._content.width(), full_height)
            self._content_layout.setEnabled(True)
            self._content_layout.activate()

        self._content_layout.setEnabled(False)
        self._content.setMaximumHeight(current_height)
        self._animation = QPropertyAnimation(self._content, b'maximumHeight', self)
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._animation.setStartValue(current_height)
        self._animation.setEndValue(full_height if self._expanded else 0)
        self._animation.finished.connect(
            self._finish_expand if self._expanded else self._finish_collapse
        )
        self._animation.start()

    def _finish_expand(self) -> None:
        self._content.setMaximumHeight(16777215)
        self._content_layout.setEnabled(True)
        self._content_layout.activate()
        self._content.setMinimumHeight(self._content_layout.sizeHint().height())
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None and (parent_layout := parent.layout()) is not None:
            parent_layout.invalidate()
            parent_layout.activate()

    def _finish_collapse(self) -> None:
        # Restore layout updates after clipping has fully closed
        self._content_layout.setEnabled(True)
        self._content_layout.activate()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            parent_layout = parent.layout()
            if parent_layout is not None:
                parent_layout.invalidate()
                parent_layout.activate()
