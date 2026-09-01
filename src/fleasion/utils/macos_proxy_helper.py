"""Client and one-time installer for the privileged macOS proxy helper."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import plistlib
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .json_types import as_object_dict
from .logging import log_buffer
from .paths import CONFIG_DIR, MACOS_PROXY_BACKEND_PORT, MACOS_PROXY_HELPER_CONTROL_PORT

HELPER_ID = 'com.fleasion.proxy-helper'
HELPER_BUNDLED_EXECUTABLE_NAME = 'fleasion-proxy-helper'
HELPER_BUNDLED_EXECUTABLE_NAMES = {
    'arm64': 'fleasion-proxy-helper-arm64',
    'x86_64': 'fleasion-proxy-helper-x86_64',
}
HELPER_INSTALL_PATH = Path('/Library/PrivilegedHelperTools') / HELPER_ID
HELPER_PLIST_PATH = Path('/Library/LaunchDaemons') / f'{HELPER_ID}.plist'
HELPER_TOKEN_FILE = CONFIG_DIR / 'proxy-helper.token'
HELPER_LOG_DIR = Path('/Library/Logs')
HELPER_LOG_PATH = HELPER_LOG_DIR / 'Fleasion.proxy-helper.log'
HELPER_STDOUT_LOG_PATH = HELPER_LOG_DIR / 'Fleasion.proxy-helper.stdout.log'
HELPER_STDERR_LOG_PATH = HELPER_LOG_DIR / 'Fleasion.proxy-helper.stderr.log'
EXPECTED_HELPER_VERSION = 7
REQUIRED_HELPER_CAPABILITIES = {'relay', 'patch_ca', 'probe_backend'}
# A first launch of a root-owned helper can be held briefly by macOS execution
# policy checks even after launchd has spawned it.  Keep this comfortably above
# that cold-start window, while still giving a real installation failure a
# bounded, actionable outcome.
HELPER_READY_TIMEOUT_SECONDS = 45.0
HELPER_READY_POLL_SECONDS = 0.25


type HelperObject = dict[str, object]


if TYPE_CHECKING:
    from collections.abc import Mapping


def _int_value(value: object) -> int:
    if not isinstance(value, int | float | str | bytes | bytearray):
        msg = 'macOS proxy helper value must be numeric'
        raise TypeError(msg)
    return int(value)


def _ensure_token() -> str:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if HELPER_TOKEN_FILE.exists():
        token = HELPER_TOKEN_FILE.read_text(encoding='utf-8').strip()
        if len(token) >= 32:
            with contextlib.suppress(OSError):
                HELPER_TOKEN_FILE.chmod(0o600)
            return token

    token = secrets.token_urlsafe(48)
    HELPER_TOKEN_FILE.write_text(token, encoding='utf-8')
    HELPER_TOKEN_FILE.chmod(0o600)
    return token


def _request(
    action: str,
    hosts: set[str] | None = None,
    timeout: float = 3.0,
    *,
    raise_on_error: bool = True,
    **payload: object,
) -> HelperObject:
    token = _ensure_token()
    request: HelperObject = {'token': token, 'action': action}
    if hosts is not None:
        request['hosts'] = sorted(hosts)
    request.update(payload)

    with socket.create_connection(
        ('127.0.0.1', MACOS_PROXY_HELPER_CONTROL_PORT), timeout=timeout
    ) as sock:
        sock.sendall((json.dumps(request, separators=(',', ':')) + '\n').encode('utf-8'))
        sock_file = sock.makefile('rb')
        raw = sock_file.readline(1024 * 1024)
    response_value: object = json.loads(raw.decode('utf-8'))
    response = as_object_dict(response_value)
    if response is None:
        msg = 'macOS proxy helper response must be a JSON object'
        raise TypeError(msg)
    if raise_on_error and not response.get('ok'):
        raise RuntimeError(str(response.get('error') or 'macOS proxy helper request failed'))
    return response


def helper_status(timeout: float = 1.0) -> HelperObject | None:
    try:
        return _request('status', timeout=timeout)
    except OSError, UnicodeError, json.JSONDecodeError, RuntimeError, TypeError:
        return None


def helper_has_expected_identity(status: Mapping[str, object] | None) -> bool:
    if not status:
        return False
    try:
        version_ok = _int_value(status.get('version', 0)) == EXPECTED_HELPER_VERSION
    except TypeError, ValueError:
        version_ok = False
    raw_capabilities = status.get('capabilities')
    capabilities: set[str] = set()
    if isinstance(raw_capabilities, list):
        capabilities.update(str(value) for value in cast('list[object]', raw_capabilities))
    return version_ok and REQUIRED_HELPER_CAPABILITIES.issubset(capabilities)


def helper_is_ready() -> bool:
    status = helper_status()
    if not status:
        return False
    try:
        backend_ok = _int_value(status.get('backend_port', 0)) == MACOS_PROXY_BACKEND_PORT
    except TypeError, ValueError:
        backend_ok = False
    if not backend_ok:
        return False
    if not helper_has_expected_identity(status):
        log_buffer.log(
            'ProxyHelper',
            'Installed macOS proxy helper identity does not match this app build; '
            f'expected version {EXPECTED_HELPER_VERSION}, got {status.get("version")!r}; '
            f'pid={status.get("pid")!r}, ppid={status.get("ppid")!r}, '
            f'executable={status.get("executable")!r}',
        )
        return False
    return True


def _helper_readiness_diagnostic() -> tuple[bool, str]:
    """Return readiness plus the reason a newly-installed helper is not ready."""
    try:
        status = _request('status', timeout=1.0)
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError, TypeError) as exc:
        return False, f'Could not contact the helper control service: {type(exc).__name__}: {exc}'

    try:
        backend_ok = _int_value(status.get('backend_port', 0)) == MACOS_PROXY_BACKEND_PORT
    except TypeError, ValueError:
        backend_ok = False
    if not backend_ok:
        return (
            False,
            (
                'Helper reported an unexpected backend port: '
                f'{status.get("backend_port")!r} (expected {MACOS_PROXY_BACKEND_PORT})'
            ),
        )
    if not helper_has_expected_identity(status):
        return (
            False,
            (
                'Helper identity does not match this app build: '
                f'version={status.get("version")!r} (expected {EXPECTED_HELPER_VERSION}), '
                f'capabilities={status.get("capabilities")!r}, '
                f'pid={status.get("pid")!r}, ppid={status.get("ppid")!r}, '
                f'executable={status.get("executable")!r}'
            ),
        )
    return True, ''


def helper_apply_hosts(hosts: set[str]) -> bool:
    try:
        _request('apply', set(hosts), timeout=5.0)
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError, TypeError) as exc:
        log_buffer.log('ProxyHelper', f'Failed to apply macOS hosts entries: {exc}')
        return False
    return True


def helper_clear_hosts() -> bool:
    try:
        _request('clear', timeout=5.0)
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError, TypeError) as exc:
        log_buffer.log('ProxyHelper', f'Failed to clear macOS hosts entries: {exc}')
        return False
    return True


def helper_heartbeat() -> bool:
    try:
        _request('heartbeat', timeout=2.0)
    except OSError, UnicodeError, json.JSONDecodeError, RuntimeError, TypeError:
        return False
    return True


def helper_probe_backend() -> HelperObject:
    try:
        return _request('probe_backend', timeout=3.0)
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError, TypeError) as exc:
        return {
            'ok': False,
            'reachable': False,
            'backend_port': MACOS_PROXY_BACKEND_PORT,
            'error_type': type(exc).__name__,
            'errno': getattr(exc, 'errno', None),
            'error': str(exc),
        }


def helper_patch_ca(ca_pem: str, installs: list[HelperObject]) -> HelperObject | None:
    try:
        response = _request(
            'patch_ca',
            timeout=10.0,
            raise_on_error=False,
            ca_pem=ca_pem,
            installs=installs,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError, TypeError) as exc:
        log_buffer.log('ProxyHelper', f'Failed to request macOS Roblox CA patch: {exc}')
        return None

    if not response.get('ok'):
        log_buffer.log(
            'ProxyHelper',
            f'macOS Roblox CA patch reported failure: {response.get("error") or "unknown error"}',
        )
    return response


def _source_helper_path() -> Path:
    frozen_meipass = getattr(sys, '_MEIPASS', None)
    if frozen_meipass:
        frozen_root = Path(frozen_meipass)
        machine = platform.machine().lower()
        helper_names = [
            HELPER_BUNDLED_EXECUTABLE_NAMES.get(machine),
            HELPER_BUNDLED_EXECUTABLE_NAME,
        ]
        helper_names.extend(HELPER_BUNDLED_EXECUTABLE_NAMES.values())
        # PyInstaller puts data files in Resources but native executables in
        # Frameworks inside a macOS .app bundle.  Installing the bundled Python
        # source as a fallback makes launchd depend on a system Python and can
        # fail before the helper has a chance to write its own log.
        bundle_roots = (frozen_root.parent / 'Frameworks', frozen_root)
        for bundle_root in bundle_roots:
            for helper_name in helper_names:
                if not helper_name:
                    continue
                bundled_executable = bundle_root / helper_name
                if bundled_executable.is_file() and os.access(bundled_executable, os.X_OK):
                    return bundled_executable

        # Return a useful missing path so install_helper can show a clear
        # packaged-build error instead of silently installing Python source.
        return frozen_root.parent / 'Frameworks' / HELPER_BUNDLED_EXECUTABLE_NAME
    return Path(__file__).resolve().parents[1] / 'macos_proxy_helper_daemon.py'


def _build_plist() -> bytes:
    return plistlib.dumps(
        {
            'Label': HELPER_ID,
            'ProgramArguments': [
                str(HELPER_INSTALL_PATH),
                '--token-file',
                str(HELPER_TOKEN_FILE),
                '--backend-port',
                str(MACOS_PROXY_BACKEND_PORT),
                '--control-port',
                str(MACOS_PROXY_HELPER_CONTROL_PORT),
                '--log-path',
                str(HELPER_LOG_PATH),
            ],
            'RunAtLoad': True,
            'KeepAlive': True,
            'ProcessType': 'Background',
            'ThrottleInterval': 2,
            # Keep launchd output separate from the helper's rotating application
            # log.  A crash before Python configures its logger is then still
            # captured in stderr instead of leaving the reported log empty.
            'StandardOutPath': str(HELPER_STDOUT_LOG_PATH),
            'StandardErrorPath': str(HELPER_STDERR_LOG_PATH),
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


def _stage_installer_payload(source: Path) -> tuple[Path, Path, Path]:
    """Copy helper install inputs outside TCC-protected project paths."""
    staging_dir = Path(tempfile.mkdtemp(prefix=f'{HELPER_ID}.'))
    staging_dir.chmod(0o755)

    staging_helper = staging_dir / HELPER_ID
    staging_helper.write_bytes(source.read_bytes())
    staging_helper.chmod(0o644)

    staging_plist = staging_dir / f'{HELPER_ID}.plist'
    staging_plist.write_bytes(_build_plist())
    staging_plist.chmod(0o644)
    return staging_dir, staging_helper, staging_plist


def _installer_source() -> tuple[Path | None, str]:
    if sys.platform != 'darwin':
        return None, 'The macOS proxy helper is only available on macOS.'
    _ensure_token()
    source = _source_helper_path()
    if not source.exists():
        return None, f'Bundled helper executable is missing: {source}'
    try:
        source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        return None, f'Could not fingerprint the bundled macOS proxy helper: {exc}'

    log_buffer.log(
        'ProxyHelper',
        f'Bundled macOS proxy helper: path={source}; sha256={source_sha256}',
    )
    return source, ''


def install_helper() -> tuple[bool, str]:
    """Install/start the root helper with one macOS administrator approval."""
    source, source_error = _installer_source()
    if source is None:
        return False, source_error

    try:
        staging_dir, staging_helper, staging_plist = _stage_installer_payload(source)
    except OSError as exc:
        return False, f'Could not stage the macOS proxy helper installer: {exc}'

    commands = [
        [
            '/usr/bin/install',
            '-d',
            '-o',
            'root',
            '-g',
            'wheel',
            '-m',
            '755',
            str(HELPER_INSTALL_PATH.parent),
        ],
        [
            '/usr/bin/install',
            '-d',
            '-o',
            'root',
            '-g',
            'wheel',
            '-m',
            '755',
            str(HELPER_LOG_DIR),
        ],
        *[
            [
                '/usr/bin/install',
                '-o',
                'root',
                '-g',
                'wheel',
                '-m',
                '644',
                '/dev/null',
                str(log_path),
            ]
            for log_path in (HELPER_LOG_PATH, HELPER_STDOUT_LOG_PATH, HELPER_STDERR_LOG_PATH)
        ],
        [
            '/usr/bin/install',
            '-o',
            'root',
            '-g',
            'wheel',
            '-m',
            '755',
            str(staging_helper),
            str(HELPER_INSTALL_PATH),
        ],
        [
            '/usr/bin/install',
            '-o',
            'root',
            '-g',
            'wheel',
            '-m',
            '644',
            str(staging_plist),
            str(HELPER_PLIST_PATH),
        ],
    ]
    xattr_cmd = shlex.join(
        ['/usr/bin/xattr', '-c', str(HELPER_INSTALL_PATH), str(HELPER_PLIST_PATH)]
    )
    bootstrap_cmd = shlex.join(['/bin/launchctl', 'bootstrap', 'system', str(HELPER_PLIST_PATH)])
    load_cmd = shlex.join(['/bin/launchctl', 'load', '-w', str(HELPER_PLIST_PATH)])
    service_target = f'system/{HELPER_ID}'
    bootout_label = shlex.join(['/bin/launchctl', 'bootout', service_target])
    bootout_plist = shlex.join(['/bin/launchctl', 'bootout', 'system', str(HELPER_PLIST_PATH)])
    kill_service = shlex.join(['/bin/launchctl', 'kill', 'SIGKILL', service_target])
    print_service = shlex.join(['/bin/launchctl', 'print', service_target])
    enable_cmd = shlex.join(['/bin/launchctl', 'enable', service_target])
    lsof_listener = shlex.join(
        [
            '/usr/sbin/lsof',
            '-nP',
            f'-iTCP:{MACOS_PROXY_HELPER_CONTROL_PORT}',
            '-sTCP:LISTEN',
        ]
    )
    lsof_listener_pids = shlex.join(
        [
            '/usr/sbin/lsof',
            '-nP',
            '-t',
            f'-iTCP:{MACOS_PROXY_HELPER_CONTROL_PORT}',
            '-sTCP:LISTEN',
        ]
    )
    helper_hash = shlex.join(['/usr/bin/shasum', '-a', '256', str(HELPER_INSTALL_PATH)])
    helper_file = shlex.join(['/usr/bin/file', str(HELPER_INSTALL_PATH)])
    install_cmds = ' && '.join(shlex.join(command) for command in commands)
    shell_cmd = f"""
