import ctypes
import importlib.util
import os
import sys
import types
import subprocess
from pathlib import Path
from types import SimpleNamespace


def _load_platform_windows(monkeypatch, registry_command: str | None = None):
    source = Path(__file__).resolve().parents[1] / "src" / "fleasion" / "utils" / "platform_windows.py"

    paths = types.ModuleType("fleasion.utils.paths")
    paths.LOCAL_APPDATA = ""
    paths.ROBLOX_PROCESS = "RobloxPlayerBeta.exe"
    paths.ROBLOX_STUDIO_PROCESS = "RobloxStudioBeta.exe"
    paths.STORAGE_DB = ""
    paths.STORAGE_DB_GDK = ""

    logging = types.ModuleType("fleasion.utils.logging")
    logging.log_buffer = types.SimpleNamespace(log=lambda *_args, **_kwargs: None)

    winreg = types.ModuleType("winreg")
    winreg.HKEY_CURRENT_USER = object()

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def _open_key(*_args, **_kwargs):
        if registry_command is None:
            raise OSError
        return _Key()

    def _query_value_ex(*_args, **_kwargs):
        return registry_command, 1

    winreg.OpenKey = _open_key
    winreg.QueryValueEx = _query_value_ex

    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    monkeypatch.setitem(sys.modules, "fleasion", types.ModuleType("fleasion"))
    monkeypatch.setitem(sys.modules, "fleasion.utils", types.ModuleType("fleasion.utils"))
    monkeypatch.setitem(sys.modules, "fleasion.utils.paths", paths)
    monkeypatch.setitem(sys.modules, "fleasion.utils.logging", logging)
    monkeypatch.setitem(sys.modules, "winreg", winreg)

    module_name = "fleasion.utils.platform_windows_under_test"
    spec = importlib.util.spec_from_file_location(module_name, source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def _touch(path: Path, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"exe")
    os.utime(path, (mtime, mtime))
    return path


def test_run_cmd_decodes_localized_console_output_with_windows_oem_page(monkeypatch):
    module = _load_platform_windows(monkeypatch)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='Opération réussie', stderr='')

    monkeypatch.setattr(module, '_windows_oem_encoding', lambda: 'cp850')
    monkeypatch.setattr(module.subprocess, 'run', fake_run)

    assert module.run_cmd(['taskkill', '/PID', '100']) == (0, 'Opération réussie')
    assert calls[0][1]['encoding'] == 'cp850'


def test_gdk_package_lookup_allows_slow_powershell_startup(monkeypatch):
    module = _load_platform_windows(monkeypatch)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(module.subprocess, 'run', fake_run)

    assert module._find_installed_roblox_gdk_package_identity() is None
    assert calls[0][1]['timeout'] == 20


def test_delete_storage_family_removes_sqlite_and_session_companions(monkeypatch, tmp_path):
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

    messages = []
    module._delete_storage_family(db_path, messages)

    assert not db_path.exists()
    assert not Path(f'{db_path}-wal').exists()
    assert not Path(f'{db_path}-shm').exists()
    assert not Path(f'{db_path}-journal').exists()
    assert not (db_path.parent / 'rbx-storage.id').exists()
    assert not (db_path.parent / 'rbx-storage').exists()
    assert not (db_path.parent / 'rbx-storage-sc').exists()
    assert 'Storage database deleted successfully' in messages
    assert 'Session storage folder deleted successfully' in messages


def test_cache_cleanup_messages_combine_routine_storage_statuses(monkeypatch):
    module = _load_platform_windows(monkeypatch)

    messages = module._summarize_cache_messages(
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
        'Roblox cache storage deleted successfully '
        '(database, database WAL, database shared memory, identifier, folder)',
        'Microsoft Store / GDK cache storage not found',
        'Fleasion obj cache deleted successfully',
    ]


