"""ProxyMaster: manages the lifecycle of the Fleasion proxy.

Interception strategy:
  1. Write hosts file entries pointing assetdelivery.roblox.com and
     Roblox CDN hosts at 127.0.0.1.  Roblox uses libcurl which honours
     the OS hosts file unconditionally (unlike WinINet PAC files).
  2. Run a direct TLS server on 127.0.0.1:443.  Roblox connects directly
     (no HTTP CONNECT tunnel needed) and we present a leaf cert signed by
     our local CA.  Roblox's libcurl validates it against the CA we install
     into each Roblox version's ssl/cacert.pem.
  3. On stop, remove our hosts entries and stop the server.

Privilege requirement:
  Windows runs the proxy elevated. On macOS, a small root LaunchDaemon owns
  port 443 and hosts-file writes while this proxy and the GUI stay unprivileged.

VPN compatibility:
  Loopback (127.0.0.1) traffic is never routed through VPN adapters.
  Only our proxy->CDN upstream connections go through the VPN (correct).
"""

import asyncio
import base64
import hashlib
import csv
import ctypes
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Set

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only module
    winreg = None

from ..utils import (
    LOCAL_APPDATA,
    MACOS_PROXY_BACKEND_PORT,
    PROXY_CA_DIR,
    PROXY_PORT,
    ROBLOX_PROCESS,
    ROBLOX_STUDIO_PROCESS,
    STORAGE_DB,
    STORAGE_DB_GDK,
    format_count,
    log_buffer,
    terminate_roblox,
    wait_for_roblox_exit,
    wait_for_roblox_window,
    delete_cache,
    run_in_thread,
)
from .addons import CacheScraper, TextureStripper, UsernameSpoofer
from .server import (
    BASE_INTERCEPT_HOSTS,
    ASSET_DELIVERY_HOST,
    CDN_HOSTS,
    FleasionProxy,
    GAMEJOIN_HOST,
    INTERCEPT_HOSTS,
    USERNAME_SPOOFER_INTERCEPT_HOSTS,
)
from .upstream import HttpProxyConfig, Socks5ProxyConfig, UpstreamEndpoint, UpstreamMode
from .windows_proxy import WindowsProxyInfo, detect_windows_proxy, detected_http_proxy
from ..cache.cache_manager import CacheManager
from ..utils.certs import generate_ca, generate_host_cert, generate_multi_host_cert, get_ca_pem
from ..utils.roblox_dirs import is_roblox_studio_resource_dir, load_saved_roblox_dirs, save_saved_roblox_dirs
from ..utils.windows import get_roblox_player_exe_path, get_roblox_studio_exe_path, launch_as_standard_user

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == 'win32'
IS_MACOS = sys.platform == 'darwin'
IS_LINUX = sys.platform.startswith('linux')
_ACTIVE_PROXY_CA_DIR = PROXY_CA_DIR

if IS_MACOS or IS_LINUX:
    HOSTS_FILE = Path('/etc/hosts')
    _PLATFORM_TEMP_DIR = Path(tempfile.gettempdir())
else:
    HOSTS_FILE = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'drivers' / 'etc' / 'hosts'
    _PLATFORM_TEMP_DIR = Path(os.environ.get('TEMP', r'C:\Windows\Temp'))
_HOSTS_MARKER = '# Fleasion proxy entry'

# Registry key used by Windows to replace files on next reboot
_PENDING_RENAME_KEY   = r'SYSTEM\CurrentControlSet\Control\Session Manager'
_PENDING_RENAME_VALUE = 'PendingFileRenameOperations'
# Temp file that will replace the hosts file on next boot after a crash
_TEMP_CLEAN_HOSTS = _PLATFORM_TEMP_DIR / 'fleasion_hosts_restore.txt'
# Tracks which elevated Fleasion PID currently owns the proxy/hosts/watchdog.
# Other instances check this on startup to avoid disturbing a live proxy.
_PROXY_OWNER_PID_FILE = _PLATFORM_TEMP_DIR / 'fleasion_proxy_owner.pid'

# ---------------------------------------------------------------------------
# Task-Scheduler watchdog (force-kill guard)
# ---------------------------------------------------------------------------
# When the proxy is running, we maintain a Windows Task Scheduler task that
# fires a short time into the future.  A background thread refreshes the task
# before that deadline so it never actually fires during normal operation.  If the
# process is force-killed (Task Manager, etc.) the task fires soon after and
# restores the hosts file.
#
# StartWhenAvailable is set to FALSE in the task XML.  This means if the
# scheduled time passes while the PC is OFF (power loss, BSOD), the task
# will NEVER fire retroactively on the next boot — the PendingFileRename
# guard handles that case instead.  On the next Fleasion launch we also
# delete any stale watchdog task left from a previous crash.
# ---------------------------------------------------------------------------

_WATCHDOG_TASK_NAME = 'Fleasion-HostsWatchdog'
_WATCHDOG_LOOKAHEAD = 30  # seconds ahead the task is scheduled
_WATCHDOG_INTERVAL  = 10  # seconds between watchdog refreshes
_WATCHDOG_SCHTASKS_TIMEOUT = 20
_WATCHDOG_TASK_XML  = _PLATFORM_TEMP_DIR / 'fleasion_watchdog_task.xml'
_SCHTASKS_EXE = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'schtasks.exe')
_CERTUTIL_EXE = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'certutil.exe')

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
    if not IS_WINDOWS:
        return
    try:
        run_at = datetime.now() + timedelta(seconds=_WATCHDOG_LOOKAHEAD)
        xml = _build_watchdog_xml(run_at)
        _WATCHDOG_TASK_XML.write_text(xml, encoding='utf-16')
        cmd = [
            _SCHTASKS_EXE, '/create', '/TN', _WATCHDOG_TASK_NAME,
            '/XML', str(_WATCHDOG_TASK_XML), '/RU', 'SYSTEM', '/F',
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=_WATCHDOG_SCHTASKS_TIMEOUT,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or b'').decode('utf-8', errors='replace').strip()
            log_buffer.log('Watchdog', f'schtasks returned non-zero ({result.returncode}): {err}')
    except subprocess.TimeoutExpired:
        log_buffer.log(
            'Watchdog',
            f'schtasks timed out after {_WATCHDOG_SCHTASKS_TIMEOUT}s while creating '
            f'{_WATCHDOG_TASK_NAME}; Task Scheduler or security software may be slow/blocking it. '
            f'XML: {_WATCHDOG_TASK_XML}',
        )
    except Exception as exc:
        log_buffer.log('Watchdog', f'Could not upsert watchdog task (non-fatal): {exc}')


