"""Task Scheduler-based autostart for Fleasion.

Creates a scheduled task that runs Fleasion at user logon with highest privileges
(no UAC prompt).  Detects whether we're running as a compiled .exe or via uv run
and updates the task when the launch method changes.
"""

import os
import sys
import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Use Fleasion's log_buffer when available, fall back to Python logger
def _log(msg: str) -> None:
    try:
        from ..utils.logging import log_buffer
        log_buffer.log('Autostart', msg)
    except Exception:
        logger.info(msg)

TASK_NAME = 'Fleasion_Autostart'


# Bump this whenever the task XML format changes to force recreation on next launch.
_TASK_FORMAT_VERSION = 4

def _get_launch_info() -> dict:
    """Return a dict describing how to launch the current instance."""
    if getattr(sys, 'frozen', False):
        return {'mode': 'exe', 'path': sys.executable, '_fmt': _TASK_FORMAT_VERSION}

    # Dev / uv run
    import shutil
    uv = shutil.which('uv') or shutil.which('uv.exe') or 'uv'
    # Find project root (dir containing pyproject.toml)
    check = Path(sys.argv[0]).resolve().parent
    for _ in range(8):
        if (check / 'pyproject.toml').exists():
            break
        check = check.parent
    return {'mode': 'uv', 'path': uv, 'project': str(check), '_fmt': _TASK_FORMAT_VERSION}


def _task_exists() -> bool:
    try:
        r = subprocess.run(
            ['schtasks', '/Query', '/TN', TASK_NAME],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _delete_task() -> None:
    try:
        subprocess.run(
            ['schtasks', '/Delete', '/TN', TASK_NAME, '/F'],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10,
        )
    except Exception:
        pass


def _create_task(launch_info: dict) -> bool:
    """Create the scheduled task with highest privileges (no UAC on logon)."""
    import tempfile, textwrap, html as _html

    # Resolve the current user so the task is scoped to them specifically.
    # Without an explicit <UserId> in the XML, Windows may not associate the
    # task with the correct user and can silently discard it after a restart.
    _username = os.environ.get('USERNAME', '')
    _domain   = os.environ.get('USERDOMAIN', os.environ.get('COMPUTERNAME', ''))
    user_id = _html.escape(f'{_domain}\\{_username}' if _domain else _username)

    if launch_info['mode'] == 'exe':
        command = _html.escape(launch_info['path'])
        args = '--no-dashboard'
    else:
        # For uv, wrap in PowerShell with -WindowStyle Hidden to suppress the
        # console window that uv.exe would otherwise show at logon.
        uv_path   = launch_info['path']
        proj_path = launch_info['project']
        # PowerShell command: use single-quotes around paths (PS native quoting)
        ps_cmd = (
            f"-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -Command "
            f"& '{uv_path}' --project '{proj_path}' "
            f"run fleasion --no-dashboard"
        )
        command = 'powershell.exe'
        args = _html.escape(ps_cmd)

    # We use an XML task definition so we can set RunLevel=HighestAvailable.
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
              <RunLevel>HighestAvailable</RunLevel>
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

    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml',
                                     encoding='utf-16', delete=False) as f:
        f.write(xml)
        tmp = f.name

    try:
        r = subprocess.run(
            ['schtasks', '/Create', '/TN', TASK_NAME, '/XML', tmp, '/F'],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15,
        )
        if r.returncode != 0:
            _log(f'schtasks failed (rc={r.returncode}): '
                 f'{r.stdout.decode(errors="replace").strip()} '
                 f'{r.stderr.decode(errors="replace").strip()}')
        return r.returncode == 0
    except Exception as e:
        _log(f'Failed to create scheduled task: {e}')
        return False
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def _get_stored_launch_info(config_dir: Path) -> dict | None:
    p = config_dir / 'autostart_info.json'
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except Exception:
        return None


def _save_launch_info(config_dir: Path, info: dict) -> None:
    try:
        (config_dir / 'autostart_info.json').write_text(json.dumps(info))
    except Exception:
        pass


def sync_autostart(enabled: bool, config_dir: Path) -> bool:
    """Ensure the scheduled task matches the desired state.

    Called on startup (to update if launch method changed) and when the
    user toggles the setting.  Returns True on success.
    """
    if not enabled:
        if _task_exists():
            _delete_task()
        return True

    current = _get_launch_info()
    stored = _get_stored_launch_info(config_dir)

    # Recreate if: task missing, or launch method changed since last save.
    # NOTE: _create_task uses /F (force-overwrite), so we must NOT pre-delete
    # the old task.  If we deleted first and creation failed, the task would be
    # permanently gone while run_on_boot remains True in settings.
    if not _task_exists() or stored != current:
        ok = _create_task(current)
        if ok:
            _save_launch_info(config_dir, current)
        return ok
    return True