def test_delete_cache_resets_live_replacement_routes(monkeypatch, tmp_path):
    module = _load_platform_windows(monkeypatch)
    roblox_dir = tmp_path / 'Roblox'
    roblox_dir.mkdir()
    db_path = roblox_dir / 'rbx-storage.db'
    db_path.write_bytes(b'db')
    app_cache = tmp_path / 'FleasionCache'
    app_cache.mkdir()

    module.STORAGE_DB = db_path
    module.STORAGE_DB_GDK = db_path
    monkeypatch.setattr(module, 'is_roblox_running', lambda: False)
    sys.modules['fleasion.utils.paths'].APP_CACHE_DIR = app_cache

    proxy_pkg = types.ModuleType('fleasion.proxy')
    proxy_pkg.__path__ = []
    addons_pkg = types.ModuleType('fleasion.proxy.addons')
    addons_pkg.__path__ = []
    texture_module = types.ModuleType('fleasion.proxy.addons.texture_stripper')
    reset_reasons = []

    class _TextureStripper:
        @classmethod
        def reset_routes(cls, reason):
            reset_reasons.append(reason)
            return {'pending': 1}

    texture_module.TextureStripper = _TextureStripper
    monkeypatch.setitem(sys.modules, 'fleasion.proxy', proxy_pkg)
    monkeypatch.setitem(sys.modules, 'fleasion.proxy.addons', addons_pkg)
    monkeypatch.setitem(sys.modules, 'fleasion.proxy.addons.texture_stripper', texture_module)

    messages = module.delete_cache()

    assert reset_reasons == ['cache clear']
    assert 'Fleasion replacement routes cleared successfully' in messages


def _skip_immediate_close_for_relaunch_test(monkeypatch, module):
    monkeypatch.setattr(
        module,
        "_force_close_process_immediately",
        lambda *_args, **_kwargs: True,
    )


def test_windows_relaunch_extractor_preserves_both_roblox_uri_forms(monkeypatch):
    module = _load_platform_windows(monkeypatch)

    assert module._extract_roblox_deeplink(
        'RobloxPlayerBeta.exe roblox-player:1+launchmode:play'
    ) == 'roblox-player:1+launchmode:play'
    assert module._extract_roblox_deeplink(
        'RobloxPlayerBeta.exe roblox://experiences/start?placeId=1'
    ) == 'roblox://experiences/start?placeId=1'


def test_windows_process_query_uses_native_unicode_path_and_command_line(
    monkeypatch, tmp_path
):
    module = _load_platform_windows(monkeypatch)
    exe = _touch(
        tmp_path / 'Michael Březina' / 'Roblox' / 'RobloxPlayerBeta.exe',
        3000,
    )
    command_line = f'"{exe}" roblox-player:1+launchmode:play'

    monkeypatch.setattr(module, '_find_pids', lambda _name: [123])
    monkeypatch.setattr(module, '_query_exe_path', lambda _pid: exe)
    monkeypatch.setattr(module, '_query_process_command_line', lambda _pid: command_line)
    monkeypatch.setattr(
        module.subprocess,
        'run',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('native process discovery must not start PowerShell')
        ),
    )

    assert module._query_roblox_processes('RobloxPlayerBeta.exe') == [
        {
            'ProcessId': 123,
            'ExecutablePath': str(exe),
            'CommandLine': command_line,
        }
    ]


def test_windows_native_command_line_query_preserves_unicode(monkeypatch):
    module = _load_platform_windows(monkeypatch)
    command_line = (
        '"C:\\Users\\Michael Březina\\RobloxPlayerBeta.exe" '
        'roblox-player:1+launchmode:play'
    )
    encoded = command_line.encode('utf-16-le')

    class _NativeQuery:
        argtypes = None
        restype = None

        def __call__(self, _handle, info_class, buffer, _size, needed):
            assert info_class == module._PROCESS_COMMAND_LINE_INFORMATION
            total_size = module.ctypes.sizeof(module._UNICODE_STRING) + len(encoded) + 2
            module.ctypes.cast(
                needed,
                module.ctypes.POINTER(module.ctypes.wintypes.ULONG),
            ).contents.value = total_size
            if buffer is None:
                return module.ctypes.c_long(module._STATUS_INFO_LENGTH_MISMATCH).value

            string_address = module.ctypes.addressof(buffer) + module.ctypes.sizeof(
                module._UNICODE_STRING
            )
            module.ctypes.memmove(string_address, encoded, len(encoded))
            info = module._UNICODE_STRING.from_buffer(buffer)
            info.Length = len(encoded)
            info.MaximumLength = len(encoded) + 2
            info.Buffer = string_address
            return 0

    closed = []
    monkeypatch.setattr(
        module.ctypes,
        'windll',
        SimpleNamespace(
            kernel32=SimpleNamespace(
                OpenProcess=lambda *_args: 99,
                CloseHandle=lambda handle: closed.append(handle),
            ),
            ntdll=SimpleNamespace(NtQueryInformationProcess=_NativeQuery()),
        ),
        raising=False,
    )

    assert module._query_process_command_line(123) == command_line
    assert closed == [99]


