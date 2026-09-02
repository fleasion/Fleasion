import inspect
import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
)

from fleasion import localization
from fleasion.gui import modifications_tab
from fleasion.gui.modifications_tab import (
    CompactBooleanComboBox,
    CustomFFlagEditor,
    FastFlagValueDelegate,
    FFlagBrowserDialog,
)

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.master import ProxyMaster


type BrowserFlags = dict[str, str | None]


def _qapp() -> QApplication:
    app = QApplication.instance()
    return cast('QApplication', app) if app is not None else QApplication([])


def _noop_refresh(_self: FFlagBrowserDialog, force: bool = False) -> None:
    del force


def _apply_flags(dialog: FFlagBrowserDialog, flags: BrowserFlags) -> None:
    callback = cast(
        'Callable[[FFlagBrowserDialog, BrowserFlags], None]',
        FFlagBrowserDialog.__dict__['_apply_flags'],
    )
    callback(dialog, flags)


def _extract_flags(payload: object) -> dict[str, str]:
    callback = cast(
        'Callable[[object], dict[str, str]]',
        FFlagBrowserDialog.__dict__['_extract_flags'],
    )
    return callback(payload)


def _extract_tracker_flags(payload: bytes) -> dict[str, None]:
    callback = cast(
        'Callable[[bytes], dict[str, None]]',
        getattr(FFlagBrowserDialog, '_extract_tracker_flags'),
    )
    return callback(payload)


def _add_selected(dialog: FFlagBrowserDialog) -> None:
    callback = cast(
        'Callable[[FFlagBrowserDialog], None]',
        FFlagBrowserDialog.__dict__['_add_selected'],
    )
    callback(dialog)


def _display_value(value: str | None) -> str:
    callback = cast(
        'Callable[[str | None], str]',
        FFlagBrowserDialog.__dict__['_display_value'],
    )
    return callback(value)


def _settings_urls() -> tuple[str, ...]:
    callback = cast(
        'Callable[[], tuple[str, ...]]',
        getattr(FFlagBrowserDialog, '_settings_urls'),
    )
    return callback()


def _fetch_flags(dialog: FFlagBrowserDialog) -> None:
    callback = cast(
        'Callable[[FFlagBrowserDialog], None]',
        FFlagBrowserDialog.__dict__['_fetch_flags'],
    )
    callback(dialog)


def _write_cache(flags: BrowserFlags, *, now: float | None = None) -> None:
    callback = cast(
        'Callable[..., None]',
        getattr(FFlagBrowserDialog, '_write_cache'),
    )
    callback(flags, now=now)


def _read_cache(*, now: float | None = None) -> BrowserFlags | None:
    callback = cast(
        'Callable[..., BrowserFlags | None]',
        getattr(FFlagBrowserDialog, '_read_cache'),
    )
    return callback(now=now)


def _refresh(dialog: FFlagBrowserDialog, *, force: bool = False) -> None:
    callback = cast(
        'Callable[..., None]',
        FFlagBrowserDialog.__dict__['_refresh'],
    )
    callback(dialog, force=force)


def _dialog_table(dialog: FFlagBrowserDialog) -> QTableWidget:
    return cast('QTableWidget', dialog.__dict__['_table'])


def _dialog_count(dialog: FFlagBrowserDialog) -> QLabel:
    return cast('QLabel', dialog.__dict__['_count'])


def _dialog_search(dialog: FFlagBrowserDialog) -> QLineEdit:
    return cast('QLineEdit', dialog.__dict__['_search'])


def _dialog_family_filter(dialog: FFlagBrowserDialog) -> QComboBox:
    return cast('QComboBox', dialog.__dict__['_family_filter'])


def _dialog_flags(dialog: FFlagBrowserDialog) -> BrowserFlags:
    return cast('BrowserFlags', dialog.__dict__['_flags'])


def _settings_url() -> str:
    return cast('str', FFlagBrowserDialog.__dict__['_SETTINGS_URL'])


def _bypass_header() -> dict[str, str]:
    return cast('dict[str, str]', FFlagBrowserDialog.__dict__['_BYPASS_CUSTOM_FFLAGS_HEADER'])


def _tracker_url(name: str) -> str:
    return cast('str', FFlagBrowserDialog.__dict__[name])


