"""Conflict reporting follows effective proxy keys and trayless theme changes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from fleasion.config import manager
from fleasion.gui import conflicts
from fleasion.gui.replacer_config import ReplacerConfigWindow
from fleasion.gui.settings_tab import SettingsTab
from fleasion.gui.theme import ThemeManager
from fleasion.proxy.addons.texture_stripper import TextureStripper
from fleasion.utils.json_types import JsonValue

if TYPE_CHECKING:
    from fleasion.config.manager import ReplacementMaps


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> manager.ConfigManager:
    monkeypatch.setattr(manager, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(manager, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path / 'configs')
    return manager.ConfigManager()


@pytest.fixture
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def _enable(config: manager.ConfigManager, name: str, key: str, *, mode: str = 'id') -> None:
    assert config.create_config(name)
    rule: dict[str, JsonValue] = {'replace_ids': [key], 'mode': mode}
    if mode == 'id':
        rule['with_id'] = 20001
    elif mode == 'local':
        rule['local_path'] = '/tmp/animation.rbxm'
    elif mode == 'cdn':
        rule['cdn_url'] = 'https://example.invalid/animation'
    config.set_replacement_rules(name, [rule])
    config.set_config_enabled(name, True)


def _process(
    stripper: TextureStripper,
    entry: dict[str, JsonValue],
    replacements: ReplacementMaps,
    *,
    batch: str,
) -> dict[str, JsonValue]:
    body = json.dumps([entry]).encode()
    modified = stripper.process_batch_request(body, {}, replacements, batch_id=batch)
    changed = modified[0] if isinstance(modified, tuple) else modified
    return cast('dict[str, JsonValue]', json.loads(changed)[0])


@pytest.mark.parametrize('mode', ['id', 'cdn', 'local'])
def test_virtual_animation_config_has_no_effective_proxy_route(
    config: manager.ConfigManager, mode: str
) -> None:
    _enable(config, 'r6', 'R6Animation', mode=mode)
    _enable(config, 'r15', 'R15Animation', mode=mode)
    assert conflicts.find_conflicts(config) == []
    replacements = config.get_all_replacements()
    assert 'R6Animation' in replacements[{'id': 0, 'cdn': 2, 'local': 3}[mode]]
    stripper = TextureStripper(config)
    TextureStripper.reset_routes('virtual animation routing regression')
    try:
        result = _process(
            stripper,
            {'assetId': 1001, 'assetType': 'Animation', 'assetTypeId': 24, 'requestId': 'a'},
            replacements,
            batch=f'anim-{mode}',
        )
        assert result['assetId'] == 1001
        assert not stripper.has_pending()
        assert not stripper._anim_local_pending  # pyright: ignore[reportPrivateUsage]
    finally:
        TextureStripper.reset_routes('end virtual animation routing regression')


def test_virtual_animation_rig_scopes_are_disjoint_even_if_routing_is_added() -> None:
    r6 = conflicts._request_scope('R6Animation')  # pyright: ignore[reportPrivateUsage]
    r15 = conflicts._request_scope('R15Animation')  # pyright: ignore[reportPrivateUsage]
    nonplayer = conflicts._request_scope('NonPlayerAnimation')  # pyright: ignore[reportPrivateUsage]
    animation = conflicts._request_scope('24')  # pyright: ignore[reportPrivateUsage]
    assert r6 is not None and r15 is not None and nonplayer is not None
    assert animation is not None
    assert not r6.overlaps(r15)
    assert not r6.overlaps(nonplayer)
    assert not r15.overlaps(nonplayer)
    assert all(scope.overlaps(animation) for scope in (r6, r15, nonplayer))


@pytest.mark.parametrize('key', ['TexturePack:00', '01001:0', '1001:00'])
def test_exact_inert_texturepack_keys_are_not_reported_as_conflicts(
    config: manager.ConfigManager, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    _enable(config, 'a', key)
    _enable(config, 'b', key)
    assert conflicts.find_conflicts(config) == []
    assert key in config.get_all_replacements()[0]
    stripper = TextureStripper(config)

    def generated_slot_map(
        data: list[JsonValue], slot_target_ids: set[int] | None = None
    ) -> dict[int, int]:
        assert data
        return {0: 0}

    monkeypatch.setattr(stripper, '_build_texpack_request_slot_map', generated_slot_map)
    TextureStripper.reset_routes('noncanonical slot regression')
    try:
        result = _process(
            stripper,
            {'assetId': 1001, 'assetType': 'TexturePack', 'assetTypeId': 63, 'requestId': 'a'},
            config.get_all_replacements(),
            batch=f'slot-{key}',
        )
        assert result['assetId'] == 1001
    finally:
        TextureStripper.reset_routes('end noncanonical slot regression')


def test_canonical_texturepack_key_still_matches_and_reports_exact_conflict(
    config: manager.ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(config, 'a', 'TexturePack:0')
    _enable(config, 'b', 'TexturePack:0')
    (reported,) = conflicts.find_conflicts(config)
    assert not reported.potential
    assert reported.winner.config == 'b'
    stripper = TextureStripper(config)

    def generated_slot_map(
        data: list[JsonValue], slot_target_ids: set[int] | None = None
    ) -> dict[int, int]:
        assert data
        return {0: 0}

    monkeypatch.setattr(stripper, '_build_texpack_request_slot_map', generated_slot_map)
    TextureStripper.reset_routes('canonical slot regression')
    try:
        result = _process(
            stripper,
            {'assetId': 1001, 'assetType': 'TexturePack', 'assetTypeId': 63, 'requestId': 'a'},
            config.get_all_replacements(),
            batch='canonical-slot',
        )
        assert result['assetId'] == 20001
    finally:
        TextureStripper.reset_routes('end canonical slot regression')


def test_command_palette_without_tray_refreshes_settings_theme_and_glass_controls(
    app: QApplication, config: manager.ConfigManager
) -> None:
    previous_theme = ThemeManager._current_theme  # pyright: ignore[reportPrivateUsage]
    previous_effective = ThemeManager._effective_theme  # pyright: ignore[reportPrivateUsage]
    config.theme = 'System'
    window = ReplacerConfigWindow(config, system_tray=None)
    tab = SettingsTab(config, parent=window)
    window._settings_tab = tab  # pyright: ignore[reportPrivateUsage]
    try:
        for theme in ('Dark', 'Glass', 'Light'):
            window._apply_theme(theme)  # pyright: ignore[reportPrivateUsage]
            assert config.theme == theme
            assert tab._style_buttons[config.appearance_style].isChecked()  # pyright: ignore[reportPrivateUsage]
            assert tab._color_buttons[config.color_mode].isChecked()  # pyright: ignore[reportPrivateUsage]
            assert tab._backdrop_combo.isHidden() == (theme != 'Glass')  # pyright: ignore[reportPrivateUsage]
    finally:
        ThemeManager.apply_theme(previous_theme)
        ThemeManager._effective_theme = previous_effective  # pyright: ignore[reportPrivateUsage]
        window.close()
        window.deleteLater()
        app.processEvents()
