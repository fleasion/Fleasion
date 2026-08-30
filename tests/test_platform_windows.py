from __future__ import annotations

import ctypes
import ctypes.wintypes
import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Never, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import pytest


class _ProcessInfo(TypedDict, total=False):
    ProcessId: int | str | None
    ExecutablePath: str | Path | None
    CommandLine: str | None


class _CancelEvent(Protocol):
    def is_set(self) -> bool: ...


class _LogBuffer(Protocol):
    def log(self, category: str, message: str) -> None: ...


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ('Length', ctypes.wintypes.USHORT),
        ('MaximumLength', ctypes.wintypes.USHORT),
        ('Buffer', ctypes.c_void_p),
    ]


class _PlatformWindowsAdapter:
    def __init__(self, raw: types.ModuleType) -> None:
        self.raw = raw

    @property
    def process_command_line_information(self) -> int:
        return cast('int', self.raw.__dict__['_PROCESS_COMMAND_LINE_INFORMATION'])

    @property
    def process_terminate(self) -> int:
        return cast('int', self.raw.__dict__['_PROCESS_TERMINATE'])

    @property
    def status_info_length_mismatch(self) -> int:
        return cast('int', self.raw.__dict__['_STATUS_INFO_LENGTH_MISMATCH'])

    @property
    def unicode_string_type(self) -> type[_UnicodeString]:
        return cast('type[_UnicodeString]', self.raw.__dict__['_UNICODE_STRING'])

    @property
    def env_proxy_owned_process(self) -> tuple[int, str] | None:
        return cast('tuple[int, str] | None', self.raw.__dict__['_env_proxy_owned_process'])

    @env_proxy_owned_process.setter
    def env_proxy_owned_process(self, value: tuple[int, str] | None) -> None:
        self.raw.__dict__['_env_proxy_owned_process'] = value

    @property
    def log_buffer(self) -> _LogBuffer:
        return cast('_LogBuffer', self.raw.__dict__['log_buffer'])

    def activate_roblox_gdk_with_proxy_env(
        self,
        proxy_url: str,
        *,
        label: str,
        pid: int,
        exe_path: Path,
        launch_arg: str,
        query_processes: Callable[[], list[_ProcessInfo]],
        prepare_launch: Callable[[Path], bool] | None,
        cancel_event: _CancelEvent | None,
    ) -> tuple[int, str] | None:
        function = cast(
            'Callable[..., tuple[int, str] | None]',
            self.raw.__dict__['_activate_roblox_gdk_with_proxy_env'],
        )
        return function(
            proxy_url,
            label=label,
            pid=pid,
            exe_path=exe_path,
            launch_arg=launch_arg,
            query_processes=query_processes,
            prepare_launch=prepare_launch,
            cancel_event=cancel_event,
        )

    def delete_storage_family(self, db_path: Path, messages: list[str], suffix: str = '') -> None:
        function = cast(
            'Callable[[Path, list[str], str], None]',
            self.raw.__dict__['_delete_storage_family'],
        )
        function(db_path, messages, suffix)

    def extract_exe_from_command(self, command: str) -> Path | None:
        function = cast(
            'Callable[[str], Path | None]', self.raw.__dict__['_extract_exe_from_command']
        )
        return function(command)

    def extract_roblox_deeplink(self, command_line: str, marker: str = 'roblox-player:') -> str:
        function = cast('Callable[[str, str], str]', self.raw.__dict__['_extract_roblox_deeplink'])
        return function(command_line, marker)

    def find_installed_roblox_gdk_package_identity(self) -> tuple[str, str] | None:
        function = cast(
            'Callable[[], tuple[str, str] | None]',
            self.raw.__dict__['_find_installed_roblox_gdk_package_identity'],
        )
        return function()

    def force_close_process_immediately(
        self,
        pid: int,
        exe_name: str,
        *,
        label: str,
        timeout: float = 8.0,
        cancel_event: _CancelEvent | None = None,
    ) -> bool:
        function = cast(
            'Callable[..., bool]', self.raw.__dict__['_force_close_process_immediately']
        )
        return function(
            pid,
            exe_name,
            label=label,
            timeout=timeout,
            cancel_event=cancel_event,
        )

    def get_roblox_gdk_package_identity(self, exe_path: Path) -> tuple[str, str] | None:
        function = cast(
            'Callable[[Path], tuple[str, str] | None]',
            self.raw.__dict__['_get_roblox_gdk_package_identity'],
        )
        return function(exe_path)

    def package_environment_block(
        self, environment: dict[str, str]
    ) -> ctypes.Array[ctypes.c_wchar]:
        function = cast(
            'Callable[[dict[str, str]], ctypes.Array[ctypes.c_wchar]]',
            self.raw.__dict__['_package_environment_block'],
        )
        return function(environment)

    def query_process_command_line(self, pid: int) -> str:
        function = cast('Callable[[int], str]', self.raw.__dict__['_query_process_command_line'])
        return function(pid)

    def query_roblox_processes(self, exe_name: str) -> list[_ProcessInfo]:
        function = cast(
            'Callable[[str], list[_ProcessInfo]]', self.raw.__dict__['_query_roblox_processes']
        )
        return function(exe_name)

    def relaunch_roblox_exe_with_proxy_env(
        self,
        proxy_url: str,
        *,
        label: str,
        query_processes: Callable[[], list[_ProcessInfo]],
        extract_launch_arg: Callable[[str], str],
        wait_pid_exe_name: str,
        fallback_exe_path: Callable[[], Path | None],
        force: bool = False,
        cancel_event: _CancelEvent | None = None,
        prepare_launch: Callable[[Path], bool] | None = None,
    ) -> bool:
        function = cast(
            'Callable[..., bool]', self.raw.__dict__['_relaunch_roblox_exe_with_proxy_env']
        )
        return function(
            proxy_url,
            label=label,
            query_processes=query_processes,
            extract_launch_arg=extract_launch_arg,
            wait_pid_exe_name=wait_pid_exe_name,
            fallback_exe_path=fallback_exe_path,
            force=force,
            cancel_event=cancel_event,
            prepare_launch=prepare_launch,
        )

    def safe_mtime(self, path: Path) -> float:
        function = cast('Callable[[Path], float]', self.raw.__dict__['_safe_mtime'])
        return function(path)

    def summarize_cache_messages(self, messages: list[str]) -> list[str]:
        function = cast(
            'Callable[[list[str]], list[str]]', self.raw.__dict__['_summarize_cache_messages']
        )
        return function(messages)

    def terminate_process_direct(self, pid: int) -> tuple[bool, str]:
        function = cast(
            'Callable[[int], tuple[bool, str]]', self.raw.__dict__['_terminate_process_direct']
        )
        return function(pid)

    def arm_roblox_gdk_env_proxy(self, proxy_url: str) -> bool:
        function = cast('Callable[[str], bool]', self.raw.__dict__['arm_roblox_gdk_env_proxy'])
        return function(proxy_url)

    def close_roblox_for_env_lifecycle(self) -> bool:
        function = cast('Callable[[], bool]', self.raw.__dict__['close_roblox_for_env_lifecycle'])
        return function()

    def delete_cache(self) -> list[str]:
        function = cast('Callable[[], list[str]]', self.raw.__dict__['delete_cache'])
        return function()

    def is_roblox_gdk_exe_path(self, exe_path: Path | str | None) -> bool:
        function = cast(
            'Callable[[Path | str | None], bool]', self.raw.__dict__['is_roblox_gdk_exe_path']
        )
        return function(exe_path)

    def resolve_roblox_player_exe_for_launch(self) -> Path | None:
        function = cast(
            'Callable[[], Path | None]', self.raw.__dict__['resolve_roblox_player_exe_for_launch']
        )
        return function()

    def run_cmd(self, args: list[str], timeout: float = 10.0) -> tuple[int, str]:
        function = cast(
            'Callable[[list[str], float], tuple[int, str]]', self.raw.__dict__['run_cmd']
        )
        return function(args, timeout)

    def terminate_roblox(self) -> bool:
        function = cast('Callable[[], bool]', self.raw.__dict__['terminate_roblox'])
        return function()


