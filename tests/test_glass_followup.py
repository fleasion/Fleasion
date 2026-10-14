"""Regression coverage for compositor status, XFCE wallpaper discovery, and focus styling."""

from __future__ import annotations

import subprocess
from threading import Event, current_thread, main_thread
from typing import TYPE_CHECKING

import pytest

from fleasion.gui.glass import desktop_blur, style, wallpaper
from fleasion.gui.glass.state import GlassSettings

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ('output', 'expected'),
    [
        ('State: ACTIVE', True),
        ('State: INACTIVE', False),
        ('State: ENABLED', False),
        ('State: ERROR', False),
        ('Name: ACTIVE / ENABLED\nState: INACTIVE', False),
        ('Enabled: true\nState: INITIALIZED', False),
        ('State:    active ', True),
        ('', False),
    ],
)
def test_extension_requires_active_state(
    monkeypatch: pytest.MonkeyPatch, output: str, expected: bool
) -> None:
    monkeypatch.setattr(desktop_blur.sys, 'platform', 'linux')
    def fake_run(*_args: str) -> str:
        return output

    monkeypatch.setattr(desktop_blur, '_run', fake_run)
    assert desktop_blur._extension_active() is expected  # pyright: ignore[reportPrivateUsage]


def test_xfce_prefers_active_workspace_and_desktop_over_stale_gnome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / 'current workspace.png'
    first = tmp_path / 'workspace0.png'
    gnome = tmp_path / 'stale_gnome.png'
    for image in (current, first, gnome):
        image.touch()
    monkeypatch.setenv('XDG_CURRENT_DESKTOP', 'XFCE')
    def executable(name: str) -> str:
        return name

    def gsettings(_schema: str, _key: str) -> str:
        return gnome.as_uri()

    monkeypatch.setattr(wallpaper.shutil, 'which', executable)
    monkeypatch.setattr(wallpaper, '_gsettings', gsettings)

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[0] == 'xprop':
            return subprocess.CompletedProcess(args, 0, '_NET_CURRENT_DESKTOP(CARDINAL) = 1\n')
        assert args[:4] == ['xfconf-query', '-c', 'xfce4-desktop', '-lv']
        return subprocess.CompletedProcess(
            args,
            0,
            f'/backdrop/screen0/monitorDP-0/workspace0/last-image  {first}\n'
            f'/backdrop/screen0/monitorDP-0/workspace1/last-image  {current}\n',
        )

    monkeypatch.setattr(wallpaper.subprocess, 'run', run)
    assert wallpaper.wallpaper_path() == current


def test_xfce_wallpaper_fallback_without_workspace_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fallback = tmp_path / 'wallpaper.png'
    fallback.touch()
    monkeypatch.setenv('XDG_CURRENT_DESKTOP', 'XFCE')
    def executable(name: str) -> str | None:
        return None if name == 'xprop' else name

    monkeypatch.setattr(wallpaper.shutil, 'which', executable)

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, f'/backdrop/screen0/monitor0/workspace0/last-image  {fallback}\n'
        )

    monkeypatch.setattr(wallpaper.subprocess, 'run', run)
    assert wallpaper.wallpaper_path() == fallback


def test_desktop_blur_status_refresh_is_nonblocking_and_reacts_to_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = {'blur': 'true', 'enable-all': 'false'}
    clock = [0.0]
    def extension_active() -> bool:
        return True

    def read(key: str) -> str:
        return values.get(key, '')

    def read_list(_key: str) -> list[str]:
        return ['fleasion']

    monkeypatch.setattr(desktop_blur, '_extension_active', extension_active)
    monkeypatch.setattr(desktop_blur, '_read', read)
    monkeypatch.setattr(desktop_blur, '_read_list', read_list)
    monkeypatch.setattr(desktop_blur, '_BACKUP_FILE', tmp_path / 'backup.json')
    def now() -> float:
        return clock[0]

    monkeypatch.setattr(desktop_blur, 'monotonic', now)
    blur = desktop_blur.DesktopBlur()
    try:
        assert blur.cached_status() == 'unavailable'
        assert blur.poll_refresh() is False
        pending = blur._pending  # pyright: ignore[reportPrivateUsage]
        assert pending is not None
        pending.result(timeout=3)
        assert blur.poll_refresh()
        assert blur.cached_status() == 'on'
        values['blur'] = 'false'
        clock[0] = 16.0
        assert blur.poll_refresh() is False
        pending = blur._pending  # pyright: ignore[reportPrivateUsage]
        assert pending is not None
        pending.result(timeout=3)
        assert blur.poll_refresh()
        assert blur.cached_status() == 'off'
    finally:
        blur.stop_polling()


def test_async_blur_probe_does_not_override_explicit_refresh(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    started = Event()
    release = Event()

    def probe() -> tuple[desktop_blur.BlurStatus, bool]:
        if current_thread() is main_thread():
            return 'off', False
        started.set()
        assert release.wait(timeout=3)
        return 'on', True

    monkeypatch.setattr(desktop_blur.DesktopBlur, '_probe', staticmethod(probe))
    blur = desktop_blur.DesktopBlur()
    try:
        assert blur.poll_refresh() is False
        assert started.wait(timeout=3)
        assert blur.status(refresh=True) == 'off'
        release.set()
        pending = blur._pending  # pyright: ignore[reportPrivateUsage]
        assert pending is not None
        pending.result(timeout=3)
        assert blur.poll_refresh() is False
        assert blur.cached_status() == 'off'
    finally:
        release.set()
        blur.stop_polling()


def test_glass_keeps_keyboard_focus_indicators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(style, 'ICON_DIR', tmp_path)
    stylesheet = style.stylesheet(GlassSettings())
    assert '* { outline: 0; }' not in stylesheet
    assert 'QPushButton:focus' in stylesheet
    assert 'QCheckBox:focus' in stylesheet
    assert 'QRadioButton:focus' in stylesheet
    assert 'QTabBar::tab:focus' in stylesheet


def test_settings_blur_toggle_follows_cached_compositor_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtWidgets import QApplication, QPushButton

    from fleasion.gui.glass.controller import BLUR_BUTTON, GlassTheme
    from fleasion.gui.glass.state import state
    from fleasion.gui.theme import ThemeManager
    from fleasion.localization import tr

    instance = QApplication.instance()
    _application = instance if isinstance(instance, QApplication) else QApplication([])
    ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
    button = QPushButton()
    button.setObjectName(BLUR_BUTTON)
    button.show()
    monkeypatch.setattr(state.settings, 'backdrop', 'desktop')
    monkeypatch.setattr(desktop_blur.blur, '_status', 'on')
    monkeypatch.setattr(desktop_blur.blur, '_can_toggle', True)
    try:
        GlassTheme._sync_blur_controls()  # pyright: ignore[reportPrivateUsage]
        assert button.isVisible()
        assert button.isEnabled()
        assert button.text() == tr('settings.appearance.glass_blur_on')
        monkeypatch.setattr(desktop_blur.blur, '_status', 'unavailable')
        GlassTheme._sync_blur_controls()  # pyright: ignore[reportPrivateUsage]
        assert not button.isVisible()
        assert not button.isEnabled()
    finally:
        button.close()
        ThemeManager.apply_theme('Light')
        button.deleteLater()