set -e
bootout_output="$({bootout_label} 2>&1)" || true
{bootout_plist} >/dev/null 2>&1 || true
/bin/sleep 0.2
if {print_service} >/dev/null 2>&1; then
  # Some macOS/launchd states leave the old KeepAlive helper registered even
  # after bootout. Kill the managed process, then retry the unregister before
  # replacing its executable.
  {kill_service} >/dev/null 2>&1 || true
  /bin/sleep 0.2
  {bootout_label} >/dev/null 2>&1 || true
  {bootout_plist} >/dev/null 2>&1 || true
  /bin/sleep 0.2
fi
if {print_service} >/dev/null 2>&1; then
  service_state="$({print_service} 2>&1)"
  /bin/echo "could not unload existing helper service: $bootout_output" >&2
  /bin/echo "$service_state" >&2
  exit 41
fi

# launchctl can forget a previously-managed helper while the old root process
# itself survives. If that stale process still owns the control port, a newly
# bootstrapped helper cannot bind and will immediately crash/restart forever.
listener_pids="$({lsof_listener_pids} 2>/dev/null || true)"
if [ -n "$listener_pids" ]; then
  for listener_pid in $listener_pids; do
    listener_executable="$(/usr/sbin/lsof -a -p "$listener_pid" -d txt -Fn 2>/dev/null | /usr/bin/sed -n 's/^n//p' | /usr/bin/head -n 1)"
    listener_uid="$(/bin/ps -p "$listener_pid" -o uid= 2>/dev/null | /usr/bin/tr -d '[:space:]')"
    listener_command="$(/bin/ps -ww -p "$listener_pid" -o command= 2>/dev/null || true)"
    listener_is_helper=0
    if [ "$listener_executable" = "{HELPER_INSTALL_PATH}" ]; then
      listener_is_helper=1
    elif [ "$listener_uid" = "0" ]; then
      # Source-tree installs execute the installed helper script through Python,
      # so lsof reports the interpreter as txt. Accept that form only when the
      # root-owned listener is a Python process whose argv contains the exact
      # installed helper path as a whitespace-delimited argument.
      case "$listener_executable" in
        */python|*/python[0-9]*|*/Python)
          case " $listener_command " in
            *" {HELPER_INSTALL_PATH} "*) listener_is_helper=1 ;;
          esac
          ;;
      esac
    fi
    if [ "$listener_is_helper" != "1" ]; then
      /bin/echo "control port {MACOS_PROXY_HELPER_CONTROL_PORT} is owned by unexpected process pid=$listener_pid uid=$listener_uid executable=$listener_executable command=$listener_command" >&2
      {lsof_listener} >&2 || true
      exit 43
    fi
    /bin/echo "terminating stale Fleasion proxy helper listener pid=$listener_pid uid=$listener_uid executable=$listener_executable command=$listener_command"
    /bin/kill -KILL "$listener_pid" >/dev/null 2>&1 || true
  done

  listener_deadline=30
  while [ "$listener_deadline" -gt 0 ]; do
    if ! {lsof_listener_pids} >/dev/null 2>&1; then
      break
    fi
    /bin/sleep 0.1
    listener_deadline=$((listener_deadline - 1))
  done
  if {lsof_listener_pids} >/dev/null 2>&1; then
    /bin/echo "stale Fleasion proxy helper still owns control port {MACOS_PROXY_HELPER_CONTROL_PORT} after SIGKILL" >&2
    {lsof_listener} >&2 || true
    exit 44
  fi
