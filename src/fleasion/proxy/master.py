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

from __future__ import annotations

import asyncio
import base64
import csv
import ctypes
import hashlib
import importlib
import ipaddress
import json
import logging
import os
import platform
import re
import shlex
import shutil
import socket
import ssl
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, Protocol, Self, TypedDict, TypeIs, cast

if TYPE_CHECKING:
    winreg: _WinregLike

    from fleasion.config.manager import ConfigManager, JsonValue
    from fleasion.utils.linux_clients import LinuxClientDescriptor, LinuxClientInstallation
else:
    try:
        import winreg
    except ImportError:  # pragma: no cover - Windows-only module
        winreg = None

import contextlib

from fleasion.cache.cache_manager import CacheManager
from fleasion.utils import (
    LOCAL_APPDATA,
    MACOS_PROXY_BACKEND_PORT,
    PROXY_CA_DIR,
    PROXY_PORT,
    ROBLOX_PROCESS,
    ROBLOX_STUDIO_PROCESS,
    format_count,
    log_buffer,
    run_in_thread,
)
from fleasion.utils.certs import (
    generate_ca,
    generate_host_cert,
    generate_multi_host_cert,
    get_ca_pem,
)
from fleasion.utils.roblox_dirs import (
    is_roblox_studio_resource_dir,
    load_saved_roblox_dirs,
    save_saved_roblox_dirs,
)

from .addons import CacheScraper, CustomFFlagModifier, TextureStripper, UsernameSpoofer
from .server import (
    ASSET_DELIVERY_HOST,
    BASE_INTERCEPT_HOSTS,
    CDN_HOSTS,
    CUSTOM_FFLAGS_INTERCEPT_HOSTS,
    GAMEJOIN_HOST,
    INTERCEPT_HOSTS,
    PROXY_TLS_MAX_VERSION,
    USERNAME_SPOOFER_INTERCEPT_HOSTS,
    FleasionProxy,
    ProxyFlow,
)
from .upstream import HttpProxyConfig, Socks5ProxyConfig, UpstreamEndpoint, UpstreamMode
from .windows_proxy import WindowsProxyInfo, detect_windows_proxy, detected_http_proxy

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == 'win32'
IS_MACOS = sys.platform == 'darwin'
IS_LINUX = sys.platform.startswith('linux')
_WINDOWS_PROACTOR_ACCEPT_WINERROR = 10014
_RESOURCE_ROOT_FROM_EXECUTABLE_ATTR = '_resource_root_from_executable'
_OS_CHFLAGS_ATTR = 'chflags'
_SOCKET_GETSOCKNAME_ATTR = 'getsockname'
_SYSTEM_ROOT_ENV_KEY = 'System' + 'Root'
_UNSPECIFIED_IPV4 = str(ipaddress.IPv4Address(0))


def _resolve_command(args: Sequence[str]) -> list[str]:
    if not args:
        msg = 'Command must include an executable'
        raise ValueError(msg)
    executable = shutil.which(args[0]) or args[0]
    return [executable, *args[1:]]


def _run_command(
    args: Sequence[str],
    *,
    text: bool,
    timeout: float,
    creationflags: int = 0,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        _resolve_command(args),
        capture_output=True,
        text=text,
        encoding='utf-8' if text else None,
        errors='replace' if text else None,
        creationflags=creationflags,
        timeout=timeout,
        check=False,
        shell=False,
    )
    return cast('subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]', result)


def _run_binary_command(
    args: Sequence[str],
    *,
    timeout: float,
    creationflags: int = 0,
) -> subprocess.CompletedProcess[bytes]:
    return cast(
        'subprocess.CompletedProcess[bytes]',
        _run_command(args, text=False, timeout=timeout, creationflags=creationflags),
    )


def _run_text_command(
    args: Sequence[str],
    *,
    timeout: float,
    creationflags: int = 0,
) -> subprocess.CompletedProcess[str]:
    return cast(
        'subprocess.CompletedProcess[str]',
        _run_command(args, text=True, timeout=timeout, creationflags=creationflags),
    )


def _socket_local_address(socket_obj: object | None) -> object | None:
    if socket_obj is None:
        return None
    getsockname_value = getattr(socket_obj, _SOCKET_GETSOCKNAME_ATTR, None)
    if not callable(getsockname_value):
        return None
    getsockname = cast('Callable[[], object]', getsockname_value)
    try:
        return getsockname()
    except OSError:
        return None


class _RetryProxyWithWindowsSelectorError(RuntimeError):
    """Signal the proxy worker to replace a broken Windows Proactor loop."""


_RetryProxyWithWindowsSelector = _RetryProxyWithWindowsSelectorError


class _PortListener(TypedDict):
    pid: int
    process_name: str
    local_address: str


class _HostsEntry(TypedDict):
    ip: str
    line_no: int
    line: str


class _CacertState(TypedDict):
    path: str
    install: str
    exists: bool
    size: int
    mtime_ns: int
    sha256: str
    total_certs: int
    fleasion_certs: int
    current_fleasion_certs: int
    healthy: bool
    health_reason: str
    error: str


class _ProxyRequestLogEntry(TypedDict):
    id: int
    time: float
    host: str
    port: int
    method: str
    path: str
    intercepted: bool
    status: int | None
    size: int
    ms: int | None
    request_raw: bytes | bytearray | None
    response_raw: bytes | bytearray | None
    pending_stage: str | None
    was_intercepted: bool
    dropped_request: NotRequired[bool]
    dropped_response: NotRequired[bool]


class _ProxyCertificateState(TypedDict):
    proxy_ca_dir: Path
    ca_cert_path: Path
    ca_key_path: Path
    host_certs: dict[str, tuple[Path, Path]]
    default_cert: tuple[Path, Path]


class _ProxyStartupState(_ProxyCertificateState):
    ca_pem: str
    selected_linux_client_key: str | None
    ca_patch_ok: bool


class _ProxyServerState(TypedDict):
    active_hosts: set[str]
    use_linux_helper: bool
    env_proxy_intercept_excluded_hosts: set[str]
    listen_port: int


class _AutoReplaceRule(TypedDict, total=False):
    enabled: bool
    direction: str
    host_filter: str
    path_filter: str
    type: str
    match: str
    replacement: str


class _ModuleInterceptor(Protocol):
    def request(self, flow: ProxyFlow) -> None: ...

    def response(self, flow: ProxyFlow) -> None: ...


class _BinaryLineFile(Protocol):
    def seek(self, offset: int, whence: int = 0, /) -> int: ...

    def read(self, size: int = -1, /) -> bytes: ...


class _X509NameAttribute(Protocol):
    value: str


class _X509Name(Protocol):
    def get_attributes_for_oid(self, oid: object) -> Sequence[_X509NameAttribute]: ...


class _X509Certificate(Protocol):
    subject: _X509Name
    issuer: _X509Name


class _NameOidLike(Protocol):
    COMMON_NAME: object
    ORGANIZATION_NAME: object


class _RegistryKey(Protocol):
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool | None: ...


class _OpenKeyCallable(Protocol):
    def __call__(
        self,
        key: object,
        sub_key: str,
        reserved: int = 0,
        access: int = 0,
    ) -> _RegistryKey: ...


class _WinregLike(Protocol):
    HKEY_LOCAL_MACHINE: object
    HKEY_CURRENT_USER: object
    KEY_ALL_ACCESS: int
    REG_MULTI_SZ: int
    REG_SZ: int
    OpenKey: _OpenKeyCallable
    QueryValueEx: Callable[[_RegistryKey, str], tuple[object, int]]
    SetValueEx: Callable[[_RegistryKey, str, int, int, object], None]
    DeleteValue: Callable[[_RegistryKey, str], None]
    EnumKey: Callable[[_RegistryKey, int], str]


class _WindowsSubprocess(Protocol):
    CREATE_NO_WINDOW: int


class _DnsApi(Protocol):
    DnsFlushResolverCache: Callable[[], int]


class _Kernel32(Protocol):
    OpenProcess: Callable[[int, bool, int], int]
    GetExitCodeProcess: Callable[[int, object], int]
    CloseHandle: Callable[[int], int]


class _Shell32(Protocol):
    IsUserAnAdmin: Callable[[], int]


class _Windll(Protocol):
    kernel32: _Kernel32
    shell32: _Shell32
    LoadLibrary: Callable[[str], _DnsApi]


class _WindowsCtypes(Protocol):
    windll: _Windll


_windows_subprocess = cast('_WindowsSubprocess', subprocess)
_windows_ctypes = cast('_WindowsCtypes', ctypes)


type _ErrorDetails = dict[str, object]
type _PeerCertificate = Mapping[str, object]
type _CancelCheck = Callable[[], bool]
type _HelperPatchCa = Callable[[str, list[_ErrorDetails]], _ErrorDetails | None]
type _CacertInspection = _CacertState | _ErrorDetails


def _lazy_attr(module_name: str, attribute: str) -> object:
    module = importlib.import_module(module_name)
    try:
        return vars(module)[attribute]
    except KeyError as exc:
        msg = f'{module_name!r} does not export {attribute!r}'
        raise ImportError(msg) from exc


def delete_cache() -> list[str]:
    operation = cast('Callable[[], list[str]]', _lazy_attr('fleasion.utils', 'delete_cache'))
    return operation()


def is_roblox_running() -> bool:
    operation = cast('Callable[[], bool]', _lazy_attr('fleasion.utils', 'is_roblox_running'))
    return operation()


def terminate_roblox() -> bool:
    operation = cast('Callable[[], bool]', _lazy_attr('fleasion.utils', 'terminate_roblox'))
    return operation()


def wait_for_roblox_exit(*, timeout: float = 10.0) -> bool:
    operation = cast('Callable[..., bool]', _lazy_attr('fleasion.utils', 'wait_for_roblox_exit'))
    return operation(timeout=timeout)


def wait_for_roblox_window(*, timeout: float = 60.0) -> bool:
    operation = cast('Callable[..., bool]', _lazy_attr('fleasion.utils', 'wait_for_roblox_window'))
    return operation(timeout=timeout)


def get_roblox_player_exe_path() -> Path | None:
    operation = cast(
        'Callable[[], Path | None]',
        _lazy_attr('fleasion.utils.windows', 'get_roblox_player_exe_path'),
    )
    return operation()


def get_roblox_studio_exe_path() -> Path | None:
    operation = cast(
        'Callable[[], Path | None]',
        _lazy_attr('fleasion.utils.windows', 'get_roblox_studio_exe_path'),
    )
    return operation()


def launch_as_standard_user(target: str | Path) -> bool:
    operation = cast(
        'Callable[[str | Path], bool]',
        _lazy_attr('fleasion.utils.windows', 'launch_as_standard_user'),
    )
    return operation(target)


def _error_detail_list(value: object) -> list[_ErrorDetails]:
    return cast('list[_ErrorDetails]', value)


def _cacert_state_list(value: object) -> list[_CacertState]:
    return cast('list[_CacertState]', value)


def _is_str_object_dict(value: object) -> TypeIs[dict[str, object]]:
    if not isinstance(value, dict):
        return False
    mapping = cast('dict[object, object]', value)
    return all(isinstance(key, str) for key in mapping)


def _preserve_str(value: object) -> str:
    if TYPE_CHECKING:
        assert isinstance(value, str)
    return value


def _is_str_list(value: object) -> TypeIs[list[str]]:
    if not isinstance(value, list):
        return False
    values = cast('list[object]', value)
    return all(isinstance(item, str) for item in values)


def _preserve_str_list(value: object) -> list[str]:
    if TYPE_CHECKING:
        assert _is_str_list(value)
    return value


if TYPE_CHECKING:

    def _error_details(value: Mapping[str, object]) -> _ErrorDetails: ...

    def _failed_details(value: object) -> list[object]: ...

    def _upstream_endpoint_map(
        value: dict[str, list[UpstreamEndpoint]],
    ) -> dict[str, Sequence[UpstreamEndpoint | str]]: ...

    def _set_texture_scraper(texture: TextureStripper, scraper: CacheScraper) -> None: ...

    def _loopback_ips(proxy: FleasionProxy) -> tuple[str, ...] | list[str] | set[str] | None: ...

    def _maybe_proxy(value: FleasionProxy | None) -> FleasionProxy | None: ...

    def _maybe_texture(value: TextureStripper | None) -> TextureStripper | None: ...

    def _macos_helper_status() -> _ErrorDetails | None: ...

    def _macos_helper_probe_backend() -> _ErrorDetails: ...
else:

    def _error_details(value: Mapping[str, object]) -> _ErrorDetails:
        return value

    def _failed_details(value: object) -> list[object]:
        return value or []

    def _upstream_endpoint_map(
        value: dict[str, list[UpstreamEndpoint]],
    ) -> dict[str, Sequence[UpstreamEndpoint | str]]:
        return value

    def _set_texture_scraper(texture: TextureStripper, scraper: CacheScraper) -> None:
        texture.set_cache_scraper(scraper)

    def _loopback_ips(proxy: FleasionProxy) -> tuple[str, ...] | list[str] | set[str] | None:
        callback = getattr(proxy, 'loopback_ips_for_hosts', None)
        return callback() if callable(callback) else None

    def _maybe_proxy(value: FleasionProxy | None) -> FleasionProxy | None:
        return value

    def _maybe_texture(value: TextureStripper | None) -> TextureStripper | None:
        return value

    def _macos_helper_status() -> _ErrorDetails | None:
        helper_status = cast('Callable[[], _ErrorDetails | None]', _lazy_attr(
            'fleasion.utils.macos_proxy_helper', 'helper_status'
        ))
        return helper_status()

    def _macos_helper_probe_backend() -> _ErrorDetails:
        helper_probe_backend = cast('Callable[[], _ErrorDetails]', _lazy_attr(
            'fleasion.utils.macos_proxy_helper', 'helper_probe_backend'
        ))
        return helper_probe_backend()


_ACTIVE_PROXY_CA_DIR = PROXY_CA_DIR

HOSTS_FILE: Path = (
    Path('/etc/hosts')
    if IS_MACOS or IS_LINUX
    else Path(os.environ.get(_SYSTEM_ROOT_ENV_KEY, r'C:\Windows'))
    / 'System32'
    / 'drivers'
    / 'etc'
    / 'hosts'
)
_PLATFORM_TEMP_DIR: Path = (
    Path(tempfile.gettempdir())
    if IS_MACOS or IS_LINUX
    else Path(os.environ.get('TEMP', r'C:\Windows\Temp'))
)
_HOSTS_MARKER = '# Fleasion proxy entry'
_HOSTS_FILE_REPAIR_THRESHOLD_BYTES = 512 * 1024
SOBER_CUSTOM_FFLAG_ROUTE_ARM_DELAY_SECONDS = 30.0
_SOBER_CUSTOM_FFLAG_POLL_SECONDS = 0.25

# Registry key used by Windows to replace files on next reboot
_PENDING_RENAME_KEY = r'SYSTEM\CurrentControlSet\Control\Session Manager'
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
_WATCHDOG_INTERVAL = 10  # seconds between watchdog refreshes
_WATCHDOG_SCHTASKS_TIMEOUT = 20
_WATCHDOG_TASK_XML = _PLATFORM_TEMP_DIR / 'fleasion_watchdog_task.xml'
_SCHTASKS_EXE = str(Path(os.environ.get(_SYSTEM_ROOT_ENV_KEY, r'C:\Windows')) / 'System32' / 'schtasks.exe')
_CERTUTIL_EXE = str(Path(os.environ.get(_SYSTEM_ROOT_ENV_KEY, r'C:\Windows')) / 'System32' / 'certutil.exe')

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


def _watchdog_create_command() -> list[str]:
    run_at = datetime.now(UTC).astimezone() + timedelta(seconds=_WATCHDOG_LOOKAHEAD)
    _WATCHDOG_TASK_XML.write_text(_build_watchdog_xml(run_at), encoding='utf-16')
    return [
        _SCHTASKS_EXE,
        '/create',
        '/TN',
        _WATCHDOG_TASK_NAME,
        '/XML',
        str(_WATCHDOG_TASK_XML),
        '/RU',
        'SYSTEM',
        '/F',
    ]


