from __future__ import annotations

import asyncio
import os
import signal
import struct
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Never, cast

import pytest
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from fleasion import app as app_module
from fleasion.cache import cache_viewer, rbxm_parser
from fleasion.cache.roblox_document import classify_roblox_document
from fleasion.cache.tools.solidmodel_converter import converter as solidmodel_converter
from fleasion.gui import json_viewer, prejsons_dialog
from fleasion.proxy import master as proxy_master
from fleasion.proxy import server as proxy_server
from fleasion.utils import autostart, macos_proxy_helper, roblox_auth
from fleasion.utils.logging import log_buffer


_BAD_GZIP = b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xffnotdeflate' + b'\x00' * 8


def test_hosts_direct_write_does_not_run_atomic_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text('old', encoding='utf-8')
    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)

    def fail_atomic_replace(_content: str) -> Never:
        msg = 'atomic fallback ran after a successful direct write'
        raise AssertionError(msg)

    monkeypatch.setattr(proxy_master, '_atomic_replace_hosts_file', fail_atomic_replace)
    write_hosts = cast('Callable[[str], None]', vars(proxy_master)['_write_hosts_file'])

    write_hosts('new')

    assert hosts_file.read_text(encoding='utf-8') == 'new'


def test_invalid_gzip_remains_opaque_for_proxy_and_document_detection() -> None:
    decompress_body = cast(
        'Callable[[bytes, dict[bytes, bytes]], bytes]',
        vars(proxy_server)['_decompress_body'],
    )

    assert decompress_body(_BAD_GZIP, {b'content-encoding': b'gzip'}) == _BAD_GZIP
    assert classify_roblox_document(_BAD_GZIP) is None


def test_json_viewer_invalid_gzip_detection_does_not_raise() -> None:
    detect_content_type = cast(
        'Callable[[object, bytes], str]',
        vars(json_viewer.JsonTreeViewer)['_detect_content_type'],
    )

    assert detect_content_type(object(), _BAD_GZIP) == 'unknown'


def test_oversized_header_line_returns_none() -> None:
    read_headers = cast(
        'Callable[[asyncio.StreamReader], Awaitable[bytes | None]]',
        vars(proxy_server)['_read_headers_raw'],
    )

    async def read() -> bytes | None:
        reader = asyncio.StreamReader(limit=64 * 1024)
        reader.feed_data(b'A' * (70 * 1024) + b'\n')
        reader.feed_eof()
        return await read_headers(reader)

    assert asyncio.run(read()) is None


def test_truncated_rbxm_preview_falls_back_to_hex() -> None:
    class PreviewStub:
        def __init__(self) -> None:
            self.reason: str | None = None

        def _show_rbxm_preview(self, _data: bytes, _title_prefix: str | None) -> None:
            raise struct.error('truncated RBXM')

        def _preview_hex(self, _data: bytes, reason: str | None = None) -> None:
            self.reason = reason

    preview = PreviewStub()
    preview_rbxm = cast(
        'Callable[[object, bytes, str | None], None]',
        vars(json_viewer.JsonTreeViewer)['_preview_rbxm'],
    )

    preview_rbxm(preview, b'<roblox!', None)

    assert preview.reason is not None
    assert 'truncated RBXM' in preview.reason


def test_invalid_lz4_chunk_returns_raw_compressed_slice() -> None:
    data = b'ABCDEFG'

    assert rbxm_parser.decompress_chunk(data, compressed_size=1, uncompressed_size=1) == b'A'


def test_macos_helper_rejects_non_object_response_as_recoverable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    object_dict = cast(
        'Callable[[object], dict[str, object]]',
        vars(macos_proxy_helper)['_object_dict'],
    )
    with pytest.raises(TypeError, match='JSON object'):
        object_dict([])

    def invalid_request(*_args: object, **_kwargs: object) -> Never:
        msg = 'macOS proxy helper response must be a JSON object'
        raise TypeError(msg)

    monkeypatch.setattr(macos_proxy_helper, '_request', invalid_request)

    assert macos_proxy_helper.helper_status() is None
    assert macos_proxy_helper.helper_heartbeat() is False
    probe = macos_proxy_helper.helper_probe_backend()
    assert probe['ok'] is False
    assert probe['error_type'] == 'TypeError'


