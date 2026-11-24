"""A titled card whose content collapses and expands with an animation."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QAbstractAnimation, QPointF, QRectF, QSize, Qt
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
from fleasion.utils.spring import SpringTuning
from fleasion.widgets.spacing import GAP, PANEL_MARGINS, PANEL_PADDING
from fleasion.widgets.spring_animation import SpringAnimation

from . import glass
from .theme import ThemeManager

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent


# Springs for the content height
# OPEN_SPRING = SpringTuning(damping=0.05, speed=5.0)  # Funny bounce
# OPEN_SPRING = SpringTuning(damping=0.65, speed=13.0)  # IOS moment
OPEN_SPRING = SpringTuning(damping=1.1, speed=18.0)
CLOSE_SPRING = SpringTuning(damping=1.1, speed=20.0)


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


class _SectionContent(QWidget):
    """The clipped content viewport, sized by its animation while it slides."""

    def __init__(self) -> None:
        super().__init__()
        self.sliding = False

    # Each animation step lays the section out before its parent has resized it.
    # A growing minimum here would squash the header to fit instead of clipping
    # the bottom of the content, and box layouts treat height-for-width as one.
    # The animated height stands in for both meanwhile, so an easing curve that
    # overshoots can also open the section past its natural height

    @override
    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self.sliding:
            hint.setHeight(self.maximumHeight())
        return hint

    @override
    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        if self.sliding:
            hint.setHeight(0)
        return hint

    @override
    def hasHeightForWidth(self) -> bool:
        return not self.sliding and super().hasHeightForWidth()

    def slide_to(self, height: float) -> None:
        """Show this much of the content; a spring undershooting zero stays closed."""
        self.setMaximumHeight(max(0, round(height)))


class _SectionLayout(QVBoxLayout):
    """Size a section by its content without claiming spare height.

    A scroll area sizes a page without height-for-width by its minimum height,
    which is only the header while the content slides. Always answering with
    the preferred height keeps every page on the height-for-width path, even
    when every section in it is animating at once
    """

    @override
    def expandingDirections(self) -> Qt.Orientation:
        # The inner stretch would otherwise make the whole section expanding
        return super().expandingDirections() & Qt.Orientation.Horizontal

    @override
    def hasHeightForWidth(self) -> bool:
        return True

    @override
    def heightForWidth(self, arg__1: int, /) -> int:
        if super().hasHeightForWidth():
            return super().heightForWidth(arg__1)
        return self.sizeHint().height()


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
        # A section takes the height of its header plus the visible content and
        # leaves spare viewport height to the parent's stretch.  Maximum would cap
        # it at sizeHint, which clips content that wraps taller in a narrow window
        policy = self.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        self.setSizePolicy(policy)

        self._expanded = expanded
        # Drives the content height; slide_to clamps an overshoot below zero
        self._animation = SpringAnimation(self)

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
        self._content = _SectionContent()
        self._animation.moved.connect(self._content.slide_to)
        self._animation.settled.connect(self._settle)
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(*PANEL_MARGINS)
        self._content_layout.setSpacing(GAP)
        self._content.setLayout(self._content_layout)

        if not expanded:
            self._content.setMaximumHeight(0)

        # --- Assemble ---
        main = _SectionLayout()
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

    def _natural_height(self) -> int:
        """The open content height at the current width, counting wrapped rows."""
        layout = self._content_layout
        if layout.hasHeightForWidth():
            return layout.totalHeightForWidth(self._content.width())
        return layout.sizeHint().height()

    def toggle(self) -> None:
        self._expanded = not self._expanded
        if not self._expanded:
            self._content.setMinimumHeight(0)
        self._arrow.set_expanded(self._expanded)

        # Carry on from where the spring is, keeping its velocity, so reversing
        # midway swings back. Keep child geometry frozen for BOTH directions:
        # enabling the layout midway through a reopen squashes its children
        animation = self._animation
        if animation.state() == QAbstractAnimation.State.Running:
            current_height = max(0, round(animation.position()))
        else:
            current_height = min(self._content.height(), self._content.maximumHeight())
            animation.jump_to(current_height)

        full_height = self._natural_height()
        if self._expanded and current_height == 0:
            # Populate natural child geometry before opening from zero height,
            # including sections constructed initially collapsed
            self._content.setMaximumHeight(full_height)
            self._content.resize(self._content.width(), full_height)
            self._content_layout.setEnabled(True)
            self._content_layout.activate()

        self._content_layout.setEnabled(False)
        self._content.sliding = True
        self._content.setMaximumHeight(current_height)

        if self._expanded:
            animation.animate_to(full_height, OPEN_SPRING)
        else:
            animation.animate_to(0, CLOSE_SPRING)

    def _settle(self) -> None:
        if self._expanded:
            self._finish_expand()
        else:
            self._finish_collapse()

    def _finish_expand(self) -> None:
        self._content.sliding = False
        self._content.setMaximumHeight(16777215)
        self._content_layout.setEnabled(True)
        self._content_layout.activate()
        # A scroll area shrinks content without height-for-width to its minimum
        # size, so pin it open; wrapping content is sized by width instead
        if not self._content_layout.hasHeightForWidth():
            self._content.setMinimumHeight(self._content_layout.sizeHint().height())
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None and (parent_layout := parent.layout()) is not None:
            parent_layout.invalidate()
            parent_layout.activate()

    def _finish_collapse(self) -> None:
        self._content.sliding = False
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
