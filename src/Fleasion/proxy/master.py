"""ProxyMaster: manages the lifecycle of the Fleasion proxy.

Interception strategy:
  1. Write hosts file entries pointing assetdelivery.roblox.com and
     fts.rbxcdn.com at 127.0.0.1.  Roblox uses libcurl which honours
     the OS hosts file unconditionally (unlike WinINet PAC files).
  2. Run a direct TLS server on 127.0.0.1:443.  Roblox connects directly
     (no HTTP CONNECT tunnel needed) and we present a leaf cert signed by
     our local CA.  Roblox's libcurl validates it against the CA we install
     into each Roblox version's ssl/cacert.pem.
  3. On stop, remove our hosts entries and stop the server.

Admin requirement:
  Writing to %SystemRoot%\\System32\\drivers\\etc\\hosts and binding port 443
  both require administrator privileges. Fleasion will check for elevation
  and log an error if it is missing.

VPN compatibility:
  Loopback (127.0.0.1) traffic is never routed through VPN adapters.
  Only our proxy->CDN upstream connections go through the VPN (correct).
"""

import asyncio
import base64
import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
import winreg
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Set

from ..utils import (
    LOCAL_APPDATA,
    PROXY_CA_DIR,
    PROXY_PORT,
    ROBLOX_PROCESS,
    STORAGE_DB,
    STORAGE_DB_GDK,
    log_buffer,
    terminate_roblox,
    wait_for_roblox_exit,
)
from .addons import CacheScraper, TextureStripper
from .server import FleasionProxy, INTERCEPT_HOSTS
from ..cache.cache_manager import CacheManager
from ..utils.certs import generate_ca, generate_host_cert, get_ca_pem

logger = logging.getLogger(__name__)

HOSTS_FILE = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'drivers' / 'etc' / 'hosts'
_HOSTS_MARKER = '# Fleasion proxy entry'

# Registry key used by Windows to replace files on next reboot
_PENDING_RENAME_KEY   = r'SYSTEM\CurrentControlSet\Control\Session Manager'
_PENDING_RENAME_VALUE = 'PendingFileRenameOperations'
# Temp file that will replace the hosts file on next boot after a crash
_TEMP_CLEAN_HOSTS = Path(os.environ.get('TEMP', r'C:\Windows\Temp')) / 'fleasion_hosts_restore.txt'
# Tracks which elevated Fleasion PID currently owns the proxy/hosts/watchdog.
# Other instances check this on startup to avoid disturbing a live proxy.
_PROXY_OWNER_PID_FILE = Path(os.environ.get('TEMP', r'C:\Windows\Temp')) / 'fleasion_proxy_owner.pid'

# ---------------------------------------------------------------------------
# Task-Scheduler watchdog (force-kill guard)
# ---------------------------------------------------------------------------
# When the proxy is running, we maintain a Windows Task Scheduler task that
# fires ~5 seconds into the future.  A background thread refreshes the task
# every 3 seconds so it never actually fires during normal operation.  If the
# process is force-killed (Task Manager, etc.) the task fires within 5 s and
# restores the hosts file.
#
# StartWhenAvailable is set to FALSE in the task XML.  This means if the
# scheduled time passes while the PC is OFF (power loss, BSOD), the task
# will NEVER fire retroactively on the next boot — the PendingFileRename
# guard handles that case instead.  On the next Fleasion launch we also
# delete any stale watchdog task left from a previous crash.
# ---------------------------------------------------------------------------

_WATCHDOG_TASK_NAME = 'Fleasion-HostsWatchdog'
_WATCHDOG_LOOKAHEAD = 15  # seconds ahead the task is scheduled
_WATCHDOG_INTERVAL  = 3   # seconds between watchdog refreshes
_WATCHDOG_TASK_XML  = Path(os.environ.get('TEMP', r'C:\Windows\Temp')) / 'fleasion_watchdog_task.xml'

