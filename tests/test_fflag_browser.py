import inspect
import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem, QTableWidget, QTableWidgetItem

from fleasion import localization
from fleasion.gui import modifications_tab
from fleasion.gui.modifications_tab import (
    CustomFFlagEditor,
    FFlagBrowserDialog,
    FastFlagValueDelegate,
    CompactBooleanComboBox,
)


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



def test_boolean_value_cell_click_opens_selector_even_after_row_selection():
    app = _qapp()
    config = SimpleNamespace(
        custom_fflags={'DFFlagExample': 'False'},
        custom_fflags_enabled=False,
    )
    editor = CustomFFlagEditor(
        config, SimpleNamespace(refresh_custom_fflag_interception=lambda: None)
    )
    editor.show()
    app.processEvents()
    table = editor._table
    table.selectRow(0)
    index = table.model().index(0, 1)

    editor._edit_value_cell(index)
    app.processEvents()

    assert any(combo.isVisible() for combo in table.findChildren(CompactBooleanComboBox))


def test_grouped_boolean_flag_uses_canonical_name_for_delegate():
    app = _qapp()
    config = SimpleNamespace(
        custom_fflags={'DFFlagExample': 'False'},
        custom_fflags_enabled=False,
        custom_fflag_folders={'Visual': ['DFFlagExample']},
    )
    editor = CustomFFlagEditor(
        config, SimpleNamespace(refresh_custom_fflag_interception=lambda: None)
    )
    table = editor._table
    index = table.model().index(1, 1)
    delegate = table.itemDelegateForColumn(1)

    combo = delegate.createEditor(table.viewport(), QStyleOptionViewItem(), index)

    assert isinstance(combo, CompactBooleanComboBox)
    assert app is not None


def test_flag_value_mutation_does_not_refresh_interception_routes():
    app = _qapp()
    config = SimpleNamespace(
        custom_fflags={'DFFlagExample': 'False'},
        custom_fflags_enabled=False,
    )
    proxy = SimpleNamespace(refresh_calls=0)
    proxy.refresh_custom_fflag_interception = lambda: setattr(
        proxy, 'refresh_calls', proxy.refresh_calls + 1
    )
    editor = CustomFFlagEditor(config, proxy)

    editor._set_flags({'DFFlagExample': 'True', 'DFIntExample': '60'})

    assert proxy.refresh_calls == 0
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


def test_custom_fflag_editor_renders_and_toggles_folder_rows():
    app = _qapp()
    config = SimpleNamespace(
        custom_fflags={'FFlagOne': 'True', 'FFlagTwo': 'False'},
        custom_fflags_enabled=True,
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
        custom_fflag_folders={'Visual': ['FFlagOne', 'FFlagTwo']},
        custom_fflag_disabled_folders=[],
        custom_fflag_folder_keybinds={},
        custom_fflags_warning_accepted=True,
    )
    proxy = SimpleNamespace(refresh_custom_fflag_interception=lambda: None)

    editor = CustomFFlagEditor(config, proxy)
    table = editor._table

    assert table.rowCount() == 3
    assert table.item(0, 0).text() == 'Visual'
    assert table.item(0, 1).text() == '2 FastFlags'
    assert table.item(1, 0).text().strip() == 'FFlagOne'
    assert table.item(2, 0).text().strip() == 'FFlagTwo'

    table.item(0, 2).setCheckState(Qt.CheckState.Unchecked)
    app.processEvents()

    assert config.custom_fflag_disabled_folders == ['Visual']
    assert config.custom_fflag_disabled == []
    assert app is not None


def test_custom_fflag_toolbar_uses_compact_json_menu_and_custom_actions_button():
    app = _qapp()
    config = SimpleNamespace(
        custom_fflags={},
        custom_fflags_enabled=False,
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
        custom_fflag_folders={},
        custom_fflag_disabled_folders=[],
        custom_fflag_folder_keybinds={},
        custom_fflag_actions={},
    )
    editor = CustomFFlagEditor(
        config, SimpleNamespace(refresh_custom_fflag_interception=lambda: None)
    )

    button_texts = {button.text() for button in editor.findChildren(modifications_tab.QPushButton)}

    assert 'JSON' in button_texts
    assert 'Custom Actions' in button_texts
    assert 'Import JSON…' not in button_texts
    assert 'Export JSON…' not in button_texts
    assert app is not None


def test_custom_actions_manager_preserves_multiple_actions_for_same_fastflag():
    app = _qapp()
    actions = {
        '90 FPS': {'flags': {'DFIntTaskSchedulerTargetFps': '90'}},
        '144 FPS': {'flags': {'DFIntTaskSchedulerTargetFps': '144'}},
    }
    dialog = modifications_tab.FastFlagActionsDialog(
        actions,
        {},
        lambda _name: (False, None),
        lambda _binding: 'Not assigned',
    )

    assert dialog.actions['90 FPS']['flags']['DFIntTaskSchedulerTargetFps'] == '90'
    assert dialog.actions['144 FPS']['flags']['DFIntTaskSchedulerTargetFps'] == '144'
    assert app is not None


