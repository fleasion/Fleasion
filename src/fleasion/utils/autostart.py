"""Autostart integration for Fleasion.

Creates a native Windows per-user Run entry (or a Task Scheduler task for
development launches), a macOS LaunchAgent, or an XDG autostart desktop entry
on Linux. Detects whether we're running as a compiled executable or from a
development checkout and updates the launch method when it changes.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import plistlib
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, TypedDict

from fleasion.localization import tr

from .paths import USER_HOME

logger = logging.getLogger(__name__)


class LaunchInfo(TypedDict):
    mode: str
    path: str
    _fmt: int
    project: NotRequired[str]
    log: NotRequired[str]
    proxy_mode: NotRequired[str]


if TYPE_CHECKING:

    def _creation_flags() -> int: ...

    def _json_launch_info(value: object) -> LaunchInfo | None: ...

    def _required_project(info: LaunchInfo) -> str: ...

    def _query_windows_run_value() -> tuple[object, int] | None: ...

    def _write_windows_run_value(command: str) -> None: ...

    def _delete_windows_run_value() -> None: ...
else:

    def _creation_flags() -> int:
        return getattr(subprocess, 'CREATE_NO_WINDOW', 0)

    def _json_launch_info(value: object) -> LaunchInfo | None:
        return value if isinstance(value, dict) else None

    def _required_project(info: LaunchInfo) -> str:  # ruff: ignore[reimplemented-operator]
        return info['project']

    def _query_windows_run_value() -> tuple[object, int] | None:
        import winreg  # ruff: ignore[import-outside-top-level]

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _WINDOWS_RUN_KEY,
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            return winreg.QueryValueEx(key, _WINDOWS_RUN_VALUE)

    def _write_windows_run_value(command: str) -> None:
        import winreg  # ruff: ignore[import-outside-top-level]

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            _WINDOWS_RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, _WINDOWS_RUN_VALUE, 0, winreg.REG_SZ, command)

    def _delete_windows_run_value() -> None:
        import winreg  # ruff: ignore[import-outside-top-level]

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _WINDOWS_RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, _WINDOWS_RUN_VALUE)


# Use Fleasion's log_buffer when available, fall back to Python logger
def _log(msg: str) -> None:
    try:
        from fleasion.utils.logging import log_buffer  # ruff: ignore[import-outside-top-level]

        log_buffer.log('Autostart', msg)
    except Exception:  # ruff: ignore[blind-except]
        logger.info(msg)


def _command_output(
    result: subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str],
) -> str:
    """Return captured command output without hiding scheduler diagnostics."""
    parts: list[str] = []
    for output in (getattr(result, 'stdout', None), getattr(result, 'stderr', None)):
        if isinstance(output, bytes):
            output = output.decode(errors='replace')  # ruff: ignore[redefined-loop-name]
        if output:
            parts.append(str(output).strip())
    return ' '.join(parts)


TASK_NAME = 'Fleasion_Autostart'
LAUNCH_AGENT_ID = 'com.fleasion.autostart'
LAUNCH_AGENT_PATH = USER_HOME / 'Library' / 'LaunchAgents' / f'{LAUNCH_AGENT_ID}.plist'
LINUX_AUTOSTART_PATH = USER_HOME / '.config' / 'autostart' / 'fleasion.desktop'
_WINDOWS_RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
_WINDOWS_RUN_VALUE = 'Fleasion'
_WINDOWS_RUN_COMMAND_MAX = 260
_LEGACY_TASK_CLEANUP_MARKER = 'autostart_task_scheduler_migration_v9.done'
_legacy_task_cleanup_started = False


# Bump this whenever an autostart format changes to force reconciliation on next launch.
_TASK_FORMAT_VERSION = 9


def _windows_run_command(launch_info: LaunchInfo) -> str:
    """Return the command stored in the current user's Windows Run key."""
    return subprocess.list2cmdline([str(launch_info['path']), '--no-dashboard'])


def _windows_run_entry_matches(launch_info: LaunchInfo) -> bool:
    """Return whether the native per-user autostart value is current."""
    try:
        result = _query_windows_run_value()
        if result is None:
            return False
        value, value_type = result
        # REG_SZ == 1 on Windows; keeping the comparison numeric avoids importing
        # winreg into non-Windows type-checking environments.
        return value_type == 1 and value == _windows_run_command(launch_info)
    except ImportError, OSError:
        return False


