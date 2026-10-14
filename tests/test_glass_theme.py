"""Glass theme settings, desktop blur round-trip and command palette ranking."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from fleasion.config import manager as manager_module
from fleasion.gui import command_palette
from fleasion.gui.glass import desktop_blur as blur_module
from fleasion.gui.glass.state import MAX_OPACITY, MIN_OPACITY, GlassState

if TYPE_CHECKING:
    from pathlib import Path

    from fleasion.utils.json_types import JsonObject


class _FakeConfig:
    def __init__(self, settings: dict[str, object]) -> None:
        self.settings = settings

    @property
    def accent_color(self) -> str:
        return str(self.settings.get('accent_color', 'Ice'))

    @accent_color.setter
    def accent_color(self, value: str) -> None:
        self.settings['accent_color'] = value

    @property
    def glass_opacity(self) -> float:
        value = self.settings.get('glass_opacity', 0.45)
        return float(value) if isinstance(value, (int, float)) else 0.45

    @glass_opacity.setter
    def glass_opacity(self, value: float) -> None:
        self.settings['glass_opacity'] = value

    @property
    def glass_backdrop(self) -> str:
        return str(self.settings.get('glass_backdrop', 'desktop'))

    @glass_backdrop.setter
    def glass_backdrop(self, value: str) -> None:
        self.settings['glass_backdrop'] = value


def test_state_persists_through_config() -> None:
    config = _FakeConfig({'accent_color': 'Mint', 'glass_backdrop': 'wallpaper'})
    state = GlassState()
    state.configure(config)  # pyright: ignore[reportArgumentType]
    assert state.settings.accent == 'Mint'
    assert state.settings.backdrop == 'wallpaper'
    state.set_opacity(5.0)
    assert state.settings.opacity == MAX_OPACITY
    assert config.settings['glass_opacity'] == MAX_OPACITY
    state.set_opacity(-1.0)
    assert state.settings.opacity == MIN_OPACITY


def test_unknown_accent_falls_back_to_default_colour() -> None:
    state = GlassState()
    state.settings.accent = 'Nope'
    assert state.settings.accent_color.startswith('#')


def test_config_manager_glass_properties_validate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', tmp_path / 'settings.json')
    config = manager_module.ConfigManager.__new__(manager_module.ConfigManager)
    settings: JsonObject = {'glass_opacity': 'junk', 'glass_backdrop': 'nonsense'}
    config.settings = settings
    monkeypatch.setattr(config, '_save_settings', lambda: None)
    assert config.glass_backdrop == 'desktop'
    assert MIN_OPACITY <= config.glass_opacity <= MAX_OPACITY
    config.glass_opacity = 9
    assert config.settings['glass_opacity'] == MAX_OPACITY


class _FakeDconf:
    """In-memory stand-in for the dconf and gnome-extensions CLIs."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def run(self, *args: str) -> str | None:
        if args[0] == 'gnome-extensions':
            return 'State: ACTIVE'
        command, path = args[1], args[2]
        key = path.rsplit('/', 1)[-1]
        if command == 'read':
            return self.values.get(key, '')
        if command == 'write':
            self.values[key] = args[3]
        elif command == 'reset':
            self.values.pop(key, None)
        return ''


def test_desktop_blur_round_trip_restores_original_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = {
        'blur': 'true',
        'opacity': '215',
        'dynamic-opacity': 'true',
        'whitelist': "['Firefox']",
    }
    fake = _FakeDconf(dict(original))
    monkeypatch.setattr(blur_module, '_run', fake.run)
    monkeypatch.setattr(blur_module.sys, 'platform', 'linux')
    monkeypatch.setattr(blur_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(blur_module, '_BACKUP_FILE', tmp_path / 'backup.json')
    blur = blur_module.DesktopBlur()

    assert blur.enable() is True
    assert fake.values['whitelist'] == "['Firefox', 'fleasion']"
    assert json.loads((tmp_path / 'backup.json').read_text())['whitelist'] == "['Firefox']"

    blur.disable()
    assert fake.values == original
    assert not (tmp_path / 'backup.json').exists()


def test_disable_preserves_user_whitelist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeDconf({'whitelist': "['fleasion']"})
    monkeypatch.setattr(blur_module, '_run', fake.run)
    monkeypatch.setattr(blur_module, '_BACKUP_FILE', tmp_path / 'backup.json')
    blur_module.DesktopBlur().disable()
    assert fake.values['whitelist'] == "['fleasion']"


@pytest.mark.parametrize('original', [None, '@as []', "['Firefox']", "['Firefox', 'fleasion']"])
def test_desktop_blur_preserves_exact_whitelist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original: str | None,
) -> None:
    values = {'blur': 'true', 'opacity': '180', 'dynamic-opacity': 'true'}
    if original is not None:
        values['whitelist'] = original
    fake = _FakeDconf(dict(values))
    monkeypatch.setattr(blur_module, '_run', fake.run)
    monkeypatch.setattr(blur_module.sys, 'platform', 'linux')
    monkeypatch.setattr(blur_module, 'CONFIG_DIR', tmp_path)
    backup = tmp_path / 'backup.json'
    monkeypatch.setattr(blur_module, '_BACKUP_FILE', backup)
    blur = blur_module.DesktopBlur()
    assert blur.enable()
    assert blur.enable()
    assert fake.values['blur'] == values['blur']
    assert fake.values['opacity'] == values['opacity']
    assert fake.values['dynamic-opacity'] == values['dynamic-opacity']
    blur.disable()
    assert fake.values == values
    assert not backup.exists()


