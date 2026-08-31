"""Desktop/start-menu integration for Fleasion.

The integration intentionally points at the currently running build and is
resynced on launch when enabled. This keeps shortcuts useful while users move
between packaged builds and ``uv run`` development sessions.
"""

from __future__ import annotations

import importlib
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.version import macos_bundle_version

from .metadata import APP_NAME, APP_VERSION
from .paths import USER_HOME, get_icon_path

WINDOWS_START_MENU_SHORTCUT_PATH = (
    Path(os.environ.get('APPDATA', USER_HOME / 'AppData' / 'Roaming'))
    / 'Microsoft'
    / 'Windows'
    / 'Start Menu'
    / 'Programs'
    / f'{APP_NAME}.lnk'
)
MACOS_APPLICATION_PATH = USER_HOME / 'Applications' / f'{APP_NAME}.app'
_MACOS_MARKER_NAME = '.fleasion-managed-launcher'
_DESCRIPTION = 'Roblox asset interceptor and replacer'


def _log(message: str) -> None:
    try:
        logging_module = importlib.import_module('.logging', __package__)
        logging_module.log_buffer.log('DesktopIntegration', message)
    except (ImportError, AttributeError, OSError):
        return


def _find_project_root() -> Path | None:
    check = Path(__file__).resolve().parent
    for _ in range(8):
        if (check / 'pyproject.toml').is_file() and (check / 'launcher.py').is_file():
            return check
        if check.parent == check:
            break
        check = check.parent
    return None


def _launch_command() -> tuple[list[str], Path | None, dict[str, str]]:
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable)
        return [str(executable)], executable.parent, {}

    project = _find_project_root()
    uv = shutil.which('uv') or shutil.which('uv.exe')
    if project is not None and uv:
        return (
            [str(Path(uv).resolve()), '--project', str(project), 'run', 'fleasion'],
            project,
            {},
        )

    if project is not None:
        return (
            [sys.executable, str(project / 'launcher.py')],
            project,
            {'PYTHONPATH': str(project / 'src')},
        )

    return [sys.executable, '-c', 'from fleasion import main; main()'], None, {}


def _remove_windows_shortcut() -> bool:
    try:
        WINDOWS_START_MENU_SHORTCUT_PATH.unlink(missing_ok=True)
    except OSError as exc:
        _log(f'Failed to remove Windows start-menu shortcut: {exc}')
        return False
    return True


def _write_windows_shortcut(
    pythoncom: Any,
    win32com_client: Any,
    command: list[str],
    working_dir: Path | None,
    icon_path: Path | None,
) -> None:
    pythoncom.CoInitialize()
    shell = win32com_client.Dispatch('WScript.Shell')
    WINDOWS_START_MENU_SHORTCUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    shortcut = shell.CreateShortCut(str(WINDOWS_START_MENU_SHORTCUT_PATH))
    shortcut.Targetpath = command[0]
    shortcut.Arguments = subprocess.list2cmdline(command[1:])
    shortcut.WorkingDirectory = str(working_dir or Path(command[0]).parent)
    shortcut.Description = _DESCRIPTION
    if icon_path is not None:
        shortcut.IconLocation = f'{icon_path},0'
    shortcut.Save()


if TYPE_CHECKING:
    from typing import Any

    def _create_windows_shortcut_runtime(
        command: list[str], working_dir: Path | None, icon_path: Path | None
    ) -> bool: ...
else:

    def _create_windows_shortcut_runtime(
        command: list[str], working_dir: Path | None, icon_path: Path | None
    ) -> bool:
        try:
            pythoncom = importlib.import_module('pythoncom')
            win32com_client = importlib.import_module('win32com.client')
        except ImportError as exc:
            _log(f'Failed to load Windows shortcut support: {exc}')
            return False

        try:
            _write_windows_shortcut(pythoncom, win32com_client, command, working_dir, icon_path)
        except (AttributeError, OSError, pythoncom.com_error) as exc:
            _log(f'Failed to create Windows start-menu shortcut: {exc}')
            return False
        _log(f'Windows start-menu shortcut updated: {WINDOWS_START_MENU_SHORTCUT_PATH}')
        return True


def _create_windows_shortcut() -> bool:
    command, working_dir, _env = _launch_command()
    return _create_windows_shortcut_runtime(command, working_dir, get_icon_path())


def _macos_launcher_script(
    command: list[str], working_dir: Path | None, env: dict[str, str]
) -> str:
    lines = ['#!/bin/sh', 'set -eu']
    if working_dir is not None:
        lines.append(f'cd {shlex.quote(str(working_dir))}')
    for name, value in env.items():
        lines.append(f'export {name}={shlex.quote(value)}' + '${' + name + ':+:$' + name + '}')
    lines.append('exec ' + ' '.join(shlex.quote(part) for part in command) + ' "$@"')
    return '\n'.join(lines) + '\n'


def _register_macos_application(app_path: Path) -> None:
    """Register a generated app bundle with Launch Services."""
    lsregister = Path(
        '/System/Library/Frameworks/CoreServices.framework/Frameworks/'
        'LaunchServices.framework/Support/lsregister'
    )
    if not lsregister.is_file():
        return
    try:
        subprocess.run(
            [str(lsregister), '-f', str(app_path)],
            capture_output=True,
            timeout=10,
            check=False,
            shell=False,
        )
    except OSError as exc:
        _log(f'Failed to register macOS launcher app with Launch Services: {exc}')


