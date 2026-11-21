"""Glass theme settings: the values chosen by the user and where they persist."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fleasion.appearance import (
    ACCENTS as ACCENTS,
    BACKDROPS as BACKDROPS,
    DEFAULT_ACCENT as DEFAULT_ACCENT,
    DEFAULT_BACKDROP as DEFAULT_BACKDROP,
    DEFAULT_OPACITY as DEFAULT_OPACITY,
    FALLBACK_ACCENT,
    MAX_OPACITY as MAX_OPACITY,
    MIN_OPACITY as MIN_OPACITY,
    Backdrop as Backdrop,
    clamp_opacity,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QColor, QPalette

    from fleasion.config.manager import ConfigManager


@dataclass
class GlassSettings:
    """The user's Glass choices, as currently applied."""

    accent: str = DEFAULT_ACCENT
    opacity: float = DEFAULT_OPACITY
    backdrop: Backdrop = DEFAULT_BACKDROP

    @property
    def accent_color(self) -> str:
        """The accent as a colour string; named accents resolve through ACCENTS."""
        return (
            self.accent
            if self.accent.startswith('#')
            else ACCENTS.get(self.accent, ACCENTS[FALLBACK_ACCENT])
        )


class GlassState:
    """Current settings plus the ConfigManager they are saved through."""

    def __init__(self) -> None:
        self.settings = GlassSettings()

        # Set by ThemeManager on every apply; base_palette is None for plain Dark and Light
        self.base_palette: QPalette | None = None
        self.system_accent: QColor | None = None
        self.theme_accent: QColor | None = None
        self.dark = True

        self._config: ConfigManager | None = None

    def configure(self, config: ConfigManager) -> None:
        self._config = config
        self.reload()

    def reload(self) -> None:
        if self._config is None:
            return

        self.settings = GlassSettings(
            accent=self._config.accent_color,
            opacity=self._config.glass_opacity,
            backdrop=self._config.glass_backdrop,
        )

    def set_accent(self, name: str) -> None:
        self.settings.accent = name
        if self._config is not None:
            self._config.accent_color = name

    def set_opacity(self, value: float) -> None:
        self.settings.opacity = clamp_opacity(value)
        if self._config is not None:
            self._config.glass_opacity = self.settings.opacity

    def set_backdrop(self, mode: Backdrop) -> None:
        self.settings.backdrop = mode
        if self._config is not None:
            self._config.glass_backdrop = mode


state = GlassState()
