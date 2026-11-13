"""Release-facing Windows static slot scanner and FVariables hint integration."""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest

from fleasion.modifications.fflag_windows_catalog import (
    WindowsStaticFlagCatalog,
    display_initial_value,
    parse_fvariables,
    scan_windows_player,
    selectable_qualified_name,
)


def _fixture() -> bytes:
    """A coherent x64 PE with independent synthetic 24-byte slot records."""
    data = bytearray(0x6000)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3C, 0x100)
    data[0x100:0x104] = b'PE\0\0'
    struct.pack_into('<HH', data, 0x104, 0x8664, 2)
    struct.pack_into('<H', data, 0x114, 240)
    struct.pack_into('<H', data, 0x118, 0x20B)
    base = 0x140000000
    struct.pack_into('<Q', data, 0x130, base)
    for index, (name, rva, off, size, flags) in enumerate(
        (
            ('.rdata', 0x3000, 0x400, 0x2400, 0x40000040),
            ('.data', 0x6000, 0x3000, 0x1800, 0xC0000040),
        )
    ):
        offset = 0x118 + 240 + index * 40
        data[offset : offset + 8] = name.encode().ljust(8, b'\0')
        struct.pack_into('<IIII', data, offset + 8, size, rva, size, off)
        struct.pack_into('<I', data, offset + 36, flags)
    values = (0, 1, 0xFFFFFFFF, 0xFFFFFFFB, 0x1234567800000000, 0x3F800000)
    for index in range(36):
        name = f'StaticExample{index:02d}'.encode()
        name_at = 0x400 + index * 32
        data[name_at : name_at + len(name) + 1] = name + b'\0'
        value = values[index] if index < len(values) else index
        struct.pack_into(
            '<QQQ',
            data,
            0x3000 + index * 24,
            value,
            base + 0x3000 + index * 32,
            len(name),
        )
    return bytes(data)


@pytest.fixture
def fake_exe(tmp_path: Path) -> Path:
    """Create a local, never-executed synthetic Player EXE."""
    path = tmp_path / 'RobloxPlayerBeta.exe'
    path.write_bytes(_fixture())
    return path


def _hints() -> frozenset[str]:
    return parse_fvariables(
        b'[C++] DFIntStaticExample00\n'
        b'[C++] FFlagStaticExample01\n'
        b'FIntStaticExample02\n'
        b'DFFlagStaticExample03\n'
        b'FFlagStaticExample06\n'
        b'DFIntStaticExample06\n'
        b'[Lua] FIntStaticExample07\n'
        b'DFStringStaticExample08\n'
        b'FIntStaticExample09\n'
    )