def test_gdk_arming_reports_missing_installation_precisely(monkeypatch):
    module = _load_platform_windows(monkeypatch)
    messages = []
    monkeypatch.setattr(module.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(
        module.subprocess,
        'run',
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=''),
    )
    monkeypatch.setattr(
        module,
        'log_buffer',
        SimpleNamespace(log=lambda category, message: messages.append((category, message))),
    )

    assert not module.arm_roblox_gdk_env_proxy('http://127.0.0.1:58443')
    assert messages == [('Launcher', 'No GDK Roblox installation found')]


def test_windows_identifies_the_store_gdk_player_path(monkeypatch):
    module = _load_platform_windows(monkeypatch)

    assert module.is_roblox_gdk_exe_path(
        r'C:\Program Files\WindowsApps\ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr\RobloxPlayerBeta.exe'
    )
    assert not module.is_roblox_gdk_exe_path(
        r'C:\Users\Sviat\AppData\Local\Roblox\Versions\version-current\RobloxPlayerBeta.exe'
    )
    assert module.is_roblox_gdk_exe_path(
        r'C:\XboxGames\Roblox\Content\RobloxPlayerBeta.exe'
    )


def test_windows_reads_store_package_full_name_and_aumid(monkeypatch, tmp_path):
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

    assert module._get_roblox_gdk_package_identity(exe) == (
        'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr',
        'ROBLOXCorporation.RobloxGDK_55nm5eh3cm0pr!Game',
    )


def test_gdk_repair_activation_falls_back_to_registered_package(monkeypatch):
    module = _load_platform_windows(monkeypatch)
    exe = Path(r'C:\XboxGames\Roblox\Content\RobloxPlayerBeta.exe')
    package = (
        'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr',
        'ROBLOXCorporation.RobloxGDK_55nm5eh3cm0pr!Game',
    )
    calls = []
    monkeypatch.setattr(module, '_get_roblox_gdk_package_identity', lambda _path: None)
    monkeypatch.setattr(
        module,
        '_find_installed_roblox_gdk_package_identity',
        lambda: calls.append('lookup') or package,
    )

    result = module._activate_roblox_gdk_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        pid=100,
        exe_path=exe,
        launch_arg='',
        query_processes=lambda: [],
        prepare_launch=None,
        cancel_event=SimpleNamespace(is_set=lambda: True),
    )
    assert result is None
    assert calls == ['lookup']


def test_forced_gdk_relaunch_receives_ca_preparation_callback(monkeypatch, tmp_path):
    module = _load_platform_windows(monkeypatch)
    exe = _touch(
        tmp_path
        / 'Program Files'
        / 'WindowsApps'
        / 'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr'
        / 'RobloxPlayerBeta.exe',
        3000,
    )
    prepare = lambda _path: True
    calls = []

    def activate(*_args, **kwargs):
        calls.append(kwargs)
        return 200, str(exe)

    monkeypatch.setattr(module, '_activate_roblox_gdk_with_proxy_env', activate)

    assert module._relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=lambda: [
            {'ProcessId': 100, 'ExecutablePath': str(exe), 'CommandLine': ''}
        ],
        extract_launch_arg=lambda _cmd: '',
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=lambda: exe,
        force=True,
        prepare_launch=prepare,
    )
    assert calls and calls[0]['prepare_launch'] is prepare


def test_windows_proxy_environment_block_is_double_nul_terminated(monkeypatch):
    module = _load_platform_windows(monkeypatch)

    block = module._package_environment_block({'HTTP_PROXY': 'http://127.0.0.1:1'})

    assert block.value == 'HTTP_PROXY=http://127.0.0.1:1'
    assert block[block._length_ - 1] == '\x00'
    assert block[block._length_ - 2] == '\x00'