def _delete_watchdog_task() -> None:
    """Delete the watchdog task if it exists.  Safe to call even if absent."""
    if not IS_WINDOWS:
        return
    try:
        result = subprocess.run(
            [_SCHTASKS_EXE, '/delete', '/TN', _WATCHDOG_TASK_NAME, '/F'],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=_WATCHDOG_SCHTASKS_TIMEOUT,
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
    """Return True only if *ip* is a publicly routable IP address.

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
        addr = _ipaddress.ip_address(ip)
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


def _dns_query_udp(hostname: str, server: str, port: int = 53, timeout: float = 3.0, qtype: int = 1) -> list:
    """Send a raw DNS A/AAAA-record query over UDP to *server*, bypassing the OS
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

    # QTYPE=A (1) or AAAA (28), QCLASS=IN (1)
    question = labels + _struct.pack('!HH', qtype, 1)
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

    # Parse answer RRs — collect all A/AAAA records
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
        elif rtype == 28 and rdlength == 16:  # AAAA record
            try:
                ip = _socket.inet_ntop(_socket.AF_INET6, response[pos:pos + 16])
            except OSError:
                ip = ''
            if ip and _is_routable_public_ip(ip):
                ips.append(ip)
        pos += rdlength

    return ips


_DNS_FALLBACK_SERVERS = ['8.8.8.8', '1.1.1.1', '1.0.0.1']


def _prefer_ipv4_endpoints(endpoints: list[UpstreamEndpoint]) -> list[UpstreamEndpoint]:
    """Return endpoints ordered like v2.0.1's stable IPv4-first upstream path."""
    return sorted(
        endpoints,
        key=lambda ep: (
            0 if ep.family == socket.AF_INET else 1 if ep.family == socket.AF_INET6 else 2,
            ep.ip or ep.host,
        ),
    )


def _resolve_real_endpoints(hosts: set[str]) -> dict[str, list[UpstreamEndpoint]]:
    """Resolve real upstream endpoints before hosts entries point at localhost.

    We MUST do this first - once hosts file points them to 127.0.0.1, any
    subsequent socket.getaddrinfo() call would return 127.0.0.1, causing our
    upstream connections to loop back to ourselves.

    Primary strategy: socket.getaddrinfo() (uses OS resolver — fast, respects
    system network config). IPv4 endpoints are preferred because v2.0.1 was
    IPv4-only and some user networks expose broken or very slow Roblox IPv6
    routes that produce upstream TLS failures or HTTP 524 responses.

    Fallback strategy: raw UDP DNS query to well-known public resolvers, only
    when the OS resolver produced no routable endpoints. Public DNS can select
    a CDN edge that is wrong for VPN routing, so it is never preferred over the
    OS/VPN resolver.
    """
    import socket
    real_endpoints: dict[str, list[UpstreamEndpoint]] = {}
    for host in sorted(hosts):
        endpoints: list[UpstreamEndpoint] = []
        seen: set[tuple[int, str]] = set()

        # --- Primary: OS resolver ---
        try:
            results = socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for family, _socktype, _proto, _canonname, sockaddr in results:
                if family not in (socket.AF_INET, socket.AF_INET6):
                    continue
                ip = sockaddr[0]
                key = (family, ip)
                if (
                    isinstance(ip, str)
                    and key not in seen
                    and _is_routable_public_ip(ip)
                ):
                    seen.add(key)
                    endpoints.append(UpstreamEndpoint(host=host, ip=ip, family=family))
        except Exception as exc:
            log_buffer.log('Proxy', f'DNS resolve failed for {host} (OS resolver): {exc}')

        if endpoints:
            endpoints = _prefer_ipv4_endpoints(endpoints)
            real_endpoints[host] = endpoints
            log_buffer.log('Proxy', f'Resolved {host} -> {endpoints[0].ip} (OS resolver)')
            continue

        # --- Fallback: raw UDP DNS, last resort only ---
        log_buffer.log(
            'Proxy',
            f'OS resolver returned no routable endpoints for {host}; trying public DNS as a last resort.',
        )
        for dns_server in _DNS_FALLBACK_SERVERS:
            try:
                fallback: list[UpstreamEndpoint] = []
                for family, qtype in ((socket.AF_INET, 1), (socket.AF_INET6, 28)):
                    for ip in _dns_query_udp(host, dns_server, qtype=qtype):
                        key = (family, ip)
                        if key in seen:
                            continue
                        seen.add(key)
                        fallback.append(UpstreamEndpoint(host=host, ip=ip, family=family))
                if fallback:
                    fallback = _prefer_ipv4_endpoints(fallback)
                    real_endpoints[host] = fallback
                    log_buffer.log(
                        'Proxy',
                        f'Public DNS fallback used for {host}. This may be incompatible with VPN routing.',
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

    return real_endpoints


def _resolve_real_ips(hosts: set[str]) -> dict[str, list[str]]:
    """Compatibility wrapper for older code that expects string IP lists."""
    endpoints = _resolve_real_endpoints(hosts)
    return {
        host: [ep.ip for ep in eps if ep.ip]
        for host, eps in endpoints.items()
    }


def _log_upstream_ip_coverage(hosts: set[str], real_endpoints: dict) -> None:
    for host in sorted(hosts):
        endpoints = real_endpoints.get(host) or []
        ips = [
            (ep.ip if isinstance(ep, UpstreamEndpoint) else str(ep))
            for ep in endpoints
            if (ep.ip if isinstance(ep, UpstreamEndpoint) else str(ep))
        ]
        if ips:
            log_buffer.log('Proxy', f'Upstream IP coverage: {host} -> {", ".join(ips)}')
        else:
            log_buffer.log('Proxy', f'Upstream IP coverage: {host} -> NO ROUTABLE IPS')


def _first_endpoint_ips(real_endpoints: dict[str, list[UpstreamEndpoint]]) -> dict[str, str]:
    return {
        host: eps[0].ip
        for host, eps in real_endpoints.items()
        if eps and eps[0].ip
    }


def _log_system_proxy_info(info: WindowsProxyInfo, system_proxy: Optional[HttpProxyConfig]) -> None:
    if IS_MACOS:
        http_enabled = 'yes' if info.macos_http_enabled else 'no'
        https_enabled = 'yes' if info.macos_https_enabled else 'no'
        log_buffer.log(
            'ProxyDiag',
            f'macOS HTTP proxy enabled: {http_enabled} server={info.macos_http_proxy_server or "none"}',
        )
        log_buffer.log(
            'ProxyDiag',
            f'macOS HTTPS proxy enabled: {https_enabled} server={info.macos_https_proxy_server or "none"}',
        )
        if info.macos_auto_config_url:
            log_buffer.log(
                'ProxyDiag',
                f'PAC detected: {info.macos_auto_config_url} unsupported for automatic upstream mode',
            )
        if system_proxy is not None:
            log_buffer.log(
                'ProxyDiag',
                f'System HTTP CONNECT candidate: {system_proxy.host}:{system_proxy.port}',
            )
        return

    wininet_enabled = 'yes' if info.wininet_enabled else 'no'
    log_buffer.log(
        'ProxyDiag',
        f'WinINET proxy enabled: {wininet_enabled} server={info.wininet_proxy_server or "none"}',
    )
    log_buffer.log('ProxyDiag', f'WinHTTP proxy: {info.winhttp_proxy_server or "none"}')
    if info.wininet_auto_config_url:
        log_buffer.log(
            'ProxyDiag',
            f'PAC detected: {info.wininet_auto_config_url} unsupported for automatic upstream mode',
        )
    if system_proxy is not None:
        log_buffer.log(
            'ProxyDiag',
            f'System HTTP CONNECT candidate: {system_proxy.host}:{system_proxy.port}',
        )


def _manual_http_proxy_from_settings(config_manager) -> Optional[HttpProxyConfig]:
    host = str(getattr(config_manager, 'upstream_http_connect_host', '') or '').strip()
    port = int(getattr(config_manager, 'upstream_http_connect_port', 0) or 0)
    if not host or port <= 0:
        return None
    username = str(getattr(config_manager, 'upstream_http_connect_username', '') or '') or None
    password = str(getattr(config_manager, 'upstream_http_connect_password', '') or '') or None
    return HttpProxyConfig(host=host, port=port, username=username, password=password)


def _manual_socks5_proxy_from_settings(config_manager) -> Optional[Socks5ProxyConfig]:
    host = str(getattr(config_manager, 'upstream_socks5_host', '') or '').strip()
    port = int(getattr(config_manager, 'upstream_socks5_port', 0) or 0)
    if not host or port <= 0:
        return None
    username = str(getattr(config_manager, 'upstream_socks5_username', '') or '') or None
    password = str(getattr(config_manager, 'upstream_socks5_password', '') or '') or None
    return Socks5ProxyConfig(host=host, port=port, username=username, password=password)


def _connect_tls_for_self_test(host: str | None, ca_cert_path: Path, port: int) -> dict:
    ctx = ssl.create_default_context(cafile=str(ca_cert_path))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if host is None:
        ctx.check_hostname = False
    with socket.create_connection(('127.0.0.1', port), timeout=5.0) as raw_sock:
        with ctx.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
            cert = tls_sock.getpeercert()
            return cert if isinstance(cert, dict) else {}


def _cert_dict_san_hosts(cert: dict) -> set[str]:
    names: set[str] = set()
    for kind, value in cert.get('subjectAltName', ()):
        if kind in ('DNS', 'IP Address'):
            names.add(str(value).lower())
    return names


def _run_tls_self_test_sync(hosts: set[str], ca_cert_path: Path, port: int) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for host in sorted(hosts):
        try:
            _connect_tls_for_self_test(host, ca_cert_path, port)
        except Exception as exc:
            failures.append(f'{host}: {type(exc).__name__}: {exc}')

    try:
        default_cert = _connect_tls_for_self_test(None, ca_cert_path, port)
        san_hosts = _cert_dict_san_hosts(default_cert)
        missing = sorted(host for host in hosts if host.lower() not in san_hosts)
        if missing:
            failures.append(f'default cert missing SAN hosts: {", ".join(missing)}')
    except Exception as exc:
        failures.append(f'default cert without SNI: {type(exc).__name__}: {exc}')

    return not failures, failures


async def _run_tls_self_test(hosts: set[str], ca_cert_path: Path, port: int) -> bool:
    loop = asyncio.get_running_loop()
    ok, failures = await loop.run_in_executor(
        None,
        _run_tls_self_test_sync,
        set(hosts),
        ca_cert_path,
        port,
    )
    if ok:
        log_buffer.log('TLS', f'Startup TLS self-test passed for {format_count(hosts, "intercept host")}')
        return True
    for failure in failures:
        log_buffer.log('TLS', f'Startup TLS self-test failed: {failure}')
    return False


def _directory_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix='.fleasion-write-test-', dir=str(path))
        os.close(fd)
        os.unlink(tmp_path)
        return True
    except OSError:
        return False


def _select_proxy_ca_dir() -> Path:
    """Return the CA directory to use for this run, falling back if legacy ownership blocks writes."""
    global _ACTIVE_PROXY_CA_DIR

    if _directory_is_writable(PROXY_CA_DIR):
        _ACTIVE_PROXY_CA_DIR = PROXY_CA_DIR
        return _ACTIVE_PROXY_CA_DIR

    fallback = PROXY_CA_DIR.with_name(f'{PROXY_CA_DIR.name}_user')
    if _directory_is_writable(fallback):
        log_buffer.log(
            'Certificate',
            f'Configured CA directory is not writable ({PROXY_CA_DIR}); using {fallback}',
        )
        _ACTIVE_PROXY_CA_DIR = fallback
        return _ACTIVE_PROXY_CA_DIR

    _ACTIVE_PROXY_CA_DIR = PROXY_CA_DIR
    return _ACTIVE_PROXY_CA_DIR


def _current_proxy_ca_dir() -> Path:
    return _ACTIVE_PROXY_CA_DIR


def _is_macos_studio_bundle_path(exe_path: Path) -> bool:
    if not IS_MACOS:
        return False
    resolved = Path(exe_path)
    if resolved.name == 'RobloxStudio.app':
        return True
    return any(parent.name == 'RobloxStudio.app' for parent in resolved.parents)



def _flush_dns() -> None:
    """Flush the OS DNS cache so hosts-file changes take effect immediately.

    Calls ``DnsFlushResolverCache`` in *dnsapi.dll* directly via ctypes first.
    This is an in-process call — no subprocess is spawned — so security software
    that blocks child-process creation (e.g. Webroot SecureAnywhere / WRSVC)
    cannot interfere with it.  Falls back to ``ipconfig /flushdns`` only if the
    DLL call itself raises an exception (e.g. on a non-Windows build environment).
    """
    if IS_MACOS:
        if not _is_admin():
            # The privileged macOS helper flushes DNS as part of every hosts
            # apply/clear operation. Avoid a failing killall attempt here.
            return
        flushed = False
        for cmd in (['dscacheutil', '-flushcache'], ['killall', '-HUP', 'mDNSResponder']):
            try:
                subprocess.run(cmd, capture_output=True, timeout=5)
                flushed = True
            except Exception as exc:
                log_buffer.log('Hosts', f'DNS flush command failed ({cmd[0]}): {exc}')
        if flushed:
            log_buffer.log('Hosts', 'DNS cache flushed')
        return

    if IS_LINUX:
        flushed = False
        for cmd in (
            ['resolvectl', 'flush-caches'],
            ['systemd-resolve', '--flush-caches'],
            ['service', 'nscd', 'restart'],
        ):
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=5)
                if result.returncode == 0:
                    flushed = True
                    break
            except Exception:
                pass
        if flushed:
            log_buffer.log('Hosts', 'DNS cache flushed')
        else:
            log_buffer.log('Hosts', 'DNS cache flush skipped: no supported Linux flush command succeeded')
        return

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
    if not IS_WINDOWS:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

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
    if not IS_WINDOWS:
        return
    try:
        # Build a clean copy of the current hosts file (strip Fleasion lines)
        try:
            original = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
        except OSError:
            original = ''
        clean_content = ''.join(
            line for line in original.splitlines(keepends=True)
            if _HOSTS_MARKER not in line and not _hosts_line_has_target_loopback(line, set(INTERCEPT_HOSTS))
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
    if not IS_WINDOWS:
        return
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
    if IS_MACOS or IS_LINUX:
        return hasattr(os, 'geteuid') and os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _use_linux_privileged_helper() -> bool:
    return IS_LINUX and not _is_admin()


def _extract_exe_from_command(command: str) -> Optional[Path]:
    """Extract an executable path from a registry shell/open command string."""
    if not command:
        return None

    cmd = command.replace('\x00', '').strip()
    if not cmd:
        return None

    match = re.match(r'(.+?\.exe)(?:["\s]|$)', cmd, re.IGNORECASE)
    if match:
        exe_path = match.group(1).strip('"')
    else:
        try:
            parts = shlex.split(cmd, posix=False)
        except ValueError:
            parts = []
        exe_path = parts[0].strip('"') if parts else cmd.split()[0]

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
    if IS_MACOS or IS_LINUX:
        try:
            result = subprocess.run(
                ['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=6,
            )
        except Exception:
            result = None

        listeners: list[dict] = []
        if result is not None and result.returncode == 0:
            for raw_line in result.stdout.splitlines()[1:]:
                parts = raw_line.split()
                if len(parts) < 9:
                    continue
                try:
                    pid = int(parts[1])
                except ValueError:
                    continue
                local_address = parts[8].rsplit('->', 1)[0]
                if ':' in local_address:
                    local_address = local_address.rsplit(':', 1)[0]
                listeners.append(
                    {
                        'pid': pid,
                        'process_name': parts[0],
                        'local_address': local_address or '0.0.0.0',
                    }
                )
    else:
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
_HOSTS_IPV4_LOOPBACK = '127.0.0.1'
_HOSTS_IPV6_LOOPBACK = '::1'
_HOSTS_LOOPBACK_IPS = frozenset({_HOSTS_IPV4_LOOPBACK, _HOSTS_IPV6_LOOPBACK})
_HOSTS_ACTIVE_LOOPBACK_IPS: tuple[str, ...] | None = None


def _required_hosts_loopbacks() -> tuple[str, ...]:
    """Return loopback mappings Fleasion should own in the hosts file."""
    if _HOSTS_ACTIVE_LOOPBACK_IPS:
        return _HOSTS_ACTIVE_LOOPBACK_IPS
    if IS_WINDOWS:
        return (_HOSTS_IPV4_LOOPBACK, _HOSTS_IPV6_LOOPBACK)
    return (_HOSTS_IPV4_LOOPBACK,)


def _set_active_hosts_loopbacks(loopbacks: tuple[str, ...] | list[str] | set[str] | None) -> None:
    global _HOSTS_ACTIVE_LOOPBACK_IPS
    if not loopbacks:
        _HOSTS_ACTIVE_LOOPBACK_IPS = None
        return
    ordered = []
    for ip in (_HOSTS_IPV4_LOOPBACK, _HOSTS_IPV6_LOOPBACK):
        if ip in loopbacks:
            ordered.append(ip)
    _HOSTS_ACTIVE_LOOPBACK_IPS = tuple(ordered) or None


def _is_hosts_loopback_ip(ip: str) -> bool:
    return str(ip or '').strip().lower() in _HOSTS_LOOPBACK_IPS


def _parse_active_hosts_entries(content: str) -> dict[str, list[dict]]:
    """Parse active hosts-file mappings keyed by lowercase hostname."""
    entries: dict[str, list[dict]] = {}
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        active = raw_line.split('#', 1)[0].strip()
        if not active:
            continue
        parts = active.split()
        if len(parts) < 2:
            continue
        ip = parts[0]
        for hostname in parts[1:]:
            host_key = hostname.strip().lower()
            if not host_key:
                continue
            entries.setdefault(host_key, []).append(
                {
                    'ip': ip,
                    'line_no': line_no,
                    'line': raw_line.rstrip('\r\n'),
                }
            )
    return entries


def _hosts_conflicts(hosts: Set[str], entries: dict[str, list[dict]]) -> list[tuple[str, dict]]:
    conflicts: list[tuple[str, dict]] = []
    for host in sorted(hosts):
        for entry in entries.get(host.lower(), []):
            if not _is_hosts_loopback_ip(entry.get('ip', '')):
                conflicts.append((host, entry))
    return conflicts


def _record_hosts_error(error_details: Optional[dict], exc_or_text) -> None:
    if error_details is None:
        return
    err_text = str(exc_or_text)
    all_attempts_exhausted = 'all strategies exhausted' in err_text.lower()
    error_details.clear()
    error_details.update(
        {
            'hosts_path': str(HOSTS_FILE),
            'hosts_directory': str(HOSTS_FILE.parent),
            'error': err_text,
            'all_attempts_exhausted': all_attempts_exhausted,
            'notify_user': isinstance(exc_or_text, PermissionError) or all_attempts_exhausted,
        }
    )


def _log_hosts_conflicts(conflicts: list[tuple[str, dict]]) -> None:
    for host, entry in conflicts:
        log_buffer.log(
            'Hosts',
            f'Hosts conflict for {host}: line {entry["line_no"]}: {entry["line"]}',
        )


def _verify_hosts_entries(hosts: Set[str], error_details: Optional[dict] = None) -> bool:
    """Verify exact active hosts mappings after a write and DNS flush."""
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        log_buffer.log('Hosts', f'Hosts verification failed: cannot read hosts file: {exc}')
        _record_hosts_error(error_details, exc)
        return False

    entries = _parse_active_hosts_entries(existing)
    conflicts = _hosts_conflicts(hosts, entries)
    if conflicts:
        _log_hosts_conflicts(conflicts)
        _record_hosts_error(error_details, 'active conflicting hosts mappings detected')
        return False

    missing = []
    required_ips = _required_hosts_loopbacks()
    for host in sorted(hosts):
        host_entries = entries.get(host.lower(), [])
        for ip in required_ips:
            if not any(str(entry.get('ip', '')).lower() == ip for entry in host_entries):
                missing.append(f'{host}->{ip}')

    if missing:
        log_buffer.log('Hosts', f'Hosts verification failed: missing active mappings for {", ".join(missing)}')
        _record_hosts_error(error_details, f'missing active hosts mappings for {", ".join(missing)}')
        return False

    log_buffer.log('Hosts', f'Hosts verification passed for: {", ".join(sorted(hosts))}')
    return True


def _hosts_line_has_target_loopback(raw_line: str, hosts: Set[str]) -> bool:
    active = raw_line.split('#', 1)[0].strip()
    if not active:
        return False
    parts = active.split()
    if len(parts) < 2 or not _is_hosts_loopback_ip(parts[0]):
        return False
    target_hosts = {host.lower() for host in hosts}
    return any(host.lower() in target_hosts for host in parts[1:])


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
    HOSTS_FILE.parent.mkdir(exist_ok=True)

    # --- Strategy 0: clear read-only attribute if present ---
    if HOSTS_FILE.exists():
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
            f'If Webroot, Kaspersky, or another security product is installed, open its settings '
            f'and try to disable any setting relating to protecting the hosts file. '
            f'Last direct-write error: {last_exc}; rename error: {exc}'
        ) from exc


def _add_hosts_entries(hosts: Set[str], error_details: Optional[dict] = None) -> bool:
    """Append redirect entries for *hosts* to the system hosts file.

    Returns True on success.  Skips entries already present.
    Creates the hosts file from the Windows default if it is missing.
    If *error_details* is provided and a write fails with PermissionError,
    it is populated with metadata for user-facing error notifications.
    """
    if IS_MACOS and not _is_admin():
        from ..utils.macos_proxy_helper import helper_apply_hosts

        if helper_apply_hosts(set(hosts)):
            for host in sorted(hosts):
                log_buffer.log('Hosts', f'Added redirect through macOS helper: {host} -> 127.0.0.1')
            return True
        _record_hosts_error(error_details, 'macOS proxy helper failed to apply hosts entries')
        return False

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
            _record_hosts_error(error_details, exc)
            return False
    except OSError as exc:
        log_buffer.log('Hosts', f'Cannot read hosts file: {exc}')
        _record_hosts_error(error_details, exc)
        return False

    entries = _parse_active_hosts_entries(existing)
    conflicts = _hosts_conflicts(hosts, entries)
    if conflicts:
        _log_hosts_conflicts(conflicts)
        _record_hosts_error(error_details, 'active conflicting hosts mappings detected')
        return False

    lines_to_add = []
    required_ips = _required_hosts_loopbacks()
    for host in sorted(hosts):
        host_entries = entries.get(host.lower(), [])
        for ip in required_ips:
            entry = f'{ip} {host} {_HOSTS_MARKER}'
            if not any(str(e.get('ip', '')).lower() == ip for e in host_entries):
                lines_to_add.append(entry)

    if not lines_to_add:
        log_buffer.log('Hosts', 'Exact active hosts entries already present, skipping')
        return True

    new_content = existing.rstrip('\n') + '\n' + '\n'.join(lines_to_add) + '\n'
    try:
        _write_hosts_file(new_content)
        for entry in lines_to_add:
            ip, host = entry.split()[:2]
            log_buffer.log('Hosts', f'Added redirect: {host} -> {ip}')
        return True
    except PermissionError as exc:
        log_buffer.log('Hosts', f'Permission denied writing hosts file: {exc}')
        _record_hosts_error(error_details, exc)
        return False
    except OSError as exc:
        log_buffer.log('Hosts', f'Failed to write hosts file: {exc}')
        _record_hosts_error(error_details, exc)
        return False


def _remove_hosts_entries(hosts: Set[str], error_details: Optional[dict] = None) -> bool:
    """Remove any hosts file entries we previously added.

    Returns True if the hosts file is clean (entries removed or were already
    absent).  Returns False if the write failed — callers must NOT cancel the
    reboot guard in that case, so the next boot still cleans up automatically.
    """
    if IS_MACOS and not _is_admin():
        from ..utils.macos_proxy_helper import helper_clear_hosts

        if helper_clear_hosts():
            log_buffer.log('Hosts', 'Removed proxy hosts entries through macOS helper')
            return True
        _record_hosts_error(error_details, 'macOS proxy helper failed to clear hosts entries')
        return False

    def _record_error(exc: OSError) -> None:
        if error_details is None:
            return
        err_text = str(exc)
        all_attempts_exhausted = 'all strategies exhausted' in err_text.lower()
        error_details.clear()
        error_details.update(
            {
                'hosts_path': str(HOSTS_FILE),
                'hosts_directory': str(HOSTS_FILE.parent),
                'error': err_text,
                'all_attempts_exhausted': all_attempts_exhausted,
                'notify_user': isinstance(exc, PermissionError) or all_attempts_exhausted,
            }
        )

    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return True  # Can't read — assume nothing to clean, not a write failure

    lines = existing.splitlines(keepends=True)
    filtered = [
        line for line in lines
        if _HOSTS_MARKER not in line and not _hosts_line_has_target_loopback(line, hosts)
    ]

    if len(filtered) == len(lines):
        return True  # Nothing to remove — already clean

    try:
        _write_hosts_file(''.join(filtered))
        log_buffer.log('Hosts', 'Removed proxy hosts entries')
        return True
    except OSError as exc:
        _record_error(exc)
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
    if IS_MACOS:
        from ..utils.platform_macos import find_roblox_resource_dirs
    elif IS_LINUX:
        from ..utils.platform_linux import find_roblox_resource_dirs
    else:
        find_roblox_resource_dirs = None

    if find_roblox_resource_dirs is not None:
        found: list[Path] = []
        seen: set[str] = set()

        def _add_posix(path: Path) -> bool:
            if IS_MACOS and 'RobloxStudio.app' in path.parts:
                return False
            if is_roblox_studio_resource_dir(path):
                return False
            key = str(path.resolve()).lower()
            if key in seen:
                return False
            seen.add(key)
            found.append(path)
            return True

        for roblox_dir in find_roblox_resource_dirs(include_studio=not IS_MACOS):
            _add_posix(roblox_dir)
        for cached_dir in load_saved_roblox_dirs():
            _add_posix(cached_dir)
        save_saved_roblox_dirs(found)
        return found

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


_PEM_CERT_BLOCK_RE = re.compile(
    r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',
    re.DOTALL,
)
_CACERT_MIN_HEALTHY_CERTS = 2
_CACERT_MIN_HEALTHY_SIZE_BYTES = 4096
_CACERT_LAUNCH_SETTLE_SECONDS = 2.5
_CACERT_LAUNCH_POLL_SECONDS = 10.0
_CACERT_LAUNCH_POLL_INTERVAL_SECONDS = 0.5
_CACERT_RESTART_DEDUP_SECONDS = 8.0


def _normalize_newlines(text: str) -> str:
    """Normalize mixed newlines to LF for stable PEM comparisons."""
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _normalize_pem_block(pem_block: str) -> str:
    """Return a canonical PEM block representation (LF + trailing newline)."""
    return f"{_normalize_newlines(pem_block).strip()}\n"


def _is_fleasion_ca_cert_block(pem_block: str) -> bool:
    """Return True if *pem_block* is a Fleasion self-signed CA cert."""
    try:
        from cryptography import x509
        from cryptography.utils import CryptographyDeprecationWarning
        from cryptography.x509.oid import NameOID

        with warnings.catch_warnings():
            warnings.filterwarnings(
                'ignore',
                category=CryptographyDeprecationWarning,
                message=r"Parsed a serial number which wasn't positive.*",
            )
            cert = x509.load_pem_x509_certificate(pem_block.encode('utf-8'))
        cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        org_attrs = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
        cn = cn_attrs[0].value if cn_attrs else ''
        org = org_attrs[0].value if org_attrs else ''
        return (
            cert.subject == cert.issuer
            and cn == 'Fleasion Proxy CA'
            and org == 'Fleasion'
        )
    except Exception:
        return False


def _analyze_and_strip_fleasion_cas(pem_bundle: str, current_ca_pem: str) -> tuple[str, int, int]:
    """Remove all Fleasion CA blocks and return (cleaned_text, fleasion_count, current_count)."""
    normalized_bundle = _normalize_newlines(pem_bundle)
    normalized_current = _normalize_pem_block(current_ca_pem)

    parts: list[str] = []
    last_end = 0
    fleasion_count = 0
    current_count = 0

    for match in _PEM_CERT_BLOCK_RE.finditer(normalized_bundle):
        parts.append(normalized_bundle[last_end:match.start()])
        block = match.group(0)

        if _is_fleasion_ca_cert_block(block):
            fleasion_count += 1
            if _normalize_pem_block(block) == normalized_current:
                current_count += 1
        else:
            parts.append(block)

        last_end = match.end()

    parts.append(normalized_bundle[last_end:])
    return ''.join(parts), fleasion_count, current_count


def _fleasion_ca_blocks(pem_bundle: str) -> list[str]:
    """Return normalized Fleasion CA PEM blocks found in *pem_bundle*."""
    blocks: list[str] = []
    for match in _PEM_CERT_BLOCK_RE.finditer(_normalize_newlines(pem_bundle)):
        block = match.group(0)
        if _is_fleasion_ca_cert_block(block):
            blocks.append(_normalize_pem_block(block))
    return blocks


def _describe_cacert_state(ca_file: Path, ca_pem: str) -> dict:
    """Return a stable diagnostic snapshot for a Roblox cacert.pem bundle."""
    state = {
        'path': str(ca_file),
        'install': ca_file.parent.parent.name if ca_file.parent.name == 'ssl' else ca_file.parent.name,
        'exists': False,
        'size': 0,
        'mtime_ns': 0,
        'sha256': '',
        'total_certs': 0,
        'fleasion_certs': 0,
        'current_fleasion_certs': 0,
        'healthy': False,
        'error': '',
    }

    try:
        stat = ca_file.stat()
    except FileNotFoundError:
        return state
    except OSError as exc:
        state['error'] = str(exc)
        return state

    try:
        raw = ca_file.read_bytes()
    except OSError as exc:
        state['error'] = str(exc)
        return state

    text = raw.decode('utf-8', errors='replace')
    total_count = len(_PEM_CERT_BLOCK_RE.findall(_normalize_newlines(text)))
    _, fleasion_count, current_count = _analyze_and_strip_fleasion_cas(text, ca_pem)

    healthy = (
        stat.st_size >= _CACERT_MIN_HEALTHY_SIZE_BYTES
        and total_count >= _CACERT_MIN_HEALTHY_CERTS
        and fleasion_count == 1
        and current_count == 1
    )

    state.update({
        'exists': True,
        'size': stat.st_size,
        'mtime_ns': stat.st_mtime_ns,
        'sha256': hashlib.sha256(raw).hexdigest(),
        'total_certs': total_count,
        'fleasion_certs': fleasion_count,
        'current_fleasion_certs': current_count,
        'healthy': healthy,
    })
    return state


def _format_cacert_state(state: dict) -> str:
    sha = str(state.get('sha256') or '')
    short_sha = sha[:12] if sha else 'none'
    error = state.get('error') or ''
    error_text = f', error={error}' if error else ''
    return (
        f"path={state.get('path')}, exists={'yes' if state.get('exists') else 'no'}, "
        f"size={state.get('size')} bytes, mtime_ns={state.get('mtime_ns')}, "
        f"sha256={short_sha}, total certs={state.get('total_certs')}, "
        f"Fleasion certs={state.get('fleasion_certs')}, "
        f"current Fleasion certs={state.get('current_fleasion_certs')}, "
        f"healthy={'yes' if state.get('healthy') else 'no'}{error_text}"
    )


def _log_cacert_state(ca_file: Path, ca_pem: str, reason: str, *, log_healthy: bool = False) -> dict:
    state = _describe_cacert_state(ca_file, ca_pem)
    is_problem = (
        not state.get('exists')
        or bool(state.get('error'))
        or not bool(state.get('healthy'))
    )

    if log_healthy or is_problem:
        log_buffer.log('Certificate', f'{reason}: {_format_cacert_state(state)}')

    if not state.get('exists'):
        log_buffer.log('Certificate', f'WARNING: CERTS FILE MISSING: {ca_file}')
    elif state.get('error'):
        log_buffer.log('Certificate', f'Failed to inspect cacert.pem at {ca_file}: {state["error"]}')
    elif not state.get('healthy'):
        log_buffer.log('Certificate', f'WARNING: cacert.pem is not launch-healthy for {state.get("install")}: {_format_cacert_state(state)}')
    return state


def _log_cacert_health(ca_file: Path, ca_pem: str) -> None:
    """Compatibility wrapper for existing startup patch call sites."""
    _log_cacert_state(ca_file, ca_pem, f'cacert.pem health for {ca_file.parent.parent.name}')


def _linux_cacert_needs_seed(state: dict) -> bool:
    return (
        not bool(state.get('exists'))
        or int(state.get('size') or 0) < _CACERT_MIN_HEALTHY_SIZE_BYTES
        or int(state.get('total_certs') or 0) < _CACERT_MIN_HEALTHY_CERTS
    )


def _clear_cacert_write_barriers(path: Path) -> None:
    """Clear OS write barriers that would block rewriting Roblox cacert.pem."""
    try:
        current_flags = getattr(path.stat(), 'st_flags', 0)
    except OSError:
        current_flags = 0

    if current_flags and hasattr(os, 'chflags'):
        immutable_mask = 0
        for name in ('UF_IMMUTABLE', 'UF_APPEND', 'SF_IMMUTABLE', 'SF_APPEND'):
            immutable_mask |= getattr(stat, name, 0)
        if immutable_mask:
            try:
                os.chflags(path, current_flags & ~immutable_mask)
            except OSError:
                pass

    try:
        mode = path.stat().st_mode
    except OSError:
        return

    desired_mode = mode | stat.S_IWRITE
    if path.is_dir():
        desired_mode |= stat.S_IXUSR
    if desired_mode == mode:
        return
    try:
        path.chmod(desired_mode)
    except OSError:
        pass


def _prepare_cacert_target_for_write(ca_file: Path) -> None:
    """Make Roblox's ssl/cacert.pem destination writable before direct writes."""
    ssl_dir = ca_file.parent
    resource_dir = ssl_dir.parent

    if resource_dir.exists():
        _clear_cacert_write_barriers(resource_dir)
    if ssl_dir.exists():
        _clear_cacert_write_barriers(ssl_dir)
    else:
        ssl_dir.mkdir(exist_ok=True)
        _clear_cacert_write_barriers(ssl_dir)
    if ca_file.exists():
        _clear_cacert_write_barriers(ca_file)


def _cacert_is_read_only(ca_file: Path) -> bool:
    try:
        return ca_file.exists() and not bool(ca_file.stat().st_mode & stat.S_IWRITE)
    except OSError:
        return False


def _restore_cacert_read_only(ca_file: Path) -> None:
    try:
        if ca_file.exists():
            ca_file.chmod(ca_file.stat().st_mode & ~stat.S_IWRITE)
    except OSError:
        pass


def _healthy_linux_cacert_source(ca_file: Path, ca_pem: str, dirs: list[Path]) -> Path | None:
    for candidate_dir in dirs:
        candidate = candidate_dir / 'ssl' / 'cacert.pem'
        if candidate == ca_file:
            continue
        state = _describe_cacert_state(candidate, ca_pem)
        if bool(state.get('healthy')):
            return candidate
    return None


def _seed_linux_cacert_if_needed(ca_file: Path, state: dict, install_name: str, ca_pem: str, dirs: list[Path]) -> bool:
    """Replace a missing/truncated Roblox CA bundle with a healthy local or Mozilla bundle."""
    if not IS_LINUX:
        return False
    if bool(state.get('error')):
        return False
    if not _linux_cacert_needs_seed(state):
        return False

    source = _healthy_linux_cacert_source(ca_file, ca_pem, dirs)
    if source is not None:
        restore_read_only = _cacert_is_read_only(ca_file)
        try:
            _prepare_cacert_target_for_write(ca_file)
            shutil.copy2(source, ca_file)
            log_buffer.log('Certificate', f'Seeded Roblox cacert.pem from healthy local bundle for {install_name}: {source}')
            return True
        except Exception as exc:
            log_buffer.log('Certificate', f'Could not seed Roblox cacert.pem from local bundle for {install_name}: {exc}')
        finally:
            if restore_read_only:
                _restore_cacert_read_only(ca_file)

    restore_read_only = False
    try:
        import certifi

        restore_read_only = _cacert_is_read_only(ca_file)
        _prepare_cacert_target_for_write(ca_file)
        shutil.copy2(certifi.where(), ca_file)
        log_buffer.log('Certificate', f'Seeded Roblox cacert.pem from Mozilla CA bundle for {install_name}')
        return True
    except Exception as exc:
        log_buffer.log('Certificate', f'Could not seed Roblox cacert.pem for {install_name}: {exc}')
        return False
    finally:
        if restore_read_only:
            _restore_cacert_read_only(ca_file)


def _upsert_fleasion_ca_in_cacert(ca_file: Path, ca_pem: str) -> tuple[bool, int, int]:
    """Ensure exactly one current Fleasion CA exists in *ca_file*.

    Returns (changed, fleasion_count_before, current_count_before).
    """
    restore_read_only = _cacert_is_read_only(ca_file)
    _prepare_cacert_target_for_write(ca_file)
    try:
        existing = ca_file.read_text(encoding='utf-8', errors='replace') if ca_file.exists() else ''
        normalized_existing = _normalize_newlines(existing)

        cleaned, fleasion_count, current_count = _analyze_and_strip_fleasion_cas(existing, ca_pem)
        normalized_current = _normalize_pem_block(ca_pem)

        cleaned = cleaned.rstrip('\n')
        updated = f'{cleaned}\n{normalized_current}' if cleaned else normalized_current

        changed = updated != normalized_existing
        if changed:
            ca_file.write_text(updated, encoding='utf-8')

        return changed, fleasion_count, current_count
    finally:
        if restore_read_only:
            _restore_cacert_read_only(ca_file)


def _cacert_has_only_current_fleasion_ca(cacert_text: str, current_ca_pem: str) -> bool:
    """Return True when cacert contains exactly one Fleasion CA and it is current.

    This intentionally remains a narrow PEM-content predicate for callers that
    already own file-level health checks. Launch gating should use
    _describe_cacert_state() so a one-cert or truncated bundle is not treated as
    ready for Roblox.
    """
    _, fleasion_count, current_count = _analyze_and_strip_fleasion_cas(cacert_text, current_ca_pem)
    return fleasion_count == 1 and current_count == 1


def _install_ca_into_roblox_with_helper(ca_pem: str, dirs: list[Path]) -> tuple[bool, dict]:
    from ..utils.macos_proxy_helper import helper_patch_ca

    installs: list[dict] = []
    for roblox_dir in dirs:
        ca_file = roblox_dir / 'ssl' / 'cacert.pem'
        strip_all_fleasion_ca = False
        try:
            existing = ca_file.read_text(encoding='utf-8', errors='replace') if ca_file.exists() else ''
        except OSError as exc:
            log_buffer.log('Certificate', f'Could not pre-read cacert.pem for {roblox_dir.name}; helper will try root read/write: {exc}')
            existing = ''
            strip_all_fleasion_ca = True
        installs.append({
            'resource_dir': str(roblox_dir),
            'remove_pems': _fleasion_ca_blocks(existing),
            'strip_all_fleasion_ca': strip_all_fleasion_ca,
        })

    response = helper_patch_ca(ca_pem, installs)
    details = response or {
        'patched': [],
        'skipped': [],
        'failed': [{'error': 'macOS proxy helper did not return a CA patch response'}],
    }

    for key, label in (('patched', 'patched'), ('skipped', 'already current'), ('failed', 'failed')):
        for item in details.get(key) or []:
            path = item.get('ca_file') or item.get('resource_dir') or '(unknown)'
            if key == 'failed':
                log_buffer.log('Certificate', f'macOS helper CA patch {label} for {path}: {item.get("error") or item.get("status") or "unknown error"}')
            else:
                changed = 'changed' if item.get('changed') else 'unchanged'
                log_buffer.log('Certificate', f'macOS helper CA patch {label} for {path} ({changed})')

    all_healthy = bool(response and response.get('ok'))
    verified: list[dict] = []
    for roblox_dir in dirs:
        state = _log_cacert_state(
            roblox_dir / 'ssl' / 'cacert.pem',
            ca_pem,
            f'cacert.pem after macOS helper patch for {roblox_dir.name}',
        )
        verified.append(state)
        all_healthy = all_healthy and bool(state.get('healthy'))

    details['verified'] = verified
    return all_healthy, details


def _patch_roblox_ca_with_macos_helper(ca_pem: str, roblox_dir: Path) -> tuple[bool, bool, dict]:
    """Patch one macOS Roblox cacert.pem through the privileged helper.

    Returns (request_ok, changed, response_details).
    """
    from ..utils.macos_proxy_helper import helper_patch_ca

    ca_file = roblox_dir / 'ssl' / 'cacert.pem'
    strip_all_fleasion_ca = False
    try:
        existing = ca_file.read_text(encoding='utf-8', errors='replace') if ca_file.exists() else ''
    except OSError as exc:
        log_buffer.log('Certificate', f'Could not pre-read cacert.pem for {roblox_dir.name}; helper will try root read/write: {exc}')
        existing = ''
        strip_all_fleasion_ca = True

    response = helper_patch_ca(
        ca_pem,
        [{
            'resource_dir': str(roblox_dir),
            'remove_pems': _fleasion_ca_blocks(existing),
            'strip_all_fleasion_ca': strip_all_fleasion_ca,
        }],
    )
    if not response:
        return False, False, {'failed': [{'resource_dir': str(roblox_dir), 'error': 'macOS proxy helper did not return a CA patch response'}]}

    changed = any(bool(item.get('changed')) for item in response.get('patched') or [])
    for item in response.get('failed') or []:
        path = item.get('ca_file') or item.get('resource_dir') or str(ca_file)
        log_buffer.log('Certificate', f'macOS helper CA patch failed for {path}: {item.get("error") or item.get("status") or "unknown error"}')
    for key, label in (('patched', 'patched'), ('skipped', 'already current')):
        for item in response.get(key) or []:
            path = item.get('ca_file') or item.get('resource_dir') or str(ca_file)
            item_changed = 'changed' if item.get('changed') else 'unchanged'
            log_buffer.log('Certificate', f'macOS helper CA patch {label} for {path} ({item_changed})')
    return bool(response.get('ok')), changed, response


def _install_ca_into_roblox(ca_pem: str) -> tuple[bool, dict]:
    """Ensure each Roblox ssl/cacert.pem has exactly one current Fleasion CA cert."""
    t0 = time.perf_counter()
    dirs = _find_roblox_dirs()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if not dirs:
        log_buffer.log('Certificate', f'No Roblox installs found to patch (scanned in {elapsed_ms} ms)')
        return False, {'error': 'no_roblox_installs', 'dirs': []}
    log_buffer.log('Certificate', f'Found {format_count(dirs, "Roblox install")} to patch (scanned in {elapsed_ms} ms)')

    if IS_MACOS and not _is_admin():
        return _install_ca_into_roblox_with_helper(ca_pem, dirs)

    ok = True
    details = {'patched': [], 'failed': [], 'verified': []}
    for d in dirs:
        ssl_dir = d / 'ssl'
        ca_file = ssl_dir / 'cacert.pem'
        try:
            _prepare_cacert_target_for_write(ca_file)
            pre_state = _log_cacert_state(ca_file, ca_pem, f'cacert.pem health for {d.name}')
            seeded = _seed_linux_cacert_if_needed(ca_file, pre_state, d.name, ca_pem, dirs)
            changed, fleasion_count, current_count = _upsert_fleasion_ca_in_cacert(ca_file, ca_pem)
            changed = changed or seeded
            post_state = _log_cacert_state(ca_file, ca_pem, f'cacert.pem after startup patch for {d.name}')
            details['verified'].append(post_state)
            ok = ok and bool(post_state.get('healthy'))
            already_current = (
                fleasion_count == 1
                and current_count == 1
                and bool(post_state.get('healthy'))
            )

            if changed and not already_current:
                stale_count = max(fleasion_count - current_count, 0)
                duplicate_current = max(current_count - 1, 0)
                removed_count = stale_count + duplicate_current
                if removed_count > 0:
                    log_buffer.log('Certificate', f'Refreshed CA in {d.name} (removed {removed_count} stale/duplicate Fleasion CA entries)')
                else:
                    log_buffer.log('Certificate', f'Installed CA into {d.name}')
            elif changed:
                log_buffer.log('Certificate', f'Normalized CA bundle formatting in {d.name}')
            else:
                log_buffer.log('Certificate', f'CA already installed in {d.name}')
            details['patched'].append({'resource_dir': str(d), 'ca_file': str(ca_file), 'changed': changed})
        except (PermissionError, OSError, UnicodeDecodeError) as exc:
            log_buffer.log('Certificate', f'Failed to write CA for {d.name}: {exc}')
            details['failed'].append({'resource_dir': str(d), 'ca_file': str(ca_file), 'error': str(exc)})
            ok = False
    return ok, details


def _ca_thumbprint_sha1(ca_pem: str) -> str:
    body = ''.join(
        line.strip()
        for line in ca_pem.splitlines()
        if line and not line.startswith('-----')
    )
    der = base64.b64decode(body)
    return hashlib.sha1(der).hexdigest().upper()


def _certutil_store_has_thumbprint(store_location: str, thumbprint: str) -> bool:
    try:
        result = subprocess.run(
            [_CERTUTIL_EXE, '-store', store_location, thumbprint],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10,
        )
    except Exception as exc:
        log_buffer.log('Certificate', f'Windows {store_location} trust-store check failed: {exc}')
        return False
    text = ((result.stdout or b'') + (result.stderr or b'')).decode('utf-8', errors='replace')
    return result.returncode == 0 and thumbprint.lower() in text.replace(' ', '').lower()


def _certutil_fleasion_root_thumbprints(store_location: str) -> list[str]:
    try:
        result = subprocess.run(
            [_CERTUTIL_EXE, '-store', store_location],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
    except Exception as exc:
        log_buffer.log('Certificate', f'Windows {store_location} trust-store enumeration failed: {exc}')
        return []
    if result.returncode != 0:
        err = ((result.stderr or result.stdout or b'').decode('utf-8', errors='replace').strip())
        log_buffer.log('Certificate', f'Windows {store_location} trust-store enumeration failed: {err or result.returncode}')
        return []

    entries: list[tuple[str | None, str]] = []
    current_hash: str | None = None
    current_text: list[str] = []
    for line in (result.stdout or b'').decode('utf-8', errors='replace').splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith('cert hash(sha1):'):
            if current_hash is not None:
                entries.append((current_hash, '\n'.join(current_text)))
            current_hash = stripped.split(':', 1)[1].strip().replace(' ', '').upper()
            current_text = [stripped]
        elif current_hash is not None:
            current_text.append(stripped)
    if current_hash is not None:
        entries.append((current_hash, '\n'.join(current_text)))

    return [
        thumbprint
        for thumbprint, text in entries
        if thumbprint and 'fleasion proxy ca' in text.lower()
    ]


def _certutil_delete_from_store(store_location: str, thumbprint: str) -> bool:
    try:
        result = subprocess.run(
            [_CERTUTIL_EXE, '-delstore', store_location, thumbprint],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
    except Exception as exc:
        log_buffer.log('Certificate', f'Failed to remove stale CA {thumbprint} from Windows {store_location} store: {exc}')
        return False
    if result.returncode == 0:
        return True
    err = ((result.stderr or result.stdout or b'').decode('utf-8', errors='replace').strip())
    log_buffer.log('Certificate', f'Failed to remove stale CA {thumbprint} from Windows {store_location} store: {err or result.returncode}')
    return False


def _install_ca_into_windows_root(ca_cert_path: Path, ca_pem: str) -> None:
    """Trust Fleasion's CA in the Windows machine root store for browsers/tools."""
    thumbprint = _ca_thumbprint_sha1(ca_pem)
    store_location = r'Root'
    stale_thumbprints = [
        stored_thumbprint
        for stored_thumbprint in _certutil_fleasion_root_thumbprints(store_location)
        if stored_thumbprint != thumbprint
    ]
    removed_count = sum(
        1
        for stored_thumbprint in stale_thumbprints
        if _certutil_delete_from_store(store_location, stored_thumbprint)
    )

    if _certutil_store_has_thumbprint(store_location, thumbprint):
        if removed_count:
            log_buffer.log('Certificate', f'CA already trusted in Windows Root store (removed {removed_count} stale Fleasion CA entr{"y" if removed_count == 1 else "ies"})')
        else:
            log_buffer.log('Certificate', 'CA already trusted in Windows Root store')
        return

    try:
        result = subprocess.run(
            [_CERTUTIL_EXE, '-addstore', '-f', store_location, str(ca_cert_path)],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
    except Exception as exc:
        log_buffer.log('Certificate', f'Failed to install CA into Windows Root store: {exc}')
        return

    if result.returncode == 0:
        if removed_count:
            log_buffer.log('Certificate', f'Installed CA into Windows Root store (removed {removed_count} stale Fleasion CA entr{"y" if removed_count == 1 else "ies"})')
        else:
            log_buffer.log('Certificate', 'Installed CA into Windows Root store')
        return

    err = ((result.stderr or result.stdout or b'').decode('utf-8', errors='replace').strip())
    log_buffer.log('Certificate', f'Failed to install CA into Windows Root store: {err or result.returncode}')


def _macos_fleasion_keychain_thumbprints(keychain: str) -> list[str]:
    try:
        result = subprocess.run(
            ['security', 'find-certificate', '-a', '-p', '-c', 'Fleasion Proxy CA', keychain],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10,
        )
    except Exception as exc:
        log_buffer.log('Certificate', f'macOS trust-store enumeration failed: {exc}')
        return []
    if result.returncode != 0:
        return []

    thumbprints: list[str] = []
    for match in _PEM_CERT_BLOCK_RE.finditer(result.stdout or ''):
        block = match.group(0)
        if _is_fleasion_ca_cert_block(block):
            thumbprint = _ca_thumbprint_sha1(block).upper()
            if thumbprint:
                thumbprints.append(thumbprint)
    return thumbprints


def _macos_delete_keychain_certificate(keychain: str, thumbprint: str) -> bool:
    try:
        result = subprocess.run(
            ['security', 'delete-certificate', '-Z', thumbprint, keychain],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=20,
        )
    except Exception as exc:
        log_buffer.log('Certificate', f'Failed to remove stale macOS CA {thumbprint}: {exc}')
        return False
    if result.returncode == 0:
        return True
    err = (result.stderr or result.stdout or '').strip()
    log_buffer.log('Certificate', f'Failed to remove stale macOS CA {thumbprint}: {err or result.returncode}')
    return False


def _install_ca_into_macos_system_keychain(ca_cert_path: Path, ca_pem: str) -> None:
    """Trust Fleasion's CA in the macOS system keychain for local TLS clients."""
    thumbprint = _ca_thumbprint_sha1(ca_pem).upper()
    keychain = '/Library/Keychains/System.keychain'

    stored_thumbprints = _macos_fleasion_keychain_thumbprints(keychain)
    stale_thumbprints = [
        stored_thumbprint
        for stored_thumbprint in stored_thumbprints
        if stored_thumbprint != thumbprint
    ]
    removed_count = sum(
        1
        for stored_thumbprint in stale_thumbprints
        if _macos_delete_keychain_certificate(keychain, stored_thumbprint)
    )

    if thumbprint in stored_thumbprints:
        if removed_count:
            log_buffer.log('Certificate', f'CA already trusted in macOS System keychain (removed {removed_count} stale Fleasion CA entr{"y" if removed_count == 1 else "ies"})')
        else:
            log_buffer.log('Certificate', 'CA already trusted in macOS System keychain')
        return

    try:
        result = subprocess.run(
            [
                'security',
                'add-trusted-cert',
                '-d',
                '-r',
                'trustRoot',
                '-k',
                keychain,
                str(ca_cert_path),
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=20,
        )
    except Exception as exc:
        log_buffer.log('Certificate', f'Failed to install CA into macOS System keychain: {exc}')
        return

    if result.returncode == 0:
        if removed_count:
            log_buffer.log('Certificate', f'Installed CA into macOS System keychain (removed {removed_count} stale Fleasion CA entr{"y" if removed_count == 1 else "ies"})')
        else:
            log_buffer.log('Certificate', 'Installed CA into macOS System keychain')
        return

    err = (result.stderr or result.stdout or '').strip()
    log_buffer.log('Certificate', f'Failed to install CA into macOS System keychain: {err or result.returncode}')


def check_and_patch_running_roblox_ca(exe_path: 'Path') -> bool:
    """Check if the currently running Roblox instance has our CA in its cacert.pem.

    Called when RobloxPlayerBeta.exe is detected launching at runtime.
    If the cert chain is stale/missing/incomplete it is normalized immediately
    and an alert is logged.

    Returns True if the cert bundle needed refresh (Roblox needs a restart).
    Returns False if already launch-healthy or the CA has not been generated.
    """
    ca_cert_path = _current_proxy_ca_dir() / 'ca.crt'
    if not ca_cert_path.exists():
        return False  # CA not generated yet – nothing to patch
    if _is_macos_studio_bundle_path(Path(exe_path)):
        log_buffer.log('Certificate', f'Skipping macOS Roblox Studio CA patch for {Path(exe_path).name}')
        return False

    ca_pem = get_ca_pem(ca_cert_path)
    if IS_MACOS:
        from ..utils.platform_macos import _resource_root_from_executable

        roblox_dir = _resource_root_from_executable(exe_path) or exe_path.parent
    elif IS_LINUX:
        from ..utils.platform_linux import find_roblox_resource_dirs

        dirs = find_roblox_resource_dirs(include_studio=False)
        roblox_dir = dirs[0] if dirs else exe_path.parent
    else:
        roblox_dir = exe_path.parent
    ssl_dir = roblox_dir / 'ssl'
    ca_file = ssl_dir / 'cacert.pem'

    try:
        pre_state = _log_cacert_state(ca_file, ca_pem, f'cacert.pem before running-instance patch for {roblox_dir.name}')
        pre_state_readable = bool(pre_state.get('exists')) and not bool(pre_state.get('error'))
        if IS_MACOS and not _is_admin():
            request_ok, changed, helper_details = _patch_roblox_ca_with_macos_helper(ca_pem, roblox_dir)
            if not request_ok:
                log_buffer.log('Certificate', f'Failed to inject CA into running Roblox instance through macOS helper: {helper_details}')
                return False
            fleasion_count = int(pre_state.get('fleasion_certs') or 0) if pre_state_readable else 0
            current_count = int(pre_state.get('current_fleasion_certs') or 0) if pre_state_readable else 0
        else:
            _prepare_cacert_target_for_write(ca_file)
            changed, fleasion_count, current_count = _upsert_fleasion_ca_in_cacert(ca_file, ca_pem)
        post_state = _log_cacert_state(ca_file, ca_pem, f'cacert.pem after running-instance patch for {roblox_dir.name}')
        if IS_MACOS and not _is_admin() and not pre_state_readable:
            fleasion_count = int(post_state.get('fleasion_certs') or 0)
            current_count = int(post_state.get('current_fleasion_certs') or 0)
    except (PermissionError, OSError) as exc:
        log_buffer.log('Certificate', f'Failed to inject CA into running Roblox instance: {exc}')
        return False

    was_launch_healthy = bool(pre_state.get('healthy'))
    is_launch_healthy = bool(post_state.get('healthy'))
    already_current = fleasion_count == 1 and current_count == 1 and was_launch_healthy
    if already_current:
        log_buffer.log('Certificate', f'Roblox launch detected: cacert.pem already launch-healthy for {roblox_dir.name}')
        return False

    stale_count = max(fleasion_count - current_count, 0)
    duplicate_current = max(current_count - 1, 0)
    removed_count = stale_count + duplicate_current

    if current_count == 0:
        log_buffer.log(
            'Certificate',
            f'[ALERT] {exe_path.name} does not have a valid modified '
            'cacert.pem! It has been injected, you may need to relaunch it.',
        )
    elif removed_count > 0:
        log_buffer.log(
            'Certificate',
            f'[ALERT] {exe_path.name} had stale/duplicate Fleasion CAs in cacert.pem '
            f'({removed_count} removed). You may need to relaunch it.',
        )
    elif not was_launch_healthy or not is_launch_healthy:
        log_buffer.log(
            'Certificate',
            f'[ALERT] {exe_path.name} has an incomplete or unstable cacert.pem bundle. '
            'It has been normalized, you may need to relaunch it.',
        )

    if changed:
        log_buffer.log('Certificate', f'CA injected into running Roblox instance: {roblox_dir.name}')

    return changed or not was_launch_healthy or not is_launch_healthy


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
        self.username_spoofer = UsernameSpoofer(config_manager)
        
        self._texture_stripper: Optional[TextureStripper] = None

        self._proxy: Optional[FleasionProxy] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._hosts_installed: bool = False
        self._active_intercept_hosts: set[str] = set(BASE_INTERCEPT_HOSTS)
        self._roblox_player_running: bool = False
        self._watchdog_stop: Optional[threading.Event] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._module_interceptors: list = [self.username_spoofer]
        self._cert_refresh_lock = threading.Lock()
        self._last_cert_refresh_by_exe: dict[Path, tuple[float, str]] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    def _proxy_debug_enabled(self) -> bool:
        return bool(self.config_manager.settings.get('_runtime_proxy_debug', False))

    def _proxy_debug_mode(self) -> str:
        mode = str(self.config_manager.settings.get('_runtime_proxy_debug_mode', 'full') or 'full').lower()
        return mode if mode in {'a', 'b', 'c', 'd', 'e', 'full'} else 'full'

    def _effective_upstream_mode(self) -> str:
        if self._proxy_debug_enabled() and self._proxy_debug_mode() == 'e':
            return UpstreamMode.SYSTEM_PROXY.value
        return self.config_manager.upstream_transport_mode

    def _effective_wire_preserving_passthrough(self) -> bool:
        if self._proxy_debug_enabled() and self._proxy_debug_mode() == 'd':
            return True
        if IS_MACOS or _use_linux_privileged_helper():
            # Helper/relay platforms keep their existing passthrough behavior.
            # Do not use this as evidence that Windows should enable it globally.
            return True
        return self.config_manager.wire_preserving_passthrough

    def register_module_interceptor(self, module) -> None:
        """Register a module whose request()/response() methods are called for gamejoin traffic."""
        with self._lock:
            if module not in self._module_interceptors:
                self._module_interceptors.append(module)
            interceptors = list(self._module_interceptors)
        if self._proxy is not None:
            self._proxy.set_module_interceptors(interceptors)

    def unregister_module_interceptor(self, module) -> None:
        """Unregister a gamejoin traffic interceptor when its owning UI is destroyed."""
        with self._lock:
            before = len(self._module_interceptors)
            self._module_interceptors = [
                interceptor for interceptor in self._module_interceptors
                if interceptor is not module
            ]
            if len(self._module_interceptors) == before:
                return
            interceptors = list(self._module_interceptors)
        if self._proxy is not None:
            self._proxy.set_module_interceptors(interceptors)

    def _desired_intercept_hosts(self) -> set[str]:
        if self._proxy_debug_enabled():
            mode = self._proxy_debug_mode()
            if mode == 'a':
                hosts = {GAMEJOIN_HOST}
            elif mode == 'b':
                hosts = {GAMEJOIN_HOST, ASSET_DELIVERY_HOST}
            elif mode == 'c':
                hosts = {GAMEJOIN_HOST, ASSET_DELIVERY_HOST, *CDN_HOSTS}
            else:
                hosts = set(BASE_INTERCEPT_HOSTS)
        else:
            hosts = set(BASE_INTERCEPT_HOSTS)
        if _use_linux_privileged_helper():
            hosts.update(USERNAME_SPOOFER_INTERCEPT_HOSTS)
        spoofer = getattr(self, 'username_spoofer', None)
        if self._roblox_player_running and spoofer is not None and spoofer.is_enabled():
            hosts.update(USERNAME_SPOOFER_INTERCEPT_HOSTS)
        return hosts

    def set_roblox_player_running(self, running: bool) -> None:
        with self._lock:
            if self._roblox_player_running == running:
                return
            self._roblox_player_running = running
        self.refresh_username_spoofer_interception()

    def refresh_username_spoofer_interception(self) -> None:
        """Add or remove the profile API hosts entry as the spoofer is enabled."""
        desired_hosts = self._desired_intercept_hosts()
        with self._lock:
            if desired_hosts == self._active_intercept_hosts:
                return
            if not self._hosts_installed or self._proxy is None:
                self._active_intercept_hosts = set(desired_hosts)
                return

            previous_hosts = set(self._active_intercept_hosts)
            _remove_hosts_entries(set(INTERCEPT_HOSTS))
            _flush_dns()
            real_endpoints = _resolve_real_endpoints(desired_hosts)
            _log_upstream_ip_coverage(desired_hosts, real_endpoints)
            if not _add_hosts_entries(desired_hosts):
                log_buffer.log('Hosts', 'Failed to update username spoofer hosts entries')
                _add_hosts_entries(previous_hosts)
                _flush_dns()
                return
            _flush_dns()
            if not _verify_hosts_entries(desired_hosts):
                log_buffer.log('Hosts', 'Failed to verify username spoofer hosts entries')
                _remove_hosts_entries(set(INTERCEPT_HOSTS))
                _add_hosts_entries(previous_hosts)
                _flush_dns()
                _verify_hosts_entries(previous_hosts)
                return

            self._active_intercept_hosts = set(desired_hosts)
            self._proxy.set_upstream_endpoints(real_endpoints)
            scraper_ips = _first_endpoint_ips(real_endpoints)
            if scraper_ips:
                self.cache_scraper.set_real_ips(scraper_ips)
            log_buffer.log('Hosts', f'Active intercepts updated: {", ".join(sorted(desired_hosts))}')

    def _emit_proxy_start_error(self, code: str, details: dict) -> None:
        """Forward startup failures to the app layer for user-facing dialogs."""
        if self._on_proxy_start_error is None:
            return
        try:
            self._on_proxy_start_error(code, details)
        except Exception as exc:
            log_buffer.log('Error', f'Failed to dispatch proxy startup error callback: {exc}')

    def _start_watchdog(self) -> None:
        """Start the platform crash guard/heartbeat thread."""
        self._watchdog_stop = threading.Event()
        stop_event = self._watchdog_stop

        def _loop() -> None:
            while not stop_event.wait(_WATCHDOG_INTERVAL):
                if IS_MACOS:
                    from ..utils.macos_proxy_helper import helper_heartbeat

                    if not helper_heartbeat():
                        log_buffer.log('ProxyHelper', 'macOS proxy helper heartbeat failed')
                else:
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

    def _refresh_proxy_ips_for_cert_repair(self) -> None:
        if not self._hosts_installed:
            return
        active_hosts = self._desired_intercept_hosts()
        if _use_linux_privileged_helper():
            log_buffer.log(
                'Hosts',
                'Skipping in-place hosts refresh during Linux helper mode; helper owns /etc/hosts and port 443',
            )
            new_endpoints = _resolve_real_endpoints(active_hosts)
            if self._proxy is not None and new_endpoints:
                self._proxy.set_upstream_endpoints(new_endpoints)
            scraper_ips = _first_endpoint_ips(new_endpoints)
            if scraper_ips:
                self.cache_scraper.set_real_ips(scraper_ips)
            return
        # Remove entries temporarily so getaddrinfo() sees real IPs again.
        _remove_hosts_entries(set(INTERCEPT_HOSTS))
        _flush_dns()
        new_endpoints = _resolve_real_endpoints(active_hosts)
        _log_upstream_ip_coverage(active_hosts, new_endpoints)
        # Re-install entries pointing back to our proxy.
        # Acquire the lock before re-adding to guard against a race with stop():
        # if stop() ran while we were resolving IPs it will have set
        # _hosts_installed = False under this same lock, cancelled all cleanup
        # guards, and returned. Adding entries at that point would leave the
        # hosts file dirty with no mechanism to clean it up.
        with self._lock:
            if not self._hosts_installed:
                return
            if not _add_hosts_entries(active_hosts):
                log_buffer.log('Hosts', 'Failed to re-add hosts entries during Roblox cert refresh')
                return
            self._active_intercept_hosts = set(active_hosts)

        _flush_dns()
        if not _verify_hosts_entries(active_hosts):
            log_buffer.log('Hosts', 'Failed to verify hosts entries during Roblox cert refresh')
            return
        # Update running proxy and scraper with fresh upstream IPs.
        if self._proxy is not None and new_endpoints:
            self._proxy.set_upstream_endpoints(new_endpoints)
        scraper_ips = _first_endpoint_ips(new_endpoints)
        if scraper_ips:
            self.cache_scraper.set_real_ips(scraper_ips)

    def _repair_cert_refresh_ips_and_restart_roblox(self, exe_path: Path, ca_pem: str, ca_file: Path, reason: str) -> None:
        log_buffer.log('Certificate', f'{reason} — refreshing hosts and restarting...')

        def _patch_cert() -> None:
            check_and_patch_running_roblox_ca(exe_path)

        def _refresh_ips() -> None:
            self._refresh_proxy_ips_for_cert_repair()

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='fleasion-cert-refresh') as pool:
            f_cert = pool.submit(_patch_cert)
            f_ips = pool.submit(_refresh_ips)
        # Both futures are done after the with block (shutdown waits for them).

        for label, fut in (('cert patch', f_cert), ('IP refresh', f_ips)):
            if fut.exception():
                log_buffer.log('Certificate', f'Error during {label}: {fut.exception()}')

        _log_cacert_state(ca_file, ca_pem, f'cacert.pem before Roblox restart for {exe_path.parent.name}')
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

    def refresh_and_restart_roblox(self, exe_path: Path) -> None:
        """Validate launch-time Roblox CA state, repair it, and restart once if needed.

        Roblox/Fishstrap can rewrite ssl/cacert.pem after the first process is
        observed. A single immediate "already patched" check is therefore not
        enough. This method records the initial state, waits briefly for launch
        file churn to settle, then polls the active bundle. If the bundle is
        overwritten or incomplete, it patches certs, refreshes hosts/upstream IPs,
        and restarts Roblox exactly once for that launch window.
        """
        ca_cert_path = _current_proxy_ca_dir() / 'ca.crt'
        if not ca_cert_path.exists():
            return

        exe_path = Path(exe_path)
        if _is_macos_studio_bundle_path(exe_path):
            log_buffer.log('Certificate', f'Skipping macOS Roblox Studio CA refresh for {exe_path.name}')
            return
        ca_pem = get_ca_pem(ca_cert_path)
        if IS_MACOS:
            from ..utils.platform_macos import _resource_root_from_executable

            roblox_dir = _resource_root_from_executable(exe_path) or exe_path.parent
        elif IS_LINUX:
            from ..utils.platform_linux import find_roblox_resource_dirs

            dirs = find_roblox_resource_dirs(include_studio=False)
            roblox_dir = dirs[0] if dirs else exe_path.parent
        else:
            roblox_dir = exe_path.parent
        ca_file = roblox_dir / 'ssl' / 'cacert.pem'

        if not self._cert_refresh_lock.acquire(blocking=False):
            log_buffer.log('Certificate', f'Roblox launch CA refresh already in progress for {exe_path.parent.name}; skipping duplicate trigger')
            return

        try:
            initial_state = _log_cacert_state(ca_file, ca_pem, f'Roblox launch initial cacert.pem state for {exe_path.parent.name}')
            now = time.monotonic()
            last_refresh = self._last_cert_refresh_by_exe.get(exe_path)
            initial_sha = str(initial_state.get('sha256') or '')
            if last_refresh is not None:
                last_time, last_sha = last_refresh
                if now - last_time < _CACERT_RESTART_DEDUP_SECONDS and initial_sha == last_sha:
                    log_buffer.log(
                        'Certificate',
                        f'Roblox launch CA repair recently ran for {exe_path.parent.name}; '
                        'skipping duplicate restart because cacert.pem hash is unchanged',
                    )
                    return

            time.sleep(_CACERT_LAUNCH_SETTLE_SECONDS)
            stable_state = _log_cacert_state(ca_file, ca_pem, f'Roblox launch settled cacert.pem state for {exe_path.parent.name}')
            if stable_state.get('sha256') != initial_state.get('sha256'):
                log_buffer.log(
                    'Certificate',
                    f'cacert.pem changed during Roblox launch for {exe_path.parent.name}: '
                    f'{str(initial_state.get("sha256") or "none")[:12]} -> '
                    f'{str(stable_state.get("sha256") or "none")[:12]}',
                )

            deadline = time.monotonic() + _CACERT_LAUNCH_POLL_SECONDS
            last_state = stable_state
            last_sha = str(stable_state.get('sha256') or '')
            stable_unhealthy_samples = 0
            while time.monotonic() < deadline:
                if bool(last_state.get('healthy')):
                    log_buffer.log('Certificate', f'Roblox launch detected: stable patched cert confirmed for {exe_path.parent.name}')
                    return

                time.sleep(_CACERT_LAUNCH_POLL_INTERVAL_SECONDS)
                next_state = _log_cacert_state(ca_file, ca_pem, f'Roblox launch polling cacert.pem state for {exe_path.parent.name}')
                next_sha = str(next_state.get('sha256') or '')
                if next_sha and next_sha != last_sha:
                    log_buffer.log(
                        'Certificate',
                        f'cacert.pem overwritten after Roblox launch for {exe_path.parent.name}: '
                        f'{last_sha[:12] if last_sha else "none"} -> {next_sha[:12]}',
                    )
                    stable_unhealthy_samples = 0
                else:
                    stable_unhealthy_samples += 1
                    if stable_unhealthy_samples >= 2:
                        break
                last_state = next_state
                last_sha = next_sha

            self._last_cert_refresh_by_exe[exe_path] = (time.monotonic(), last_sha)
            self._repair_cert_refresh_ips_and_restart_roblox(
                exe_path,
                ca_pem,
                ca_file,
                'Roblox missing or unstable CA cert',
            )
        finally:
            self._cert_refresh_lock.release()


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
                if _use_linux_privileged_helper():
                    from ..utils.linux_proxy_helper import stop_helper

                    hosts_cleaned = stop_helper()
                else:
                    hosts_cleaned = _remove_hosts_entries(set(INTERCEPT_HOSTS))
                    _flush_dns()  # Clear stale 127.0.0.1 cache so new connections stop coming in
                self._hosts_installed = False
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
            _set_active_hosts_loopbacks(None)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                log_buffer.log('Proxy', 'Warning: proxy thread did not stop cleanly')

    async def _run_proxy(self) -> None:
        self._running = True
        self._loop = asyncio.get_running_loop()

        # ── Privileged proxy endpoint check ───────────────────────────────
        if IS_MACOS:
            from ..utils.macos_proxy_helper import helper_is_ready

            if not helper_is_ready():
                log_buffer.log('Error', 'The macOS proxy helper is not installed or not running')
                self._emit_proxy_start_error('macos_helper_unavailable', {})
                self._running = False
                return
        elif not _is_admin() and not _use_linux_privileged_helper():
            log_buffer.log('Error', (
                'Fleasion requires administrator privileges to modify the hosts file '
                'and bind port 443.  Please run as Administrator.'
            ))
            self._running = False
            return

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
        proxy_ca_dir = _select_proxy_ca_dir()
        try:
            ca_cert_path, ca_key_path = generate_ca(proxy_ca_dir)
        except Exception as exc:
            log_buffer.log('Certificate', f'CA generation failed: {exc}')
            self._running = False
            return

        host_certs = {}
        for host in INTERCEPT_HOSTS:
            try:
                cert_path, key_path = generate_host_cert(
                    host, ca_cert_path, ca_key_path, proxy_ca_dir,
                )
                host_certs[host] = (cert_path, key_path)
            except Exception as exc:
                log_buffer.log('Certificate', f'Leaf cert failed for {host}: {exc}')
                self._running = False
                return

        try:
            default_cert = generate_multi_host_cert(
                'intercept-default',
                INTERCEPT_HOSTS,
                ca_cert_path,
                ca_key_path,
                proxy_ca_dir,
            )
        except Exception as exc:
            log_buffer.log('Certificate', f'Default multi-host cert failed: {exc}')
            self._running = False
            return

        elapsed_ms = (time.perf_counter() - t0) * 1000
        log_buffer.log('Certificate', f'Certificates ready in {elapsed_ms:.0f} ms')

        # Install CA into Roblox ssl dirs
        ca_pem = get_ca_pem(ca_cert_path)
        ca_patch_ok, ca_patch_details = _install_ca_into_roblox(ca_pem)
        if IS_MACOS and not ca_patch_ok:
            log_buffer.log(
                'Certificate',
                'macOS Roblox CA patch verification failed; proxy startup aborted before writing hosts entries',
            )
            self._emit_proxy_start_error('macos_ca_patch_failed', ca_patch_details)
            self._running = False
            return
        if IS_WINDOWS:
            _install_ca_into_windows_root(ca_cert_path, ca_pem)
        elif IS_LINUX:
            from ..utils.linux_proxy_helper import install_ca_into_linux_trust

            install_ca_into_linux_trust(
                ca_cert_path,
                install_system=False,
            )

        # ── Clean up stale state from a previous crash ───────────────────
        # Skip cleanup entirely if another elevated Fleasion instance already
        # owns the proxy.  Deleting its watchdog task or hosts entries while it
        # is running would break it silently.
        if _use_linux_privileged_helper():
            log_buffer.log('ProxyHelper', 'Linux user-mode GUI active; privileged helper will own port 443 and hosts entries')
        elif not _other_proxy_owner_alive():
            _delete_watchdog_task()
            # Remove stale hosts entries: if the previous session crashed without
            # calling stop(), our entries may still be present.  getaddrinfo()
            # would return 127.0.0.1 instead of real CDN IPs, and upstream
            # connections would fail with WinError 1225.
            stale_hosts_error_details: dict = {}
            if not _remove_hosts_entries(set(INTERCEPT_HOSTS), error_details=stale_hosts_error_details):
                log_buffer.log('Error',
                    'Failed to remove stale proxy hosts entries — real CDN IPs '
                    'cannot be resolved safely.  Aborting proxy start. '
                    'If the problem persists, manually remove "# Fleasion proxy entry" '
                    f'lines from {HOSTS_FILE} and restart.')
                if stale_hosts_error_details.get('notify_user'):
                    self._emit_proxy_start_error('hosts_write_exhausted', stale_hosts_error_details)
                self._running = False
                return
            _flush_dns()
        else:
            log_buffer.log('Proxy', 'Another proxy owner is running — skipping startup cleanup')

        # ── Resolve real CDN IPs BEFORE writing new hosts file entries ────
        # CRITICAL: must happen after removing stale entries (above) and before
        # writing new ones. This guarantees getaddrinfo() returns real IPs.
        active_hosts = self._desired_intercept_hosts()
        self._active_intercept_hosts = set(active_hosts)
        if self._proxy_debug_enabled():
            log_buffer.log(
                'ProxyDiag',
                f'Proxy debug mode active: {self._proxy_debug_mode()} hosts={", ".join(sorted(active_hosts))}',
            )
        real_endpoints = _resolve_real_endpoints(active_hosts)
        _log_upstream_ip_coverage(active_hosts, real_endpoints)

        windows_proxy_info = detect_windows_proxy()
        system_http_proxy = detected_http_proxy(windows_proxy_info)
        _log_system_proxy_info(windows_proxy_info, system_http_proxy)
        manual_http_proxy = _manual_http_proxy_from_settings(self.config_manager)
        manual_socks5_proxy = _manual_socks5_proxy_from_settings(self.config_manager)

        # ── Create addon instances ────────────────────────────────────────
        self._texture_stripper = TextureStripper(self.config_manager)
        self._texture_stripper.set_cache_scraper(self.cache_scraper)
        # Give the scraper real IPs for ALL intercepted hosts so its API
        # calls bypass our hosts file redirect (including CDN redirects).
        scraper_ips = _first_endpoint_ips(real_endpoints)
        self.cache_scraper.set_real_ips(scraper_ips)

        # Wire the scraper into the json_viewer's AssetFetcherThread so the
        # Preview tab in the standalone JSON viewer also bypasses the hosts file.
        try:
            from ..gui.json_viewer import AssetFetcherThread
            AssetFetcherThread.set_scraper(self.cache_scraper)
        except Exception:
            pass

        # ── Start TLS proxy server ────────────────────────────────────────
        use_linux_helper = _use_linux_privileged_helper()
        listen_port = MACOS_PROXY_BACKEND_PORT if IS_MACOS or use_linux_helper else PROXY_PORT
        self._proxy = FleasionProxy(
            texture_stripper=self._texture_stripper,
            cache_scraper=self.cache_scraper,
            host_certs=host_certs,
            upstream_endpoints=real_endpoints,
            default_cert=default_cert,
            port=listen_port,
            upstream_mode=self._effective_upstream_mode(),
            system_http_proxy=system_http_proxy,
            manual_http_proxy=manual_http_proxy,
            manual_socks5_proxy=manual_socks5_proxy,
            wire_preserving_passthrough=self._effective_wire_preserving_passthrough(),
            vpn_compat_max_assetdelivery_connections=self.config_manager.vpn_compat_max_assetdelivery_connections,
            vpn_compat_max_cdn_connections=self.config_manager.vpn_compat_max_cdn_connections,
        )
        with self._lock:
            interceptors = list(self._module_interceptors)
        self._proxy.set_module_interceptors(interceptors)
        await self._proxy.log_upstream_self_test(active_hosts)
        try:
            await self._proxy.start()
        except OSError as exc:
            err_text = str(exc).lower()
            if (
                exc.errno in (10013, 10048)
                or 'access' in err_text
                or 'address already in use' in err_text
                or 'only one usage of each socket address' in err_text
                or (str(listen_port) in err_text and 'bind' in err_text)
            ):
                owners = _list_port_listeners(listen_port)
                log_buffer.log('Error', (
                    f'Cannot bind local proxy backend port {listen_port}: another process is already listening.'
                ))
                if owners:
                    owners_summary = '; '.join(
                        f"{owner['process_name']} (PID {owner['pid']}) on {owner['local_address']}:{listen_port}"
                        for owner in owners
                    )
                    log_buffer.log('Error', f'Port {listen_port} listeners: {owners_summary}')
                self._emit_proxy_start_error(
                    'port_bind_failed',
                    {
                        'port': listen_port,
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

        loopback_ips_for_hosts = getattr(self._proxy, 'loopback_ips_for_hosts', None)
        _set_active_hosts_loopbacks(
            loopback_ips_for_hosts() if IS_WINDOWS and callable(loopback_ips_for_hosts) else None
        )

        # ── TLS startup self-test ───────────────────────────────────────────
        # Probe every intercepted host with SNI plus one no-SNI connection before
        # the hosts file points Roblox at us. This catches certificate/SNI failures
        # that otherwise happen before normal request logs exist.
        if not await _run_tls_self_test(set(INTERCEPT_HOSTS), ca_cert_path, listen_port):
            await self._proxy.stop()
            _set_active_hosts_loopbacks(None)
            self._running = False
            return
        if use_linux_helper:
            from ..utils.linux_proxy_helper import linux_system_ca_needs_install, start_helper

            helper_ca_cert_path = ca_cert_path if linux_system_ca_needs_install(ca_cert_path) else None

            if not start_helper(
                active_hosts,
                backend_port=listen_port,
                ca_cert_path=helper_ca_cert_path,
            ):
                await self._proxy.stop()
                _set_active_hosts_loopbacks(None)
                self._emit_proxy_start_error('linux_helper_unavailable', {})
                self._running = False
                return
            if not ca_patch_ok:
                ca_patch_ok, ca_patch_details = _install_ca_into_roblox(ca_pem)
                if not ca_patch_ok:
                    log_buffer.log(
                        'Certificate',
                        'Linux Roblox CA patch verification failed after privileged helper ownership repair',
                    )
                    await self._proxy.stop()
                    _set_active_hosts_loopbacks(None)
                    from ..utils.linux_proxy_helper import stop_helper

                    stop_helper()
                    self._running = False
                    return
        if (IS_MACOS or use_linux_helper) and not await _run_tls_self_test(set(INTERCEPT_HOSTS), ca_cert_path, PROXY_PORT):
            log_buffer.log('ProxyHelper', 'Privileged port-443 relay TLS self-test failed')
            await self._proxy.stop()
            _set_active_hosts_loopbacks(None)
            if use_linux_helper:
                from ..utils.linux_proxy_helper import stop_helper

                stop_helper()
            self._running = False
            return

        # ── Write hosts file entries ──────────────────────────────────────
        hosts_error_details: dict = {}
        if use_linux_helper:
            self._hosts_installed = True
        elif not _add_hosts_entries(active_hosts, error_details=hosts_error_details):
            if hosts_error_details.get('notify_user'):
                self._emit_proxy_start_error('hosts_write_exhausted', hosts_error_details)
            # Hosts write failed - stop the server and bail
            await self._proxy.stop()
            _set_active_hosts_loopbacks(None)
            self._running = False
            return
        else:
            self._hosts_installed = True
            _flush_dns()  # Make the new entries take effect immediately
        if not _verify_hosts_entries(active_hosts, error_details=hosts_error_details):
            if use_linux_helper:
                from ..utils.linux_proxy_helper import stop_helper

                stop_helper()
            else:
                _remove_hosts_entries(set(INTERCEPT_HOSTS))
                _flush_dns()
            self._hosts_installed = False
            await self._proxy.stop()
            _set_active_hosts_loopbacks(None)
            self._running = False
            return
        try:
            _PROXY_OWNER_PID_FILE.write_text(str(os.getpid()))
        except OSError:
            pass
        _schedule_hosts_cleanup_on_reboot()  # Boot guard: power-loss / BSOD
        _upsert_watchdog_task()              # Initial task creation
        self._start_watchdog()               # Keep task pushed 5 s ahead

        log_buffer.log('Info', '=' * 50)
        log_buffer.log('Info', 'Fleasion Proxy Active')
        log_buffer.log('Info', f'Intercepting: {", ".join(sorted(active_hosts))}')
        log_buffer.log('Info', f'Port: {PROXY_PORT}')
        if IS_MACOS:
            log_buffer.log('Info', f'Unprivileged backend port: {listen_port}')
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
            await self._proxy.serve_forever()
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
            _set_active_hosts_loopbacks(None)
            self._running = False
            self._loop = None