def test_custom_actions_keybind_column_double_click_assigns_hotkey():
    app = _qapp()
    captured = []
    binding = {'scan_code': 0x10, 'extended': False, 'modifiers': 0}

    def capture(name):
        captured.append(name)
        return True, binding

    dialog = modifications_tab.FastFlagActionsDialog(
        {'90 FPS': {'flags': {'DFIntTaskSchedulerTargetFps': '90'}}},
        {},
        capture,
        lambda _binding: 'Not assigned',
    )

    dialog._table.cellDoubleClicked.emit(0, 2)
    app.processEvents()

    assert captured == ['90 FPS']
    assert dialog.actions['90 FPS']['keybind'] == binding
    button_texts = {
        button.text() for button in dialog.findChildren(modifications_tab.QPushButton)
    }
    assert 'Assign Hotkey…' not in button_texts
    assert 'Clear Keybind' not in button_texts


def test_custom_actions_non_keybind_double_click_edits_action(monkeypatch):
    app = _qapp()
    dialog = modifications_tab.FastFlagActionsDialog(
        {'90 FPS': {'flags': {'DFIntTaskSchedulerTargetFps': '90'}}},
        {},
        lambda _name: (False, None),
        lambda _binding: 'Not assigned',
    )
    edited = []

    def edit_action():
        edited.append(None)

    monkeypatch.setattr(dialog, '_edit_action', edit_action)

    dialog._table.cellDoubleClicked.emit(0, 1)
    app.processEvents()

    assert edited == [None]


def test_create_folder_rejects_existing_name_without_changing_membership(monkeypatch):
    app = _qapp()
    config = SimpleNamespace(
        custom_fflags={'FFlagOne': '1', 'FFlagTwo': '2'},
        custom_fflags_enabled=False,
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
        custom_fflag_folders={'Visual': ['FFlagOne']},
        custom_fflag_disabled_folders=[],
        custom_fflag_folder_keybinds={},
        custom_fflag_actions={},
    )
    editor = CustomFFlagEditor(
        config, SimpleNamespace(refresh_custom_fflag_interception=lambda: None)
    )
    table = editor._table
    table.selectRow(2)
    monkeypatch.setattr(
        modifications_tab.QInputDialog, 'getText', lambda *_a, **_k: ('Visual', True)
    )
    warnings = []
    monkeypatch.setattr(
        modifications_tab.QMessageBox,
        'warning',
        lambda *args, **_kwargs: warnings.append(args),
    )

    editor._create_folder_from_selected()

    assert config.custom_fflag_folders == {'Visual': ['FFlagOne']}
    assert warnings
    assert app is not None


def test_inline_duplicate_fastflag_rename_is_reverted(monkeypatch):
    app = _qapp()
    config = SimpleNamespace(
        custom_fflags={'FFlagOne': '1', 'FFlagTwo': '2'},
        custom_fflags_enabled=False,
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
        custom_fflag_folders={},
        custom_fflag_disabled_folders=[],
        custom_fflag_folder_keybinds={},
        custom_fflag_actions={},
    )
    editor = CustomFFlagEditor(
        config, SimpleNamespace(refresh_custom_fflag_interception=lambda: None)
    )
    warnings = []
    monkeypatch.setattr(
        modifications_tab.QMessageBox,
        'warning',
        lambda *args, **_kwargs: warnings.append(args),
    )
    first_name = editor._table.item(0, 0)
    assert first_name is not None

    first_name.setText('FFlagTwo')
    app.processEvents()

    assert first_name.text() == 'FFlagOne'
    assert config.custom_fflags == {'FFlagOne': '1', 'FFlagTwo': '2'}
    assert warnings


def test_hotkey_refresh_preserves_active_value_sort():
    app = _qapp()
    config = SimpleNamespace(
        custom_fflags={'FFlagOne': '2', 'FFlagTwo': '1'},
        custom_fflags_enabled=False,
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
        custom_fflag_folders={},
        custom_fflag_disabled_folders=[],
        custom_fflag_folder_keybinds={},
        custom_fflag_actions={},
    )
    editor = CustomFFlagEditor(
        config, SimpleNamespace(refresh_custom_fflag_interception=lambda: None)
    )
    editor._sort_rows(1)
    config.custom_fflags = {'FFlagOne': '0', 'FFlagTwo': '3'}

    editor._on_hotkey_toggled('action:test')

    names = []
    for row in range(editor._table.rowCount()):
        item = editor._table.item(row, 0)
        if item is not None:
            names.append(item.text().strip())
    assert names == ['FFlagOne', 'FFlagTwo']
    assert app is not None


def test_windows_hotkey_capture_marks_numpad_divide_and_enter_extended():
    divide_event = QKeyEvent(
        modifications_tab.QEvent.Type.KeyPress,
        int(Qt.Key.Key_Slash),
        Qt.KeyboardModifier.KeypadModifier,
        0x35,
        0x6F,
        0,
    )
    enter_event = QKeyEvent(
        modifications_tab.QEvent.Type.KeyPress,
        int(Qt.Key.Key_Enter),
        Qt.KeyboardModifier.KeypadModifier,
        0x1C,
        0x0D,
        0,
    )

    divide = modifications_tab.WindowsHotkeyCaptureDialog._event_binding(divide_event, 0)
    enter = modifications_tab.WindowsHotkeyCaptureDialog._event_binding(enter_event, 0)

    assert divide is not None and divide['extended'] is True
    assert enter is not None and enter['extended'] is True