fi

{install_cmds}
{xattr_cmd} >/dev/null 2>&1 || true
set +e
bootstrap_output="$({bootstrap_cmd} 2>&1)"
bootstrap_status=$?
if [ "$bootstrap_status" -ne 0 ]; then
  {bootout_label} >/dev/null 2>&1 || true
  {bootout_plist} >/dev/null 2>&1 || true
  /bin/sleep 0.5
  bootstrap_retry_output="$({bootstrap_cmd} 2>&1)"
  bootstrap_retry_status=$?
else
  bootstrap_retry_output=""
  bootstrap_retry_status=0
fi
if [ "$bootstrap_status" -ne 0 ] && [ "$bootstrap_retry_status" -ne 0 ]; then
  load_output="$({load_cmd} 2>&1)"
  load_status=$?
else
  load_output=""
  load_status=0
fi
{enable_cmd} >/dev/null 2>&1 || true
if [ "$bootstrap_status" -ne 0 ]; then
  /bin/echo "bootstrap failed ($bootstrap_status): $bootstrap_output"
fi
if [ "$bootstrap_retry_status" -ne 0 ]; then
  /bin/echo "bootstrap retry failed ($bootstrap_retry_status): $bootstrap_retry_output"
fi
if [ "$load_status" -ne 0 ]; then
  /bin/echo "legacy load failed ($load_status): $load_output"