def _set_windows_run_entry(launch_info: LaunchInfo) -> bool:
    """Create/update packaged Fleasion autostart without starting a subprocess."""
    try:
        _write_windows_run_value(_windows_run_command(launch_info))
        return True  # ruff: ignore[try-consider-else]
    except (ImportError, OSError) as exc:
        _log(f'Failed to update native Windows autostart: {exc}')
        return False


def _delete_windows_run_entry() -> bool:
    """Remove packaged Fleasion autostart from the current user's Run key."""
    try:
        _delete_windows_run_value()
        return True  # ruff: ignore[try-consider-else]
    except FileNotFoundError:
        return True
    except (ImportError, OSError) as exc:
        _log(f'Failed to remove native Windows autostart: {exc}')
        return False


def _delete_legacy_windows_task_async(config_dir: Path) -> None:
    """Remove the old task in a retryable background migration."""
    global _legacy_task_cleanup_started  # ruff: ignore[global-statement]

    marker = config_dir / _LEGACY_TASK_CLEANUP_MARKER
    if _legacy_task_cleanup_started or marker.exists():
        return
    _legacy_task_cleanup_started = True

    def _cleanup() -> None:
        flags = _creation_flags()
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            query = subprocess.run(  # ruff: ignore[replace-stdout-stderr, subprocess-run-without-check, subprocess-without-shell-equals-true]
                ['schtasks', '/Query', '/TN', TASK_NAME],  # ruff: ignore[start-process-with-partial-path]
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=flags,
                timeout=30,
            )
            if query.returncode != 0:
                marker.write_text('legacy task absent\n', encoding='utf-8')
                return
            deleted = subprocess.run(  # ruff: ignore[replace-stdout-stderr, subprocess-run-without-check, subprocess-without-shell-equals-true]
                ['schtasks', '/Delete', '/TN', TASK_NAME, '/F'],  # ruff: ignore[start-process-with-partial-path]
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=flags,
                timeout=30,
            )
            if deleted.returncode == 0:
                marker.write_text('legacy task deleted\n', encoding='utf-8')
            else:
                _log(
                    f'Legacy scheduled-task cleanup failed (rc={deleted.returncode}): '
                    f'{_command_output(deleted)}'
                )
        except Exception as exc:  # ruff: ignore[blind-except]
            # No marker means a later Fleasion launch retries, while this slow
            # or unhealthy Task Scheduler call never delays the current launch.
            _log(f'Legacy scheduled-task cleanup deferred: {exc}')

    threading.Thread(target=_cleanup, name='FleasionAutostartMigration', daemon=True).start()


def _project_root() -> Path:
    """Return the checkout root when running from a development environment."""
    check = Path(__file__).resolve().parent
    for _ in range(8):
        if (check / 'pyproject.toml').exists():
            return check
        check = check.parent
    return Path(__file__).resolve().parent


def _windows_uv_executable() -> str:
    """Return a stable absolute uv path for Windows task registration."""
    import shutil  # ruff: ignore[import-outside-top-level]

    for name in ('uv', 'uv.exe'):
        found = shutil.which(name)
        if found:
            if os.name == 'nt' and not Path(found).is_absolute():
                found = os.path.abspath(found)  # ruff: ignore[os-path-abspath]
            return found

    user_profile = os.environ.get('USERPROFILE') or str(Path.home())
    installed_uv = Path(user_profile) / '.local' / 'bin' / 'uv.exe'
    if installed_uv.is_file():
        return str(installed_uv)
    return 'uv'


def _ps_single_quote(value: str) -> str:
    """Return *value* as a PowerShell single-quoted string literal."""
    return "'" + value.replace("'", "''") + "'"


def windows_autostart_privilege_hint(proxy_mode: str | None) -> str:
    """Describe proxy-mode elevation without conflating it with autostart."""
    if proxy_mode == 'hosts':
        return tr('autostart.windows.hosts_privilege_hint')
    return tr('autostart.windows.env_privilege_hint')


