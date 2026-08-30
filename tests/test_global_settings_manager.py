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
