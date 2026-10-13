"""Applying and removing the Glass theme, and changing its settings live."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QWidget

from fleasion.localization import tr

from .chrome import apply_chrome, register, registered_windows
from .desktop_blur import APP_ID, blur
from .state import ACCENTS, Backdrop, state
from .style import palette, pick_font, stylesheet
from .surface import GlassWindows

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager

ACCENT_COMBO = 'GlassAccentCombo'
BACKDROP_COMBO = 'GlassBackdropCombo'
BLUR_BUTTON = 'GlassBlurButton'


def backdrop_label(mode: Backdrop) -> str:
    labels: dict[Backdrop, str] = {
        'desktop': tr('settings.appearance.glass_backdrop_desktop'),
        'wallpaper': tr('settings.appearance.glass_backdrop_wallpaper'),
    }
    return labels[mode]


def _app() -> QApplication | None:
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else None


def _refresh_windows(app: QApplication) -> None:
    for widget in app.topLevelWidgets():
        widget.update()


def _sync_combo(
    app: QApplication, name: str, *, text: str | None = None, data: str | None = None
) -> None:
    """Keep every settings combo in step when a value changes elsewhere (e.g. Ctrl+K)."""
    for widget in app.allWidgets():
        if not isinstance(widget, QComboBox) or widget.objectName() != name:
            continue
        index = widget.findText(text) if text is not None else widget.findData(data)
        if index >= 0 and index != widget.currentIndex():
            blocked = widget.blockSignals(True)  # ruff: ignore[boolean-positional-value-in-call] - Qt API
            widget.setCurrentIndex(index)
            widget.blockSignals(blocked)


class GlassTheme:
    """Applies and removes the theme; owns the app-wide event filter."""

    def __init__(self) -> None:
        self._filter: GlassWindows | None = None
        self._previous_font: QFont | None = None

    @property
    def active(self) -> bool:
        return self._filter is not None

    @staticmethod
    def configure(config: ConfigManager) -> None:
        """Read and persist Glass settings through this ConfigManager."""
        state.configure(config)

    def enable(self, app: QApplication) -> None:
        state.reload()
        if not app.desktopFileName():
            # A stable Wayland app ID lets compositor blur rules (and docks) match us
            app.setDesktopFileName(APP_ID)
        app.setStyle('Fusion')
        app.setPalette(palette(state.settings))
        if self._previous_font is None:
            self._previous_font = QFont(app.font())
            if (font := pick_font()) is not None:
                app.setFont(font)
        app.setStyleSheet(stylesheet(state.settings))
        if self._filter is None:
            self._filter = GlassWindows(app)
            self._filter.blur_status_changed.connect(self._sync_blur_controls)
            app.installEventFilter(self._filter)
        for widget in app.allWidgets():
            self._filter.polish(widget)
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()
        for window in registered_windows():
            apply_chrome(window, enabled=True)

    def disable(self, app: QApplication) -> None:
        window_filter = self._filter
        if self._filter is not None:
            app.removeEventFilter(self._filter)
            self._filter = None
        if self._previous_font is not None:
            app.setFont(self._previous_font)
            self._previous_font = None
        app.setStyleSheet('')
        for window in registered_windows():
            apply_chrome(window, enabled=False)
        if window_filter is not None:
            window_filter.restore()
            window_filter.deleteLater()
        _refresh_windows(app)

    def attach(self, window: QWidget) -> None:
        """Let Glass replace this window's title bar while the theme is active."""
        register(window)
        if self.active:
            apply_chrome(window, enabled=True)

    def set_accent(self, name: str) -> None:
        if name not in ACCENTS:
            return
        state.set_accent(name)
        if (app := _app()) is not None:
            _sync_combo(app, ACCENT_COMBO, text=name)
            if self.active:
                self.enable(app)

    @staticmethod
    def set_opacity(value: float) -> None:
        state.set_opacity(value)
        if (app := _app()) is not None:
            _refresh_windows(app)

    @staticmethod
    def _sync_blur_controls() -> None:
        """Refresh the blur toggle when compositor settings change outside Fleasion."""
        app = _app()
        if app is None:
            return
        status = blur.cached_status()
        for widget in app.allWidgets():
            if isinstance(widget, QPushButton) and widget.objectName() == BLUR_BUTTON:
                widget.setVisible(status != 'unavailable' and state.settings.backdrop == 'desktop')
                widget.setEnabled(status != 'unavailable' and blur.can_toggle())
                widget.setText(
                    tr('settings.appearance.glass_blur_on')
                    if status == 'on'
                    else tr('settings.appearance.glass_blur_off')
                )

    def set_backdrop(self, mode: Backdrop) -> None:
        state.set_backdrop(mode)
        if self._filter is not None:
            self._filter.refresh_wallpaper()
        if (app := _app()) is None:
            return
        _sync_combo(app, BACKDROP_COMBO, data=mode)
        self._sync_blur_controls()
        _refresh_windows(app)

    @staticmethod
    def toggle_desktop_blur() -> None:
        blur.status(refresh=True)
        if not blur.can_toggle():
            return
        if blur.owns_entry():
            blur.disable()
        else:
            blur.enable()
        GlassTheme._sync_blur_controls()
        if (app := _app()) is not None:
            _refresh_windows(app)


theme = GlassTheme()
