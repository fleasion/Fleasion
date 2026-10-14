"""Palette roles and contrast-aware accent colors for both styles."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

from . import ACCENTS, accent_name


def luminance(color: QColor) -> float:
    channels = (color.redF(), color.greenF(), color.blueF())
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return sum(
        value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
    )


def contrast(first: QColor, second: QColor) -> float:
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def on_accent(color: QColor) -> QColor:
    dark, light = QColor('#0c0d12'), QColor('#ffffff')
    return dark if contrast(color, dark) >= contrast(color, light) else light


def accent_color(name: str, background: QColor) -> QColor:
    """Keep the preset hue while making controls distinguishable from their surface."""
    color = QColor(ACCENTS[accent_name(name)])
    dark_surface = luminance(background) < 0.3
    for _ in range(40):
        if contrast(color, background) >= 3.0:
            break
        color = color.lighter(108) if dark_surface else color.darker(108)
    return color


def apply_accent(palette: QPalette, name: str) -> QPalette:
    result = QPalette(palette)
    accent = accent_color(name, result.window().color())
    link = QColor(accent)
    for _ in range(40):
        if (
            min(contrast(link, result.window().color()), contrast(link, result.base().color()))
            >= 4.5
        ):
            break
        link = link.lighter(108) if luminance(result.window().color()) < 0.3 else link.darker(108)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        result.setColor(group, QPalette.ColorRole.Highlight, accent)
        result.setColor(group, QPalette.ColorRole.Accent, accent)
        result.setColor(group, QPalette.ColorRole.HighlightedText, on_accent(accent))
        result.setColor(group, QPalette.ColorRole.Link, link)
        result.setColor(group, QPalette.ColorRole.LinkVisited, link)
    return result