def _translations() -> dict[str, dict[str, str]]:
    return cast('dict[str, dict[str, str]]', localization.__dict__['_TRANSLATIONS'])


def _empty_cache(_cls: type[FFlagBrowserDialog]) -> BrowserFlags:
    return {'DFIntTaskSchedulerTargetFps': None}


def _record_fetch(values: list[bool]) -> None:
    values.append(True)


def _no_timer(*_args: object) -> None:
    return None


def _refresh_proxy_noop() -> None:
    return None


def _editor_table(editor: CustomFFlagEditor) -> QTableWidget:
    return cast('QTableWidget', editor.__dict__['_table'])


def _add_flag_source() -> str:
    callback = cast('Callable[..., object]', CustomFFlagEditor.__dict__['_add_flag'])
    return inspect.getsource(callback)


def test_fflag_browser_reports_retrieved_and_filtered_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _qapp()
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _noop_refresh)
    dialog = FFlagBrowserDialog()
    _apply_flags(
        dialog,
        {
            'DFFlagAlpha': 'True',
            'DFFlagBeta': 'False',
            'DFIntTargetFps': '60',
            'FFlagGamma': 'True',
            'FIntLevel': '2',
            'UnclassifiedFlag': 'enabled',
        },
    )

    assert _dialog_count(dialog).text() == 'Showing 6 FastFlags • 6 retrieved from Roblox'
    assert _dialog_table(dialog).columnCount() == 2
    assert _dialog_family_filter(dialog).minimumWidth() == 165

    _dialog_family_filter(dialog).setCurrentIndex(_dialog_family_filter(dialog).findData('DFFlag'))
    assert _dialog_count(dialog).text() == 'Showing 2 FastFlags • 6 retrieved from Roblox'

    _dialog_search(dialog).setText('beta')
    assert _dialog_count(dialog).text() == 'Showing 1 FastFlags • 6 retrieved from Roblox'
    assert app is not None


def test_fflag_browser_extracts_current_values_and_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _qapp()
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _noop_refresh)
    assert _extract_flags(
        {'applicationSettings': {'FFlagExample': True, 'DFIntLimit': 120, 'skip': []}}
    ) == {'FFlagExample': 'True', 'DFIntLimit': '120'}
    with pytest.raises(ValueError, match='application FastFlags'):
        _extract_flags({})

    dialog = FFlagBrowserDialog()
    _apply_flags(dialog, {'FFlagExample': 'True'})
    _dialog_table(dialog).selectRow(0)
    app.processEvents()
    _add_selected(dialog)

    assert dialog.selected_flags == {'FFlagExample': 'True'}


def test_fflag_browser_extracts_tracker_only_fastvariables() -> None:
    assert _extract_tracker_flags(
        b'[C++] DFFlagDebugDrawBroadPhaseAABBs\n'
        b'[C++] DFIntTaskSchedulerTargetFps\n'
        b'[C++] NotAFastVariable\n'
    ) == {
        'DFFlagDebugDrawBroadPhaseAABBs': None,
        'DFIntTaskSchedulerTargetFps': None,
    }


def test_fflag_browser_adds_tracker_only_fastvariables_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _qapp()
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _noop_refresh)
    dialog = FFlagBrowserDialog()
    _apply_flags(dialog, {'DFIntTaskSchedulerTargetFps': None})
    _dialog_table(dialog).selectRow(0)
    app.processEvents()
    _add_selected(dialog)

    assert dialog.selected_flags == {'DFIntTaskSchedulerTargetFps': ''}


def test_fflag_browser_translates_unpublished_display_without_changing_none_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _qapp()
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _noop_refresh)
    pseudo = dict(localization.ENGLISH)
    pseudo['modifications.fastflags.no_value'] = '⟦no-value⟧'
    monkeypatch.setitem(_translations(), 'zz', pseudo)
    try:
        localization.set_language('zz')
        dialog = FFlagBrowserDialog()
        _apply_flags(dialog, {'DFIntTaskSchedulerTargetFps': None})

        assert _dialog_flags(dialog)['DFIntTaskSchedulerTargetFps'] is None
        assert _display_value(None) == '⟦no-value⟧'
        item = _dialog_table(dialog).item(0, 1)
        assert item is not None
        assert item.text() == '⟦no-value⟧'

        _dialog_search(dialog).setText('⟦no-value⟧')
        assert not _dialog_table(dialog).isRowHidden(0)
        assert app is not None
    finally:
        localization.set_language('en')