def _returns[T](value: T) -> Callable[..., T]:
    def _stub(*_args: object, **_kwargs: object) -> T:
        return value

    return _stub


def _raises(error: BaseException) -> Callable[..., Never]:
    def _stub(*_args: object, **_kwargs: object) -> Never:
        raise error

    return _stub


def _process_info(pid: int, exe_path: Path | str, command_line: str = '') -> _ProcessInfo:
    return {
        'ProcessId': pid,
        'ExecutablePath': str(exe_path),
        'CommandLine': command_line,
    }


class _NextResult[T]:
    def __init__(self, values: Iterator[T]) -> None:
        self._values = values

    def __call__(self, *_args: object, **_kwargs: object) -> T:
        return next(self._values)


def _describe_pids(pids: list[int]) -> str:
    return ','.join(map(str, pids))


def _last_argument(command: str) -> str:
    return command.rsplit(maxsplit=1)[-1]


def _path_equals(expected: Path) -> Callable[[Path], bool]:
    def _matches(path: Path) -> bool:
        return path == expected

    return _matches


def _no_paths(*_args: object, **_kwargs: object) -> list[Path]:
    return []


class _CancelEventStub:
    def __init__(self, is_set: bool) -> None:
        self._is_set = is_set

    def is_set(self) -> bool:
        return self._is_set


class _NullLogBuffer:
    def log(self, _category: str, _message: str) -> None:
        return None


class _LogRecorder:
    def __init__(self, target: list[tuple[str, str]]) -> None:
        self._target = target

    def __call__(self, category: str, message: str) -> None:
        self._target.append((category, message))


def _load_platform_windows(
    monkeypatch: pytest.MonkeyPatch, registry_command: str | None = None
) -> _PlatformWindowsAdapter:
    source = (
        Path(__file__).resolve().parents[1] / 'src' / 'fleasion' / 'utils' / 'platform_windows.py'
    )

    paths = types.ModuleType('fleasion.utils.paths')
    paths.__dict__['LOCAL_APPDATA'] = ''
    paths.__dict__['ROBLOX_PROCESS'] = 'RobloxPlayerBeta.exe'
    paths.__dict__['ROBLOX_STUDIO_PROCESS'] = 'RobloxStudioBeta.exe'
    paths.__dict__['STORAGE_DB'] = ''
    paths.__dict__['STORAGE_DB_GDK'] = ''

    logging = types.ModuleType('fleasion.utils.logging')
    logging.__dict__['log_buffer'] = _NullLogBuffer()

    winreg = types.ModuleType('winreg')
    winreg.__dict__['HKEY_CURRENT_USER'] = object()

    class _Key:
        def __enter__(self) -> _Key:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    def _open_key(*_args: object, **_kwargs: object) -> _Key:
        if registry_command is None:
            raise OSError
        return _Key()

    def _query_value_ex(*_args: object, **_kwargs: object) -> tuple[str | None, int]:
        return registry_command, 1

    winreg.__dict__['OpenKey'] = _open_key
    winreg.__dict__['QueryValueEx'] = _query_value_ex

    monkeypatch.setattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE, raising=False)
    monkeypatch.setitem(sys.modules, 'fleasion', types.ModuleType('fleasion'))
    monkeypatch.setitem(sys.modules, 'fleasion.utils', types.ModuleType('fleasion.utils'))
    monkeypatch.setitem(sys.modules, 'fleasion.utils.paths', paths)
    monkeypatch.setitem(sys.modules, 'fleasion.utils.logging', logging)
    monkeypatch.setitem(sys.modules, 'winreg', winreg)

    module_name = 'fleasion.utils.platform_windows_under_test'
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        msg = f'failed to load {source}'
        raise AssertionError(msg)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return _PlatformWindowsAdapter(module)


def _touch(path: Path, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'exe')
    os.utime(path, (mtime, mtime))
    return path


