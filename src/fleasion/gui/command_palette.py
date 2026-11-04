"""Ctrl+K command palette: fuzzy search over commands a window provides."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, override

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsDropShadowEffect,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from fleasion.localization import tr

from . import glass
from .theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Callable

_SHORTCUTS: Final = ('Ctrl+K', 'Ctrl+P')
_MAX_RESULTS: Final = 60
_WORD_BOUNDARIES: Final = frozenset(' _-./')
_STREAK_BONUS: Final = 3
_WORD_START_BONUS: Final = 6
_LENGTH_PENALTY_EVERY: Final = 12
_FADE_MS: Final = 140
_TAB_FADE_MS: Final = 180
_SHADOW_MARGIN: Final = 24
# Used when the Glass theme (which styles the card itself) is not active
_FALLBACK_STYLE: Final = """
#PaletteCard { background: palette(window); border-radius: 16px;
               border: 1px solid palette(mid); }
#PaletteCard QWidget { color: palette(window-text); }
#PaletteCard QLineEdit { font-size: 13pt; padding: 9px 12px; border-radius: 10px; }
#PaletteCard QLineEdit { background: palette(base); selection-background-color: palette(highlight);
                       selection-color: palette(highlighted-text); }
#PaletteCard QListWidget { background: transparent; border: 0; }
#PaletteCard QListWidget::item { padding: 8px 10px; border-radius: 8px; }
#PaletteCard QListWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }
#PaletteHint { font-size: 8.5pt; }
"""


@dataclass(frozen=True)
class Command:
    title: str
    kind: str
    run: Callable[[], object]
    hint: str = ''

    @property
    def search_text(self) -> str:
        return f'{self.kind} {self.title}'

    @property
    def display_text(self) -> str:
        suffix = f'    {self.hint}' if self.hint else ''
        return f'{self.kind}  \u203a  {self.title}{suffix}'


def fuzzy_score(query: str, text: str) -> int | None:
    """Subsequence match; consecutive and word-start hits score higher."""
    if not query:
        return 0
    haystack = text.lower()
    score, position, streak = 0, -1, 0
    for char in query.lower():
        found = haystack.find(char, position + 1)
        if found < 0:
            return None
        streak = streak + 1 if found == position + 1 else 0
        at_word_start = found == 0 or haystack[found - 1] in _WORD_BOUNDARIES
        score += 1 + streak * _STREAK_BONUS + (_WORD_START_BONUS if at_word_start else 0)
        position = found
    return score - len(haystack) // _LENGTH_PENALTY_EVERY


def rank(commands: list[Command], query: str) -> list[Command]:
    scored = [
        (score, command)
        for command in commands
        if (score := fuzzy_score(query, command.search_text)) is not None
    ]
    scored.sort(key=lambda pair: -pair[0])
    return [command for _, command in scored[:_MAX_RESULTS]]


class CommandPalette(QDialog):
    def __init__(self, parent: QWidget, commands: Callable[[], list[Command]]) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup)
        self._provider = commands
        self._commands: list[Command] = []
        self._fade: QPropertyAnimation | None = None
        self.setObjectName('CommandPalette')
        # The card is the visible surface; Glass must not paint the popup itself
        self.setProperty('glassOwnSurface', True)  # ruff: ignore[boolean-positional-value-in-call] - Qt API
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        card = QWidget(self)
        self._card = card
        card.setObjectName('PaletteCard')
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('ui.gui.command_palette.placeholder'))
        self.search.textChanged.connect(self._refresh)
        self.search.installEventFilter(self)
        self.results = QListWidget()
        self.results.itemActivated.connect(self._run_item)
        hint = QLabel(tr('ui.gui.command_palette.hint'))
        hint.setObjectName('PaletteHint')

        inner = QVBoxLayout(card)
        inner.setContentsMargins(14, 14, 14, 10)
        inner.addWidget(self.search)
        inner.addWidget(self.results)
        inner.addWidget(hint)

        outer = QVBoxLayout(self)
        self._outer = outer
        outer.addWidget(card)
        self._refresh_theme()
        ThemeManager.events().changed.connect(self._refresh_theme)

    def _refresh_theme(self) -> None:
        active = glass.theme.active
        stylesheet = '' if active else _FALLBACK_STYLE
        self.setStyleSheet(stylesheet)
        # Compositor blur covers the popup rect; Glass omits the shadow margin
        margin = 0 if active else _SHADOW_MARGIN
        self._outer.setContentsMargins(margin, margin, margin, margin)
        if active:
            # Qt accepts a null effect to remove it; the PySide stub omits None
            self._card.setGraphicsEffect(None)  # pyright: ignore[reportArgumentType]
        elif self._card.graphicsEffect() is None:
            shadow = QGraphicsDropShadowEffect(self._card)
            shadow.setBlurRadius(40)
            shadow.setOffset(0, 10)
            shadow.setColor(QColor(0, 0, 0, 160))
            self._card.setGraphicsEffect(shadow)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self.search
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
        ):
            key = event.key()
            if key in {Qt.Key.Key_Down, Qt.Key.Key_Up}:
                step = 1 if key == Qt.Key.Key_Down else -1
                row = self.results.currentRow() + step
                self.results.setCurrentRow(max(0, min(row, self.results.count() - 1)))
                return True
            if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                self._run_item(self.results.currentItem())
                return True
        return super().eventFilter(watched, event)

    def popup(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self._refresh_theme()
        self._commands = self._provider()
        width = min(640, max(420, parent.width() - 80))
        self.resize(width, 460)
        center = parent.mapToGlobal(parent.rect().center())
        top = parent.mapToGlobal(parent.rect().topLeft()).y() + 70
        self.move(center.x() - width // 2, top)
        self.search.clear()
        self._refresh('')
        self.setWindowOpacity(0.0)
        self.show()
        self.search.setFocus()
        self._fade = QPropertyAnimation(self, b'windowOpacity', self)
        self._fade.setDuration(_FADE_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def _refresh(self, text: str) -> None:
        self.results.clear()
        for command in rank(self._commands, text):
            item = QListWidgetItem(command.display_text)
            item.setData(Qt.ItemDataRole.UserRole, command)
            self.results.addItem(item)
        self.results.setCurrentRow(0)

    def _run_item(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        command = item.data(Qt.ItemDataRole.UserRole)
        self.close()
        if isinstance(command, Command):
            command.run()


class _TabFade(QWidget):
    """Fade a complete snapshot of the incoming page over its live contents."""

    finished = Signal(object)

    def __init__(self, tabs: QTabWidget, page: QWidget) -> None:
        super().__init__(tabs)
        self.setObjectName('TabContentFade')
        self._page = page
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # Capture through the top-level window, not page.grab(). Glass paints
        # its backdrop on the window, so a page-only grab retains transparency
        # and produces a visible jump when the temporary frame disappears
        window = page.window()
        frame_rect = QRect(page.mapTo(window, QPoint()), page.size())
        self._frame: QPixmap = window.grab(frame_rect)
        self._opacity = 0.0
        self._finished = False
        self.setGeometry(QRect(page.mapTo(tabs, QPoint()), page.size()))
        self._animation = QPropertyAnimation(self, b'opacity', self)
        self._animation.setDuration(_TAB_FADE_MS)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self.cancel)
        page.installEventFilter(self)
        self._hiding_page = True
        page.hide()
        self._hiding_page = False
        self.show()
        self.raise_()
        self._animation.start()

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self._page
            and not self._hiding_page
            and event.type()
            in {
                QEvent.Type.Hide,
                QEvent.Type.Move,
                QEvent.Type.Resize,
            }
        ):
            # A cached image no longer matches a moved or resized page
            self.cancel()
        return False

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, value: float) -> None:
        self._opacity = value
        self.update()

    opacity = Property(float, _get_opacity, _set_opacity)

    @override
    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setOpacity(self._opacity)
        painter.drawPixmap(0, 0, self._frame)
        painter.end()

    def cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._animation.stop()
        parent = self.parentWidget()
        if (
            isValid(self._page)
            and isinstance(parent, QTabWidget)
            and parent.currentWidget() is self._page
        ):
            self._page.show()
        self.hide()
        self.finished.emit(self)
        self.deleteLater()


class _TabTransitionController(QObject):
    """Cancel in-flight frames on tab and appearance changes."""

    def __init__(self, tabs: QTabWidget) -> None:
        super().__init__(tabs)
        self._tabs = tabs
        self._active: _TabFade | None = None
        tabs.installEventFilter(self)
        tabs.currentChanged.connect(self._on_change)
        ThemeManager.events().changed.connect(self.cancel)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._tabs and event.type() == QEvent.Type.Resize:
            self.cancel()
        return False

    def _on_change(self, index: int) -> None:
        self.cancel()
        page = self._tabs.widget(index)
        if page is not None and page.isVisible() and page.width() > 0 and page.height() > 0:
            fade = _TabFade(self._tabs, page)
            fade.finished.connect(self._on_finished)
            self._active = fade

    def _on_finished(self, fade: object) -> None:
        if self._active is fade:
            self._active = None

    def cancel(self) -> None:
        if self._active is not None and isValid(self._active):
            self._active.cancel()
        self._active = None


def fade_tabs(tabs: QTabWidget) -> None:
    """Fade in each new tab as one complete frame, including item viewports."""
    _TabTransitionController(tabs)


def install(window: QWidget, commands: Callable[[], list[Command]]) -> CommandPalette:
    """Open the palette with Ctrl+K or Ctrl+P inside ``window``."""
    palette = CommandPalette(window, commands)
    for sequence in _SHORTCUTS:
        QShortcut(QKeySequence(sequence), window).activated.connect(palette.popup)
    return palette
