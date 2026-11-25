"""Replacer column sizing and untruncated replacement display."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QHeaderView

from fleasion.config import manager as manager_module
from fleasion.gui.replacer_config import ReplacerConfigWindow

_app: QApplication | None = None


def _qapp() -> QApplication:
    global _app
    instance = QApplication.instance()
    _app = cast('QApplication', instance) if instance is not None else QApplication([])
    return _app


def test_replacer_columns_resize_and_persist_without_truncating_cdn_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', config_dir / 'configs')

    config = manager_module.ConfigManager()
    url = 'https://example.invalid/assets/' + 'very-long-replacement-asset-id-' * 5
    config.set_replacement_rules(
        'Default',
        [{'name': 'A profile', 'replace_ids': [123], 'mode': 'cdn', 'cdn_url': url}],
    )
    window = ReplacerConfigWindow(config)
    window.resize(1250, 750)
    window.show()
    app.processEvents()
    try:
        header = window.tree.header()
        for column in range(4):
            assert header.sectionResizeMode(column) == QHeaderView.ResizeMode.Interactive
        assert header.sectionResizeMode(4) == QHeaderView.ResizeMode.Stretch
        item = window.tree.topLevelItem(0)
        assert item is not None
        assert item.text(4) == url

        header.resizeSection(1, 310)
        header.resizeSection(3, 160)
        app.processEvents()
        assert config.settings['replacer_column_widths'] == {
            'profile_name': 310,
            'asset_ids': 160,
        }
        refresh_tree = cast('Callable[[], None]', getattr(window, '_refresh_tree'))
        refresh_tree()
        assert window.tree.columnWidth(1) == 310
        assert window.tree.columnWidth(3) == 160
        item = window.tree.topLevelItem(0)
        assert item is not None
        assert item.text(4) == url
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()

    reopened_config = manager_module.ConfigManager()
    reopened = ReplacerConfigWindow(reopened_config)
    reopened.resize(1250, 750)
    reopened.show()
    app.processEvents()
    try:
        assert reopened.tree.columnWidth(1) == 310
        assert reopened.tree.columnWidth(3) == 160
        item = reopened.tree.topLevelItem(0)
        assert item is not None
        assert item.text(4) == url
    finally:
        reopened.close()
        reopened.deleteLater()
        app.processEvents()


def test_invalid_saved_replacer_widths_fall_back_to_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', config_dir / 'configs')
    config = manager_module.ConfigManager()
    config.settings['replacer_column_widths'] = {
        'status': True,
        'profile_name': -15,
        'mode': '300',
        'asset_ids': 170,
        'replacement': 800,
    }
    window = ReplacerConfigWindow(config)
    window.resize(1250, 750)
    window.show()
    app.processEvents()
    try:
        assert window.tree.columnWidth(0) == 85
        assert window.tree.columnWidth(1) == 220
        assert window.tree.columnWidth(2) == 95
        assert window.tree.columnWidth(3) == 170
        assert window.tree.header().sectionResizeMode(4) == QHeaderView.ResizeMode.Stretch
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
