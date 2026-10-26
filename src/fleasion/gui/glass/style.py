"""Glass palette, stylesheet and the SVG glyphs the stylesheet uses."""

from __future__ import annotations

from dataclasses import dataclass
from string import Template
from typing import TYPE_CHECKING, Final

from PySide6.QtGui import QColor, QPalette

from fleasion.appearance.colors import accent_color, apply_accent, on_accent
from fleasion.utils.paths import CONFIG_DIR
from fleasion.widgets.spacing import CONTROL_HEIGHT, CONTROL_PADDING

from .state import state

if TYPE_CHECKING:
    from pathlib import Path

    from .state import GlassSettings

TEXT: Final = '#f5f7fb'
MUTED: Final = 'rgba(245,247,251,0.62)'
RADIUS: Final = 14
ICON_DIR: Final[Path] = CONFIG_DIR / 'glass'


@dataclass(frozen=True)
class GlassColors:
    text: QColor
    ink: QColor
    surface: QColor
    well: QColor
    popup: QColor
    accent: QColor
    accent_text: QColor


def glass_colors(settings: GlassSettings | None = None) -> GlassColors:
    settings = settings or state.settings
    native = state.base_palette
    dark = state.dark
    surface = QColor('#101118' if dark else '#f4f5f8')
    text = QColor(TEXT if dark else '#20232b')
    well = QColor('#08090e' if dark else '#ffffff')
    popup = QColor('#1c1d26' if dark else '#f7f8fb')
    if native is not None:
        surface = native.window().color()
        text = native.windowText().color()
        well = native.base().color()
        popup = QColor(well)
    accent = accent_color(settings.accent, surface)
    return GlassColors(
        text,
        QColor('#ffffff' if dark else '#1b2030'),
        surface,
        well,
        popup,
        accent,
        on_accent(accent),
    )


_STROKE: Final = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'
_ICONS: Final[dict[str, str]] = {
    'chevron-down': f'<path d="M4 6l4 4 4-4" {_STROKE} stroke="{TEXT}" stroke-width="1.8"/>',
    'chevron-up': f'<path d="M4 10l4-4 4 4" {_STROKE} stroke="{TEXT}" stroke-width="1.8"/>',
    'check': f'<path d="M3.5 8.5l3 3 6-7" {_STROKE} stroke="#0c0d12" stroke-width="2.2"/>',
    'dot': '<circle cx="8" cy="8" r="3.2" fill="#0c0d12"/>',
    'win-min': f'<path d="M4.5 8h7" {_STROKE} stroke="{TEXT}" stroke-width="1.6"/>',
    'win-max': f'<rect x="4.5" y="4.5" width="7" height="7" rx="1.6" {_STROKE} '
    f'stroke="{TEXT}" stroke-width="1.5"/>',
    'win-close': f'<path d="M5 5l6 6M11 5l-6 6" {_STROKE} stroke="{TEXT}" stroke-width="1.6"/>',
}


def icon_path(name: str) -> str:
    """Return the file for a glyph; QSS can only load images from files."""
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    colors = glass_colors()
    foreground = colors.accent_text if name in {'check', 'dot'} else colors.text
    path = ICON_DIR / f'{name}-{foreground.name()[1:]}.svg'
    glyph = _ICONS[name].replace(TEXT, foreground.name()).replace('#0c0d12', foreground.name())
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
        f'{glyph}</svg>'
    )
    if not path.is_file() or path.read_text(encoding='utf-8') != svg:
        path.write_text(svg, encoding='utf-8')
    return path.as_posix()


def _rgb(hex_color: str) -> str:
    color = QColor(hex_color)
    return f'{color.red()},{color.green()},{color.blue()}'


def palette(settings: GlassSettings, *, base_palette: QPalette | None = None) -> QPalette:
    colors = glass_colors(settings)
    base = base_palette if base_palette is not None else state.base_palette
    pal = QPalette(base) if base is not None else QPalette()
    role = QPalette.ColorRole
    for color_role in (role.WindowText, role.Text, role.ButtonText, role.ToolTipText):
        foreground = (
            state.base_palette.color(color_role) if state.base_palette is not None else colors.text
        )
        pal.setColor(color_role, foreground)
        disabled = QColor(foreground)
        disabled.setAlpha(100)
        pal.setColor(QPalette.ColorGroup.Disabled, color_role, disabled)
    pal.setColor(role.Window, colors.surface)
    pal.setColor(role.Base, colors.well)
    pal.setColor(role.AlternateBase, colors.surface)
    pal.setColor(role.ToolTipBase, colors.popup)
    pal.setColor(role.Button, colors.surface)
    placeholder = QColor(colors.text)
    placeholder.setAlpha(128)
    pal.setColor(role.PlaceholderText, placeholder)
    pal = apply_accent(pal, settings.accent)
    # GlassWindows paints the surface; inherited widget backgrounds stay transparent
    window = QColor(colors.surface)
    window.setAlpha(0)
    pal.setColor(role.Window, window)
    return pal


