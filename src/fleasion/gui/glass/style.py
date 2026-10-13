"""Glass palette, stylesheet, font and the SVG glyphs the stylesheet uses."""

from __future__ import annotations

from string import Template
from typing import TYPE_CHECKING, Final

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

from fleasion.utils.paths import CONFIG_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from .state import GlassSettings

TEXT: Final = '#f5f7fb'
MUTED: Final = 'rgba(245,247,251,0.62)'
RADIUS: Final = 14
ICON_DIR: Final[Path] = CONFIG_DIR / 'glass'

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
_PREFERRED_FONTS: Final = (
    'Inter',
    'Adwaita Sans',
    'Geist',
    'Segoe UI Variable Text',
    'SF Pro Text',
    'Segoe UI',
)


def icon_path(name: str) -> str:
    """Return the file for a glyph; QSS can only load images from files."""
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    path = ICON_DIR / f'{name}.svg'
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
        f'{_ICONS[name]}</svg>'
    )
    if not path.is_file() or path.read_text(encoding='utf-8') != svg:
        path.write_text(svg, encoding='utf-8')
    return path.as_posix()


def _rgb(hex_color: str) -> str:
    color = QColor(hex_color)
    return f'{color.red()},{color.green()},{color.blue()}'


def palette(settings: GlassSettings) -> QPalette:
    pal = QPalette()
    role = QPalette.ColorRole
    text = QColor(TEXT)
    accent = QColor(settings.accent_color)
    colors = {
        role.Window: QColor(0, 0, 0, 0),
        role.WindowText: text,
        role.Base: QColor(10, 11, 16, 120),
        role.AlternateBase: QColor(255, 255, 255, 8),
        role.ToolTipBase: QColor(28, 29, 38),
        role.ToolTipText: text,
        role.Text: text,
        role.Button: QColor(255, 255, 255, 20),
        role.ButtonText: text,
        role.BrightText: QColor('#ffffff'),
        role.Link: accent,
        role.Highlight: accent,
        role.HighlightedText: QColor('#0c0d12'),
        role.PlaceholderText: QColor(245, 247, 251, 110),
        role.Light: QColor(255, 255, 255, 50),
        role.Midlight: QColor(255, 255, 255, 26),
        role.Mid: QColor(255, 255, 255, 20),
        role.Dark: QColor(0, 0, 0, 110),
        role.Shadow: QColor(0, 0, 0, 150),
    }
    for color_role, color in colors.items():
        pal.setColor(color_role, color)
    for color_role in (role.Text, role.ButtonText, role.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, color_role, QColor(255, 255, 255, 85))
    return pal


def pick_font() -> QFont | None:
    families = set(QFontDatabase.families())
    for name in _PREFERRED_FONTS:
        if name in families:
            font = QFont(name)
            font.setPointSizeF(10)
            font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
            return font
    return None


# Pane: brighter at the top, like light hitting glass. Rim: bright top edge.
# Well: recessed input field with its highlight along the bottom edge.
_PANE: Final = (
    'qlineargradient(x1:0, y1:0, x2:0, y2:1, '
    'stop:0 rgba(255,255,255,0.11), stop:1 rgba(255,255,255,0.045))'
)
_PANE_HOVER: Final = (
    'qlineargradient(x1:0, y1:0, x2:0, y2:1, '
    'stop:0 rgba(255,255,255,0.19), stop:1 rgba(255,255,255,0.08))'
)
_RIM: Final = 'border: 1px solid rgba(255,255,255,0.10); border-top-color: rgba(255,255,255,0.30);'
_WELL: Final = (
    'background: rgba(6,7,11,0.34); border: 1px solid rgba(255,255,255,0.07);'
    ' border-bottom-color: rgba(255,255,255,0.16);'
)