def test_direct_terminate_requests_only_process_terminate_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)
    open_calls: list[tuple[int, bool, int]] = []
    terminate_calls: list[tuple[int, int]] = []
    close_calls: list[int] = []

    class _Function:
        def __init__(self, callback: Callable[..., int]) -> None:
            self.callback = callback
            self.argtypes: object | None = None
            self.restype: object | None = None

        def __call__(self, *args: object) -> int:
            return self.callback(*args)

    def open_process(access: int, inherit: bool, pid: int) -> int:
        open_calls.append((access, inherit, pid))
        return 123

    def terminate_process(handle: int, exit_code: int) -> int:
        terminate_calls.append((handle, exit_code))
        return 1

    def close_handle(handle: int) -> int:
        close_calls.append(handle)
        return 1

    kernel32 = SimpleNamespace(
        OpenProcess=_Function(open_process),
        TerminateProcess=_Function(terminate_process),
        CloseHandle=_Function(close_handle),
        GetLastError=_Function(_returns(0)),
    )
    monkeypatch.setattr(
        ctypes,
        'windll',
        SimpleNamespace(kernel32=kernel32),
        raising=False,
    )

    assert module.terminate_process_direct(4242) == (
        True,
        'TerminateProcess issued successfully',
    )
    assert open_calls == [(module.process_terminate, False, 4242)]
    assert terminate_calls == [(123, 1)]
    assert close_calls == [123]


def test_run_cmd_decodes_localized_console_output_with_windows_oem_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='Opération réussie', stderr='')

    monkeypatch.setattr(module.raw, '_windows_oem_encoding', _returns('cp850'))
    monkeypatch.setattr(subprocess, 'run', fake_run)

    assert module.run_cmd(['taskkill', '/PID', '100']) == (0, 'Opération réussie')
    assert calls[0][1]['encoding'] == 'cp850'


def test_gdk_package_lookup_allows_slow_powershell_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_platform_windows(monkeypatch)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(subprocess, 'run', fake_run)

    assert module.find_installed_roblox_gdk_package_identity() is None
    assert calls[0][1]['timeout'] == 20


def test_delete_storage_family_removes_sqlite_and_session_companions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    db_path = tmp_path / 'Roblox' / 'rbx-storage.db'
    db_path.parent.mkdir()
    for path in (
        db_path,
        Path(f'{db_path}-wal'),
        Path(f'{db_path}-shm'),
        Path(f'{db_path}-journal'),
        db_path.parent / 'rbx-storage.id',
    ):
        path.write_bytes(b'cache')
    for folder_name in ('rbx-storage', 'rbx-storage-sc'):
        folder = db_path.parent / folder_name
        folder.mkdir()
        (folder / 'entry').write_bytes(b'cache')

    messages: list[str] = []
    module.delete_storage_family(db_path, messages)

    assert not db_path.exists()
    assert not Path(f'{db_path}-wal').exists()
    assert not Path(f'{db_path}-shm').exists()
    assert not Path(f'{db_path}-journal').exists()
    assert not (db_path.parent / 'rbx-storage.id').exists()
    assert not (db_path.parent / 'rbx-storage').exists()
    assert not (db_path.parent / 'rbx-storage-sc').exists()
    assert 'Storage database deleted successfully' in messages
    assert 'Session storage folder deleted successfully' in messages


def test_cache_cleanup_messages_combine_routine_storage_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)

    messages = module.summarize_cache_messages(
        [
            'Roblox is running, terminating...',
            'Roblox terminated successfully',
            'Storage database deleted successfully',
            'Storage database WAL deleted successfully',
            'Storage database shared memory deleted successfully',
            'Storage identifier deleted successfully',
            'Storage folder deleted successfully',
            'Storage database (GDK) not found',
            'Storage folder (GDK) not found',
            'Fleasion obj cache deleted successfully',
        ]
    )

    assert messages == [
        'Roblox was running; terminated successfully',
        (
            'Roblox cache storage deleted successfully '
            '(database, database WAL, database shared memory, identifier, folder)'
        ),
        'Microsoft Store / GDK cache storage not found',
        'Fleasion obj cache deleted successfully',
    ]


def test_delete_cache_resets_live_replacement_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    roblox_dir = tmp_path / 'Roblox'
    roblox_dir.mkdir()
    db_path = roblox_dir / 'rbx-storage.db'
    db_path.write_bytes(b'db')
    app_cache = tmp_path / 'FleasionCache'
    app_cache.mkdir()

    module.raw.__dict__['STORAGE_DB'] = db_path
    module.raw.__dict__['STORAGE_DB_GDK'] = db_path
    monkeypatch.setattr(module.raw, 'is_roblox_running', _returns(False))
    sys.modules['fleasion.utils.paths'].__dict__['APP_CACHE_DIR'] = app_cache

    proxy_pkg = types.ModuleType('fleasion.proxy')
    proxy_pkg.__path__ = []
    addons_pkg = types.ModuleType('fleasion.proxy.addons')
    addons_pkg.__path__ = []
    texture_module = types.ModuleType('fleasion.proxy.addons.texture_stripper')
    reset_reasons: list[str] = []

    class _TextureStripper:
        @classmethod
        def reset_routes(cls, reason: str) -> dict[str, int]:
            reset_reasons.append(reason)
            return {'pending': 1}

    texture_module.__dict__['TextureStripper'] = _TextureStripper
    monkeypatch.setitem(sys.modules, 'fleasion.proxy', proxy_pkg)
    monkeypatch.setitem(sys.modules, 'fleasion.proxy.addons', addons_pkg)
    monkeypatch.setitem(sys.modules, 'fleasion.proxy.addons.texture_stripper', texture_module)

    messages = module.delete_cache()

    assert reset_reasons == ['cache clear']
    assert 'Fleasion replacement routes cleared successfully' in messages


def _skip_immediate_close_for_relaunch_test(
    monkeypatch: pytest.MonkeyPatch, module: _PlatformWindowsAdapter
) -> None:
    monkeypatch.setattr(
        module.raw,
        '_force_close_process_immediately',
        _returns(True),
    )


def test_windows_relaunch_extractor_preserves_both_roblox_uri_forms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)

    assert (
        module.extract_roblox_deeplink('RobloxPlayerBeta.exe roblox-player:1+launchmode:play')
        == 'roblox-player:1+launchmode:play'
    )
    assert (
        module.extract_roblox_deeplink('RobloxPlayerBeta.exe roblox://experiences/start?placeId=1')
        == 'roblox://experiences/start?placeId=1'
    )