def test_force_close_kills_immediately_before_waiting_for_process_exit(monkeypatch):
    module = _load_platform_windows(monkeypatch)
    events = []

    monkeypatch.setattr(
        module,
        "run_cmd",
        lambda args: events.append(tuple(args)) or (0, ""),
    )
    monkeypatch.setattr(
        module,
        "_wait_for_pid_exit",
        lambda *_args, **_kwargs: events.append("pid_exit") or True,
    )

    assert module._force_close_process_immediately(
        100,
        "RobloxPlayerBeta.exe",
        label="Roblox",
    )
    assert events == [
        ("taskkill", "/F", "/PID", "100"),
        "pid_exit",
    ]


def test_terminate_roblox_logs_taskkill_result_for_every_process(monkeypatch):
    module = _load_platform_windows(monkeypatch)
    commands = []
    logs = []
    process_snapshots = iter(([100, 200], []))

    monkeypatch.setattr(module, "_find_pids", lambda _name: next(process_snapshots))
    monkeypatch.setattr(module, "_pid_is_running", lambda _pid, _name: True)
    monkeypatch.setattr(module, "_is_process_elevated", lambda: False)
    monkeypatch.setattr(module, "_describe_pids", lambda pids: ",".join(map(str, pids)))
    monkeypatch.setattr(module, "_wait_for_pid_exit", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        module,
        "run_cmd",
        lambda args: commands.append(tuple(args)) or (0, f"SUCCESS: {args[-1]}"),
    )
    monkeypatch.setattr(
        module.log_buffer,
        "log",
        lambda category, message: logs.append((category, message)),
    )

    assert module.terminate_roblox()
    assert commands == [
        ("taskkill", "/F", "/PID", "100"),
        ("taskkill", "/F", "/PID", "200"),
    ]
    assert any("PID 100 returned 0" in message for _, message in logs)
    assert any("PID 200 returned 0" in message for _, message in logs)


def test_terminate_roblox_reports_taskkill_timeout(monkeypatch):
    module = _load_platform_windows(monkeypatch)
    logs = []

    monkeypatch.setattr(module, "_find_pids", lambda _name: [100])
    monkeypatch.setattr(module, "_pid_is_running", lambda _pid, _name: True)
    monkeypatch.setattr(module, "_is_process_elevated", lambda: True)
    monkeypatch.setattr(module, "_describe_pids", lambda pids: ",".join(map(str, pids)))
    monkeypatch.setattr(
        module.log_buffer,
        "log",
        lambda category, message: logs.append((category, message)),
    )

    def timeout(_args):
        raise subprocess.TimeoutExpired(cmd="taskkill", timeout=10)

    monkeypatch.setattr(module, "run_cmd", timeout)

    assert not module.terminate_roblox()
    assert any("timed out after 10 seconds" in message for _, message in logs)


def test_terminate_roblox_logs_taskkill_failure_output(monkeypatch):
    module = _load_platform_windows(monkeypatch)
    logs = []
    process_snapshots = iter(([100], []))

    monkeypatch.setattr(module, "_find_pids", lambda _name: next(process_snapshots))
    monkeypatch.setattr(module, "_pid_is_running", lambda _pid, _name: True)
    monkeypatch.setattr(module, "_is_process_elevated", lambda: False)
    monkeypatch.setattr(module, "_describe_pids", lambda pids: ",".join(map(str, pids)))
    monkeypatch.setattr(module, "_wait_for_pid_exit", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "run_cmd", lambda _args: (5, "ERROR: Access is denied."))
    monkeypatch.setattr(
        module.log_buffer,
        "log",
        lambda category, message: logs.append((category, message)),
    )

    assert not module.terminate_roblox()
    assert any("returned 5" in message and "Access is denied" in message for _, message in logs)


def test_env_proxy_relaunch_leaves_store_gdk_player_untouched(monkeypatch, tmp_path):
    module = _load_platform_windows(monkeypatch)
    exe = _touch(
        tmp_path
        / 'Program Files'
        / 'WindowsApps'
        / 'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr'
        / 'RobloxPlayerBeta.exe',
        3000,
    )
    commands = []

    monkeypatch.setattr(module, 'run_cmd', lambda args: commands.append(args) or '')
    query = lambda: [
        {'ProcessId': 100, 'ExecutablePath': str(exe), 'CommandLine': ''}
    ]

    assert not module._relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=query,
        extract_launch_arg=lambda _cmd: '',
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=lambda: exe,
    )
    assert commands == []