# PowerShell command that strips Fleasion entries from the hosts file and
# flushes DNS.  Encoded as UTF-16-LE base64 to avoid XML/shell-escaping pain.
#
# Guarded by a PID check: if a Fleasion process is still alive and owns the
# proxy (PID file present + process running), the script exits without touching
# the hosts file.  This prevents two failure modes:
#   1. Kill-and-replace: old instance's task fires after new instance wrote hosts.
#   2. Slow-machine false-fire: AV delays schtasks long enough that the task's
#      trigger time passes before the next refresh updates it.
#
# The PID file path is embedded literally (not via $env:TEMP) because this
# script runs as SYSTEM whose %TEMP% is C:\Windows\Temp, not the user's folder.
_pid_path_ps = str(_PROXY_OWNER_PID_FILE).replace('\\', '/')
_WATCHDOG_PS_CMD = (
    f'$pp="{_pid_path_ps}";'
    '$alive=$false;'
    'if(Test-Path $pp){'
    'try{$fpid=[int](Get-Content $pp -Raw);'
    'if(Get-Process -Id $fpid -ErrorAction SilentlyContinue){$alive=$true}}catch{}};'
    'if(-not $alive){'
    '$f="$env:SystemRoot/System32/drivers/etc/hosts";'
    "[System.IO.File]::WriteAllLines($f,((Get-Content $f)|Where-Object{$_ -notmatch '# Fleasion proxy entry'}));"
    "Start-Process 'ipconfig.exe' '/flushdns' -NoNewWindow -Wait}"
)
_WATCHDOG_PS_ENCODED: str = base64.b64encode(_WATCHDOG_PS_CMD.encode('utf-16-le')).decode('ascii')


def _build_watchdog_xml(run_at: datetime) -> str:
    """Build a Task Scheduler XML document for a once-off task at *run_at*."""
    boundary = run_at.strftime('%Y-%m-%dT%H:%M:%S')
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Fleasion hosts watchdog: restores hosts file if Fleasion exits without cleanup</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{boundary}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT1M</ExecutionTimeLimit>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand {_WATCHDOG_PS_ENCODED}</Arguments>
    </Exec>
  </Actions>
</Task>"""


def _upsert_watchdog_task() -> None:
    """Create (or replace) the watchdog task to fire _WATCHDOG_LOOKAHEAD seconds from now."""
    try:
        run_at = datetime.now() + timedelta(seconds=_WATCHDOG_LOOKAHEAD)
        xml = _build_watchdog_xml(run_at)
        _WATCHDOG_TASK_XML.write_text(xml, encoding='utf-16')
        result = subprocess.run(
            ['schtasks', '/create', '/TN', _WATCHDOG_TASK_NAME,
             '/XML', str(_WATCHDOG_TASK_XML), '/RU', 'SYSTEM', '/F'],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or b'').decode('utf-8', errors='replace').strip()
            log_buffer.log('Watchdog', f'schtasks returned non-zero ({result.returncode}): {err}')
    except Exception as exc:
        log_buffer.log('Watchdog', f'Could not upsert watchdog task (non-fatal): {exc}')


def _delete_watchdog_task() -> None:
    """Delete the watchdog task if it exists.  Safe to call even if absent."""
    try:
        result = subprocess.run(
            ['schtasks', '/delete', '/TN', _WATCHDOG_TASK_NAME, '/F'],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
        if result.returncode == 0:
            log_buffer.log('Watchdog', 'Task deleted (clean exit)')
    except Exception:
        pass
    try:
        _WATCHDOG_TASK_XML.unlink(missing_ok=True)
    except OSError:
        pass


def _resolve_real_ips(hosts: set) -> dict:
    """Resolve real IPs for each host BEFORE we write hosts file entries.

    We MUST do this first - once hosts file points them to 127.0.0.1, any
    subsequent socket.getaddrinfo() call would return 127.0.0.1, causing our
    upstream connections to loop back to ourselves.
    """
    import socket
    real_ips: dict = {}
    for host in sorted(hosts):
        try:
            # getaddrinfo returns all IPs; take the first IPv4 one
            results = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
            ips = [r[4][0] for r in results if r[4][0] != '127.0.0.1']
            if ips:
                real_ips[host] = ips
                log_buffer.log('Proxy', f'Resolved {host} -> {ips[0]}')
            else:
                log_buffer.log('Proxy', f'Warning: no real IPs found for {host}')
        except Exception as exc:
            log_buffer.log('Proxy', f'DNS resolve failed for {host}: {exc}')
    return real_ips


def _flush_dns() -> None:
    """Flush Windows DNS client cache so the hosts file changes take effect immediately."""
    try:
        subprocess.run(
            ['ipconfig', '/flushdns'],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
        log_buffer.log('Hosts', 'DNS cache flushed')
    except Exception as exc:
        log_buffer.log('Hosts', f'DNS flush failed (non-fatal): {exc}')


# ---------------------------------------------------------------------------
# Reboot-time crash guard (PendingFileRenameOperations)
# ---------------------------------------------------------------------------

def _pid_is_alive(pid: int) -> bool:
    """Return True if the process with *pid* is still running."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return False
    try:
        code = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _other_proxy_owner_alive() -> bool:
    """Return True if another elevated Fleasion instance currently owns the proxy.

    Checked at startup so we never delete another instance's watchdog or
    hosts entries while it is still running.
    """
    try:
        pid = int(_PROXY_OWNER_PID_FILE.read_text().strip())
        return pid != os.getpid() and _pid_is_alive(pid)
    except (OSError, ValueError):
        return False