_STYLESHEET: Final = Template("""
* { outline: 0; }
QMainWindow, QDialog { background: transparent; }
QWidget { color: $text; }
QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget, QTabWidget > QWidget,
QSplitter, QStatusBar, QLabel { background: transparent; }

QToolTip {
    background: rgba(30,31,40,0.96); color: $text;
    border: 1px solid rgba(255,255,255,0.14); border-radius: 8px; padding: 6px 9px;
}

#GlassTitleBar { background: transparent; }
#GlassTitle { font-weight: 700; font-size: 10.5pt; letter-spacing: 0.3px; }
QPushButton#GlassWinBtn {
    min-width: 30px; max-width: 30px; min-height: 22px; max-height: 22px; padding: 0;
    border-radius: 11px; background: $pane; $rim
}
QPushButton#GlassWinBtn:hover { background: $pane_hover; }
QPushButton#GlassWinBtn[role="close"]:hover {
    background: rgba(255,90,104,0.85); border-color: rgba(255,255,255,0.35);
}

QTabWidget::pane { border: 0; top: 0; }
QTabWidget::tab-bar { left: 10px; }
QTabBar { qproperty-drawBase: 0; background: transparent; }
QTabBar::tab {
    background: transparent; border: 1px solid transparent; border-radius: 13px;
    padding: 5px 15px; margin: 6px 2px; color: $muted; font-weight: 600;
}
QTabBar::tab:hover { color: $text; background: rgba(255,255,255,0.06); }
QTabBar::tab:selected { color: $text; background: $pane_hover; $rim }

QGroupBox {
    background: $pane; $rim border-radius: ${radius}px;
    margin-top: 24px; padding: 12px 10px 10px 10px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 8px; top: 3px; padding: 0 4px;
    color: $muted; font-size: 9pt; font-weight: 700; letter-spacing: 0.5px;
}

QPushButton, QToolButton {
    background: $pane; $rim border-radius: 14px; padding: 5px 14px; min-height: 18px;
    font-weight: 600;
}
QPushButton[glassArrow="true"] {
    background: transparent; border: 0; padding: 0; color: $muted; font-size: 8pt;
}
QPushButton[glassArrow="true"]:hover { color: $text; background: rgba(255,255,255,0.08); }
QPushButton[compact="true"] { padding: 0; border-radius: 9px; }
QPushButton:hover, QToolButton:hover {
    background: $pane_hover; border-top-color: rgba(255,255,255,0.45);
}
QPushButton:pressed, QToolButton:pressed, QToolButton:checked {
    background: rgba($accent_rgb,0.30); border-color: rgba($accent_rgb,0.55);
    border-top-color: rgba(255,255,255,0.45);
}
QPushButton:default {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba($accent_rgb,0.95), stop:1 rgba($accent_rgb,0.75));
    color: #0c0d12; border: 1px solid rgba(255,255,255,0.55);
}
QPushButton:disabled, QToolButton:disabled {
    color: rgba(255,255,255,0.32); background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.06);
}
QPushButton#ConflictBadge {
    background: rgba(255,190,69,0.16); border: 1px solid rgba(255,190,69,0.50);
    border-top-color: rgba(255,230,170,0.75); color: #ffd98a;
}
QPushButton#ConflictBadge[clean="true"] {
    background: rgba(79,224,166,0.12); border: 1px solid rgba(79,224,166,0.40);
    border-top-color: rgba(190,255,225,0.6); color: #9ff5cf;
}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    $well border-radius: 10px; padding: 5px 10px;
    selection-background-color: rgba($accent_rgb,0.45);
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: rgba(255,255,255,0.14); }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid rgba($accent_rgb,0.85); }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 24px; border: 0; background: transparent;
}
QComboBox::down-arrow { image: url($chevron_down); width: 14px; height: 14px; }
QComboBox QAbstractItemView {
    background: rgba(28,29,38,0.98); border: 1px solid rgba(255,255,255,0.14);
    border-radius: 10px; padding: 4px; selection-background-color: rgba($accent_rgb,0.35);
    selection-color: $text;
}
QSpinBox, QDoubleSpinBox { padding-right: 22px; }
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
    background: rgba(255,255,255,0.10);
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url($chevron_up); width: 11px; height: 11px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url($chevron_down); width: 11px; height: 11px;
}

QAbstractItemView, QTreeView, QTreeWidget, QListView, QListWidget, QTableView, QTableWidget {
    background: rgba(8,9,14,0.30); alternate-background-color: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.08); border-top-color: rgba(255,255,255,0.18);
    border-radius: ${radius}px; selection-background-color: rgba($accent_rgb,0.24);
    selection-color: $text;
}
QTreeView::item, QListView::item, QTableView::item { padding: 6px 4px; border: 0; }
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {
    background: rgba(255,255,255,0.05);
}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {
    background: rgba($accent_rgb,0.24);
}
QHeaderView { background: transparent; }
QHeaderView::section {
    background: transparent; color: $muted; border: 0;
    border-bottom: 1px solid rgba(255,255,255,0.08); padding: 8px;
    font-size: 8.5pt; font-weight: 700;
}
QTableCornerButton::section { background: transparent; border: 0; }

QCheckBox, QRadioButton { spacing: 8px; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; $well }
QCheckBox::indicator { border-radius: 5px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: rgba($accent_rgb,0.8);
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba($accent_rgb,1), stop:1 rgba($accent_rgb,0.78));
    border: 1px solid rgba(255,255,255,0.55);
}
QCheckBox::indicator:checked { image: url($check); }
QRadioButton::indicator:checked { image: url($dot); }

#PaletteCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(40,42,54,0.97), stop:1 rgba(24,25,33,0.97));
    border: 1px solid rgba(255,255,255,0.12); border-top-color: rgba(255,255,255,0.32);
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
    background: rgba(255,255,255,0.20); border-radius: 3px; min-height: 30px; min-width: 30px;
}
QScrollBar::handle:hover { background: rgba(255,255,255,0.36); }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent; border: 0; width: 0; height: 0;
}

QSlider::groove:horizontal { height: 6px; $well border-radius: 3px; }
QSlider::sub-page:horizontal { background: rgba($accent_rgb,0.9); border-radius: 3px; }
QSlider::handle:horizontal {
    width: 18px; height: 18px; margin: -7px 0; border-radius: 9px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #dde2ec);
    border: 1px solid rgba(255,255,255,0.8);
}
QProgressBar { $well border-radius: 5px; text-align: center; height: 10px; color: transparent; }
QProgressBar::chunk { background: rgba($accent_rgb,0.9); border-radius: 4px; }

QMenu {
    background: rgba(28,29,38,0.97); border: 1px solid rgba(255,255,255,0.14);
    border-top-color: rgba(255,255,255,0.28); border-radius: 12px; padding: 5px;
}
QMenu::item { padding: 6px 22px; border-radius: 7px; }
QMenu::item:selected { background: rgba($accent_rgb,0.30); }
QMenu::separator { height: 1px; background: rgba(255,255,255,0.10); margin: 4px 8px; }
QMenuBar { background: transparent; }
QSplitter::handle { background: rgba(255,255,255,0.08); }
""")


def stylesheet(settings: GlassSettings) -> str:
    return _STYLESHEET.substitute(
        text=TEXT,
        muted=MUTED,
        radius=RADIUS,
        pane=_PANE,
        pane_hover=_PANE_HOVER,
        rim=_RIM,
        well=_WELL,
        accent_rgb=_rgb(settings.accent_color),
        chevron_down=icon_path('chevron-down'),
        chevron_up=icon_path('chevron-up'),
        check=icon_path('check'),
        dot=icon_path('dot'),
    )