fi
/bin/echo "helper install diagnostics: service state"
{print_service} 2>&1 || true
/bin/echo "helper install diagnostics: control-port listener"
{lsof_listener} 2>&1 || true
/bin/echo "helper install diagnostics: installed executable"
{helper_file} 2>&1 || true
{helper_hash} 2>&1 || true
if [ "$bootstrap_status" -ne 0 ] \
  && [ "$bootstrap_retry_status" -ne 0 ] \
  && [ "$load_status" -ne 0 ]; then
  exit 42
fi
exit 0
""".strip()
    apple_script = 'do shell script ' + json.dumps(shell_cmd) + ' with administrator privileges'

    log_buffer.log(
        'ProxyHelper',
        'Requesting one-time administrator approval to install the macOS proxy helper',
    )
    try:
        result = subprocess.run(
            ['/usr/bin/osascript', '-e', apple_script],
            shell=False,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f'Could not run the helper installer: {exc}'
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(staging_dir, ignore_errors=True)

    install_output = (result.stdout or result.stderr or '').strip()
    if install_output:
        log_buffer.log('ProxyHelper', f'macOS helper installer output: {install_output}')

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        return (
            False,
            detail or f'Helper installer exited with code {result.returncode}.',
        )

    deadline = time.monotonic() + HELPER_READY_TIMEOUT_SECONDS
    next_progress_log = time.monotonic() + 5.0
    last_readiness_problem = ''
    while time.monotonic() < deadline:
        ready, readiness_problem = _helper_readiness_diagnostic()
        if ready:
            log_buffer.log('ProxyHelper', 'macOS proxy helper installed and ready')
            return True, ''
        last_readiness_problem = readiness_problem
        if time.monotonic() >= next_progress_log:
            log_buffer.log(
                'ProxyHelper',
                f'Waiting for macOS proxy helper readiness: {readiness_problem}',
            )
            next_progress_log += 5.0
        time.sleep(HELPER_READY_POLL_SECONDS)
    detail = (
        f'The helper was installed but did not become ready within {HELPER_READY_TIMEOUT_SECONDS:.0f} seconds. '
        'Diagnostic logs were created at:\n'
        f'  {HELPER_LOG_PATH}\n'
        f'  {HELPER_STDERR_LOG_PATH}'
    )
    if last_readiness_problem:
        detail += f'\n\nLast readiness check:\n{last_readiness_problem}'
    if install_output:
        detail += f'\n\nLaunch output:\n{install_output}'
    return False, detail
