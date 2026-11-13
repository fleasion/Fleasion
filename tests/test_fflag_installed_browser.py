"""Production installed-client browser with immutable Mach-O/ELF metadata."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from fleasion.gui import modifications_tab
from fleasion.gui.modifications_tab import FFlagBrowserDialog
from fleasion.modifications.fflag_static import (
    ClientBinary,
    StaticFlagCatalog,
    StaticRecord,
)


@pytest.fixture
def app() -> QApplication:
    """Reuse a single QApplication and avoid the public catalog network worker."""
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


@pytest.fixture
def client(tmp_path: Path) -> ClientBinary:
    """An installed native client fixture with a local, never-executed file."""
    file = tmp_path / 'libroblox.so'
    file.write_bytes(b'not executed')
    return ClientBinary('sober', 'Sober', file)


@pytest.fixture
def catalog(client: ClientBinary) -> StaticFlagCatalog:
    """One verified typed Android record and one inferred external name."""
    stat = client.path.stat()
    return StaticFlagCatalog(
        client=client.client,
        label=client.label,
        source=str(client.path),
        flags={'DFFlagVerifiedFromElf': None, 'DFIntInferredFromTracker': '50'},
        verified=frozenset({'DFFlagVerifiedFromElf'}),
        records=2,
        unresolved=0,
        saved_at=1.0,
        source_size=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
        member=None,
        registrations=(
            StaticRecord('VerifiedFromElf', variant=2, kind='bool', verified_type=True),
            StaticRecord('InferredFromTracker', kind='unknown', verified_type=False),
        ),
    )


def _prevent_public_refresh(self: FFlagBrowserDialog, force: bool = False) -> None:
    del self, force


def test_installed_source_exists_and_uses_selected_client(
    app: QApplication,
    client: ClientBinary,
    catalog: StaticFlagCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _prevent_public_refresh)
    monkeypatch.setattr(modifications_tab, 'detect_client_binary', lambda: client)
    dialog = FFlagBrowserDialog()
    try:
        installed = dialog._source_mode.findData('installed')  # pyright: ignore[reportPrivateUsage]
        assert installed == 1
        assert dialog._source_mode.count() == 2  # pyright: ignore[reportPrivateUsage]
        assert dialog._source_mode.itemText(1) == 'Client - Sober'  # pyright: ignore[reportPrivateUsage]
        dialog._source_mode.setCurrentIndex(installed)  # pyright: ignore[reportPrivateUsage]
        generation = dialog._local_generation  # pyright: ignore[reportPrivateUsage]
        dialog._apply_installed_scan((generation, catalog, client, False))  # pyright: ignore[reportPrivateUsage]
        assert dialog._installed_catalog == catalog  # pyright: ignore[reportPrivateUsage]
        assert dialog._flags == catalog.flags  # pyright: ignore[reportPrivateUsage]
        assert dialog._table.rowCount() == 2  # pyright: ignore[reportPrivateUsage]
        assert '1 independently verified types' in dialog._count.text()  # pyright: ignore[reportPrivateUsage]
        assert 'Sober' in dialog._count.text()  # pyright: ignore[reportPrivateUsage]
        assert 'not scanned runtime defaults' not in dialog._count.text()  # pyright: ignore[reportPrivateUsage]
        header = dialog._table.horizontalHeaderItem(1)  # pyright: ignore[reportPrivateUsage]
        assert header is not None
        assert header.text() == 'Value'
        assert dialog._source_mode.currentData() == 'installed'  # pyright: ignore[reportPrivateUsage]
        assert not dialog._choose_exe_button.isVisible()  # pyright: ignore[reportPrivateUsage]
        app.processEvents()
    finally:
        dialog.close()


def test_installed_outdated_thread_result_is_ignored_after_source_switch(
    app: QApplication,
    client: ClientBinary,
    catalog: StaticFlagCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _prevent_public_refresh)
    monkeypatch.setattr(modifications_tab, 'detect_client_binary', lambda: client)
    dialog = FFlagBrowserDialog()
    try:
        dialog._source_mode.setCurrentIndex(1)  # pyright: ignore[reportPrivateUsage]
        old = dialog._local_generation  # pyright: ignore[reportPrivateUsage]
        dialog._source_mode.setCurrentIndex(0)  # pyright: ignore[reportPrivateUsage]
        assert dialog._installed_catalog is None  # pyright: ignore[reportPrivateUsage]
        dialog._apply_installed_scan((old, catalog, client, False))  # pyright: ignore[reportPrivateUsage]
        dialog._apply_installed_error((old, 'stale failure'))  # pyright: ignore[reportPrivateUsage]
        assert dialog._installed_catalog is None  # pyright: ignore[reportPrivateUsage]
        assert dialog._flags == {}  # pyright: ignore[reportPrivateUsage]
        dialog._source_mode.setCurrentIndex(1)  # pyright: ignore[reportPrivateUsage]
        assert dialog._installed_catalog is None  # pyright: ignore[reportPrivateUsage]
        app.processEvents()
    finally:
        dialog.close()


def test_installed_cache_reuse_and_requalification_without_rescan(
    app: QApplication,
    client: ClientBinary,
    catalog: StaticFlagCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _prevent_public_refresh)
    monkeypatch.setattr(modifications_tab, 'detect_client_binary', lambda: client)

    def cached_scan(_client: str) -> StaticFlagCatalog:
        return catalog

    def matching_source(_cat: StaticFlagCatalog, _src: ClientBinary) -> bool:
        return True

    def no_other_hints(_name: str) -> frozenset[str]:
        return frozenset()

    monkeypatch.setattr(modifications_tab, 'load_scan', cached_scan)
    monkeypatch.setattr(modifications_tab, 'scan_is_current', matching_source)
    monkeypatch.setattr(
        modifications_tab, 'cached_cross_client_type_hints', no_other_hints
    )
    called: list[str] = []

    def forbidden_scan(
        _src: ClientBinary, _published: dict[str, str | None]
    ) -> StaticFlagCatalog:
        called.append('scan')
        raise AssertionError('unchanged cache must not rescan original binary')

    monkeypatch.setattr(modifications_tab, 'scan_binary', forbidden_scan)
    dialog = FFlagBrowserDialog()
    try:
        current, saved = dialog._compute_installed_scan(client, force=False)  # pyright: ignore[reportPrivateUsage]
        assert saved
        assert current.flags == catalog.flags
        assert not called
        assert current.verified == catalog.verified
        app.processEvents()
    finally:
        dialog.close()


def test_offline_installed_scan_uses_native_types_without_tracker(
    app: QApplication,
    client: ClientBinary,
    catalog: StaticFlagCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(FFlagBrowserDialog, '_refresh', _prevent_public_refresh)
    monkeypatch.setattr(modifications_tab, 'detect_client_binary', lambda: client)

    def no_cached_scan(_client: str) -> None:
        return None

    def no_other_hints(_name: str) -> frozenset[str]:
        return frozenset()

    def fail_http(*_args: object, **_kwargs: object) -> bytes:
        raise OSError('offline')

    def local_scan(
        _src: ClientBinary, _published: dict[str, str | None]
    ) -> StaticFlagCatalog:
        return catalog

    monkeypatch.setattr(modifications_tab, 'load_scan', no_cached_scan)
    monkeypatch.setattr(
        modifications_tab, 'cached_cross_client_type_hints', no_other_hints
    )
    monkeypatch.setattr(modifications_tab, 'http_get', fail_http)
    monkeypatch.setattr(modifications_tab, 'scan_binary', local_scan)
    saved: list[StaticFlagCatalog] = []
    monkeypatch.setattr(modifications_tab, 'save_scan', saved.append)
    dialog = FFlagBrowserDialog()
    try:
        result, from_cache = dialog._compute_installed_scan(client, force=False)  # pyright: ignore[reportPrivateUsage]
        assert not from_cache
        assert saved == [catalog]
        assert 'DFFlagVerifiedFromElf' in result.verified
        assert result.flags['DFFlagVerifiedFromElf'] is None
        app.processEvents()
    finally:
        dialog.close()