def _nt_path(p: Path) -> str:
    """Return the NT namespace path required by PendingFileRenameOperations."""
    return f'\\??\\{p}'


def _schedule_hosts_cleanup_on_reboot() -> None:
    """Register a PendingFileRenameOperations entry that replaces the hosts file
    with a clean (Fleasion-entry-free) copy on the next Windows boot.

    This acts as a crash / sudden-power-loss guard: if the process is killed
    before stop() can remove our hosts entries, they will be cleaned on the
    next reboot automatically — even before any user process starts.
    """
    try:
        # Build a clean copy of the current hosts file (strip Fleasion lines)
        try:
            original = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
        except OSError:
            original = ''
        clean_content = ''.join(
            line for line in original.splitlines(keepends=True)
            if _HOSTS_MARKER not in line and not any(
                f'127.0.0.1 {h}' in line for h in INTERCEPT_HOSTS
            )
        )
        _TEMP_CLEAN_HOSTS.write_text(clean_content, encoding='utf-8')

        src = _nt_path(_TEMP_CLEAN_HOSTS)
        dst = _nt_path(HOSTS_FILE)

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, _PENDING_RENAME_KEY,
            access=winreg.KEY_ALL_ACCESS,
        ) as key:
            try:
                existing, _ = winreg.QueryValueEx(key, _PENDING_RENAME_VALUE)
                entries: list = list(existing)
            except FileNotFoundError:
                entries = []

            # Remove any stale Fleasion entries to avoid duplicates
            filtered: list = []
            i = 0
            while i < len(entries):
                if i + 1 < len(entries):
                    if entries[i].lower() == src.lower():
                        i += 2
                        continue
                    filtered.append(entries[i])
                    filtered.append(entries[i + 1])
                    i += 2
                else:
                    filtered.append(entries[i])
                    i += 1

            filtered.extend([src, dst])
            winreg.SetValueEx(key, _PENDING_RENAME_VALUE, 0, winreg.REG_MULTI_SZ, filtered)

        log_buffer.log('Hosts', 'Crash guard: hosts cleanup scheduled for next reboot')
    except Exception as exc:
        log_buffer.log('Hosts', f'Could not schedule reboot cleanup (non-fatal): {exc}')


def _cancel_hosts_cleanup_on_reboot() -> None:
    """Remove the PendingFileRenameOperations entry added by
    _schedule_hosts_cleanup_on_reboot.

    Called after a successful stop() so the boot-time cleanup does not run
    unnecessarily and cannot interfere with a subsequent Fleasion session.
    """
    try:
        src = _nt_path(_TEMP_CLEAN_HOSTS)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, _PENDING_RENAME_KEY,
            access=winreg.KEY_ALL_ACCESS,
        ) as key:
            try:
                existing, _ = winreg.QueryValueEx(key, _PENDING_RENAME_VALUE)
                entries: list = list(existing)
            except FileNotFoundError:
                return  # nothing to remove

            filtered: list = []
            i = 0
            while i < len(entries):
                if i + 1 < len(entries):
                    if entries[i].lower() == src.lower():
                        i += 2
                        continue
                    filtered.append(entries[i])
                    filtered.append(entries[i + 1])
                    i += 2
                else:
                    filtered.append(entries[i])
                    i += 1

            if filtered:
                winreg.SetValueEx(key, _PENDING_RENAME_VALUE, 0, winreg.REG_MULTI_SZ, filtered)
            else:
                try:
                    winreg.DeleteValue(key, _PENDING_RENAME_VALUE)
                except FileNotFoundError:
                    pass

        try:
            _TEMP_CLEAN_HOSTS.unlink(missing_ok=True)
        except OSError:
            pass

        log_buffer.log('Hosts', 'Crash guard: reboot cleanup cancelled (clean exit)')
    except Exception as exc:
        log_buffer.log('Hosts', f'Could not cancel reboot cleanup (non-fatal): {exc}')