# Pane: brighter at the top, like light hitting glass. Rim: bright top edge.
# Well: recessed input field with its highlight along the bottom edge.
_PANE: Final = (
    'qlineargradient(x1:0, y1:0, x2:0, y2:1, '
    'stop:0 rgba($ink_rgb,0.11), stop:1 rgba($ink_rgb,0.045))'
)
_PANE_HOVER: Final = (
    'qlineargradient(x1:0, y1:0, x2:0, y2:1, '
    'stop:0 rgba($ink_rgb,0.19), stop:1 rgba($ink_rgb,0.08))'
)
_RIM: Final = 'border: 1px solid rgba($ink_rgb,0.10); border-top-color: rgba($ink_rgb,0.30);'
_WELL: Final = (
    'background: rgba($well_rgb,0.34); border: 1px solid rgba($ink_rgb,0.07);'
    ' border-bottom-color: rgba($ink_rgb,0.16);'
)

_STYLESHEET: Final = Template("""
QMainWindow, QDialog { background: transparent; }
QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget, QTabWidget > QWidget,
QSplitter, QStatusBar, QLabel { background: transparent; }
QComboBoxPrivateContainer { background: transparent; border: 0; }

QToolTip {
    background: rgba($popup_rgb,0.96); color: $tooltip_text;
    border: 1px solid rgba($ink_rgb,0.14); border-radius: 8px; padding: 6px 9px;
}

#GlassTitleBar { background: transparent; }
#GlassTitle { font-weight: 700; font-size: 10.5pt; letter-spacing: 0.3px; }
QPushButton#GlassWinBtn {
    min-width: 30px; max-width: 30px; min-height: 22px; max-height: 22px; padding: 0;
    border-radius: 11px; background: $pane; $rim
}
QPushButton#GlassWinBtn:hover { background: $pane_hover; }
QPushButton#GlassWinBtn[role="close"]:hover {
    background: rgba(255,90,104,0.85); border-color: rgba($ink_rgb,0.35);
}

QTabWidget::pane { border: 0; top: 0; }
QTabBar { qproperty-drawBase: 0; background: transparent; }
QTabBar::tab {
    background: transparent; border: 1px solid transparent; border-radius: 8px;
    padding: 4px 11px; margin: 0; color: $muted;
}
QTabBar::tab:hover { color: $text; background: rgba($ink_rgb,0.06); }
QTabBar::tab:selected { color: $text; background: $pane_hover; $rim }

QFrame#SubplaceSearchPanel {
    background: rgba($ink_rgb,0.065); border: 1px solid rgba($ink_rgb,0.12);
    border-top-color: rgba($ink_rgb,0.23); border-radius: 12px;
}
QScrollArea#SubplaceRecentScroll, QScrollArea#SubplaceFavoritesScroll,
QScrollArea#SubplaceResultsScroll {
    background: rgba($well_rgb,0.26); border: 1px solid rgba($ink_rgb,0.09);
    border-radius: 10px;
}
QFrame#SubplaceGameCard {
    background: rgba($ink_rgb,0.075); border: 1px solid rgba($ink_rgb,0.17);
    border-top-color: rgba($ink_rgb,0.30); border-radius: 12px;
}
QFrame#SubplaceGameCard:hover {
    background: rgba($ink_rgb,0.11); border-color: rgba($ink_rgb,0.30);
}
QPushButton#SubplaceSidebarButton {
    padding: 4px 10px; border-radius: 8px;
}
QPushButton#SubplaceSidebarButton:hover {
    background: rgba($ink_rgb,0.16);
}
QWidget#SubplaceFooter { background: transparent; border: 0; }
QGroupBox {
    background: $pane; $rim border-radius: ${radius}px;
    margin-top: 1.4em; padding: 0;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 8px; top: 3px; padding: 0 4px;
    color: $muted;
}

QPushButton, QToolButton {
    color: $button_text;
    background: $pane; $rim border-radius: 8px;
    padding: ${control_padding}px 10px; min-height: ${control_content_height}px;
}
QPushButton { min-width: 58px; }
QPushButton[glassArrow="true"] {
    background: transparent; border: 0; padding: 0; min-width: 0; color: $muted;
}
QPushButton[glassArrow="true"]:hover { color: $text; background: rgba($ink_rgb,0.08); }
QPushButton[compact="true"] { padding: 0; min-width: 0; border-radius: 9px; }
QPushButton:hover, QToolButton:hover {
    background: $pane_hover; border-top-color: rgba($ink_rgb,0.45);
}
QPushButton:focus, QToolButton:focus {
    border-color: rgba($accent_rgb,0.95);
}
QTabBar::tab:focus { border-color: rgba($accent_rgb,0.95); }
QCheckBox:focus, QRadioButton:focus {
    outline: 1px dotted rgba($accent_rgb,0.95);
}
QPushButton:pressed, QPushButton:checked, QToolButton:pressed, QToolButton:checked {
    background: rgba($accent_rgb,0.30); border-color: rgba($accent_rgb,0.55);
    border-top-color: rgba($ink_rgb,0.45);
}
QPushButton:default {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $accent, stop:1 $accent);
    color: $accent_text; border: 1px solid rgba($ink_rgb,0.55);
}
QPushButton:disabled, QToolButton:disabled {
    color: rgba($ink_rgb,0.32); background: rgba($ink_rgb,0.03);
    border: 1px solid rgba($ink_rgb,0.06);
}
QPushButton#ConflictBadge {
    background: rgba(255,190,69,0.16); border: 1px solid rgba(255,190,69,0.50);
    border-top-color: rgba(255,230,170,0.75); color: $warning;
}
QPushButton#ConflictBadge[clean="true"] {
    background: rgba(79,224,166,0.12); border: 1px solid rgba(79,224,166,0.40);
    border-top-color: rgba(190,255,225,0.6); color: $success;
}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    color: $input_text;
    $well border-radius: 8px; padding: 4px 8px;
    selection-background-color: $accent; selection-color: $accent_text;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    padding: ${input_padding}px 8px; min-height: ${input_content_height}px;
}
QAbstractSpinBox > QLineEdit, QComboBox > QLineEdit {
    background: transparent; border: 0; border-radius: 0; padding: 0; min-height: 0;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: rgba($ink_rgb,0.14); }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid rgba($accent_rgb,0.85); }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 24px; border: 0; background: transparent;
}
QComboBox::down-arrow { image: url($chevron_down); width: 14px; height: 14px; }
QComboBox QAbstractItemView {
    background: rgba($popup_rgb,0.98); border: 1px solid transparent;
    border-radius: 10px; padding: 4px; selection-background-color: rgba($accent_rgb,0.35);
    selection-color: $text;
}
QSpinBox, QDoubleSpinBox { padding-right: 24px; }
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border; width: 20px; border: 0; background: transparent;
    border-radius: 6px;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-position: top right; margin: 3px 3px 0 0;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-position: bottom right; margin: 0 3px 3px 0;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background: rgba($ink_rgb,0.10);
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url($chevron_up); width: 11px; height: 11px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url($chevron_down); width: 11px; height: 11px;
}

/* The item-view effect paints the rim after clipping child backgrounds */
QAbstractItemView {
    color: $input_text;
    background: rgba($well_rgb,0.30); alternate-background-color: rgba($ink_rgb,0.025);
    border: 1px solid transparent;
    border-radius: ${radius}px; selection-background-color: rgba($accent_rgb,0.24);
    selection-color: $text;
}
/* Preserve all four rounded outer corners; the separate QHeaderView reset
   below prevents duplicate header outlines without flattening the view */
QTreeView::item, QListView::item, QTableView::item { padding: 2px 4px; border: 0; }
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {
    background: rgba($ink_rgb,0.05);
}
QTreeView::item:selected, QListView::item:selected { background: rgba($accent_rgb,0.24); }
QTableView, QTableWidget { selection-background-color: rgba($accent_rgb,0.11); }
QTableView::item:selected { background: rgba($accent_rgb,0.11); }
/* QHeaderView inherits QAbstractItemView; reset the inherited inner rounded frame */
QHeaderView { background: transparent; border: 0; border-radius: 0; }
QHeaderView::section {
    background: rgba($ink_rgb,0.045); color: $muted; border: 0;
    border-bottom: 1px solid rgba($ink_rgb,0.10); padding: 4px;
}
QTableCornerButton::section { background: transparent; border: 0; }

QCheckBox, QRadioButton { spacing: 6px; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; $well }
QCheckBox::indicator { border-radius: 5px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: rgba($accent_rgb,0.8);
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $accent, stop:1 $accent);
    border: 1px solid rgba($ink_rgb,0.55);
}
QCheckBox::indicator:checked { image: url($check); }
QRadioButton::indicator:checked { image: url($dot); }

#PaletteCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba($popup_rgb,0.97), stop:1 rgba($popup_rgb,0.97));
    border: 1px solid rgba($ink_rgb,0.12); border-top-color: rgba($ink_rgb,0.32);
    border-radius: 18px;
}
#PaletteCard QListWidget { background: transparent; border: 0; }
#PaletteCard QListWidget::item { padding: 8px 10px; border-radius: 10px; }
#PaletteCard QListWidget::item:selected { background: rgba($accent_rgb,0.28); color: $text; }
#PaletteCard QLineEdit { font-size: 13pt; padding: 9px 14px; border-radius: 12px; }
#PaletteHint { color: $muted; font-size: 8.5pt; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px 4px; }
QScrollBar::handle {
    background: rgba($ink_rgb,0.20); border-radius: 3px; min-height: 30px; min-width: 30px;
}
QScrollBar::handle:hover { background: rgba($ink_rgb,0.36); }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent; border: 0; width: 0; height: 0;
}

QSlider::groove:horizontal { height: 6px; $well border-radius: 3px; }
QSlider::sub-page:horizontal { background: rgba($accent_rgb,0.9); border-radius: 3px; }
QSlider::handle:horizontal {
    width: 18px; height: 18px; margin: -7px 0; border-radius: 9px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #dde2ec);
    border: 1px solid rgba($ink_rgb,0.8);
}
QProgressBar { $well border-radius: 5px; text-align: center; height: 10px; color: transparent; }
QProgressBar::chunk { background: rgba($accent_rgb,0.9); border-radius: 4px; }

QMenu {
    background: rgba($popup_rgb,0.97); border: 1px solid rgba($ink_rgb,0.14);
    border-top-color: rgba($ink_rgb,0.28); border-radius: 12px; padding: 5px;
}
QMenu::item { padding: 6px 22px; border-radius: 7px; }
QMenu::item:selected { background: rgba($accent_rgb,0.30); }
QMenu::separator { height: 1px; background: rgba($ink_rgb,0.10); margin: 4px 8px; }
QMenuBar { background: transparent; }
QSplitter::handle { background: rgba($ink_rgb,0.08); }
""")


