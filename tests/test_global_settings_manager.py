"""Tests for explicit Roblox framerate-cap reset behavior."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from fleasion.modifications import global_settings_manager as gsm_module
from fleasion.modifications.global_settings_manager import GlobalSettingsManager


def _stash_path(stash_dir: Path, roblox_dir: Path) -> Path:
    callback = cast(
        'Callable[[Path, Path], Path]',
        gsm_module.__dict__['_global_settings_stash_path'],
    )
    return callback(stash_dir, roblox_dir)


def _manager(tmp_path: Path, roblox_dir: Path) -> GlobalSettingsManager:
    manager = GlobalSettingsManager.__new__(GlobalSettingsManager)
    manager.__dict__['_stash_dir'] = tmp_path / 'stash'
    manager.__dict__['_user_roblox_dirs'] = [roblox_dir]
    return manager


def _write_settings(path: Path, cap: int) -> None:
    path.write_text(
        (
            '<roblox><Item class="UserGameSettings"><Properties>'
            f'<int name="FramerateCap">{cap}</int>'
            '<bool name="Fullscreen">false</bool>'
            '</Properties></Item></roblox>'
        ),
        encoding='utf-8',
    )


def _stash_dir(manager: GlobalSettingsManager) -> Path:
    return cast('Path', manager.__dict__['_stash_dir'])


def _read_cap(manager: GlobalSettingsManager, path: Path) -> int | None:
    callback = cast('Callable[[Path], int | None]', getattr(manager, '_read_framerate_cap'))
    return callback(path)


def test_reset_framerate_cap_restores_stashed_original(tmp_path: Path) -> None:
    roblox_dir = tmp_path / 'Library' / 'Roblox'
    roblox_dir.mkdir(parents=True)
    settings = roblox_dir / 'GlobalBasicSettings_13.xml'
    _write_settings(settings, 55)

    manager = _manager(tmp_path, roblox_dir)
    stash = _stash_path(_stash_dir(manager), roblox_dir)
    stash.parent.mkdir(parents=True)
    _write_settings(stash, 60)

    manager.reset_framerate_cap()

    assert _read_cap(manager, settings) == 60
    assert not stash.exists()


def test_reset_framerate_cap_replaces_unstashed_legacy_override_with_default(
    tmp_path: Path,
) -> None:
    roblox_dir = tmp_path / 'Library' / 'Roblox'
    roblox_dir.mkdir(parents=True)
    settings = roblox_dir / 'GlobalBasicSettings_13.xml'
    _write_settings(settings, 55)

    manager = _manager(tmp_path, roblox_dir)

    manager.reset_framerate_cap()

    assert _read_cap(manager, settings) == 60
    assert 'Fullscreen' in settings.read_text(encoding='utf-8')


def test_read_framerate_cap_reports_the_active_persisted_value(tmp_path: Path) -> None:
    roblox_dir = tmp_path / 'Library' / 'Roblox'
    roblox_dir.mkdir(parents=True)
    _write_settings(roblox_dir / 'GlobalBasicSettings_13.xml', 55)

    assert _manager(tmp_path, roblox_dir).read_framerate_cap() == 55


def _write_full_settings(path: Path, cap: int, *, fullscreen: str = 'false') -> None:
    path.write_text(
        (
            '<roblox><Item class="UserGameSettings"><Properties>'
            f'<int name="FramerateCap">{cap}</int>'
            f'<bool name="Fullscreen">{fullscreen}</bool>'
            '</Properties></Item></roblox>'
        ),
        encoding='utf-8',
    )


def test_restore_puts_back_only_the_cap_and_keeps_other_in_game_changes(tmp_path: Path) -> None:
    roblox_dir = tmp_path / 'Library' / 'Roblox'
    roblox_dir.mkdir(parents=True)
    settings = roblox_dir / 'GlobalBasicSettings_13.xml'
    _write_full_settings(settings, 144)
    manager = _manager(tmp_path, roblox_dir)

    manager.write(240)
    # The user changes another setting in Roblox while Fleasion's cap is active
    _write_full_settings(settings, 240, fullscreen='true')
    manager.restore()

    assert _read_cap(manager, settings) == 144
    assert '<bool name="Fullscreen">true</bool>' in settings.read_text(encoding='utf-8')
    assert not _stash_path(_stash_dir(manager), roblox_dir).exists()


def test_restore_keeps_a_cap_the_user_chose_in_roblox(tmp_path: Path) -> None:
    roblox_dir = tmp_path / 'Library' / 'Roblox'
    roblox_dir.mkdir(parents=True)
    settings = roblox_dir / 'GlobalBasicSettings_13.xml'
    _write_full_settings(settings, 60)
    manager = _manager(tmp_path, roblox_dir)

    manager.write(120)
    _write_full_settings(settings, 240)  # Raised in Roblox's own settings
    manager.restore()

    assert _read_cap(manager, settings) == 240


def test_restore_never_makes_the_settings_file_read_only(tmp_path: Path) -> None:
    roblox_dir = tmp_path / 'Library' / 'Roblox'
    roblox_dir.mkdir(parents=True)
    settings = roblox_dir / 'GlobalBasicSettings_13.xml'
    _write_full_settings(settings, 60)
    manager = _manager(tmp_path, roblox_dir)
    stash = _stash_path(_stash_dir(manager), roblox_dir)
    stash.parent.mkdir(parents=True)
    _write_full_settings(stash, 60)
    set_read_only = cast('Callable[..., None]', getattr(manager, '_set_read_only'))
    get_read_only_state = cast('Callable[[Path], bool]', getattr(manager, '_get_read_only_state'))
    set_read_only(stash, read_only=True)  # A stash from an older Fleasion

    manager.restore()

    assert not get_read_only_state(settings)