def _desktop_exec_quote(value: str) -> str:
    """Quote a single Exec token for a .desktop entry."""
    if not value:
        return '""'
    if all(ch.isalnum() or ch in '._-/:=@' for ch in value):
        return value
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _desktop_exec_join(parts: list[str]) -> str:
    """Join Exec tokens using desktop-entry quoting rules."""
    return ' '.join(_desktop_exec_quote(part) for part in parts)


def _linux_installed_launcher() -> Path | None:
    try:
        from .platform_linux import LINUX_LAUNCHER_PATH  # ruff: ignore[import-outside-top-level]
    except Exception:  # ruff: ignore[blind-except]
        return None
    try:
        if LINUX_LAUNCHER_PATH.is_file():
            return LINUX_LAUNCHER_PATH
    except OSError:
        return None
    return None


def _get_launch_info() -> LaunchInfo:
    """Return a dict describing how to launch the current instance."""
    if sys.platform.startswith('linux'):
        installed_launcher = _linux_installed_launcher()
        if installed_launcher is not None:
            return {
                'mode': 'linux-launcher',
                'path': str(installed_launcher),
                '_fmt': _TASK_FORMAT_VERSION,
            }

    if getattr(sys, 'frozen', False):
        return {
            'mode': 'exe',
            'path': str(sys.executable or ''),
            '_fmt': _TASK_FORMAT_VERSION,
        }

    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        check = Path(__file__).resolve().parent
        for _ in range(8):
            if (check / 'pyproject.toml').exists():
                break
            check = check.parent
        return {
            'mode': 'python',
            'path': str(sys.executable or ''),
            'project': str(check),
            '_fmt': _TASK_FORMAT_VERSION,
        }

    # Dev / uv run
    if sys.platform == 'win32':
        uv = _windows_uv_executable()
    else:
        import shutil  # ruff: ignore[import-outside-top-level]

        uv = shutil.which('uv') or shutil.which('uv.exe') or 'uv'
    # Find project root (dir containing pyproject.toml)
    check = _project_root()
    return {
        'mode': 'uv',
        'path': uv,
        'project': str(check),
        '_fmt': _TASK_FORMAT_VERSION,
    }


def _task_exists() -> bool:
    if sys.platform == 'darwin':
        return LAUNCH_AGENT_PATH.exists()
    if sys.platform.startswith('linux'):
        return LINUX_AUTOSTART_PATH.exists()
    try:
        r = subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
            ['schtasks', '/Query', '/TN', TASK_NAME],  # ruff: ignore[start-process-with-partial-path]
            capture_output=True,
            creationflags=_creation_flags(),
            timeout=10,
        )
        return r.returncode == 0  # ruff: ignore[try-consider-else]
    except Exception:  # ruff: ignore[blind-except]
        return False


def _delete_task() -> bool:
    if sys.platform == 'darwin':
        try:
            subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
                ['launchctl', 'unload', str(LAUNCH_AGENT_PATH)],  # ruff: ignore[start-process-with-partial-path]
                capture_output=True,
                timeout=10,
            )
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass
        with contextlib.suppress(OSError):
            LAUNCH_AGENT_PATH.unlink(missing_ok=True)
        return not LAUNCH_AGENT_PATH.exists()
    if sys.platform.startswith('linux'):
        with contextlib.suppress(OSError):
            LINUX_AUTOSTART_PATH.unlink(missing_ok=True)
        return not LINUX_AUTOSTART_PATH.exists()
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
            ['schtasks', '/Delete', '/TN', TASK_NAME, '/F'],  # ruff: ignore[start-process-with-partial-path]
            capture_output=True,
            creationflags=_creation_flags(),
            timeout=10,
        )
        if result.returncode != 0:
            _log(
                f'Failed to delete scheduled task {TASK_NAME!r} (rc={result.returncode}): '
                f'{_command_output(result)}'
            )
            return False
        if _task_exists():
            _log(f'Scheduled task {TASK_NAME!r} still exists after deletion')
            return False
        return True  # ruff: ignore[try-consider-else]
    except Exception as exc:  # ruff: ignore[blind-except]
        _log(f'Failed to delete scheduled task {TASK_NAME!r}: {exc}')
        return False