def _upsert_watchdog_task() -> None:
    """Create (or replace) the watchdog task to fire _WATCHDOG_LOOKAHEAD seconds from now."""
    if not IS_WINDOWS:
        return
    try:
        result = _run_binary_command(
            _watchdog_create_command(),
            creationflags=_windows_subprocess.CREATE_NO_WINDOW,
            timeout=_WATCHDOG_SCHTASKS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log_buffer.log(
            'Watchdog',
            f'schtasks timed out after {_WATCHDOG_SCHTASKS_TIMEOUT}s while creating '
            f'{_WATCHDOG_TASK_NAME}; Task Scheduler or security software may be slow/blocking it. '
            f'XML: {_WATCHDOG_TASK_XML}',
        )
        return
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('Watchdog', f'Could not upsert watchdog task (non-fatal): {exc}')
        return
    if result.returncode != 0:
        err = (result.stderr or result.stdout or b'').decode('utf-8', errors='replace').strip()
        log_buffer.log('Watchdog', f'schtasks returned non-zero ({result.returncode}): {err}')


def _delete_watchdog_task() -> None:
    """Delete the watchdog task if it exists.  Safe to call even if absent."""
    if not IS_WINDOWS:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        result = _run_binary_command(
            [_SCHTASKS_EXE, '/delete', '/TN', _WATCHDOG_TASK_NAME, '/F'],
            creationflags=_windows_subprocess.CREATE_NO_WINDOW,
            timeout=_WATCHDOG_SCHTASKS_TIMEOUT,
        )
        if result.returncode == 0:
            log_buffer.log('Watchdog', 'Task deleted (clean exit)')
    with contextlib.suppress(OSError):
        _WATCHDOG_TASK_XML.unlink(missing_ok=True)


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
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _dns_query_udp(
    hostname: str, server: str, port: int = 53, timeout: float = 3.0, qtype: int = 1
) -> list[str]:
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
    # --- Build a minimal DNS query packet ---
    # Transaction ID: arbitrary 16-bit value
    txid = 0x4649  # 'FI' — easy to spot in Wireshark
    # Flags: standard query, recursion desired
    flags = 0x0100
    # 1 question, 0 answer/authority/additional RRs
    header = struct.pack('!HHHHHH', txid, flags, 1, 0, 0, 0)

    # Encode hostname as a sequence of length-prefixed labels
    labels = b''
    for part in hostname.encode('ascii').split(b'.'):
        labels += struct.pack('B', len(part)) + part
    labels += b'\x00'  # root label

    # QTYPE=A (1) or AAAA (28), QCLASS=IN (1)
    question = labels + struct.pack('!HH', qtype, 1)
    packet = header + question

    # --- Send and receive ---
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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
    r_txid, _r_flags, r_qdcount, r_ancount, _, _ = struct.unpack('!HHHHHH', response[:12])
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
    ips: list[str] = []
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
        rtype, _, _, rdlength = struct.unpack('!HHIH', response[pos : pos + 10])
        pos += 10
        if rtype == 1 and rdlength == 4:  # A record
            ip = '.'.join(str(b) for b in response[pos : pos + 4])
            if _is_routable_public_ip(ip):
                ips.append(ip)
        elif rtype == 28 and rdlength == 16:  # AAAA record
            try:
                ip = socket.inet_ntop(socket.AF_INET6, response[pos : pos + 16])
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


def _resolve_os_endpoints(
    host: str, seen: set[tuple[socket.AddressFamily, str]]
) -> list[UpstreamEndpoint]:
    try:
        results = socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except OSError as exc:
        log_buffer.log('Proxy', f'DNS resolve failed for {host} (OS resolver): {exc}')
        return []
    endpoints: list[UpstreamEndpoint] = []
    for family, _socktype, _proto, _canonname, sockaddr in results:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        ip = sockaddr[0]
        if not isinstance(ip, str):
            continue
        key = (family, ip)
        if key in seen or not _is_routable_public_ip(ip):
            continue
        seen.add(key)
        endpoints.append(UpstreamEndpoint(host=host, ip=ip, family=family))
    return endpoints


def _resolve_public_dns_endpoint_set(
    host: str,
    dns_server: str,
    seen: set[tuple[socket.AddressFamily, str]],
    timeout: float,
) -> list[UpstreamEndpoint]:
    fallback: list[UpstreamEndpoint] = []
    for family, qtype in ((socket.AF_INET, 1), (socket.AF_INET6, 28)):
        for ip in _dns_query_udp(host, dns_server, timeout=timeout, qtype=qtype):
            key = (family, ip)
            if key in seen:
                continue
            seen.add(key)
            fallback.append(UpstreamEndpoint(host=host, ip=ip, family=family))
    return fallback


def _resolve_real_endpoints(
    hosts: set[str],
    *,
    collect_all_public_fallbacks: bool = False,
    public_dns_timeout: float = 3.0,
) -> dict[str, list[UpstreamEndpoint]]:
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
    real_endpoints: dict[str, list[UpstreamEndpoint]] = {}
    for host in sorted(hosts):
        seen: set[tuple[socket.AddressFamily, str]] = set()

        # --- Primary: OS resolver ---
        endpoints = _resolve_os_endpoints(host, seen)

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
        fallback_endpoints: list[UpstreamEndpoint] = []
        fallback_servers: list[str] = []
        for dns_server in _DNS_FALLBACK_SERVERS:
            try:
                fallback = _resolve_public_dns_endpoint_set(
                    host, dns_server, seen, public_dns_timeout
                )
            except (OSError, UnicodeError, ValueError, struct.error) as exc:
                log_buffer.log('Proxy', f'Direct UDP DNS to {dns_server} failed for {host}: {exc}')
                continue
            if fallback:
                fallback_endpoints.extend(fallback)
                fallback_servers.append(dns_server)
                if not collect_all_public_fallbacks:
                    break
        if fallback_endpoints:
            real_endpoints[host] = _prefer_ipv4_endpoints(fallback_endpoints)
            resolver_note = (
                f' via {", ".join(fallback_servers)}' if len(fallback_servers) > 1 else ''
            )
            log_buffer.log(
                'Proxy',
                f'Public DNS fallback used for {host}{resolver_note}. '
                'This may be incompatible with VPN routing.',
            )
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
    return {host: [ep.ip for ep in eps if ep.ip] for host, eps in endpoints.items()}


def _refresh_real_upstream_endpoints(host: str) -> list[UpstreamEndpoint]:
    """Resolve fresh real routes after an intercepted host times out.

    At runtime the hosts file deliberately points intercepted names to
    loopback, so the normal resolver cannot supply a usable upstream IP.  The
    existing raw-DNS fallback bypasses that redirect.  Querying every fallback
    resolver here gives a transiently bad CDN/API edge a chance to be replaced
    without restarting Roblox or waiting out a transport cooldown.
    """
    normalized_host = str(host).strip().lower().rstrip('.')
    if not normalized_host:
        return []
    endpoints = _resolve_real_endpoints(
        {normalized_host},
        collect_all_public_fallbacks=True,
        public_dns_timeout=0.75,
    ).get(normalized_host, [])
    if endpoints:
        log_buffer.log(
            'Proxy',
            f'Runtime upstream endpoint refresh for {normalized_host}: '
            f'{", ".join(ep.ip for ep in endpoints if ep.ip)}',
        )
    return endpoints


def _log_upstream_ip_coverage(
    hosts: set[str], real_endpoints: Mapping[str, Sequence[UpstreamEndpoint | str]]
) -> None:
    for host in sorted(hosts):
        endpoints = real_endpoints.get(host) or []
        ips = [
            ip
            for ep in endpoints
            if (ip := ep.ip if isinstance(ep, UpstreamEndpoint) else str(ep)) is not None and ip
        ]
        if ips:
            log_buffer.log('Proxy', f'Upstream IP coverage: {host} -> {", ".join(ips)}')
        else:
            log_buffer.log('Proxy', f'Upstream IP coverage: {host} -> NO ROUTABLE IPS')


def _endpoint_ip_candidates(
    real_endpoints: dict[str, list[UpstreamEndpoint]],
) -> dict[str, tuple[str, ...]]:
    """Return every usable IP for direct cache-scraper bypasses.

    The proxy already retries all resolved upstream endpoints.  Keeping only
    the first address for cache/pre-download traffic created an avoidable
    single-edge failure mode for assetdelivery and CDN downloads.
    """
    return {
        host: tuple(ep.ip for ep in eps if ep.ip)
        for host, eps in real_endpoints.items()
        if any(ep.ip for ep in eps)
    }


def _set_proxy_upstream_endpoints(
    proxy: FleasionProxy, endpoints: dict[str, list[UpstreamEndpoint]]
) -> None:
    proxy.set_upstream_endpoints(cast('dict[str, Sequence[UpstreamEndpoint | str]]', endpoints))


def _set_cache_scraper_real_ips(
    scraper: CacheScraper, real_ips: dict[str, tuple[str, ...]]
) -> None:
    setter = cast(
        'Callable[[dict[str, tuple[str, ...]]], None]',
        scraper.set_real_ips,
    )
    setter(real_ips)


def _log_system_proxy_info(info: WindowsProxyInfo, system_proxy: HttpProxyConfig | None) -> None:
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


def _log_upstream_transport_settings(
    *,
    configured_mode: str,
    effective_mode: str,
    system_proxy: HttpProxyConfig | None,
    manual_http_proxy: HttpProxyConfig | None,
    manual_socks5_proxy: Socks5ProxyConfig | None,
    asset_limit: int,
    cdn_limit: int,
) -> None:
    override = ''
    if effective_mode != configured_mode:
        override = f' effective={effective_mode}'
    system_state = (
        f'{system_proxy.host}:{system_proxy.port}' if system_proxy is not None else 'not detected'
    )
    manual_http_state = (
        f'{manual_http_proxy.host}:{manual_http_proxy.port}'
        if manual_http_proxy is not None
        else 'not configured'
    )
    manual_socks5_state = (
        f'{manual_socks5_proxy.host}:{manual_socks5_proxy.port}'
        if manual_socks5_proxy is not None
        else 'not configured'
    )
    log_buffer.log(
        'ProxyDiag',
        'Upstream transport settings: '
        f'configured={configured_mode}{override}; '
        f'system_proxy={system_state}; '
        f'manual_http_connect={manual_http_state}; '
        f'manual_socks5={manual_socks5_state}; '
        f'vpn_limits assetdelivery={asset_limit} cdn={cdn_limit}',
    )


def _manual_http_proxy_from_settings(config_manager: ConfigManager) -> HttpProxyConfig | None:
    host = str(getattr(config_manager, 'upstream_http_connect_host', '') or '').strip()
    port = int(getattr(config_manager, 'upstream_http_connect_port', 0) or 0)
    if not host or port <= 0:
        return None
    username = str(getattr(config_manager, 'upstream_http_connect_username', '') or '') or None
    password = str(getattr(config_manager, 'upstream_http_connect_password', '') or '') or None
    return HttpProxyConfig(host=host, port=port, username=username, password=password)


def _manual_socks5_proxy_from_settings(config_manager: ConfigManager) -> Socks5ProxyConfig | None:
    host = str(getattr(config_manager, 'upstream_socks5_host', '') or '').strip()
    port = int(getattr(config_manager, 'upstream_socks5_port', 0) or 0)
    if not host or port <= 0:
        return None
    username = str(getattr(config_manager, 'upstream_socks5_username', '') or '') or None
    password = str(getattr(config_manager, 'upstream_socks5_password', '') or '') or None
    return Socks5ProxyConfig(host=host, port=port, username=username, password=password)


def _connect_tls_for_self_test(
    host: str | None,
    ca_cert_path: Path,
    port: int,
    loopback_host: str = '127.0.0.1',
    tls_max_version: ssl.TLSVersion = PROXY_TLS_MAX_VERSION,
) -> _PeerCertificate:
    ctx = ssl.create_default_context(cafile=str(ca_cert_path))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = tls_max_version
    if host is None:
        ctx.check_hostname = False
    with (
        socket.create_connection((loopback_host, port), timeout=5.0) as raw_sock,
        ctx.wrap_socket(raw_sock, server_hostname=host) as tls_sock,
    ):
        cert = tls_sock.getpeercert()
        return cert if isinstance(cert, dict) else {}


def _connect_explicit_proxy_tls_for_self_test(
    host: str | None,
    ca_cert_path: Path,
    port: int,
    loopback_host: str = '127.0.0.1',
    tls_max_version: ssl.TLSVersion = PROXY_TLS_MAX_VERSION,
) -> _PeerCertificate:
    if host is None:
        return _connect_tls_for_self_test(
            host,
            ca_cert_path,
            port,
            loopback_host=loopback_host,
            tls_max_version=tls_max_version,
        )
    ctx = ssl.create_default_context(cafile=str(ca_cert_path))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = tls_max_version
    with socket.create_connection((loopback_host, port), timeout=5.0) as raw_sock:
        request = (
            f'CONNECT {host}:443 HTTP/1.1\r\n'
            f'Host: {host}:443\r\n'
            'Proxy-Connection: keep-alive\r\n'
            '\r\n'
        ).encode('ascii')
        raw_sock.sendall(request)
        response = b''
        while b'\r\n\r\n' not in response and len(response) < 4096:
            chunk = raw_sock.recv(4096)
            if not chunk:
                break
            response += chunk
        first_line = response.split(b'\r\n', 1)[0]
        if b' 200 ' not in first_line:
            msg = f'CONNECT self-test failed: {first_line!r}'
            raise OSError(msg)
        with ctx.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
            cert = tls_sock.getpeercert()
            return cert if isinstance(cert, dict) else {}


def _cert_dict_san_hosts(cert: _PeerCertificate) -> set[str]:
    names: set[str] = set()
    subject_alt_names = cert.get('subjectAltName', ())
    if not isinstance(subject_alt_names, tuple):
        return names
    for entry_value in cast('tuple[object, ...]', subject_alt_names):
        if not isinstance(entry_value, tuple):
            continue
        entry = cast('tuple[object, ...]', entry_value)
        if len(entry) != 2:
            continue
        kind, value = entry
        if kind in {'DNS', 'IP Address'} and isinstance(value, str):
            names.add(value.lower())
    return names


def _run_tls_self_test_sync(
    hosts: set[str],
    ca_cert_path: Path,
    port: int,
    *,
    explicit_proxy: bool = False,
    loopback_host: str = '127.0.0.1',
    tls_max_version: ssl.TLSVersion = PROXY_TLS_MAX_VERSION,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    connector = (
        _connect_explicit_proxy_tls_for_self_test if explicit_proxy else _connect_tls_for_self_test
    )
    for host in sorted(hosts):
        try:
            connector(
                host,
                ca_cert_path,
                port,
                loopback_host=loopback_host,
                tls_max_version=tls_max_version,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append(f'{host}: {type(exc).__name__}: {exc}')

    if not explicit_proxy:
        try:
            default_cert = _connect_tls_for_self_test(
                None,
                ca_cert_path,
                port,
                loopback_host=loopback_host,
                tls_max_version=tls_max_version,
            )
            san_hosts = _cert_dict_san_hosts(default_cert)
            missing = sorted(host for host in hosts if host.lower() not in san_hosts)
            if missing:
                failures.append(f'default cert missing SAN hosts: {", ".join(missing)}')
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append(f'default cert without SNI: {type(exc).__name__}: {exc}')

    return not failures, failures


async def _tls_self_test_result(  # ruff: ignore[too-many-positional-arguments]
    hosts: set[str],
    ca_cert_path: Path,
    port: int,
    explicit_proxy: bool = False,
    loopback_host: str = '127.0.0.1',
    tls_max_version: ssl.TLSVersion = PROXY_TLS_MAX_VERSION,
) -> tuple[bool, list[str]]:
    loop = asyncio.get_running_loop()
    operation = partial(
        _run_tls_self_test_sync,
        set(hosts),
        ca_cert_path,
        port,
        explicit_proxy=explicit_proxy,
        loopback_host=loopback_host,
        tls_max_version=tls_max_version,
    )
    return await loop.run_in_executor(None, operation)


def _record_raw_tls_server_session(
    raw_sock: socket.socket,
    server_ctx: ssl.SSLContext,
    server_result: dict[str, str],
) -> None:
    with raw_sock:
        raw_sock.settimeout(5.0)
        with server_ctx.wrap_socket(raw_sock, server_side=True) as tls_sock:
            server_result['protocol'] = tls_sock.version() or 'unknown'
            cipher = tls_sock.cipher()
            server_result['cipher'] = cipher[0] if cipher else 'unknown'


def _serve_raw_tls_probe_once(
    listener: socket.socket,
    server_ctx: ssl.SSLContext,
    server_result: dict[str, str],
) -> None:
    try:
        raw_sock, _address = listener.accept()
        server_result['accepted'] = 'yes'
        _record_raw_tls_server_session(raw_sock, server_ctx, server_result)
    except (OSError, RuntimeError, ValueError) as exc:
        server_result['error'] = f'{type(exc).__name__}: {exc}'


def _run_raw_tls_client_handshake(
    host: str,
    ca_cert_path: Path,
    loopback_host: str,
    port: int,
    tls_max_version: ssl.TLSVersion,
) -> tuple[str, str]:
    client_ctx = ssl.create_default_context(cafile=str(ca_cert_path))
    client_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    client_ctx.maximum_version = tls_max_version
    with (
        socket.create_connection((loopback_host, port), timeout=5.0) as raw_sock,
        client_ctx.wrap_socket(raw_sock, server_hostname=host) as tls_sock,
    ):
        protocol = tls_sock.version() or 'unknown'
        cipher = tls_sock.cipher()
        return protocol, cipher[0] if cipher else 'unknown'


def _run_raw_tls_loopback_probe_sync(
    host: str,
    ca_cert_path: Path,
    cert_path: Path,
    key_path: Path,
    *,
    loopback_host: str = '127.0.0.1',
    tls_max_version: ssl.TLSVersion = PROXY_TLS_MAX_VERSION,
) -> tuple[bool, str]:
    """Test local TLS over a blocking loopback socket, without asyncio."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(cert_path), str(key_path))
    server_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    server_ctx.maximum_version = tls_max_version
    server_ctx.set_alpn_protocols(['http/1.1'])

    server_result: dict[str, str] = {}

    def _capture_sni(
        _ssl_obj: ssl.SSLSocket | ssl.SSLObject,
        server_name: str | None,
        _initial_ctx: ssl.SSLContext,
    ) -> None:
        server_result['sni'] = server_name or '<none>'

    # Python 3.14 typeshed currently models the third callback argument as an
    # SSLSocket even though CPython passes the initial SSLContext here.
    server_ctx.set_servername_callback(_capture_sni)  # pyright: ignore[reportArgumentType]

    family = socket.AF_INET6 if ':' in loopback_host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((loopback_host, 0))
    listener.listen(1)
    listener.settimeout(6.0)
    port = int(listener.getsockname()[1])

    def _serve_once() -> None:
        _serve_raw_tls_probe_once(listener, server_ctx, server_result)

    server_thread = threading.Thread(
        target=_serve_once,
        name='FleasionRawTlsLoopbackProbe',
        daemon=True,
    )
    server_thread.start()

    client_error: str | None = None
    client_protocol = 'unknown'
    client_cipher = 'unknown'
    try:
        client_protocol, client_cipher = _run_raw_tls_client_handshake(
            host, ca_cert_path, loopback_host, port, tls_max_version
        )
    except (OSError, RuntimeError, ValueError) as exc:
        client_error = f'{type(exc).__name__}: {exc}'
    finally:
        with contextlib.suppress(OSError):
            listener.close()

    server_thread.join(timeout=6.0)
    if client_error is not None:
        server_error = server_result.get('error', 'none')
        accepted = server_result.get('accepted', 'no')
        sni = server_result.get('sni', 'not-seen')
        alive = 'yes' if server_thread.is_alive() else 'no'
        return (
            False,
            (f'client={client_error}; server={server_error}; accepted={accepted}; '
            f'sni={sni}; server_thread_alive={alive}'),
        )
    if server_thread.is_alive():
        return False, 'server thread did not finish after client handshake'
    if 'error' in server_result:
        return False, f'server={server_result["error"]}'
    return True, f'protocol={client_protocol}; cipher={client_cipher}'


async def _run_raw_tls_loopback_probe(
    host: str,
    ca_cert_path: Path,
    cert_path: Path,
    key_path: Path,
    *,
    loopback_host: str = '127.0.0.1',
    tls_max_version: ssl.TLSVersion = PROXY_TLS_MAX_VERSION,
) -> tuple[bool, str]:
    loop = asyncio.get_running_loop()
    operation = partial(
        _run_raw_tls_loopback_probe_sync,
        host,
        ca_cert_path,
        cert_path,
        key_path,
        loopback_host=loopback_host,
        tls_max_version=tls_max_version,
    )
    return await loop.run_in_executor(None, operation)


def _advance_in_memory_tls_endpoint(
    endpoint: ssl.SSLObject,
    outbound: ssl.MemoryBIO,
    peer_inbound: ssl.MemoryBIO,
    *,
    done: bool,
) -> bool:
    if not done:
        try:
            endpoint.do_handshake()
        except ssl.SSLWantReadError:
            pass
        else:
            done = True
    pending = outbound.read()
    if pending:
        peer_inbound.write(pending)
    return done


def _run_in_memory_tls_probe_sync(
    host: str,
    ca_cert_path: Path,
    cert_path: Path,
    key_path: Path,
    tls_max_version: ssl.TLSVersion = PROXY_TLS_MAX_VERSION,
) -> tuple[bool, str]:
    """Exercise OpenSSL/certificates without touching the socket layer."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(cert_path), str(key_path))
    server_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    server_ctx.maximum_version = tls_max_version
    server_ctx.set_alpn_protocols(['http/1.1'])

    client_ctx = ssl.create_default_context(cafile=str(ca_cert_path))
    client_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    client_ctx.maximum_version = tls_max_version
    client_ctx.set_alpn_protocols(['http/1.1'])

    client_in = ssl.MemoryBIO()
    client_out = ssl.MemoryBIO()
    server_in = ssl.MemoryBIO()
    server_out = ssl.MemoryBIO()
    client = client_ctx.wrap_bio(
        client_in,
        client_out,
        server_side=False,
        server_hostname=host,
    )
    server = server_ctx.wrap_bio(server_in, server_out, server_side=True)
    client_done = False
    server_done = False

    try:
        for _ in range(64):
            client_done = _advance_in_memory_tls_endpoint(
                client,
                client_out,
                server_in,
                done=client_done,
            )
            server_done = _advance_in_memory_tls_endpoint(
                server,
                server_out,
                client_in,
                done=server_done,
            )
            if client_done and server_done:
                cipher = client.cipher()
                cipher_name = cipher[0] if cipher else 'unknown'
                return True, f'protocol={client.version() or "unknown"}; cipher={cipher_name}'
    except (OSError, RuntimeError, ValueError) as exc:
        return False, f'{type(exc).__name__}: {exc}'

    return False, 'handshake did not complete after 64 in-memory exchanges'


async def _run_in_memory_tls_probe(
    host: str,
    ca_cert_path: Path,
    cert_path: Path,
    key_path: Path,
    tls_max_version: ssl.TLSVersion = PROXY_TLS_MAX_VERSION,
) -> tuple[bool, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        _run_in_memory_tls_probe_sync,
        host,
        ca_cert_path,
        cert_path,
        key_path,
        tls_max_version,
    )


def _log_tls_self_test_passed(hosts: set[str], explicit_proxy: bool = False) -> None:
    mode = 'explicit proxy TLS' if explicit_proxy else 'TLS'
    log_buffer.log(
        'TLS',
        f'Startup {mode} self-test passed for {format_count(hosts, "intercept host")}',
    )


def _log_tls_self_test_failures(failures: list[str]) -> None:
    for failure in failures:
        log_buffer.log('TLS', f'Startup TLS self-test failed: {failure}')


async def _run_tls_self_test(
    hosts: set[str],
    ca_cert_path: Path,
    port: int,
    explicit_proxy: bool = False,
) -> bool:
    ok, failures = await _tls_self_test_result(hosts, ca_cert_path, port, explicit_proxy)
    if ok:
        _log_tls_self_test_passed(hosts, explicit_proxy)
        return True
    _log_tls_self_test_failures(failures)
    return False


async def _run_privileged_relay_tls_self_test(
    hosts: set[str],
    ca_cert_path: Path,
    port: int,
    *,
    attempts: int = 3,
    retry_delay: float = 0.5,
) -> tuple[bool, list[str]]:
    """Test the relay, retrying a small representative probe before giving up."""
    attempts = max(1, int(attempts))
    ok, failures = await _tls_self_test_result(hosts, ca_cert_path, port)
    if ok:
        _log_tls_self_test_passed(hosts)
        return True, []

    representative_hosts: set[str] = {min(hosts)} if hosts else set()
    full_failures = list(failures)
    last_failures = list(failures)
    for attempt in range(2, attempts + 1):
        log_buffer.log(
            'ProxyHelper',
            f'Privileged relay TLS probe attempt {attempt - 1}/{attempts} failed; '
            f'retrying in {retry_delay:.1f}s',
        )
        await asyncio.sleep(retry_delay)
        recovered, retry_failures = await _tls_self_test_result(
            representative_hosts,
            ca_cert_path,
            port,
        )
        last_failures = list(retry_failures)
        if not recovered:
            continue

        ok, failures = await _tls_self_test_result(hosts, ca_cert_path, port)
        if ok:
            log_buffer.log(
                'ProxyHelper',
                f'Privileged relay TLS probe recovered on attempt {attempt}/{attempts}',
            )
            _log_tls_self_test_passed(hosts)
            return True, []
        full_failures = list(failures)
        last_failures = list(failures)

    final_failures = full_failures
    if last_failures != full_failures:
        final_failures = [
            *full_failures,
            *(f'relay retry check: {failure}' for failure in last_failures),
        ]
    _log_tls_self_test_failures(final_failures)
    return False, final_failures


def _directory_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix='.fleasion-write-test-', dir=str(path))
        os.close(fd)
        Path(tmp_path).unlink()
    except OSError:
        return False
    else:
        return True


def _set_active_proxy_ca_dir(path: Path) -> Path:
    globals()['_ACTIVE_PROXY_CA_DIR'] = path
    return path


def _select_proxy_ca_dir() -> Path:
    """Return the CA directory to use for this run, falling back if legacy ownership blocks writes."""
    if _directory_is_writable(PROXY_CA_DIR):
        return _set_active_proxy_ca_dir(PROXY_CA_DIR)

    fallback = PROXY_CA_DIR.with_name(f'{PROXY_CA_DIR.name}_user')
    if _directory_is_writable(fallback):
        log_buffer.log(
            'Certificate',
            f'Configured CA directory is not writable ({PROXY_CA_DIR}); using {fallback}',
        )
        return _set_active_proxy_ca_dir(fallback)

    return _set_active_proxy_ca_dir(PROXY_CA_DIR)


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
        for cmd in (
            ['dscacheutil', '-flushcache'],
            ['killall', '-HUP', 'mDNSResponder'],
        ):
            try:
                _run_binary_command(cmd, timeout=5)
                flushed = True
            except (OSError, subprocess.SubprocessError) as exc:
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
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                result = _run_binary_command(cmd, timeout=5)
                if result.returncode == 0:
                    flushed = True
                    break
        if flushed:
            log_buffer.log('Hosts', 'DNS cache flushed')
        else:
            log_buffer.log(
                'Hosts',
                'DNS cache flush skipped: no supported Linux flush command succeeded',
            )
        return

    # Primary: in-process DLL call — fast, no subprocess, immune to AV process blocks.
    try:
        _windows_ctypes.windll.LoadLibrary('dnsapi.dll').DnsFlushResolverCache()
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        log_buffer.log('Hosts', f'DnsFlushResolverCache failed, falling back to ipconfig: {exc}')
    else:
        log_buffer.log('Hosts', 'DNS cache flushed')
        return

    # Fallback: subprocess (may be blocked or slow under security software).
    try:
        _run_binary_command(
            ['ipconfig', '/flushdns'],
            creationflags=_windows_subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
        log_buffer.log('Hosts', 'DNS cache flushed (via ipconfig fallback)')
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('Hosts', f'DNS flush failed (non-fatal): {exc}')


# ---------------------------------------------------------------------------
# Reboot-time crash guard (PendingFileRenameOperations)
# ---------------------------------------------------------------------------


def _pid_is_alive(pid: int) -> bool:
    """Return True if the process with *pid* is still running."""
    if not IS_WINDOWS:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        else:
            return True

    process_query_limited_information = 0x1000
    still_active = 259
    inherit_handle = False
    handle = _windows_ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information,
        inherit_handle,
        pid,
    )
    if not handle:
        return False
    try:
        code = ctypes.c_ulong(0)
        _windows_ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == still_active
    finally:
        _windows_ctypes.windll.kernel32.CloseHandle(handle)


def _other_proxy_owner_alive() -> bool:
    """Return True if another elevated Fleasion instance currently owns the proxy.

    Checked at startup so we never delete another instance's watchdog or
    hosts entries while it is still running.
    """
    try:
        pid = int(_PROXY_OWNER_PID_FILE.read_text().strip())
        return pid != os.getpid() and _pid_is_alive(pid)
    except OSError, ValueError:
        return False


def _nt_path(p: Path) -> str:
    """Return the NT namespace path required by PendingFileRenameOperations."""
    return f'\\??\\{p}'



def _pending_rename_entries(key: _RegistryKey) -> list[str] | None:
    try:
        existing, _ = winreg.QueryValueEx(key, _PENDING_RENAME_VALUE)
    except FileNotFoundError:
        return None
    return list(_preserve_str_list(existing))


def _without_pending_rename_source(entries: list[str], source: str) -> list[str]:
    filtered: list[str] = []
    index = 0
    while index < len(entries):
        if index + 1 < len(entries):
            if entries[index].lower() == source.lower():
                index += 2
                continue
            filtered.extend((entries[index], entries[index + 1]))
            index += 2
        else:
            filtered.append(entries[index])
            index += 1
    return filtered


def _schedule_pending_hosts_rename(source: str, destination: str) -> None:
    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        _PENDING_RENAME_KEY,
        access=winreg.KEY_ALL_ACCESS,
    ) as key:
        entries = _pending_rename_entries(key) or []
        filtered = _without_pending_rename_source(entries, source)
        filtered.extend((source, destination))
        winreg.SetValueEx(key, _PENDING_RENAME_VALUE, 0, winreg.REG_MULTI_SZ, filtered)


def _cancel_pending_hosts_rename(source: str) -> bool:
    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        _PENDING_RENAME_KEY,
        access=winreg.KEY_ALL_ACCESS,
    ) as key:
        entries = _pending_rename_entries(key)
        if entries is None:
            return False
        filtered = _without_pending_rename_source(entries, source)
        if filtered:
            winreg.SetValueEx(key, _PENDING_RENAME_VALUE, 0, winreg.REG_MULTI_SZ, filtered)
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, _PENDING_RENAME_VALUE)
    return True


def _write_reboot_cleanup_hosts_copy() -> None:
    try:
        original = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except OSError:
        original = ''
    clean_content = ''.join(
        line
        for line in original.splitlines(keepends=True)
        if _HOSTS_MARKER not in line
        and not _hosts_line_has_target_loopback(line, set(INTERCEPT_HOSTS))
    )
    _TEMP_CLEAN_HOSTS.write_text(clean_content, encoding='utf-8')


def _schedule_hosts_cleanup_on_reboot() -> None:
    """Register a PendingFileRenameOperations hosts cleanup for the next Windows boot."""
    if not IS_WINDOWS:
        return
    if hosts_file_is_oversized():
        log_buffer.log(
            'Hosts',
            'Skipped reboot hosts cleanup because the hosts file is too large to read safely',
        )
        return
    try:
        _write_reboot_cleanup_hosts_copy()
        _schedule_pending_hosts_rename(_nt_path(_TEMP_CLEAN_HOSTS), _nt_path(HOSTS_FILE))
        log_buffer.log('Hosts', 'Crash guard: hosts cleanup scheduled for next reboot')
    except (OSError, TypeError, ValueError) as exc:
        log_buffer.log('Hosts', f'Could not schedule reboot cleanup (non-fatal): {exc}')


def _cancel_hosts_cleanup_on_reboot() -> None:
    """Remove the PendingFileRenameOperations hosts cleanup after a clean exit."""
    if not IS_WINDOWS:
        return
    try:
        if not _cancel_pending_hosts_rename(_nt_path(_TEMP_CLEAN_HOSTS)):
            return
        with contextlib.suppress(OSError):
            _TEMP_CLEAN_HOSTS.unlink(missing_ok=True)
        log_buffer.log('Hosts', 'Crash guard: reboot cleanup cancelled (clean exit)')
    except (OSError, TypeError, ValueError) as exc:
        log_buffer.log('Hosts', f'Could not cancel reboot cleanup (non-fatal): {exc}')


# ---------------------------------------------------------------------------
# Admin check
# ---------------------------------------------------------------------------