def test_fvariables_parser_accepts_annotated_names_and_rejects_noise() -> None:
    parsed = parse_fvariables(
        b'[C++] DFIntStaticExample00\n'
        b'[Lua] FFlagStaticExample01\n'
        b'\xef\xbb\xbfFIntStaticExample02\n'
        b'FFlag\n'
        b'SFFlagNotAPlayerRecord\n'
        b'FFlagMalformed-Name\n'
        b'[C++] UnknownUnqualified\n'
        b'FStringStaticExample09\n'
        b'FStringStaticExample09\n'
    )
    assert parsed == {
        'DFIntStaticExample00',
        'FFlagStaticExample01',
        'FStringStaticExample09',
    }
    with pytest.raises(ValueError, match='UTF-8'):
        parse_fvariables(b'\xff')
    with pytest.raises(ValueError, match='size'):
        parse_fvariables(b'F' * (16 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match='too many'):
        parse_fvariables(b'\n' * 200_001)


def test_all_slots_retained_when_type_inventory_missing(fake_exe: Path) -> None:
    catalog = scan_windows_player(fake_exe)
    assert isinstance(catalog, WindowsStaticFlagCatalog)
    assert len(catalog.entries) == 36
    assert len(catalog.sha256) == 64
    assert catalog.hint_source == 'none'
    assert catalog.hinted == 0
    assert all(selectable_qualified_name(slot) is None for slot in catalog.entries)
    assert all(slot.scalar_kind == 'untyped' for slot in catalog.entries)
    assert display_initial_value(catalog.entries[0]) == '0x0000000000000000'
    assert display_initial_value(catalog.entries[1]) == '0x0000000000000001'


def test_unambiguous_tracker_hints_are_explicitly_unverified(fake_exe: Path) -> None:
    catalog = scan_windows_player(
        fake_exe, qualified_type_hints=_hints(), hint_source='fvariables'
    )
    assert len(catalog.entries) == 36
    assert catalog.hint_source == 'fvariables'
    slots = {entry.name: entry for entry in catalog.entries}
    assert catalog.hinted == 5
    assert selectable_qualified_name(slots['StaticExample00']) == 'DFIntStaticExample00'
    assert slots['StaticExample00'].scalar_kind == 'int'
    assert type(slots['StaticExample00'].scalar_hint) is int
    assert display_initial_value(slots['StaticExample00']) == '0'
    assert selectable_qualified_name(slots['StaticExample01']) == 'FFlagStaticExample01'
    assert slots['StaticExample01'].scalar_kind == 'bool'
    assert display_initial_value(slots['StaticExample01']) == 'True'
    assert display_initial_value(slots['StaticExample02']) == '-1'
    assert slots['StaticExample02'].scalar_kind == 'int'
    assert slots['StaticExample03'].scalar_kind == 'untyped'
    assert slots['StaticExample04'].scalar_kind == 'untyped'
    assert display_initial_value(slots['StaticExample04']) == '0x1234567800000000'
    assert slots['StaticExample06'].qualifier_hint is None
    assert selectable_qualified_name(slots['StaticExample06']) is None
    assert slots['StaticExample08'].scalar_kind == 'untyped'


def test_unsupported_pe_and_truncated_table_fail_closed(fake_exe: Path) -> None:
    fake_exe.write_bytes(b'not a Player')
    with pytest.raises(ValueError, match='Unsupported|oversized'):
        scan_windows_player(fake_exe)
    data = bytearray(_fixture())
    for index in range(3, 36):
        struct.pack_into('<Q', data, 0x3008 + index * 24, 0)
    fake_exe.write_bytes(data)
    with pytest.raises(ValueError, match='Unsupported'):
        scan_windows_player(fake_exe)


def test_scan_refuses_changed_file_during_read(
    fake_exe: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_read = Path.read_bytes

    def replace_after_read(file: Path) -> bytes:
        result = original_read(file)
        if file == fake_exe:
            file.write_bytes(b'changed')
        return result

    monkeypatch.setattr(Path, 'read_bytes', replace_after_read)
    with pytest.raises(ValueError, match='changed'):
        scan_windows_player(fake_exe)


@pytest.mark.parametrize('version', ('0.745', '0.746', '0.747'))
def test_historical_current_windows_table_matches_real_binary(version: str) -> None:
    p = Path(
        os.environ.get(
            'FLEASION_WINDOWS_HISTORY_DIR', '/tmp/fleasion-windows-historical'
        )
    )
    exe = p / version / 'RobloxPlayerBeta.exe'
    if not exe.is_file():
        pytest.skip(f'Original {version} Player archive unavailable')
    result = scan_windows_player(exe)
    assert 13_000 < len(result.entries) < 14_000
    target = next(
        slot for slot in result.entries if slot.name == 'TaskSchedulerTargetFps'
    )
    assert target.raw_u64 == 0
    assert target.scalar_kind == 'untyped'
    assert result.hinted == 0


def test_gui_local_scan_separates_unknowns_from_actionable_hints(
    fake_exe: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication

    from fleasion.gui.modifications_tab import FFlagBrowserDialog

    app = QApplication.instance() or QApplication([])

    def no_refresh(self: FFlagBrowserDialog, force: bool = False) -> None:
        del self, force

    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', no_refresh)
    browser = FFlagBrowserDialog()
    try:
        browser._local_exe = fake_exe  # pyright: ignore[reportPrivateUsage]
        browser._source_mode.setCurrentIndex(1)  # pyright: ignore[reportPrivateUsage]
        assert browser._source_mode.currentData() == 'windows'  # pyright: ignore[reportPrivateUsage]
        catalog = scan_windows_player(
            fake_exe, qualified_type_hints=_hints(), hint_source='fvariables'
        )
        browser._apply_local_scan((browser._local_generation, catalog))  # pyright: ignore[reportPrivateUsage]
        assert browser._table.rowCount() == 36  # pyright: ignore[reportPrivateUsage]
        assert browser._local_catalog is catalog  # pyright: ignore[reportPrivateUsage]
        labels = list(browser._flags)  # pyright: ignore[reportPrivateUsage]
        untyped = next(
            i for i, label in enumerate(labels) if label.startswith('[type unknown]')
        )
        browser._table.selectRow(untyped)  # pyright: ignore[reportPrivateUsage]
        app.processEvents()
        assert not browser._add_button.isEnabled()  # pyright: ignore[reportPrivateUsage]
        browser._add_selected()  # pyright: ignore[reportPrivateUsage]
        assert browser.selected_flags == {}
        typed = labels.index('DFIntStaticExample00')
        browser._table.selectRow(typed)  # pyright: ignore[reportPrivateUsage]
        app.processEvents()
        assert browser._add_button.isEnabled()  # pyright: ignore[reportPrivateUsage]
        browser._add_selected()  # pyright: ignore[reportPrivateUsage]
        assert browser.selected_flags == {'DFIntStaticExample00': '0'}
    finally:
        browser.close()


def test_gui_optional_inventory_request_failure_leaves_raw_slots(
    fake_exe: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication

    from fleasion.gui import modifications_tab
    from fleasion.gui.modifications_tab import FFlagBrowserDialog

    app = QApplication.instance() or QApplication([])

    def no_refresh(self: FFlagBrowserDialog, force: bool = False) -> None:
        del self, force

    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', no_refresh)
    browser = FFlagBrowserDialog()
    try:
        browser._local_exe = fake_exe  # pyright: ignore[reportPrivateUsage]
        browser._source_mode.setCurrentIndex(1)  # pyright: ignore[reportPrivateUsage]

        def no_network(*_args: object, **_kwargs: object) -> bytes:
            raise OSError('offline')

        monkeypatch.setattr(modifications_tab, 'http_get', no_network)
        browser._fetch_local_scan(browser._local_generation, fake_exe)  # pyright: ignore[reportPrivateUsage]
        app.processEvents()
        assert browser._local_catalog is not None  # pyright: ignore[reportPrivateUsage]
        assert browser._local_catalog.hinted == 0  # pyright: ignore[reportPrivateUsage]
        assert len(browser._flags) == 36  # pyright: ignore[reportPrivateUsage]
    finally:
        browser.close()


def test_offline_export_contains_provenance_and_inferred_types(
    fake_exe: Path, tmp_path: Path
) -> None:
    from scripts.export_windows_fastflags import main

    fvariables = tmp_path / 'FVariables.txt'
    fvariables.write_bytes(b'[C++] DFIntStaticExample00\nFFlagStaticExample01\n')
    output = tmp_path / 'exported.json'
    assert (
        main(
            [
                '--exe',
                str(fake_exe),
                '--fvariables',
                str(fvariables),
                '--output',
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding='utf-8'))
    assert payload['schema'] == 1
    assert payload['type_hint_source'] == 'fvariables'
    assert payload['record_count'] == 36
    assert payload['inferred_type_count'] == 2
    assert payload['runtime_defaults_verified'] == 0
    rows = {item['name']: item for item in payload['entries']}
    assert rows['StaticExample00']['initial_value_hint'] == '0'
    assert rows['StaticExample00']['raw_u64'] == '0x0000000000000000'
    assert rows['StaticExample00']['qualified_hint'] == 'DFIntStaticExample00'
    assert rows['StaticExample01']['initial_value_hint'] == 'True'
    assert rows['StaticExample09']['scalar_type_hint'] is None
    assert rows['StaticExample09']['selectable_qualified_name'] is None
    assert all(len(item['raw_u64']) == 18 for item in payload['entries'])


def test_export_cannot_overwrite_an_input_file(fake_exe: Path, tmp_path: Path) -> None:
    from scripts.export_windows_fastflags import main

    fvariables = tmp_path / 'FVariables.txt'
    fvariables.write_bytes(b'FFlagStaticExample01\n')
    exe_before = fake_exe.read_bytes()
    with pytest.raises(SystemExit) as error:
        main(['--exe', str(fake_exe), '--output', str(fake_exe)])
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        main(
            [
                '--exe',
                str(fake_exe),
                '--fvariables',
                str(fvariables),
                '--output',
                str(fvariables),
            ]
        )
    assert error.value.code == 2
    hardlink = tmp_path / 'alias-to-player.json'
    hardlink.hardlink_to(fake_exe)
    with pytest.raises(SystemExit) as error:
        main(['--exe', str(fake_exe), '--output', str(hardlink)])
    assert error.value.code == 2
    assert fake_exe.read_bytes() == exe_before
    assert fvariables.read_bytes() == b'FFlagStaticExample01\n'


def test_browser_ignores_stale_scans_and_invalidates_earlier_file_selection(
    fake_exe: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QFileDialog

    from fleasion.gui.modifications_tab import FFlagBrowserDialog

    app = QApplication.instance() or QApplication([])
    refresh_calls: list[bool] = []

    def record_refresh(_self: FFlagBrowserDialog, force: bool = False) -> None:
        refresh_calls.append(force)

    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', record_refresh)
    browser = FFlagBrowserDialog()
    try:
        browser._local_exe = fake_exe  # pyright: ignore[reportPrivateUsage]
        browser._refresh_button.setEnabled(False)  # pyright: ignore[reportPrivateUsage]
        browser._source_mode.setCurrentIndex(1)  # pyright: ignore[reportPrivateUsage]
        assert browser._refresh_button.isEnabled()  # pyright: ignore[reportPrivateUsage]
        current_generation = browser._local_generation  # pyright: ignore[reportPrivateUsage]
        catalog = scan_windows_player(fake_exe)
        browser._apply_local_scan((current_generation - 1, catalog))  # pyright: ignore[reportPrivateUsage]
        assert browser._local_catalog is None  # pyright: ignore[reportPrivateUsage]
        second = tmp_path / 'second.exe'
        second.write_bytes(fake_exe.read_bytes())

        def choose_second(*_args: object, **_kwargs: object) -> tuple[str, str]:
            return str(second), ''

        monkeypatch.setattr(QFileDialog, 'getOpenFileName', choose_second)
        browser._choose_windows_exe()  # pyright: ignore[reportPrivateUsage]
        assert browser._local_exe == second  # pyright: ignore[reportPrivateUsage]
        assert browser._local_generation > current_generation  # pyright: ignore[reportPrivateUsage]
        assert refresh_calls[-1] is True
        browser._apply_local_scan((current_generation, catalog))  # pyright: ignore[reportPrivateUsage]
        assert browser._local_catalog is None  # pyright: ignore[reportPrivateUsage]
        app.processEvents()
    finally:
        browser.close()