def _windows_launch_action(launch_info: LaunchInfo) -> tuple[str, str]:
    """Return the executable and arguments used by the Windows task."""
    if launch_info['mode'] == 'exe':
        return launch_info['path'], '--no-dashboard'

    # For uv, wrap in PowerShell with -WindowStyle Hidden to suppress the
    # console window that uv.exe would otherwise show at logon.
    uv_path = launch_info['path']
    proj_path = _required_project(launch_info)
    uv_args = subprocess.list2cmdline(['--project', proj_path, 'run', 'fleasion', '--no-dashboard'])
    log_path = launch_info.get('log')
    ps_script = (
        'try{'
        f'Start-Process -FilePath {_ps_single_quote(uv_path)} '
        f'-ArgumentList {_ps_single_quote(uv_args)} '
        '-WindowStyle Hidden -ErrorAction Stop'
        '}catch{'
    )
    if log_path:
        ps_script += (
            f'New-Item -ItemType Directory -Force -Path '
            f'{_ps_single_quote(str(Path(log_path).parent))}|Out-Null;'
            f'Add-Content -LiteralPath {_ps_single_quote(log_path)} '
            "-Value ((Get-Date -Format o)+' '+($_|Out-String));"
        )
    ps_script += 'exit 1}'
    ps_encoded = base64.b64encode(ps_script.encode('utf-16-le')).decode('ascii')
    return (
        'powershell.exe',
        f'-WindowStyle Hidden -NoProfile -NonInteractive '  # ruff: ignore[implicit-string-concatenation-in-collection-literal]
        f'-ExecutionPolicy Bypass -EncodedCommand {ps_encoded}',
    )


def _create_windows_task_as_current_user(launch_info: LaunchInfo) -> bool:
    """Create/update the per-user task without requiring elevation.

    ``schtasks /Create`` rejects standard-user task creation on current Windows
    builds, even when the requested task is limited to the interactive user.
    The Task Scheduler COM API supports the intended per-user interactive-token
    task without that elevation requirement.
    """
    import textwrap  # ruff: ignore[import-outside-top-level]

    command, args = _windows_launch_action(launch_info)
    script = textwrap.dedent(
        f"""
        $ErrorActionPreference = 'Stop'
        $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $service = New-Object -ComObject 'Schedule.Service'
        $service.Connect()
        $folder = $service.GetFolder('\')
        $definition = $service.NewTask(0)
        $definition.RegistrationInfo.Description = 'Fleasion per-user autostart'
        $definition.Settings.Enabled = $true
        $definition.Settings.Hidden = $true
        $definition.Settings.MultipleInstances = 2
        $trigger = $definition.Triggers.Create(9)
        $trigger.Enabled = $true
        $trigger.UserId = $userId
        $principal = $definition.Principal
        $principal.UserId = $userId
        $principal.LogonType = 3
        $principal.RunLevel = 0
        $action = $definition.Actions.Create(0)
        $action.Path = {_ps_single_quote(command)}
        $action.Arguments = {_ps_single_quote(args)}
        $registered = $folder.RegisterTaskDefinition(
            {_ps_single_quote(TASK_NAME)}, $definition, 6, $userId, $null, 3, $null
        )
        if ($registered.Definition.Principal.LogonType -ne 3 -or
            $registered.Definition.Principal.RunLevel -ne 0) {{
            throw 'Task Scheduler returned a task with an unexpected privilege level'
        }}
        """
    ).strip()
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
            [  # ruff: ignore[start-process-with-partial-path]
                'powershell.exe',
                '-NoProfile',
                '-NonInteractive',
                '-ExecutionPolicy',
                'Bypass',
                '-EncodedCommand',
                encoded,
            ],
            capture_output=True,
            creationflags=_creation_flags(),
            timeout=15,
        )
        if result.returncode != 0:
            _log(
                f'PowerShell Task Scheduler registration failed (rc={result.returncode}): '
                f'{result.stdout.decode(errors="replace").strip()} '
                f'{result.stderr.decode(errors="replace").strip()}'
            )
        return result.returncode == 0  # ruff: ignore[try-consider-else]
    except Exception as exc:  # ruff: ignore[blind-except]
        _log(f'Failed to create per-user scheduled task: {exc}')
        return False


