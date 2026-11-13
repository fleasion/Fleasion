"""The custom name warning and runtime expansion share both available inventories."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from fleasion.gui import modifications_tab
from fleasion.gui.modifications_tab import CustomFFlagEditor, CustomFFlagNameDelegate
from fleasion.modifications import fflag_static
from fleasion.modifications.fflag_static import (
    ClientBinary,
    StaticFlagCatalog,
    StaticRecord,
)
from fleasion.utils import fflag_resolution
from fleasion.utils.fflag_resolution import (
    expand_unprefixed_fflags,
    installed_name_evidence,
    known_typed_names,
    known_untyped_names,
    qualified_matches,
)

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.master import ProxyMaster


@pytest.fixture
def catalog_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ClientBinary:
    """Two genuinely independent cached inventories and one source binary."""
    scan_dir = tmp_path / 'scans'
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', scan_dir)
    monkeypatch.setattr(fflag_resolution, '_SCAN_CACHE_DIR', scan_dir)
    binary = tmp_path / 'sober.apk'
    binary.write_bytes(b'original Sober binary')
    source = ClientBinary('sober', 'Sober', binary, member='lib/x86_64/libroblox.so')
    monkeypatch.setattr(fflag_resolution, '_selected_player_binary', lambda: source)
    stat = binary.stat()
    fflag_static.save_scan(
        StaticFlagCatalog(
            'sober',
            'Sober',
            str(binary),
            {
                'DFIntExtraFloorRaycastDistanceHipHeightPercent': None,
                'DFIntShared': None,
            },
            frozenset(
                {'DFIntExtraFloorRaycastDistanceHipHeightPercent', 'DFIntShared'}
            ),
            3,
            1,
            time.time(),
            stat.st_size,
            stat.st_mtime_ns,
            source.member,
            registrations=(
                StaticRecord(
                    'ExtraFloorRaycastDistanceHipHeightPercent', 2, 'numeric', True
                ),
                StaticRecord('Shared', 2, 'numeric', True),
                StaticRecord('UntypedInBinary', None, 'unknown', False),
            ),
        )
    )
    public_cache = tmp_path / 'public.json'
    public_cache.write_text(
        json.dumps(
            {
                'version': 3,
                'fetched_at': time.time(),
                'flags': {
                    'FLogShared': None,
                    'FIntOnlyPublic': None,
                    'FFlagFullyQualified': None,
                },
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(fflag_resolution, '_BROWSER_CACHE_PATH', public_cache)
    return source


def test_selected_sober_exact_name_is_accepted_and_sent_to_correct_type(
    catalog_sources: ClientBinary,
) -> None:
    key = 'ExtraFloorRaycastDistanceHipHeightPercent'
    names = known_typed_names()
    assert names is not None
    assert qualified_matches(key, names) == ('DFInt' + key,)
    assert 'DFInt' + key in names
    assert 'FIntOnlyPublic' in names
    assert 'FLogShared' in names
    assert installed_name_evidence(catalog_sources) is not None
    assert 'UntypedInBinary' in known_untyped_names()
    assert expand_unprefixed_fflags({key: '1000000'}) == {'DFInt' + key: '1000000'}
    from fleasion.proxy.addons.custom_fflags import CustomFFlagModifier

    config = SimpleNamespace(
        custom_fflags={key: '1000000'},
        custom_fflags_enabled=True,
        custom_fflag_disabled=[],
        custom_fflag_folders={},
        custom_fflag_disabled_folders=[],
    )
    live = CustomFFlagModifier(config).runtime_flags()
    assert [name for name in live if name.endswith(key)] == ['DFInt' + key]
    assert live['DFInt' + key] == '1000000'
    assert config.custom_fflags == {key: '1000000'}
    assert expand_unprefixed_fflags({'Shared': '9'}) == {
        'DFIntShared': '9',
        'FLogShared': '9',
    }


def test_warning_white_for_ambiguous_or_untyped_orange_only_for_missing(
    catalog_sources: ClientBinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    config = cast(
        'ConfigManager',
        SimpleNamespace(
            custom_fflags={
                'ExtraFloorRaycastDistanceHipHeightPercent': '1000000',
                'Shared': '9',
                'UntypedInBinary': '8',
                'DoesNotExistAnywhere': '7',
                'DFIntNotActuallyInBinary': '3',
                'FIntOnlyPublic': '1',
            },
            custom_fflags_enabled=False,
        ),
    )
    proxy = cast(
        'ProxyMaster',
        SimpleNamespace(
            refresh_custom_fflag_interception=lambda: None,
        ),
    )
    editor = CustomFFlagEditor(config, proxy)
    try:
        table = editor._table  # pyright: ignore[reportPrivateUsage]
        delegate = table.itemDelegateForColumn(0)
        assert isinstance(delegate, CustomFFlagNameDelegate)
        statuses = {
            editor._row_name(row): delegate._warning_kind(table.model().index(row, 0))  # pyright: ignore[reportPrivateUsage]
            for row in range(table.model().rowCount())
        }
        assert statuses == {
            'ExtraFloorRaycastDistanceHipHeightPercent': None,
            'Shared': 'multiple',
            'UntypedInBinary': 'untyped',
            'DoesNotExistAnywhere': 'missing',
            'DFIntNotActuallyInBinary': 'missing',
            'FIntOnlyPublic': None,
        }
        messages: list[str] = []

        def tooltip(_position: object, value: str, _parent: object) -> None:
            messages.append(value)

        monkeypatch.setattr(modifications_tab.QToolTip, 'showText', tooltip)
        editor.show()
        app.processEvents()
        for flag, expected in (
            ('Shared', 'multiple types'),
            ('UntypedInBinary', 'type is unknown'),
            ('DoesNotExistAnywhere', 'No matching FastFlag'),
        ):
            row = next(
                r
                for r in range(table.rowCount())
                if table.model().index(r, 0).data() == flag
            )
            index = table.model().index(row, 0)
            rect = table.visualRect(index)
            option = QStyleOptionViewItem()
            option.rect = rect
            position = delegate._warning_rect(rect).center()  # pyright: ignore[reportPrivateUsage]
            event = QHelpEvent(
                QEvent.Type.ToolTip, position, table.viewport().mapToGlobal(position)
            )
            assert delegate.helpEvent(event, table, option, index)
            assert expected in messages[-1]
    finally:
        editor.close()


def test_changed_native_source_is_not_mistaken_for_same_installed_build(
    catalog_sources: ClientBinary,
) -> None:
    assert 'DFIntExtraFloorRaycastDistanceHipHeightPercent' in (
        known_typed_names() or ()
    )
    catalog_sources.path.write_bytes(
        b'new binary, same location but different contents'
    )
    assert installed_name_evidence() is None
    names = known_typed_names()
    assert names is not None
    assert 'DFIntExtraFloorRaycastDistanceHipHeightPercent' not in names
    assert 'FLogShared' in names


def test_public_inventory_stays_usable_without_an_installed_snapshot(
    catalog_sources: ClientBinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fflag_resolution, '_selected_player_binary', lambda: None)
    names = known_typed_names()
    assert names is not None
    assert 'FIntOnlyPublic' in names
    assert 'DFIntExtraFloorRaycastDistanceHipHeightPercent' not in names
    assert known_untyped_names() == frozenset()


def test_selected_client_switch_does_not_use_another_clients_type_cache(
    catalog_sources: ClientBinary,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    other_file = tmp_path / 'nightcap.so'
    other_file.write_bytes(b'nightcap bytes')
    stat = other_file.stat()
    nightcap = ClientBinary('nightcap', 'Nightcap', other_file)
    fflag_static.save_scan(
        StaticFlagCatalog(
            'nightcap',
            'Nightcap',
            str(other_file),
            {'FFlagNightcapOnly': None},
            frozenset({'FFlagNightcapOnly'}),
            1,
            0,
            time.time(),
            stat.st_size,
            stat.st_mtime_ns,
            None,
            registrations=(StaticRecord('NightcapOnly', 1, 'bool', True),),
        )
    )
    monkeypatch.setattr(fflag_resolution, '_selected_player_binary', lambda: nightcap)
    names = known_typed_names()
    assert names is not None
    assert 'FFlagNightcapOnly' in names
    assert 'DFIntExtraFloorRaycastDistanceHipHeightPercent' not in names
    assert 'FIntOnlyPublic' in names