# ---------------------------------------------------------------------------
# Admin check
# ---------------------------------------------------------------------------

def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Hosts file management
# ---------------------------------------------------------------------------

def _add_hosts_entries(hosts: Set[str]) -> bool:
    """Append redirect entries for *hosts* to the system hosts file.

    Returns True on success.  Skips entries already present.
    Creates the hosts file from the Windows default if it is missing.
    """
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        # hosts file is missing entirely — recreate it with the Windows default
        existing = (
            '# Copyright (c) 1993-2009 Microsoft Corp.\n'
            '#\n'
            '# This is a sample HOSTS file used by Microsoft TCP/IP for Windows.\n'
            '#\n'
            '# This file contains the mappings of IP addresses to host names. Each\n'
            '# entry should be kept on an individual line. The IP address should\n'
            '# be placed in the first column followed by the corresponding host name.\n'
            '# The IP address and the host name should be separated by at least one\n'
            '# space.\n'
            '#\n'
            '# Additionally, comments (such as these) may be inserted on individual\n'
            '# lines or following the machine name denoted by a \'#\' symbol.\n'
            '#\n'
            '# For example:\n'
            '#\n'
            '#      102.54.94.97     rhino.acme.com          # source server\n'
            '#       38.25.63.10     x.acme.com              # x client host\n'
            '\n'
            '# localhost name resolution is handled within DNS itself.\n'
            '#\t127.0.0.1       localhost\n'
            '#\t::1             localhost\n'
        )
        try:
            HOSTS_FILE.write_text(existing, encoding='utf-8')
            log_buffer.log('Hosts', 'hosts file was missing — created new default hosts file')
        except OSError as exc:
            log_buffer.log('Hosts', f'Failed to create hosts file: {exc}')
            return False
    except OSError as exc:
        log_buffer.log('Hosts', f'Cannot read hosts file: {exc}')
        return False

    lines_to_add = []
    for host in sorted(hosts):
        entry = f'127.0.0.1 {host} {_HOSTS_MARKER}'
        if host not in existing:
            lines_to_add.append(entry)

    if not lines_to_add:
        log_buffer.log('Hosts', 'Hosts entries already present, skipping')
        return True

    new_content = existing.rstrip('\n') + '\n' + '\n'.join(lines_to_add) + '\n'
    try:
        HOSTS_FILE.write_text(new_content, encoding='utf-8')
        for host in sorted(hosts):
            log_buffer.log('Hosts', f'Added redirect: {host} -> 127.0.0.1')
        return True
    except PermissionError:
        log_buffer.log('Hosts', 'Permission denied writing hosts file - run as Administrator')
        return False
    except OSError as exc:
        log_buffer.log('Hosts', f'Failed to write hosts file: {exc}')
        return False


def _remove_hosts_entries(hosts: Set[str]) -> None:
    """Remove any hosts file entries we previously added."""
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return

    lines = existing.splitlines(keepends=True)
    filtered = [
        line for line in lines
        if _HOSTS_MARKER not in line and not any(
            f'127.0.0.1 {h}' in line for h in hosts
        )
    ]

    if len(filtered) == len(lines):
        return  # nothing to remove

    try:
        HOSTS_FILE.write_text(''.join(filtered), encoding='utf-8')
        log_buffer.log('Hosts', 'Removed proxy hosts entries')
    except OSError as exc:
        log_buffer.log('Hosts', f'Failed to clean hosts file: {exc}')


# ---------------------------------------------------------------------------
# Roblox CA installation
# ---------------------------------------------------------------------------

