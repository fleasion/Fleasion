"""Ctrl+K command palette: fuzzy search over commands a window provides."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final, override

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from fleasion.localization import tr

from . import glass

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
#PaletteCard { background: rgba(28,29,38,0.97); border-radius: 16px;
               border: 1px solid rgba(255,255,255,0.18); }
#PaletteCard QWidget { color: #f5f7fb; }
#PaletteCard QLineEdit { font-size: 13pt; padding: 9px 12px; border-radius: 10px; }
#PaletteCard QLineEdit { background: #171820; selection-background-color: #405a80; }
#PaletteCard QListWidget { background: transparent; border: 0; }
#PaletteCard QListWidget::item { padding: 8px 10px; border-radius: 8px; }
#PaletteCard QListWidget::item:selected { background: #405a80; color: #f5f7fb; }
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

    def _refresh_theme(self) -> None:
        active = glass.theme.active
        self.setStyleSheet('' if active else _FALLBACK_STYLE)
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


def fade_tabs(tabs: QTabWidget) -> None:
    """Fade the incoming page in when the tab changes."""

    def on_change(index: int) -> None:
        page = tabs.widget(index)
        if page is None:
            return
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b'opacity', page)
        animation.setDuration(_TAB_FADE_MS)
        animation.setStartValue(0.25)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Graphics effects slow down large tables, so switch it off once faded in
        animation.finished.connect(partial(effect.setEnabled, False))  # ruff: ignore[boolean-positional-value-in-call] - Qt API
        animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    tabs.currentChanged.connect(on_change)


def install(window: QWidget, commands: Callable[[], list[Command]]) -> CommandPalette:
    """Open the palette with Ctrl+K or Ctrl+P inside ``window``."""
    palette = CommandPalette(window, commands)
    for sequence in _SHORTCUTS:
        QShortcut(QKeySequence(sequence), window).activated.connect(palette.popup)
    return palette
