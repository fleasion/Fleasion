"""Fusion metrics shared with the Glass presentation."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Protocol, overload, override

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialogButtonBox,
    QProxyStyle,
    QStyle,
    QTableView,
    QTreeView,
)
from shiboken6 import isValid

from .menu_items import use_menu_items
from .spacing import CONTROL_HEIGHT, CONTROL_PADDING, GAP, PANEL_PADDING

if TYPE_CHECKING:
    from collections.abc import Callable

    from PySide6.QtCore import QRect, QSize
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import (
        QStyleHintReturn,
        QStyleOption,
        QStyleOptionComplex,
        QWidget,
    )


class Presentation(Protocol):
    """A look, such as Glass, drawn on top of the Fusion metrics."""

    # Whether selections are faint tints that keep the normal text color on top
    tinted_selection: bool

    def draw_primitive(
        self,
        element: QStyle.PrimitiveElement,
        option: QStyleOption,
        painter: QPainter,
        widget: QWidget | None,
    ) -> bool:
        """Draw a primitive and return True, or return False to leave it to Fusion."""
        ...

    def draw_control(
        self,
        element: QStyle.ControlElement,
        option: QStyleOption,
        painter: QPainter,
        widget: QWidget | None,
        fusion: Callable[[QStyleOption], None],
    ) -> bool:
        """Draw a control and return True, or return False to leave it to Fusion.

        ``fusion`` draws the control the Fusion way with any option, so a look
        can paint beneath it or hand it changed colors.
        """
        ...

    def draw_complex_control(
        self,
        control: QStyle.ComplexControl,
        option: QStyleOptionComplex,
        painter: QPainter,
        widget: QWidget | None,
    ) -> bool:
        """Draw a complex control and return True, or return False to leave it to Fusion."""
        ...

    def sub_control_rect(
        self,
        control: QStyle.ComplexControl,
        option: QStyleOptionComplex,
        sub_control: QStyle.SubControl,
        widget: QWidget | None,
    ) -> QRect | None:
        """Return where a part of a complex control sits, or None for the Fusion place."""
        ...

    def sub_element_rect(
        self, element: QStyle.SubElement, option: QStyleOption, widget: QWidget | None, rect: QRect
    ) -> QRect | None:
        """Return a replacement for the Fusion rectangle of a sub-element, or None."""
        ...

    def size_from_contents(
        self,
        contents_type: QStyle.ContentsType,
        option: QStyleOption,
        size: QSize,
        widget: QWidget | None,
        result: QSize,
    ) -> QSize | None:
        """Return a replacement for the size computed so far, or None to keep it."""
        ...

    def pixel_metric(
        self, metric: QStyle.PixelMetric, option: QStyleOption | None, widget: QWidget | None
    ) -> int | None:
        """Return a metric of the look, or None to keep the Fusion one."""
        ...

    def style_hint(
        self, hint: QStyle.StyleHint, option: QStyleOption | None, widget: QWidget | None
    ) -> int | None:
        """Return a style hint of the look, or None to keep the Fusion one."""
        ...


class ButtonOrderStyle(QProxyStyle):
    """A style with dialog buttons in the Windows order on every desktop.

    Qt otherwise follows each desktop's conventions, which reorders standard
    buttons between GNOME, KDE and Windows; confirmations keep one layout.

    The instance the application uses is also recorded, so code that draws
    with the style directly reaches it beneath any style-sheet wrapper.
    """

    _installed: ClassVar[ButtonOrderStyle | None] = None

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.setObjectName(self.baseStyle().objectName())

    @staticmethod
    def application_style() -> QStyle:
        """The application's own style, even while a style sheet wraps it."""
        installed = ButtonOrderStyle._installed
        if installed is not None and isValid(installed):
            return installed
        return QApplication.style()

    @overload
    def polish(self, app: QApplication, /) -> None: ...
    @overload
    def polish(self, widget: QWidget, /) -> None: ...
    @overload
    def polish(self, arg__1: QPalette | Qt.GlobalColor | QColor, /) -> None: ...
    @override
    def polish(
        self, target: QApplication | QWidget | QPalette | Qt.GlobalColor | QColor, /
    ) -> None:
        # Qt polishes the application with the style it has just been given
        if isinstance(target, QApplication):
            ButtonOrderStyle._installed = self
        super().polish(target)

    @override
    def styleHint(
        self,
        hint: QStyle.StyleHint,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
        returnData: QStyleHintReturn | None = None,
    ) -> int:
        if hint == QStyle.StyleHint.SH_DialogButtonLayout:
            return QDialogButtonBox.ButtonLayout.WinLayout.value
        return super().styleHint(hint, option, widget, returnData)