def _grant_windows_task_user_control(windows_user_id: str) -> bool:
    """Grant the requesting user control of an elevated-created task.

    An elevated ``schtasks /Create /XML`` call can leave the task owned by
    Administrators even when its principal is an interactive, least-privilege
    user.  Preserve the task's existing ACL and add full control for the user
    that will update it on future normal launches.
    """
    import textwrap  # ruff: ignore[import-outside-top-level]

    script = textwrap.dedent(
        f"""
        $ErrorActionPreference = 'Stop'
        $service = New-Object -ComObject 'Schedule.Service'
        $service.Connect()
        $task = $service.GetFolder('\\').GetTask({_ps_single_quote(TASK_NAME)})
        $account = New-Object System.Security.Principal.NTAccount({_ps_single_quote(str(windows_user_id))})
        $sid = $account.Translate([System.Security.Principal.SecurityIdentifier]).Value
        $descriptor = $task.GetSecurityDescriptor(15)
        $ace = '(A;;FA;;;' + $sid + ')'
        if ($descriptor.IndexOf($ace, [System.StringComparison]::Ordinal) -lt 0) {{
            $task.SetSecurityDescriptor($descriptor + $ace, 0)
        }}
        """
    ).strip()
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
            [  # ruff: ignore[start-process-with-partial-path]
                'powershell.exe',
                '-NoProfile',
                '-NonInteractive',
                '-ExecutionPolicy',
                'Bypass',
                '-EncodedCommand',
                encoded,
            ],
            capture_output=True,
            creationflags=_creation_flags(),
            timeout=15,
        )
        if result.returncode != 0:
            _log(
                f'PowerShell Task Scheduler ACL repair failed (rc={result.returncode}): '
                f'{result.stdout.decode(errors="replace").strip()} '
                f'{result.stderr.decode(errors="replace").strip()}'
            )
        return result.returncode == 0  # ruff: ignore[try-consider-else]
    except Exception as exc:  # ruff: ignore[blind-except]
        _log(f'Failed to repair scheduled task permissions: {exc}')
        return False