def _find_roblox_dirs() -> list:
    """Locate every RobloxPlayerBeta.exe installation via registry and known paths.

    Methods used (combined):
      1. Main Registry   — HKCU\\Software (two levels) for REG_SZ "PlayerPath"
      2. MS Store        — C:\\XboxGames\\Roblox up to two layers deep
      3. Active Roblox   — HKCU\\...\\roblox-player\\open\\command (Default)
      4. Regular Roblox  — %LocalAppData%\\Roblox\\Versions one layer deep
    """
    import winreg

    found: list = []
    seen: set = set()

    def _add(path: Path) -> bool:
        key = str(path)
        if key not in seen:
            found.append(path)
            seen.add(key)
            return True
        return False

    def _scan_for_exe(root: Path, max_depth: int) -> list:
        """Return all subdirs up to max_depth layers under root that contain RobloxPlayerBeta.exe."""
        results: list = []

        def _recurse(path: Path, depth: int) -> None:
            try:
                for entry in os.scandir(path):
                    if not entry.is_dir():
                        continue
                    if os.path.isfile(os.path.join(entry.path, ROBLOX_PROCESS)):
                        results.append(Path(entry.path))
                    if depth < max_depth:
                        _recurse(Path(entry.path), depth + 1)
            except OSError:
                pass

        if root.is_dir():
            _recurse(root, 1)
        return results

    # ── 1. Main Registry Search ──────────────────────────────────────────
    # Walk HKCU\Software and one layer of subkeys; collect any "PlayerPath" value.
    t = time.perf_counter()
    reg_found = 0

    def _check_player_path_key(key) -> None:
        nonlocal reg_found
        try:
            val, rtype = winreg.QueryValueEx(key, 'PlayerPath')
        except OSError:
            return
        if rtype != winreg.REG_SZ or not val:
            return
        p = Path(val)
        # PlayerPath may occasionally point at the exe itself rather than the dir
        if p.name.lower() == ROBLOX_PROCESS.lower():
            p = p.parent
        if os.path.isfile(os.path.join(str(p), ROBLOX_PROCESS)):
            reg_found += 1
            _add(p)
        else:
            for d in _scan_for_exe(p, 1):
                reg_found += 1
                _add(d)

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software') as hkey:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(hkey, i); i += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(hkey, name) as sk:
                        _check_player_path_key(sk)
                        j = 0
                        while True:
                            try:
                                sub = winreg.EnumKey(sk, j); j += 1
                            except OSError:
                                break
                            try:
                                with winreg.OpenKey(sk, sub) as ssk:
                                    _check_player_path_key(ssk)
                            except OSError:
                                pass
                except OSError:
                    pass
    except OSError:
        pass
    log_buffer.log('Certificate', f'  Registry PlayerPath: {int((time.perf_counter() - t) * 1000)} ms ({reg_found} found)')

    # ── 2. MS Store Version ──────────────────────────────────────────────
    # C:\XboxGames\Roblox, up to two layers deep.
    t = time.perf_counter()
    xbox_found = 0
    for d in _scan_for_exe(Path(r'C:\XboxGames\Roblox'), 2):
        xbox_found += 1
        _add(d)
    log_buffer.log('Certificate', f'  XboxGames\\Roblox: {int((time.perf_counter() - t) * 1000)} ms ({xbox_found} found)')

    # ── 3. Active Roblox ─────────────────────────────────────────────────
    # Read HKCU\...\roblox-player\shell\open\command (Default); parse the exe
    # path and search up to two layers under its parent directory.
    t = time.perf_counter()
    active_found = 0
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'SOFTWARE\Classes\roblox-player\shell\open\command',
        ) as key:
            try:
                cmd, rtype = winreg.QueryValueEx(key, '')
                if rtype == winreg.REG_SZ and cmd:
                    cmd = cmd.strip()
                    if cmd.startswith('"'):
                        exe_path = cmd[1 : cmd.index('"', 1)]
                    else:
                        exe_path = cmd.split()[0]
                    exe_dir = Path(exe_path).parent
                    for d in _scan_for_exe(exe_dir, 2):
                        active_found += 1
                        _add(d)
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    log_buffer.log('Certificate', f'  Active Roblox (registry): {int((time.perf_counter() - t) * 1000)} ms ({active_found} found)')

    # ── 4. Regular Roblox ────────────────────────────────────────────────
    # %LocalAppData%\Roblox\Versions — one layer down.
    t = time.perf_counter()
    roblox_found = 0
    for d in _scan_for_exe(LOCAL_APPDATA / 'Roblox' / 'Versions', 1):
        roblox_found += 1
        _add(d)
    log_buffer.log('Certificate', f'  AppData Roblox\\Versions: {int((time.perf_counter() - t) * 1000)} ms ({roblox_found} found)')

    return found