def test_windows_process_query_uses_native_unicode_path_and_command_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    exe = _touch(
        tmp_path / 'Michael Březina' / 'Roblox' / 'RobloxPlayerBeta.exe',
        3000,
    )
    command_line = f'"{exe}" roblox-player:1+launchmode:play'

    monkeypatch.setattr(module.raw, '_find_pids', _returns([123]))
    monkeypatch.setattr(module.raw, '_query_exe_path', _returns(exe))
    monkeypatch.setattr(module.raw, '_query_process_command_line', _returns(command_line))
    monkeypatch.setattr(
        subprocess,
        'run',
        _raises(AssertionError('native process discovery must not start PowerShell')),
    )

    assert module.query_roblox_processes('RobloxPlayerBeta.exe') == [
        {
            'ProcessId': 123,
            'ExecutablePath': str(exe),
            'CommandLine': command_line,
        }
    ]


def test_windows_native_command_line_query_preserves_unicode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)
    command_line = (
        '"C:\\Users\\Michael Březina\\RobloxPlayerBeta.exe" roblox-player:1+launchmode:play'
    )
    encoded = command_line.encode('utf-16-le')

    class _NativeQuery:
        argtypes: object | None = None
        restype: object | None = None

        def __call__(
            self,
            _handle: int,
            info_class: int,
            buffer: ctypes.Array[ctypes.c_char] | None,
            _size: int,
            needed: ctypes.c_void_p,
        ) -> int:
            assert info_class == module.process_command_line_information
            total_size = ctypes.sizeof(module.unicode_string_type) + len(encoded) + 2
            ctypes.cast(
                needed,
                ctypes.POINTER(ctypes.wintypes.ULONG),
            ).contents.value = total_size
            if buffer is None:
                return ctypes.c_long(module.status_info_length_mismatch).value

            string_address = ctypes.addressof(buffer) + ctypes.sizeof(module.unicode_string_type)
            ctypes.memmove(string_address, encoded, len(encoded))
            info = module.unicode_string_type.from_buffer(buffer)
            info.Length = len(encoded)
            info.MaximumLength = len(encoded) + 2
            info.Buffer = string_address
            return 0

    closed: list[int] = []

    def close_handle(handle: int) -> None:
        closed.append(handle)

    monkeypatch.setattr(
        ctypes,
        'windll',
        SimpleNamespace(
            kernel32=SimpleNamespace(
                OpenProcess=_returns(99),
                CloseHandle=close_handle,
            ),
            ntdll=SimpleNamespace(NtQueryInformationProcess=_NativeQuery()),
        ),
        raising=False,
    )

    assert module.query_process_command_line(123) == command_line
    assert closed == [99]


def test_gdk_arming_reports_missing_installation_precisely(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_platform_windows(monkeypatch)
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(
        subprocess,
        'run',
        _returns(SimpleNamespace(returncode=0, stdout='')),
    )
    monkeypatch.setattr(
        module.raw,
        'log_buffer',
        SimpleNamespace(log=_LogRecorder(messages)),
    )

    assert not module.arm_roblox_gdk_env_proxy('http://127.0.0.1:58443')
    assert messages == [('Launcher', 'No GDK Roblox installation found')]


def test_windows_identifies_the_store_gdk_player_path(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_platform_windows(monkeypatch)

    assert module.is_roblox_gdk_exe_path(
        r'C:\Program Files\WindowsApps\ROBLOXCorporation.'
        r'RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr\RobloxPlayerBeta.exe'
    )
    assert not module.is_roblox_gdk_exe_path(
        r'C:\Users\Sviat\AppData\Local\Roblox\Versions\version-current\RobloxPlayerBeta.exe'
    )
    assert module.is_roblox_gdk_exe_path(r'C:\XboxGames\Roblox\Content\RobloxPlayerBeta.exe')


def test_windows_reads_store_package_full_name_and_aumid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    package = (
        tmp_path
        / 'Program Files'
        / 'WindowsApps'
        / 'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr'
    )
    exe = _touch(package / 'RobloxPlayerBeta.exe', 3000)
    (package / 'AppxManifest.xml').write_text(
        '<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">'
        '<Applications><Application Id="Game" Executable="GameLaunchHelper.exe" />'
        '</Applications></Package>',
        encoding='utf-8',
    )

    assert module.get_roblox_gdk_package_identity(exe) == (
        'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr',
        'ROBLOXCorporation.RobloxGDK_55nm5eh3cm0pr!Game',
    )


def test_gdk_repair_activation_falls_back_to_registered_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)
    exe = Path(r'C:\XboxGames\Roblox\Content\RobloxPlayerBeta.exe')
    package = (
        'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr',
        'ROBLOXCorporation.RobloxGDK_55nm5eh3cm0pr!Game',
    )
    calls: list[str] = []

    def find_package() -> tuple[str, str]:
        calls.append('lookup')
        return package

    monkeypatch.setattr(module.raw, '_get_roblox_gdk_package_identity', _returns(None))
    monkeypatch.setattr(
        module.raw,
        '_find_installed_roblox_gdk_package_identity',
        find_package,
    )

    result = module.activate_roblox_gdk_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        pid=100,
        exe_path=exe,
        launch_arg='',
        query_processes=_returns(list[_ProcessInfo]()),
        prepare_launch=None,
        cancel_event=_CancelEventStub(True),
    )
    assert result is None
    assert calls == ['lookup']


def test_forced_gdk_relaunch_receives_ca_preparation_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    exe = _touch(
        tmp_path
        / 'Program Files'
        / 'WindowsApps'
        / 'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr'
        / 'RobloxPlayerBeta.exe',
        3000,
    )

    def prepare(_path: Path) -> bool:
        return True

    calls: list[dict[str, object]] = []

    def activate(*_args: object, **kwargs: object) -> tuple[int, str]:
        calls.append(kwargs)
        return 200, str(exe)

    monkeypatch.setattr(module.raw, '_activate_roblox_gdk_with_proxy_env', activate)

    assert module.relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=_returns([_process_info(100, exe)]),
        extract_launch_arg=_returns(''),
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=_returns(exe),
        force=True,
        prepare_launch=prepare,
    )
    assert calls
    assert calls[0]['prepare_launch'] is prepare


def test_windows_proxy_environment_block_is_double_nul_terminated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)

    block = module.package_environment_block({'HTTP_PROXY': 'http://127.0.0.1:1'})

    assert block.value == 'HTTP_PROXY=http://127.0.0.1:1'
    assert block[block._length_ - 1] == '\x00'
    assert block[block._length_ - 2] == '\x00'


