"""Glass buttons resolve overlapping states in a fixed order."""

from __future__ import annotations

from PySide6.QtWidgets import QStyle

from fleasion.gui.glass.primitives import _button_colors  # pyright: ignore[reportPrivateUsage]
from fleasion.gui.glass.style import glass_colors

_State = QStyle.StateFlag
_ENABLED = _State.State_Enabled


def test_disabled_wins_over_default_and_pressed() -> None:
    colors = glass_colors()
    state = _State.State_Sunken | _State.State_HasFocus
    fill, _side, _top = _button_colors(colors, colors.accent, state, flat=False, default=True)
    assert fill is not None
    assert fill.alphaF() < 0.05


def test_default_stays_accent_while_pressed() -> None:
    colors = glass_colors()
    fill, _side, _top = _button_colors(
        colors, colors.accent, _ENABLED | _State.State_Sunken, flat=False, default=True
    )
    assert fill == colors.accent


def test_focus_rings_the_border_in_accent() -> None:
    colors = glass_colors()
    _fill, side, top = _button_colors(colors, colors.accent, _ENABLED | _State.State_HasFocus, flat=False, default=False)
    assert side is not None
    assert top == side
    assert side.rgb() == colors.accent.rgb()


def test_flat_buttons_show_nothing_until_hovered_or_pressed() -> None:
    colors = glass_colors()
    assert _button_colors(colors, colors.accent, _ENABLED, flat=True, default=False) == (None, None, None)

    hovered, _side, _top = _button_colors(
        colors, colors.accent, _ENABLED | _State.State_MouseOver, flat=True, default=False
    )
    assert hovered is not None

    pressed, side, _top = _button_colors(
        colors, colors.accent, _ENABLED | _State.State_Sunken, flat=True, default=False
    )
    assert pressed is not None
    assert side is not None