def _install_ca_into_roblox(ca_pem: str) -> None:
    """Append our CA cert to ssl/cacert.pem next to every found RobloxPlayerBeta.exe."""
    t0 = time.perf_counter()
    dirs = _find_roblox_dirs()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if not dirs:
        log_buffer.log('Certificate', f'No Roblox installs found to patch (scanned in {elapsed_ms} ms)')
        return
    log_buffer.log('Certificate', f'Found {len(dirs)} Roblox install(s) to patch (scanned in {elapsed_ms} ms)')

    for d in dirs:
        ssl_dir = d / 'ssl'
        ssl_dir.mkdir(exist_ok=True)
        ca_file = ssl_dir / 'cacert.pem'
        try:
            existing = ca_file.read_text() if ca_file.exists() else ''
            if ca_pem not in existing:
                ca_file.write_text(f'{existing}\n{ca_pem}')
                log_buffer.log('Certificate', f'Installed CA into {d.name}')
            else:
                log_buffer.log('Certificate', f'CA already installed in {d.name}')
        except (PermissionError, OSError) as exc:
            log_buffer.log('Certificate', f'Failed to write CA for {d.name}: {exc}')


def check_and_patch_running_roblox_ca(exe_path: 'Path') -> None:
    """Check if the currently running Roblox instance has our CA in its cacert.pem.

    Called when RobloxPlayerBeta.exe is detected launching at runtime.
    If the cert is absent it is injected immediately and an alert is logged.
    """
    ca_cert_path = PROXY_CA_DIR / 'ca.crt'
    if not ca_cert_path.exists():
        return  # CA not generated yet – nothing to patch

    ca_pem = get_ca_pem(ca_cert_path)
    roblox_dir = exe_path.parent
    ssl_dir = roblox_dir / 'ssl'
    ca_file = ssl_dir / 'cacert.pem'

    try:
        existing = ca_file.read_text(encoding='utf-8', errors='replace') if ca_file.exists() else ''
    except OSError:
        existing = ''

    if ca_pem in existing:
        return  # Already patched – nothing to do

    log_buffer.log(
        'Certificate',
        '[ALERT] The currently running RobloxPlayerBeta.exe does not have a modified '
        'cacert.pem! It has been injected into Roblox, you may need to relaunch it.',
    )
    try:
        ssl_dir.mkdir(exist_ok=True)
        ca_file.write_text(f'{existing}\n{ca_pem}', encoding='utf-8')
        log_buffer.log('Certificate', f'CA injected into running Roblox instance: {roblox_dir.name}')
    except (PermissionError, OSError) as exc:
        log_buffer.log('Certificate', f'Failed to inject CA into running Roblox instance: {exc}')


# ---------------------------------------------------------------------------
# ProxyMaster
# ---------------------------------------------------------------------------