class LayoutStyle(ButtonOrderStyle):
    # The look drawn over Fusion while it is active; None draws plain Fusion
    presentation: ClassVar[Presentation | None] = None

    def __init__(self) -> None:
        super().__init__('Fusion')
        self.setObjectName('fusion')

    @staticmethod
    def selected_text_role() -> QPalette.ColorRole:
        """The palette role for text on a selection, solid or tinted."""
        presentation = LayoutStyle.presentation
        if presentation is not None and presentation.tinted_selection:
            return QPalette.ColorRole.Text
        return QPalette.ColorRole.HighlightedText

    @overload
    def polish(self, app: QApplication, /) -> None: ...
    @overload
    def polish(self, widget: QWidget, /) -> None: ...
    @overload
    def polish(self, arg__1: QPalette | Qt.GlobalColor | QColor, /) -> None: ...
    @override
    def polish(
        self, target: QApplication | QWidget | QPalette | Qt.GlobalColor | QColor, /
    ) -> None:
        if isinstance(target, QComboBox):
            # Popup rows match the menus behind every other dropdown button
            use_menu_items(target)
        elif isinstance(target, QTableView | QTreeView):
            # Tables and column trees stripe their rows like the Scraper, so wide rows
            # stay easy to follow
            target.setAlternatingRowColors(True)
        super().polish(target)

    @override
    def pixelMetric(
        self,
        metric: QStyle.PixelMetric,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
    ) -> int:
        if (presentation := LayoutStyle.presentation) is not None and (
            value := presentation.pixel_metric(metric, option, widget)
        ) is not None:
            return value

        match metric:
            case (
                QStyle.PixelMetric.PM_LayoutLeftMargin
                | QStyle.PixelMetric.PM_LayoutTopMargin
                | QStyle.PixelMetric.PM_LayoutRightMargin
                | QStyle.PixelMetric.PM_LayoutBottomMargin
            ):
                value = PANEL_PADDING
            case (
                QStyle.PixelMetric.PM_LayoutHorizontalSpacing
                | QStyle.PixelMetric.PM_LayoutVerticalSpacing
            ):
                value = GAP
            case QStyle.PixelMetric.PM_TabBarTabHSpace:
                value = PANEL_PADDING * 2
            case QStyle.PixelMetric.PM_TabBarTabVSpace:
                value = CONTROL_PADDING * 2 + 2
            case QStyle.PixelMetric.PM_HeaderMargin:
                value = CONTROL_PADDING
            case (
                QStyle.PixelMetric.PM_IndicatorWidth
                | QStyle.PixelMetric.PM_IndicatorHeight
                | QStyle.PixelMetric.PM_ExclusiveIndicatorWidth
                | QStyle.PixelMetric.PM_ExclusiveIndicatorHeight
            ):
                value = 18
            case (
                QStyle.PixelMetric.PM_CheckBoxLabelSpacing
                | QStyle.PixelMetric.PM_RadioButtonLabelSpacing
            ):
                value = GAP
            case _:
                return super().pixelMetric(metric, option, widget)
        return value

    @override
    def styleHint(
        self,
        hint: QStyle.StyleHint,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
        returnData: QStyleHintReturn | None = None,
    ) -> int:
        if (presentation := LayoutStyle.presentation) is not None and (
            value := presentation.style_hint(hint, option, widget)
        ) is not None:
            return value
        if hint == QStyle.StyleHint.SH_ComboBox_Popup and isinstance(widget, QComboBox):
            # Qt otherwise opens the selected item directly over the selector
            # before the popup has painted its first frame
            return 0
        return super().styleHint(hint, option, widget, returnData)

    @override
    def drawPrimitive(
        self,
        element: QStyle.PrimitiveElement,
        option: QStyleOption,
        painter: QPainter,
        widget: QWidget | None = None,
    ) -> None:
        # Fusion's dotted focus primitive can extend beyond button bounds and
        # overlap adjacent controls. The control's existing state remains visible
        if element == QStyle.PrimitiveElement.PE_FrameFocusRect and isinstance(
            widget, QAbstractButton
        ):
            return
        if (presentation := LayoutStyle.presentation) is not None and presentation.draw_primitive(
            element, option, painter, widget
        ):
            return
        if (
            element == QStyle.PrimitiveElement.PE_FrameTabWidget
            and widget is not None
            and widget.objectName() == 'DashboardTabs'
        ):
            divider = option.rect.adjusted(0, 0, 0, 2 - option.rect.height())
            painter.save()
            painter.setClipRect(divider, Qt.ClipOperation.IntersectClip)
            super().drawPrimitive(element, option, painter, widget)
            painter.restore()
            return
        super().drawPrimitive(element, option, painter, widget)

    @override
    def drawComplexControl(
        self,
        control: QStyle.ComplexControl,
        option: QStyleOptionComplex,
        painter: QPainter,
        widget: QWidget | None = None,
    ) -> None:
        if (
            presentation := LayoutStyle.presentation
        ) is not None and presentation.draw_complex_control(control, option, painter, widget):
            return
        super().drawComplexControl(control, option, painter, widget)

    @override
    def subControlRect(
        self,
        control: QStyle.ComplexControl,
        option: QStyleOptionComplex,
        sub_control: QStyle.SubControl,
        widget: QWidget | None = None,
    ) -> QRect:
        if (presentation := LayoutStyle.presentation) is not None and (
            rect := presentation.sub_control_rect(control, option, sub_control, widget)
        ) is not None:
            return rect
        return super().subControlRect(control, option, sub_control, widget)  # pyright: ignore[reportArgumentType] - Qt accepts a null widget

    @override
    def drawControl(
        self,
        element: QStyle.ControlElement,
        option: QStyleOption,
        painter: QPainter,
        widget: QWidget | None = None,
    ) -> None:
        if (presentation := LayoutStyle.presentation) is not None:

            def fusion(changed: QStyleOption) -> None:
                QProxyStyle.drawControl(self, element, changed, painter, widget)

            if presentation.draw_control(element, option, painter, widget, fusion):
                return
        super().drawControl(element, option, painter, widget)

    @override
    def subElementRect(
        self,
        element: QStyle.SubElement,
        option: QStyleOption,
        widget: QWidget | None = None,
    ) -> QRect:
        rect = super().subElementRect(element, option, widget)  # pyright: ignore[reportArgumentType] - Qt accepts a null widget
        if (
            element == QStyle.SubElement.SE_TabWidgetTabBar
            and widget is not None
            and widget.objectName() == 'DashboardTabs'
        ):
            contents = (
                widget.style()
                .subElementRect(QStyle.SubElement.SE_TabWidgetTabContents, option, widget)
                .adjusted(PANEL_PADDING, 0, -PANEL_PADDING, 0)
            )
            rect.setWidth(min(rect.width(), max(0, contents.width())))
            if widget.layoutDirection() == Qt.LayoutDirection.RightToLeft:
                rect.moveRight(contents.right())
            else:
                rect.moveLeft(contents.left())
        if (presentation := LayoutStyle.presentation) is not None and (
            changed := presentation.sub_element_rect(element, option, widget, rect)
        ) is not None:
            return changed
        return rect

    @override
    def sizeFromContents(
        self,
        contents_type: QStyle.ContentsType,
        option: QStyleOption,
        size: QSize,
        widget: QWidget | None = None,
    ) -> QSize:
        result = self._shared_size(contents_type, option, size, widget)
        if (presentation := LayoutStyle.presentation) is not None and (
            changed := presentation.size_from_contents(contents_type, option, size, widget, result)
        ) is not None:
            return changed
        return result

    def _shared_size(
        self,
        contents_type: QStyle.ContentsType,
        option: QStyleOption,
        size: QSize,
        widget: QWidget | None,
    ) -> QSize:
        """Fusion sizes with the control heights both presentations share."""
        result = super().sizeFromContents(contents_type, option, size, widget)  # pyright: ignore[reportArgumentType] - Qt accepts a null widget
        if (
            contents_type == QStyle.ContentsType.CT_LineEdit
            and widget is not None
            and isinstance(widget.parentWidget(), QAbstractSpinBox | QComboBox)
        ):
            return result
        if contents_type in {
            QStyle.ContentsType.CT_PushButton,
            QStyle.ContentsType.CT_ToolButton,
            QStyle.ContentsType.CT_LineEdit,
            QStyle.ContentsType.CT_ComboBox,
            QStyle.ContentsType.CT_SpinBox,
        }:
            result.setHeight(
                max(
                    result.height(),
                    CONTROL_HEIGHT,
                    option.fontMetrics.height() + CONTROL_PADDING * 2 + 2,
                )
            )
        elif contents_type == QStyle.ContentsType.CT_ItemViewItem:
            result.setHeight(max(result.height(), option.fontMetrics.height() + CONTROL_PADDING))
        return result