def test_fflag_browser_merges_live_values_with_the_tracker_lists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _qapp()
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _noop_refresh)
    monkeypatch.setattr(FFlagBrowserDialog, '_CACHE_PATH', tmp_path / 'fflag_browser.json')
    dialog = FFlagBrowserDialog()
    fetched: list[BrowserFlags] = []

    def record_flags(flags: object) -> None:
        fetched.append(cast('BrowserFlags', flags))

    dialog.flags_loaded.connect(record_flags)

    def fake_http_get(url: str, timeout: int = 15, headers: dict[str, str] | None = None) -> bytes:
        if url == _settings_url():
            assert headers == _bypass_header()
            return b'{"applicationSettings":{"DFFlagDebugDrawBroadPhaseAABBs":"False"}}'
        if url in _settings_urls():
            assert headers == _bypass_header()
            return b'{"applicationSettings":{"DFIntTaskSchedulerTargetFps":"60"}}'
        if url == _tracker_url('_TRACKER_VARIABLES_URL'):
            return b'[C++] DFFlagDebugDrawBroadPhaseAABBs\n'
        if url == _tracker_url('_HISTORICAL_TRACKER_VARIABLES_URL'):
            return b'[C++] DFIntTaskSchedulerTargetFps\n'
        msg = f'unexpected URL: {url}'
        raise AssertionError(msg)

    monkeypatch.setattr(modifications_tab, 'http_get', fake_http_get)
    _fetch_flags(dialog)

    assert fetched == [
        {
            'DFFlagDebugDrawBroadPhaseAABBs': 'False',
            'DFIntTaskSchedulerTargetFps': None,
        }
    ]
    assert app is not None


def test_fflag_browser_cache_expires_after_one_hour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_path = tmp_path / 'fflag_browser.json'
    monkeypatch.setattr(FFlagBrowserDialog, '_CACHE_PATH', cache_path)
    flags = {'DFFlagDebugDrawBroadPhaseAABBs': None, 'FFlagExample': 'True'}

    _write_cache(flags, now=10_000)

    assert _read_cache(now=13_599) == flags
    assert _read_cache(now=13_600) is None


def test_fflag_browser_refresh_bypasses_a_fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _qapp()
    cached_flags: BrowserFlags = {'DFIntTaskSchedulerTargetFps': None}
    monkeypatch.setattr(
        FFlagBrowserDialog,
        '_read_cache',
        classmethod(_empty_cache),
    )
    dialog = FFlagBrowserDialog()
    assert _dialog_flags(dialog) == cached_flags
    assert _dialog_count(dialog).text().endswith('cached')

    fetched: list[bool] = []
    monkeypatch.setattr(dialog, '_fetch_flags', lambda: _record_fetch(fetched))

    class ImmediateThread:
        def __init__(self, *, target: Callable[[], None], daemon: bool) -> None:
            del daemon
            self._target = target

        def start(self) -> None:
            self._target()

    monkeypatch.setattr(modifications_tab.threading, 'Thread', ImmediateThread)
    _refresh(dialog, force=True)

    assert fetched == [True]
    assert app is not None


def test_custom_fflag_editor_uses_an_edit_on_demand_boolean_selector() -> None:
    app = _qapp()
    config = cast(
        'ConfigManager',
        SimpleNamespace(
            custom_fflags={'FFlagExample': 'True', 'DFIntExample': '60'},
            custom_fflags_enabled=False,
        ),
    )
    proxy = cast(
        'ProxyMaster',
        SimpleNamespace(refresh_custom_fflag_interception=_refresh_proxy_noop),
    )

    editor = CustomFFlagEditor(config, proxy)

    assert _editor_table(editor).rowCount() == 2
    assert _editor_table(editor).cellWidget(0, 1) is None
    assert isinstance(_editor_table(editor).itemDelegateForColumn(1), FastFlagValueDelegate)
    assert app is not None