def test_force_close_kills_immediately_before_waiting_for_process_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)
    events: list[tuple[str, ...] | str] = []

    def run_cmd(args: list[str]) -> tuple[int, str]:
        events.append(tuple(args))
        return 0, ''

    def wait_for_pid_exit(
        _pid: int,
        _exe_name: str,
        _timeout: float,
        _cancel_event: _CancelEvent | None = None,
    ) -> bool:
        events.append('pid_exit')
        return True

    monkeypatch.setattr(module.raw, 'run_cmd', run_cmd)
    monkeypatch.setattr(module.raw, '_wait_for_pid_exit', wait_for_pid_exit)

    assert module.force_close_process_immediately(
        100,
        'RobloxPlayerBeta.exe',
        label='Roblox',
    )
    assert events == [
        ('taskkill', '/F', '/PID', '100'),
        'pid_exit',
    ]


def test_force_close_uses_direct_terminate_when_taskkill_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)
    events: list[tuple[str, ...] | tuple[str, int] | str] = []
    logs: list[tuple[str, str]] = []

    def run_cmd(args: list[str]) -> tuple[int, str]:
        events.append(tuple(args))
        return 1, 'ERROR: The specified service does not exist as an installed service.'

    def terminate_process(pid: int) -> tuple[bool, str]:
        events.append(('direct', pid))
        return True, 'TerminateProcess issued successfully'

    def wait_for_pid_exit(
        _pid: int,
        _exe_name: str,
        _timeout: float,
        _cancel_event: _CancelEvent | None = None,
    ) -> bool:
        events.append('pid_exit')
        return True

    monkeypatch.setattr(module.raw, 'run_cmd', run_cmd)
    monkeypatch.setattr(module.raw, '_terminate_process_direct', terminate_process)
    monkeypatch.setattr(module.raw, '_wait_for_pid_exit', wait_for_pid_exit)
    monkeypatch.setattr(
        module.log_buffer,
        'log',
        _LogRecorder(logs),
    )

    assert module.force_close_process_immediately(
        100,
        'RobloxPlayerBeta.exe',
        label='Roblox',
    )
    assert events == [
        ('taskkill', '/F', '/PID', '100'),
        ('direct', 100),
        'pid_exit',
    ]
    assert any('service does not exist' in message for _, message in logs)
    assert any('direct PROCESS_TERMINATE fallback' in message for _, message in logs)


def test_terminate_roblox_uses_minimal_rights_force_kill_after_taskkill_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)
    logs: list[tuple[str, str]] = []
    commands: list[tuple[str, ...]] = []
    direct_calls: list[int] = []
    process_snapshots: Iterator[list[int]] = iter(([100], [100], list[int]()))
    exit_results: Iterator[bool] = iter((False, True))

    def terminate_process(pid: int) -> tuple[bool, str]:
        direct_calls.append(pid)
        return True, 'TerminateProcess issued successfully'

    monkeypatch.setattr(module.raw, '_find_pids', _NextResult(process_snapshots))
    monkeypatch.setattr(module.raw, '_pid_is_running', _returns(True))
    monkeypatch.setattr(module.raw, '_is_process_elevated', _returns(True))
    monkeypatch.setattr(module.raw, '_describe_pids', _describe_pids)
    monkeypatch.setattr(module.raw, '_terminate_process_direct', terminate_process)
    monkeypatch.setattr(
        module.raw,
        '_request_process_window_close',
        _raises(AssertionError('WM_CLOSE must not run when direct force termination succeeds')),
    )
    monkeypatch.setattr(module.raw, '_wait_for_pid_exit', _NextResult(exit_results))

    def run_cmd(args: list[str]) -> tuple[int, str]:
        commands.append(tuple(args))
        return 1, 'ERROR: Access is denied.'

    monkeypatch.setattr(module.raw, 'run_cmd', run_cmd)
    monkeypatch.setattr(
        module.log_buffer,
        'log',
        _LogRecorder(logs),
    )

    assert module.terminate_roblox()
    assert commands == [('taskkill', '/F', '/PID', '100')]
    assert direct_calls == [100]
    assert any('returned 1' in message and 'Access is denied' in message for _, message in logs)
    assert any('direct PROCESS_TERMINATE fallback' in message for _, message in logs)
    assert any('exited after direct PROCESS_TERMINATE fallback' in message for _, message in logs)


def test_terminate_roblox_uses_window_close_after_direct_force_kill_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)
    logs: list[tuple[str, str]] = []
    process_snapshots: Iterator[list[int]] = iter(([100], [100], list[int]()))
    exit_results: Iterator[bool] = iter((False, True))

    monkeypatch.setattr(module.raw, '_find_pids', _NextResult(process_snapshots))
    monkeypatch.setattr(module.raw, '_pid_is_running', _returns(True))
    monkeypatch.setattr(module.raw, '_is_process_elevated', _returns(False))
    monkeypatch.setattr(module.raw, '_describe_pids', _describe_pids)
    monkeypatch.setattr(
        module.raw,
        '_terminate_process_direct',
        _returns((False, 'OpenProcess(PROCESS_TERMINATE) failed with WinError 5')),
    )
    monkeypatch.setattr(module.raw, '_request_process_window_close', _returns(True))
    monkeypatch.setattr(module.raw, '_wait_for_pid_exit', _NextResult(exit_results))
    monkeypatch.setattr(module.raw, 'run_cmd', _returns((1, 'ERROR: Access is denied.')))
    monkeypatch.setattr(
        module.log_buffer,
        'log',
        _LogRecorder(logs),
    )

    assert module.terminate_roblox()
    assert any('WinError 5' in message for _, message in logs)
    assert any('WM_CLOSE fallback' in message for _, message in logs)
    assert any('exited after WM_CLOSE fallback' in message for _, message in logs)


