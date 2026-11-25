"""An orange custom FastFlag prompts one native scan only when cache is absent."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from fleasion.gui import modifications_tab
from fleasion.gui.modifications_tab import CustomFFlagEditor, CustomFFlagNameDelegate
from fleasion.modifications.fflag_static import ClientBinary

if TYPE_CHECKING:
    from collections.abc import Callable

    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.master import ProxyMaster


_app: QApplication | None = None


@pytest.fixture
def app() -> QApplication:
    # Keep Qt alive for the full module. QWidgets may still be queued for
    # deferred destruction after a test releases its local QApplication ref.
    global _app
    running = QApplication.instance()
    _app = cast('QApplication', running) if running is not None else QApplication([])
    return _app


def _dispose_editor(editor: CustomFFlagEditor) -> None:
    editor.close()
    editor.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    running = QApplication.instance()
    if isinstance(running, QApplication):
        running.processEvents()


def _editor(flags: dict[str, str]) -> CustomFFlagEditor:
    config = cast(
        'ConfigManager',
        SimpleNamespace(
            custom_fflags=flags,
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
        SimpleNamespace(refresh_custom_fflag_interception=lambda: None),
    )
    return CustomFFlagEditor(config, proxy)


@pytest.fixture
def scan_environment(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[list[frozenset[str] | None], list[bool], list[ClientBinary]]:
    del app
    names: list[frozenset[str] | None] = [frozenset({'FFlagAlreadyKnown'})]
    cached = [False]
    calls: list[ClientBinary] = []
    source = ClientBinary('sober', 'Sober', tmp_path / 'libroblox.so')
    source.path.write_bytes(b'binary')
    monkeypatch.setattr(modifications_tab, 'known_typed_names', lambda: names[0])
    monkeypatch.setattr(modifications_tab, 'known_untyped_names', lambda: frozenset({'UntypedFlag'}))
    monkeypatch.setattr(modifications_tab, 'detect_client_binary', lambda: source)
    def cached_scan(_client: str) -> object | None:
        return object() if cached[0] else None

    monkeypatch.setattr(modifications_tab, 'load_scan', cached_scan)

    def scan(binary: ClientBinary, force: bool) -> tuple[object, bool]:
        assert not force
        calls.append(binary)
        cached[0] = True
        names[0] = frozenset({'FFlagAlreadyKnown', 'DFIntMissingFlag'})
        return object(), False

    monkeypatch.setattr(
        modifications_tab.FFlagBrowserDialog, 'scan_installed_client', staticmethod(scan)
    )

    class ImmediateThread:
        def __init__(self, *, target: Callable[[], None], daemon: bool) -> None:
            assert daemon
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(modifications_tab.threading, 'Thread', ImmediateThread)
    return names, cached, calls


def test_missing_flag_triggers_one_scan_and_updates_warning(
    scan_environment: tuple[list[frozenset[str] | None], list[bool], list[ClientBinary]],
) -> None:
    names, cached, calls = scan_environment
    editor = _editor({'MissingFlag': '123'})
    try:
        delegate = editor._table.itemDelegateForColumn(0)  # pyright: ignore[reportPrivateUsage]
        assert isinstance(delegate, CustomFFlagNameDelegate)
        index = editor._table.model().index(0, 0)  # pyright: ignore[reportPrivateUsage]
        assert delegate._warning_kind(index) == 'missing'  # pyright: ignore[reportPrivateUsage]
        editor.prefetch_flag_catalog_if_needed()
        assert cached == [True]
        assert len(calls) == 1 and calls[0].client == 'sober'
        assert 'DFIntMissingFlag' in (names[0] or ())
        assert delegate._warning_kind(index) is None  # pyright: ignore[reportPrivateUsage]
        editor.prefetch_flag_catalog_if_needed()
        assert len(calls) == 1
    finally:
        _dispose_editor(editor)


def test_existing_cache_never_triggers_scan(
    scan_environment: tuple[list[frozenset[str] | None], list[bool], list[ClientBinary]],
) -> None:
    _names, cached, calls = scan_environment
    cached[0] = True
    editor = _editor({'MissingFlag': '123'})
    try:
        editor.prefetch_flag_catalog_if_needed()
        editor.prefetch_flag_catalog_if_needed()
        assert calls == []
    finally:
        _dispose_editor(editor)


@pytest.mark.parametrize(
    ('flags', 'inventory'),
    [
        ({}, frozenset({'FFlagAlreadyKnown'})),
        ({'FFlagAlreadyKnown': 'True'}, frozenset({'FFlagAlreadyKnown'})),
        ({'UntypedFlag': '1'}, frozenset({'FFlagAlreadyKnown'})),
        ({'AmbiguousFlag': '1'}, frozenset({'FFlagAmbiguousFlag', 'DFFlagAmbiguousFlag'})),
        ({'MissingFlag': '1'}, None),
    ],
)
def test_non_orange_entries_do_not_trigger_scan(
    scan_environment: tuple[list[frozenset[str] | None], list[bool], list[ClientBinary]],
    flags: dict[str, str],
    inventory: frozenset[str] | None,
) -> None:
    names, _cached, calls = scan_environment
    names[0] = inventory
    editor = _editor(flags)
    try:
        editor.prefetch_flag_catalog_if_needed()
        assert calls == []
    finally:
        _dispose_editor(editor)


def test_newly_imported_missing_flag_triggers_scan(
    scan_environment: tuple[list[frozenset[str] | None], list[bool], list[ClientBinary]],
) -> None:
    _names, _cached, calls = scan_environment
    editor = _editor({'FFlagAlreadyKnown': 'True'})
    try:
        editor.prefetch_flag_catalog_if_needed()
        assert calls == []
        editor._set_flags({'MissingFlag': '1'})  # pyright: ignore[reportPrivateUsage]
        assert len(calls) == 1
    finally:
        _dispose_editor(editor)


def test_failed_scan_is_not_retried_on_every_visit(
    scan_environment: tuple[list[frozenset[str] | None], list[bool], list[ClientBinary]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _names, _cached, _calls = scan_environment
    attempts: list[bool] = []

    def fail(_binary: ClientBinary, force: bool) -> tuple[object, bool]:
        assert not force
        attempts.append(True)
        raise ValueError('invalid executable')

    monkeypatch.setattr(
        modifications_tab.FFlagBrowserDialog, 'scan_installed_client', staticmethod(fail)
    )
    editor = _editor({'MissingFlag': '1'})
    try:
        editor.prefetch_flag_catalog_if_needed()
        editor.prefetch_flag_catalog_if_needed()
        assert attempts == [True]
        editor._missing_scan_retry_not_before = 0  # pyright: ignore[reportPrivateUsage]
        editor.prefetch_flag_catalog_if_needed()
        assert attempts == [True, True]
    finally:
        _dispose_editor(editor)


def test_virtual_flag_table_uses_the_same_missing_cache_trigger(
    scan_environment: tuple[list[frozenset[str] | None], list[bool], list[ClientBinary]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _names, _cached, calls = scan_environment
    monkeypatch.setattr(CustomFFlagEditor, '_VIRTUAL_THRESHOLD', 1)
    editor = _editor({'MissingFlag': '1', 'FFlagAlreadyKnown': 'True'})
    try:
        assert editor._virtual_active  # pyright: ignore[reportPrivateUsage]
        editor.prefetch_flag_catalog_if_needed()
        assert len(calls) == 1
    finally:
        _dispose_editor(editor)


def test_unavailable_client_does_not_invoke_scanner(
    scan_environment: tuple[list[frozenset[str] | None], list[bool], list[ClientBinary]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _names, _cached, calls = scan_environment
    monkeypatch.setattr(modifications_tab, 'detect_client_binary', lambda: None)
    editor = _editor({'MissingFlag': '1'})
    try:
        editor.prefetch_flag_catalog_if_needed()
        assert calls == []
    finally:
        _dispose_editor(editor)
