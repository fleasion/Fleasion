"""Spacing shared by native and Glass widgets."""

from __future__ import annotations

from typing import Final

GAP: Final = 6
PANEL_PADDING: Final = 12
PANEL_MARGINS: Final = (PANEL_PADDING,) * 4
CONTROL_PADDING: Final = 4
CONTROL_HEIGHT: Final = 28

# Glass needs breathing room around its rounded panes; the original Fusion
# dashboard and Scraper use edge-to-edge section borders
GLASS_PAGE_INSET: Final = 8


def page_content_margins(*, glass: bool) -> tuple[int, int, int, int]:
    if glass:
        return (GLASS_PAGE_INSET, 0, GLASS_PAGE_INSET, 4)
    return (0, 0, 0, 0)