def test_terminate_roblox_logs_taskkill_result_for_every_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)
    commands: list[tuple[str, ...]] = []
    logs: list[tuple[str, str]] = []
    process_snapshots: Iterator[list[int]] = iter(([100, 200], list[int]()))

    monkeypatch.setattr(module.raw, '_find_pids', _NextResult(process_snapshots))
    monkeypatch.setattr(module.raw, '_pid_is_running', _returns(True))
    monkeypatch.setattr(module.raw, '_is_process_elevated', _returns(False))
    monkeypatch.setattr(module.raw, '_describe_pids', _describe_pids)
    monkeypatch.setattr(
        module.raw,
        '_terminate_process_direct',
        _raises(AssertionError('direct fallback must not run after successful taskkill')),
    )
    monkeypatch.setattr(
        module.raw,
        '_request_process_window_close',
        _raises(AssertionError('WM_CLOSE fallback must not run after successful taskkill')),
    )
    monkeypatch.setattr(module.raw, '_wait_for_pid_exit', _returns(True))

    def run_cmd(args: list[str]) -> tuple[int, str]:
        commands.append(tuple(args))
        return 0, f'SUCCESS: {args[-1]}'

    monkeypatch.setattr(module.raw, 'run_cmd', run_cmd)
    monkeypatch.setattr(
        module.log_buffer,
        'log',
        _LogRecorder(logs),
    )

    assert module.terminate_roblox()
    assert commands == [
        ('taskkill', '/F', '/PID', '100'),
        ('taskkill', '/F', '/PID', '200'),
    ]
    assert any('PID 100 returned 0' in message for _, message in logs)
    assert any('PID 200 returned 0' in message for _, message in logs)


def test_terminate_roblox_reports_taskkill_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_platform_windows(monkeypatch)
    logs: list[tuple[str, str]] = []

    monkeypatch.setattr(module.raw, '_find_pids', _returns([100]))
    monkeypatch.setattr(module.raw, '_pid_is_running', _returns(True))
    monkeypatch.setattr(module.raw, '_is_process_elevated', _returns(True))
    monkeypatch.setattr(module.raw, '_describe_pids', _describe_pids)
    monkeypatch.setattr(module.raw, '_wait_for_pid_exit', _returns(False))
    monkeypatch.setattr(
        module.raw,
        '_terminate_process_direct',
        _returns((False, 'OpenProcess(PROCESS_TERMINATE) failed with WinError 5')),
    )
    monkeypatch.setattr(module.raw, '_request_process_window_close', _returns(False))
    monkeypatch.setattr(
        module.log_buffer,
        'log',
        _LogRecorder(logs),
    )

    def timeout(_args: object) -> Never:
        raise subprocess.TimeoutExpired(cmd='taskkill', timeout=10)

    monkeypatch.setattr(module.raw, 'run_cmd', timeout)

    assert not module.terminate_roblox()
    assert any('timed out after 10 seconds' in message for _, message in logs)


def test_terminate_roblox_logs_taskkill_failure_output(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_platform_windows(monkeypatch)
    logs: list[tuple[str, str]] = []
    process_snapshots: Iterator[list[int]] = iter(([100], [100], [100]))

    monkeypatch.setattr(module.raw, '_find_pids', _NextResult(process_snapshots))
    monkeypatch.setattr(module.raw, '_pid_is_running', _returns(True))
    monkeypatch.setattr(module.raw, '_is_process_elevated', _returns(False))
    monkeypatch.setattr(module.raw, '_describe_pids', _describe_pids)
    monkeypatch.setattr(
        module.raw,
        '_terminate_process_direct',
        _returns((False, 'OpenProcess(PROCESS_TERMINATE) failed with WinError 5')),
    )
    monkeypatch.setattr(module.raw, '_request_process_window_close', _returns(False))
    monkeypatch.setattr(module.raw, '_wait_for_pid_exit', _returns(False))
    monkeypatch.setattr(module.raw, 'run_cmd', _returns((5, 'ERROR: Access is denied.')))
    monkeypatch.setattr(
        module.log_buffer,
        'log',
        _LogRecorder(logs),
    )

    assert not module.terminate_roblox()
    assert any('returned 5' in message and 'Access is denied' in message for _, message in logs)


def test_env_proxy_relaunch_leaves_store_gdk_player_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    exe = _touch(
        tmp_path
        / 'Program Files'
        / 'WindowsApps'
        / 'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr'
        / 'RobloxPlayerBeta.exe',
        3000,
    )
    commands: list[list[str]] = []

    def run_cmd(args: list[str]) -> str:
        commands.append(args)
        return ''

    monkeypatch.setattr(module.raw, 'run_cmd', run_cmd)

    assert not module.relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=_returns([_process_info(100, exe)]),
        extract_launch_arg=_returns(''),
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=_returns(exe),
    )
    assert commands == []


def test_env_proxy_relaunch_adopts_armed_store_gdk_player(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    exe = _touch(
        tmp_path
        / 'Program Files'
        / 'WindowsApps'
        / 'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr'
        / 'RobloxPlayerBeta.exe',
        3000,
    )
    commands: list[list[str]] = []

    def run_cmd(args: list[str]) -> str:
        commands.append(args)
        return ''

    monkeypatch.setattr(module.raw, '_gdk_env_proxy_armed_package', ('package', 'aumid'))
    monkeypatch.setattr(module.raw, 'run_cmd', run_cmd)

    assert module.relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=_returns([_process_info(100, exe)]),
        extract_launch_arg=_returns(''),
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=_returns(exe),
    )
    assert commands == []
    assert module.env_proxy_owned_process == (100, str(exe))


def test_env_proxy_lifecycle_closes_owned_store_gdk_player(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    exe = _touch(
        tmp_path
        / 'Program Files'
        / 'WindowsApps'
        / 'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr'
        / 'RobloxPlayerBeta.exe',
        3000,
    )
    module.env_proxy_owned_process = (100, str(exe))
    events: list[str] = []

    def request_window_close(_pid: int) -> bool:
        events.append('window_close')
        return True

    def wait_for_pid_exit(
        _pid: int,
        _exe_name: str,
        _timeout: float,
        _cancel_event: _CancelEvent | None = None,
    ) -> bool:
        events.append('pid_exit')
        return True

    monkeypatch.setattr(module.raw, '_query_exe_path', _returns(exe))
    monkeypatch.setattr(module.raw, '_request_process_window_close', request_window_close)
    monkeypatch.setattr(module.raw, '_wait_for_pid_exit', wait_for_pid_exit)

    assert module.close_roblox_for_env_lifecycle()
    assert events == ['window_close', 'pid_exit']
    assert module.env_proxy_owned_process is None


def test_env_proxy_relaunch_skips_gdk_even_when_helper_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    install_dir = (
        tmp_path
        / 'Program Files'
        / 'WindowsApps'
        / 'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr'
    )
    exe = _touch(install_dir / 'RobloxPlayerBeta.exe', 3000)
    _touch(install_dir / 'GameLaunchHelper.exe', 3000)
    (install_dir / 'AppxManifest.xml').write_text(
        '<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">'
        '<Applications><Application Id="Game" Executable="GameLaunchHelper.exe" />'
        '</Applications></Package>',
        encoding='utf-8',
    )
    commands: list[list[str]] = []

    def run_cmd(args: list[str]) -> str:
        commands.append(args)
        return ''

    monkeypatch.setattr(module.raw, 'run_cmd', run_cmd)

    assert not module.relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=_returns([_process_info(100, exe)]),
        extract_launch_arg=_returns(''),
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=_returns(exe),
        prepare_launch=_path_equals(exe),
    )
    assert commands == []


