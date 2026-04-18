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
import csv
import ctypes
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import winreg
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Set

from ..utils import (
    LOCAL_APPDATA,
    PROXY_CA_DIR,
    PROXY_PORT,
    ROBLOX_PROCESS,
    ROBLOX_STUDIO_PROCESS,
    STORAGE_DB,
    STORAGE_DB_GDK,
    log_buffer,
    terminate_roblox,
    wait_for_roblox_exit,
    wait_for_roblox_window,
    delete_cache,
    run_in_thread,
)
from .addons import CacheScraper, TextureStripper
from .server import FleasionProxy, INTERCEPT_HOSTS
from ..cache.cache_manager import CacheManager
from ..utils.certs import generate_ca, generate_host_cert, get_ca_pem
from ..utils.roblox_dirs import load_saved_roblox_dirs
from ..utils.windows import get_roblox_player_exe_path, get_roblox_studio_exe_path, launch_as_standard_user

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
  <Actions>
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



def _is_routable_public_ip(ip: str) -> bool:
    """Return True only if *ip* is a publicly routable IPv4 address.

    Rejects everything that cannot legitimately be a Roblox CDN address:
      - Loopback          127.0.0.0/8
      - Private (RFC1918) 10/8, 172.16/12, 192.168/16
      - Link-local        169.254.0.0/16
      - CGNAT / WARP vNIC 100.64.0.0/10  (includes WARP's 100.96.x.x range)
      - Multicast         224.0.0.0/4
      - Reserved / bogon  0.0.0.0/8, 240.0.0.0/4, 255.255.255.255, etc.
    """
    import ipaddress as _ipaddress
    try:
        addr = _ipaddress.IPv4Address(ip)
        return not (
            addr.is_loopback
            or addr.is_private
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        )
    except ValueError:
        return False


def _dns_query_udp(hostname: str, server: str, port: int = 53, timeout: float = 3.0) -> list:
    """Send a raw DNS A-record query over UDP to *server*, bypassing the OS
    resolver stack entirely.

    This sidesteps both the Windows DNS Client service cache AND VPN client
    caches (e.g. Cloudflare WARP's WFP-level resolver) that may still be
    serving stale 127.0.0.1 entries from a previous Fleasion crash, even after
    we have already removed the hosts file entries and called
    DnsFlushResolverCache().

    Returns a list of IPv4 address strings, or [] on any failure.

    DNS wire-format references: RFC 1035 §4.1
    """
    import socket as _socket
    import struct as _struct

    # --- Build a minimal DNS query packet ---
    # Transaction ID: arbitrary 16-bit value
    txid = 0x4649  # 'FI' — easy to spot in Wireshark
    # Flags: standard query, recursion desired
    flags = 0x0100
    # 1 question, 0 answer/authority/additional RRs
    header = _struct.pack('!HHHHHH', txid, flags, 1, 0, 0, 0)

    # Encode hostname as a sequence of length-prefixed labels
    labels = b''
    for part in hostname.encode('ascii').split(b'.'):
        labels += _struct.pack('B', len(part)) + part
    labels += b'\x00'  # root label

    # QTYPE=A (1), QCLASS=IN (1)
    question = labels + _struct.pack('!HH', 1, 1)
    packet = header + question

    # --- Send and receive ---
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (server, port))
        response, _ = sock.recvfrom(4096)
    except OSError:
        return []
    finally:
        sock.close()

    if len(response) < 12:
        return []

    # Parse response header
    r_txid, r_flags, r_qdcount, r_ancount, _, _ = _struct.unpack('!HHHHHH', response[:12])
    if r_txid != txid or r_ancount == 0:
        return []

    # Skip the question section (mirror of what we sent; just skip over it)
    pos = 12
    for _ in range(r_qdcount):
        while pos < len(response):
            length = response[pos]
            pos += 1
            if length == 0:
                break
            if length & 0xC0 == 0xC0:  # pointer
                pos += 1
                break
            pos += length
        pos += 4  # QTYPE + QCLASS

    # Parse answer RRs — collect all A records
    ips = []
    for _ in range(r_ancount):
        if pos >= len(response):
            break
        # Name field (may be a pointer)
        if response[pos] & 0xC0 == 0xC0:
            pos += 2
        else:
            while pos < len(response) and response[pos] != 0:
                pos += response[pos] + 1
            pos += 1
        if pos + 10 > len(response):
            break
        rtype, _, _, rdlength = _struct.unpack('!HHIH', response[pos:pos + 10])
        pos += 10
        if rtype == 1 and rdlength == 4:  # A record
            ip = '.'.join(str(b) for b in response[pos:pos + 4])
            if _is_routable_public_ip(ip):
                ips.append(ip)
        pos += rdlength

    return ips