@pytest.mark.parametrize('global_blur,enable_all', [('false', 'false'), ('true', 'true')])
def test_desktop_blur_does_not_change_global_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    global_blur: str,
    enable_all: str,
) -> None:
    original = {
        'blur': global_blur,
        'enable-all': enable_all,
        'opacity': '180',
        'dynamic-opacity': 'true',
        'whitelist': "['Firefox']",
    }
    fake = _FakeDconf(dict(original))
    monkeypatch.setattr(blur_module, '_run', fake.run)
    monkeypatch.setattr(blur_module.sys, 'platform', 'linux')
    monkeypatch.setattr(blur_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(blur_module, '_BACKUP_FILE', tmp_path / 'backup.json')
    blur = blur_module.DesktopBlur()
    blur.enable()
    assert not blur.can_toggle()
    blur.disable()
    assert fake.values == original
    assert not (tmp_path / 'backup.json').exists()


def test_desktop_blur_preserves_concurrent_whitelist_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeDconf({'blur': 'true', 'whitelist': "['Firefox']"})
    monkeypatch.setattr(blur_module, '_run', fake.run)
    monkeypatch.setattr(blur_module.sys, 'platform', 'linux')
    monkeypatch.setattr(blur_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(blur_module, '_BACKUP_FILE', tmp_path / 'backup.json')
    blur = blur_module.DesktopBlur()
    assert blur.enable()
    fake.values['whitelist'] = "['Firefox', 'fleasion', 'Other,App']"
    # Ownership survives restarting Fleasion
    blur_module.DesktopBlur().disable()
    assert fake.values['whitelist'] == "['Firefox', 'Other,App']"


def test_desktop_blur_keeps_backup_after_failed_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeDconf({'blur': 'true', 'whitelist': "['Firefox']"})
    monkeypatch.setattr(blur_module, '_run', fake.run)
    monkeypatch.setattr(blur_module.sys, 'platform', 'linux')
    monkeypatch.setattr(blur_module, 'CONFIG_DIR', tmp_path)
    backup = tmp_path / 'backup.json'
    monkeypatch.setattr(blur_module, '_BACKUP_FILE', backup)
    blur = blur_module.DesktopBlur()
    assert blur.enable()

    def failed_write(*args: str) -> str | None:
        if args[0] == 'dconf' and args[1] == 'write':
            return None
        return fake.run(*args)

    monkeypatch.setattr(blur_module, '_run', failed_write)
    blur.disable()
    assert backup.exists()
    assert fake.values['whitelist'] == "['Firefox', 'fleasion']"
    monkeypatch.setattr(blur_module, '_run', fake.run)
    blur.disable()
    assert fake.values['whitelist'] == "['Firefox']"
    assert not backup.exists()


def test_palette_ranks_word_starts_first() -> None:
    def noop() -> None:
        return None

    commands = [
        command_palette.Command('Open Configs', 'Action', noop),
        command_palette.Command('Show mod conflicts', 'Action', noop),
        command_palette.Command('Glass', 'Theme', noop),
    ]
    ranked = command_palette.rank(commands, 'confl')
    assert [c.title for c in ranked] == ['Show mod conflicts']
    assert command_palette.rank(commands, '')[0].title == 'Open Configs'
    assert command_palette.fuzzy_score('zzz', 'Theme Glass') is None