def _is_admin() -> bool:
    if IS_MACOS or IS_LINUX:
        return hasattr(os, 'geteuid') and os.geteuid() == 0
    try:
        return bool(_windows_ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _use_linux_privileged_helper() -> bool:
    return IS_LINUX and not _is_admin()


def _ensure_linux_system_trust_for_hosts(hosts: set[str], ca_cert_path: Path) -> bool:
    """Best-effort Linux system trust install before intercepting WebView-visible hosts."""
    if not IS_LINUX or not (set(hosts) & USERNAME_SPOOFER_INTERCEPT_HOSTS):
        return True
    if not ca_cert_path.is_file():
        log_buffer.log(
            'Certificate',
            f'Linux system trust-store install failed: CA certificate not found: {ca_cert_path}',
        )
        return False

    install_ca_into_linux_trust = cast(
        'Callable[..., object]',
        _lazy_attr('fleasion.utils.linux_proxy_helper', 'install_ca_into_linux_trust'),
    )

    details_value = install_ca_into_linux_trust(
        ca_cert_path,
        install_system=True,
        install_nss=False,
    )
    details = (
        cast('dict[object, object]', details_value) if isinstance(details_value, dict) else None
    )
    system_value = details.get('system') if details is not None else None
    system = cast('dict[object, object]', system_value) if isinstance(system_value, dict) else None
    if system is not None and system.get('ok'):
        return True
    if system is not None and system.get('error') == 'no_supported_system_trust_store':
        log_buffer.log(
            'Certificate',
            'Linux system trust-store install is unsupported on this distro; continuing with Sober cacert.pem trust only',
        )
        return True

    error = system.get('error') or system if system is not None else None
    log_buffer.log(
        'Certificate',
        f'Linux system trust-store install failed for WebView-visible intercepts: {error or details_value}',
    )
    return False


def _extract_exe_from_command(command: str) -> Path | None:
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
        result = _run_text_command(
            ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
            creationflags=_windows_subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return 'Unknown'

    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith('INFO:'):
            return 'Unknown'
        with contextlib.suppress(csv.Error, StopIteration):
            row = next(csv.reader([line]))
            if row and row[0]:
                return row[0].strip()
    return 'Unknown'


def _list_port_listeners_powershell(port: int) -> list[_PortListener]:
    """Return listening process info for a TCP port via Get-NetTCPConnection."""
    ps_cmd = (
        f'$rows=Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | '
        'ForEach-Object { '
        '$p=Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; '
        '[PSCustomObject]@{ '
        'LocalAddress=$_.LocalAddress; '
        'PID=$_.OwningProcess; '
        "ProcessName=$(if($p){$p.ProcessName}else{'Unknown'}) "
        '} '
        '}; '
        'if($rows){$rows | ConvertTo-Json -Compress}'
    )
    try:
        result = _run_text_command(
            ['powershell', '-NoProfile', '-Command', ps_cmd],
            creationflags=_windows_subprocess.CREATE_NO_WINDOW,
            timeout=6,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if result.returncode != 0:
        return []

    payload = (result.stdout or '').strip()
    if not payload:
        return []

    try:
        parsed = cast('object', json.loads(payload))
    except json.JSONDecodeError:
        return []

    rows: list[object] = cast('list[object]', parsed) if isinstance(parsed, list) else [parsed]
    listeners: list[_PortListener] = []
    for row_value in rows:
        if not _is_str_object_dict(row_value):
            continue
        row = row_value
        try:
            pid = int(cast('int | str | bytes', row.get('PID', 0)))
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            continue

        process_name = str(row.get('ProcessName') or 'Unknown').strip() or 'Unknown'
        local_address = str(row.get('LocalAddress') or _UNSPECIFIED_IPV4).strip() or _UNSPECIFIED_IPV4
        listeners.append(
            {
                'pid': pid,
                'process_name': process_name,
                'local_address': local_address,
            }
        )
    return listeners


def _list_port_listeners_netstat(port: int) -> list[_PortListener]:
    """Fallback listener lookup using netstat + tasklist."""
    try:
        result = _run_text_command(
            ['netstat', '-aon', '-p', 'tcp'],
            creationflags=_windows_subprocess.CREATE_NO_WINDOW,
            timeout=6,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    listeners: list[_PortListener] = []
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
        if not (local_addr.endswith((suffix, f']{suffix}'))):
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


def _list_port_listeners(port: int) -> list[_PortListener]:
    """Return unique listener records for a TCP port."""
    if IS_MACOS or IS_LINUX:
        try:
            result = _run_text_command(
                ['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN'],
                timeout=6,
            )
        except (OSError, subprocess.SubprocessError):
            result = None

        listeners: list[_PortListener] = []
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
                        'local_address': local_address or _UNSPECIFIED_IPV4,
                    }
                )
    else:
        listeners = _list_port_listeners_powershell(port)
        if not listeners:
            listeners = _list_port_listeners_netstat(port)

    unique: list[_PortListener] = []
    seen: set[tuple[int, str, str]] = set()
    for entry in listeners:
        pid = int(entry.get('pid', 0) or 0)
        process_name = str(entry.get('process_name') or 'Unknown').strip() or 'Unknown'
        local_address = str(entry.get('local_address') or _UNSPECIFIED_IPV4).strip() or _UNSPECIFIED_IPV4
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
_HOSTS_WRITE_DELAY = 0.25  # seconds between direct-write retries
_HOSTS_IPV4_LOOPBACK = '127.0.0.1'
_HOSTS_IPV6_LOOPBACK = '::1'
_HOSTS_LOOPBACK_IPS = frozenset({_HOSTS_IPV4_LOOPBACK, _HOSTS_IPV6_LOOPBACK})
_HOSTS_ACTIVE_LOOPBACK_IPS: tuple[str, ...] | None = None
# Voidstrap has been observed leaving stale Roblox edge-IP mappings tagged with
# this marker.  They prevent our loopback proxy from owning the same hostnames.
# Keep this deliberately narrow: only lines with this exact marker *and* one
# of the hostnames we are about to intercept are eligible for cleanup.
_VOIDSTRAP_GU_ACC_MARKER = '#gu_acc'


def _required_hosts_loopbacks() -> tuple[str, ...]:
    """Return loopback mappings Fleasion should own in the hosts file."""
    if _HOSTS_ACTIVE_LOOPBACK_IPS:
        return _HOSTS_ACTIVE_LOOPBACK_IPS
    if IS_WINDOWS:
        return (_HOSTS_IPV4_LOOPBACK, _HOSTS_IPV6_LOOPBACK)
    return (_HOSTS_IPV4_LOOPBACK,)


def _set_active_hosts_loopbacks(
    loopbacks: tuple[str, ...] | list[str] | set[str] | None,
) -> None:
    if not loopbacks:
        globals()['_HOSTS_ACTIVE_LOOPBACK_IPS'] = None
        return
    ordered: list[str] = [ip for ip in (_HOSTS_IPV4_LOOPBACK, _HOSTS_IPV6_LOOPBACK) if ip in loopbacks]
    globals()['_HOSTS_ACTIVE_LOOPBACK_IPS'] = tuple(ordered) or None


def _is_hosts_loopback_ip(ip: str) -> bool:
    return str(ip or '').strip().lower() in _HOSTS_LOOPBACK_IPS


def _parse_active_hosts_entries(content: str) -> dict[str, list[_HostsEntry]]:
    """Parse active hosts-file mappings keyed by lowercase hostname."""
    entries: dict[str, list[_HostsEntry]] = {}
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


def _hosts_conflicts(
    hosts: set[str], entries: dict[str, list[_HostsEntry]]
) -> list[tuple[str, _HostsEntry]]:
    conflicts: list[tuple[str, _HostsEntry]] = []
    for host in sorted(hosts):
        conflicts.extend((host, entry) for entry in entries.get(host.lower(), []) if not _is_hosts_loopback_ip(entry.get('ip', '')))
    return conflicts


def _record_hosts_error(error_details: _ErrorDetails | None, exc_or_text: object) -> None:
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


def hosts_file_size() -> int | None:
    """Return the system hosts-file size without opening or reading it."""
    try:
        return HOSTS_FILE.stat().st_size
    except FileNotFoundError:
        return 0
    except OSError:
        return None


def _record_oversized_hosts_error(error_details: _ErrorDetails | None, size: int) -> None:
    if error_details is None:
        return
    error_details.clear()
    error_details.update(
        {
            'error_code': 'hosts_file_too_large',
            'hosts_path': str(HOSTS_FILE),
            'hosts_directory': str(HOSTS_FILE.parent),
            'hosts_size_bytes': size,
            'hosts_size_limit_bytes': _HOSTS_FILE_REPAIR_THRESHOLD_BYTES,
            'error': (
                f'Hosts file is {size} bytes; Fleasion will not read files larger than '
                f'{_HOSTS_FILE_REPAIR_THRESHOLD_BYTES} bytes without repair.'
            ),
            'notify_user': True,
        }
    )


def hosts_file_is_oversized(error_details: _ErrorDetails | None = None) -> bool:
    """Return whether the hosts file is too large for whole-file operations."""
    size = hosts_file_size()
    if size is None or size <= _HOSTS_FILE_REPAIR_THRESHOLD_BYTES:
        return False
    _record_oversized_hosts_error(error_details, size)
    return True


def _log_hosts_conflicts(conflicts: list[tuple[str, _HostsEntry]]) -> None:
    for host, entry in conflicts:
        log_buffer.log(
            'Hosts',
            f'Hosts conflict for {host}: line {entry["line_no"]}: {entry["line"]}',
        )


def _verify_hosts_entries(hosts: set[str], error_details: _ErrorDetails | None = None) -> bool:
    """Verify exact active hosts mappings after a write and DNS flush."""
    if hosts_file_is_oversized(error_details):
        return False
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

    missing: list[str] = []
    required_ips = _required_hosts_loopbacks()
    for host in sorted(hosts):
        host_entries = entries.get(host.lower(), [])
        missing.extend(f'{host}->{ip}' for ip in required_ips if not any(str(entry.get('ip', '')).lower() == ip for entry in host_entries))

    if missing:
        log_buffer.log(
            'Hosts',
            f'Hosts verification failed: missing active mappings for {", ".join(missing)}',
        )
        _record_hosts_error(
            error_details, f'missing active hosts mappings for {", ".join(missing)}'
        )
        return False

    log_buffer.log('Hosts', f'Hosts verification passed for: {", ".join(sorted(hosts))}')
    return True


def _hosts_line_has_target_loopback(raw_line: str, hosts: set[str]) -> bool:
    active = raw_line.split('#', 1)[0].strip()
    if not active:
        return False
    parts = active.split()
    if len(parts) < 2 or not _is_hosts_loopback_ip(parts[0]):
        return False
    target_hosts = {host.lower() for host in hosts}
    return any(host.lower() in target_hosts for host in parts[1:])


def has_stale_hosts_entries(hosts: set[str] | None = None) -> bool:
    """Return whether Fleasion-owned hosts entries need privileged cleanup."""
    if hosts_file_is_oversized():
        return True
    target_hosts = set(hosts or INTERCEPT_HOSTS)
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return False
    return any(
        _HOSTS_MARKER in line or _hosts_line_has_target_loopback(line, target_hosts)
        for line in existing.splitlines()
    )


def _hosts_file_loopback_hosts(hosts: set[str]) -> set[str]:
    """Return requested hosts that already have active loopback mappings."""
    if hosts_file_is_oversized():
        return set()
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return set()
    entries = _parse_active_hosts_entries(existing)
    present: set[str] = set()
    for host in sorted(hosts):
        if any(
            _is_hosts_loopback_ip(entry.get('ip', '')) for entry in entries.get(host.lower(), [])
        ):
            present.add(host)
    return present


def _is_voidstrap_gu_acc_line(raw_line: str, hosts: set[str]) -> bool:
    """Return whether *raw_line* is a known stale Voidstrap hosts entry.

    Some affected files prefix an existing line with ``#gu_acc`` while other
    lines end in that marker.  Matching host tokens, rather than attempting to
    parse the line as an active mapping, handles both forms without touching
    unrelated ``#gu_acc`` content.
    """
    if _VOIDSTRAP_GU_ACC_MARKER not in raw_line.lower():
        return False
    target_hosts = {host.lower() for host in hosts}
    return any(token.lower() in target_hosts for token in raw_line.split())


def _remove_voidstrap_gu_acc_entries(
    hosts: set[str], error_details: _ErrorDetails | None = None
) -> bool:
    """Remove known Voidstrap ``#gu_acc`` entries for Fleasion proxy hosts."""
    if hosts_file_is_oversized(error_details):
        return False
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return True
    except OSError as exc:
        _record_hosts_error(error_details, exc)
        log_buffer.log('Hosts', f'Cannot read hosts file for Voidstrap cleanup: {exc}')
        return False

    lines = existing.splitlines(keepends=True)
    filtered = [line for line in lines if not _is_voidstrap_gu_acc_line(line, hosts)]
    removed_count = len(lines) - len(filtered)
    if not removed_count:
        return True

    try:
        _write_hosts_file(''.join(filtered))
    except OSError as exc:
        _record_hosts_error(error_details, exc)
        log_buffer.log('Hosts', f'Failed to remove Voidstrap #gu_acc entries: {exc}')
        return False

    log_buffer.log(
        'Hosts',
        f'Removed {removed_count} stale Voidstrap #gu_acc hosts entr'
        f'{"y" if removed_count == 1 else "ies"}',
    )
    return True


def _spilled_hosts_line_should_remove(line_file: _BinaryLineFile, hosts: set[str]) -> bool:
    """Classify an unterminated hosts line without loading it into memory."""
    line_file.seek(0)
    prefix = line_file.read(4096)
    first_token = prefix.lstrip(b' \t\r\n').split(None, 1)[0].lower() if prefix.strip() else b''
    is_loopback = first_token in {b'127.0.0.1', b'::1'}

    target_patterns = [
        re.compile(rb'(?<!\S)' + re.escape(host.encode('ascii')) + rb'(?!\S)', re.IGNORECASE)
        for host in hosts
    ]
    marker = _HOSTS_MARKER.encode('ascii')
    voidstrap_marker = _VOIDSTRAP_GU_ACC_MARKER.encode('ascii')
    marker_tail = b''
    voidstrap_tail = b''
    target_tail = b''
    active_tail = b''
    marker_found = False
    voidstrap_marker_found = False
    target_found = False
    active_target_found = False
    active_done = False
    max_target_len = max((len(host.encode('ascii')) for host in hosts), default=1)

    line_file.seek(0)
    while chunk := line_file.read(64 * 1024):
        marker_scan = marker_tail + chunk
        if marker in marker_scan:
            marker_found = True
        marker_tail = marker_scan[-(len(marker) - 1) :]

        voidstrap_scan = voidstrap_tail + chunk.lower()
        if voidstrap_marker in voidstrap_scan:
            voidstrap_marker_found = True
        voidstrap_tail = voidstrap_scan[-(len(voidstrap_marker) - 1) :]

        target_scan = target_tail + chunk
        if any(pattern.search(target_scan) for pattern in target_patterns):
            target_found = True
        target_tail = target_scan[-(max_target_len + 1) :]

        if is_loopback and not active_done:
            active_scan = active_tail + chunk
            comment_start = active_scan.find(b'#')
            if comment_start >= 0:
                active_scan = active_scan[:comment_start]
                active_done = True
            if any(pattern.search(active_scan) for pattern in target_patterns):
                active_target_found = True
            if not active_done:
                active_tail = active_scan[-(max_target_len + 1) :]

    return (
        marker_found
        or (is_loopback and active_target_found)
        or (voidstrap_marker_found and target_found)
    )


def _repair_hosts_file_with_temp(
    fd: int,
    temp_path: Path,
    target_hosts: set[str],
    error_details: _ErrorDetails | None,
    *,
    require_safe_size: bool,
) -> bool:
    original_mode: int | None = None
    removed_blank_lines = 0
    removed_proxy_lines = 0
    output_size = 0
    try:
        original_mode = stat.S_IMODE(HOSTS_FILE.stat().st_mode)
    except OSError as exc:
        _record_hosts_error(error_details, exc)
        return False
    with (
        HOSTS_FILE.open('rb') as source,
        os.fdopen(fd, 'wb') as target,
        contextlib.ExitStack() as spill_stack,
    ):
        # Process blocks so a file containing billions of blank lines does
        # not turn into billions of Python loop iterations. Only the
        # incomplete final line of each block is carried forward. If that
        # line has no terminator, it is spilled to disk rather than
        # accumulated in memory.
        pending = b''
        pending_spill = None
        stop_after_oversized = False

        def _write_repair_line(raw_line: bytes) -> None:
            nonlocal removed_blank_lines, removed_proxy_lines, stop_after_oversized
            if not raw_line.strip(b' \t\r\n'):
                removed_blank_lines += 1
                return
            line = raw_line.decode('utf-8', errors='replace')
            if (
                _HOSTS_MARKER in line
                or _hosts_line_has_target_loopback(line, target_hosts)
                or _is_voidstrap_gu_acc_line(line, target_hosts)
            ):
                removed_proxy_lines += 1
                return
            target.write(raw_line)
            if require_safe_size and target.tell() > _HOSTS_FILE_REPAIR_THRESHOLD_BYTES:
                stop_after_oversized = True

        def _process_complete_block(data: bytes) -> bytes:
            nonlocal removed_blank_lines
            if b'\n' not in data:
                return data
            last_newline = data.rfind(b'\n')
            complete, remainder = data[: last_newline + 1], data[last_newline + 1 :]
            complete, blank_count = re.subn(rb'(?m)^[ \t]*(?:\r\n|\n|\r)', b'', complete)
            removed_blank_lines += blank_count
            for raw_line in complete.splitlines(keepends=True):
                _write_repair_line(raw_line)
                if stop_after_oversized:
                    break
            return remainder

        while not stop_after_oversized and (chunk := source.read(1024 * 1024)):
            if pending_spill is not None:
                newline = chunk.find(b'\n')
                if newline < 0:
                    pending_spill.write(chunk)
                    continue
                pending_spill.write(chunk[: newline + 1])
                pending_spill.flush()
                if _spilled_hosts_line_should_remove(pending_spill, target_hosts):
                    removed_proxy_lines += 1
                else:
                    pending_spill.seek(0)
                    shutil.copyfileobj(pending_spill, target, length=64 * 1024)
                    if require_safe_size and target.tell() > _HOSTS_FILE_REPAIR_THRESHOLD_BYTES:
                        stop_after_oversized = True
                pending_spill.close()
                pending_spill = None
                pending = b''
                if stop_after_oversized:
                    break
                chunk = chunk[newline + 1 :]
                if not chunk:
                    continue

            if pending:
                newline = chunk.find(b'\n')
                if newline < 0:
                    if len(pending) + len(chunk) <= 1024 * 1024:
                        pending += chunk
                    else:
                        pending_spill = spill_stack.enter_context(tempfile.TemporaryFile(mode='w+b'))
                        pending_spill.write(pending)
                        pending_spill.write(chunk)
                        pending = b''
                    continue
                _write_repair_line(pending + chunk[: newline + 1])
                pending = b''
                if stop_after_oversized:
                    break
                chunk = chunk[newline + 1 :]
                if not chunk:
                    continue

            if b'\n' in chunk:
                pending = _process_complete_block(chunk)
            elif chunk.strip(b' \t\r'):
                pending = chunk
            else:
                removed_blank_lines += 1

        if not stop_after_oversized:
            if pending_spill is not None:
                pending_spill.flush()
                if _spilled_hosts_line_should_remove(pending_spill, target_hosts):
                    removed_proxy_lines += 1
                else:
                    pending_spill.seek(0)
                    shutil.copyfileobj(pending_spill, target, length=64 * 1024)
                pending_spill.close()
                pending_spill = None
            elif pending:
                _write_repair_line(pending)
        if pending_spill is not None:
            pending_spill.close()
        output_size = target.tell()

    output_is_oversized = output_size > _HOSTS_FILE_REPAIR_THRESHOLD_BYTES
    if output_is_oversized and require_safe_size:
        _record_oversized_hosts_error(error_details, output_size)
        if error_details is not None:
            error_details.update(
                {
                    'error_code': 'hosts_file_repair_failed',
                    'repair_attempted': True,
                    'repair_output_size_bytes': output_size,
                }
            )
        return False

    Path(temp_path).chmod(original_mode)

    timestamp = datetime.now(UTC).astimezone().strftime('%Y%m%d-%H%M%S')
    backup_path = HOSTS_FILE.with_name(f'{HOSTS_FILE.name}.fleasion-backup-{timestamp}')
    suffix = 1
    while backup_path.exists():
        backup_path = HOSTS_FILE.with_name(
            f'{HOSTS_FILE.name}.fleasion-backup-{timestamp}-{suffix}'
        )
        suffix += 1

    Path(HOSTS_FILE).replace(backup_path)
    try:
        Path(temp_path).replace(HOSTS_FILE)
    except Exception:
        Path(backup_path).replace(HOSTS_FILE)
        raise

    repaired_size = hosts_file_size()
    if repaired_size is None or (
        require_safe_size and repaired_size > _HOSTS_FILE_REPAIR_THRESHOLD_BYTES
    ):
        if error_details is not None:
            error_details.update(
                {
                    'error_code': 'hosts_file_repair_failed',
                    'repair_attempted': True,
                    'repair_output_size_bytes': repaired_size,
                }
            )
        return False
    backup_deleted = False
    try:
        backup_path.unlink(missing_ok=True)
        backup_deleted = True
    except OSError as exc:
        # The repaired hosts file is already valid; a backup-delete
        # failure must not roll back a successful repair. Keep the issue
        # visible in the log so it can be cleaned up later.
        log_buffer.log('Hosts', f'Could not remove temporary repair backup: {exc}')
    if error_details is not None:
        error_details.clear()
        error_details.update(
            {
                'repair_attempted': True,
                'repair_succeeded': True,
                'backup_path': str(backup_path),
                'backup_deleted': backup_deleted,
                'hosts_path': str(HOSTS_FILE),
                'hosts_size_bytes': repaired_size,
                'repair_output_oversized': repaired_size > _HOSTS_FILE_REPAIR_THRESHOLD_BYTES,
                'removed_blank_lines': removed_blank_lines,
                'removed_proxy_lines': removed_proxy_lines,
            }
        )
    log_buffer.log(
        'Hosts',
        f'Repaired hosts file: removed {removed_blank_lines} blank lines and '
        f'{removed_proxy_lines} Fleasion-owned lines; temporary backup deleted={backup_deleted}',
    )
    return True


def repair_hosts_file(
    hosts: set[str] | None = None,
    error_details: _ErrorDetails | None = None,
    *,
    require_safe_size: bool = True,
) -> bool:
    """Stream-repair an oversized hosts file without loading it into memory.

    Blank lines and Fleasion-owned/stale mappings are removed. Every other
    non-empty line is copied byte-for-byte so user and VM mappings remain
    intact. The original file is renamed to a timestamped backup before the
    repaired file is atomically installed.
    """
    target_hosts = set(hosts or INTERCEPT_HOSTS)
    size = hosts_file_size()
    if size is None or size == 0:
        if size is None:
            _record_hosts_error(error_details, 'Could not stat the hosts file before repair.')
        return size == 0

    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            dir=HOSTS_FILE.parent,
            prefix='.fleasion_hosts_repair_',
        )
        temp_path = Path(temp_name)
        return _repair_hosts_file_with_temp(
            fd,
            temp_path,
            target_hosts,
            error_details,
            require_safe_size=require_safe_size,
        )
    except OSError as exc:
        _record_hosts_error(error_details, exc)
        if error_details is not None:
            error_details.update(
                {'error_code': 'hosts_file_repair_failed', 'repair_attempted': True}
            )
        return False
    finally:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)


def _atomic_replace_hosts_file(content: str) -> None:
    fd, raw_tmp_path = tempfile.mkstemp(dir=HOSTS_FILE.parent, prefix='.fleasion_hosts_')
    tmp_path = Path(raw_tmp_path)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(content)
        tmp_path.replace(HOSTS_FILE)
        log_buffer.log(
            'Hosts',
            'Hosts file updated via atomic rename (security software workaround)',
        )
    except OSError:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


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
        except OSError:
            raise  # non-permission errors are not retryable
        else:
            return

    # --- Strategy 2: temp-file + atomic rename ---
    log_buffer.log(
        'Hosts',
        f'Direct write failed after {_HOSTS_WRITE_RETRIES} attempts — '
        'attempting atomic rename workaround for security software lock…',
    )
    try:
        _atomic_replace_hosts_file(content)
    except OSError as exc:
        msg = (
            f'Cannot write hosts file — all strategies exhausted. '
            f'If Webroot, Kaspersky, or another security product is installed, open its settings '
            f'and try to disable any setting relating to protecting the hosts file. '
            f'Last direct-write error: {last_exc}; rename error: {exc}'
        )
        raise PermissionError(msg) from exc


def _add_hosts_entries_with_macos_helper(
    hosts: set[str],
    error_details: _ErrorDetails | None,
) -> bool:
    helper_apply_hosts = cast(
        'Callable[[set[str]], bool]',
        _lazy_attr('fleasion.utils.macos_proxy_helper', 'helper_apply_hosts'),
    )
    if not helper_apply_hosts(set(hosts)):
        _record_hosts_error(error_details, 'macOS proxy helper failed to apply hosts entries')
        return False
    for host in sorted(hosts):
        log_buffer.log('Hosts', f'Added redirect through macOS helper: {host} -> 127.0.0.1')
    return True


def _read_hosts_file_for_add(error_details: _ErrorDetails | None) -> str | None:
    try:
        return HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
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
            "# lines or following the machine name denoted by a '#' symbol.\n"
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
        except OSError as exc:
            log_buffer.log('Hosts', f'Failed to create hosts file: {exc}')
            _record_hosts_error(error_details, exc)
            return None
        log_buffer.log('Hosts', 'hosts file was missing — created new default hosts file')
        return existing
    except OSError as exc:
        log_buffer.log('Hosts', f'Cannot read hosts file: {exc}')
        _record_hosts_error(error_details, exc)
        return None


def _prepare_hosts_entries_to_add(
    hosts: set[str],
    error_details: _ErrorDetails | None,
) -> tuple[str, list[str]] | None:
    existing = _read_hosts_file_for_add(error_details)
    if existing is None:
        return None

    # Remove only the known Voidstrap marker before evaluating conflicts
    if not _remove_voidstrap_gu_acc_entries(hosts, error_details=error_details):
        return None
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        log_buffer.log('Hosts', f'Cannot reread hosts file after Voidstrap cleanup: {exc}')
        _record_hosts_error(error_details, exc)
        return None

    entries = _parse_active_hosts_entries(existing)
    conflicts = _hosts_conflicts(hosts, entries)
    if conflicts:
        _log_hosts_conflicts(conflicts)
        _record_hosts_error(error_details, 'active conflicting hosts mappings detected')
        return None

    lines_to_add: list[str] = []
    required_ips = _required_hosts_loopbacks()
    for host in sorted(hosts):
        host_entries = entries.get(host.lower(), [])
        for ip in required_ips:
            entry = f'{ip} {host} {_HOSTS_MARKER}'
            if not any(str(e.get('ip', '')).lower() == ip for e in host_entries):
                lines_to_add.append(entry)
    return existing, lines_to_add


def _commit_hosts_entries(
    existing: str,
    lines_to_add: list[str],
    error_details: _ErrorDetails | None,
) -> bool:
    if not lines_to_add:
        log_buffer.log('Hosts', 'Exact active hosts entries already present, skipping')
        return True

    new_content = existing.rstrip('\n') + '\n' + '\n'.join(lines_to_add) + '\n'
    candidate_size = len(new_content.encode('utf-8'))
    if candidate_size > _HOSTS_FILE_REPAIR_THRESHOLD_BYTES:
        _record_oversized_hosts_error(error_details, candidate_size)
        if error_details is not None:
            error_details.update(
                {
                    'error_code': 'hosts_entries_would_exceed_limit',
                    'error': (
                        f'Adding Fleasion mappings would make the hosts file {candidate_size} '
                        f'bytes, above the {_HOSTS_FILE_REPAIR_THRESHOLD_BYTES}-byte safety limit.'
                    ),
                    'hosts_size_before_write_bytes': len(existing.encode('utf-8')),
                }
            )
        log_buffer.log(
            'Hosts',
            'Refused hosts update because the new Fleasion mappings would exceed the safety limit',
        )
        return False

    try:
        _write_hosts_file(new_content)
    except PermissionError as exc:
        log_buffer.log('Hosts', f'Permission denied writing hosts file: {exc}')
        _record_hosts_error(error_details, exc)
        return False
    except OSError as exc:
        log_buffer.log('Hosts', f'Failed to write hosts file: {exc}')
        _record_hosts_error(error_details, exc)
        return False

    for entry in lines_to_add:
        ip, host = entry.split()[:2]
        log_buffer.log('Hosts', f'Added redirect: {host} -> {ip}')
    return True


def _add_hosts_entries(hosts: set[str], error_details: _ErrorDetails | None = None) -> bool:
    """Append redirect entries for *hosts* to the system hosts file.

    Returns True on success.  Skips entries already present.
    Creates the hosts file from the Windows default if it is missing.
    If *error_details* is provided and a write fails with PermissionError,
    it is populated with metadata for user-facing error notifications.
    """
    if IS_MACOS and not _is_admin():
        return _add_hosts_entries_with_macos_helper(hosts, error_details)
    if hosts_file_is_oversized(error_details):
        return False

    prepared = _prepare_hosts_entries_to_add(hosts, error_details)
    if prepared is None:
        return False
    existing, lines_to_add = prepared
    return _commit_hosts_entries(existing, lines_to_add, error_details)