_DNS_FALLBACK_SERVERS = ['8.8.8.8', '1.1.1.1', '1.0.0.1']


def _resolve_real_ips(hosts: set) -> dict:
    """Resolve real IPs for each host BEFORE we write hosts file entries.

    We MUST do this first - once hosts file points them to 127.0.0.1, any
    subsequent socket.getaddrinfo() call would return 127.0.0.1, causing our
    upstream connections to loop back to ourselves.

    Primary strategy: socket.getaddrinfo() (uses OS resolver — fast, respects
    IPv6 and system network config).

    Fallback strategy: raw UDP DNS query to well-known public resolvers,
    bypassing the OS resolver stack and any VPN client caches (e.g. Cloudflare
    WARP's WFP-level resolver) that may still hold stale 127.0.0.1 mappings
    from a previous crashed Fleasion session even after the hosts file has been
    cleaned and DnsFlushResolverCache() has been called.
    """
    import socket
    real_ips: dict = {}
    for host in sorted(hosts):
        ips = []

        # --- Primary: OS resolver ---
        try:
            results = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
            ips = []
            for result in results:
                ip = result[4][0]
                if isinstance(ip, str) and _is_routable_public_ip(ip):
                    ips.append(ip)
        except Exception as exc:
            log_buffer.log('Proxy', f'DNS resolve failed for {host} (OS resolver): {exc}')

        if ips:
            real_ips[host] = ips
            log_buffer.log('Proxy', f'Resolved {host} -> {ips[0]}')
            continue

        # --- Fallback: raw UDP DNS, bypassing OS resolver + VPN cache ---
        # Fires when the OS resolver returns no publicly routable IPs — typically
        # caused by a VPN (e.g. Cloudflare WARP) whose internal DNS cache holds
        # stale loopback/private entries from a previous crashed Fleasion session,
        # or a VPN that routes Roblox to its own private/CGNAT address space.
        # DnsFlushResolverCache() does not flush the VPN client's own in-process
        # cache; a direct UDP query to a public resolver bypasses it entirely.
        log_buffer.log(
            'Proxy',
            f'OS resolver returned no routable IPs for {host} — '
            'trying direct UDP DNS (VPN cache bypass)…',
        )
        for dns_server in _DNS_FALLBACK_SERVERS:
            try:
                fallback_ips = _dns_query_udp(host, dns_server)
                if fallback_ips:
                    real_ips[host] = fallback_ips
                    log_buffer.log(
                        'Proxy',
                        f'Resolved {host} -> {fallback_ips[0]} '
                        f'(via direct UDP to {dns_server} — VPN cache bypass)',
                    )
                    break
            except Exception as exc:
                log_buffer.log('Proxy', f'Direct UDP DNS to {dns_server} failed for {host}: {exc}')
        else:
            log_buffer.log(
                'Proxy',
                f'Warning: could not resolve real IPs for {host} via any method. '
                'If you are using a VPN or firewall that blocks outbound UDP port 53, '
                'try temporarily disabling it before starting Fleasion.',
            )

    return real_ips



