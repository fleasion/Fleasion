"""Fusion metrics shared with the Glass presentation."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QProxyStyle, QStyle

from .spacing import CONTROL_HEIGHT, CONTROL_PADDING, GAP, PANEL_PADDING

if TYPE_CHECKING:
    from PySide6.QtCore import QRect, QSize
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QStyleOption, QWidget


class LayoutStyle(QProxyStyle):
    def __init__(self) -> None:
        super().__init__('Fusion')
        self.setObjectName('fusion')

    @override
    def pixelMetric(
        self,
        metric: QStyle.PixelMetric,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
    ) -> int:
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
    def drawPrimitive(
        self,
        element: QStyle.PrimitiveElement,
        option: QStyleOption,
        painter: QPainter,
        widget: QWidget | None = None,
    ) -> None:
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
        return rect

    @override
    def sizeFromContents(
        self,
        contents_type: QStyle.ContentsType,
        option: QStyleOption,
        size: QSize,
        widget: QWidget | None = None,
    ) -> QSize:
        result = super().sizeFromContents(contents_type, option, size, widget)  # pyright: ignore[reportArgumentType] - Qt accepts a null widget
        if (
            contents_type == QStyle.ContentsType.CT_SpinBox
            and widget is not None
            and widget.testAttribute(Qt.WidgetAttribute.WA_StyleSheetTarget)
        ):
            # QSS adds its own padding around the spin box's content size
            result.setHeight(size.height())
            return result
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
