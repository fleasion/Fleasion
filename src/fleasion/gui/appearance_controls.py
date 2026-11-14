"""Theme cards and accent swatches for the appearance settings."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QColorDialog,
    QGridLayout,
    QSizePolicy,
    QWidget,
)

from fleasion.appearance import (
    ACCENT_KEYS,
    ACCENTS,
    COLOR_MODES,
    CUSTOM_ACCENT,
    DEFAULT_CUSTOM_ACCENT,
    SYSTEM_ACCENT,
    THEME_ACCENT,
    accent_label,
    accent_name,
    theme_label,
)
from fleasion.appearance.colors import on_accent
from fleasion.appearance.palettes import mix
from fleasion.localization import tr
from fleasion.widgets.flow_layout import FlowLayout

from .theme import ThemeManager

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent, QResizeEvent

    from fleasion.appearance import ColorMode
    from fleasion.appearance.palettes import ThemeColors


# Sized to sit beside ordinary controls rather than to dominate the section
_SWATCH_SIZE = 33
_SWATCH_INSET = 2.0
_CARD_HEIGHT = 36
_CARD_MIN_WIDTH = 80
_CARD_TARGET_WIDTH = 100
_CARD_MAX_WIDTH = 120
_CARD_MAX_COLUMNS = 5
_CARD_GAP = 6
_CARD_RADIUS = 6.0


def choice_label(name: str) -> str:
    """Label a style, theme, or accent choice."""
    if name == 'Glass':
        return tr('settings.appearance.theme.glass')
    return theme_label(name) or accent_label(name) or name


def accent_tooltip(name: str) -> str:
    if name == THEME_ACCENT:
        theme = choice_label(ThemeManager.current_color_mode())
        return tr('settings.appearance.accent_theme_named', theme=theme)
    if name == SYSTEM_ACCENT:
        return tr('settings.appearance.accent_system_named')
    return choice_label(name)


def swatch_color(name: str, *, custom_color: str = DEFAULT_CUSTOM_ACCENT) -> QColor:
    if name == THEME_ACCENT:
        return ThemeManager.theme_accent_color()
    if name == SYSTEM_ACCENT:
        return ThemeManager.system_accent_color()
    if name == CUSTOM_ACCENT:
        return QColor(custom_color)
    resolved = accent_name(name)
    return QColor(resolved if resolved.startswith('#') else ACCENTS[resolved])


def _swatch_letter(name: str) -> str:
    # Theme and System are marked with the first letter of their localized names
    if name in {THEME_ACCENT, SYSTEM_ACCENT}:
        return choice_label(name)[:1].upper()
    return ''


def paint_swatch(
    painter: QPainter,
    rect: QRectF,
    color: QColor,
    *,
    letter: str = '',
    selected: bool = False,
    custom: bool = False,
) -> None:
    """Paint a round accent swatch, its letter, and the check of the selected one."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    diameter = rect.width()
    painter.setPen(QPen(QColor(128, 128, 128, 110), 1))
    painter.setBrush(color)
    painter.drawEllipse(rect)
    if custom:
        # A spectrum rim marks the swatch that opens the color picker
        gradient = QConicalGradient(rect.center(), 90)
        for stop, hue in ((0.0, 0), (1 / 6, 60), (2 / 6, 120), (3 / 6, 180), (4 / 6, 240)):
            gradient.setColorAt(stop, QColor.fromHsv(hue, 210, 255))
        gradient.setColorAt(5 / 6, QColor.fromHsv(300, 210, 255))
        gradient.setColorAt(1.0, QColor.fromHsv(0, 210, 255))
        rim = max(2.0, diameter * 0.11)
        painter.setPen(QPen(gradient, rim))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect.adjusted(rim / 2, rim / 2, -rim / 2, -rim / 2))
    foreground = on_accent(color)
    if selected:
        pen = QPen(foreground, max(2.0, diameter * 0.11))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        x, y, unit = rect.x(), rect.y(), diameter / 26
        check = QPainterPath(QPointF(x + 7.5 * unit, y + 13.5 * unit))
        check.lineTo(QPointF(x + 11.5 * unit, y + 17.5 * unit))
        check.lineTo(QPointF(x + 19 * unit, y + 9 * unit))
        painter.drawPath(check)
    elif letter:
        font = QFont(painter.font())
        font.setPixelSize(max(8, round(diameter * 0.5)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(foreground)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, letter)
    painter.restore()


def swatch_icon(
    name: str, *, selected: bool = False, custom_color: str = DEFAULT_CUSTOM_ACCENT
) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    paint_swatch(
        painter,
        QRectF(3, 3, 26, 26),
        swatch_color(name, custom_color=custom_color),
        letter=_swatch_letter(name),
        selected=selected,
        custom=name == CUSTOM_ACCENT,
    )
    painter.end()
    return QIcon(pixmap)


class AccentSwatch(QAbstractButton):
    """One round, checkable accent choice."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self._color = QColor()
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
        self.setObjectName(f'Accent{name}')
        self.setProperty('accentName', name)

    def set_color(self, color: QColor) -> None:
        tooltip = accent_tooltip(self.name)
        self.setToolTip(tooltip)
        self.setAccessibleName(tr('settings.appearance.accent_named', name=tooltip))
        if color != self._color:
            self._color = QColor(color)
            self.update()

    def color(self) -> QColor:
        return QColor(self._color)

    @override
    def sizeHint(self) -> QSize:
        return QSize(_SWATCH_SIZE, _SWATCH_SIZE)

    @override
    def paintEvent(self, e: QPaintEvent, /) -> None:
        painter = QPainter(self)
        inset = _SWATCH_INSET
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        paint_swatch(
            painter,
            rect,
            self._color,
            letter=_swatch_letter(self.name),
            selected=self.isChecked(),
            custom=self.name == CUSTOM_ACCENT,
        )
        ring = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        if self.hasFocus() or (self.underMouse() and not self.isChecked()):
            ring.setAlpha(200 if self.hasFocus() else 90)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(ring, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75))
        painter.end()


class AccentSelector(QWidget):
    accent_changed = Signal(str)

    def __init__(
        self, parent: QWidget | None = None, *, custom_color: str = DEFAULT_CUSTOM_ACCENT
    ) -> None:
        super().__init__(parent)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, AccentSwatch] = {}
        self._selected_name: str | None = None
        self._accent = THEME_ACCENT
        resolved = accent_name(custom_color)
        self._custom_color = resolved if resolved.startswith('#') else DEFAULT_CUSTOM_ACCENT
        layout = FlowLayout(self, spacing=4)
        for name in ACCENT_KEYS:
            button = AccentSwatch(name, self)
            self._group.addButton(button)
            self._buttons[name] = button
            layout.add(button)
        self.refresh_colors()
        self._group.buttonClicked.connect(self._selected)
        ThemeManager.events().changed.connect(self.refresh_colors)

    def refresh_colors(self) -> None:
        for name, button in self._buttons.items():
            button.set_color(swatch_color(name, custom_color=self._custom_color))

    def button(self, name: str) -> AccentSwatch:
        return self._buttons[name]

    def _selected(self, button: QAbstractButton) -> None:
        name = str(button.property('accentName'))
        if name == CUSTOM_ACCENT:
            picked = QColorDialog.getColor(
                QColor(self._custom_color), self, tr('settings.appearance.accent')
            )
            if not picked.isValid():
                if self._selected_name is not None:
                    self._buttons[self._selected_name].setChecked(True)
                return
            color = picked.name()
            self.set_accent(color)
            self.accent_changed.emit(color)
            return
        self.set_accent(name)
        self.accent_changed.emit(name)

    def set_custom_color(self, color: str) -> None:
        resolved = accent_name(color)
        if not resolved.startswith('#'):
            return
        self._custom_color = resolved
        self._buttons[CUSTOM_ACCENT].set_color(QColor(resolved))

    def set_accent(self, name: str) -> None:
        resolved = accent_name(name)
        if resolved.startswith('#'):
            self.set_custom_color(resolved)
        selected = CUSTOM_ACCENT if resolved.startswith('#') else resolved
        self._buttons[selected].setChecked(True)
        self._selected_name = selected
        self._accent = resolved

    def accent(self) -> str:
        return self._accent


class ThemeCard(QAbstractButton):
    """A checkable preview of a theme, painted in that theme's own colors."""

    def __init__(self, mode: ColorMode, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode: ColorMode = mode
        self._colors: ThemeColors | None = None
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumSize(_CARD_MIN_WIDTH, _CARD_HEIGHT)
        self.setObjectName(f'Theme{mode}')
        self.setText(choice_label(mode))
        self.setAccessibleName(choice_label(mode))
        self.refresh_colors()

    def refresh_colors(self) -> None:
        self._colors = ThemeManager.theme_colors(self.mode)
        self.update()

    def colors(self) -> ThemeColors:
        if self._colors is None:
            self._colors = ThemeManager.theme_colors(self.mode)
        return self._colors

    @override
    def sizeHint(self) -> QSize:
        return QSize(_CARD_TARGET_WIDTH, _CARD_HEIGHT)

    @override
    def paintEvent(self, e: QPaintEvent, /) -> None:
        colors = self.colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        outline = QColor(palette.color(QPalette.ColorRole.WindowText))
        if self.isChecked():
            width, outline = 2.0, QColor(palette.color(QPalette.ColorRole.Accent))
        else:
            width = 1.0
            outline.setAlpha(120 if self.underMouse() else 55)
        rect = QRectF(self.rect()).adjusted(width / 2, width / 2, -width / 2, -width / 2)
        painter.setPen(QPen(outline, width))
        painter.setBrush(colors.window)
        painter.drawRoundedRect(rect, _CARD_RADIUS, _CARD_RADIUS)
        if self.hasFocus():
            focus = mix(colors.window, colors.text, 0.55)
            painter.setPen(QPen(focus, 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), _CARD_RADIUS - 2, _CARD_RADIUS - 2)
        painter.setPen(colors.text)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()


class ThemeSelector(QWidget):
    """Theme cards in a grid that wraps between one and three columns."""

    theme_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMaximumWidth(
            _CARD_MAX_COLUMNS * _CARD_MAX_WIDTH + (_CARD_MAX_COLUMNS - 1) * _CARD_GAP
        )
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._cards: dict[str, ThemeCard] = {}
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(_CARD_GAP)
        self._columns = 0
        for mode in COLOR_MODES:
            card = ThemeCard(mode, self)
            self._group.addButton(card)
            self._cards[mode] = card
        self._place(_CARD_MAX_COLUMNS)
        self._group.buttonClicked.connect(self._selected)
        ThemeManager.events().changed.connect(self.refresh_colors)

    def card(self, mode: str) -> ThemeCard:
        return self._cards[mode]

    def refresh_colors(self) -> None:
        for card in self._cards.values():
            card.refresh_colors()

    def set_theme(self, mode: str) -> None:
        if card := self._cards.get(mode):
            card.setChecked(True)

    def theme(self) -> str | None:
        checked = self._group.checkedButton()
        return checked.mode if isinstance(checked, ThemeCard) else None

    def _selected(self, button: QAbstractButton) -> None:
        if isinstance(button, ThemeCard):
            self.theme_changed.emit(button.mode)

    def _place(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for card in self._cards.values():
            self._layout.removeWidget(card)
        for index, card in enumerate(self._cards.values()):
            self._layout.addWidget(card, index // columns, index % columns)
        for column in range(_CARD_MAX_COLUMNS):
            self._layout.setColumnStretch(column, 1 if column < columns else 0)

    @override
    def resizeEvent(self, event: QResizeEvent, /) -> None:
        super().resizeEvent(event)
        fit = (event.size().width() + _CARD_GAP) // _CARD_TARGET_WIDTH
        self._place(max(1, min(_CARD_MAX_COLUMNS, fit)))