def test_macos_relay_failure_dialog_uses_exported_helper_log_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMessageBox:
        class Icon:
            Warning = object()

        class ButtonRole:
            AcceptRole = object()
            ActionRole = object()

        class StandardButton:
            Close = object()

        def __init__(self, _parent: object) -> None:
            self._clicked: object | None = None

        def setWindowFlag(self, _flag: object) -> None:
            return None

        def setWindowTitle(self, _title: str) -> None:
            return None

        def setIcon(self, _icon: object) -> None:
            return None

        def setText(self, _text: str) -> None:
            return None

        def setTextFormat(self, _format: object) -> None:
            return None

        def setInformativeText(self, _text: str) -> None:
            return None

        def addButton(self, *args: object) -> object:
            button = object()
            if args and args[0] is self.StandardButton.Close:
                self._clicked = button
            return button

        def setDefaultButton(self, _button: object) -> None:
            return None

        def setWindowIcon(self, _icon: object) -> None:
            return None

        def findChildren(self, _type: object) -> list[object]:
            return []

        def exec(self) -> None:
            return None

        def clickedButton(self) -> object | None:
            return self._clicked

    def top_level_widgets() -> list[object]:
        return []

    def no_icon_path() -> None:
        return None

    monkeypatch.setattr(
        app_module,
        'QApplication',
        SimpleNamespace(topLevelWidgets=top_level_widgets),
    )
    monkeypatch.setattr(app_module, 'QMessageBox', FakeMessageBox)
    monkeypatch.setattr(app_module, 'get_icon_path', no_icon_path)
    show_dialog = cast(
        'Callable[[dict[str, object]], str]',
        vars(app_module)['_show_macos_relay_failed_dialog'],
    )

    assert show_dialog({}) == 'close'


def test_windows_admin_relaunch_passes_string_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeCFunction:
        def __init__(self, callback: Callable[..., int]) -> None:
            self.argtypes: object = None
            self.restype: object = None
            self._callback = callback

        def __call__(self, *args: object) -> int:
            return self._callback(*args)

    def shell_execute(pointer: object) -> int:
        sei = getattr(pointer, '_obj')
        captured['directory'] = getattr(sei, 'lpDirectory')
        setattr(sei, 'hProcess', 1)
        return 1

    shell32 = SimpleNamespace(ShellExecuteExW=FakeCFunction(shell_execute))
    def return_zero(*_args: object) -> int:
        return 0

    def return_one(*_args: object) -> int:
        return 1

    kernel32 = SimpleNamespace(
        WaitForSingleObject=FakeCFunction(return_zero),
        GetExitCodeProcess=FakeCFunction(return_one),
        GetProcessId=FakeCFunction(return_one),
        TerminateProcess=FakeCFunction(return_one),
        CloseHandle=FakeCFunction(return_one),
    )

    def fake_windll(name: str, **_kwargs: object) -> object:
        return shell32 if name == 'shell32' else kernel32

    executable = tmp_path / 'Fleasion.exe'
    monkeypatch.setattr(app_module.sys, 'argv', ['fleasion'])
    monkeypatch.setattr(app_module.sys, 'executable', str(executable))
    monkeypatch.setattr(app_module.sys, 'frozen', True, raising=False)
    def append_requesting_user_args(_args: list[str]) -> bool:
        return True

    monkeypatch.setattr(app_module, '_append_windows_requesting_user_args', append_requesting_user_args)
    monkeypatch.setattr(app_module.ctypes, 'WinDLL', fake_windll, raising=False)
    relaunch = cast('Callable[..., bool]', vars(app_module)['_relaunch_as_admin_windows'])

    assert relaunch(
        '',
        None,
        wait_for_completion=False,
        wait_timeout_ms=1_000,
        completion=None,
        restart_handoff_token=None,
        restart_handoff_parent_pid=None,
    )
    assert captured['directory'] == str(executable.resolve().parent)
    assert isinstance(captured['directory'], str)


def test_persisted_community_creator_type_opens_community_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RowIndex:
        def row(self) -> int:
            return 0

    class SelectionModel:
        def selectedRows(self) -> list[RowIndex]:
            return [RowIndex()]

    class Item:
        def data(self, _role: object) -> dict[str, str]:
            return {'id': 'asset-1'}

    class Table:
        def selectionModel(self) -> SelectionModel:
            return SelectionModel()

        def item(self, _row: int, _column: int) -> Item:
            return Item()

    urls: list[str] = []

    def open_url(url: str) -> bool:
        urls.append(url)
        return True

    def ignore_log(_category: str, _message: str) -> None:
        return None

    monkeypatch.setattr(cache_viewer.webbrowser, 'open', open_url)
    monkeypatch.setattr(cache_viewer.log_buffer, 'log', ignore_log)
    viewer = SimpleNamespace(
        table=Table(),
        _asset_info={
            'asset-1': {
                'creator_id': 12345,
                'creator_type': 'Community',
            }
        },
    )
    open_creator = cast(
        'Callable[[object], None]',
        vars(cache_viewer.CacheViewerTab)['_open_creator_in_browser'],
    )

    open_creator(viewer)

    assert urls == ['https://www.roblox.com/communities/12345']