def stylesheet(settings: GlassSettings) -> str:
    colors = glass_colors(settings)
    native = state.base_palette
    values = {
        'text': colors.text.name(),
        'input_text': native.text().color().name() if native is not None else colors.text.name(),
        'button_text': native.buttonText().color().name()
        if native is not None
        else colors.text.name(),
        'tooltip_text': native.toolTipText().color().name()
        if native is not None
        else colors.text.name(),
        'muted': f'rgba({_rgb(colors.text.name())},0.62)',
        'ink_rgb': _rgb(colors.ink.name()),
        'well_rgb': _rgb(colors.well.name()),
        'popup_rgb': _rgb(colors.popup.name()),
        'accent_rgb': _rgb(colors.accent.name()),
        'accent': colors.accent.name(),
        'accent_text': colors.accent_text.name(),
        'warning': '#ffd98a' if state.dark else '#805500',
        'success': '#9ff5cf' if state.dark else '#16613f',
    }
    return _STYLESHEET.substitute(
        **values,
        radius=RADIUS,
        control_padding=CONTROL_PADDING,
        control_content_height=CONTROL_HEIGHT - CONTROL_PADDING * 2 - 2,
        input_padding=CONTROL_PADDING - 1,
        input_content_height=CONTROL_HEIGHT - (CONTROL_PADDING - 1) * 2 - 2,
        pane=Template(_PANE).substitute(values),
        pane_hover=Template(_PANE_HOVER).substitute(values),
        rim=Template(_RIM).substitute(values),
        well=Template(_WELL).substitute(values),
        chevron_down=icon_path('chevron-down'),
        chevron_up=icon_path('chevron-up'),
        check=icon_path('check'),
        dot=icon_path('dot'),
    )
