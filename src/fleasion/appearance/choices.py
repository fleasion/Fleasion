"""Widget style choices."""

from __future__ import annotations

from typing import Final, Literal

type AppearanceStyle = Literal['Fusion', 'Glass']

STYLES: Final[tuple[AppearanceStyle, ...]] = ('Fusion', 'Glass')


def appearance_style(value: object) -> AppearanceStyle:
    return 'Glass' if value == 'Glass' else 'Fusion'
