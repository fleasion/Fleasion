"""Tests for explicit Roblox framerate-cap reset behavior."""

from __future__ import annotations

from fleasion.modifications.global_settings_manager import (
    GlobalSettingsManager,
    _global_settings_stash_path,
)


def _manager(tmp_path, roblox_dir):
    manager = GlobalSettingsManager.__new__(GlobalSettingsManager)
    manager._stash_dir = tmp_path / 'stash'
    manager._user_roblox_dirs = [roblox_dir]
    return manager


def _write_settings(path, cap: int) -> None:
    path.write_text(
        (
            '<roblox><Item class="UserGameSettings"><Properties>'
            f'<int name="FramerateCap">{cap}</int>'
            '<bool name="Fullscreen">false</bool>'
            '</Properties></Item></roblox>'
        ),
        encoding='utf-8',
    )


def test_reset_framerate_cap_restores_stashed_original(tmp_path):
    roblox_dir = tmp_path / 'Library' / 'Roblox'
    roblox_dir.mkdir(parents=True)
    settings = roblox_dir / 'GlobalBasicSettings_13.xml'
    _write_settings(settings, 55)

    manager = _manager(tmp_path, roblox_dir)
    stash = _global_settings_stash_path(manager._stash_dir, roblox_dir)
    stash.parent.mkdir(parents=True)
    _write_settings(stash, 60)

    manager.reset_framerate_cap()

    assert manager._read_framerate_cap(settings) == 60
    assert not stash.exists()


def test_reset_framerate_cap_replaces_unstashed_legacy_override_with_default(tmp_path):
    roblox_dir = tmp_path / 'Library' / 'Roblox'
    roblox_dir.mkdir(parents=True)
    settings = roblox_dir / 'GlobalBasicSettings_13.xml'
    _write_settings(settings, 55)

    manager = _manager(tmp_path, roblox_dir)

    manager.reset_framerate_cap()

    assert manager._read_framerate_cap(settings) == 60
    assert 'Fullscreen' in settings.read_text(encoding='utf-8')


def test_read_framerate_cap_reports_the_active_persisted_value(tmp_path):
    roblox_dir = tmp_path / 'Library' / 'Roblox'
    roblox_dir.mkdir(parents=True)
    _write_settings(roblox_dir / 'GlobalBasicSettings_13.xml', 55)

    assert _manager(tmp_path, roblox_dir).read_framerate_cap() == 55
