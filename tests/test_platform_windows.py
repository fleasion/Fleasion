import ctypes
import importlib.util
import os
import sys
import types
import subprocess
from pathlib import Path


def _load_platform_windows(monkeypatch, registry_command: str | None = None):
    source = Path(__file__).resolve().parents[1] / "src" / "fleasion" / "utils" / "platform_windows.py"

    paths = types.ModuleType("fleasion.utils.paths")
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


def test_firewall_repair_installs_scoped_inbound_and_outbound_rules(tmp_path, monkeypatch):
    module = _load_platform_windows(monkeypatch)
    executable = _touch(tmp_path / "Fleasion.exe", 3000)
    calls = []

    def _run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="Ok.", stderr="")

    monkeypatch.setattr(module.subprocess, "run", _run)

    installed, target = module.install_fleasion_firewall_rules(executable)

    assert installed is True
    assert target == str(executable.resolve())
    add_commands = [command for command, _kwargs in calls if "add" in command]
    assert len(add_commands) == 2
    assert any("dir=in" in command and "localport=443" in command for command in add_commands)
    assert any("dir=out" in command and "remoteport=443" in command for command in add_commands)
    assert all("profile=private,public" in command for command in add_commands)
    assert all(f"program={executable.resolve()}" in command for command in add_commands)