def test_wrong_shaped_browser_auth_cache_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / 'browser_auth_cache.json'
    cache_path.write_text('[]', encoding='utf-8')
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_FILE', cache_path)
    def ignore_auth_failure(_key: str, _message: str) -> None:
        return None

    def ignore_cache_state(
        _reason: str,
        _message: str,
        *,
        block_automatic_import: bool = False,
    ) -> None:
        del block_automatic_import

    monkeypatch.setattr(roblox_auth, '_log_auth_failure', ignore_auth_failure)
    monkeypatch.setattr(roblox_auth, '_log_browser_auth_cache_state', ignore_cache_state)
    read_fields = cast(
        'Callable[[], tuple[str, str] | None]',
        vars(roblox_auth)['_read_browser_auth_cache_fields'],
    )

    assert read_fields() is None
    assert cache_path.exists()
    assert cache_path.read_text(encoding='utf-8') == '[]'


def test_relative_windows_uv_path_is_normalized_to_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePath:
        def __init__(self, value: str) -> None:
            self.value = value

        def is_absolute(self) -> bool:
            return False

        def resolve(self) -> FakePath:
            return FakePath(r'C:\Tools\uv.exe')

        def __str__(self) -> str:
            return self.value

    monkeypatch.setattr(autostart, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(autostart, 'Path', FakePath)
    def relative_uv(_name: str) -> str:
        return r'tools\uv.exe'

    monkeypatch.setattr(autostart.shutil, 'which', relative_uv)
    windows_uv_executable = cast(
        'Callable[[], str]',
        vars(autostart)['_windows_uv_executable'],
    )

    value = windows_uv_executable()

    assert value == r'C:\Tools\uv.exe'
    assert isinstance(value, str)


def test_solidmodel_converter_mirrors_messages_to_ui_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[tuple[str, str]] = []

    def record_log(category: str, message: str) -> None:
        messages.append((category, message))

    monkeypatch.setattr(log_buffer, 'log', record_log)
    log_message = cast(
        'Callable[[str], None]',
        vars(solidmodel_converter)['_log_solidmodel_message'],
    )

    log_message('converted')

    assert messages == [('SolidModel', 'converted')]


def test_prejson_rounded_pixmap_supports_pyside_memoryview() -> None:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    pixmap = QPixmap(32, 24)
    pixmap.fill(QColor('red'))

    make_rounded_pixmap = cast(
        'Callable[[QPixmap, int, int], QPixmap]',
        vars(prejsons_dialog)['_make_rounded_pixmap'],
    )
    rounded = make_rounded_pixmap(pixmap, 20, 12)

    assert rounded.width() == 20
    assert rounded.height() == 12
    assert not rounded.isNull()


def test_gui_sigint_handler_exits_qt_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers: dict[int, Callable[[int, object], None]] = {}

    class FakeTimeoutSignal:
        def __init__(self) -> None:
            self.callback: Callable[[], None] | None = None

        def connect(self, callback: Callable[[], None]) -> None:
            self.callback = callback

    class FakeTimer:
        def __init__(self, _parent: object) -> None:
            self.timeout = FakeTimeoutSignal()
            self.interval: int | None = None
            self.started = False

        def setInterval(self, interval: int) -> None:
            self.interval = interval

        def start(self) -> None:
            self.started = True

    class FakeApplication:
        def __init__(self) -> None:
            self.exit_codes: list[int] = []

        def exit(self, code: int = 0) -> None:
            self.exit_codes.append(code)

    def capture_signal(
        signum: int,
        handler: Callable[[int, object], None],
    ) -> signal.Handlers:
        handlers[signum] = handler
        return signal.SIG_DFL

    monkeypatch.setattr(app_module.signal, 'signal', capture_signal)
    monkeypatch.setattr(app_module, 'QTimer', FakeTimer)
    install_handler = cast(
        'Callable[[object], FakeTimer]',
        vars(app_module)['_install_gui_sigint_handler'],
    )
    fake_app = FakeApplication()

    timer = install_handler(fake_app)
    handlers[signal.SIGINT](signal.SIGINT, None)

    assert timer.interval == 200
    assert timer.started is True
    assert timer.timeout.callback is not None
    timer.timeout.callback()
    assert fake_app.exit_codes == [128 + signal.SIGINT]
