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


def test_windows_relaunch_extractor_preserves_both_roblox_uri_forms(monkeypatch):
    module = _load_platform_windows(monkeypatch)

    assert module._extract_roblox_deeplink(
        'RobloxPlayerBeta.exe roblox-player:1+launchmode:play'
    ) == 'roblox-player:1+launchmode:play'
    assert module._extract_roblox_deeplink(
        'RobloxPlayerBeta.exe roblox://experiences/start?placeId=1'
    ) == 'roblox://experiences/start?placeId=1'


def test_windows_identifies_the_store_gdk_player_path(monkeypatch):
    module = _load_platform_windows(monkeypatch)

    assert module.is_roblox_gdk_exe_path(
        r'C:\Program Files\WindowsApps\ROBLOXCorporation.RobloxGDK_2.733.988.0_x64__55nm5eh3cm0pr\RobloxPlayerBeta.exe'
    )
    assert not module.is_roblox_gdk_exe_path(
        r'C:\Users\Sviat\AppData\Local\Roblox\Versions\version-current\RobloxPlayerBeta.exe'
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


def test_windows_proxy_environment_block_is_double_nul_terminated(monkeypatch):
    module = _load_platform_windows(monkeypatch)

    block = module._package_environment_block({'HTTP_PROXY': 'http://127.0.0.1:1'})

    assert block.value == 'HTTP_PROXY=http://127.0.0.1:1'
    assert block[block._length_ - 1] == '\x00'
    assert block[block._length_ - 2] == '\x00'


def test_gdk_initial_launch_settle_honors_cancellation(monkeypatch):
    module = _load_platform_windows(monkeypatch)
    cancelled = SimpleNamespace(wait=lambda _timeout: True)

    assert not module._wait_for_gdk_initial_launch_settle(cancelled)


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