def _remove_macos_app() -> bool:
    if not MACOS_APPLICATION_PATH.exists():
        return True
    marker = MACOS_APPLICATION_PATH / 'Contents' / _MACOS_MARKER_NAME
    if not marker.exists():
        _log(f'Refusing to remove unmarked macOS app: {MACOS_APPLICATION_PATH}')
        return True
    try:
        shutil.rmtree(MACOS_APPLICATION_PATH)
    except OSError as exc:
        _log(f'Failed to remove macOS launcher app: {exc}')
        return False
    return True


def _write_macos_app_bundle(
    command: list[str], working_dir: Path | None, env: dict[str, str], contents: Path
) -> None:
    macos_dir = contents / 'MacOS'
    resources = contents / 'Resources'
    marker = contents / _MACOS_MARKER_NAME
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    executable = macos_dir / APP_NAME
    executable.write_text(_macos_launcher_script(command, working_dir, env), encoding='utf-8')
    executable.chmod(0o755)

    icon_name = None
    icon_path = get_icon_path()
    if icon_path is not None and icon_path.suffix.lower() == '.icns':
        icon_name = icon_path.stem
        shutil.copy2(icon_path, resources / icon_path.name)

    bundle_version = macos_bundle_version(APP_VERSION)
    info = {
        'CFBundleDevelopmentRegion': 'en',
        'CFBundleDisplayName': APP_NAME,
        'CFBundleExecutable': APP_NAME,
        'CFBundleIdentifier': 'com.fleasion.launcher',
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleName': APP_NAME,
        'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': bundle_version,
        'CFBundleVersion': bundle_version,
        'LSApplicationCategoryType': 'public.app-category.utilities',
        'NSHumanReadableCopyright': _DESCRIPTION,
    }
    if icon_name:
        info['CFBundleIconFile'] = icon_name
    with (contents / 'Info.plist').open('wb') as plist_file:
        plistlib.dump(info, plist_file)
    _register_macos_application(MACOS_APPLICATION_PATH)
    marker.write_text('Managed by Fleasion desktop integration.\n', encoding='utf-8')


def _create_macos_app() -> bool:
    # Keep Roblox's own LaunchServices association. Fleasion observes the
    # resulting Player launch, matching the Windows URI flow; claiming
    # roblox:// here would launch a second Fleasion instance from the browser.
    command, working_dir, env = _launch_command()
    contents = MACOS_APPLICATION_PATH / 'Contents'
    marker = contents / _MACOS_MARKER_NAME

    if MACOS_APPLICATION_PATH.exists() and not marker.exists():
        if getattr(sys, 'frozen', False):
            try:
                if Path(sys.executable).resolve().is_relative_to(MACOS_APPLICATION_PATH.resolve()):
                    _log(f'macOS app is already installed: {MACOS_APPLICATION_PATH}')
                    return True
            except OSError:
                pass
        _log(f'Refusing to overwrite unmarked macOS app: {MACOS_APPLICATION_PATH}')
        return False

    try:
        _write_macos_app_bundle(command, working_dir, env, contents)
    except (OSError, ValueError) as exc:
        _log(f'Failed to create macOS launcher app: {exc}')
        return False
    _log(f'macOS launcher app updated: {MACOS_APPLICATION_PATH}')
    return True


if TYPE_CHECKING:

    def _restore_linux_uri_handler_runtime() -> None: ...
else:

    def _restore_linux_uri_handler_runtime() -> None:
        platform_linux = importlib.import_module('.platform_linux', __package__)
        platform_linux.restore_linux_roblox_uri_handler()


def _remove_linux_desktop_entries() -> bool:
    try:
        platform_linux = importlib.import_module('.platform_linux', __package__)
        for path in (
            platform_linux.LINUX_DESKTOP_ENTRY_PATH,
            platform_linux.LINUX_LAUNCHER_PATH,
        ):
            path.unlink(missing_ok=True)
        if (
            platform_linux.LINUX_DESKTOP_ENTRY_PATH.parent
            == platform_linux.LINUX_APPLICATIONS_DIR
        ):
            _restore_linux_uri_handler_runtime()
    except (ImportError, AttributeError, OSError) as exc:
        _log(f'Failed to remove Linux desktop integration: {exc}')
        return False
    return True


def _create_linux_desktop_entries() -> bool:
    try:
        platform_linux = importlib.import_module('.platform_linux', __package__)
        platform_linux.install_desktop_entries()
    except (ImportError, AttributeError, OSError) as exc:
        _log(f'Failed to create Linux desktop integration: {exc}')
        return False
    return True


def sync_desktop_integration(enabled: bool) -> bool:
    """Ensure desktop/start-menu integration matches the desired state."""
    if sys.platform == 'win32':
        return _create_windows_shortcut() if enabled else _remove_windows_shortcut()
    if sys.platform == 'darwin':
        return _create_macos_app() if enabled else _remove_macos_app()
    if sys.platform.startswith('linux'):
        return _create_linux_desktop_entries() if enabled else _remove_linux_desktop_entries()
    return True