def test_env_proxy_relaunch_adopts_armed_store_gdk_player(monkeypatch, tmp_path):
    module = _load_platform_windows(monkeypatch)
    exe = _touch(
        tmp_path
        / 'Program Files'
        / 'WindowsApps'
        / 'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr'
        / 'RobloxPlayerBeta.exe',
        3000,
    )
    commands = []

    monkeypatch.setattr(module, '_gdk_env_proxy_armed_package', ('package', 'aumid'))
    monkeypatch.setattr(module, 'run_cmd', lambda args: commands.append(args) or '')

    assert module._relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=lambda: [
            {'ProcessId': 100, 'ExecutablePath': str(exe), 'CommandLine': ''}
        ],
        extract_launch_arg=lambda _cmd: '',
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=lambda: exe,
    )
    assert commands == []
    assert module._env_proxy_owned_process == (100, str(exe))


def test_env_proxy_lifecycle_closes_owned_store_gdk_player(monkeypatch, tmp_path):
    module = _load_platform_windows(monkeypatch)
    exe = _touch(
        tmp_path
        / 'Program Files'
        / 'WindowsApps'
        / 'ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr'
        / 'RobloxPlayerBeta.exe',
        3000,
    )
    module._env_proxy_owned_process = (100, str(exe))
    events = []

    monkeypatch.setattr(module, '_query_exe_path', lambda _pid: exe)
    monkeypatch.setattr(
        module,
        '_request_process_window_close',
        lambda _pid: events.append('window_close') or True,
    )
    monkeypatch.setattr(
        module,
        '_wait_for_pid_exit',
        lambda *_args, **_kwargs: events.append('pid_exit') or True,
    )

    assert module.close_roblox_for_env_lifecycle()
    assert events == ['window_close', 'pid_exit']
    assert module._env_proxy_owned_process is None


def test_env_proxy_relaunch_skips_gdk_even_when_helper_exists(monkeypatch, tmp_path):
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
    commands = []

    monkeypatch.setattr(module, 'run_cmd', lambda args: commands.append(args) or '')

    assert not module._relaunch_roblox_exe_with_proxy_env(
        'http://127.0.0.1:58443',
        label='Roblox',
        query_processes=lambda: [
            {'ProcessId': 100, 'ExecutablePath': str(exe), 'CommandLine': ''}
        ],
        extract_launch_arg=lambda _cmd: '',
        wait_pid_exe_name='RobloxPlayerBeta.exe',
        fallback_exe_path=lambda: exe,
        prepare_launch=lambda path: path == exe,
    )
    assert commands == []


def test_env_proxy_relaunch_skips_the_proxy_owned_process(monkeypatch, tmp_path):
    module = _load_platform_windows(monkeypatch)
    _skip_immediate_close_for_relaunch_test(monkeypatch, module)
    exe = _touch(tmp_path / "Content" / "RobloxPlayerBeta.exe", 3000)
    running_pids = {100}
    current = {"pid": 100}
    popen_pids = iter((200,))

    monkeypatch.setattr(
        module,
        "_iter_processes",
        lambda: iter([(pid, "robloxplayerbeta.exe") for pid in running_pids]),
    )
    monkeypatch.setattr(module, "_find_pid", lambda _name: None)
    monkeypatch.setattr(module, "run_cmd", lambda _args: "")
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=next(popen_pids)),
    )

    query = lambda: [
        {"ProcessId": current["pid"], "ExecutablePath": str(exe), "CommandLine": ""}
    ]
    assert module._relaunch_roblox_exe_with_proxy_env(
        "http://127.0.0.1:58443",
        label="Roblox",
        query_processes=query,
        extract_launch_arg=lambda _cmd: "",
        wait_pid_exe_name="RobloxPlayerBeta.exe",
        fallback_exe_path=lambda: exe,
    )

    running_pids.add(200)
    current["pid"] = 200
    assert not module._relaunch_roblox_exe_with_proxy_env(
        "http://127.0.0.1:58443",
        label="Roblox",
        query_processes=query,
        extract_launch_arg=lambda _cmd: "",
        wait_pid_exe_name="RobloxPlayerBeta.exe",
        fallback_exe_path=lambda: exe,
    )


