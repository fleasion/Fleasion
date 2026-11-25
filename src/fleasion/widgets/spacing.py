"""Spacing shared by native and Glass widgets."""

from __future__ import annotations

from typing import Final

GAP: Final = 6
PANEL_PADDING: Final = 12
PANEL_MARGINS: Final = (PANEL_PADDING,) * 4
CONTROL_PADDING: Final = 4
CONTROL_HEIGHT: Final = 28

# Tooltips are small labels: shorter than a control and padded wider than tall
TOOLTIP_PADDING_V: Final = CONTROL_PADDING
TOOLTIP_PADDING_H: Final = CONTROL_PADDING * 2

# Splitter handles leave room on both sides of their grip
SPLITTER_WIDTH: Final = 10

# Glass needs breathing room around its rounded panes; the original Fusion
# dashboard and Scraper use edge-to-edge section borders
GLASS_PAGE_INSET: Final = 8


def page_content_margins(*, glass: bool) -> tuple[int, int, int, int]:
    if glass:
        return (GLASS_PAGE_INSET, 0, GLASS_PAGE_INSET, 4)
    return (0, 0, 0, 0)