def test_env_proxy_relaunch_skips_the_proxy_owned_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    _skip_immediate_close_for_relaunch_test(monkeypatch, module)
    exe = _touch(tmp_path / 'Content' / 'RobloxPlayerBeta.exe', 3000)
    running_pids: set[int] = {100}
    current: dict[str, int] = {'pid': 100}
    popen_pids: Iterator[int] = iter((200,))

    monkeypatch.setattr(
        module.raw,
        '_iter_processes',
        lambda: iter([(pid, 'robloxplayerbeta.exe') for pid in running_pids]),
    )
    monkeypatch.setattr(module.raw, '_find_pid', _returns(None))
    monkeypatch.setattr(module.raw, 'run_cmd', _returns(''))

    def fake_popen(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(pid=next(popen_pids))

    def query() -> list[_ProcessInfo]:
        return [_process_info(current['pid'], exe)]

    monkeypatch.setattr(subprocess, 'Popen', fake_popen)
    assert module.relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=query,
        extract_launch_arg=_returns(''),
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=_returns(exe),
    )

    running_pids.add(200)
    current['pid'] = 200
    assert not module.relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=query,
        extract_launch_arg=_returns(''),
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=_returns(exe),
    )


def test_env_proxy_relaunch_allows_new_process_after_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    _skip_immediate_close_for_relaunch_test(monkeypatch, module)
    exe = _touch(tmp_path / 'Content' / 'RobloxPlayerBeta.exe', 3000)
    running_pids: set[int] = {100}
    current: dict[str, int] = {'pid': 100}
    popen_pids: Iterator[int] = iter((200, 300))

    monkeypatch.setattr(
        module.raw,
        '_iter_processes',
        lambda: iter([(pid, 'robloxplayerbeta.exe') for pid in running_pids]),
    )
    monkeypatch.setattr(module.raw, '_find_pid', _returns(None))
    monkeypatch.setattr(module.raw, 'run_cmd', _returns(''))

    def fake_popen(*_args: object, **_kwargs: object) -> SimpleNamespace:
        pid = next(popen_pids)
        running_pids.add(pid)
        return SimpleNamespace(pid=pid)

    def query() -> list[_ProcessInfo]:
        return [_process_info(current['pid'], exe)]

    monkeypatch.setattr(subprocess, 'Popen', fake_popen)

    assert module.relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=query,
        extract_launch_arg=_returns(''),
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=_returns(exe),
    )
    running_pids.remove(200)
    current['pid'] = 100
    assert module.relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=query,
        extract_launch_arg=_returns(''),
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=_returns(exe),
    )


def test_env_proxy_relaunch_rechecks_a_replacement_player_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    exe = _touch(tmp_path / 'Content' / 'RobloxPlayerBeta.exe', 3000)
    snapshots: Iterator[list[_ProcessInfo]] = iter(
        (
            [_process_info(100, exe, 'RobloxPlayerBeta.exe roblox-player:stale-uri')],
            [_process_info(200, exe, 'RobloxPlayerBeta.exe roblox-player:successor-uri')],
        )
    )
    close_calls: list[int] = []
    popen_calls: list[list[str]] = []
    logs: list[tuple[str, str]] = []

    def force_close(pid: int, *_args: object, **_kwargs: object) -> bool:
        close_calls.append(pid)
        return pid == 200

    def fake_popen(args: list[str], **_kwargs: object) -> SimpleNamespace:
        popen_calls.append(args)
        return SimpleNamespace(pid=300)

    monkeypatch.setattr(module.raw, '_pid_is_running', _returns(False))
    monkeypatch.setattr(module.raw, '_force_close_process_immediately', force_close)
    monkeypatch.setattr(module.raw, '_proxy_environment', _returns(dict[str, str]()))
    monkeypatch.setattr(subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(
        module.log_buffer,
        'log',
        _LogRecorder(logs),
    )

    assert module.relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=_NextResult(snapshots),
        extract_launch_arg=_last_argument,
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=_returns(exe),
    )
    assert close_calls == [100, 200]
    assert popen_calls == [[str(exe), 'roblox-player:successor-uri']]
    assert any('rechecking successor PID(s): 200' in message for _, message in logs)


def test_env_proxy_relaunch_does_not_replay_uri_when_no_successor_is_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_platform_windows(monkeypatch)
    exe = _touch(tmp_path / 'Content' / 'RobloxPlayerBeta.exe', 3000)
    snapshots: Iterator[list[_ProcessInfo]] = iter(
        (
            [_process_info(100, exe, 'RobloxPlayerBeta.exe roblox-player:one-time-uri')],
            list[_ProcessInfo](),
        )
    )
    popen_calls: list[list[str]] = []
    logs: list[tuple[str, str]] = []

    def fake_popen(args: list[str], **_kwargs: object) -> SimpleNamespace:
        popen_calls.append(args)
        return SimpleNamespace(pid=300)

    monkeypatch.setattr(module.raw, '_pid_is_running', _returns(False))
    monkeypatch.setattr(module.raw, '_force_close_process_immediately', _returns(False))
    monkeypatch.setattr(subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(
        module.log_buffer,
        'log',
        _LogRecorder(logs),
    )

    assert not module.relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=_NextResult(snapshots),
        extract_launch_arg=_last_argument,
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=_returns(exe),
    )
    assert popen_calls == []
    assert any('not replaying a potentially consumed launch URI' in message for _, message in logs)


