"""Regression coverage for the original 24-hour shared FastFlag catalog workflow."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy

from fleasion.gui import modifications_tab
from fleasion.gui.modifications_tab import (
    CustomFFlagEditor,
    FFlagBrowserDialog,
    FFlagSection,
)
from fleasion.utils import platform_linux, platform_macos

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.master import ProxyMaster


@pytest.fixture
def qt_app() -> QApplication:
    """Keep Qt instances on the main test thread."""
    original = QApplication.instance()
    return original if isinstance(original, QApplication) else QApplication([])


def test_public_cache_is_reused_on_three_consecutive_browser_opens_without_network(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / 'fflag_browser.json'
    monkeypatch.setattr(FFlagBrowserDialog, '_CACHE_PATH', path)
    assert FFlagBrowserDialog._CACHE_TTL_SECONDS == 24 * 60 * 60  # pyright: ignore[reportPrivateUsage]
    assert FFlagBrowserDialog._write_cache({'DFFlagKnown': 'True', 'DFIntOther': '35'})  # pyright: ignore[reportPrivateUsage]

    def forbidden_network(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError(
            'Opening a fresh public catalog must not perform network I/O'
        )

    monkeypatch.setattr(modifications_tab, 'http_get', forbidden_network)
    for _ in range(3):
        dialog = FFlagBrowserDialog()
        try:
            assert dialog._cache_loaded  # pyright: ignore[reportPrivateUsage]
            assert dialog._table.rowCount() == 2  # pyright: ignore[reportPrivateUsage]
            assert 'cached' in dialog._count.text()  # pyright: ignore[reportPrivateUsage]
            assert 'Retriev' not in dialog._count.text()  # pyright: ignore[reportPrivateUsage]
        finally:
            dialog.close()
    assert FFlagBrowserDialog.cache_is_current()
    assert json.loads(path.read_text(encoding='utf-8'))['version'] == 3


def test_public_cache_lifetime_is_24_hours_and_stale_only_refreshes_once(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / 'fflag_browser.json'
    monkeypatch.setattr(FFlagBrowserDialog, '_CACHE_PATH', path)
    clock = time.time()
    assert FFlagBrowserDialog._write_cache({'FIntTest': '3'}, now=clock - 86_399)  # pyright: ignore[reportPrivateUsage]
    assert FFlagBrowserDialog.cache_is_current()
    assert FFlagBrowserDialog._write_cache({'FIntTest': '3'}, now=clock - 86_401)  # pyright: ignore[reportPrivateUsage]
    assert not FFlagBrowserDialog.cache_is_current()
    calls: list[int] = []

    def collect(_cls: type[FFlagBrowserDialog]) -> dict[str, str | None]:
        calls.append(1)
        return {'FIntTest': '9'}

    monkeypatch.setattr(FFlagBrowserDialog, '_collect_flags', classmethod(collect))
    assert FFlagBrowserDialog.refresh_cache_if_stale()
    assert not FFlagBrowserDialog.refresh_cache_if_stale()
    assert calls == [1]
    assert FFlagBrowserDialog._read_cache() == {'FIntTest': '9'}  # pyright: ignore[reportPrivateUsage]


def test_prefetch_runs_only_when_enabled_and_respects_failure_backoff(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = cast(
        'ConfigManager',
        SimpleNamespace(
            custom_fflags={},
            custom_fflags_enabled=False,
            custom_fflag_keybinds={},
            custom_fflag_folder_keybinds={},
            custom_fflag_actions={},
        ),
    )
    proxy = cast(
        'ProxyMaster',
        SimpleNamespace(
            refresh_custom_fflag_interception=lambda: None,
        ),
    )
    fresh = [False]
    calls: list[str] = []

    def current(_cls: type[FFlagBrowserDialog]) -> bool:
        return fresh[0]

    def refresh(_cls: type[FFlagBrowserDialog]) -> bool:
        calls.append('fetch')
        if len(calls) == 1:
            raise OSError('offline')
        fresh[0] = True
        return True

    class ImmediateThread:
        def __init__(self, *, target: object, daemon: bool) -> None:
            assert daemon
            self.target = cast('Callable[[], None]', target)

        def start(self) -> None:
            self.target()

    from collections.abc import Callable

    monkeypatch.setattr(FFlagBrowserDialog, 'cache_is_current', classmethod(current))
    monkeypatch.setattr(
        FFlagBrowserDialog, 'refresh_cache_if_stale', classmethod(refresh)
    )
    monkeypatch.setattr(modifications_tab.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(modifications_tab, 'known_typed_names', lambda: None)
    editor = CustomFFlagEditor(config, proxy)
    try:
        editor.prefetch_flag_catalog_if_needed()
        assert calls == []
        config.custom_fflags_enabled = True
        editor.prefetch_flag_catalog_if_needed()
        assert calls == ['fetch']
        editor.prefetch_flag_catalog_if_needed()
        assert calls == ['fetch']
        editor._catalog_retry_not_before = 0  # pyright: ignore[reportPrivateUsage]
        editor.prefetch_flag_catalog_if_needed()
        assert calls == ['fetch', 'fetch']
        editor.prefetch_flag_catalog_if_needed()
        assert calls == ['fetch', 'fetch']
    finally:
        editor.close()


def test_cache_cleanup_preserves_catalog_on_linux_and_macos(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name, module in (
        ('linux', platform_linux),
        ('macos', platform_macos),
    ):
        base = tmp_path / name
        base.mkdir()
        (base / 'predownloaded').mkdir()
        (base / 'predownloaded' / 'asset.dat').write_bytes(b'keep')
        (base / 'converted').mkdir()
        (base / 'converted' / 'delete.bin').write_bytes(b'gone')
        (base / 'fflag_browser.json').write_bytes(b'keep catalog')
        monkeypatch.setattr(module, 'APP_CACHE_DIR', base)
        if name == 'linux':
            platform_linux._clear_cache_directory_contents()  # pyright: ignore[reportPrivateUsage]
        else:
            platform_macos._delete_app_cache_contents()  # pyright: ignore[reportPrivateUsage]
        assert (base / 'fflag_browser.json').read_bytes() == b'keep catalog'
        assert (base / 'predownloaded' / 'asset.dat').is_file()
        assert not (base / 'converted').exists()


def test_warning_label_uses_its_own_height_without_blank_lines(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_load(_section: FFlagSection) -> None:
        return None

    monkeypatch.setattr(FFlagSection, '_load_from_manager', no_load)
    widget = FFlagSection(None)  # pyright: ignore[reportArgumentType]
    try:
        widget.resize(900, 900)
        widget.show()
        qt_app.processEvents()
        layout = widget.layout()
        assert layout is not None
        item = layout.itemAt(0)
        assert item is not None
        label = item.widget()
        assert isinstance(label, QLabel)
        assert label.text().count('\n') == 0
        assert 'padding: 0' in label.styleSheet()
        assert label.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
        assert label.height() <= label.fontMetrics().lineSpacing() + 1
    finally:
        widget.close()


def test_unprefixed_warning_updates_only_after_real_cached_types_exist(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog: list[frozenset[str] | None] = [None]

    def known() -> frozenset[str] | None:
        return catalog[0]

    monkeypatch.setattr(modifications_tab, 'known_typed_names', known)
    config = cast(
        'ConfigManager',
        SimpleNamespace(
            custom_fflags={'Network': '8'},
            custom_fflags_enabled=False,
            custom_fflag_disabled=[],
            custom_fflag_folders={},
            custom_fflag_disabled_folders=[],
            custom_fflag_keybinds={},
            custom_fflag_folder_keybinds={},
            custom_fflag_actions={},
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
        assert isinstance(delegate, modifications_tab.CustomFFlagNameDelegate)
        index = table.model().index(0, 0)
        assert delegate._matches(index) == ()  # pyright: ignore[reportPrivateUsage]
        catalog[0] = frozenset({'DFIntNetwork', 'FLogNetwork'})
        editor._reload_known_flag_names()  # pyright: ignore[reportPrivateUsage]
        assert delegate._matches(index) == ('DFIntNetwork', 'FLogNetwork')  # pyright: ignore[reportPrivateUsage]
        assert config.custom_fflags == {'Network': '8'}
        qt_app.processEvents()
    finally:
        editor.close()
