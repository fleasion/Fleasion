"""Glass colours, palette and the style sheet that remains for a few widgets."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, sqrt
from string import Template
from typing import TYPE_CHECKING, Final

from PySide6.QtGui import QColor, QPalette

from fleasion.appearance.colors import accent_color, apply_accent, on_accent

from .state import state

if TYPE_CHECKING:
    from .state import GlassSettings


# Text on Glass's own dark surface
TEXT: Final = '#f5f7fb'

# Corner radius of windows, group boxes and item views
RADIUS: Final = 14

# Item-view scrollbars sit 3px in from the rim (frame plus margin); their ends stop where
# the rounded corner has curved that far inward, plus room for the rim and some air
_SCROLLBAR_EDGE: Final = 3
SCROLLBAR_CORNER_INSET: Final = ceil(RADIUS - sqrt(RADIUS**2 - (RADIUS - _SCROLLBAR_EDGE) ** 2)) + 3


#
# Colours
#


@dataclass(frozen=True)
class GlassColors:
    """The resolved colours one Glass appearance paints with."""

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

    # Glass's own Dark and Light surfaces
    surface = QColor('#101118' if dark else '#f4f5f8')
    text = QColor(TEXT if dark else '#20232b')
    well = QColor('#08090e' if dark else '#ffffff')
    popup = QColor('#1c1d26' if dark else '#f7f8fb')

    # Other colour themes tint the glass with their own palette
    if native is not None:
        surface = native.window().color()
        text = native.windowText().color()
        well = native.base().color()
        popup = QColor(well)

    accent = accent_color(
        settings.accent,
        surface,
        system_accent=state.system_accent,
        theme_accent=state.theme_accent,
    )

    return GlassColors(
        text,
        _ink(surface, dark=dark),
        surface,
        well,
        popup,
        accent,
        on_accent(accent),
    )


# Enough of the surface hue to keep overlays from greying a tinted theme
_INK_SATURATION: Final = 28


def _ink(surface: QColor, *, dark: bool) -> QColor:
    """Highlight colour for panes and rims; pure white greys out tinted dark surfaces."""
    if not dark:
        return QColor('#1b2030')

    # Achromatic surfaces report hue -1 and stay white
    return QColor.fromHsv(
        max(surface.hsvHue(), 0), min(surface.hsvSaturation(), _INK_SATURATION), 255
    )


#
# Palette and stylesheet
#


def _rgb(hex_color: str) -> str:
    """An `r,g,b` triple for use inside QSS `rgba()`."""
    color = QColor(hex_color)
    return f'{color.red()},{color.green()},{color.blue()}'


def palette(settings: GlassSettings, *, base_palette: QPalette | None = None) -> QPalette:
    colors = glass_colors(settings)
    base = base_palette if base_palette is not None else state.base_palette
    pal = QPalette(base) if base is not None else QPalette()
    role = QPalette.ColorRole

    # Foregrounds, with a faded disabled variant of each
    for color_role in (role.WindowText, role.Text, role.ButtonText, role.ToolTipText):
        foreground = (
            state.base_palette.color(color_role) if state.base_palette is not None else colors.text
        )
        pal.setColor(color_role, foreground)
        disabled = QColor(foreground)
        disabled.setAlpha(100)
        pal.setColor(QPalette.ColorGroup.Disabled, color_role, disabled)

    # Backgrounds
    pal.setColor(role.Window, colors.surface)
    pal.setColor(role.Base, colors.well)
    pal.setColor(role.AlternateBase, colors.surface)
    pal.setColor(role.ToolTipBase, colors.popup)
    pal.setColor(role.Button, colors.surface)

    placeholder = QColor(colors.text)
    placeholder.setAlpha(128)
    pal.setColor(role.PlaceholderText, placeholder)

    pal = apply_accent(
        pal, settings.accent, system_accent=state.system_accent, theme_accent=state.theme_accent
    )

    # GlassWindows paints the surface; inherited widget backgrounds stay transparent
    window = QColor(colors.surface)
    window.setAlpha(0)
    pal.setColor(role.Window, window)
    return pal


# Panes are flat: per-control gradients stack inside nested panes and read as harsh.
# Rim: a slightly brighter top edge. Well: a recessed input field
_PANE: Final = 'rgba($ink_rgb,0.06)'
_PANE_HOVER: Final = 'rgba($ink_rgb,0.11)'
_RIM: Final = 'border: 1px solid rgba($ink_rgb,0.08); border-top-color: rgba($ink_rgb,0.13);'
_WELL: Final = 'background: rgba($well_rgb,0.28); border: 1px solid rgba($ink_rgb,0.06);'


_STYLESHEET: Final = Template("""
/* QScrollArea makes its content fill its own background; the glass shows through
   instead. Naming the viewport keeps scroll bars, in a sibling container, with the style */
QScrollArea, QScrollArea > QWidget#qt_scrollarea_viewport > QWidget { background: transparent; }
QComboBoxPrivateContainer { background: transparent; border: 0; }



/* Text edits keep style-sheet wells until item views move to the style */
QTextEdit, QPlainTextEdit {
    color: $input_text;
    $well border-radius: 8px; padding: 4px 8px;
    selection-background-color: $accent; selection-color: $accent_text;
}
QTextEdit:focus, QPlainTextEdit:focus { border: 1px solid rgba($accent_rgb,0.85); }
/* Matches QMenu; the item-view effect paints the rim and the menu-item rows pad themselves */
QComboBox QAbstractItemView {
    background: rgba($popup_rgb,0.97); border: 1px solid transparent;
    border-radius: 12px; padding: 0;
}


""")


def stylesheet(settings: GlassSettings) -> str:
    colors = glass_colors(settings)
    native = state.base_palette

    # Colours, substituted into the fragments and the stylesheet alike
    values = {
        'text': colors.text.name(),
        'input_text': native.text().color().name() if native is not None else colors.text.name(),
        'muted': f'rgba({_rgb(colors.text.name())},0.62)',
        'ink_rgb': _rgb(colors.ink.name()),
        'well_rgb': _rgb(colors.well.name()),
        'popup_rgb': _rgb(colors.popup.name()),
        'accent_rgb': _rgb(colors.accent.name()),
        'accent': colors.accent.name(),
        'accent_text': colors.accent_text.name(),
    }

    return _STYLESHEET.substitute(
        **values,
        radius=RADIUS,
        pane=Template(_PANE).substitute(values),
        pane_hover=Template(_PANE_HOVER).substitute(values),
        rim=Template(_RIM).substitute(values),
        well=Template(_WELL).substitute(values),
    )