def test_roblox_launch_resolver_upgrades_registry_path_when_versions_scan_finds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_appdata = tmp_path / 'LocalAppData'
    versions = local_appdata / 'Roblox' / 'Versions'
    current = _touch(versions / 'version-current' / 'RobloxPlayerBeta.exe', 3000)
    stale = _touch(versions / 'version-stale' / 'RobloxPlayerBeta.exe', 2000)
    registry_command = f'"{current}" %1'

    module = _load_platform_windows(monkeypatch, registry_command=registry_command)
    monkeypatch.setattr(module.raw, 'get_roblox_player_exe_path', _returns(None))
    monkeypatch.setattr(module.raw, 'LOCAL_APPDATA', local_appdata)

    assert module.safe_mtime(current) > module.safe_mtime(stale)
    assert module.resolve_roblox_player_exe_for_launch() == current


def test_roblox_launch_resolver_finds_player_near_registered_custom_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom_root = tmp_path / 'CustomBootstrapper'
    launcher = _touch(custom_root / 'Bootstrapper.exe', 4000)
    player = _touch(custom_root / 'Versions' / 'version-current' / 'RobloxPlayerBeta.exe', 3000)
    registry_command = f'"{launcher}" %1'

    module = _load_platform_windows(monkeypatch, registry_command=registry_command)
    monkeypatch.setattr(module.raw, 'get_roblox_player_exe_path', _returns(None))
    monkeypatch.setattr(module.raw, 'LOCAL_APPDATA', tmp_path / 'EmptyLocalAppData')

    assert module.resolve_roblox_player_exe_for_launch() == player


def test_roblox_launch_resolver_logs_source_diagnostics_when_nothing_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_command = f'"{tmp_path / "MissingBootstrapper.exe"}" %1'
    module = _load_platform_windows(monkeypatch, registry_command=registry_command)
    logs: list[tuple[str, str]] = []
    monkeypatch.setattr(module.raw, 'get_roblox_player_exe_path', _returns(None))
    monkeypatch.setattr(module.raw, 'LOCAL_APPDATA', tmp_path / 'EmptyLocalAppData')
    monkeypatch.setattr(module.raw, '_scan_for_player_exes', _no_paths)
    monkeypatch.setattr(
        module.log_buffer,
        'log',
        _LogRecorder(logs),
    )

    assert module.resolve_roblox_player_exe_for_launch() is None
    messages = [message for category, message in logs if category == 'Launcher']
    assert any('resolver diagnostics' in message for message in messages)
    assert any('running Roblox process' in message for message in messages)
    assert any('roblox-player protocol' in message for message in messages)
    assert any('LocalAppData Roblox\\Versions scan' in message for message in messages)
    assert any('Program Files Roblox\\Versions scan' in message for message in messages)


def test_roblox_launch_resolver_uses_valid_saved_custom_install_as_last_resort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved_dir = tmp_path / 'SavedCustomInstall'
    player = _touch(saved_dir / 'RobloxPlayerBeta.exe', 3000)
    roblox_dirs = types.ModuleType('fleasion.utils.roblox_dirs')
    roblox_dirs.__dict__['load_saved_roblox_dirs'] = _returns([saved_dir])

    module = _load_platform_windows(monkeypatch, registry_command=None)
    monkeypatch.setitem(sys.modules, 'fleasion.utils.roblox_dirs', roblox_dirs)
    monkeypatch.setattr(module.raw, 'get_roblox_player_exe_path', _returns(None))
    monkeypatch.setattr(module.raw, 'LOCAL_APPDATA', tmp_path / 'EmptyLocalAppData')
    monkeypatch.setattr(module.raw, '_scan_for_player_exes', _no_paths)

    assert module.resolve_roblox_player_exe_for_launch() == player


def test_roblox_launch_resolver_prefers_current_install_over_stale_running_player(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_appdata = tmp_path / 'LocalAppData'
    versions = local_appdata / 'Roblox' / 'Versions'
    current = _touch(versions / 'version-current' / 'RobloxPlayerBeta.exe', 3000)
    stale = _touch(versions / 'version-stale' / 'RobloxPlayerBeta.exe', 2000)
    registry_command = f'"{current}" %1'

    module = _load_platform_windows(monkeypatch, registry_command=registry_command)
    monkeypatch.setattr(module.raw, 'get_roblox_player_exe_path', lambda: stale)
    monkeypatch.setattr(module.raw, 'LOCAL_APPDATA', local_appdata)

    assert module.resolve_roblox_player_exe_for_launch() == current


def test_roblox_launch_resolver_rejects_registry_installer_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _touch(
        tmp_path
        / 'LocalAppData'
        / 'Roblox'
        / 'Versions'
        / 'version-current'
        / 'RobloxPlayerInstaller.exe',
        3000,
    )
    registry_command = f'"{installer}" -app -force'

    module = _load_platform_windows(monkeypatch, registry_command=registry_command)
    monkeypatch.setattr(module.raw, 'get_roblox_player_exe_path', _returns(None))
    monkeypatch.setattr(module.raw, '_scan_for_player_exes', _no_paths)
    monkeypatch.setattr(module.raw, 'LOCAL_APPDATA', tmp_path / 'LocalAppData')

    assert module.resolve_roblox_player_exe_for_launch() is None


def test_roblox_launch_resolver_rejects_running_installer_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _touch(
        tmp_path
        / 'LocalAppData'
        / 'Roblox'
        / 'Versions'
        / 'version-current'
        / 'RobloxPlayerInstaller.exe',
        3000,
    )

    module = _load_platform_windows(monkeypatch, registry_command=None)
    monkeypatch.setattr(module.raw, 'get_roblox_player_exe_path', lambda: installer)
    monkeypatch.setattr(module.raw, '_scan_for_player_exes', _no_paths)
    monkeypatch.setattr(module.raw, 'LOCAL_APPDATA', tmp_path / 'LocalAppData')

    assert module.resolve_roblox_player_exe_for_launch() is None


def test_env_proxy_has_no_automatic_firewall_rule_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_platform_windows(monkeypatch)
    assert not hasattr(module, 'install_fleasion_firewall_rules')


def test_extract_exe_from_command_fallback_has_shlex_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_platform_windows(monkeypatch)

    assert module.extract_exe_from_command('launcher --flag') == Path('launcher')
