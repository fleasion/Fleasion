"""Game Rules dialog: filter editing, profiles and the mod pack buttons."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from fleasion.config import manager
from fleasion.gui import game_rules_dialog
from fleasion.gui.game_rules_dialog import GameRulesDialog
from fleasion.rules.game_profiles import make_profile

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def app() -> Iterator[QApplication]:
    instance = QApplication.instance()
    yield instance if isinstance(instance, QApplication) else QApplication([])


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> manager.ConfigManager:
    monkeypatch.setattr(manager, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(manager, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path / 'configs')
    monkeypatch.setattr(QMessageBox, 'information', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_args, **_kwargs: None)
    return manager.ConfigManager()


def test_preset_changes_preview_and_saves(app: QApplication, config: manager.ConfigManager) -> None:
    dialog = GameRulesDialog(config)
    filters = dialog.filters_tab
    before = filters.after_label.pixmap().toImage()
    filters.preset_combo.setCurrentIndex(filters.preset_combo.findData('noir'))
    filters._apply_preset()  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    after = filters.after_label.pixmap().toImage()
    assert before != after
    assert filters.steps_list.count() == 1

    filters.strength_slider.setValue(40)
    dialog.save()
    saved = config.asset_filters
    assert saved['enabled']
    assert saved['steps'][0]['effect'] == 'grayscale'
    assert saved['steps'][0]['strength'] == pytest.approx(0.4)
    dialog.close()
    app.processEvents()


def test_step_editing_add_move_remove(app: QApplication, config: manager.ConfigManager) -> None:
    dialog = GameRulesDialog(config)
    filters = dialog.filters_tab
    filters._add_step('tint')  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    filters._add_step('pixelate')  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert filters.amount_slider.isEnabled()
    filters._move_step(-1)  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert [step['effect'] for step in filters.settings()['steps']] == ['pixelate', 'tint']
    filters._remove_step()  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert [step['effect'] for step in filters.settings()['steps']] == ['tint']
    dialog.close()
    app.processEvents()


def test_profiles_tab_lists_and_saves_profiles(app: QApplication, config: manager.ConfigManager) -> None:
    config.create_config('Sky')
    config.game_profiles = [make_profile(5000, 'Surf', ['Sky'])]
    config.set_active_place(5000)
    dialog = GameRulesDialog(config)
    profiles = dialog.profiles_tab
    assert profiles.table.rowCount() == 1
    assert 'Surf' in profiles.current_label.text()
    profiles._upsert(make_profile(6000, 'Other'))  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    profiles._upsert(make_profile(6000, 'Duplicate'))  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert profiles.table.rowCount() == 2
    item = profiles.table.item(0, 0)
    assert item is not None
    item.setCheckState(game_rules_dialog.Qt.CheckState.Unchecked)
    dialog.save()
    assert [(p['place_id'], p['enabled']) for p in config.game_profiles] == [(5000, False), (6000, True)]
    dialog.close()
    app.processEvents()


def test_pack_export_and_import_buttons(
    app: QApplication, config: manager.ConfigManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config.create_config('Sky')
    rules: list[manager.JsonValue] = [{'replace_ids': [1], 'mode': 'remove', 'enabled': True}]
    config.set_replacement_rules('Sky', rules)
    config.enabled_configs = ['Sky']
    pack_path = tmp_path / 'out.fleasionpack'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_args, **_kwargs: (str(pack_path), ''))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_args, **_kwargs: (str(pack_path), ''))
    monkeypatch.setattr(QDialog, 'exec', lambda _self: QDialog.DialogCode.Accepted)

    dialog = GameRulesDialog(config)
    packs = dialog.packs_tab
    packs.name_edit.setText('Sky Pack')
    packs._export()  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert pack_path.exists()

    packs = cast('game_rules_dialog._PacksTab', dialog.packs_tab)  # pyright: ignore[reportPrivateUsage]
    packs._import()  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert 'Sky (Sky Pack)' in config.config_names
    assert 'Sky (Sky Pack)' in config.enabled_configs
    dialog.close()
    app.processEvents()