def _flush_dns() -> None:
    """Flush Windows DNS client cache so the hosts file changes take effect immediately.

    Calls ``DnsFlushResolverCache`` in *dnsapi.dll* directly via ctypes first.
    This is an in-process call — no subprocess is spawned — so security software
    that blocks child-process creation (e.g. Webroot SecureAnywhere / WRSVC)
    cannot interfere with it.  Falls back to ``ipconfig /flushdns`` only if the
    DLL call itself raises an exception (e.g. on a non-Windows build environment).
    """
    # Primary: in-process DLL call — fast, no subprocess, immune to AV process blocks.
    try:
        ctypes.windll.LoadLibrary('dnsapi.dll').DnsFlushResolverCache()
        log_buffer.log('Hosts', 'DNS cache flushed')
        return
    except Exception as exc:
        log_buffer.log('Hosts', f'DnsFlushResolverCache failed, falling back to ipconfig: {exc}')

    # Fallback: subprocess (may be blocked or slow under security software).
    try:
        subprocess.run(
            ['ipconfig', '/flushdns'],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
        log_buffer.log('Hosts', 'DNS cache flushed (via ipconfig fallback)')
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


def _extract_exe_from_command(command: str) -> Optional[Path]:
    """Extract an executable path from a registry shell/open command string."""
    if not command:
        return None

    cmd = command.replace('\x00', '').strip()
    if not cmd:
        return None

    if cmd.startswith('"'):
        end_quote = cmd.find('"', 1)
        if end_quote <= 1:
            return None
        exe_path = cmd[1:end_quote]
    else:
        exe_path = cmd.split()[0]

    if not exe_path:
        return None
    return Path(exe_path)


def _get_process_name_from_pid(pid: int) -> str:
    """Resolve a PID to process name using tasklist."""
    try:
        result = subprocess.run(
            ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
    except Exception:
        return 'Unknown'

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith('INFO:'):
            return 'Unknown'
        try:
            row = next(csv.reader([line]))
        except Exception:
            continue
        if row and row[0]:
            return row[0].strip()
    return 'Unknown'


def _list_port_listeners_powershell(port: int) -> list[dict]:
    """Return listening process info for a TCP port via Get-NetTCPConnection."""
    ps_cmd = (
        f"$rows=Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | "
        "ForEach-Object { "
        "$p=Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; "
        "[PSCustomObject]@{ "
        "LocalAddress=$_.LocalAddress; "
        "PID=$_.OwningProcess; "
        "ProcessName=$(if($p){$p.ProcessName}else{'Unknown'}) "
        "} "
        "}; "
        "if($rows){$rows | ConvertTo-Json -Compress}"
    )
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps_cmd],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=6,
        )
    except Exception:
        return []

    if result.returncode != 0:
        return []

    payload = (result.stdout or '').strip()
    if not payload:
        return []

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return []

    rows = parsed if isinstance(parsed, list) else [parsed]
    listeners: list[dict] = []
    for row in rows:
        try:
            pid = int(row.get('PID', 0))
        except Exception:
            pid = 0
        if pid <= 0:
            continue

        process_name = str(row.get('ProcessName') or 'Unknown').strip() or 'Unknown'
        local_address = str(row.get('LocalAddress') or '0.0.0.0').strip() or '0.0.0.0'
        listeners.append(
            {
                'pid': pid,
                'process_name': process_name,
                'local_address': local_address,
            }
        )
    return listeners