def test_boolean_value_cell_click_opens_selector_even_after_row_selection() -> None:
    app = _qapp()
    config = cast(
        'ConfigManager',
        SimpleNamespace(custom_fflags={'DFFlagExample': 'False'}, custom_fflags_enabled=False),
    )
    proxy = cast(
        'ProxyMaster',
        SimpleNamespace(refresh_custom_fflag_interception=_refresh_proxy_noop),
    )
    editor = CustomFFlagEditor(config, proxy)
    editor.show()
    app.processEvents()
    table = _editor_table(editor)
    table.selectRow(0)
    index = table.model().index(0, 1)

    table.clicked.emit(index)
    app.processEvents()

    assert any(combo.isVisible() for combo in table.findChildren(CompactBooleanComboBox))


def test_grouped_boolean_flag_uses_canonical_name_for_delegate() -> None:
    app = _qapp()
    config = cast(
        'ConfigManager',
        SimpleNamespace(
            custom_fflags={'DFFlagExample': 'False'},
            custom_fflags_enabled=False,
            custom_fflag_folders={'Visual': ['DFFlagExample']},
        ),
    )
    proxy = cast(
        'ProxyMaster',
        SimpleNamespace(refresh_custom_fflag_interception=_refresh_proxy_noop),
    )
    editor = CustomFFlagEditor(config, proxy)
    table = _editor_table(editor)
    index = table.model().index(1, 1)
    delegate = table.itemDelegateForColumn(1)

    combo = delegate.createEditor(table.viewport(), QStyleOptionViewItem(), index)

    assert isinstance(combo, CompactBooleanComboBox)
    assert app is not None


def test_flag_value_mutation_does_not_refresh_interception_routes() -> None:
    app = _qapp()
    config = cast(
        'ConfigManager',
        SimpleNamespace(custom_fflags={'DFFlagExample': 'False'}, custom_fflags_enabled=False),
    )
    refresh_calls: list[None] = []

    def refresh() -> None:
        refresh_calls.append(None)

    proxy = cast('ProxyMaster', SimpleNamespace(refresh_custom_fflag_interception=refresh))
    editor = CustomFFlagEditor(config, proxy)

    editor._set_flags({'DFFlagExample': 'True', 'DFIntExample': '60'})  # pyright: ignore[reportPrivateUsage]

    assert refresh_calls == []
    assert app is not None

def test_boolean_fflag_picker_commits_and_closes_after_selection() -> None:
    app = _qapp()
    table = QTableWidget(1, 2)
    table.setItem(0, 0, QTableWidgetItem('FFlagExample'))
    table.setItem(0, 1, QTableWidgetItem('True'))
    delegate = FastFlagValueDelegate(table)
    index = table.model().index(0, 1)
    combo = cast(
        'QComboBox', delegate.createEditor(table.viewport(), QStyleOptionViewItem(), index)
    )
    committed: list[object] = []
    closed: list[tuple[object, object]] = []

    def record_committed(editor: object) -> None:
        committed.append(editor)

    def record_closed(editor: object, hint: object) -> None:
        closed.append((editor, hint))

    delegate.commitData.connect(record_committed)
    delegate.closeEditor.connect(record_closed)

    combo.activated.emit(1)

    assert committed == [combo]
    assert closed[0][0] is combo
    assert app is not None


def test_boolean_fflag_editor_reads_canonical_user_role_not_translated_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _qapp()
    table = QTableWidget(1, 2)
    table.setItem(0, 0, QTableWidgetItem('FFlagExample'))
    value_item = QTableWidgetItem('Vrai')
    value_item.setData(Qt.ItemDataRole.UserRole, 'True')
    table.setItem(0, 1, value_item)
    delegate = FastFlagValueDelegate(table)
    index = table.model().index(0, 1)
    combo = cast(
        'QComboBox', delegate.createEditor(table.viewport(), QStyleOptionViewItem(), index)
    )
    monkeypatch.setattr(modifications_tab.QTimer, 'singleShot', _no_timer)

    delegate.setEditorData(combo, index)

    assert combo.currentData() == 'True'
    assert app is not None


def test_add_custom_boolean_fflag_uses_combo_item_data_not_translated_text() -> None:
    source = _add_flag_source()
    assert 'value_combo.currentData()' in source
    assert 'value_combo.currentText()' not in source