def _create_task(  # ruff: ignore[too-many-return-statements]
    launch_info: LaunchInfo,
    *,
    windows_user_id: str | None = None,
) -> bool:
    """Create a per-user autostart entry without elevation."""
    if sys.platform == 'darwin':
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            if launch_info['mode'] == 'exe':
                args = [launch_info['path'], '--no-dashboard']
                working_dir = str(Path(launch_info['path']).parent)
                env = {}
            else:
                project = Path(_required_project(launch_info))
                args = [
                    launch_info['path'],
                    str(project / 'launcher.py'),
                    '--no-dashboard',
                ]
                working_dir = str(project)
                env = {'PYTHONPATH': str(project / 'src')}

            plist = {
                'Label': LAUNCH_AGENT_ID,
                'ProgramArguments': args,
                'RunAtLoad': True,
                'WorkingDirectory': working_dir,
                'StandardOutPath': str(
                    USER_HOME / 'Library' / 'Logs' / 'Fleasion.autostart.out.log'
                ),
                'StandardErrorPath': str(
                    USER_HOME / 'Library' / 'Logs' / 'Fleasion.autostart.err.log'
                ),
            }
            if env:
                plist['EnvironmentVariables'] = env

            LAUNCH_AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LAUNCH_AGENT_PATH.open('wb') as f:
                plistlib.dump(plist, f)
            # Do not load the agent in the current login session. RunAtLoad
            # would immediately launch a second Fleasion instance while the
            # first one is still completing startup. macOS discovers the plist
            # automatically on the next login.
            _log('LaunchAgent updated; it will take effect at the next login')
            return True  # ruff: ignore[try-consider-else]
        except Exception as e:  # ruff: ignore[blind-except]
            _log(f'Failed to create LaunchAgent: {e}')
            return False

    if sys.platform.startswith('linux'):
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            if launch_info['mode'] == 'linux-launcher':
                launcher = Path(launch_info['path'])
                command = _desktop_exec_join([str(launcher), '--no-dashboard'])
                working_dir = str(launcher.parent)
            elif launch_info['mode'] == 'exe':
                command = _desktop_exec_join([launch_info['path'], '--no-dashboard'])
                working_dir = str(Path(launch_info['path']).parent)
            else:
                project = Path(_required_project(launch_info))
                command = _desktop_exec_join(
                    [
                        launch_info['path'],
                        str(project / 'launcher.py'),
                        '--no-dashboard',
                    ]
                )
                working_dir = str(project)

            content = (
                '[Desktop Entry]\n'
                'Type=Application\n'
                'Name=Fleasion\n'
                f'Exec={command}\n'
                f'Path={working_dir}\n'
                'Terminal=false\n'
                'X-GNOME-Autostart-enabled=true\n'
            )
            LINUX_AUTOSTART_PATH.parent.mkdir(parents=True, exist_ok=True)
            LINUX_AUTOSTART_PATH.write_text(content, encoding='utf-8')
            _log('XDG autostart entry updated; it will take effect at the next login')
            return True  # ruff: ignore[try-consider-else]
        except Exception as e:  # ruff: ignore[blind-except]
            _log(f'Failed to create XDG autostart entry: {e}')
            return False

    if not windows_user_id:
        return _create_windows_task_as_current_user(launch_info)

    import html as _html  # ruff: ignore[import-outside-top-level]
    import tempfile  # ruff: ignore[import-outside-top-level]
    import textwrap  # ruff: ignore[import-outside-top-level]

    # Resolve the current user so the task is scoped to them specifically.
    # Without an explicit <UserId> in the XML, Windows may not associate the
    # task with the correct user and can silently discard it after a restart.
    if windows_user_id:
        raw_user_id = str(windows_user_id)
    else:
        username = os.environ.get('USERNAME', '')
        domain = os.environ.get('USERDOMAIN', os.environ.get('COMPUTERNAME', ''))
        raw_user_id = f'{domain}\\{username}' if domain else username
    user_id = _html.escape(raw_user_id)

    if launch_info['mode'] == 'exe':
        command = _html.escape(launch_info['path'])
        args = '--no-dashboard'
    else:
        # For uv, wrap in PowerShell with -WindowStyle Hidden to suppress the
        # console window that uv.exe would otherwise show at logon.
        uv_path = launch_info['path']
        proj_path = _required_project(launch_info)
        uv_args = subprocess.list2cmdline(
            ['--project', proj_path, 'run', 'fleasion', '--no-dashboard']
        )
        log_path = launch_info.get('log')
        ps_script = (
            'try{'
            f'Start-Process -FilePath {_ps_single_quote(uv_path)} '
            f'-ArgumentList {_ps_single_quote(uv_args)} '
            '-WindowStyle Hidden -ErrorAction Stop'
            '}catch{'
        )
        if log_path:
            ps_script += (
                f'New-Item -ItemType Directory -Force -Path {_ps_single_quote(str(Path(log_path).parent))}|Out-Null;'
                f'Add-Content -LiteralPath {_ps_single_quote(log_path)} '
                "-Value ((Get-Date -Format o)+' '+($_|Out-String));"
            )
        ps_script += 'exit 1}'
        ps_encoded = base64.b64encode(ps_script.encode('utf-16-le')).decode('ascii')
        ps_cmd = (
            f'-WindowStyle Hidden -NoProfile -NonInteractive '
            f'-ExecutionPolicy Bypass -EncodedCommand {ps_encoded}'
        )
        command = 'powershell.exe'
        args = _html.escape(ps_cmd)

    # Use a per-user interactive task. Env Proxy does not need elevation, and
    # hosts mode can request it only when the user explicitly selects that mode.
    # Both <Principal> and <LogonTrigger> must carry <UserId> so that:
    #   - The task is owned by (and runs as) the correct user account.
    #   - The logon trigger fires only when that specific user logs on.
    xml = textwrap.dedent(f"""
        <?xml version="1.0" encoding="UTF-16"?>
        <Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
          <Triggers>
            <LogonTrigger>
              <Enabled>true</Enabled>
              <UserId>{user_id}</UserId>
            </LogonTrigger>
          </Triggers>
          <Principals>
            <Principal id="Author">
              <UserId>{user_id}</UserId>
              <LogonType>InteractiveToken</LogonType>
              <RunLevel>LeastPrivilege</RunLevel>
            </Principal>
          </Principals>
          <Settings>
            <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
            <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
            <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
            <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
            <Hidden>true</Hidden>
            <Priority>7</Priority>
          </Settings>
          <Actions>
            <Exec>
              <Command>{command}</Command>
              <Arguments>{args}</Arguments>
            </Exec>
          </Actions>
        </Task>
    """).strip()

    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', encoding='utf-16', delete=False) as f:
        f.write(xml)
        tmp = f.name

    try:
        r = subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
            ['schtasks', '/Create', '/TN', TASK_NAME, '/XML', tmp, '/F'],  # ruff: ignore[start-process-with-partial-path]
            capture_output=True,
            creationflags=_creation_flags(),
            timeout=15,
        )
        if r.returncode != 0:
            _log(
                f'schtasks failed (rc={r.returncode}): '
                f'{r.stdout.decode(errors="replace").strip()} '
                f'{r.stderr.decode(errors="replace").strip()}'
            )
            return False
        return _grant_windows_task_user_control(raw_user_id)
    except Exception as e:  # ruff: ignore[blind-except]
        _log(f'Failed to create scheduled task: {e}')
        return False
    finally:
        try:
            os.unlink(tmp)  # ruff: ignore[os-unlink]
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass


def _get_stored_launch_info(config_dir: Path) -> LaunchInfo | None:
    p = config_dir / 'autostart_info.json'
    try:
        if not p.exists():
            return None
        payload: object = json.loads(p.read_text())
        return _json_launch_info(payload)
    except Exception:  # ruff: ignore[blind-except]
        return None


def _save_launch_info(config_dir: Path, info: LaunchInfo) -> None:
    try:
        (config_dir / 'autostart_info.json').write_text(json.dumps(info))
    except Exception:  # ruff: ignore[blind-except, try-except-pass]
        pass


def sync_autostart(
    enabled: bool,
    config_dir: Path,
    *,
    windows_user_id: str | None = None,
    proxy_mode: str | None = None,
) -> bool:
    """Ensure the platform autostart entry matches the desired state.

    Called on startup (to update if launch method changed) and when the
    user toggles the setting.  Returns True on success.
    """
    if not enabled:
        registry_ok = True
        if sys.platform == 'win32':
            registry_ok = _delete_windows_run_entry()
        if _task_exists():
            task_ok = _delete_task()
            if not task_ok:
                _log(
                    'Run on Boot could not be fully disabled because the legacy '
                    f'scheduled task {TASK_NAME!r} could not be removed'
                )
            return task_ok and registry_ok
        return registry_ok

    current = _get_launch_info()
    if current.get('mode') == 'uv':
        current['log'] = str(config_dir / 'autostart_launch_error.log')
    if proxy_mode is not None:
        # Persist the mode so switching Hosts <-> Env forces a task refresh.
        # This also repairs stale legacy HighestAvailable tasks when the user
        # moves into Env Proxy mode.
        current['proxy_mode'] = proxy_mode
    stored = _get_stored_launch_info(config_dir)

    # Packaged Windows builds only need a normal per-user logon launch.  The
    # HKCU Run key provides that directly and avoids blocking startup on both
    # schtasks.exe and a full PowerShell/Task Scheduler COM initialization.
    # Keep Task Scheduler for development launches, where PowerShell is still
    # used to hide uv.exe's console, and for targeted elevated legacy repair.
    if (
        sys.platform == 'win32'
        and current.get('mode') == 'exe'
        and windows_user_id is None
        and len(_windows_run_command(current)) <= _WINDOWS_RUN_COMMAND_MAX
    ):
        entry_was_current = _windows_run_entry_matches(current)
        ok = entry_was_current or _set_windows_run_entry(current)
        if ok:
            _save_launch_info(config_dir, current)
            _delete_legacy_windows_task_async(config_dir)
            if not entry_was_current:
                _log('Native per-user Windows autostart updated')
        return ok

    # Recreate if: task missing, or launch method changed since last save.
    # NOTE: _create_task uses /F (force-overwrite), so we must NOT pre-delete
    # the old task.  If we deleted first and creation failed, the task would be
    # permanently gone while run_on_boot remains True in settings.
    if not _task_exists() or stored != current:
        ok = _create_task(current, windows_user_id=windows_user_id)
        if ok:
            _save_launch_info(config_dir, current)
            if sys.platform == 'win32' and windows_user_id is None:
                # Remove a packaged Run entry when switching to a development
                # launch or when an unusually long command requires the task
                # fallback. Do this only after the replacement task succeeds.
                _delete_windows_run_entry()
        return ok
    if sys.platform == 'win32' and windows_user_id is None:
        _delete_windows_run_entry()
    return True
