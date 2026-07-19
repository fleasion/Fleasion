import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem, QTableWidget, QTableWidgetItem

from fleasion.gui import modifications_tab
from fleasion.gui.modifications_tab import CustomFFlagEditor, FFlagBrowserDialog, FastFlagValueDelegate


def _qapp():
    return QApplication.instance() or QApplication([])


def test_fflag_browser_reports_retrieved_and_filtered_totals(monkeypatch):
    app = _qapp()
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', lambda _self: None)
    dialog = FFlagBrowserDialog()
    dialog._apply_flags(
        {
            'DFFlagAlpha': 'True',
            'DFFlagBeta': 'False',
            'DFIntTargetFps': '60',
            'FFlagGamma': 'True',
            'FIntLevel': '2',
            'UnclassifiedFlag': 'enabled',
        }
    )

    assert dialog._count.text() == 'Showing 6 FastFlags • 6 retrieved from Roblox'
    assert dialog._table.columnCount() == 2
    assert dialog._family_filter.minimumWidth() == 165

    dialog._family_filter.setCurrentIndex(dialog._family_filter.findData('DFFlag'))
    assert dialog._count.text() == 'Showing 2 FastFlags • 6 retrieved from Roblox'

    dialog._search.setText('beta')
    assert dialog._count.text() == 'Showing 1 FastFlags • 6 retrieved from Roblox'
    assert app is not None


def test_fflag_browser_extracts_current_values_and_selection(monkeypatch):
    app = _qapp()
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', lambda _self: None)
    assert FFlagBrowserDialog._extract_flags(
        {'applicationSettings': {'FFlagExample': True, 'DFIntLimit': 120, 'skip': []}}
    ) == {'FFlagExample': 'True', 'DFIntLimit': '120'}
    with pytest.raises(ValueError, match='application FastFlags'):
        FFlagBrowserDialog._extract_flags({})

    dialog = FFlagBrowserDialog()
    dialog._apply_flags({'FFlagExample': 'True'})
    dialog._table.selectRow(0)
    app.processEvents()
    dialog._add_selected()

    assert dialog.selected_flags == {'FFlagExample': 'True'}


def test_fflag_browser_extracts_tracker_only_fastvariables():
    assert FFlagBrowserDialog._extract_tracker_flags(
        b'[C++] DFFlagDebugDrawBroadPhaseAABBs\n'
        b'[C++] DFIntTaskSchedulerTargetFps\n'
        b'[C++] NotAFastVariable\n'
    ) == {
        'DFFlagDebugDrawBroadPhaseAABBs': None,
        'DFIntTaskSchedulerTargetFps': None,
    }


def test_fflag_browser_adds_tracker_only_fastvariables_blank(monkeypatch):
    app = _qapp()
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', lambda _self: None)
    dialog = FFlagBrowserDialog()
    dialog._apply_flags({'DFIntTaskSchedulerTargetFps': None})
    dialog._table.selectRow(0)
    app.processEvents()
    dialog._add_selected()

    assert dialog.selected_flags == {'DFIntTaskSchedulerTargetFps': ''}


def test_fflag_browser_merges_live_values_with_the_tracker_lists(monkeypatch, tmp_path):
    app = _qapp()
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', lambda _self: None)
    monkeypatch.setattr(FFlagBrowserDialog, '_CACHE_PATH', tmp_path / 'fflag_browser.json')
    dialog = FFlagBrowserDialog()
    fetched = []
    dialog.flags_loaded.connect(fetched.append)

    def fake_http_get(url, timeout, headers=None):
        if url == dialog._SETTINGS_URL:
            assert headers == dialog._BYPASS_CUSTOM_FFLAGS_HEADER
            return b'{"applicationSettings":{"DFFlagDebugDrawBroadPhaseAABBs":"False"}}'
        if url in dialog._settings_urls():
            assert headers == dialog._BYPASS_CUSTOM_FFLAGS_HEADER
            return b'{"applicationSettings":{"DFIntTaskSchedulerTargetFps":"60"}}'
        if url == dialog._TRACKER_VARIABLES_URL:
            return b'[C++] DFFlagDebugDrawBroadPhaseAABBs\n'
        if url == dialog._HISTORICAL_TRACKER_VARIABLES_URL:
            return b'[C++] DFIntTaskSchedulerTargetFps\n'
        raise AssertionError(f'unexpected URL: {url}')

    monkeypatch.setattr(modifications_tab, 'http_get', fake_http_get)
    dialog._fetch_flags()

    assert fetched == [
        {
            'DFFlagDebugDrawBroadPhaseAABBs': 'False',
            'DFIntTaskSchedulerTargetFps': None,
        }
    ]
    assert app is not None


def test_fflag_browser_cache_expires_after_one_hour(monkeypatch, tmp_path):
    cache_path = tmp_path / 'fflag_browser.json'
    monkeypatch.setattr(FFlagBrowserDialog, '_CACHE_PATH', cache_path)
    flags = {'DFFlagDebugDrawBroadPhaseAABBs': None, 'FFlagExample': 'True'}

    FFlagBrowserDialog._write_cache(flags, now=10_000)

    assert FFlagBrowserDialog._read_cache(now=13_599) == flags
    assert FFlagBrowserDialog._read_cache(now=13_600) is None


def test_fflag_browser_refresh_bypasses_a_fresh_cache(monkeypatch):
    app = _qapp()
    cached_flags = {'DFIntTaskSchedulerTargetFps': None}
    monkeypatch.setattr(
        FFlagBrowserDialog,
        '_read_cache',
        classmethod(lambda _cls: cached_flags),
    )
    dialog = FFlagBrowserDialog()
    assert dialog._flags == cached_flags
    assert dialog._count.text().endswith('cached')

    fetched = []
    monkeypatch.setattr(dialog, '_fetch_flags', lambda: fetched.append(True))

    class ImmediateThread:
        def __init__(self, *, target, daemon):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(modifications_tab.threading, 'Thread', ImmediateThread)
    dialog._refresh(force=True)

    assert fetched == [True]
    assert app is not None


def test_custom_fflag_editor_uses_an_edit_on_demand_boolean_selector():
    app = _qapp()
    config = SimpleNamespace(
        custom_fflags={'FFlagExample': 'True', 'DFIntExample': '60'},
        custom_fflags_enabled=False,
    )
    proxy = SimpleNamespace(refresh_custom_fflag_interception=lambda: None)

    editor = CustomFFlagEditor(config, proxy)

    assert editor._table.rowCount() == 2
    assert editor._table.cellWidget(0, 1) is None
    assert isinstance(editor._table.itemDelegateForColumn(1), FastFlagValueDelegate)
    assert app is not None


def test_boolean_fflag_picker_commits_and_closes_after_selection():
    app = _qapp()
    table = QTableWidget(1, 2)
    table.setItem(0, 0, QTableWidgetItem('FFlagExample'))
    table.setItem(0, 1, QTableWidgetItem('True'))
    delegate = FastFlagValueDelegate(table)
    index = table.model().index(0, 1)
    combo = delegate.createEditor(table.viewport(), QStyleOptionViewItem(), index)
    committed = []
    closed = []
    delegate.commitData.connect(committed.append)
    delegate.closeEditor.connect(lambda editor, hint: closed.append((editor, hint)))

    combo.activated.emit(1)

    assert committed == [combo]
    assert closed[0][0] is combo
    assert app is not None