def _remove_hosts_entries(hosts: set[str], error_details: _ErrorDetails | None = None) -> bool:
    """Remove any hosts file entries we previously added.

    Returns True if the hosts file is clean (entries removed or were already
    absent).  Returns False if the write failed — callers must NOT cancel the
    reboot guard in that case, so the next boot still cleans up automatically.
    """
    if IS_MACOS and not _is_admin():
        helper_clear_hosts = cast(
            'Callable[[], bool]',
            _lazy_attr('fleasion.utils.macos_proxy_helper', 'helper_clear_hosts'),
        )

        if helper_clear_hosts():
            log_buffer.log('Hosts', 'Removed proxy hosts entries through macOS helper')
            return True
        _record_hosts_error(error_details, 'macOS proxy helper failed to clear hosts entries')
        return False

    if hosts_file_is_oversized(error_details):
        # The file may remain above the advisory threshold because it contains
        # legitimate VM/user mappings, but stale Fleasion redirects must still
        # be removed during rollback and shutdown. The bounded repair preserves
        # those mappings and atomically installs the cleaned file.
        return repair_hosts_file(
            hosts,
            error_details=error_details,
            require_safe_size=False,
        )

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
        existing = ''
        read_failed = True
    else:
        read_failed = False

    lines = existing.splitlines(keepends=True)
    filtered = [
        line
        for line in lines
        if _HOSTS_MARKER not in line and not _hosts_line_has_target_loopback(line, hosts)
    ]

    if read_failed or len(filtered) == len(lines):
        return True  # Unreadable or already clean; neither is a write failure

    try:
        _write_hosts_file(''.join(filtered))
    except OSError as exc:
        _record_error(exc)
        log_buffer.log('Hosts', f'Failed to clean hosts file: {exc}')
        return False
    else:
        log_buffer.log('Hosts', 'Removed proxy hosts entries')
        return True


# ---------------------------------------------------------------------------
# Roblox CA installation
# ---------------------------------------------------------------------------