def test_env_proxy_relaunch_allows_new_process_after_crash(monkeypatch, tmp_path):
    module = _load_platform_windows(monkeypatch)
    _skip_immediate_close_for_relaunch_test(monkeypatch, module)
    exe = _touch(tmp_path / "Content" / "RobloxPlayerBeta.exe", 3000)
    running_pids = {100}
    current = {"pid": 100}
    popen_pids = iter((200, 300))

    monkeypatch.setattr(
        module,
        "_iter_processes",
        lambda: iter([(pid, "robloxplayerbeta.exe") for pid in running_pids]),
    )
    monkeypatch.setattr(module, "_find_pid", lambda _name: None)
    monkeypatch.setattr(module, "run_cmd", lambda _args: "")

    def fake_popen(*_args, **_kwargs):
        pid = next(popen_pids)
        running_pids.add(pid)
        return SimpleNamespace(pid=pid)

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    query = lambda: [
        {"ProcessId": current["pid"], "ExecutablePath": str(exe), "CommandLine": ""}
    ]
    kwargs = {
        "label": "Roblox",
        "query_processes": query,
        "extract_launch_arg": lambda _cmd: "",
        "wait_pid_exe_name": "RobloxPlayerBeta.exe",
        "fallback_exe_path": lambda: exe,
    }

    assert module._relaunch_roblox_exe_with_proxy_env(
        "http://127.0.0.1:58443", **kwargs
    )
    running_pids.remove(200)
    current["pid"] = 100
    assert module._relaunch_roblox_exe_with_proxy_env(
        "http://127.0.0.1:58443", **kwargs
    )


def test_env_proxy_relaunch_rechecks_a_replacement_player_pid(monkeypatch, tmp_path):
    module = _load_platform_windows(monkeypatch)
    exe = _touch(tmp_path / "Content" / "RobloxPlayerBeta.exe", 3000)
    snapshots = iter(
        (
            [
                {
                    "ProcessId": 100,
                    "ExecutablePath": str(exe),
                    "CommandLine": "RobloxPlayerBeta.exe roblox-player:stale-uri",
                }
            ],
            [
                {
                    "ProcessId": 200,
                    "ExecutablePath": str(exe),
                    "CommandLine": "RobloxPlayerBeta.exe roblox-player:successor-uri",
                }
            ],
        )
    )
    close_calls = []
    popen_calls = []
    logs = []

    monkeypatch.setattr(module, "_pid_is_running", lambda _pid, _name: False)
    monkeypatch.setattr(
        module,
        "_force_close_process_immediately",
        lambda pid, *_args, **_kwargs: close_calls.append(pid) or pid == 200,
    )
    monkeypatch.setattr(module, "_proxy_environment", lambda _url: {})
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda args, **_kwargs: popen_calls.append(args) or SimpleNamespace(pid=300),
    )
    monkeypatch.setattr(
        module.log_buffer,
        "log",
        lambda category, message: logs.append((category, message)),
    )

    assert module._relaunch_roblox_exe_with_proxy_env(
        "http://127.0.0.1:58443",
        label="Roblox",
        query_processes=lambda: next(snapshots),
        extract_launch_arg=lambda command: command.split()[-1],
        wait_pid_exe_name="RobloxPlayerBeta.exe",
        fallback_exe_path=lambda: exe,
    )
    assert close_calls == [100, 200]
    assert popen_calls == [[str(exe), "roblox-player:successor-uri"]]
    assert any("rechecking successor PID(s): 200" in message for _, message in logs)


