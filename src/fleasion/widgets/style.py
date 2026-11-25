"""Fusion metrics shared with the Glass presentation."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final, Protocol, overload, override

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter, QPalette, QPixmap, QPixmapCache
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialogButtonBox,
    QProxyStyle,
    QStyle,
    QStyleOptionButton,
    QTableView,
    QTreeView,
)
from shiboken6 import isValid

from .dialog_icons import dialog_icon, tinted
from .menu_items import use_menu_items
from .painting import pixel_aligned
from .spacing import CONTROL_HEIGHT, CONTROL_PADDING, GAP, PANEL_PADDING, SPLITTER_WIDTH

if TYPE_CHECKING:
    from collections.abc import Callable

    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QIcon
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


_PM = QStyle.PixelMetric

# Metrics both presentations share. Qt asks for these hundreds of times a
# frame, so they are a single lookup rather than a chain of comparisons
_SHARED_METRICS: Final[dict[QStyle.PixelMetric, int]] = {
    **dict.fromkeys(
        (
            _PM.PM_LayoutLeftMargin,
            _PM.PM_LayoutTopMargin,
            _PM.PM_LayoutRightMargin,
            _PM.PM_LayoutBottomMargin,
        ),
        PANEL_PADDING,
    ),
    **dict.fromkeys(
        (
            _PM.PM_LayoutHorizontalSpacing,
            _PM.PM_LayoutVerticalSpacing,
            _PM.PM_CheckBoxLabelSpacing,
            _PM.PM_RadioButtonLabelSpacing,
        ),
        GAP,
    ),
    _PM.PM_TabBarTabHSpace: PANEL_PADDING * 2,
    _PM.PM_TabBarTabVSpace: CONTROL_PADDING * 2 + 2,
    _PM.PM_HeaderMargin: CONTROL_PADDING,
    _PM.PM_SplitterWidth: SPLITTER_WIDTH,
    **dict.fromkeys(
        (
            _PM.PM_IndicatorWidth,
            _PM.PM_IndicatorHeight,
            _PM.PM_ExclusiveIndicatorWidth,
            _PM.PM_ExclusiveIndicatorHeight,
        ),
        18,
    ),
}


def _label_icon_tinted(element: QStyle.ControlElement, option: QStyleOption) -> QStyleOption:
    """A button label's option with its line icon in the label's text color.

    Runs after a presentation has picked the label colors, so the icon on an
    accent default button turns the same color as its text
    """
    if element != QStyle.ControlElement.CE_PushButtonLabel or not isinstance(
        option, QStyleOptionButton
    ):
        return option

    color = option.palette.color(QPalette.ColorRole.ButtonText)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    if (icon := tinted(option.icon, color)) is None:
        return option
    changed = QStyleOptionButton(option)
    changed.icon = icon
    return changed


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

        if (value := _SHARED_METRICS.get(metric)) is not None:
            return value
        return super().pixelMetric(metric, option, widget)

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
        if hint == QStyle.StyleHint.SH_DialogButtonLayout:
            return QDialogButtonBox.ButtonLayout.WinLayout.value
        if hint == QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons:
            # Windows and macOS leave dialog buttons bare; every desktop shows icons
            return 1
        # Straight to Qt: this runs hundreds of times a frame, and the button
        # order above is all ButtonOrderStyle adds
        return QProxyStyle.styleHint(self, hint, option, widget, returnData)

    @override
    def standardIcon(
        self,
        standardIcon: QStyle.StandardPixmap,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
    ) -> QIcon:
        # Line icons stand in for Qt's bitmap ones where no icon theme has them
        if (icon := dialog_icon(standardIcon)) is not None:
            return icon
        return super().standardIcon(standardIcon, option, widget)

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
        if (
            element == QStyle.ControlElement.CE_PushButton
            and LayoutStyle.presentation is not None
            and isinstance(option, QStyleOptionButton)
            and self._draw_cached_button(option, painter, widget)
        ):
            return
        self._draw_control(element, option, painter, widget)

    def _draw_control(
        self,
        element: QStyle.ControlElement,
        option: QStyleOption,
        painter: QPainter,
        widget: QWidget | None,
    ) -> None:
        if (presentation := LayoutStyle.presentation) is not None:

            def fusion(changed: QStyleOption) -> None:
                QProxyStyle.drawControl(
                    self, element, _label_icon_tinted(element, changed), painter, widget
                )

            if presentation.draw_control(element, option, painter, widget, fusion):
                return
        super().drawControl(element, _label_icon_tinted(element, option), painter, widget)

    def _draw_cached_button(
        self, option: QStyleOptionButton, painter: QPainter, widget: QWidget | None
    ) -> bool:
        """Draw a push button from a pixmap of its look, rendering it on first use.

        A presentation's buttons take many Python calls apiece, and every frame of
        an animation repaints each button it moves. The key holds everything the
        option and painter contribute to the look; a new appearance clears the
        cache, see ThemeManager.apply_appearance
        """
        rect = option.rect
        if rect.isEmpty() or not pixel_aligned(painter):
            return False

        scale = painter.device().devicePixelRatioF()
        font = painter.font()
        icon_size = option.iconSize
        key = (
            f'fleasion-button:{rect.width()}x{rect.height()}@{scale}:'
            f'{option.state.value}:{option.features.value}:{option.direction.value}:'
            f'{option.palette.cacheKey()}:{font.key()}:{painter.pen().color().rgba()}:'
            f'{option.icon.cacheKey()}:{icon_size.width()}x{icon_size.height()}:{option.text}'
        )
        pixmap = QPixmap()
        if not QPixmapCache.find(key, pixmap):
            pixmap = QPixmap(round(rect.width() * scale), round(rect.height() * scale))
            pixmap.setDevicePixelRatio(scale)
            pixmap.fill(Qt.GlobalColor.transparent)
            local = QPainter(pixmap)
            local.setFont(font)
            local.setPen(painter.pen())
            local.setRenderHints(painter.renderHints())

            # A pixmap painter starts with an automatic direction, which makes
            # Qt push the label of a button with an icon against its far edge
            local.setLayoutDirection(painter.layoutDirection())
            moved = QStyleOptionButton(option)
            moved.rect = QRect(0, 0, rect.width(), rect.height())
            self._draw_control(QStyle.ControlElement.CE_PushButton, moved, local, widget)
            local.end()
            QPixmapCache.insert(key, pixmap)
        painter.drawPixmap(rect.topLeft(), pixmap)
        return True

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