def _find_roblox_dirs(*, include_studio: bool = True) -> list[Path]:
    """Locate Roblox resource directories, optionally including Studio.

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
        find_roblox_resource_dirs = cast(
            'Callable[..., list[Path]]',
            _lazy_attr('fleasion.utils.platform_macos', 'find_roblox_resource_dirs'),
        )
    elif IS_LINUX:
        find_roblox_resource_dirs = cast(
            'Callable[..., list[Path]]',
            _lazy_attr('fleasion.utils.platform_linux', 'find_roblox_resource_dirs'),
        )
    else:
        find_roblox_resource_dirs = None

    if find_roblox_resource_dirs is not None:
        found: list[Path] = []
        seen: set[str] = set()

        def _add_posix(path: Path) -> bool:
            if '\x00' in str(path):
                return False
            if IS_MACOS and 'RobloxStudio.app' in path.parts:
                return False
            if is_roblox_studio_resource_dir(path):
                return False
            try:
                key = str(path.resolve()).lower()
            except OSError, ValueError:
                key = str(path).lower()
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

    if TYPE_CHECKING:
        registry = winreg
    else:
        registry = __import__('winreg')

    found: list[Path] = []
    seen: set[str] = set()
    discovery_sources: dict[str, set[str]] = {}

    def _add(path: Path, source: str = 'unknown') -> bool:
        if '\x00' in str(path):
            return False
        if not include_studio and is_roblox_studio_resource_dir(path):
            return False
        key = str(path)
        discovery_sources.setdefault(key, set()).add(source)
        if key not in seen:
            found.append(path)
            seen.add(key)
            return True
        return False

    def _path_is_file(path: Path) -> bool:
        # Preserve the monkeypatchable os.path.isfile discovery seam.
        is_file = vars(os.path)['isfile']
        return is_file(str(path))

    def _scan_for_exe(root: Path, max_depth: int) -> list[Path]:
        """Return all subdirs up to max_depth layers under root that contain RobloxPlayerBeta.exe or RobloxStudioBeta.exe."""
        results: list[Path] = []

        def _has_roblox_exe(path: Path) -> bool:
            try:
                return _path_is_file(path / ROBLOX_PROCESS) or _path_is_file(
                    path / ROBLOX_STUDIO_PROCESS
                )
            except OSError, ValueError:
                return False

        try:
            root_is_dir = root.is_dir()
        except OSError, ValueError:
            return results
        if root_is_dir and _has_roblox_exe(root):
            results.append(root)

        def _directory_children(path: Path) -> list[Path]:
            try:
                with os.scandir(path) as entries:
                    return [Path(entry.path) for entry in entries if entry.is_dir()]
            except (OSError, ValueError):
                return []

        def _recurse(path: Path, depth: int) -> None:
            for entry_path in _directory_children(path):
                if _has_roblox_exe(entry_path):
                    results.append(entry_path)
                if depth < max_depth:
                    _recurse(entry_path, depth + 1)

        if root_is_dir:
            _recurse(root, 1)
        return results

    def _registry_subkey_names(key: _RegistryKey) -> list[str]:
        names: list[str] = []
        index = 0
        while True:
            try:
                name = registry.EnumKey(key, index)
            except OSError:
                return names
            index += 1
            if '\x00' not in name:
                names.append(name)

    def _scan_registry_key_and_children(parent: object, name: str) -> None:
        try:
            key_context = registry.OpenKey(parent, name)
        except (OSError, ValueError):
            return
        with key_context as key:
            _check_player_path_key(key)
            for child_name in _registry_subkey_names(key):
                try:
                    child_context = registry.OpenKey(key, child_name)
                except (OSError, ValueError):
                    continue
                with child_context as child_key:
                    _check_player_path_key(child_key)

    def _scan_main_registry_player_paths() -> None:
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, r'Software') as software_key:
                names = _registry_subkey_names(software_key)
                for name in names:
                    _scan_registry_key_and_children(software_key, name)
        except (OSError, ValueError):
            return

    def _scan_protocol_install(protocol_key: str, source: str) -> int:
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, protocol_key) as key:
                command, value_type = registry.QueryValueEx(key, '')
        except (OSError, ValueError):
            return 0
        if value_type != registry.REG_SZ or not command:
            return 0
        exe_path = _extract_exe_from_command(_preserve_str(command))
        if exe_path is None:
            return 0
        directories = _scan_for_exe(exe_path.parent, 2)
        for directory in directories:
            _add(directory, source)
        return len(directories)

    # ── 1. Main Registry Search ──────────────────────────────────────────
    # Walk HKCU\Software and one layer of subkeys; collect any "PlayerPath" value.
    t = time.perf_counter()
    reg_found = 0

    def _check_player_path_key(key: _RegistryKey) -> None:
        nonlocal reg_found
        for value_name, process_name in (
            ('PlayerPath', ROBLOX_PROCESS),
            ('StudioPath', ROBLOX_STUDIO_PROCESS),
        ):
            try:
                val, rtype = registry.QueryValueEx(key, value_name)
            except OSError:
                continue
            if rtype != registry.REG_SZ or not val:
                continue
            val = _preserve_str(val).replace('\x00', '').strip()
            if not val:
                continue
            p = Path(val)
            # Path may occasionally point at the exe itself rather than the dir
            if p.name.lower() == process_name.lower():
                p = p.parent
            source = f'Registry {value_name}'
            if _path_is_file(p / process_name):
                reg_found += 1
                _add(p, source)
            else:
                for d in _scan_for_exe(p, 1):
                    reg_found += 1
                    _add(d, source)

    _scan_main_registry_player_paths()
    log_buffer.log(
        'Certificate',
        f'  Registry PlayerPath: {int((time.perf_counter() - t) * 1000)} ms ({reg_found} found)',
    )

    # ── 2. MS Store Version ──────────────────────────────────────────────
    # C:\XboxGames\Roblox, up to two layers deep.
    t = time.perf_counter()
    xbox_found = 0
    for d in _scan_for_exe(Path(r'C:\XboxGames\Roblox'), 2):
        xbox_found += 1
        _add(d, 'XboxGames\\Roblox')
    log_buffer.log(
        'Certificate',
        f'  XboxGames\\Roblox: {int((time.perf_counter() - t) * 1000)} ms ({xbox_found} found)',
    )

    # ── 3. Active Roblox ─────────────────────────────────────────────────
    # Read HKCU\...\roblox-player\shell\open\command (Default); parse the exe
    # path and search up to two layers under its parent directory.
    t = time.perf_counter()
    active_found = _scan_protocol_install(
        r'SOFTWARE\Classes\roblox-player\shell\open\command',
        'Active Roblox protocol',
    )
    log_buffer.log(
        'Certificate',
        f'  Active Roblox (registry): {int((time.perf_counter() - t) * 1000)} ms ({active_found} found)',
    )

    # ── 4. Program Files (x86) Roblox ────────────────────────────────────
    t = time.perf_counter()
    program_files_found = 0
    for d in _scan_for_exe(Path(r'C:\Program Files (x86)\Roblox\Versions'), 2):
        program_files_found += 1
        _add(d, 'Program Files Roblox\\Versions')
    log_buffer.log(
        'Certificate',
        f'  Program Files (x86) Roblox\\Versions: {int((time.perf_counter() - t) * 1000)} ms ({program_files_found} found)',
    )

    # ── 5. Regular Roblox ────────────────────────────────────────────────
    # %LocalAppData%\Roblox\Versions — one layer down.
    t = time.perf_counter()
    roblox_found = 0
    for d in _scan_for_exe(LOCAL_APPDATA / 'Roblox' / 'Versions', 1):
        roblox_found += 1
        _add(d, 'AppData Roblox\\Versions')
    log_buffer.log(
        'Certificate',
        f'  AppData Roblox\\Versions: {int((time.perf_counter() - t) * 1000)} ms ({roblox_found} found)',
    )

    # ── 6. Active Studio ─────────────────────────────────────────────────
    # Read HKCU\...\roblox-studio\shell\open\command (Default); parse the exe
    # path and search up to two layers under its parent directory.
    t = time.perf_counter()
    studio_found = _scan_protocol_install(
        r'SOFTWARE\Classes\roblox-studio\shell\open\command',
        'Active Studio protocol',
    )
    log_buffer.log(
        'Certificate',
        f'  Active Studio (registry): {int((time.perf_counter() - t) * 1000)} ms ({studio_found} found)',
    )

    # ── 7. Running process install paths ─────────────────────────────────
    t = time.perf_counter()
    running_found = 0
    for running_exe in (get_roblox_player_exe_path(), get_roblox_studio_exe_path()):
        if running_exe is None:
            continue
        if _add(running_exe.parent, 'Running Roblox process'):
            running_found += 1
    log_buffer.log(
        'Certificate',
        f'  Running Roblox process path: {int((time.perf_counter() - t) * 1000)} ms ({running_found} found)',
    )

    for cached_dir in load_saved_roblox_dirs():
        _add(cached_dir, 'Saved Roblox directory')

    for candidate in found:
        sources = ', '.join(sorted(discovery_sources.get(str(candidate), {'unknown'})))
        log_buffer.log(
            'Certificate', f'  Candidate Roblox install: {candidate} (sources={sources})'
        )

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


def _env_proxy_global_ca_patch_failure_is_fatal() -> bool:
    """Only Windows defers the hard CA gate to the resolved launch target."""
    return not IS_WINDOWS


def _normalize_newlines(text: str) -> str:
    """Normalize mixed newlines to LF for stable PEM comparisons."""
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _normalize_pem_block(pem_block: str) -> str:
    """Return a canonical PEM block representation (LF + trailing newline)."""
    return f'{_normalize_newlines(pem_block).strip()}\n'


def _fleasion_ca_identity(pem_block: str) -> tuple[_X509Certificate, str, str]:
    load_certificate = cast(
        'Callable[[bytes], _X509Certificate]',
        _lazy_attr('cryptography.x509', 'load_pem_x509_certificate'),
    )
    deprecation_warning = cast(
        'type[Warning]',
        _lazy_attr('cryptography.utils', 'CryptographyDeprecationWarning'),
    )
    name_oid = cast(
        '_NameOidLike',
        _lazy_attr('cryptography.x509.oid', 'NameOID'),
    )
    with warnings.catch_warnings():
        warnings.filterwarnings(
            'ignore',
            category=deprecation_warning,
            message=r"Parsed a serial number which wasn't positive.*",
        )
        cert = load_certificate(pem_block.encode('utf-8'))
    cn_attrs = cert.subject.get_attributes_for_oid(name_oid.COMMON_NAME)
    org_attrs = cert.subject.get_attributes_for_oid(name_oid.ORGANIZATION_NAME)
    cn = cn_attrs[0].value if cn_attrs else ''
    org = org_attrs[0].value if org_attrs else ''
    return cert, cn, org


def _is_fleasion_ca_cert_block(pem_block: str) -> bool:
    """Return True if *pem_block* is a Fleasion self-signed CA cert."""
    try:
        cert, cn, org = _fleasion_ca_identity(pem_block)
    except (ImportError, TypeError, ValueError):
        return False
    return cert.subject == cert.issuer and cn == 'Fleasion Proxy CA' and org == 'Fleasion'


def _analyze_and_strip_fleasion_cas(pem_bundle: str, current_ca_pem: str) -> tuple[str, int, int]:
    """Remove all Fleasion CA blocks and return (cleaned_text, fleasion_count, current_count)."""
    normalized_bundle = _normalize_newlines(pem_bundle)
    normalized_current = _normalize_pem_block(current_ca_pem)

    parts: list[str] = []
    last_end = 0
    fleasion_count = 0
    current_count = 0

    for match in _PEM_CERT_BLOCK_RE.finditer(normalized_bundle):
        parts.append(normalized_bundle[last_end : match.start()])
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


def _describe_cacert_state(ca_file: Path, ca_pem: str) -> _CacertState:
    """Return a stable diagnostic snapshot for a Roblox cacert.pem bundle."""
    state: _CacertState = {
        'path': str(ca_file),
        'install': ca_file.parent.parent.name
        if ca_file.parent.name == 'ssl'
        else ca_file.parent.name,
        'exists': False,
        'size': 0,
        'mtime_ns': 0,
        'sha256': '',
        'total_certs': 0,
        'fleasion_certs': 0,
        'current_fleasion_certs': 0,
        'healthy': False,
        'health_reason': 'missing_bundle',
        'error': '',
    }

    try:
        stat = ca_file.stat()
    except FileNotFoundError:
        return state
    except OSError as exc:
        state['health_reason'] = 'stat_error'
        state['error'] = str(exc)
        return state

    try:
        raw = ca_file.read_bytes()
    except OSError as exc:
        state['health_reason'] = 'read_error'
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
    if stat.st_size < _CACERT_MIN_HEALTHY_SIZE_BYTES:
        health_reason = 'bundle_too_small'
    elif total_count < _CACERT_MIN_HEALTHY_CERTS:
        health_reason = 'too_few_certificates'
    elif fleasion_count == 0:
        health_reason = 'fleasion_ca_missing'
    elif fleasion_count != 1:
        health_reason = 'fleasion_ca_duplicate_or_stale'
    elif current_count != 1:
        health_reason = 'fleasion_ca_not_current'
    else:
        health_reason = 'healthy'

    state.update(
        {
            'exists': True,
            'size': stat.st_size,
            'mtime_ns': stat.st_mtime_ns,
            'sha256': hashlib.sha256(raw).hexdigest(),
            'total_certs': total_count,
            'fleasion_certs': fleasion_count,
            'current_fleasion_certs': current_count,
            'healthy': healthy,
            'health_reason': health_reason,
        }
    )
    return state


def _format_cacert_state(state: _CacertState) -> str:
    sha = str(state.get('sha256') or '')
    short_sha = sha[:12] if sha else 'none'
    error = state.get('error') or ''
    error_text = f', error={error}' if error else ''
    return (
        f'path={state.get("path")}, exists={"yes" if state.get("exists") else "no"}, '
        f'size={state.get("size")} bytes, mtime_ns={state.get("mtime_ns")}, '
        f'sha256={short_sha}, total certs={state.get("total_certs")}, '
        f'Fleasion certs={state.get("fleasion_certs")}, '
        f'current Fleasion certs={state.get("current_fleasion_certs")}, '
        f'healthy={"yes" if state.get("healthy") else "no"}, '
        f'reason={state.get("health_reason") or "unknown"}{error_text}'
    )


def _log_cacert_state(
    ca_file: Path, ca_pem: str, reason: str, *, log_healthy: bool = False
) -> _CacertState:
    state = _describe_cacert_state(ca_file, ca_pem)
    is_problem = (
        not state.get('exists') or bool(state.get('error')) or not bool(state.get('healthy'))
    )

    if log_healthy or is_problem:
        log_buffer.log('Certificate', f'{reason}: {_format_cacert_state(state)}')

    if not state.get('exists'):
        log_buffer.log('Certificate', f'WARNING: CERTS FILE MISSING: {ca_file}')
    elif state.get('error'):
        log_buffer.log(
            'Certificate',
            f'Failed to inspect cacert.pem at {ca_file}: {state["error"]}',
        )
    elif not state.get('healthy'):
        log_buffer.log(
            'Certificate',
            f'WARNING: cacert.pem is not launch-healthy for {state.get("install")}: {_format_cacert_state(state)}',
        )
    return state


def _log_cacert_health(ca_file: Path, ca_pem: str) -> None:
    """Compatibility wrapper for existing startup patch call sites."""
    _log_cacert_state(ca_file, ca_pem, f'cacert.pem health for {ca_file.parent.parent.name}')


def _cacert_needs_seed(state: _CacertState) -> bool:
    """Return True when the base trust bundle is missing or clearly truncated."""
    return (
        not bool(state.get('exists'))
        or int(state.get('size') or 0) < _CACERT_MIN_HEALTHY_SIZE_BYTES
        or int(state.get('total_certs') or 0) < _CACERT_MIN_HEALTHY_CERTS
    )


def _linux_cacert_needs_seed(state: _CacertState) -> bool:
    """Backward-compatible alias for Linux-specific callers/tests."""
    return _cacert_needs_seed(state)


def _clear_cacert_write_barriers(path: Path) -> None:
    """Clear OS write barriers that would block rewriting Roblox cacert.pem."""
    try:
        current_flags = getattr(path.stat(), 'st_flags', 0)
    except OSError:
        current_flags = 0

    chflags_value = getattr(os, _OS_CHFLAGS_ATTR, None)
    if current_flags and callable(chflags_value):
        immutable_mask = 0
        for name in ('UF_IMMUTABLE', 'UF_APPEND', 'SF_IMMUTABLE', 'SF_APPEND'):
            immutable_mask |= getattr(stat, name, 0)
        if immutable_mask:
            try:
                chflags = cast('Callable[[Path, int], None]', chflags_value)
                chflags(path, current_flags & ~immutable_mask)
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
    with contextlib.suppress(OSError):
        path.chmod(desired_mode)


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


def _healthy_cacert_source(ca_file: Path, ca_pem: str, dirs: list[Path]) -> Path | None:
    """Find another install with an intact base trust bundle suitable for seeding."""
    for candidate_dir in dirs:
        candidate = candidate_dir / 'ssl' / 'cacert.pem'
        if candidate == ca_file:
            continue
        state = _describe_cacert_state(candidate, ca_pem)
        if (
            bool(state.get('exists'))
            and not state.get('error')
            and int(state.get('size') or 0) >= _CACERT_MIN_HEALTHY_SIZE_BYTES
            and int(state.get('total_certs') or 0) >= _CACERT_MIN_HEALTHY_CERTS
        ):
            return candidate
    return None


def _healthy_linux_cacert_source(ca_file: Path, ca_pem: str, dirs: list[Path]) -> Path | None:
    """Backward-compatible alias for Linux-specific callers/tests."""
    return _healthy_cacert_source(ca_file, ca_pem, dirs)


def _seed_cacert_if_needed(
    ca_file: Path, state: _CacertState, install_name: str, ca_pem: str, dirs: list[Path]
) -> bool:
    """Replace a missing/truncated Windows/Linux Roblox bundle before CA upsert."""
    if not (IS_WINDOWS or IS_LINUX):
        return False
    if bool(state.get('error')):
        return False
    if not _cacert_needs_seed(state):
        return False

    source = _healthy_cacert_source(ca_file, ca_pem, dirs)
    if source is not None:
        restore_read_only = _cacert_is_read_only(ca_file)
        try:
            _prepare_cacert_target_for_write(ca_file)
            shutil.copy2(source, ca_file)
        except OSError as exc:
            log_buffer.log(
                'Certificate',
                f'Could not seed Roblox cacert.pem from local bundle for {install_name}: {exc}',
            )
        else:
            log_buffer.log(
                'Certificate',
                f'Seeded Roblox cacert.pem from healthy local bundle for {install_name}: {source}',
            )
            return True
        finally:
            if restore_read_only:
                _restore_cacert_read_only(ca_file)

    restore_read_only = False
    try:
        certifi_where = cast(
            'Callable[[], str]',
            _lazy_attr('certifi', 'where'),
        )
        restore_read_only = _cacert_is_read_only(ca_file)
        _prepare_cacert_target_for_write(ca_file)
        shutil.copy2(certifi_where(), ca_file)
    except (ImportError, OSError) as exc:
        log_buffer.log('Certificate', f'Could not seed Roblox cacert.pem for {install_name}: {exc}')
        return False
    else:
        log_buffer.log(
            'Certificate',
            f'Seeded Roblox cacert.pem from Mozilla CA bundle for {install_name}',
        )
        return True
    finally:
        if restore_read_only:
            _restore_cacert_read_only(ca_file)


def _seed_linux_cacert_if_needed(
    ca_file: Path, state: _CacertState, install_name: str, ca_pem: str, dirs: list[Path]
) -> bool:
    """Backward-compatible Linux wrapper for the shared Windows/Linux seeder."""
    if not IS_LINUX:
        return False
    return _seed_cacert_if_needed(ca_file, state, install_name, ca_pem, dirs)


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


def _normalize_bootstrapper_ca_backup(resource_dir: Path, ca_pem: str) -> tuple[bool, _ErrorDetails]:
    ca_file = resource_dir / 'ssl' / 'cacert.pem'
    bootstrapper = 'AppleBlox' if 'AppleBlox' in resource_dir.parts else 'Froststrap'
    changed, _fleasion_count, _current_count = _upsert_fleasion_ca_in_cacert(ca_file, ca_pem)
    state = _log_cacert_state(
        ca_file,
        ca_pem,
        f'{bootstrapper} backup cacert.pem after normalization',
    )
    healthy = bool(state.get('healthy'))
    log_buffer.log(
        'Certificate',
        f'Normalized {bootstrapper} restore snapshot CA'
        if changed
        else f'{bootstrapper} restore snapshot CA already current',
    )
    return healthy, {
        'resource_dir': str(resource_dir),
        'ca_file': str(ca_file),
        'changed': changed,
        'healthy': healthy,
    }


def _patch_bootstrapper_ca_backups(ca_pem: str) -> tuple[bool, list[_ErrorDetails]]:
    """Normalize bootstrapper snapshots that may restore managed Roblox files."""
    if not IS_MACOS:
        return True, []

    find_bootstrapper_restore_resource_dirs = cast(
        'Callable[[], list[Path]]',
        _lazy_attr(
            'fleasion.utils.platform_macos', 'find_bootstrapper_restore_resource_dirs'
        ),
    )

    details: list[_ErrorDetails] = []
    ok = True
    for resource_dir in find_bootstrapper_restore_resource_dirs():
        ca_file = resource_dir / 'ssl' / 'cacert.pem'
        bootstrapper = 'AppleBlox' if 'AppleBlox' in resource_dir.parts else 'Froststrap'
        try:
            healthy, detail = _normalize_bootstrapper_ca_backup(resource_dir, ca_pem)
        except (PermissionError, OSError, UnicodeDecodeError) as exc:
            ok = False
            details.append(
                {
                    'resource_dir': str(resource_dir),
                    'ca_file': str(ca_file),
                    'error': str(exc),
                    'healthy': False,
                }
            )
            log_buffer.log(
                'Certificate',
                f'Failed to normalize {bootstrapper} restore snapshot CA: {exc}',
            )
        else:
            ok = ok and healthy
            details.append(detail)
    return ok, details


def _cacert_has_only_current_fleasion_ca(cacert_text: str, current_ca_pem: str) -> bool:
    """Return True when cacert contains exactly one Fleasion CA and it is current.

    This intentionally remains a narrow PEM-content predicate for callers that
    already own file-level health checks. Launch gating should use
    _describe_cacert_state() so a one-cert or truncated bundle is not treated as
    ready for Roblox.
    """
    _, fleasion_count, current_count = _analyze_and_strip_fleasion_cas(cacert_text, current_ca_pem)
    return fleasion_count == 1 and current_count == 1


def _install_ca_into_roblox_with_helper(
    ca_pem: str, dirs: list[Path]
) -> tuple[bool, _ErrorDetails]:
    patch_ca = cast(
        '_HelperPatchCa',
        _lazy_attr('fleasion.utils.macos_proxy_helper', 'helper_patch_ca'),
    )
    installs: list[_ErrorDetails] = []
    for roblox_dir in dirs:
        ca_file = roblox_dir / 'ssl' / 'cacert.pem'
        strip_all_fleasion_ca = False
        try:
            existing = (
                ca_file.read_text(encoding='utf-8', errors='replace') if ca_file.exists() else ''
            )
        except OSError as exc:
            log_buffer.log(
                'Certificate',
                f'Could not pre-read cacert.pem for {roblox_dir.name}; helper will try root read/write: {exc}',
            )
            existing = ''
            strip_all_fleasion_ca = True
        installs.append(
            {
                'resource_dir': str(roblox_dir),
                'remove_pems': _fleasion_ca_blocks(existing),
                'strip_all_fleasion_ca': strip_all_fleasion_ca,
            }
        )

    response = patch_ca(ca_pem, installs)
    details: _ErrorDetails = response or {
        'patched': [],
        'skipped': [],
        'failed': [{'error': 'macOS proxy helper did not return a CA patch response'}],
    }

    for key, label in (
        ('patched', 'patched'),
        ('skipped', 'already current'),
        ('failed', 'failed'),
    ):
        for item in _error_detail_list(details.get(key) or []):
            path = item.get('ca_file') or item.get('resource_dir') or '(unknown)'
            if key == 'failed':
                log_buffer.log(
                    'Certificate',
                    f'macOS helper CA patch {label} for {path}: {item.get("error") or item.get("status") or "unknown error"}',
                )
            else:
                changed = 'changed' if item.get('changed') else 'unchanged'
                log_buffer.log(
                    'Certificate',
                    f'macOS helper CA patch {label} for {path} ({changed})',
                )

    all_healthy = bool(response and response.get('ok'))
    verified: list[_CacertState] = []
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


def _patch_roblox_ca_with_macos_helper(
    ca_pem: str, roblox_dir: Path
) -> tuple[bool, bool, _ErrorDetails]:
    """Patch one macOS Roblox cacert.pem through the privileged helper.

    Returns (request_ok, changed, response_details).
    """
    patch_ca = cast(
        '_HelperPatchCa',
        _lazy_attr('fleasion.utils.macos_proxy_helper', 'helper_patch_ca'),
    )
    ca_file = roblox_dir / 'ssl' / 'cacert.pem'
    strip_all_fleasion_ca = False
    try:
        existing = ca_file.read_text(encoding='utf-8', errors='replace') if ca_file.exists() else ''
    except OSError as exc:
        log_buffer.log(
            'Certificate',
            f'Could not pre-read cacert.pem for {roblox_dir.name}; helper will try root read/write: {exc}',
        )
        existing = ''
        strip_all_fleasion_ca = True

    response = patch_ca(
        ca_pem,
        [
            {
                'resource_dir': str(roblox_dir),
                'remove_pems': _fleasion_ca_blocks(existing),
                'strip_all_fleasion_ca': strip_all_fleasion_ca,
            }
        ],
    )
    if not response:
        return (
            False,
            False,
            {
                'failed': [
                    {
                        'resource_dir': str(roblox_dir),
                        'error': 'macOS proxy helper did not return a CA patch response',
                    }
                ]
            },
        )

    changed = any(
        bool(item.get('changed')) for item in _error_detail_list(response.get('patched') or [])
    )
    for item in _error_detail_list(response.get('failed') or []):
        path = item.get('ca_file') or item.get('resource_dir') or str(ca_file)
        log_buffer.log(
            'Certificate',
            f'macOS helper CA patch failed for {path}: {item.get("error") or item.get("status") or "unknown error"}',
        )
    for key, label in (('patched', 'patched'), ('skipped', 'already current')):
        for item in _error_detail_list(response.get(key) or []):
            path = item.get('ca_file') or item.get('resource_dir') or str(ca_file)
            item_changed = 'changed' if item.get('changed') else 'unchanged'
            log_buffer.log(
                'Certificate',
                f'macOS helper CA patch {label} for {path} ({item_changed})',
            )
    return bool(response.get('ok')), changed, response


def _patch_single_roblox_ca(
    resource_dir: Path,
    ca_pem: str,
    seed_dirs: list[Path],
) -> tuple[_CacertState, _ErrorDetails]:
    ca_file = resource_dir / 'ssl' / 'cacert.pem'
    _prepare_cacert_target_for_write(ca_file)
    pre_state = _log_cacert_state(
        ca_file,
        ca_pem,
        f'cacert.pem health for {resource_dir.name}',
    )
    seeded = _seed_cacert_if_needed(ca_file, pre_state, resource_dir.name, ca_pem, seed_dirs)
    changed, fleasion_count, current_count = _upsert_fleasion_ca_in_cacert(ca_file, ca_pem)
    changed = changed or seeded
    post_state = _log_cacert_state(
        ca_file,
        ca_pem,
        f'cacert.pem after startup patch for {resource_dir.name}',
    )
    already_current = fleasion_count == 1 and current_count == 1 and bool(post_state.get('healthy'))
    if changed and not already_current:
        stale_count = max(fleasion_count - current_count, 0)
        duplicate_current = max(current_count - 1, 0)
        removed_count = stale_count + duplicate_current
        if removed_count > 0:
            log_buffer.log(
                'Certificate',
                f'Refreshed CA in {resource_dir.name} '
                f'(removed {removed_count} stale/duplicate Fleasion CA entries)',
            )
        else:
            log_buffer.log('Certificate', f'Installed CA into {resource_dir.name}')
    elif changed:
        log_buffer.log('Certificate', f'Normalized CA bundle formatting in {resource_dir.name}')
    else:
        log_buffer.log('Certificate', f'CA already installed in {resource_dir.name}')
    return post_state, {
        'resource_dir': str(resource_dir),
        'ca_file': str(ca_file),
        'changed': changed,
    }


def _install_ca_into_roblox(
    ca_pem: str, *, include_studio: bool = True
) -> tuple[bool, _ErrorDetails]:
    """Ensure each Roblox ssl/cacert.pem has exactly one current Fleasion CA cert."""
    t0 = time.perf_counter()
    dirs = _find_roblox_dirs(include_studio=include_studio)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if not dirs:
        log_buffer.log(
            'Certificate',
            f'No Roblox installs found to patch (scanned in {elapsed_ms} ms)',
        )
        return False, {'error': 'no_roblox_installs', 'dirs': []}
    log_buffer.log(
        'Certificate',
        f'Found {format_count(dirs, "Roblox install")} to patch (scanned in {elapsed_ms} ms)',
    )

    ok = True
    patched: list[_ErrorDetails] = []
    failed: list[_ErrorDetails] = []
    verified: list[_CacertState] = []
    details: _ErrorDetails = {'patched': patched, 'failed': failed, 'verified': verified}
    helper_fallback_dirs: list[Path] = []
    for d in dirs:
        ca_file = d / 'ssl' / 'cacert.pem'
        try:
            post_state, patch_detail = _patch_single_roblox_ca(d, ca_pem, dirs)
        except (PermissionError, OSError, UnicodeDecodeError) as exc:
            log_buffer.log('Certificate', f'Failed to write CA for {d.name}: {exc}')
            failed.append({'resource_dir': str(d), 'ca_file': str(ca_file), 'error': str(exc)})
            ok = False
            if IS_MACOS and not _is_admin():
                helper_fallback_dirs.append(d)
            continue

        verified.append(post_state)
        patched.append(patch_detail)
        post_healthy = bool(post_state.get('healthy'))
        ok = ok and post_healthy
        if not post_healthy:
            failed.append(
                {
                    'resource_dir': str(d),
                    'ca_file': str(ca_file),
                    'error': (
                        'cacert.pem was not launch-healthy after direct patch '
                        f'(reason={post_state.get("health_reason") or "unknown"})'
                    ),
                }
            )
            if IS_MACOS and not _is_admin():
                helper_fallback_dirs.append(d)

    if helper_fallback_dirs:
        direct_failures = list(failed)
        helper_ok, helper_details = _install_ca_into_roblox_with_helper(
            ca_pem, helper_fallback_dirs
        )
        details['direct_failures'] = direct_failures
        details['helper'] = helper_details
        details['helper_required'] = not helper_ok
        fallback_keys = {str(path.resolve()).lower() for path in helper_fallback_dirs}
        failed = [
            item
            for item in failed
            if str(Path(_preserve_str(item['resource_dir'])).resolve()).lower() not in fallback_keys
        ]
        details['failed'] = failed
        patched.extend(_error_detail_list(helper_details.get('patched') or []))
        patched.extend(_error_detail_list(helper_details.get('skipped') or []))
        failed.extend(_error_detail_list(helper_details.get('failed') or []))
        verified.extend(_cacert_state_list(helper_details.get('verified') or []))
        ok = helper_ok and not failed
    backup_ok, backup_details = _patch_bootstrapper_ca_backups(ca_pem)
    details['bootstrapper_backups'] = backup_details
    return ok and backup_ok, details


def _ca_thumbprint_sha1(ca_pem: str) -> str:
    body = ''.join(
        line.strip() for line in ca_pem.splitlines() if line and not line.startswith('-----')
    )
    der = base64.b64decode(body)
    return hashlib.sha1(der, usedforsecurity=False).hexdigest().upper()


def _certutil_store_has_thumbprint(store_location: str, thumbprint: str) -> bool:
    try:
        result = _run_binary_command(
            [_CERTUTIL_EXE, '-store', store_location, thumbprint],
            creationflags=_windows_subprocess.CREATE_NO_WINDOW,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('Certificate', f'Windows {store_location} trust-store check failed: {exc}')
        return False
    text = ((result.stdout or b'') + (result.stderr or b'')).decode('utf-8', errors='replace')
    return result.returncode == 0 and thumbprint.lower() in text.replace(' ', '').lower()


def _certutil_fleasion_root_thumbprints(store_location: str) -> list[str]:
    try:
        result = _run_binary_command(
            [_CERTUTIL_EXE, '-store', store_location],
            creationflags=_windows_subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log(
            'Certificate',
            f'Windows {store_location} trust-store enumeration failed: {exc}',
        )
        return []
    if result.returncode != 0:
        err = (result.stderr or result.stdout or b'').decode('utf-8', errors='replace').strip()
        log_buffer.log(
            'Certificate',
            f'Windows {store_location} trust-store enumeration failed: {err or result.returncode}',
        )
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
        result = _run_binary_command(
            [_CERTUTIL_EXE, '-delstore', store_location, thumbprint],
            creationflags=_windows_subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log(
            'Certificate',
            f'Failed to remove stale CA {thumbprint} from Windows {store_location} store: {exc}',
        )
        return False
    if result.returncode == 0:
        return True
    err = (result.stderr or result.stdout or b'').decode('utf-8', errors='replace').strip()
    log_buffer.log(
        'Certificate',
        f'Failed to remove stale CA {thumbprint} from Windows {store_location} store: {err or result.returncode}',
    )
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
            log_buffer.log(
                'Certificate',
                f'CA already trusted in Windows Root store (removed {removed_count} stale Fleasion CA entr{"y" if removed_count == 1 else "ies"})',
            )
        else:
            log_buffer.log('Certificate', 'CA already trusted in Windows Root store')
        return

    try:
        result = _run_binary_command(
            [_CERTUTIL_EXE, '-addstore', '-f', store_location, str(ca_cert_path)],
            creationflags=_windows_subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('Certificate', f'Failed to install CA into Windows Root store: {exc}')
        return

    if result.returncode == 0:
        if removed_count:
            log_buffer.log(
                'Certificate',
                f'Installed CA into Windows Root store (removed {removed_count} stale Fleasion CA entr{"y" if removed_count == 1 else "ies"})',
            )
        else:
            log_buffer.log('Certificate', 'Installed CA into Windows Root store')
        return

    err = (result.stderr or result.stdout or b'').decode('utf-8', errors='replace').strip()
    log_buffer.log(
        'Certificate',
        f'Failed to install CA into Windows Root store: {err or result.returncode}',
    )


def _macos_fleasion_keychain_thumbprints(keychain: str) -> list[str]:
    try:
        result = _run_text_command(
            [
                'security',
                'find-certificate',
                '-a',
                '-p',
                '-c',
                'Fleasion Proxy CA',
                keychain,
            ],
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
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
        result = _run_text_command(
            ['security', 'delete-certificate', '-Z', thumbprint, keychain],
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('Certificate', f'Failed to remove stale macOS CA {thumbprint}: {exc}')
        return False
    if result.returncode == 0:
        return True
    err = (result.stderr or result.stdout or '').strip()
    log_buffer.log(
        'Certificate',
        f'Failed to remove stale macOS CA {thumbprint}: {err or result.returncode}',
    )
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
            log_buffer.log(
                'Certificate',
                f'CA already trusted in macOS System keychain (removed {removed_count} stale Fleasion CA entr{"y" if removed_count == 1 else "ies"})',
            )
        else:
            log_buffer.log('Certificate', 'CA already trusted in macOS System keychain')
        return

    try:
        result = _run_text_command(
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
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('Certificate', f'Failed to install CA into macOS System keychain: {exc}')
        return

    if result.returncode == 0:
        if removed_count:
            log_buffer.log(
                'Certificate',
                f'Installed CA into macOS System keychain (removed {removed_count} stale Fleasion CA entr{"y" if removed_count == 1 else "ies"})',
            )
        else:
            log_buffer.log('Certificate', 'Installed CA into macOS System keychain')
        return

    err = (result.stderr or result.stdout or '').strip()
    log_buffer.log(
        'Certificate',
        f'Failed to install CA into macOS System keychain: {err or result.returncode}',
    )


def _install_ca_into_macos_login_keychain(
    ca_cert_path: Path, ca_pem: str
) -> tuple[bool, _ErrorDetails]:
    """Trust Fleasion's CA for HTTP clients launched by the signed-in user."""
    thumbprint = _ca_thumbprint_sha1(ca_pem).upper()
    keychain = str(Path.home() / 'Library' / 'Keychains' / 'login.keychain-db')
    stored_thumbprints = _macos_fleasion_keychain_thumbprints(keychain)
    stale_thumbprints = [stored for stored in stored_thumbprints if stored != thumbprint]
    removed = sum(
        1 for stored in stale_thumbprints if _macos_delete_keychain_certificate(keychain, stored)
    )
    if thumbprint in stored_thumbprints:
        return True, {
            'trusted': True,
            'changed': False,
            'removed_stale': removed,
            'keychain': keychain,
        }

    try:
        result = _run_text_command(
            [
                '/usr/bin/security',
                'add-trusted-cert',
                '-r',
                'trustRoot',
                '-k',
                keychain,
                str(ca_cert_path),
            ],
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, {
            'trusted': False,
            'changed': False,
            'removed_stale': removed,
            'keychain': keychain,
            'error': f'{type(exc).__name__}: {exc}',
        }

    if result.returncode == 0:
        return True, {
            'trusted': True,
            'changed': True,
            'removed_stale': removed,
            'keychain': keychain,
        }
    error = (result.stderr or result.stdout or '').strip()
    return False, {
        'trusted': False,
        'changed': False,
        'removed_stale': removed,
        'keychain': keychain,
        'error': error or f'security exited with {result.returncode}',
    }


def _macos_resource_root_from_executable(exe_path: Path) -> Path | None:
    platform_macos = importlib.import_module('fleasion.utils.platform_macos')
    resolver = cast(
        'Callable[[Path], Path | None]',
        getattr(platform_macos, _RESOURCE_ROOT_FROM_EXECUTABLE_ATTR),
    )
    return resolver(exe_path)


def _selected_linux_client_installation() -> LinuxClientInstallation | None:
    """Resolve the selected Linux client without importing Linux helpers elsewhere."""
    if not IS_LINUX:
        return None
    try:
        get_selected = cast(
            'Callable[[], LinuxClientInstallation | None]',
            _lazy_attr(
                'fleasion.utils.platform_linux', 'get_selected_linux_client_installation'
            ),
        )
        return get_selected()
    except OSError, RuntimeError, ValueError:
        return None


type _RunningCaPatchResult = tuple[_CacertState, _CacertState, bool, int, int]


def _patch_running_roblox_ca_file(
    ca_file: Path,
    ca_pem: str,
    roblox_dir: Path,
) -> _RunningCaPatchResult | None:
    pre_state = _log_cacert_state(
        ca_file,
        ca_pem,
        f'cacert.pem before running-instance patch for {roblox_dir.name}',
    )
    pre_state_readable = bool(pre_state.get('exists')) and not bool(pre_state.get('error'))
    seeded = False
    if (
        (IS_WINDOWS or IS_LINUX)
        and _cacert_needs_seed(pre_state)
        and not pre_state.get('error')
    ):
        seed_dirs = _find_roblox_dirs(include_studio=False)
        if roblox_dir not in seed_dirs:
            seed_dirs.insert(0, roblox_dir)
        seeded = _seed_cacert_if_needed(ca_file, pre_state, roblox_dir.name, ca_pem, seed_dirs)

    direct_error: PermissionError | OSError | UnicodeDecodeError | None = None
    try:
        _prepare_cacert_target_for_write(ca_file)
        changed, fleasion_count, current_count = _upsert_fleasion_ca_in_cacert(ca_file, ca_pem)
        changed = changed or seeded
    except (PermissionError, OSError, UnicodeDecodeError) as exc:
        direct_error = exc
        changed = seeded
        fleasion_count = int(pre_state.get('fleasion_certs') or 0)
        current_count = int(pre_state.get('current_fleasion_certs') or 0)

    post_state = _log_cacert_state(
        ca_file,
        ca_pem,
        f'cacert.pem after running-instance patch for {roblox_dir.name}',
    )
    if (
        IS_MACOS
        and not _is_admin()
        and (direct_error is not None or not bool(post_state.get('healthy')))
    ):
        if direct_error is not None:
            log_buffer.log(
                'Certificate',
                'Direct macOS cacert.pem patch needs helper fallback for '
                f'{roblox_dir.name}: {direct_error}',
            )
        request_ok, helper_changed, helper_details = _patch_roblox_ca_with_macos_helper(
            ca_pem, roblox_dir
        )
        if not request_ok:
            log_buffer.log(
                'Certificate',
                'Failed to inject CA into running Roblox instance through '
                f'macOS helper: {helper_details}',
            )
            return None
        changed = changed or helper_changed
        post_state = _log_cacert_state(
            ca_file,
            ca_pem,
            f'cacert.pem after running-instance helper patch for {roblox_dir.name}',
        )
        if not pre_state_readable:
            fleasion_count = int(post_state.get('fleasion_certs') or 0)
            current_count = int(post_state.get('current_fleasion_certs') or 0)
    elif direct_error is not None:
        raise direct_error

    return pre_state, post_state, changed, fleasion_count, current_count


def check_and_patch_running_roblox_ca(exe_path: Path) -> bool:
    """Check if the currently running Roblox instance has our CA in its cacert.pem.

    Called when RobloxPlayerBeta.exe is detected launching at runtime.
    If the cert chain is stale/missing/incomplete it is normalized immediately
    and an alert is logged.

    Returns True if the cert bundle needed refresh (Roblox needs a restart).
    Returns False if already launch-healthy or the CA has not been generated.
    """
    ca_cert_path = _current_proxy_ca_dir() / 'ca.crt'
    if not ca_cert_path.exists():
        return False  # CA not generated yet - nothing to patch
    if _is_macos_studio_bundle_path(Path(exe_path)):
        log_buffer.log(
            'Certificate',
            f'Skipping macOS Roblox Studio CA patch for {Path(exe_path).name}',
        )
        return False
    ca_pem = get_ca_pem(ca_cert_path)
    if IS_MACOS:
        _patch_bootstrapper_ca_backups(ca_pem)
    if IS_MACOS:
        roblox_dir = _macos_resource_root_from_executable(exe_path) or exe_path.parent
    elif IS_LINUX:
        find_roblox_resource_dirs = cast(
            'Callable[..., list[Path]]',
            _lazy_attr('fleasion.utils.platform_linux', 'find_roblox_resource_dirs'),
        )

        dirs = find_roblox_resource_dirs(include_studio=False)
        roblox_dir = dirs[0] if dirs else exe_path.parent
    else:
        roblox_dir = exe_path.parent
    ssl_dir = roblox_dir / 'ssl'
    ca_file = ssl_dir / 'cacert.pem'

    try:
        patch_result = _patch_running_roblox_ca_file(ca_file, ca_pem, roblox_dir)
    except (PermissionError, OSError) as exc:
        log_buffer.log('Certificate', f'Failed to inject CA into running Roblox instance: {exc}')
        return False
    if patch_result is None:
        return False
    pre_state, post_state, changed, fleasion_count, current_count = patch_result

    was_launch_healthy = bool(pre_state.get('healthy'))
    is_launch_healthy = bool(post_state.get('healthy'))
    already_current = fleasion_count == 1 and current_count == 1 and was_launch_healthy
    if already_current:
        log_buffer.log(
            'Certificate',
            f'Roblox launch detected: cacert.pem already launch-healthy for {roblox_dir.name}',
        )
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
        log_buffer.log(
            'Certificate',
            f'CA injected into running Roblox instance: {roblox_dir.name}',
        )

    return changed or not was_launch_healthy or not is_launch_healthy


# ---------------------------------------------------------------------------
# ProxyMaster
# ---------------------------------------------------------------------------


if TYPE_CHECKING:
    _ = (
        _resolve_real_ips,
        _log_cacert_health,
        _linux_cacert_needs_seed,
        _healthy_linux_cacert_source,
        _seed_linux_cacert_if_needed,
        _cacert_has_only_current_fleasion_ca,
        _install_ca_into_macos_system_keychain,
    )


def _generate_proxy_certificate(
    failure_label: str,
    operation: Callable[[], tuple[Path, Path]],
) -> tuple[Path, Path] | None:
    try:
        return operation()
    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log('Certificate', f'{failure_label}: {exc}')
        return None


class ProxyMaster:
    """Manages the Fleasion proxy lifecycle."""

    def __init__(
        self,
        config_manager: ConfigManager,
        on_proxy_start_error: Callable[[str, _ErrorDetails], None] | None = None,
    ) -> None:
        self.config_manager = config_manager
        self._on_proxy_start_error = on_proxy_start_error
        if IS_LINUX:
            recover_stale_override = cast(
                'Callable[[], bool]',
                _lazy_attr(
                    'fleasion.utils.platform_linux',
                    'recover_stale_linux_client_env_proxy_override',
                ),
            )
            if not recover_stale_override():
                log_buffer.log(
                    'Launcher',
                    'Could not recover a stale Linux Env Proxy override during startup; '
                    'the persisted ownership marker was kept for a later retry',
                )
        cache_manager_factory = cast('Callable[[ConfigManager], CacheManager]', CacheManager)
        self.cache_manager = cache_manager_factory(config_manager)

        # Singleton addon instances - GUI holds references to these directly
        self.cache_scraper = CacheScraper(self.cache_manager)
        self.cache_scraper.set_enabled(False)

        # Wire scraper into cache_manager for private asset downloads
        self.cache_manager.set_scraper(self.cache_scraper)
        self.username_spoofer = UsernameSpoofer(config_manager)
        self.custom_fflag_modifier = CustomFFlagModifier(
            config_manager, reload_settings_from_disk=True
        )

        self._texture_stripper: TextureStripper | None = None

        self._proxy: FleasionProxy | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._active_proxy_port: int | None = None
        self._env_proxy_loopback_host = '127.0.0.1'
        self._active_local_tls_max_version = PROXY_TLS_MAX_VERSION
        self._tls_startup_attempts: list[_ErrorDetails] = []
        self._lock = threading.Lock()
        self._env_proxy_ready = threading.Event()
        self._hosts_proxy_ready = threading.Event()
        self._windows_proactor_accept_fault = False
        self._windows_selector_fallback_attempted = False
        self._hosts_installed: bool = False
        self._active_env_proxy_mode: bool = False
        self._active_linux_client_installation = (
            _selected_linux_client_installation() if IS_LINUX else None
        )
        self._active_linux_client_key: str | None = getattr(
            self._active_linux_client_installation, 'key', None
        )
        self._linux_env_proxy_override_client_key: str | None = None
        # Compatibility alias retained for callers/tests that only knew Sober.
        self._sober_env_proxy_override_active: bool = False
        self._active_intercept_hosts: set[str] = set(BASE_INTERCEPT_HOSTS)
        self._env_proxy_intercept_match: str = ''
        # In-memory only, on purpose - never read from or written to
        # config_manager, so it always resets to False on every app launch.
        self._env_proxy_intercept_all: bool = False
        self._roblox_player_running: bool = False
        self._watchdog_stop: threading.Event | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._sober_fflag_timer_stop: threading.Event | None = None
        self._sober_fflag_timer_thread: threading.Thread | None = None
        self._module_interceptors: list[_ModuleInterceptor] = [self.username_spoofer]
        self._cert_refresh_lock = threading.Lock()
        self._last_cert_refresh_by_exe: dict[Path, tuple[float, str]] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    def hosts_intercepts_host(self, host: str) -> bool:
        """Return whether managed hosts routing currently sends *host* through Fleasion."""
        normalized = host.strip().lower().rstrip('.')
        if not normalized:
            return False
        with self._lock:
            return (
                self._running
                and self._hosts_installed
                and normalized in self._active_intercept_hosts
            )

    def _proxy_debug_enabled(self) -> bool:
        return bool(self.config_manager.settings.get('_runtime_proxy_debug', False))

    def _proxy_debug_mode(self) -> str:
        mode = str(
            self.config_manager.settings.get('_runtime_proxy_debug_mode', 'full') or 'full'
        ).lower()
        return mode if mode in {'a', 'b', 'c', 'd', 'e', 'full'} else 'full'

    def _effective_upstream_mode(self) -> str:
        if self._proxy_debug_enabled() and self._proxy_debug_mode() == 'e':
            return UpstreamMode.SYSTEM_PROXY.value
        return self.config_manager.upstream_transport_mode

    def _use_env_proxy_mode(self) -> bool:
        return str(getattr(self.config_manager, 'proxy_mode', 'hosts') or 'hosts') == 'env'

    def can_live_switch_to_hosts(self) -> bool:
        """Return whether this process has a safe hosts-mode privilege path."""
        if IS_WINDOWS:
            return _is_admin()
        if IS_MACOS:
            helper_is_ready = cast(
                'Callable[[], bool]',
                _lazy_attr('fleasion.utils.macos_proxy_helper', 'helper_is_ready'),
            )
            return helper_is_ready()
        # Linux hosts mutation and privileged port ownership are delegated to
        # the root helper, so the normal-user GUI never needs to restart.
        return IS_LINUX

    def roblox_env_proxy_url(self) -> str:
        port = self._active_proxy_port or MACOS_PROXY_BACKEND_PORT
        host = getattr(self, '_env_proxy_loopback_host', '127.0.0.1')
        url_host = f'[{host}]' if ':' in host else host
        return f'http://{url_host}:{port}'

    def _linux_client_installation(self) -> LinuxClientInstallation | None:
        installation = getattr(self, '_active_linux_client_installation', None)
        if installation is not None:
            return installation
        # Raw ``__new__`` test doubles predate selected-client state. Resolve
        # the configured client when possible and otherwise retain Sober's
        # historical behavior.
        return _selected_linux_client_installation()

    def _linux_client_key(self) -> str | None:
        if hasattr(self, '_active_linux_client_key'):
            return self._active_linux_client_key
        installation = self._linux_client_installation()
        return getattr(installation, 'key', None) or ('sober' if IS_LINUX else None)

    def _linux_client_descriptor(self) -> LinuxClientDescriptor | None:
        installation = self._linux_client_installation()
        descriptor = getattr(installation, 'client', None)
        if descriptor is not None:
            return descriptor
        client_key = self._linux_client_key()
        if client_key is None:
            return None
        try:
            get_linux_client = cast(
                'Callable[[str], LinuxClientDescriptor]',
                _lazy_attr('fleasion.utils.linux_clients', 'get_linux_client'),
            )
            return get_linux_client(client_key)
        except ValueError:
            return None

    def _linux_proxy_passthrough_hosts(self) -> set[str]:
        """Return bootstrap hosts that the selected client must tunnel."""
        descriptor = self._linux_client_descriptor()
        return set(getattr(descriptor, 'proxy_passthrough_hosts', ()))

    def _linux_env_proxy_excluded_hosts(self) -> set[str]:
        """Return selected-client hosts that explicit mode must tunnel."""
        hosts = self._linux_proxy_passthrough_hosts()
        descriptor = self._linux_client_descriptor()
        route_delay = getattr(descriptor, 'clientsettings_route_delay_seconds', 0.0)
        if route_delay > 0:
            # The first ClientSettings request is made by Sober's pinned
            # bootstrap client, so keep it tunneled until the descriptor's
            # route-arm delay has elapsed.
            hosts.update(CUSTOM_FFLAGS_INTERCEPT_HOSTS)
        return hosts

    def _arm_linux_env_proxy_override(self) -> bool:
        """Arm the explicit proxy for exactly the selected Linux client."""
        client_key = self._linux_client_key()
        if client_key is None:
            log_buffer.log('Launcher', 'Cannot arm Linux Env Proxy: no client is selected')
            return False
        set_env_proxy_override = cast(
            'Callable[..., bool]',
            _lazy_attr('fleasion.utils.platform_linux', 'set_linux_client_env_proxy_override'),
        )
        armed = set_env_proxy_override(
            self.roblox_env_proxy_url(),
            client_key=client_key,
        )
        if armed:
            self._linux_env_proxy_override_client_key = client_key
            self._sober_env_proxy_override_active = client_key == 'sober'
        return bool(armed)

    def _clear_linux_env_proxy_override(self) -> bool:
        """Clear only the client override armed by this proxy instance."""
        client_key = getattr(self, '_linux_env_proxy_override_client_key', None)
        if client_key is None and getattr(self, '_sober_env_proxy_override_active', False):
            client_key = 'sober'
        if client_key is None:
            return True
        clear_env_proxy_override = cast(
            'Callable[..., bool]',
            _lazy_attr('fleasion.utils.platform_linux', 'clear_linux_client_env_proxy_override'),
        )
        cleared = clear_env_proxy_override(client_key=client_key)
        if cleared:
            self._linux_env_proxy_override_client_key = None
            self._sober_env_proxy_override_active = False
        return bool(cleared)

    def _cleanup_linux_client_proxy_state(self) -> None:
        """Restore selected-client state after any proxy shutdown path."""
        if IS_LINUX:
            self._clear_linux_env_proxy_override()

    def wait_for_env_proxy_ready(
        self, timeout: float = 15.0, *, cancelled: _CancelCheck | None = None
    ) -> bool:
        """Wait for bind/TLS readiness while allowing restart handoff cancellation."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if cancelled is not None and cancelled():
                return False
            if self._env_proxy_ready.wait(min(0.1, max(0.0, deadline - time.monotonic()))):
                return True
            thread = self._thread
            if thread is not None and not thread.is_alive():
                return False
        return self._env_proxy_ready.is_set()

    def wait_for_hosts_proxy_ready(
        self, timeout: float = 30.0, *, cancelled: _CancelCheck | None = None
    ) -> bool:
        """Wait until port 443, TLS, hosts entries, and watchdog are all active."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if cancelled is not None and cancelled():
                return False
            if self._hosts_proxy_ready.wait(min(0.1, max(0.0, deadline - time.monotonic()))):
                return True
            thread = self._thread
            if thread is not None and not thread.is_alive():
                return False
        return self._hosts_proxy_ready.is_set()

    def _roblox_ca_target(self, exe_path: Path) -> tuple[Path, str] | None:
        ca_cert_path = _current_proxy_ca_dir() / 'ca.crt'
        if not ca_cert_path.exists():
            return None
        exe_path = Path(exe_path)
        if _is_macos_studio_bundle_path(exe_path):
            return None
        ca_pem = get_ca_pem(ca_cert_path)
        if IS_MACOS:
            roblox_dir = _macos_resource_root_from_executable(exe_path) or exe_path.parent
        elif IS_LINUX:
            find_roblox_resource_dirs = cast(
                'Callable[..., list[Path]]',
                _lazy_attr('fleasion.utils.platform_linux', 'find_roblox_resource_dirs'),
            )
            dirs = find_roblox_resource_dirs(include_studio=False)
            roblox_dir = dirs[0] if dirs else exe_path.parent
        else:
            roblox_dir = exe_path.parent
        return roblox_dir / 'ssl' / 'cacert.pem', ca_pem

    def inspect_env_proxy_roblox_ca(self, exe_path: Path, reason: str) -> _CacertInspection:
        target = self._roblox_ca_target(exe_path)
        if target is None:
            return {'healthy': False, 'error': 'Roblox CA target is unavailable'}
        ca_file, ca_pem = target
        return _log_cacert_state(ca_file, ca_pem, reason)

    def ensure_env_proxy_roblox_ca(self, exe_path: Path, *, settle: bool = False) -> _ErrorDetails:
        """Settle, directly repair, and fully verify Player's CA bundle."""
        if settle:
            time.sleep(_CACERT_LAUNCH_SETTLE_SECONDS)
        before = self.inspect_env_proxy_roblox_ca(
            exe_path, f'Env Proxy cacert.pem before launch preparation for {Path(exe_path).name}'
        )
        if not before.get('healthy'):
            check_and_patch_running_roblox_ca(Path(exe_path))
        after = self.inspect_env_proxy_roblox_ca(
            exe_path, f'Env Proxy cacert.pem after launch preparation for {Path(exe_path).name}'
        )
        return {
            'success': bool(after.get('healthy')),
            'healthy': bool(after.get('healthy')),
            'changed': before.get('sha256') != after.get('sha256'),
            'path': after.get('path'),
            'error': after.get('error'),
            'state': after,
        }

    def monitor_env_proxy_roblox_ca(
        self,
        exe_path: Path,
        cancel_event: threading.Event,
        *,
        duration: float = _CACERT_LAUNCH_POLL_SECONDS,
    ) -> _ErrorDetails:
        """Require CA health for the complete post-launch observation window."""
        deadline = time.monotonic() + max(0.0, duration)
        unhealthy_samples = 0
        last_state: _CacertInspection = {}
        while time.monotonic() < deadline:
            if cancel_event.is_set():
                return {'success': False, 'cancelled': True, 'state': last_state}
            last_state = self.inspect_env_proxy_roblox_ca(
                exe_path, f'Env Proxy post-launch cacert.pem health for {Path(exe_path).name}'
            )
            if last_state.get('healthy'):
                unhealthy_samples = 0
            else:
                unhealthy_samples += 1
                if unhealthy_samples >= 2:
                    return {
                        'success': False,
                        'healthy': False,
                        'path': last_state.get('path'),
                        'error': last_state.get('error') or 'cacert.pem became unhealthy',
                        'state': last_state,
                    }
            cancel_event.wait(_CACERT_LAUNCH_POLL_INTERVAL_SECONDS)
        return {
            'success': bool(last_state.get('healthy')),
            'healthy': bool(last_state.get('healthy')),
            'path': last_state.get('path'),
            'error': last_state.get('error'),
            'state': last_state,
        }

    def get_env_proxy_traffic(self) -> list[_ProxyRequestLogEntry]:
        """Every request/tunnel the explicit proxy has logged, intercepted or not."""
        if self._proxy is None:
            return []
        get_log = cast(
            'Callable[[], list[_ProxyRequestLogEntry]] | None',
            getattr(self._proxy, 'get_request_log', None),
        )
        return get_log() if callable(get_log) else []

    def clear_env_proxy_traffic(self) -> None:
        if self._proxy is None:
            return
        clear_log = getattr(self._proxy, 'clear_request_log', None)
        if callable(clear_log):
            clear_log()

    def format_env_proxy_request_preview(self, entry: _ProxyRequestLogEntry) -> str:
        if self._proxy is None:
            return ''
        fmt = cast(
            'Callable[[_ProxyRequestLogEntry], str] | None',
            getattr(self._proxy, 'format_request_preview', None),
        )
        return fmt(entry) if callable(fmt) else ''

    def format_env_proxy_response_preview(self, entry: _ProxyRequestLogEntry) -> str:
        if self._proxy is None:
            return ''
        fmt = cast(
            'Callable[[_ProxyRequestLogEntry], str] | None',
            getattr(self._proxy, 'format_response_preview', None),
        )
        return fmt(entry) if callable(fmt) else ''

    def set_env_proxy_intercept_match(self, text: str) -> None:
        """Interception is armed purely by this being non-empty text - nothing else gates it."""
        self._env_proxy_intercept_match = text
        if self._proxy is not None:
            setter = getattr(self._proxy, 'set_intercept_match', None)
            if callable(setter):
                setter(text)

    def set_env_proxy_intercept_all(self, enabled: bool) -> None:
        """Toggle whether hosts outside Fleasion's own feature set also get
        decrypted/logged. Deliberately not persisted anywhere - this always
        starts back at False on every launch, regardless of what it was
        last set to.
        """
        self._env_proxy_intercept_all = bool(enabled)
        if self._proxy is not None:
            setter = getattr(self._proxy, 'set_intercept_all_hosts', None)
            if callable(setter):
                setter(enabled)

    def get_auto_replace_rules(self) -> list[_AutoReplaceRule]:
        """Auto Replace rules, persisted to config (unlike the env-proxy
        toggles above) so they survive a restart without the dialog needing
        to have been opened.
        """
        stored = self.config_manager.settings.get('auto_replace_rules', [])
        return list(cast('list[_AutoReplaceRule]', stored))

    def set_auto_replace_rules(self, rules: list[_AutoReplaceRule]) -> None:
        self.config_manager.settings['auto_replace_rules'] = cast('JsonValue', list(rules))
        self.config_manager.save()
        if self._proxy is not None:
            setter = cast(
                'Callable[[list[_AutoReplaceRule]], None] | None',
                getattr(self._proxy, 'set_auto_replace_rules', None),
            )
            if callable(setter):
                setter(rules)

    def get_env_proxy_pending_intercepts(self) -> list[tuple[int, str]]:
        if self._proxy is None:
            return []
        getter = cast(
            'Callable[[], list[tuple[int, str]]] | None',
            getattr(self._proxy, 'get_pending_intercepts', None),
        )
        return getter() if callable(getter) else []

    def get_env_proxy_pending_data(self, entry_id: int, stage: str) -> bytes | None:
        if self._proxy is None:
            return None
        getter = cast(
            'Callable[[int, str], bytes | None] | None',
            getattr(self._proxy, 'get_pending_data', None),
        )
        return getter(entry_id, stage) if callable(getter) else None

    def submit_env_proxy_pending(
        self, entry_id: int, stage: str, action: str, edited_text: str | None = None
    ) -> bool:
        if self._proxy is None:
            return False
        submitter = cast(
            'Callable[[int, str, str, str | None], bool] | None',
            getattr(self._proxy, 'submit_pending', None),
        )
        return submitter(entry_id, stage, action, edited_text) if callable(submitter) else False

    def replay_env_proxy_request(self, entry_id: int, edited_text: str | None = None) -> bool:
        """Resend a captured/edited request fresh, overwriting that SAME
        entry's request/response fields in place - not a new row.
        Fire-and-forget from the GUI's side; the row updates once the
        round-trip completes.
        """
        if self._proxy is None or self._loop is None or not self._loop.is_running():
            return False
        entries = {e['id']: e for e in self._proxy.get_request_log()}
        entry = entries.get(entry_id)
        if entry is None:
            return False
        host = entry.get('host')
        if not host:
            return False
        if edited_text is not None:
            rebuild_edited_message = cast(
                'Callable[[str], bytes]',
                _lazy_attr('fleasion.proxy.server', 'rebuild_edited_message'),
            )
            raw = rebuild_edited_message(edited_text)
        else:
            raw = entry.get('request_raw')
        if not raw:
            return False
        asyncio.run_coroutine_threadsafe(
            self._proxy.replay_request(entry_id, bytes(raw), host), self._loop
        )
        return True

    def _effective_wire_preserving_passthrough(self) -> bool:
        if self._proxy_debug_enabled() and self._proxy_debug_mode() == 'd':
            return True
        if IS_MACOS or _use_linux_privileged_helper():
            # Helper/relay platforms keep their existing passthrough behavior.
            # Do not use this as evidence that Windows should enable it globally.
            return True
        return self.config_manager.wire_preserving_passthrough

    def register_module_interceptor(self, module: _ModuleInterceptor) -> None:
        """Register a module whose request()/response() methods are called for gamejoin traffic."""
        with self._lock:
            if module not in self._module_interceptors:
                self._module_interceptors.append(module)
            interceptors = list(self._module_interceptors)
        if self._proxy is not None:
            self._proxy.set_module_interceptors(interceptors)

    def unregister_module_interceptor(self, module: _ModuleInterceptor) -> None:
        """Unregister a gamejoin traffic interceptor when its owning UI is destroyed."""
        with self._lock:
            before = len(self._module_interceptors)
            self._module_interceptors = [
                interceptor
                for interceptor in self._module_interceptors
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
        spoofer = getattr(self, 'username_spoofer', None)
        spoofer_enabled = spoofer is not None and spoofer.is_enabled()
        if spoofer_enabled:
            hosts.update(USERNAME_SPOOFER_INTERCEPT_HOSTS)
        custom_modifier = getattr(self, 'custom_fflag_modifier', None)
        if (
            custom_modifier is not None
            and custom_modifier.is_enabled()
            and self._linux_sober_custom_fflag_routes_ready()
        ):
            hosts.update(CUSTOM_FFLAGS_INTERCEPT_HOSTS)
        return hosts

    def _log_intercept_configuration(self, reason: str, hosts: set[str]) -> None:
        """Log the feature state that selected the currently routed host set.

        The startup TLS self-test covers this same active route set, so the
        log reports the routes that were actually tested and selected.
        """
        custom_modifier = getattr(self, 'custom_fflag_modifier', None)
        custom_fflags_enabled = custom_modifier is not None and custom_modifier.is_enabled()
        spoofer = getattr(self, 'username_spoofer', None)
        username_spoofer_enabled = spoofer is not None and spoofer.is_enabled()
        log_buffer.log(
            'InterceptConfig',
            f'{reason}: custom_fflags={"enabled" if custom_fflags_enabled else "disabled"}; '
            'clientsettings_intercepted='
            f'{"yes" if bool(set(hosts) & CUSTOM_FFLAGS_INTERCEPT_HOSTS) else "no"}; '
            f'username_spoofer={"enabled" if username_spoofer_enabled else "disabled"}; '
            'profile_api_intercepted='
            f'{"yes" if bool(set(hosts) & USERNAME_SPOOFER_INTERCEPT_HOSTS) else "no"}; '
            f'hosts={", ".join(sorted(hosts))}',
        )

    @staticmethod
    def _sober_boottime() -> float:
        clock_id = getattr(time, 'CLOCK_BOOTTIME', None)
        if clock_id is None:
            return time.monotonic()
        return time.clock_gettime(clock_id)

    def _linux_sober_custom_fflag_routes_ready(self) -> bool:
        """Return when selected-client ClientSettings interception is safe."""
        # Tests (and platform-specific callers) can override one platform flag
        # without clearing the host platform flag.  Treat the Linux delay as
        # active only when Linux is the selected platform.
        if not IS_LINUX or IS_WINDOWS or IS_MACOS:
            return True
        installation = self._linux_client_installation()
        descriptor = self._linux_client_descriptor()
        route_delay = float(
            getattr(
                descriptor,
                'clientsettings_route_delay_seconds',
                SOBER_CUSTOM_FFLAG_ROUTE_ARM_DELAY_SECONDS,
            )
        )
        if route_delay <= 0:
            return True
        if installation is None:
            return False
        linux_client_main_process = cast(
            'Callable[[LinuxClientInstallation], tuple[int, float] | None]',
            _lazy_attr('fleasion.utils.platform_linux', 'linux_client_main_process'),
        )
        process = linux_client_main_process(installation)
        if process is None:
            return False
        _pid, started_at = process
        return self._sober_boottime() - started_at >= route_delay

    def _set_linux_sober_clientsettings_passthrough(self, enabled: bool) -> None:
        """Keep Sober's pinned ClientSettings bootstrap outside TLS interception."""
        if self._proxy is None:
            return
        excluded_hosts = set(
            cast('set[str]', getattr(self, '_env_proxy_intercept_excluded_hosts', set[str]()))
        )
        if enabled:
            excluded_hosts.update(CUSTOM_FFLAGS_INTERCEPT_HOSTS)
        else:
            excluded_hosts.difference_update(CUSTOM_FFLAGS_INTERCEPT_HOSTS)
        self._env_proxy_intercept_excluded_hosts = excluded_hosts
        setter = getattr(self._proxy, 'set_intercept_excluded_hosts', None)
        if callable(setter):
            setter(excluded_hosts)

    def _start_linux_sober_custom_fflag_timer(self) -> None:
        """Arm Linux ClientSettings interception after Sober's bootstrap window."""
        installation = self._linux_client_installation()
        descriptor = self._linux_client_descriptor()
        client_name = getattr(installation, 'display_name', 'Linux Roblox client')
        route_delay = float(
            getattr(
                descriptor,
                'clientsettings_route_delay_seconds',
                SOBER_CUSTOM_FFLAG_ROUTE_ARM_DELAY_SECONDS,
            )
        )
        if (
            not IS_LINUX
            or installation is None
            or route_delay <= 0
            or (self._sober_fflag_timer_thread and self._sober_fflag_timer_thread.is_alive())
        ):
            return

        stop_event = threading.Event()
        self._sober_fflag_timer_stop = stop_event

        def _poll() -> None:
            linux_client_main_process = cast(
                'Callable[[LinuxClientInstallation], tuple[int, float] | None]',
                _lazy_attr('fleasion.utils.platform_linux', 'linux_client_main_process'),
            )
            previous_process: tuple[int, float] | None = None
            previous_ready: bool | None = None
            previous_custom_fflags_enabled: bool | None = None
            while not stop_event.is_set():
                process = linux_client_main_process(installation)
                custom_fflags_enabled = bool(
                    getattr(self.config_manager, 'custom_fflags_enabled', False)
                )
                ready = False
                if process is not None:
                    _pid, started_at = process
                    ready = (
                        custom_fflags_enabled and self._sober_boottime() - started_at >= route_delay
                    )

                if process != previous_process:
                    if process is None and previous_process is not None:
                        log_buffer.log(
                            'CustomFFlags',
                            f'{client_name} closed; Linux ClientSettings interception timer reset',
                        )
                    elif process is not None:
                        remaining = max(
                            0.0,
                            route_delay - (self._sober_boottime() - process[1]),
                        )
                        log_buffer.log(
                            'CustomFFlags',
                            f'Detected {client_name} engine; delaying Linux ClientSettings interception '
                            f'for {remaining:.0f} seconds to pass the pinned bootstrap fetch',
                        )
                    previous_process = process

                if (
                    ready != previous_ready
                    or custom_fflags_enabled != previous_custom_fflags_enabled
                ):
                    if ready:
                        log_buffer.log(
                            'CustomFFlags',
                            'Linux ClientSettings interception armed; custom FastFlags will '
                            f"arrive on {client_name}'s 120-second dynamic refresh",
                        )
                    self._set_linux_sober_clientsettings_passthrough(not ready)
                    self.refresh_username_spoofer_interception()
                    previous_ready = ready
                    previous_custom_fflags_enabled = custom_fflags_enabled
                stop_event.wait(_SOBER_CUSTOM_FFLAG_POLL_SECONDS)

        self._sober_fflag_timer_thread = threading.Thread(
            target=_poll, daemon=True, name='fleasion-linux-clientsettings-timer'
        )
        self._sober_fflag_timer_thread.start()

    def _stop_linux_sober_custom_fflag_timer(self) -> None:
        if self._sober_fflag_timer_stop is not None:
            self._sober_fflag_timer_stop.set()
        if self._sober_fflag_timer_thread is not None and self._sober_fflag_timer_thread.is_alive():
            self._sober_fflag_timer_thread.join(timeout=2.0)
        self._sober_fflag_timer_stop = None
        self._sober_fflag_timer_thread = None

    def _startup_intercept_hosts(self) -> set[str]:
        hosts = self._desired_intercept_hosts()
        if IS_LINUX:
            manual_webui_hosts = (
                _hosts_file_loopback_hosts(set(USERNAME_SPOOFER_INTERCEPT_HOSTS)) - hosts
            )
            if manual_webui_hosts:
                hosts.update(manual_webui_hosts)
                log_buffer.log(
                    'Hosts',
                    'Detected existing Linux loopback hosts entries; treating as active intercepts: '
                    f'{", ".join(sorted(manual_webui_hosts))}',
                )
        return hosts

    def set_roblox_player_running(self, running: bool) -> None:
        with self._lock:
            if self._roblox_player_running == running:
                return
            self._roblox_player_running = running
        self.refresh_username_spoofer_interception()

    def _refresh_env_proxy_interception_locked(
        self,
        desired_hosts: set[str],
        env_proxy: FleasionProxy,
    ) -> None:
        previous_hosts = set(self._active_intercept_hosts)
        added_hosts = desired_hosts - previous_hosts
        retained_hosts = previous_hosts & desired_hosts
        real_endpoints = env_proxy.upstream_endpoints_for_hosts(
            cast('Sequence[str]', retained_hosts)
        )
        added_endpoints = _resolve_real_endpoints(added_hosts) if added_hosts else {}
        real_endpoints.update(added_endpoints)
        _set_proxy_upstream_endpoints(env_proxy, real_endpoints)
        env_proxy.set_intercept_hosts(desired_hosts)
        scraper_ips = _endpoint_ip_candidates(real_endpoints)
        if scraper_ips:
            _set_cache_scraper_real_ips(self.cache_scraper, scraper_ips)
        self._active_intercept_hosts = set(desired_hosts)
        log_buffer.log(
            'InterceptConfig',
            f'Env proxy intercepts updated: {", ".join(sorted(desired_hosts))}',
        )

    def _refresh_hosts_interception_locked(
        self,
        desired_hosts: set[str],
        proxy: FleasionProxy,
    ) -> None:
        previous_hosts = set(self._active_intercept_hosts)
        retained_hosts = previous_hosts & desired_hosts
        added_hosts = desired_hosts - previous_hosts
        removed_hosts = previous_hosts - desired_hosts
        log_buffer.log(
            'InterceptConfig',
            'Reconciling routes: '
            f'added={", ".join(sorted(added_hosts)) or "none"}; '
            f'removed={", ".join(sorted(removed_hosts)) or "none"}; '
            f'retained={", ".join(sorted(retained_hosts)) or "none"}',
        )

        # Resolve only routes that are about to be added, and do so before
        # the hosts update. Re-resolving retained hosts here is unsafe because
        # they already point at the loopback proxy.
        real_endpoints = proxy.upstream_endpoints_for_hosts(
            cast('Sequence[str]', retained_hosts)
        )
        missing_retained_hosts = retained_hosts - set(real_endpoints)
        if missing_retained_hosts:
            log_buffer.log(
                'Hosts',
                'Keeping existing intercept routes unchanged because their upstream '
                f'endpoints are unavailable: {", ".join(sorted(missing_retained_hosts))}',
            )
        added_endpoints = _resolve_real_endpoints(added_hosts) if added_hosts else {}
        real_endpoints.update(added_endpoints)
        _log_upstream_ip_coverage(desired_hosts, real_endpoints)

        if _use_linux_privileged_helper():
            update_helper_hosts = cast(
                'Callable[[set[str]], bool]',
                _lazy_attr('fleasion.utils.linux_proxy_helper', 'update_helper_hosts'),
            )
            if not update_helper_hosts(desired_hosts):
                log_buffer.log(
                    'Hosts',
                    'Failed to request Linux helper username spoofer hosts update',
                )
                return
            self._active_intercept_hosts = set(desired_hosts)
            _set_proxy_upstream_endpoints(proxy, real_endpoints)
            scraper_ips = _endpoint_ip_candidates(real_endpoints)
            if scraper_ips:
                _set_cache_scraper_real_ips(self.cache_scraper, scraper_ips)
            log_buffer.log(
                'Hosts',
                f'Requested Linux helper intercept update: {", ".join(sorted(desired_hosts))}',
            )
            return

        if IS_MACOS and not _is_admin():
            hosts_updated = _add_hosts_entries(desired_hosts)
        else:
            hosts_updated = (not removed_hosts or _remove_hosts_entries(removed_hosts)) and (
                not added_hosts or _add_hosts_entries(added_hosts)
            )
        if not hosts_updated:
            log_buffer.log('Hosts', 'Failed to update username spoofer hosts entries')
            return
        _flush_dns()
        if not _verify_hosts_entries(desired_hosts):
            log_buffer.log('Hosts', 'Failed to verify username spoofer hosts entries')
            return

        self._active_intercept_hosts = set(desired_hosts)
        _set_proxy_upstream_endpoints(proxy, real_endpoints)
        scraper_ips = _endpoint_ip_candidates(real_endpoints)
        if scraper_ips:
            _set_cache_scraper_real_ips(self.cache_scraper, scraper_ips)
        log_buffer.log(
            'Hosts',
            f'Active intercepts updated: {", ".join(sorted(desired_hosts))}',
        )

    def refresh_username_spoofer_interception(self) -> None:
        """Refresh hosts entries for optional proxy-backed features."""
        desired_hosts = self._desired_intercept_hosts()
        self._log_intercept_configuration('Refresh requested', desired_hosts)

        with self._lock:
            if desired_hosts == self._active_intercept_hosts:
                return
            env_proxy = _maybe_proxy(self._proxy)
            if getattr(self, '_active_env_proxy_mode', False) and env_proxy is not None:
                self._refresh_env_proxy_interception_locked(desired_hosts, env_proxy)
                return
            if not self._hosts_installed or _maybe_proxy(self._proxy) is None:
                self._active_intercept_hosts = set(desired_hosts)
                return

        if IS_LINUX and (desired_hosts & USERNAME_SPOOFER_INTERCEPT_HOSTS):
            ca_cert_path = _current_proxy_ca_dir() / 'ca.crt'
            if not _ensure_linux_system_trust_for_hosts(desired_hosts, ca_cert_path):
                log_buffer.log(
                    'Hosts',
                    'Skipped Linux WebView-visible intercept update because system trust is not ready',
                )
                return

        with self._lock:
            if desired_hosts == self._active_intercept_hosts:
                return
            proxy = _maybe_proxy(self._proxy)
            if not self._hosts_installed or proxy is None:
                self._active_intercept_hosts = set(desired_hosts)
                return

            self._refresh_hosts_interception_locked(desired_hosts, proxy)

    def refresh_custom_fflag_interception(self) -> None:
        """Apply the current custom FastFlag proxy toggle immediately."""
        self.prime_custom_fflag_cache()
        self.refresh_username_spoofer_interception()

    def prepare_custom_fflags_for_player_launch(self) -> None:
        """Arm one fresh custom-FFlag response for the next Player launch."""
        custom_modifier = getattr(self, 'custom_fflag_modifier', None)
        if custom_modifier is None:
            return
        enabled = custom_modifier.is_enabled()
        if enabled:
            custom_modifier.prepare_for_player_launch()
        seed_startup_flags = getattr(custom_modifier, 'prime_startup_flag_cache', None)
        if not callable(seed_startup_flags):
            seed_startup_flags = getattr(custom_modifier, 'prime_windows_flag_cache', None)
        if callable(seed_startup_flags):
            seed_startup_flags()
        if enabled:
            log_buffer.log(
                'CustomFFlags',
                'Armed a fresh ClientSettings response for Roblox Player launch',
            )

    def rearm_custom_fflag_delivery_for_player_launch(self) -> None:
        """Re-arm network delivery after the outgoing Player is fully stopped."""
        custom_modifier = getattr(self, 'custom_fflag_modifier', None)
        if custom_modifier is not None and custom_modifier.is_enabled():
            custom_modifier.prepare_for_player_launch()

    def prime_custom_fflag_cache(self, *, allow_running: bool = False) -> bool:
        """Preload startup-only custom FastFlags for the next Player launch."""
        custom_modifier = getattr(self, 'custom_fflag_modifier', None)
        if custom_modifier is None or (is_roblox_running() and not allow_running):
            return False
        seed_startup_flags = getattr(custom_modifier, 'prime_startup_flag_cache', None)
        if not callable(seed_startup_flags):
            seed_startup_flags = getattr(custom_modifier, 'prime_windows_flag_cache', None)
        if not callable(seed_startup_flags):
            return False
        return bool(seed_startup_flags())

    def _emit_proxy_start_error(self, code: str, details: _ErrorDetails) -> None:
        """Forward startup failures to the app layer for user-facing dialogs."""
        if self._on_proxy_start_error is None:
            return
        try:
            self._on_proxy_start_error(code, details)
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('Error', f'Failed to dispatch proxy startup error callback: {exc}')

    def _start_watchdog(self) -> None:
        """Start the platform crash guard/heartbeat thread."""
        self._watchdog_stop = threading.Event()
        stop_event = self._watchdog_stop

        def _loop() -> None:
            while not stop_event.wait(_WATCHDOG_INTERVAL):
                if IS_MACOS:
                    helper_heartbeat = cast(
                        'Callable[[], bool]',
                        _lazy_attr('fleasion.utils.macos_proxy_helper', 'helper_heartbeat'),
                    )
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
        active_hosts = self._startup_intercept_hosts()
        if _use_linux_privileged_helper():
            log_buffer.log(
                'Hosts',
                'Skipping in-place hosts refresh during Linux helper mode; helper owns /etc/hosts and port 443',
            )
            new_endpoints = _resolve_real_endpoints(active_hosts)
            if self._proxy is not None and new_endpoints:
                _set_proxy_upstream_endpoints(self._proxy, new_endpoints)
            scraper_ips = _endpoint_ip_candidates(new_endpoints)
            if scraper_ips:
                _set_cache_scraper_real_ips(self.cache_scraper, scraper_ips)
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
            _set_proxy_upstream_endpoints(self._proxy, new_endpoints)
        scraper_ips = _endpoint_ip_candidates(new_endpoints)
        if scraper_ips:
            _set_cache_scraper_real_ips(self.cache_scraper, scraper_ips)

    def _repair_cert_refresh_ips_and_restart_roblox(
        self, exe_path: Path, ca_pem: str, ca_file: Path, reason: str
    ) -> None:
        log_buffer.log('Certificate', f'{reason} — refreshing hosts and restarting...')

        def _patch_cert() -> None:
            check_and_patch_running_roblox_ca(exe_path)

        def _refresh_ips() -> None:
            self._refresh_proxy_ips_for_cert_repair()

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='fleasion-cert-refresh') as pool:
            f_cert = pool.submit(_patch_cert)
            f_ips = pool.submit(_refresh_ips)
        # Both futures are done after the with block (shutdown waits for them).

        for label, fut in (('cert patch', f_cert), ('IP refresh', f_ips)):
            if fut.exception():
                log_buffer.log('Certificate', f'Error during {label}: {fut.exception()}')

        _log_cacert_state(
            ca_file,
            ca_pem,
            f'cacert.pem before Roblox restart for {exe_path.parent.name}',
        )
        log_buffer.log(
            'Certificate',
            'Cert injected and IPs refreshed — waiting for Roblox to finish launching...',
        )
        if not wait_for_roblox_window(timeout=60.0):
            log_buffer.log(
                'Certificate',
                'Warning: Roblox window did not appear within 60 s — restarting anyway',
            )
        time.sleep(2)

        log_buffer.log('Certificate', 'Restarting Roblox...')
        terminate_roblox()
        if not wait_for_roblox_exit(timeout=15.0):
            log_buffer.log(
                'Certificate',
                'Warning: Roblox did not exit within 15 s — skipping restart',
            )
            return

        try:
            if not launch_as_standard_user(exe_path):
                msg = 'launch failed'
                raise OSError(msg)
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
            log_buffer.log(
                'Certificate',
                f'Skipping macOS Roblox Studio CA refresh for {exe_path.name}',
            )
            return
        ca_pem = get_ca_pem(ca_cert_path)
        if IS_MACOS:
            roblox_dir = _macos_resource_root_from_executable(exe_path) or exe_path.parent
        elif IS_LINUX:
            find_roblox_resource_dirs = cast(
                'Callable[..., list[Path]]',
                _lazy_attr('fleasion.utils.platform_linux', 'find_roblox_resource_dirs'),
            )
            dirs = find_roblox_resource_dirs(include_studio=False)
            roblox_dir = dirs[0] if dirs else exe_path.parent
        else:
            roblox_dir = exe_path.parent
        ca_file = roblox_dir / 'ssl' / 'cacert.pem'

        if not self._cert_refresh_lock.acquire(blocking=False):
            log_buffer.log(
                'Certificate',
                f'Roblox launch CA refresh already in progress for {exe_path.parent.name}; skipping duplicate trigger',
            )
            return

        try:
            initial_state = _log_cacert_state(
                ca_file,
                ca_pem,
                f'Roblox launch initial cacert.pem state for {exe_path.parent.name}',
            )
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
            stable_state = _log_cacert_state(
                ca_file,
                ca_pem,
                f'Roblox launch settled cacert.pem state for {exe_path.parent.name}',
            )
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
                    log_buffer.log(
                        'Certificate',
                        f'Roblox launch detected: stable patched cert confirmed for {exe_path.parent.name}',
                    )
                    return

                time.sleep(_CACERT_LAUNCH_POLL_INTERVAL_SECONDS)
                next_state = _log_cacert_state(
                    ca_file,
                    ca_pem,
                    f'Roblox launch polling cacert.pem state for {exe_path.parent.name}',
                )
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

    @staticmethod
    def _is_windows_proactor_accept_fault(
        loop: asyncio.AbstractEventLoop, context: dict[str, object]
    ) -> bool:
        exc = context.get('exception')
        return bool(
            IS_WINDOWS
            and 'proactor' in type(loop).__name__.lower()
            and context.get('message') == 'Accept failed on a socket'
            and isinstance(exc, OSError)
            and getattr(exc, 'winerror', None) == _WINDOWS_PROACTOR_ACCEPT_WINERROR
        )

    def _install_proxy_loop_diagnostics(
        self,
        loop: asyncio.AbstractEventLoop,
        env_proxy_mode: bool,
    ) -> None:
        """Capture accept failures swallowed by asyncio's Proactor server loop."""
        self._windows_proactor_accept_fault = False
        loop_name = type(loop).__name__
        mode = 'env' if env_proxy_mode else 'hosts'
        runtime = (
            f'Python {platform.python_version()} ({platform.python_implementation()}); '
            f'OpenSSL={ssl.OPENSSL_VERSION}; '
            f'local_tls_max={PROXY_TLS_MAX_VERSION.name}; '
            f'OS={platform.platform()}; machine={platform.machine() or "unknown"}'
        )
        log_buffer.log(
            'ProxyDiag',
            f'Proxy event loop: {loop_name}; mode={mode}; '
            f'selector_fallback={"yes" if getattr(self, "_windows_selector_fallback_attempted", False) else "no"}; '
            f'{runtime}',
        )

        previous_handler = loop.get_exception_handler()

        def _handle_loop_exception(
            active_loop: asyncio.AbstractEventLoop, context: dict[str, object]
        ) -> None:
            if self._is_windows_proactor_accept_fault(active_loop, context):
                exc = context['exception']
                socket_obj = context.get('socket')
                local_address = _socket_local_address(socket_obj)
                if not self._windows_proactor_accept_fault:
                    self._windows_proactor_accept_fault = True
                    log_buffer.log(
                        'ProxyDiag',
                        'Windows Proactor accept failed after bind: '
                        f'winerror={getattr(exc, "winerror", None)}; '
                        f'errno={getattr(exc, "errno", None)}; '
                        f'loop={type(active_loop).__name__}; mode={mode}; '
                        f'local_address={local_address}; {runtime}; exception={exc!r}. '
                        'The listener may be closed by asyncio; '
                        'a scoped SelectorEventLoop retry will be attempted.',
                    )

            if previous_handler is not None:
                previous_handler(active_loop, context)
            else:
                active_loop.default_exception_handler(context)

        loop.set_exception_handler(_handle_loop_exception)

    def _run_proxy_with_windows_selector_fallback(self) -> None:
        try:
            asyncio.run(self._run_proxy())
        except _RetryProxyWithWindowsSelectorError:
            self._windows_selector_fallback_attempted = True
            reason = (
                f'Proactor accept WinError {_WINDOWS_PROACTOR_ACCEPT_WINERROR}'
                if getattr(self, '_windows_proactor_accept_fault', False)
                else 'Proactor TLS listener failure with healthy blocking TLS'
            )
            log_buffer.log(
                'Proxy',
                'Retrying proxy startup with Windows SelectorEventLoop after ' + reason,
            )
            asyncio.run(self._run_proxy(), loop_factory=asyncio.SelectorEventLoop)

    def _run_proxy_worker(self) -> None:
        self._windows_selector_fallback_attempted = False
        try:
            self._run_proxy_with_windows_selector_fallback()
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('Error', f'Proxy failed: {exc}')
            self._running = False
        finally:
            # Flatpak overrides outlive this process.  Always disarm the exact
            # client captured by this worker, including when serve_forever()
            # returns unexpectedly or startup aborts after flags were primed.
            self._cleanup_linux_client_proxy_state()

    async def _run_startup_tls_self_test(
        self,
        hosts: set[str],
        ca_cert_path: Path,
        port: int,
        *,
        explicit_proxy: bool,
    ) -> bool:
        """Validate local TLS and try narrow Windows-only recovery profiles.

        The default remains IPv4 + TLS 1.2.  On Windows, a failed local
        handshake may be caused by the temporary TLS 1.2 compatibility cap
        or by an IPv4-only loopback filter.  Probe one representative host
        before the full route set so those fallbacks do not multiply startup
        time for a machine whose loopback TLS path is broken.
        """
        hosts = set(hosts)
        if not hosts:
            self._tls_startup_attempts = []
            return True

        if not IS_WINDOWS:
            ok = await _run_tls_self_test(hosts, ca_cert_path, port, explicit_proxy=explicit_proxy)
            self._tls_startup_attempts = [
                {
                    'loopback': '127.0.0.1',
                    'tls_max': PROXY_TLS_MAX_VERSION.name,
                    'ok': ok,
                    'failures': [],
                }
            ]
            return ok

        profiles: list[tuple[str, ssl.TLSVersion]] = [
            ('127.0.0.1', PROXY_TLS_MAX_VERSION),
            ('127.0.0.1', ssl.TLSVersion.MAXIMUM_SUPPORTED),
        ]
        loopback_ips = cast(
            'Callable[[], set[str]] | None',
            getattr(self._proxy, 'loopback_ips_for_hosts', None),
        )
        available_loopbacks: set[str] = (
            set(loopback_ips()) if callable(loopback_ips) else {'127.0.0.1', '::1'}
        )
        if explicit_proxy and '::1' in available_loopbacks:
            profiles.extend(
                [
                    ('::1', PROXY_TLS_MAX_VERSION),
                    ('::1', ssl.TLSVersion.MAXIMUM_SUPPORTED),
                ]
            )

        representative_hosts = {min(hosts)}
        attempts: list[_ErrorDetails] = []
        current_tls_max = PROXY_TLS_MAX_VERSION

        for index, (loopback_host, tls_max_version) in enumerate(profiles):
            if tls_max_version != current_tls_max:
                set_tls_max = getattr(self._proxy, 'set_local_tls_max_version', None)
                if not callable(set_tls_max):
                    continue
                set_tls_max(tls_max_version)
                current_tls_max = tls_max_version

            probe_hosts = representative_hosts if len(profiles) > 1 else hosts
            ok, failures = await _tls_self_test_result(
                probe_hosts,
                ca_cert_path,
                port,
                explicit_proxy,
                loopback_host,
                tls_max_version,
            )
            if ok and probe_hosts != hosts:
                ok, failures = await _tls_self_test_result(
                    hosts,
                    ca_cert_path,
                    port,
                    explicit_proxy,
                    loopback_host,
                    tls_max_version,
                )

            attempts.append(
                {
                    'loopback': loopback_host,
                    'tls_max': tls_max_version.name,
                    'ok': ok,
                    'failures': list(failures),
                }
            )
            if ok:
                self._active_local_tls_max_version = tls_max_version
                if explicit_proxy:
                    self._env_proxy_loopback_host = loopback_host
                _log_tls_self_test_passed(hosts, explicit_proxy)
                if index:
                    url_host = f'[{loopback_host}]' if ':' in loopback_host else loopback_host
                    log_buffer.log(
                        'ProxyDiag',
                        'Local TLS fallback selected: '
                        f'loopback={url_host}; tls_max={tls_max_version.name}; '
                        f'proxy_url=http://{url_host}:{port}',
                    )
                self._tls_startup_attempts = attempts
                return True

            _log_tls_self_test_failures(failures)
            if index + 1 < len(profiles):
                next_host, next_max = profiles[index + 1]
                next_url_host = f'[{next_host}]' if ':' in next_host else next_host
                log_buffer.log(
                    'ProxyDiag',
                    'Local TLS route failed; trying fallback '
                    f'loopback={next_url_host}; tls_max={next_max.name}',
                )

        self._tls_startup_attempts = attempts
        return False

    async def _raise_selector_retry_for_proactor_tls_failure(
        self, *, raw_tls_probe_ok: bool = False
    ) -> None:
        loop = getattr(self, '_loop', None)
        accept_fault = bool(getattr(self, '_windows_proactor_accept_fault', False))
        if not IS_WINDOWS or loop is None:
            return
        is_proactor = 'proactor' in type(loop).__name__.lower()
        has_retry_signal = accept_fault or raw_tls_probe_ok
        fallback_attempted = bool(getattr(self, '_windows_selector_fallback_attempted', False))
        if not is_proactor or not has_retry_signal or fallback_attempted:
            return
        reason = (
            f'Windows Proactor accept WinError {_WINDOWS_PROACTOR_ACCEPT_WINERROR}'
            if accept_fault
            else 'asyncio TLS failed while the blocking raw TLS probe passed'
        )
        log_buffer.log(
            'ProxyDiag',
            f'TLS startup self-test failed after {reason}; cleaning up the failed '
            'listener before Selector retry',
        )
        try:
            proxy = cast('FleasionProxy', self._proxy)
            await proxy.stop()
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('ProxyDiag', f'Failed listener cleanup reported: {exc}')
        self._proxy = None
        self._active_proxy_port = None
        self._env_proxy_ready.clear()
        _set_active_hosts_loopbacks(None)
        self._running = False
        raise _RetryProxyWithWindowsSelectorError

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._env_proxy_ready.clear()
            self._hosts_proxy_ready.clear()
            self._active_proxy_port = None
            self._env_proxy_loopback_host = '127.0.0.1'
            self._active_local_tls_max_version = PROXY_TLS_MAX_VERSION
            self._tls_startup_attempts = []

            self._thread = threading.Thread(
                target=self._run_proxy_worker,
                daemon=True,
                name='fleasion-proxy',
            )
            self._thread.start()

    def restart_for_mode_switch(self) -> None:
        """Live-swap the running proxy to a new mode without restarting the
        app. The mode change (self.config_manager.proxy_mode) must already
        be persisted by the caller before this runs; stop() cleans up
        whatever the old mode had in place (hosts entries, port bindings),
        then start() picks the new mode back up from config. Only safe to
        call when the new mode needs nothing this process doesn't already
        have - callers should restart the whole app instead when the new
        mode might require elevation this process doesn't hold.
        """

        # Publish the transition before starting the worker. Consumers such
        # as Windows GDK arming must not observe the old proxy as ready and
        # capture its stale port while this restart thread is still pending.
        self._env_proxy_ready.clear()

        def _do_restart() -> None:
            self.stop()
            self.start()

        threading.Thread(target=_do_restart, daemon=True, name='fleasion-proxy-mode-switch').start()

    def stop(self) -> None:
        ready_event = getattr(self, '_env_proxy_ready', None)
        if ready_event is not None:
            ready_event.clear()
        hosts_ready_event = getattr(self, '_hosts_proxy_ready', None)
        if hosts_ready_event is not None:
            hosts_ready_event.clear()
        texture_stripper = getattr(self, '_texture_stripper', None)
        if texture_stripper is not None:
            texture_stripper.reset_routes('proxy stop')
        self._stop_linux_sober_custom_fflag_timer()
        self._cleanup_linux_client_proxy_state()
        with self._lock:
            if not self._running and not (self._thread and self._thread.is_alive()):
                return
            log_buffer.log('Proxy', 'Stopping proxy...')
            self._active_env_proxy_mode = False

            # Clean up hosts file first so Roblox stops routing to us immediately
            if self._hosts_installed:
                self._stop_watchdog()  # Cancel the force-kill guard task first
                if _use_linux_privileged_helper():
                    stop_helper = cast(
                        'Callable[[], bool]',
                        _lazy_attr('fleasion.utils.linux_proxy_helper', 'stop_helper'),
                    )
                    hosts_cleaned = stop_helper()
                else:
                    cleanup_details: _ErrorDetails = {}
                    hosts_cleaned = _remove_hosts_entries(
                        set(INTERCEPT_HOSTS), error_details=cleanup_details
                    )
                    if not hosts_cleaned:
                        log_buffer.log(
                            'Error',
                            'Proxy shutdown could not remove Fleasion hosts entries; '
                            f'they may still redirect traffic: {cleanup_details.get("error") or "unknown cleanup failure"}',
                        )
                    _flush_dns()  # Clear stale 127.0.0.1 cache so new connections stop coming in
                self._hosts_installed = False
                # Only cancel the reboot guard if the hosts file was actually cleaned.
                # If cleanup failed, the PendingFileRenameOperations entry must remain
                # so the next reboot still removes our entries automatically.
                if hosts_cleaned:
                    _cancel_hosts_cleanup_on_reboot()
                with contextlib.suppress(OSError):
                    _PROXY_OWNER_PID_FILE.unlink(missing_ok=True)

            # Stop the asyncio server
            if self._proxy and self._loop and self._loop.is_running():
                with contextlib.suppress(Exception):
                    fut = asyncio.run_coroutine_threadsafe(self._proxy.stop(), self._loop)
                    fut.result(timeout=3.0)
            _set_active_hosts_loopbacks(None)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                log_buffer.log('Proxy', 'Warning: proxy thread did not stop cleanly')

    def _proxy_privilege_preflight(self, env_proxy_mode: bool) -> bool:
        if not env_proxy_mode and IS_MACOS:
            helper_is_ready = cast(
                'Callable[[], bool]',
                _lazy_attr('fleasion.utils.macos_proxy_helper', 'helper_is_ready'),
            )
            if not helper_is_ready():
                log_buffer.log('Error', 'The macOS proxy helper is not installed or not running')
                self._emit_proxy_start_error('macos_helper_unavailable', {})
                self._running = False
                return False
        elif not env_proxy_mode and not _is_admin() and not _use_linux_privileged_helper():
            log_buffer.log(
                'Error',
                (
                    'Fleasion requires administrator privileges to modify the hosts file '
                    'and bind port 443.  Please run as Administrator.'
                ),
            )
            self._running = False
            return False
        return True

    def _handle_proxy_startup_cache_clear(self, env_proxy_mode: bool) -> None:
        custom_fflags_active = bool(getattr(self.config_manager, 'custom_fflags_enabled', False))
        if env_proxy_mode and is_roblox_running():
            log_buffer.log(
                'Cleanup',
                'Roblox Env Proxy mode detected Roblox already running; '
                'skipping cache clear to preserve the launch deeplink',
            )
        elif custom_fflags_active and is_roblox_running():
            log_buffer.log(
                'Cleanup',
                'Custom FastFlags are active and Roblox is already running; skipping cache clear',
            )
        elif self.config_manager.clear_cache_on_launch:
            log_buffer.log('Cleanup', 'Clear cache on launch enabled - deleting cache')

            def _delete_and_log() -> None:
                messages = delete_cache()
                for msg in messages:
                    log_buffer.log('Cache', msg)

            run_in_thread(_delete_and_log)()
        else:
            log_buffer.log('Cleanup', 'Cache clear on launch disabled - skipping')

    def _generate_proxy_startup_certificates(self) -> _ProxyCertificateState | None:
        log_buffer.log('Certificate', 'Generating/loading CA certificates...')
        t0 = time.perf_counter()
        proxy_ca_dir = _select_proxy_ca_dir()
        ca_paths = _generate_proxy_certificate(
            'CA generation failed',
            partial(generate_ca, proxy_ca_dir),
        )
        if ca_paths is None:
            self._running = False
            return None
        ca_cert_path, ca_key_path = ca_paths

        host_certs: dict[str, tuple[Path, Path]] = {}
        for host in INTERCEPT_HOSTS:
            host_cert = _generate_proxy_certificate(
                f'Leaf cert failed for {host}',
                partial(generate_host_cert, host, ca_cert_path, ca_key_path, proxy_ca_dir),
            )
            if host_cert is None:
                self._running = False
                return None
            host_certs[host] = host_cert

        default_cert = _generate_proxy_certificate(
            'Default multi-host cert failed',
            partial(
                generate_multi_host_cert,
                'intercept-default',
                INTERCEPT_HOSTS,
                ca_cert_path,
                ca_key_path,
                proxy_ca_dir,
            ),
        )
        if default_cert is None:
            self._running = False
            return None

        elapsed_ms = (time.perf_counter() - t0) * 1000
        log_buffer.log('Certificate', f'Certificates ready in {elapsed_ms:.0f} ms')
        return {
            'proxy_ca_dir': proxy_ca_dir,
            'ca_cert_path': ca_cert_path,
            'ca_key_path': ca_key_path,
            'host_certs': host_certs,
            'default_cert': default_cert,
        }

    def _configure_proxy_startup_trust(
        self,
        env_proxy_mode: bool,
        certificates: _ProxyCertificateState,
    ) -> _ProxyStartupState | None:
        ca_cert_path = certificates['ca_cert_path']
        ca_pem = get_ca_pem(ca_cert_path)
        selected_linux_client_key = self._linux_client_key()
        ca_patch_ok, ca_patch_details = _install_ca_into_roblox(
            ca_pem, include_studio=not env_proxy_mode
        )
        if IS_MACOS and not ca_patch_ok:
            log_buffer.log(
                'Certificate',
                'macOS Roblox CA patch verification failed; proxy startup aborted before writing hosts entries',
            )
            self._emit_proxy_start_error('macos_ca_patch_failed', ca_patch_details)
            self._running = False
            return None
        if env_proxy_mode and not ca_patch_ok:
            if not _env_proxy_global_ca_patch_failure_is_fatal():
                failed = _failed_details(ca_patch_details.get('failed'))
                log_buffer.log(
                    'Certificate',
                    'One or more discovered Roblox CA bundles were not launch-healthy during '
                    f'Env Proxy startup ({len(failed)} failed); continuing because the actual '
                    'Player executable will be repaired and verified before relaunch',
                )
            else:
                log_buffer.log(
                    'Certificate',
                    'Roblox CA patch verification failed; Env Proxy startup aborted before relaunch',
                )
                self._emit_proxy_start_error('roblox_ca_patch_failed', ca_patch_details)
                self._running = False
                return None
        if IS_MACOS:
            trust_ok, trust_details = _install_ca_into_macos_login_keychain(ca_cert_path, ca_pem)
            if not trust_ok:
                details = dict(trust_details)
                details.setdefault('error', 'Could not trust Fleasion CA in login keychain')
                log_buffer.log(
                    'Certificate',
                    'macOS CA trust verification failed; proxy startup aborted before writing hosts entries',
                )
                self._emit_proxy_start_error('macos_ca_trust_failed', details)
                self._running = False
                return None
            log_buffer.log(
                'Certificate',
                'macOS login keychain CA trust installed'
                if trust_details.get('changed')
                else 'macOS login keychain CA trust already current',
            )
        if IS_WINDOWS and not env_proxy_mode:
            _install_ca_into_windows_root(ca_cert_path, ca_pem)
        elif IS_LINUX:
            install_ca_into_linux_trust = cast(
                'Callable[..., object]',
                _lazy_attr('fleasion.utils.linux_proxy_helper', 'install_ca_into_linux_trust'),
            )
            install_ca_into_linux_trust(
                ca_cert_path,
                install_system=False,
            )

        return {
            **certificates,
            'ca_pem': ca_pem,
            'selected_linux_client_key': selected_linux_client_key,
            'ca_patch_ok': ca_patch_ok,
        }

    def _cleanup_stale_proxy_startup_state(self, env_proxy_mode: bool) -> bool:
        if env_proxy_mode:
            if not _other_proxy_owner_alive():
                oversized_hosts_details: _ErrorDetails = {}
                if hosts_file_is_oversized(oversized_hosts_details):
                    log_buffer.log(
                        'Error',
                        'Hosts file is still too large after Env Proxy startup checks; '
                        'aborting to avoid leaving unsafe stale redirects in place',
                    )
                    self._emit_proxy_start_error('hosts_file_too_large', oversized_hosts_details)
                    self._running = False
                    return False
                if has_stale_hosts_entries(set(INTERCEPT_HOSTS)):
                    log_buffer.log(
                        'Error',
                        'Stale proxy hosts entries still exist while starting Roblox Env Proxy '
                        'mode; privileged cleanup was not completed. They may still redirect '
                        f'some hosts to 127.0.0.1. Hosts file: {HOSTS_FILE}',
                    )
                else:
                    _flush_dns()
            log_buffer.log(
                'Proxy',
                'Roblox Env Proxy mode active; skipping privileged relay startup',
            )
        elif _use_linux_privileged_helper():
            log_buffer.log(
                'ProxyHelper',
                'Linux user-mode GUI active; privileged helper will own port 443 and hosts entries',
            )
        elif not _other_proxy_owner_alive():
            _delete_watchdog_task()
            stale_hosts_error_details: _ErrorDetails = {}
            if not _remove_hosts_entries(
                set(INTERCEPT_HOSTS), error_details=stale_hosts_error_details
            ):
                log_buffer.log(
                    'Error',
                    'Failed to remove stale proxy hosts entries — real CDN IPs '
                    'cannot be resolved safely.  Aborting proxy start. '
                    'If the problem persists, manually remove "# Fleasion proxy entry" '
                    f'lines from {HOSTS_FILE} and restart.',
                )
                if stale_hosts_error_details.get('notify_user'):
                    self._emit_proxy_start_error(
                        _preserve_str(
                            stale_hosts_error_details.get('error_code', 'hosts_write_exhausted')
                        ),
                        stale_hosts_error_details,
                    )
                self._running = False
                return False
            _flush_dns()
        else:
            log_buffer.log('Proxy', 'Another proxy owner is running — skipping startup cleanup')
        return True

    def _prepare_proxy_startup(self, env_proxy_mode: bool) -> _ProxyStartupState | None:
        if not self._proxy_privilege_preflight(env_proxy_mode):
            return None
        self._handle_proxy_startup_cache_clear(env_proxy_mode)
        self.prime_custom_fflag_cache()
        certificates = self._generate_proxy_startup_certificates()
        if certificates is None:
            return None
        startup = self._configure_proxy_startup_trust(env_proxy_mode, certificates)
        if startup is None:
            return None
        if not self._cleanup_stale_proxy_startup_state(env_proxy_mode):
            return None
        return startup

    async def _start_proxy_server(
        self,
        proxy: FleasionProxy,
        env_proxy_mode: bool,
        listen_port: int,
    ) -> bool:
        try:
            await proxy.start()
        except OSError as exc:
            err_text = str(exc).lower()
            native_error = getattr(exc, 'winerror', None)
            bind_error = (
                exc.errno in {10013, 10048}
                or native_error in {10013, 10048}
                or 'access' in err_text
                or 'address already in use' in err_text
                or 'only one usage of each socket address' in err_text
                or (str(listen_port) in err_text and 'bind' in err_text)
            )
            owners = _list_port_listeners(listen_port) if bind_error else []

            if bind_error and env_proxy_mode and IS_WINDOWS and not owners:
                fixed_port = listen_port
                try:
                    proxy.port = 0
                    await proxy.start()
                    fallback_port = int(proxy.port)
                    self._active_proxy_port = fallback_port
                    log_buffer.log(
                        'Proxy',
                        f'Fixed Env Proxy port {fixed_port} was unavailable; '
                        f'using free loopback port {fallback_port}',
                    )
                except OSError as fallback_exc:
                    log_buffer.log(
                        'Error',
                        f'Failed to bind Env Proxy fallback port after {fixed_port}: '
                        f'{fallback_exc}',
                    )
                    self._emit_proxy_start_error(
                        'port_bind_failed',
                        {
                            'port': fixed_port,
                            'owners': owners,
                            'bind_error': str(exc),
                            'fallback_error': str(fallback_exc),
                            'bind_reason': 'access_denied_or_reserved',
                        },
                    )
                    self._running = False
                    return False
            elif bind_error:
                log_buffer.log(
                    'Error',
                    f'Cannot bind local proxy backend port {listen_port}: another process is already listening.',
                )
                if owners:
                    owners_summary = '; '.join(
                        f'{owner["process_name"]} (PID {owner["pid"]}) on {owner["local_address"]}:{listen_port}'
                        for owner in owners
                    )
                    log_buffer.log('Error', f'Port {listen_port} listeners: {owners_summary}')
                self._emit_proxy_start_error(
                    'port_bind_failed',
                    {
                        'port': listen_port,
                        'owners': owners,
                        'bind_error': str(exc),
                        'bind_reason': (
                            'access_denied_or_reserved'
                            if exc.errno == 10013 or native_error == 10013 or 'access' in err_text
                            else 'already_in_use'
                        ),
                    },
                )
                self._running = False
                return False
            else:
                log_buffer.log('Error', f'Failed to start proxy: {exc}')
                self._running = False
                return False
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('Error', f'Failed to start proxy: {exc}')
            self._running = False
            return False
        return True

    async def _create_and_start_proxy_server(
        self,
        env_proxy_mode: bool,
        startup: _ProxyStartupState,
    ) -> _ProxyServerState | None:
        active_hosts = self._startup_intercept_hosts()
        self._active_intercept_hosts = set(active_hosts)
        self._log_intercept_configuration('Startup routing selection', active_hosts)
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
        configured_upstream_mode = self.config_manager.upstream_transport_mode
        effective_upstream_mode = self._effective_upstream_mode()
        asset_connection_limit = self.config_manager.vpn_compat_max_assetdelivery_connections
        cdn_connection_limit = self.config_manager.vpn_compat_max_cdn_connections
        _log_upstream_transport_settings(
            configured_mode=configured_upstream_mode,
            effective_mode=effective_upstream_mode,
            system_proxy=system_http_proxy,
            manual_http_proxy=manual_http_proxy,
            manual_socks5_proxy=manual_socks5_proxy,
            asset_limit=asset_connection_limit,
            cdn_limit=cdn_connection_limit,
        )

        self._texture_stripper = TextureStripper(self.config_manager)
        _set_texture_scraper(self._texture_stripper, self.cache_scraper)
        scraper_ips = _endpoint_ip_candidates(real_endpoints)
        _set_cache_scraper_real_ips(self.cache_scraper, scraper_ips)

        with contextlib.suppress(Exception):
            asset_fetcher_thread = _lazy_attr(
                'fleasion.gui.json_viewer', 'AssetFetcherThread'
            )
            setter_name = 'set_scraper'
            set_scraper = cast(
                'Callable[[CacheScraper], None]',
                getattr(asset_fetcher_thread, setter_name),
            )
            set_scraper(self.cache_scraper)

        use_linux_helper = (not env_proxy_mode) and _use_linux_privileged_helper()
        env_proxy_intercept_excluded_hosts: set[str] = set()
        if env_proxy_mode and IS_LINUX:
            env_proxy_intercept_excluded_hosts.update(self._linux_env_proxy_excluded_hosts())
        self._env_proxy_intercept_excluded_hosts = set(env_proxy_intercept_excluded_hosts)
        listen_port = (
            MACOS_PROXY_BACKEND_PORT
            if env_proxy_mode or IS_MACOS or use_linux_helper
            else PROXY_PORT
        )
        if (
            IS_LINUX
            and not env_proxy_mode
            and not use_linux_helper
            and not _ensure_linux_system_trust_for_hosts(active_hosts, startup['ca_cert_path'])
        ):
            self._running = False
            return None

        def on_upstream_connect_failure(host: str, error: str) -> None:
            self._emit_proxy_start_error(
                'upstream_connect_failed',
                {
                    'host': host,
                    'error': error,
                    'proxy_mode': 'env' if env_proxy_mode else 'hosts',
                    'listen_port': listen_port,
                },
            )

        proxy = FleasionProxy(
            texture_stripper=self._texture_stripper,
            cache_scraper=self.cache_scraper,
            host_certs=startup['host_certs'],
            upstream_endpoints=_upstream_endpoint_map(real_endpoints),
            default_cert=startup['default_cert'],
            port=listen_port,
            upstream_mode=effective_upstream_mode,
            system_http_proxy=system_http_proxy,
            manual_http_proxy=manual_http_proxy,
            manual_socks5_proxy=manual_socks5_proxy,
            wire_preserving_passthrough=self._effective_wire_preserving_passthrough(),
            explicit_proxy=env_proxy_mode,
            intercept_hosts=active_hosts,
            intercept_all_hosts=getattr(self, '_env_proxy_intercept_all', False),
            intercept_excluded_hosts=env_proxy_intercept_excluded_hosts,
            auto_replace_rules=self.get_auto_replace_rules(),
            ca_cert_path=startup['ca_cert_path'],
            ca_key_path=startup['ca_key_path'],
            cert_cache_dir=startup['proxy_ca_dir'],
            vpn_compat_max_assetdelivery_connections=asset_connection_limit,
            vpn_compat_max_cdn_connections=cdn_connection_limit,
            custom_fflag_modifier=getattr(self, 'custom_fflag_modifier', None),
            on_upstream_connect_failure=on_upstream_connect_failure,
            upstream_endpoint_refresher=_refresh_real_upstream_endpoints,
        )
        self._proxy = proxy
        with self._lock:
            interceptors = list(self._module_interceptors)
        proxy.set_module_interceptors(interceptors)
        if hasattr(proxy, 'set_intercept_match'):
            proxy.set_intercept_match(self._env_proxy_intercept_match)
        await proxy.log_upstream_self_test(active_hosts)
        if not await self._start_proxy_server(proxy, env_proxy_mode, listen_port):
            return None

        if env_proxy_mode:
            self._active_proxy_port = int(proxy.port)
            listen_port = self._active_proxy_port
        _set_active_hosts_loopbacks(_loopback_ips(proxy) if IS_WINDOWS else None)
        return {
            'active_hosts': active_hosts,
            'use_linux_helper': use_linux_helper,
            'env_proxy_intercept_excluded_hosts': env_proxy_intercept_excluded_hosts,
            'listen_port': listen_port,
        }

    async def _verify_proxy_startup_tls(
        self,
        env_proxy_mode: bool,
        startup: _ProxyStartupState,
        server: _ProxyServerState,
    ) -> bool:
        active_hosts = server['active_hosts']
        listen_port = server['listen_port']
        if await self._run_startup_tls_self_test(
            set(active_hosts),
            startup['ca_cert_path'],
            listen_port,
            explicit_proxy=env_proxy_mode,
        ):
            return True

        probe_host = min(active_hosts)
        tls_attempts = list(getattr(self, '_tls_startup_attempts', []))
        raw_tls_attempts: list[_ErrorDetails] = []
        raw_probe_ok = False
        for attempt in tls_attempts:
            loopback_host = str(attempt.get('loopback') or '127.0.0.1')
            tls_max_name = str(attempt.get('tls_max') or PROXY_TLS_MAX_VERSION.name)
            tls_max_version = getattr(
                ssl.TLSVersion,
                tls_max_name,
                PROXY_TLS_MAX_VERSION,
            )
            try:
                candidate_ok, candidate_detail = await _run_raw_tls_loopback_probe(
                    probe_host,
                    startup['ca_cert_path'],
                    startup['default_cert'][0],
                    startup['default_cert'][1],
                    loopback_host=loopback_host,
                    tls_max_version=tls_max_version,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                candidate_ok = False
                candidate_detail = f'{type(exc).__name__}: {exc}'
            raw_tls_attempts.append(
                {
                    'loopback': loopback_host,
                    'tls_max': tls_max_name,
                    'ok': candidate_ok,
                    'detail': candidate_detail,
                }
            )
            candidate_status = 'passed' if candidate_ok else 'failed'
            log_buffer.log(
                'TLS',
                f'Raw TLS loopback probe {candidate_status} for {probe_host}: '
                f'loopback={loopback_host}; tls_max={tls_max_name}; {candidate_detail}',
            )
            if candidate_ok:
                raw_probe_ok = True
                break

        memory_tls_attempts: list[_ErrorDetails] = []
        memory_probe_ok = False
        seen_tls_max: set[str] = set()
        for attempt in tls_attempts:
            tls_max_name = str(attempt.get('tls_max') or PROXY_TLS_MAX_VERSION.name)
            if tls_max_name in seen_tls_max:
                continue
            seen_tls_max.add(tls_max_name)
            tls_max_version = getattr(
                ssl.TLSVersion,
                tls_max_name,
                PROXY_TLS_MAX_VERSION,
            )
            try:
                candidate_ok, candidate_detail = await _run_in_memory_tls_probe(
                    probe_host,
                    startup['ca_cert_path'],
                    startup['default_cert'][0],
                    startup['default_cert'][1],
                    tls_max_version,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                candidate_ok = False
                candidate_detail = f'{type(exc).__name__}: {exc}'
            memory_tls_attempts.append(
                {
                    'tls_max': tls_max_name,
                    'ok': candidate_ok,
                    'detail': candidate_detail,
                }
            )
            candidate_status = 'passed' if candidate_ok else 'failed'
            log_buffer.log(
                'TLS',
                f'In-memory TLS probe {candidate_status} for {probe_host}: '
                f'tls_max={tls_max_name}; {candidate_detail}',
            )
            memory_probe_ok = memory_probe_ok or candidate_ok

        if memory_probe_ok and not raw_probe_ok:
            log_buffer.log(
                'ProxyDiag',
                'OpenSSL/certificate TLS passed in memory but every tested local TCP '
                'TLS profile failed; a Windows socket/filter driver is the likely failure boundary',
            )
        await self._raise_selector_retry_for_proactor_tls_failure(raw_tls_probe_ok=raw_probe_ok)
        log_buffer.log(
            'Error',
            'Proxy startup aborted: TLS self-test failed for active intercept hosts',
        )
        self._emit_proxy_start_error(
            'tls_self_test_failed',
            {
                'hosts': sorted(active_hosts),
                'proxy_mode': 'env' if env_proxy_mode else 'hosts',
                'event_loop': type(self._loop).__name__,
                'python': platform.python_version(),
                'selector_fallback_attempted': getattr(
                    self, '_windows_selector_fallback_attempted', False
                ),
                'tls_attempts': tls_attempts,
                'raw_tls_attempts': raw_tls_attempts,
                'memory_tls_attempts': memory_tls_attempts,
                'in_memory_tls_ok': memory_probe_ok,
                'raw_tls_loopback_ok': raw_probe_ok,
            },
        )
        proxy = cast('FleasionProxy', self._proxy)
        await proxy.stop()
        _set_active_hosts_loopbacks(None)
        self._running = False
        return False

    async def _serve_env_proxy(
        self,
        startup: _ProxyStartupState,
        server: _ProxyServerState,
    ) -> None:
        proxy = cast('FleasionProxy', self._proxy)
        active_hosts = server['active_hosts']
        listen_port = server['listen_port']
        env_proxy_intercept_excluded_hosts = server['env_proxy_intercept_excluded_hosts']
        _set_active_hosts_loopbacks(None)
        if IS_LINUX and not self._arm_linux_env_proxy_override():
            log_buffer.log(
                'Error',
                'Linux Env Proxy startup aborted because the selected client override could not be armed',
            )
            self._emit_proxy_start_error(
                'linux_env_proxy_override_failed',
                {'client': startup['selected_linux_client_key'] or 'unknown'},
            )
            await proxy.stop()
            self._running = False
            return
        self._active_env_proxy_mode = True
        ready_event = getattr(self, '_env_proxy_ready', None)
        if ready_event is not None:
            ready_event.set()
        if env_proxy_intercept_excluded_hosts:
            client_name = getattr(
                self._linux_client_installation(),
                'display_name',
                'Linux Roblox client',
            )
            log_buffer.log(
                'Proxy',
                f'{client_name} bootstrap hosts remain tunneled even when Proxy-tab '
                'intercept-all is enabled: '
                f'{", ".join(sorted(env_proxy_intercept_excluded_hosts))}',
            )
        proxy.clear_request_log()
        self._start_linux_sober_custom_fflag_timer()

        log_buffer.log('Info', '=' * 50)
        log_buffer.log('Info', 'Fleasion Proxy Active')
        log_buffer.log('Info', f'Proxy mode: Roblox Env Proxy ({self.roblox_env_proxy_url()})')
        log_buffer.log('Info', f'Intercepting: {", ".join(sorted(active_hosts))}')
        log_buffer.log('Info', f'Port: {listen_port}')
        log_buffer.log('Info', 'Launch Roblox through Fleasion or let Fleasion relaunch it')
        log_buffer.log('Info', '=' * 50)

        texture_stripper = _maybe_texture(self._texture_stripper)
        if texture_stripper is not None:
            precheck_thread = threading.Thread(
                target=texture_stripper.precheck_replacements,
                name='ReplacementPrecheck',
                daemon=True,
            )
            precheck_thread.start()

        try:
            await proxy.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            ready_event = getattr(self, '_env_proxy_ready', None)
            if ready_event is not None:
                ready_event.clear()
            self._stop_linux_sober_custom_fflag_timer()
            self._running = False

    async def _serve_hosts_proxy(
        self,
        startup: _ProxyStartupState,
        server: _ProxyServerState,
    ) -> None:
        proxy = cast('FleasionProxy', self._proxy)
        active_hosts = server['active_hosts']
        use_linux_helper = server['use_linux_helper']
        listen_port = server['listen_port']
        ca_patch_ok = startup['ca_patch_ok']

        if use_linux_helper:
            last_start_error_details = cast(
                'Callable[[], Mapping[str, object]]',
                _lazy_attr('fleasion.utils.linux_proxy_helper', 'last_start_error_details'),
            )
            start_helper = cast(
                'Callable[..., bool]',
                _lazy_attr('fleasion.utils.linux_proxy_helper', 'start_helper'),
            )
            require_linux_system_ca = bool(active_hosts & USERNAME_SPOOFER_INTERCEPT_HOSTS)
            if not start_helper(
                active_hosts,
                backend_port=listen_port,
                ca_cert_path=startup['ca_cert_path'],
                require_system_ca=require_linux_system_ca,
            ):
                await proxy.stop()
                _set_active_hosts_loopbacks(None)
                details = _error_details(last_start_error_details())
                if details.get('code') == 'linux_hosts_read_only':
                    self._emit_proxy_start_error('linux_hosts_read_only', details)
                else:
                    self._emit_proxy_start_error('linux_helper_unavailable', details)
                self._running = False
                return
            if not ca_patch_ok:
                ca_patch_ok, _ = _install_ca_into_roblox(startup['ca_pem'])
                if not ca_patch_ok:
                    log_buffer.log(
                        'Certificate',
                        'Linux Roblox CA patch verification failed after privileged helper ownership repair',
                    )
                    await proxy.stop()
                    _set_active_hosts_loopbacks(None)
                    stop_helper = cast(
                        'Callable[[], bool]',
                        _lazy_attr('fleasion.utils.linux_proxy_helper', 'stop_helper'),
                    )
                    stop_helper()
                    self._running = False
                    return

        if IS_MACOS or use_linux_helper:
            relay_ok, relay_failures = await _run_privileged_relay_tls_self_test(
                set(INTERCEPT_HOSTS),
                startup['ca_cert_path'],
                PROXY_PORT,
            )
        else:
            relay_ok, relay_failures = True, []
        if not relay_ok:
            relay_details: _ErrorDetails = {
                'relay_port': PROXY_PORT,
                'backend_port': listen_port,
                'attempts': 3,
                'tls_failures': relay_failures,
            }
            if IS_MACOS:
                helper_state = _macos_helper_status()
                backend_probe = _macos_helper_probe_backend()
                relay_details['helper_status'] = helper_state or {}
                relay_details['backend_probe'] = backend_probe
                reachable = bool(backend_probe.get('reachable'))
                probe_summary = (
                    f'reachable={"yes" if reachable else "no"}; '
                    f'backend=127.0.0.1:{backend_probe.get("backend_port", listen_port)}; '
                    f'elapsed_ms={backend_probe.get("elapsed_ms", "unknown")}'
                )
                if backend_probe.get('error'):
                    probe_summary += (
                        f'; error={backend_probe.get("error_type") or "OSError"}: '
                        f'{backend_probe.get("error")}'
                    )
                log_buffer.log('ProxyHelper', f'macOS helper backend health probe: {probe_summary}')
            log_buffer.log('ProxyHelper', 'Privileged port-443 relay TLS self-test failed')
            await proxy.stop()
            _set_active_hosts_loopbacks(None)
            if use_linux_helper:
                stop_helper = cast(
                    'Callable[[], bool]',
                    _lazy_attr('fleasion.utils.linux_proxy_helper', 'stop_helper'),
                )
                stop_helper()
            self._running = False
            if IS_MACOS:
                self._emit_proxy_start_error('macos_relay_failed', relay_details)
            return

        hosts_error_details: _ErrorDetails = {}
        if use_linux_helper:
            self._hosts_installed = True
        elif not _add_hosts_entries(active_hosts, error_details=hosts_error_details):
            if hosts_error_details.get('notify_user'):
                self._emit_proxy_start_error(
                    _preserve_str(hosts_error_details.get('error_code', 'hosts_write_exhausted')),
                    hosts_error_details,
                )
            await proxy.stop()
            _set_active_hosts_loopbacks(None)
            self._running = False
            return
        else:
            self._hosts_installed = True
            _flush_dns()
        if not _verify_hosts_entries(active_hosts, error_details=hosts_error_details):
            if use_linux_helper:
                stop_helper = cast(
                    'Callable[[], bool]',
                    _lazy_attr('fleasion.utils.linux_proxy_helper', 'stop_helper'),
                )
                stop_helper()
            else:
                rollback_details: _ErrorDetails = {}
                if not _remove_hosts_entries(set(INTERCEPT_HOSTS), error_details=rollback_details):
                    log_buffer.log(
                        'Error',
                        'Proxy startup rollback could not remove Fleasion hosts entries: '
                        f'{rollback_details.get("error") or "unknown cleanup failure"}',
                    )
                _flush_dns()
            self._hosts_installed = False
            await proxy.stop()
            _set_active_hosts_loopbacks(None)
            self._running = False
            return
        with contextlib.suppress(OSError):
            _PROXY_OWNER_PID_FILE.write_text(str(os.getpid()))
        _schedule_hosts_cleanup_on_reboot()
        _upsert_watchdog_task()
        self._start_watchdog()
        self._start_linux_sober_custom_fflag_timer()
        hosts_ready_event = getattr(self, '_hosts_proxy_ready', None)
        if hosts_ready_event is not None:
            hosts_ready_event.set()

        log_buffer.log('Info', '=' * 50)
        log_buffer.log('Info', 'Fleasion Proxy Active')
        log_buffer.log('Info', f'Intercepting: {", ".join(sorted(active_hosts))}')
        log_buffer.log('Info', f'Port: {PROXY_PORT}')
        if IS_MACOS:
            log_buffer.log('Info', f'Unprivileged backend port: {listen_port}')
        log_buffer.log('Info', 'Launch Roblox')
        log_buffer.log('Info', '=' * 50)

        texture_stripper = _maybe_texture(self._texture_stripper)
        if texture_stripper is not None:
            precheck_thread = threading.Thread(
                target=texture_stripper.precheck_replacements,
                name='ReplacementPrecheck',
                daemon=True,
            )
            precheck_thread.start()
            threading.Thread(
                target=texture_stripper.precheck_anim_rigs,
                name='AnimRigPrecheck',
                daemon=True,
            ).start()

        try:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await proxy.serve_forever()
        finally:
            hosts_ready_event = getattr(self, '_hosts_proxy_ready', None)
            if hosts_ready_event is not None:
                hosts_ready_event.clear()
            self._stop_linux_sober_custom_fflag_timer()
            if self._hosts_installed:
                self._stop_watchdog()
                cleanup_details: _ErrorDetails = {}
                hosts_cleaned = _remove_hosts_entries(
                    set(INTERCEPT_HOSTS), error_details=cleanup_details
                )
                if not hosts_cleaned:
                    log_buffer.log(
                        'Error',
                        'Proxy worker shutdown could not remove Fleasion hosts entries; '
                        f'they may still redirect traffic: {cleanup_details.get("error") or "unknown cleanup failure"}',
                    )
                self._hosts_installed = False
                _flush_dns()
                if hosts_cleaned:
                    _cancel_hosts_cleanup_on_reboot()
                with contextlib.suppress(OSError):
                    _PROXY_OWNER_PID_FILE.unlink(missing_ok=True)
            with contextlib.suppress(Exception):
                await proxy.stop()
            _set_active_hosts_loopbacks(None)
            self._running = False
            self._loop = None

    async def _run_proxy(self) -> None:
        self._running = True
        self._loop = asyncio.get_running_loop()
        env_proxy_mode = self._use_env_proxy_mode()
        if IS_LINUX:
            self._active_linux_client_installation = _selected_linux_client_installation()
            self._active_linux_client_key = getattr(
                self._active_linux_client_installation,
                'key',
                None,
            )
        self._install_proxy_loop_diagnostics(self._loop, env_proxy_mode)

        startup = self._prepare_proxy_startup(env_proxy_mode)
        if startup is None:
            return

        server = await self._create_and_start_proxy_server(env_proxy_mode, startup)
        if server is None:
            return

        if not await self._verify_proxy_startup_tls(env_proxy_mode, startup, server):
            return

        if env_proxy_mode:
            await self._serve_env_proxy(startup, server)
            return
        await self._serve_hosts_proxy(startup, server)


# Public module-level aliases for cross-module orchestration.  The underscore-prefixed
# implementations remain for compatibility with existing internal imports and tests.
flush_dns = _flush_dns
other_proxy_owner_alive = _other_proxy_owner_alive
cancel_hosts_cleanup_on_reboot = _cancel_hosts_cleanup_on_reboot
remove_hosts_entries = _remove_hosts_entries
find_roblox_dirs = _find_roblox_dirs