def _list_port_listeners_netstat(port: int) -> list[dict]:
    """Fallback listener lookup using netstat + tasklist."""
    try:
        result = subprocess.run(
            ['netstat', '-aon', '-p', 'tcp'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=6,
        )
    except Exception:
        return []

    listeners: list[dict] = []
    suffix = f':{port}'
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue

        proto, local_addr, _, state, pid_text = parts[:5]
        if proto.upper() != 'TCP' or state.upper() != 'LISTENING':
            continue
        if not (local_addr.endswith(suffix) or local_addr.endswith(f']{suffix}')):
            continue

        try:
            pid = int(pid_text)
        except ValueError:
            continue

        local_address = local_addr
        if local_addr.startswith('['):
            # [::]:443 -> [::]
            local_address = local_addr.rsplit(']:', 1)[0] + ']'
        elif ':' in local_addr:
            # 0.0.0.0:443 -> 0.0.0.0
            local_address = local_addr.rsplit(':', 1)[0]

        listeners.append(
            {
                'pid': pid,
                'process_name': _get_process_name_from_pid(pid),
                'local_address': local_address,
            }
        )

    return listeners


def _list_port_listeners(port: int) -> list[dict]:
    """Return unique listener records for a TCP port."""
    listeners = _list_port_listeners_powershell(port)
    if not listeners:
        listeners = _list_port_listeners_netstat(port)

    unique: list[dict] = []
    seen: set[tuple[int, str, str]] = set()
    for entry in listeners:
        pid = int(entry.get('pid', 0) or 0)
        process_name = str(entry.get('process_name') or 'Unknown').strip() or 'Unknown'
        local_address = str(entry.get('local_address') or '0.0.0.0').strip() or '0.0.0.0'
        key = (pid, process_name.lower(), local_address)
        if key in seen:
            continue
        seen.add(key)
        unique.append(
            {
                'pid': pid,
                'process_name': process_name,
                'local_address': local_address,
            }
        )

    unique.sort(key=lambda x: (x['pid'], x['process_name'].lower(), x['local_address']))
    return unique


# ---------------------------------------------------------------------------
# Hosts file management
# ---------------------------------------------------------------------------

_HOSTS_WRITE_RETRIES = 8
_HOSTS_WRITE_DELAY   = 0.25  # seconds between direct-write retries


def _write_hosts_file(content: str) -> None:
    """Write *content* to the system hosts file, working around security
    software (e.g. Webroot SecureAnywhere / WRSVC) that intermittently or
    persistently locks the hosts file against direct writes.

    Strategy (applied in order):
      0. If the hosts file has the read-only attribute set, clear it first.
      1. Retry direct write up to *_HOSTS_WRITE_RETRIES* times with a short
         delay.  This handles brief/scan-time locks held by AV drivers.
      2. Write to a temporary file in the same directory, then use
         ``os.replace()`` (an atomic rename).  Rename is a directory-entry
         operation that bypasses file-content write filters used by some
         security products.

    Raises ``OSError`` if both strategies are exhausted.
    """
    # --- Strategy 0: clear read-only attribute if present ---
    if HOSTS_FILE.exists():
        import stat
        current_mode = HOSTS_FILE.stat().st_mode
        if not (current_mode & stat.S_IWRITE):
            try:
                HOSTS_FILE.chmod(current_mode | stat.S_IWRITE)
                log_buffer.log('Hosts', 'Hosts file was read-only — cleared read-only attribute')
            except OSError as exc:
                log_buffer.log('Hosts', f'Failed to clear read-only attribute on hosts file: {exc}')

    last_exc: OSError | None = None

    # --- Strategy 1: direct write with retries ---
    for attempt in range(_HOSTS_WRITE_RETRIES):
        try:
            HOSTS_FILE.write_text(content, encoding='utf-8')
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < _HOSTS_WRITE_RETRIES - 1:
                log_buffer.log(
                    'Hosts',
                    f'Hosts write blocked (attempt {attempt + 1}/{_HOSTS_WRITE_RETRIES}), '
                    f'retrying in {_HOSTS_WRITE_DELAY * 1000:.0f} ms '
                    f'(security software may be holding a lock)…',
                )
                time.sleep(_HOSTS_WRITE_DELAY)
        except OSError as exc:
            raise  # non-permission errors are not retryable

    # --- Strategy 2: temp-file + atomic rename ---
    log_buffer.log(
        'Hosts',
        f'Direct write failed after {_HOSTS_WRITE_RETRIES} attempts — '
        'attempting atomic rename workaround for security software lock…',
    )
    try:
        hosts_dir = HOSTS_FILE.parent
        fd, tmp_path = tempfile.mkstemp(dir=hosts_dir, prefix='.fleasion_hosts_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                fh.write(content)
            os.replace(tmp_path, HOSTS_FILE)  # atomic on Windows (MoveFileExW)
            log_buffer.log('Hosts', 'Hosts file updated via atomic rename (security software workaround)')
            return
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise PermissionError(
            f'Cannot write hosts file — all strategies exhausted. '
            f'If Webroot or another security product is installed, open its settings '
            f'and try to disable any setting relating to protecting the hosts file. '
            f'Last direct-write error: {last_exc}; rename error: {exc}'
        ) from exc


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
            _write_hosts_file(existing)
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
        _write_hosts_file(new_content)
        for host in sorted(hosts):
            log_buffer.log('Hosts', f'Added redirect: {host} -> 127.0.0.1')
        return True
    except PermissionError as exc:
        log_buffer.log('Hosts', f'Permission denied writing hosts file: {exc}')
        return False
    except OSError as exc:
        log_buffer.log('Hosts', f'Failed to write hosts file: {exc}')
        return False


def _remove_hosts_entries(hosts: Set[str]) -> bool:
    """Remove any hosts file entries we previously added.

    Returns True if the hosts file is clean (entries removed or were already
    absent).  Returns False if the write failed — callers must NOT cancel the
    reboot guard in that case, so the next boot still cleans up automatically.
    """
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return True  # Can't read — assume nothing to clean, not a write failure

    lines = existing.splitlines(keepends=True)
    filtered = [
        line for line in lines
        if _HOSTS_MARKER not in line and not any(
            f'127.0.0.1 {h}' in line for h in hosts
        )
    ]

    if len(filtered) == len(lines):
        return True  # Nothing to remove — already clean

    try:
        _write_hosts_file(''.join(filtered))
        log_buffer.log('Hosts', 'Removed proxy hosts entries')
        return True
    except OSError as exc:
        log_buffer.log('Hosts', f'Failed to clean hosts file: {exc}')
        return False


# ---------------------------------------------------------------------------
# Roblox CA installation
# ---------------------------------------------------------------------------

def _find_roblox_dirs() -> list:
    """Locate every RobloxPlayerBeta.exe and RobloxStudioBeta.exe installation.

    Methods used (combined):
      1. Main Registry   — HKCU\\Software (two levels) for REG_SZ "PlayerPath"/"StudioPath"
      2. MS Store        — C:\\XboxGames\\Roblox up to two layers deep
      3. Active Player   — HKCU\\...\\roblox-player\\open\\command (Default)
      4. Program Files   — C:\\Program Files (x86)\\Roblox\\Versions up to two layers deep
      5. Regular Roblox  — %LocalAppData%\\Roblox\\Versions one layer deep
      6. Active Studio   — HKCU\\...\\roblox-studio\\open\\command (Default)
      7. Running Process — currently running RobloxPlayerBeta/RobloxStudioBeta path
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
        """Return all subdirs up to max_depth layers under root that contain RobloxPlayerBeta.exe or RobloxStudioBeta.exe."""
        results: list = []

        def _has_roblox_exe(path: Path) -> bool:
            return (
                os.path.isfile(os.path.join(path, ROBLOX_PROCESS))
                or os.path.isfile(os.path.join(path, ROBLOX_STUDIO_PROCESS))
            )

        if root.is_dir() and _has_roblox_exe(root):
            results.append(root)

        def _recurse(path: Path, depth: int) -> None:
            try:
                for entry in os.scandir(path):
                    if not entry.is_dir():
                        continue
                    entry_path = Path(entry.path)
                    if _has_roblox_exe(entry_path):
                        results.append(entry_path)
                    if depth < max_depth:
                        _recurse(entry_path, depth + 1)
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
        for value_name, process_name in (('PlayerPath', ROBLOX_PROCESS), ('StudioPath', ROBLOX_STUDIO_PROCESS)):
            try:
                val, rtype = winreg.QueryValueEx(key, value_name)
            except OSError:
                continue
            if rtype != winreg.REG_SZ or not val:
                continue
            val = val.replace('\x00', '').strip()
            if not val:
                continue
            p = Path(val)
            # Path may occasionally point at the exe itself rather than the dir
            if p.name.lower() == process_name.lower():
                p = p.parent
            if os.path.isfile(os.path.join(str(p), process_name)):
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
                            except (OSError, ValueError):
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
                    exe_path = _extract_exe_from_command(cmd)
                    if exe_path is not None:
                        exe_dir = exe_path.parent
                        for d in _scan_for_exe(exe_dir, 2):
                            active_found += 1
                            _add(d)
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    log_buffer.log('Certificate', f'  Active Roblox (registry): {int((time.perf_counter() - t) * 1000)} ms ({active_found} found)')

    # ── 4. Program Files (x86) Roblox ────────────────────────────────────
    t = time.perf_counter()
    program_files_found = 0
    for d in _scan_for_exe(Path(r'C:\Program Files (x86)\Roblox\Versions'), 2):
        program_files_found += 1
        _add(d)
    log_buffer.log('Certificate', f'  Program Files (x86) Roblox\\Versions: {int((time.perf_counter() - t) * 1000)} ms ({program_files_found} found)')

    # ── 5. Regular Roblox ────────────────────────────────────────────────
    # %LocalAppData%\Roblox\Versions — one layer down.
    t = time.perf_counter()
    roblox_found = 0
    for d in _scan_for_exe(LOCAL_APPDATA / 'Roblox' / 'Versions', 1):
        roblox_found += 1
        _add(d)
    log_buffer.log('Certificate', f'  AppData Roblox\\Versions: {int((time.perf_counter() - t) * 1000)} ms ({roblox_found} found)')

    # ── 6. Active Studio ─────────────────────────────────────────────────
    # Read HKCU\...\roblox-studio\shell\open\command (Default); parse the exe
    # path and search up to two layers under its parent directory.
    t = time.perf_counter()
    studio_found = 0
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'SOFTWARE\Classes\roblox-studio\shell\open\command',
        ) as key:
            try:
                cmd, rtype = winreg.QueryValueEx(key, '')
                if rtype == winreg.REG_SZ and cmd:
                    exe_path = _extract_exe_from_command(cmd)
                    if exe_path is not None:
                        exe_dir = exe_path.parent
                        for d in _scan_for_exe(exe_dir, 2):
                            studio_found += 1
                            _add(d)
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    log_buffer.log('Certificate', f'  Active Studio (registry): {int((time.perf_counter() - t) * 1000)} ms ({studio_found} found)')

    # ── 7. Running process install paths ─────────────────────────────────
    t = time.perf_counter()
    running_found = 0
    for running_exe in (get_roblox_player_exe_path(), get_roblox_studio_exe_path()):
        if running_exe is None:
            continue
        if _add(running_exe.parent):
            running_found += 1
    log_buffer.log('Certificate', f'  Running Roblox process path: {int((time.perf_counter() - t) * 1000)} ms ({running_found} found)')

    for cached_dir in load_saved_roblox_dirs():
        _add(cached_dir)

    return found


def _install_ca_into_roblox(ca_pem: str) -> None:
    """Append our CA cert to ssl/cacert.pem next to every found Roblox Player/Studio install."""
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
            existing = ca_file.read_text(encoding='utf-8', errors='replace') if ca_file.exists() else ''
            if ca_pem not in existing:
                ca_file.write_text(f'{existing}\n{ca_pem}', encoding='utf-8')
                log_buffer.log('Certificate', f'Installed CA into {d.name}')
            else:
                log_buffer.log('Certificate', f'CA already installed in {d.name}')
        except (PermissionError, OSError, UnicodeDecodeError) as exc:
            log_buffer.log('Certificate', f'Failed to write CA for {d.name}: {exc}')


def check_and_patch_running_roblox_ca(exe_path: 'Path') -> bool:
    """Check if the currently running Roblox instance has our CA in its cacert.pem.

    Called when RobloxPlayerBeta.exe is detected launching at runtime.
    If the cert is absent it is injected immediately and an alert is logged.

    Returns True if the cert was missing and has been injected (Roblox needs a
    restart).  Returns False if already patched or the CA has not been generated.
    """
    ca_cert_path = PROXY_CA_DIR / 'ca.crt'
    if not ca_cert_path.exists():
        return False  # CA not generated yet – nothing to patch

    ca_pem = get_ca_pem(ca_cert_path)
    roblox_dir = exe_path.parent
    ssl_dir = roblox_dir / 'ssl'
    ca_file = ssl_dir / 'cacert.pem'

    try:
        existing = ca_file.read_text(encoding='utf-8', errors='replace') if ca_file.exists() else ''
    except OSError:
        existing = ''

    if ca_pem in existing:
        return False  # Already patched – nothing to do

    log_buffer.log(
        'Certificate',
        f'[ALERT] {exe_path.name} does not have a modified '
        'cacert.pem! It has been injected, you may need to relaunch it.',
    )
    try:
        ssl_dir.mkdir(exist_ok=True)
        ca_file.write_text(f'{existing}\n{ca_pem}', encoding='utf-8')
        log_buffer.log('Certificate', f'CA injected into running Roblox instance: {roblox_dir.name}')
    except (PermissionError, OSError) as exc:
        log_buffer.log('Certificate', f'Failed to inject CA into running Roblox instance: {exc}')
    return True


# ---------------------------------------------------------------------------
# ProxyMaster
# ---------------------------------------------------------------------------

class ProxyMaster:
    """Manages the Fleasion proxy lifecycle."""

    def __init__(self, config_manager, on_proxy_start_error: Optional[Callable[[str, dict], None]] = None) -> None:
        self.config_manager = config_manager
        self.cache_manager = CacheManager(config_manager)
        self._on_proxy_start_error = on_proxy_start_error

        # Singleton addon instances - GUI holds references to these directly
        self.cache_scraper = CacheScraper(self.cache_manager)
        self.cache_scraper.set_enabled(False)
        
        # Wire scraper into cache_manager for private asset downloads
        self.cache_manager.set_scraper(self.cache_scraper)
        
        self._texture_stripper: Optional[TextureStripper] = None

        self._proxy: Optional[FleasionProxy] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._hosts_installed: bool = False
        self._watchdog_stop: Optional[threading.Event] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._module_interceptors: list = []

    @property
    def is_running(self) -> bool:
        return self._running

    def register_module_interceptor(self, module) -> None:
        """Register a module whose request()/response() methods are called for gamejoin traffic."""
        if module not in self._module_interceptors:
            self._module_interceptors.append(module)
        if self._proxy is not None:
            self._proxy.set_module_interceptors(self._module_interceptors)

    def _emit_proxy_start_error(self, code: str, details: dict) -> None:
        """Forward startup failures to the app layer for user-facing dialogs."""
        if self._on_proxy_start_error is None:
            return
        try:
            self._on_proxy_start_error(code, details)
        except Exception as exc:
            log_buffer.log('Error', f'Failed to dispatch proxy startup error callback: {exc}')

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

    def refresh_and_restart_roblox(self, exe_path: Path) -> None:
        """Called when Roblox launches without our CA cert.

        Runs cert injection and IP/hosts refresh in parallel (two threads), then
        kills Roblox and restarts it so the new cert and fresh hosts entries take
        effect immediately.
        """
        # Quick-check: cert already present → nothing to do
        ca_cert_path = PROXY_CA_DIR / 'ca.crt'
        if not ca_cert_path.exists():
            return
        ca_pem = get_ca_pem(ca_cert_path)
        ca_file = exe_path.parent / 'ssl' / 'cacert.pem'
        try:
            existing = ca_file.read_text(encoding='utf-8', errors='replace') if ca_file.exists() else ''
        except OSError:
            existing = ''
        if ca_pem in existing:
            return  # Already patched — no restart needed

        log_buffer.log('Certificate', 'Roblox missing CA cert — refreshing hosts and restarting...')

        def _patch_cert() -> None:
            check_and_patch_running_roblox_ca(exe_path)

        def _refresh_ips() -> None:
            if not self._hosts_installed:
                return
            # Remove entries temporarily so getaddrinfo() sees real IPs again
            _remove_hosts_entries(set(INTERCEPT_HOSTS))
            _flush_dns()
            new_ips = _resolve_real_ips(set(INTERCEPT_HOSTS))
            # Re-install entries pointing back to our proxy.
            # Acquire the lock before re-adding to guard against a race with
            # stop(): if stop() ran while we were resolving IPs it will have
            # set _hosts_installed = False under this same lock, cancelled all
            # cleanup guards, and returned.  Adding entries at that point would
            # leave the hosts file dirty with no mechanism to clean it up.
            with self._lock:
                if not self._hosts_installed:
                    # stop() already ran — do not re-add entries.
                    return
                _add_hosts_entries(set(INTERCEPT_HOSTS))
                
            _flush_dns()
            # Update running proxy and scraper with fresh upstream IPs
            if self._proxy is not None and new_ips:
                self._proxy._upstream_ips = new_ips
            scraper_ips = {host: ips[0] for host, ips in new_ips.items() if ips}
            if scraper_ips:
                self.cache_scraper.set_real_ips(scraper_ips)

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='fleasion-cert-refresh') as pool:
            f_cert = pool.submit(_patch_cert)
            f_ips = pool.submit(_refresh_ips)
        # Both futures are done after the with block (shutdown waits for them)

        for label, fut in (('cert patch', f_cert), ('IP refresh', f_ips)):
            if fut.exception():
                log_buffer.log('Certificate', f'Error during {label}: {fut.exception()}')

        log_buffer.log('Certificate', 'Cert injected and IPs refreshed — waiting for Roblox to finish launching...')
        if not wait_for_roblox_window(timeout=60.0):
            log_buffer.log('Certificate', 'Warning: Roblox window did not appear within 60 s — restarting anyway')
        time.sleep(2)

        log_buffer.log('Certificate', 'Restarting Roblox...')
        terminate_roblox()
        if not wait_for_roblox_exit(timeout=15.0):
            log_buffer.log('Certificate', 'Warning: Roblox did not exit within 15 s — skipping restart')
            return

        try:
            if not launch_as_standard_user(exe_path):
                raise OSError('launch failed')
            log_buffer.log('Certificate', f'Roblox restarted: {exe_path.name}')
        except OSError as exc:
            log_buffer.log('Certificate', f'Failed to restart Roblox: {exc}')

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
                hosts_cleaned = _remove_hosts_entries(set(INTERCEPT_HOSTS))
                self._hosts_installed = False
                _flush_dns()  # Clear stale 127.0.0.1 cache so new connections stop coming in
                # Only cancel the reboot guard if the hosts file was actually cleaned.
                # If cleanup failed, the PendingFileRenameOperations entry must remain
                # so the next reboot still removes our entries automatically.
                if hosts_cleaned:
                    _cancel_hosts_cleanup_on_reboot()
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
        # ── Optional cache clear on launch ───────────────────────────────
        if self.config_manager.clear_cache_on_launch:
            log_buffer.log('Cleanup', 'Clear cache on launch enabled - deleting cache')

            def _delete_and_log():
                messages = delete_cache()
                for msg in messages:
                    log_buffer.log('Cache', msg)

            run_in_thread(_delete_and_log)()
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
            # would return 127.0.0.1 instead of real CDN IPs, and upstream
            # connections would fail with WinError 1225.
            if not _remove_hosts_entries(set(INTERCEPT_HOSTS)):
                log_buffer.log('Error',
                    'Failed to remove stale proxy hosts entries — real CDN IPs '
                    'cannot be resolved safely.  Aborting proxy start. '
                    'If the problem persists, manually remove "# Fleasion proxy entry" '
                    'lines from %SystemRoot%\\System32\\drivers\\etc\\hosts and restart.')
                self._running = False
                return
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
        self._proxy.set_module_interceptors(self._module_interceptors)
        try:
            await self._proxy.start()
        except OSError as exc:
            err_text = str(exc).lower()
            if (
                exc.errno in (10013, 10048)
                or 'access' in err_text
                or 'address already in use' in err_text
                or 'only one usage of each socket address' in err_text
                or (str(PROXY_PORT) in err_text and 'bind' in err_text)
            ):
                owners = _list_port_listeners(PROXY_PORT)
                log_buffer.log('Error', (
                    f'Cannot bind port {PROXY_PORT}: another process is already listening. '
                    'Ensure no other process is using this port and run as Administrator.'
                ))
                if owners:
                    owners_summary = '; '.join(
                        f"{owner['process_name']} (PID {owner['pid']}) on {owner['local_address']}:{PROXY_PORT}"
                        for owner in owners
                    )
                    log_buffer.log('Error', f'Port {PROXY_PORT} listeners: {owners_summary}')
                self._emit_proxy_start_error(
                    'port_bind_failed',
                    {
                        'port': PROXY_PORT,
                        'owners': owners,
                    },
                )
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
            # Always pre-create rig-converted copies (auto-convert is always enabled)
            threading.Thread(
                target=self._texture_stripper.precheck_anim_rigs,
                name='AnimRigPrecheck',
                daemon=True,
            ).start()

        # ── Run until the server is stopped ──────────────────────────────
        try:
            server = self._proxy._server
            if server is None:
                return
            await server.serve_forever()
        except (asyncio.CancelledError, Exception):
            pass  # Normal shutdown path
        finally:
            # Ensure hosts file is cleaned up even if stop() wasn't called
            if self._hosts_installed:
                self._stop_watchdog()
                hosts_cleaned = _remove_hosts_entries(set(INTERCEPT_HOSTS))
                self._hosts_installed = False
                _flush_dns()
                if hosts_cleaned:
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
