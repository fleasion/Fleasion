# pyright: reportPrivateUsage=false
"""Bounded profile preview downloads and account-switch cache invalidation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
import requests

from fleasion.gui.modifications_tab import ModificationsTab
from fleasion.gui.rando_stuff_tab import RandoStuffTab

if TYPE_CHECKING:
    from collections.abc import Iterator


class _ImageResponse:
    status_code = 200

    def __init__(self, chunks: list[bytes], *, declared_size: str = '') -> None:
        self.headers = {'Content-Length': declared_size} if declared_size else {}
        self._chunks = chunks
        self.read_count = 0
        self.closed = False

    def __enter__(self) -> _ImageResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def iter_content(self, *, chunk_size: int) -> Iterator[bytes]:
        assert chunk_size <= 64 * 1024
        for chunk in self._chunks:
            self.read_count += len(chunk)
            yield chunk


def _preview_loader(max_bytes: int) -> ModificationsTab:
    return cast('ModificationsTab', SimpleNamespace(_PFP_MAX_IMAGE_BYTES=max_bytes))


def test_preview_streaming_stops_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _ImageResponse([b'123', b'456', b'NEVER-DOWNLOAD'])

    def get(_url: str, *, stream: bool, timeout: int) -> _ImageResponse:
        assert stream is True
        assert timeout == 10
        return response

    monkeypatch.setattr(requests, 'get', get)
    result = ModificationsTab._load_pfp_image_bytes(
        _preview_loader(5), 'remote', 'https://example.com/picture.png'
    )
    assert result == b''
    assert response.read_count == 6
    assert response.closed


def test_preview_rejects_oversized_content_length_without_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _ImageResponse([b'NEVER-DOWNLOAD'], declared_size='10000000')
    def get(_url: str, **_kwargs: object) -> _ImageResponse:
        return response

    monkeypatch.setattr(requests, 'get', get)
    assert not ModificationsTab._load_pfp_image_bytes(
        _preview_loader(5), 'remote', 'https://example.com/too-large.png'
    )
    assert response.read_count == 0
    assert response.closed


def test_preview_reassembles_small_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _ImageResponse([b'12', b'34'], declared_size='4')
    def get(_url: str, **_kwargs: object) -> _ImageResponse:
        return response

    monkeypatch.setattr(requests, 'get', get)
    assert ModificationsTab._load_pfp_image_bytes(
        _preview_loader(5), 'remote', 'https://example.com/small.png'
    ) == b'1234'
    assert response.closed


def test_preview_caps_local_file_reads(tmp_path: Path) -> None:
    file = tmp_path / 'image.png'
    file.write_bytes(b'123456')
    assert not ModificationsTab._load_pfp_image_bytes(
        _preview_loader(5), 'local', str(file)
    )
    file.write_bytes(b'1234')
    assert ModificationsTab._load_pfp_image_bytes(
        _preview_loader(5), 'local', str(file)
    ) == b'1234'


def test_account_change_invalidates_cached_preview_and_stale_completion() -> None:
    class FakeTab:
        _pfp_self_id_lookup_generation = 4
        _pfp_self_id_inflight = True
        _pfp_self_id = '1'
        _pfp_table = SimpleNamespace(rowCount=lambda: 2)

        def __init__(self) -> None:
            self.refreshes: list[int] = []

        def _refresh_pfp_row_icons(self, row: int) -> None:
            self.refreshes.append(row)

    fake = FakeTab()
    tab = cast('ModificationsTab', fake)
    ModificationsTab.invalidate_profile_picture_preview(tab)
    assert fake._pfp_self_id_lookup_generation == 5
    assert not fake._pfp_self_id_inflight
    assert fake._pfp_self_id is None
    assert fake.refreshes == [0, 1]
    ModificationsTab._on_pfp_self_id_resolved(tab, 4, '1')
    assert fake._pfp_self_id is None
    ModificationsTab._on_pfp_self_id_resolved(tab, 5, '2')
    assert fake._pfp_self_id == '2'
    assert fake.refreshes == [0, 1, 0, 1]


def test_account_manager_refreshes_active_identity_without_selected_cookie() -> None:
    notifications: list[str] = []
    spoofer = SimpleNamespace(notify_active_account_changed=lambda: notifications.append('refresh'))
    fake = SimpleNamespace(_proxy_master=SimpleNamespace(profile_picture_spoofer=spoofer))
    RandoStuffTab._push_profile_picture_current_account(cast('RandoStuffTab', fake))
    assert notifications == ['refresh']


def test_profile_link_lookup_updates_real_qt_username_cell() -> None:
    from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

    app = QApplication.instance() or QApplication([])
    table = QTableWidget(1, 4)
    table.setItem(0, ModificationsTab._PFP_COL_TARGET, QTableWidgetItem('https://www.roblox.com/users/123/profile'))
    table.setItem(0, ModificationsTab._PFP_COL_USERNAME, QTableWidgetItem('…'))
    def set_username(item: QTableWidgetItem, value: str) -> None:
        item.setText(value)

    fake = SimpleNamespace(
        _pfp_username_inflight={'123'},
        _pfp_username_cache={},
        _pfp_table=table,
        _PFP_COL_TARGET=ModificationsTab._PFP_COL_TARGET,
        _PFP_COL_USERNAME=ModificationsTab._PFP_COL_USERNAME,
        _normalize_pfp_target=ModificationsTab._normalize_pfp_target,
        _set_pfp_username_text=set_username,
    )
    ModificationsTab._on_pfp_username_resolved(cast('ModificationsTab', fake), '123', 'Alice')
    item = table.item(0, ModificationsTab._PFP_COL_USERNAME)
    assert item is not None and item.text() == 'Alice'
    assert fake._pfp_username_cache == {'123': 'Alice'}
    table.deleteLater()
    app.processEvents()


def test_ticket_launch_account_selection_does_not_change_active_self_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.gui import rando_stuff_tab as account_module

    changes: list[str] = []
    selected: list[str] = []
    spoofer = SimpleNamespace(notify_active_account_changed=lambda: changes.append('changed'))
    account = {'username': 'SelectedForLaunchOnly', 'cookie': 'encrypted'}

    class FakeAccountUI:
        _account_list = SimpleNamespace(currentRow=lambda: 0)
        _accounts = [account]
        _last_switched_account: object | None = None
        _proxy_master = SimpleNamespace(profile_picture_spoofer=spoofer)

        def _set_selected_account(self, username: str) -> None:
            selected.append(username)

    monkeypatch.setattr(account_module, 'IS_MACOS', True)
    monkeypatch.setattr(account_module, '_is_rosemary_client', lambda: False)
    def decrypt(_encrypted: str) -> str:
        return 'auth-cookie'

    monkeypatch.setattr(account_module, '_decrypt_cookie', decrypt)
    fake = FakeAccountUI()
    RandoStuffTab._on_switch_account(cast('RandoStuffTab', fake))
    assert selected == ['SelectedForLaunchOnly']
    assert fake._last_switched_account is account
    assert changes == []