class ProxyMaster:
    """Manages the Fleasion proxy lifecycle."""

    def __init__(self, config_manager) -> None:
        self.config_manager = config_manager
        self.cache_manager = CacheManager(config_manager)

        # Singleton addon instances - GUI holds references to these directly
        self.cache_scraper = CacheScraper(self.cache_manager)
        self.cache_scraper.set_enabled(False)
        self._texture_stripper: Optional[TextureStripper] = None

        self._proxy: Optional[FleasionProxy] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._hosts_installed: bool = False
        self._watchdog_stop: Optional[threading.Event] = None
        self._watchdog_thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def _start_watchdog(self) -> None:
        """Start the background thread that keeps the watchdog task pushed ahead."""
        self._watchdog_stop = threading.Event()
        stop_event = self._watchdog_stop

        def _loop() -> None:
            while not stop_event.wait(_WATCHDOG_INTERVAL):
                _upsert_watchdog_task()

        self._watchdog_thread = threading.Thread(
            target=_loop, daemon=True, name='fleasion-watchdog'
        )
        self._watchdog_thread.start()

    def _stop_watchdog(self) -> None:
        """Signal the watchdog thread to stop and delete the scheduled task."""
        if self._watchdog_stop:
            self._watchdog_stop.set()
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=2.0)
        _delete_watchdog_task()

    def start(self) -> None:
        with self._lock:
            if self._running:
                return

            def _run():
                try:
                    asyncio.run(self._run_proxy())
                except Exception as exc:
                    log_buffer.log('Error', f'Proxy failed: {exc}')
                    self._running = False

            self._thread = threading.Thread(target=_run, daemon=True, name='fleasion-proxy')
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running and not (self._thread and self._thread.is_alive()):
                return
            log_buffer.log('Proxy', 'Stopping proxy...')

            # Clean up hosts file first so Roblox stops routing to us immediately
            if self._hosts_installed:
                self._stop_watchdog()           # Cancel the force-kill guard task first
                _remove_hosts_entries(set(INTERCEPT_HOSTS))
                self._hosts_installed = False
                _flush_dns()  # Clear stale 127.0.0.1 cache so new connections stop coming in
                _cancel_hosts_cleanup_on_reboot()  # No longer needed after a clean stop
                try:
                    _PROXY_OWNER_PID_FILE.unlink(missing_ok=True)
                except OSError:
                    pass

            # Stop the asyncio server
            if self._proxy and self._loop and self._loop.is_running():
                try:
                    fut = asyncio.run_coroutine_threadsafe(self._proxy.stop(), self._loop)
                    fut.result(timeout=3.0)
                except Exception:
                    pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                log_buffer.log('Proxy', 'Warning: proxy thread did not stop cleanly')

    async def _run_proxy(self) -> None:
        self._running = True
        self._loop = asyncio.get_running_loop()

        # ── Admin check ───────────────────────────────────────────────────
        if not _is_admin():
            log_buffer.log('Error', (
                'Fleasion requires administrator privileges to modify the hosts file '
                'and bind port 443.  Please run as Administrator.'
            ))
            self._running = False
            return

        # ── Optional cache clear on launch ───────────────────────────────
        if self.config_manager.clear_cache_on_launch:
            if terminate_roblox():
                log_buffer.log('Cleanup', 'Roblox found, terminating...')
                if not wait_for_roblox_exit():
                    log_buffer.log('Cleanup', 'Termination timed out')
                else:
                    log_buffer.log('Cleanup', 'Roblox terminated')
                    try:
                        STORAGE_DB.unlink()
                        log_buffer.log('Cleanup', 'Storage deleted')
                    except (FileNotFoundError, PermissionError, OSError) as exc:
                        log_buffer.log('Cleanup', f'Storage deletion: {exc}')
                    if STORAGE_DB_GDK.parent.exists():
                        try:
                            STORAGE_DB_GDK.unlink()
                            log_buffer.log('Cleanup', 'Storage (GDK) deleted')
                        except FileNotFoundError:
                            pass
                        except (PermissionError, OSError) as exc:
                            log_buffer.log('Cleanup', f'Storage (GDK) deletion: {exc}')
            else:
                log_buffer.log('Cleanup', 'Roblox not running')
        else:
            log_buffer.log('Cleanup', 'Cache clear on launch disabled - skipping')

        # ── Certificate setup ─────────────────────────────────────────────
        log_buffer.log('Certificate', 'Generating/loading CA certificates...')
        t0 = time.perf_counter()
        try:
            ca_cert_path, ca_key_path = generate_ca(PROXY_CA_DIR)
        except Exception as exc:
            log_buffer.log('Certificate', f'CA generation failed: {exc}')
            self._running = False
            return

        host_certs = {}
        for host in INTERCEPT_HOSTS:
            try:
                cert_path, key_path = generate_host_cert(
                    host, ca_cert_path, ca_key_path, PROXY_CA_DIR,
                )
                host_certs[host] = (cert_path, key_path)
            except Exception as exc:
                log_buffer.log('Certificate', f'Leaf cert failed for {host}: {exc}')
                self._running = False
                return

        elapsed_ms = (time.perf_counter() - t0) * 1000
        log_buffer.log('Certificate', f'Certificates ready in {elapsed_ms:.0f} ms')

        # Install CA into Roblox ssl dirs
        ca_pem = get_ca_pem(ca_cert_path)
        _install_ca_into_roblox(ca_pem)

        # ── Clean up stale state from a previous crash ───────────────────
        # Skip cleanup entirely if another elevated Fleasion instance already
        # owns the proxy.  Deleting its watchdog task or hosts entries while it
        # is running would break it silently.
        if not _other_proxy_owner_alive():
            _delete_watchdog_task()
            # Remove stale hosts entries: if the previous session crashed without
            # calling stop(), our entries may still be present.  getaddrinfo()
            # would return 127.0.0.1 instead of real CDN IPs.
            _remove_hosts_entries(set(INTERCEPT_HOSTS))
            _flush_dns()
        else:
            log_buffer.log('Proxy', 'Another proxy owner is running — skipping startup cleanup')

        # ── Resolve real CDN IPs BEFORE writing new hosts file entries ────
        # CRITICAL: must happen after removing stale entries (above) and before
        # writing new ones. This guarantees getaddrinfo() returns real IPs.
        real_ips = _resolve_real_ips(set(INTERCEPT_HOSTS))

        # ── Create addon instances ────────────────────────────────────────
        self._texture_stripper = TextureStripper(self.config_manager)
        self._texture_stripper.set_cache_scraper(self.cache_scraper)
        # Give the scraper real IPs for ALL intercepted hosts so its API
        # calls bypass our hosts file redirect (including CDN redirects).
        scraper_ips = {
            host: ips[0]
            for host, ips in real_ips.items()
            if ips
        }
        self.cache_scraper.set_real_ips(scraper_ips)

        # Wire the scraper into the json_viewer's AssetFetcherThread so the
        # Preview tab in the standalone JSON viewer also bypasses the hosts file.
        try:
            from ..gui.json_viewer import AssetFetcherThread
            AssetFetcherThread.set_scraper(self.cache_scraper)
        except Exception:
            pass

        # ── Start TLS proxy server ────────────────────────────────────────
        self._proxy = FleasionProxy(
            texture_stripper=self._texture_stripper,
            cache_scraper=self.cache_scraper,
            host_certs=host_certs,
            upstream_ips=real_ips,
            port=PROXY_PORT,
        )
        try:
            await self._proxy.start()
        except OSError as exc:
            if exc.errno == 10013 or 'access' in str(exc).lower() or '443' in str(exc):
                log_buffer.log('Error', (
                    f'Cannot bind port {PROXY_PORT}: access denied. '
                    'Ensure no other process is using this port and run as Administrator.'
                ))
            else:
                log_buffer.log('Error', f'Failed to start proxy: {exc}')
            self._running = False
            return
        except Exception as exc:
            log_buffer.log('Error', f'Failed to start proxy: {exc}')
            self._running = False
            return

        # ── Write hosts file entries ──────────────────────────────────────
        if not _add_hosts_entries(set(INTERCEPT_HOSTS)):
            # Hosts write failed - stop the server and bail
            await self._proxy.stop()
            self._running = False
            return
        self._hosts_installed = True
        _flush_dns()  # Make the new entries take effect immediately
        try:
            _PROXY_OWNER_PID_FILE.write_text(str(os.getpid()))
        except OSError:
            pass
        _schedule_hosts_cleanup_on_reboot()  # Boot guard: power-loss / BSOD
        _upsert_watchdog_task()              # Initial task creation
        self._start_watchdog()               # Keep task pushed 5 s ahead

        log_buffer.log('Info', '=' * 50)
        log_buffer.log('Info', 'Fleasion Proxy Active')
        log_buffer.log('Info', f'Intercepting: {", ".join(sorted(INTERCEPT_HOSTS))}')
        log_buffer.log('Info', f'Port: {PROXY_PORT}')
        log_buffer.log('Info', 'Launch Roblox')
        log_buffer.log('Info', '=' * 50)

        # ── Pre-download private replacement assets in background ─────────
        # Runs eagerly at startup so pre-downloaded files are ready before
        # Roblox sends its first batch request.
        if self._texture_stripper is not None:
            _precheck_thread = threading.Thread(
                target=self._texture_stripper.precheck_replacements,
                name='ReplacementPrecheck',
                daemon=True,
            )
            _precheck_thread.start()

        # ── Run until the server is stopped ──────────────────────────────
        try:
            await self._proxy._server.serve_forever()
        except (asyncio.CancelledError, Exception):
            pass  # Normal shutdown path
        finally:
            # Ensure hosts file is cleaned up even if stop() wasn't called
            if self._hosts_installed:
                self._stop_watchdog()
                _remove_hosts_entries(set(INTERCEPT_HOSTS))
                self._hosts_installed = False
                _flush_dns()
                _cancel_hosts_cleanup_on_reboot()
                try:
                    _PROXY_OWNER_PID_FILE.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                await self._proxy.stop()
            except Exception:
                pass
            self._running = False
            self._loop = None
