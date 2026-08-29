from __future__ import annotations

from pathlib import Path

import pytest

import inspect

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem, QTableWidget, QTableWidgetItem

from fleasion import localization
from fleasion.gui import modifications_tab
from fleasion.gui.modifications_tab import (
    CustomFFlagEditor,
    FFlagBrowserDialog,
    FastFlagValueDelegate,
)

from fleasion.modifications import fflag_catalog
from fleasion.modifications.fflag_catalog import FastFlagCatalog


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_fflag_browser_extracts_application_values():
    assert FastFlagCatalog.extract_flags(
        {'applicationSettings': {'FFlagExample': True, 'DFIntLimit': 120, 'skip': []}}
    ) == {'FFlagExample': 'True', 'DFIntLimit': '120'}
    with pytest.raises(ValueError, match='application FastFlags'):
        FastFlagCatalog.extract_flags({})


def test_fflag_browser_extracts_tracker_only_fastvariables():
    assert FastFlagCatalog.extract_tracker_flags(
        b'[C++] DFFlagDebugDrawBroadPhaseAABBs\n'
        b'[C++] DFIntTaskSchedulerTargetFps\n'
        b'[C++] NotAFastVariable\n'
    ) == {
        'DFFlagDebugDrawBroadPhaseAABBs': None,
        'DFIntTaskSchedulerTargetFps': None,
    }


def test_fflag_browser_merges_live_values_with_tracker_lists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(FastFlagCatalog, 'cache_path', tmp_path / 'fflag_browser.json')

    def fake_http_get(
        url: str,
        timeout: int,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        assert timeout > 0
        if url == FastFlagCatalog.SETTINGS_URL:
            assert headers == FastFlagCatalog.BYPASS_CUSTOM_FFLAGS_HEADER
            return b'{"applicationSettings":{"DFFlagDebugDrawBroadPhaseAABBs":"False"}}'
        if url in FastFlagCatalog.settings_urls():
            assert headers == FastFlagCatalog.BYPASS_CUSTOM_FFLAGS_HEADER
            return b'{"applicationSettings":{"DFIntTaskSchedulerTargetFps":"60"}}'
        if url == FastFlagCatalog.TRACKER_VARIABLES_URL:
            return b'[C++] DFFlagDebugDrawBroadPhaseAABBs\n'
        if url == FastFlagCatalog.HISTORICAL_TRACKER_VARIABLES_URL:
            return b'[C++] DFIntTaskSchedulerTargetFps\n'
        raise AssertionError(f'unexpected URL: {url}')

    monkeypatch.setattr(fflag_catalog, 'http_get', fake_http_get)
    fetched = FastFlagCatalog.fetch()

    assert fetched == {
        'DFFlagDebugDrawBroadPhaseAABBs': 'False',
        'DFIntTaskSchedulerTargetFps': None,
    }


def test_fflag_browser_cache_expires_after_one_hour(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    cache_path = tmp_path / 'fflag_browser.json'
    monkeypatch.setattr(FastFlagCatalog, 'cache_path', cache_path)
    flags = {'DFFlagDebugDrawBroadPhaseAABBs': None, 'FFlagExample': 'True'}

    FastFlagCatalog.write_cache(flags, now=10_000)

    assert FastFlagCatalog.read_cache(now=13_599) == flags
    assert FastFlagCatalog.read_cache(now=13_600) is None


def test_fflag_browser_translates_unpublished_display_without_changing_none_sentinel(monkeypatch):
    app = _qapp()
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', lambda _self: None)
    pseudo = dict(localization.ENGLISH)
    pseudo['modifications.fastflags.no_value'] = '⟦no-value⟧'
    monkeypatch.setitem(localization._TRANSLATIONS, 'zz', pseudo)
    try:
        localization.set_language('zz')
        dialog = FFlagBrowserDialog()
        dialog._apply_flags({'DFIntTaskSchedulerTargetFps': None})

        assert dialog._flags['DFIntTaskSchedulerTargetFps'] is None
        assert dialog._display_value(None) == '⟦no-value⟧'
        assert dialog._table.item(0, 1).text() == '⟦no-value⟧'
        dialog._search.setText('⟦no-value⟧')
        assert not dialog._table.isRowHidden(0)
        assert app is not None
    finally:
        localization.set_language('en')


def test_boolean_fflag_editor_reads_canonical_user_role_not_translated_display(monkeypatch):
    app = _qapp()
    table = QTableWidget(1, 2)
    table.setItem(0, 0, QTableWidgetItem('FFlagExample'))
    value_item = QTableWidgetItem('Vrai')
    value_item.setData(Qt.ItemDataRole.UserRole, 'True')
    table.setItem(0, 1, value_item)
    delegate = FastFlagValueDelegate(table)
    index = table.model().index(0, 1)
    combo = delegate.createEditor(table.viewport(), QStyleOptionViewItem(), index)
    monkeypatch.setattr(modifications_tab.QTimer, 'singleShot', lambda *_args: None)

    delegate.setEditorData(combo, index)

    assert combo.currentData() == 'True'
    assert app is not None


def test_add_custom_boolean_fflag_uses_combo_item_data_not_translated_text():
    source = inspect.getsource(CustomFFlagEditor._add_flag)
    assert 'value_combo.currentData()' in source
    assert 'value_combo.currentText()' not in source