def test_env_proxy_relaunch_does_not_replay_uri_when_no_successor_is_found(monkeypatch, tmp_path):
    module = _load_platform_windows(monkeypatch)
    exe = _touch(tmp_path / "Content" / "RobloxPlayerBeta.exe", 3000)
    snapshots = iter(
        (
            [
                {
                    "ProcessId": 100,
                    "ExecutablePath": str(exe),
                    "CommandLine": "RobloxPlayerBeta.exe roblox-player:one-time-uri",
                }
            ],
            [],
        )
    )
    popen_calls = []
    logs = []

    monkeypatch.setattr(module, "_pid_is_running", lambda _pid, _name: False)
    monkeypatch.setattr(module, "_force_close_process_immediately", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda args, **_kwargs: popen_calls.append(args) or SimpleNamespace(pid=300),
    )
    monkeypatch.setattr(
        module.log_buffer,
        "log",
        lambda category, message: logs.append((category, message)),
    )

    assert not module._relaunch_roblox_exe_with_proxy_env(
        "http://127.0.0.1:58443",
        label="Roblox",
        query_processes=lambda: next(snapshots),
        extract_launch_arg=lambda command: command.split()[-1],
        wait_pid_exe_name="RobloxPlayerBeta.exe",
        fallback_exe_path=lambda: exe,
    )
    assert popen_calls == []
    assert any("not replaying a potentially consumed launch URI" in message for _, message in logs)


def test_roblox_launch_resolver_upgrades_registry_path_when_versions_scan_finds_it(tmp_path, monkeypatch):
    local_appdata = tmp_path / "LocalAppData"
    versions = local_appdata / "Roblox" / "Versions"
    current = _touch(versions / "version-current" / "RobloxPlayerBeta.exe", 3000)
    stale = _touch(versions / "version-stale" / "RobloxPlayerBeta.exe", 2000)
    registry_command = f'"{current}" %1'

    module = _load_platform_windows(monkeypatch, registry_command=registry_command)
    monkeypatch.setattr(module, "get_roblox_player_exe_path", lambda: None)
    monkeypatch.setattr(
        module.os.path,
        "expandvars",
        lambda value: str(local_appdata) if value == r"%LocalAppData%" else value,
    )

    assert module._safe_mtime(current) > module._safe_mtime(stale)
    assert module.resolve_roblox_player_exe_for_launch() == current


def test_roblox_launch_resolver_prefers_current_install_over_stale_running_player(tmp_path, monkeypatch):
    local_appdata = tmp_path / "LocalAppData"
    versions = local_appdata / "Roblox" / "Versions"
    current = _touch(versions / "version-current" / "RobloxPlayerBeta.exe", 3000)
    stale = _touch(versions / "version-stale" / "RobloxPlayerBeta.exe", 2000)
    registry_command = f'"{current}" %1'

    module = _load_platform_windows(monkeypatch, registry_command=registry_command)
    monkeypatch.setattr(module, "get_roblox_player_exe_path", lambda: stale)
    monkeypatch.setattr(
        module.os.path,
        "expandvars",
        lambda value: str(local_appdata) if value == r"%LocalAppData%" else value,
    )

    assert module.resolve_roblox_player_exe_for_launch() == current


def test_roblox_launch_resolver_rejects_registry_installer_target(tmp_path, monkeypatch):
    installer = _touch(
        tmp_path / "LocalAppData" / "Roblox" / "Versions" / "version-current" / "RobloxPlayerInstaller.exe",
        3000,
    )
    registry_command = f'"{installer}" -app -force'

    module = _load_platform_windows(monkeypatch, registry_command=registry_command)
    monkeypatch.setattr(module, "get_roblox_player_exe_path", lambda: None)
    monkeypatch.setattr(module, "_scan_for_player_exes", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module.os.path,
        "expandvars",
        lambda value: str(tmp_path / "LocalAppData") if value == r"%LocalAppData%" else value,
    )

    assert module.resolve_roblox_player_exe_for_launch() is None


def test_roblox_launch_resolver_rejects_running_installer_target(tmp_path, monkeypatch):
    installer = _touch(
        tmp_path / "LocalAppData" / "Roblox" / "Versions" / "version-current" / "RobloxPlayerInstaller.exe",
        3000,
    )

    module = _load_platform_windows(monkeypatch, registry_command=None)
    monkeypatch.setattr(module, "get_roblox_player_exe_path", lambda: installer)
    monkeypatch.setattr(module, "_scan_for_player_exes", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module.os.path,
        "expandvars",
        lambda value: str(tmp_path / "LocalAppData") if value == r"%LocalAppData%" else value,
    )

    assert module.resolve_roblox_player_exe_for_launch() is None


def test_env_proxy_has_no_automatic_firewall_rule_installer(tmp_path, monkeypatch):
    module = _load_platform_windows(monkeypatch)
    assert not hasattr(module, "install_fleasion_firewall_rules")
