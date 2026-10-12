"""Glass theme settings: the values chosen by the user and where they persist."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from ...config.manager import ConfigManager

type Backdrop = Literal['desktop', 'wallpaper']

ACCENTS: Final[dict[str, str]] = {
    'Ice': '#8ab4ff',
    'Signal': '#ff5a68',
    'Mint': '#4fe0a6',
    'Amber': '#ffbe45',
    'Violet': '#b49cff',
    'Mono': '#f2f4f8',
}
BACKDROPS: Final[tuple[Backdrop, ...]] = ('desktop', 'wallpaper')

DEFAULT_ACCENT: Final = 'Ice'
DEFAULT_BACKDROP: Final[Backdrop] = 'desktop'
# Tint strength over a blurred backdrop, and the floor used when nothing blurs
DEFAULT_OPACITY: Final = 0.45
UNBLURRED_OPACITY: Final = 0.86
MIN_OPACITY: Final = 0.2
MAX_OPACITY: Final = 0.97


def clamp_opacity(value: float) -> float:
    return min(MAX_OPACITY, max(MIN_OPACITY, value))


@dataclass
class GlassSettings:
    accent: str = DEFAULT_ACCENT
    opacity: float = DEFAULT_OPACITY
    backdrop: Backdrop = DEFAULT_BACKDROP

    @property
    def accent_color(self) -> str:
        return ACCENTS.get(self.accent, ACCENTS[DEFAULT_ACCENT])


class GlassState:
    """Current settings plus the ConfigManager they are saved through."""

    def __init__(self) -> None:
        self.settings = GlassSettings()
        self._config: ConfigManager | None = None

    def configure(self, config: ConfigManager) -> None:
        self._config = config
        self.reload()

    def reload(self) -> None:
        if self._config is None:
            return
        self.settings = GlassSettings(
            accent=self._config.glass_accent,
            opacity=self._config.glass_opacity,
            backdrop=self._config.glass_backdrop,
        )

    def set_accent(self, name: str) -> None:
        self.settings.accent = name
        if self._config is not None:
            self._config.glass_accent = name

    def set_opacity(self, value: float) -> None:
        self.settings.opacity = clamp_opacity(value)
        if self._config is not None:
            self._config.glass_opacity = self.settings.opacity

    def set_backdrop(self, mode: Backdrop) -> None:
        self.settings.backdrop = mode
        if self._config is not None:
            self._config.glass_backdrop = mode


state = GlassState()