def test_custom_fflag_editor_renders_and_toggles_folder_rows() -> None:
    app = _qapp()
    config = cast(
        'ConfigManager',
        SimpleNamespace(
            custom_fflags={'FFlagOne': 'True', 'FFlagTwo': 'False'},
            custom_fflags_enabled=True,
            custom_fflag_disabled=[],
            custom_fflag_keybinds={},
            custom_fflag_folders={'Visual': ['FFlagOne', 'FFlagTwo']},
            custom_fflag_disabled_folders=[],
            custom_fflag_folder_keybinds={},
        ),
    )
    proxy = cast(
        'ProxyMaster',
        SimpleNamespace(refresh_custom_fflag_interception=_refresh_proxy_noop),
    )

    editor = CustomFFlagEditor(config, proxy)
    table = _editor_table(editor)

    assert table.rowCount() == 3
    folder_name = table.item(0, 0)
    folder_value = table.item(0, 1)
    first_flag = table.item(1, 0)
    second_flag = table.item(2, 0)
    folder_status = table.item(0, 2)
    assert folder_name is not None
    assert folder_value is not None
    assert first_flag is not None
    assert second_flag is not None
    assert folder_status is not None
    assert folder_name.text() == 'Visual'
    assert folder_value.text() == '2 FastFlags'
    assert first_flag.text().strip() == 'FFlagOne'
    assert second_flag.text().strip() == 'FFlagTwo'

    folder_status.setCheckState(Qt.CheckState.Unchecked)
    app.processEvents()

    assert config.custom_fflag_disabled_folders == ['Visual']
    assert config.custom_fflag_disabled == []
    assert app is not None



def test_custom_fflag_toolbar_uses_compact_json_menu_and_custom_actions_button() -> None:
    app = _qapp()
    config = cast(
        'ConfigManager',
        SimpleNamespace(
            custom_fflags={},
            custom_fflags_enabled=False,
            custom_fflag_disabled=[],
            custom_fflag_keybinds={},
            custom_fflag_folders={},
            custom_fflag_disabled_folders=[],
            custom_fflag_folder_keybinds={},
            custom_fflag_actions={},
        ),
    )
    proxy = cast(
        'ProxyMaster',
        SimpleNamespace(refresh_custom_fflag_interception=_refresh_proxy_noop),
    )
    editor = CustomFFlagEditor(config, proxy)

    button_texts = {button.text() for button in editor.findChildren(QPushButton)}

    assert 'JSON' in button_texts
    assert 'Custom Actions' in button_texts
    assert 'Import JSON…' not in button_texts
    assert 'Export JSON…' not in button_texts
    assert app is not None


def test_custom_actions_keybind_column_double_click_assigns_hotkey() -> None:
    app = _qapp()
    captured: list[str] = []
    binding: dict[str, int | bool | str] = {
        'scan_code': 0x10,
        'extended': False,
        'modifiers': 0,
    }

    def capture(name: str) -> tuple[bool, dict[str, int | bool | str] | None]:
        captured.append(name)
        return True, binding

    dialog = modifications_tab.FastFlagActionsDialog(
        {'90 FPS': {'flags': {'DFIntTaskSchedulerTargetFps': '90'}}},
        {},
        capture,
    )

    dialog._table.cellDoubleClicked.emit(0, 2)  # pyright: ignore[reportPrivateUsage]
    app.processEvents()

    assert captured == ['90 FPS']
    assert dialog.action_definitions['90 FPS'].get('keybind') == binding
    button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
    assert 'Assign Hotkey…' not in button_texts
    assert 'Clear Keybind' not in button_texts


def test_custom_actions_non_keybind_double_click_edits_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _qapp()
    dialog = modifications_tab.FastFlagActionsDialog(
        {'90 FPS': {'flags': {'DFIntTaskSchedulerTargetFps': '90'}}},
        {},
        lambda _name: (False, None),
    )
    edited: list[None] = []

    def edit_action(_row: int | None = None, _column: int | None = None) -> None:
        edited.append(None)

    monkeypatch.setattr(dialog, '_edit_action', edit_action)

    dialog._table.cellDoubleClicked.emit(0, 1)  # pyright: ignore[reportPrivateUsage]
    app.processEvents()

    assert edited == [None]
