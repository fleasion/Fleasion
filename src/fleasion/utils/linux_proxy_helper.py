"""Client for the one-shot privileged Linux proxy helper."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Literal, TypedDict, overload

from .logging import log_buffer
from .paths import CONFIG_DIR, MACOS_PROXY_BACKEND_PORT, PROXY_PORT
from .plural import format_count

HELPER_READY_FILE = CONFIG_DIR / 'linux_proxy_helper.ready'
HELPER_STOP_FILE = CONFIG_DIR / 'linux_proxy_helper.stop'
HELPER_HOSTS_FILE = CONFIG_DIR / 'linux_proxy_helper.hosts.json'
HELPER_LOG_FILE = CONFIG_DIR / 'linux_proxy_helper.log'
NSS_CERT_NICKNAME = 'Fleasion Proxy CA'
SYSTEM_CA_NAME = 'fleasion-proxy-ca.crt'
HELPER_BUNDLED_EXECUTABLE_NAME = 'fleasion-linux-proxy-helper'
INSTALLED_HELPER_PATH = Path('/usr/local/libexec/fleasion-linux-proxy-helper')
INSTALLED_HELPER_METADATA_PATH = Path(
    '/usr/local/libexec/fleasion-linux-proxy-helper.metadata.json'
)
HELPER_METADATA_VERSION = 1
POLKIT_ACTION_NAMESPACE = 'com.fleasion.proxy-helper'
POLKIT_POLICY_PATH = Path('/usr/share/polkit-1/actions') / f'{POLKIT_ACTION_NAMESPACE}.policy'
LEGACY_POLKIT_POLICY_PATH = (
    Path('/usr/local/share/polkit-1/actions') / f'{POLKIT_ACTION_NAMESPACE}.policy'
)
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class HelperMetadata(TypedDict):
    metadata_version: int
    source_sha256: str
    source_helper_needs_dispatch_flag: bool


class InstallHelperKwargs(TypedDict, total=False):
    enable_promptless: bool
    ca_cert_path: Path


if TYPE_CHECKING:

    def _json_object(value: object) -> JsonObject | None: ...

    def _string_list(value: object) -> list[str]: ...

    def _json_values(value: list[JsonObject]) -> list[JsonValue]: ...
else:

    def _json_object(value: object) -> JsonObject | None:
        return value if isinstance(value, dict) else None

    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    def _json_values(value: list[JsonObject]) -> list[JsonValue]:
        return value


SYSTEM_CA_DIRS = (
    Path('/usr/local/share/ca-certificates'),
    Path('/etc/pki/ca-trust/source/anchors'),
)
_last_start_error_details: JsonObject = {}
_force_source_helper_for_session = False
_PEM_CERT_BLOCK_RE = re.compile(
    r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',
    re.DOTALL,
)


def _host_subprocess_env() -> dict[str, str]:
    """Run host tools without PyInstaller's private shared-library path."""
    env = os.environ.copy()
    original_library_path = env.pop('LD_LIBRARY_PATH_ORIG', None)
    if original_library_path is not None:
        if original_library_path:
            env['LD_LIBRARY_PATH'] = original_library_path
        else:
            env.pop('LD_LIBRARY_PATH', None)
        return env

    bundle_root = getattr(sys, '_MEIPASS', None)
    library_path = env.get('LD_LIBRARY_PATH')
    if bundle_root and library_path:
        entries = [
            entry
            for entry in library_path.split(os.pathsep)
            if entry and Path(entry).resolve() != Path(bundle_root).resolve()
        ]
        if entries:
            env['LD_LIBRARY_PATH'] = os.pathsep.join(entries)
        else:
            env.pop('LD_LIBRARY_PATH', None)
    return env


@overload
def _run_host_command(
    cmd: list[str],
    *,
    capture_output: bool = False,
    text: Literal[True],
    encoding: str,
    errors: str,
    timeout: float,
) -> subprocess.CompletedProcess[str]: ...


@overload
def _run_host_command(
    cmd: list[str],
    *,
    capture_output: bool = False,
    text: Literal[False] = False,
    encoding: None = None,
    errors: None = None,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]: ...


def _run_host_command(
    cmd: list[str],
    *,
    capture_output: bool = False,
    text: bool = False,
    encoding: str | None = None,
    errors: str | None = None,
    timeout: float,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    if text:
        return subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
            cmd,
            env=_host_subprocess_env(),
            capture_output=capture_output,
            text=True,
            encoding=encoding,
            errors=errors,
            timeout=timeout,
        )
    return subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
        cmd,
        env=_host_subprocess_env(),
        capture_output=capture_output,
        timeout=timeout,
    )


def _popen_host_command(
    cmd: list[str], *, stdout: BinaryIO, stderr: BinaryIO
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(cmd, env=_host_subprocess_env(), stdout=stdout, stderr=stderr)  # ruff: ignore[subprocess-without-shell-equals-true]


def _source_helper_path() -> Path:
    frozen_meipass = getattr(sys, '_MEIPASS', None)
    if frozen_meipass:
        frozen_root = Path(frozen_meipass)
        bundled_executable = frozen_root / HELPER_BUNDLED_EXECUTABLE_NAME
        if bundled_executable.exists():
            return bundled_executable
        bundled = frozen_root / 'linux_proxy_helper_daemon.py'
        if bundled.exists():
            return bundled
    return Path(__file__).resolve().parents[1] / 'linux_proxy_helper_daemon.py'


def _installable_helper_source() -> tuple[Path, bool]:
    """Return the helper payload and whether it needs --linux-proxy-helper."""
    frozen_meipass = getattr(sys, '_MEIPASS', None)
    if frozen_meipass:
        bundled_executable = Path(frozen_meipass) / HELPER_BUNDLED_EXECUTABLE_NAME
        if bundled_executable.exists():
            return bundled_executable, False
        return Path(sys.executable), True
    return _source_helper_path(), False


def _is_trusted_installed_helper(path: Path = INSTALLED_HELPER_PATH) -> bool:
    try:
        stat_result = path.stat()
    except OSError:
        return False
    return (
        path.is_file()
        and stat_result.st_uid == 0
        and bool(stat_result.st_mode & 0o111)
        and not bool(stat_result.st_mode & 0o022)
    )


def _error_text_is_read_only_filesystem(error: object) -> bool:
    text = str(error or '').lower()
    return 'read-only file system' in text or 'errno 30' in text or 'os error 30' in text


def _path_on_read_only_mount(path: Path) -> bool:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    try:
        flags = os.statvfs(candidate).f_flag
    except OSError:
        return False
    return bool(flags & getattr(os, 'ST_RDONLY', 1))


def _persistent_helper_install_path_is_read_only() -> bool:
    return _path_on_read_only_mount(INSTALLED_HELPER_PATH) or _path_on_read_only_mount(
        INSTALLED_HELPER_PATH.parent
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _current_helper_metadata() -> HelperMetadata | None:
    try:
        source, needs_helper_flag = _installable_helper_source()
        return {
            'metadata_version': HELPER_METADATA_VERSION,
            'source_sha256': _file_sha256(source),
            'source_helper_needs_dispatch_flag': bool(needs_helper_flag),
        }
    except OSError as exc:
        log_buffer.log('ProxyHelper', f'Could not inspect current Linux helper payload: {exc}')
        return None


def _installed_helper_metadata(
    path: Path = INSTALLED_HELPER_METADATA_PATH,
) -> JsonObject | None:
    try:
        payload: object = json.loads(path.read_text(encoding='utf-8'))
    except OSError:
        return None
    except json.JSONDecodeError as exc:
        log_buffer.log('ProxyHelper', f'Installed Linux helper metadata is invalid: {exc}')
        return None
    return _json_object(payload)


def _installed_helper_metadata_is_current() -> bool:
    expected = _current_helper_metadata()
    installed = _installed_helper_metadata()
    return expected is not None and installed == expected


def _policy_file_is_current(path: Path) -> bool:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return False
    return (
        f'id="{POLKIT_ACTION_NAMESPACE}.run"' in text
        and '<allow_active>yes</allow_active>' in text
        and f'<annotate key="org.freedesktop.policykit.exec.path">{INSTALLED_HELPER_PATH}</annotate>'
        in text
    )


def _installed_policy_is_current() -> bool:
    return _policy_file_is_current(POLKIT_POLICY_PATH) and _policy_file_is_current(
        LEGACY_POLKIT_POLICY_PATH
    )


def _helper_command() -> list[str]:
    """Return a Python-free helper command for frozen builds when possible."""
    if not _force_source_helper_for_session and _is_trusted_installed_helper():
        return [str(INSTALLED_HELPER_PATH)]
    return _source_helper_command()


def _source_helper_command() -> list[str]:
    helper_path = _source_helper_path()
    if helper_path.name == HELPER_BUNDLED_EXECUTABLE_NAME:
        return [str(helper_path)]
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--linux-proxy-helper']
    return [sys.executable, str(helper_path)]


def _read_ready() -> JsonObject | None:
    try:
        payload: object = json.loads(HELPER_READY_FILE.read_text(encoding='utf-8'))
        return _json_object(payload)
    except Exception:  # ruff: ignore[blind-except]
        return None


def last_start_error_details() -> JsonObject:
    return dict(_last_start_error_details)


def _set_last_start_error(details: JsonObject | None = None, *, error: str | None = None) -> None:
    _last_start_error_details.clear()
    if details:
        _last_start_error_details.update(details)
    if error:
        _last_start_error_details.setdefault('error', error)


def _current_process_start_time() -> str | None:
    if not sys.platform.startswith('linux'):
        return None
    try:
        content = Path(f'/proc/{os.getpid()}/stat').read_text(encoding='utf-8', errors='replace')
        _before, after_comm = content.rsplit(')', 1)
        fields = after_comm.strip().split()
        return fields[19] if len(fields) > 19 else None
    except OSError:
        return None
    except ValueError:
        return None


def update_helper_hosts(hosts: set[str]) -> bool:
    """Ask the running privileged helper to apply a new allowlisted host set."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = HELPER_HOSTS_FILE.with_name(f'{HELPER_HOSTS_FILE.name}.tmp')
        tmp_path.write_text(
            json.dumps({'hosts': sorted(hosts)}, separators=(',', ':')),
            encoding='utf-8',
        )
        tmp_path.replace(HELPER_HOSTS_FILE)
        return True  # ruff: ignore[try-consider-else]
    except OSError as exc:
        log_buffer.log('ProxyHelper', f'Failed to request Linux helper hosts update: {exc}')
        return False


def install_privileged_helper(
    *,
    enable_promptless: bool = False,
    timeout: float = 120.0,
    ca_cert_path: Path | None = None,
) -> JsonObject:
    """Install the root-owned helper and Polkit policy with one admin approval."""
    pkexec = shutil.which('pkexec')
    if not pkexec:
        return {'ok': False, 'error': 'pkexec_not_found'}

    source, needs_helper_flag = _installable_helper_source()
    cmd = [
        pkexec,
        *_source_helper_command(),
        '--install-privileged-helper',
        '--source-helper',
        str(source),
    ]
    if needs_helper_flag:
        cmd.append('--source-helper-needs-dispatch-flag')
    if enable_promptless:
        cmd.append('--enable-promptless')
    if ca_cert_path is not None:
        cmd.extend(['--ca-cert', str(ca_cert_path)])

    try:
        result = _run_host_command(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
    except Exception as exc:  # ruff: ignore[blind-except]
        return {'ok': False, 'error': str(exc)}

    output = (result.stdout or '').strip()
    details: JsonObject
    try:
        details_payload: object = json.loads(output) if output else {}
        details = _json_object(details_payload) or {}
    except json.JSONDecodeError:
        details = {'output': output}
    details.setdefault('ok', result.returncode == 0)
    if not details.get('ok'):
        details.setdefault('error', (result.stderr or output or str(result.returncode)).strip())
    return details


def ensure_privileged_helper_installed(  # ruff: ignore[too-many-return-statements]
    *,
    enable_promptless: bool = True,
    ca_cert_path: Path | None = None,
) -> bool:
    """Ensure runtime launches use Fleasion's installed Polkit action."""
    global _force_source_helper_for_session  # ruff: ignore[global-statement]
    trusted_helper = _is_trusted_installed_helper()
    current_policy = _installed_policy_is_current()
    current_metadata = _installed_helper_metadata_is_current()
    if trusted_helper and current_policy and current_metadata:
        _force_source_helper_for_session = False
        return True

    if _persistent_helper_install_path_is_read_only():
        _force_source_helper_for_session = True
        log_buffer.log(
            'ProxyHelper',
            'Persistent Linux helper install path is read-only; using the current helper directly for this session',
        )
        return True

    if trusted_helper and current_policy and not current_metadata:
        log_buffer.log(
            'ProxyHelper',
            'Updating Fleasion Linux privileged helper to match this app build',
        )
    else:
        log_buffer.log(
            'ProxyHelper',
            'Installing Fleasion Linux privileged helper for persistent proxy permissions',
        )
    install_kwargs: InstallHelperKwargs = {'enable_promptless': enable_promptless}
    if ca_cert_path is not None:
        install_kwargs['ca_cert_path'] = ca_cert_path
    details = install_privileged_helper(**install_kwargs)
    if not details.get('ok'):
        error = details.get('error') or details
        log_buffer.log(
            'ProxyHelper',
            f'Linux privileged helper install failed: {error}',
        )
        if _error_text_is_read_only_filesystem(error):
            _force_source_helper_for_session = True
            log_buffer.log(
                'ProxyHelper',
                'Persistent Linux helper install path is read-only; using the current helper directly for this session',
            )
            return True
        return False

    if not _is_trusted_installed_helper():
        log_buffer.log(
            'ProxyHelper',
            'Linux privileged helper install finished but installed helper was not trusted',
        )
        return False
    if not _installed_policy_is_current():
        log_buffer.log(
            'ProxyHelper',
            'Linux privileged helper install finished but Polkit policy was not current',
        )
        return False
    if not _installed_helper_metadata_is_current():
        log_buffer.log(
            'ProxyHelper',
            'Linux privileged helper install finished but helper metadata was not current',
        )
        return False

    system_ca = _json_object(details.get('system_ca'))
    if system_ca and system_ca.get('ok'):
        store_names = _string_list(system_ca.get('stores'))
        stores = ', '.join(store_names)
        if store_names and all(str(store).endswith(':already-current') for store in store_names):
            log_buffer.log(
                'Certificate',
                f'CA already trusted in Linux system trust store during helper install{f" ({stores})" if stores else ""}',
            )
        else:
            log_buffer.log(
                'Certificate',
                f'Installed CA into Linux system trust store during helper install{f" ({stores})" if stores else ""}',
            )

    if details.get('promptless_rule'):
        log_buffer.log('ProxyHelper', 'Installed promptless Polkit rule for Fleasion proxy helper')
    elif trusted_helper and current_policy and not current_metadata:
        log_buffer.log('ProxyHelper', 'Updated Fleasion proxy helper for this app build')
    else:
        log_buffer.log('ProxyHelper', 'Installed Fleasion proxy helper Polkit action')
    _force_source_helper_for_session = False
    return True


def start_helper(  # ruff: ignore[too-many-return-statements]
    hosts: set[str],
    backend_port: int = MACOS_PROXY_BACKEND_PORT,
    timeout: float = 120.0,
    ca_cert_path: Path | None = None,
    require_system_ca: bool = False,
) -> bool:
    """Start the privileged Linux port/hosts helper and wait until it is ready."""
    _set_last_start_error()
    pkexec = shutil.which('pkexec')
    if not pkexec:
        error = 'pkexec not found'
        _set_last_start_error({'code': 'pkexec_not_found'}, error=error)
        log_buffer.log('ProxyHelper', f'Linux proxy helper failed: {error}')
        return False
    if require_system_ca and ca_cert_path is None:
        error = 'system CA trust is required but no CA cert was supplied'
        _set_last_start_error(error=error)
        log_buffer.log('ProxyHelper', f'Linux proxy helper failed: {error}')
        return False
    system_ca_supported = linux_system_ca_store_supported()
    enforce_system_ca = require_system_ca and system_ca_supported
    if require_system_ca and not system_ca_supported and ca_cert_path is not None:
        log_buffer.log(
            'Certificate',
            'Linux system trust-store install is unsupported on this distro; '
            'continuing without distro-wide CA trust',
        )
    needs_system_ca_install = (
        enforce_system_ca
        and ca_cert_path is not None
        and not linux_system_ca_is_current(ca_cert_path)
    )
    helper_install_ca = ca_cert_path if needs_system_ca_install else None
    if not ensure_privileged_helper_installed(
        enable_promptless=True,
        ca_cert_path=helper_install_ca,
    ):
        _set_last_start_error(error='privileged helper install failed')
        return False
    if (
        needs_system_ca_install
        and ca_cert_path is not None
        and not linux_system_ca_is_current(ca_cert_path)
    ):
        details = _install_ca_into_linux_system_store(ca_cert_path)
        if not details.get('ok'):
            error = details.get('error') or details
            if _system_ca_error_is_unsupported(error):
                enforce_system_ca = False
                log_buffer.log(
                    'Certificate',
                    'Linux system trust-store install is unsupported in the privileged environment; '
                    'continuing without distro-wide CA trust',
                )
            else:
                _set_last_start_error(
                    details, error=f'system CA trust could not be installed: {error}'
                )
                log_buffer.log(
                    'ProxyHelper',
                    f'Linux proxy helper failed: system CA trust could not be installed: {error}',
                )
                return False

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        HELPER_READY_FILE.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        HELPER_STOP_FILE.unlink(missing_ok=True)
    update_helper_hosts(hosts)

    cmd = [
        pkexec,
        *_helper_command(),
        '--backend-port',
        str(backend_port),
        '--listen-port',
        str(PROXY_PORT),
        '--hosts',
        ','.join(sorted(hosts)),
        '--stop-file',
        str(HELPER_STOP_FILE),
        '--ready-file',
        str(HELPER_READY_FILE),
        '--hosts-file',
        str(HELPER_HOSTS_FILE),
        '--config-dir',
        str(CONFIG_DIR),
        '--owner-uid',
        str(os.getuid()),
        '--owner-gid',
        str(os.getgid()),
        '--parent-pid',
        str(os.getpid()),
    ]
    parent_start_time = _current_process_start_time()
    if parent_start_time:
        cmd.extend(['--parent-start-time', parent_start_time])
    if require_system_ca and ca_cert_path is not None:
        cmd.extend(['--ca-cert', str(ca_cert_path)])
    if enforce_system_ca:
        cmd.append('--require-system-ca')

    log_buffer.log(
        'ProxyHelper',
        'Requesting Linux Polkit approval for Fleasion hosts entries and port-443 relay',
    )
    try:
        log_file = HELPER_LOG_FILE.open('ab')
    except OSError as exc:
        _set_last_start_error(error=f'could not open Linux helper log: {exc}')
        log_buffer.log('ProxyHelper', f'Could not open Linux helper log: {exc}')
        return False

    with log_file:
        try:
            # Keep pkexec in the GUI's login session.  Detaching it with setsid()
            # prevents Polkit from associating the request with the active desktop
            # session; on systems without a fallback text agent it then tries
            # /dev/tty and exits with code 127.  The helper already watches its
            # parent PID and removes its hosts entries when that parent exits, so
            # it does not need a separate session to be cleaned up safely.
            process = _popen_host_command(cmd, stdout=log_file, stderr=log_file)
        except Exception as exc:  # ruff: ignore[blind-except]
            _set_last_start_error(error=f'could not start Linux proxy helper: {exc}')
            log_buffer.log('ProxyHelper', f'Could not start Linux proxy helper: {exc}')
            return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        ready = _read_ready()
        if ready:
            if ready.get('ok'):
                ready_system_ca = _json_object(ready.get('system_ca'))
                if enforce_system_ca and not (ready_system_ca or {}).get('ok'):
                    _set_last_start_error(ready, error='system CA trust was not confirmed')
                    log_buffer.log(
                        'ProxyHelper',
                        'Linux proxy helper failed: system CA trust was not confirmed',
                    )
                    return False
                log_buffer.log('ProxyHelper', f'Linux proxy helper ready on port {PROXY_PORT}')
                return True
            _set_last_start_error(ready)
            log_buffer.log(
                'ProxyHelper',
                f'Linux proxy helper failed: {ready.get("error") or "unknown error"}',
            )
            return False

        returncode = process.poll()
        if returncode is not None:
            _set_last_start_error(
                error=f'helper exited before becoming ready with code {returncode}'
            )
            log_buffer.log(
                'ProxyHelper',
                f'Linux proxy helper exited before becoming ready with code {returncode}; log: {HELPER_LOG_FILE}',
            )
            return False
        time.sleep(0.2)

    _set_last_start_error(error=f'timed out waiting for readiness; log: {HELPER_LOG_FILE}')
    log_buffer.log(
        'ProxyHelper',
        f'Linux proxy helper timed out waiting for readiness; log: {HELPER_LOG_FILE}',
    )
    return False


def stop_helper(timeout: float = 8.0) -> bool:
    """Ask the privileged Linux helper to remove hosts entries and exit."""
    try:
        HELPER_STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        HELPER_STOP_FILE.touch()
    except OSError as exc:
        log_buffer.log('ProxyHelper', f'Failed to signal Linux proxy helper stop: {exc}')
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not HELPER_READY_FILE.exists():
            with contextlib.suppress(OSError):
                HELPER_HOSTS_FILE.unlink(missing_ok=True)
            return True
        time.sleep(0.2)

    log_buffer.log('ProxyHelper', f'Linux proxy helper did not stop within {timeout:.0f}s')
    return False


def cleanup_hosts_with_pkexec(timeout: float = 120.0) -> bool:
    """Run a one-shot root child that removes Fleasion hosts entries."""
    pkexec = shutil.which('pkexec')
    if not pkexec:
        log_buffer.log('ProxyHelper', 'Linux hosts cleanup failed: pkexec not found')
        return False

    cmd = [pkexec, *_helper_command(), '--cleanup-hosts']
    try:
        result = _run_host_command(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log('ProxyHelper', f'Linux hosts cleanup child failed: {exc}')
        return False

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        log_buffer.log(
            'ProxyHelper',
            f'Linux hosts cleanup was cancelled or failed (rc={result.returncode}): '
            f'{detail or "no details"}',
        )
        return False
    log_buffer.log('ProxyHelper', 'Linux one-shot hosts cleanup completed')
    return True


def _user_home() -> Path:
    return Path(os.environ.get('FLEASION_USER_HOME') or Path.home()).expanduser()


def _existing_nss_dbs(home: Path) -> list[Path]:
    """Return existing browser NSS DB directories for the current user."""
    candidates: set[Path] = set()
    direct_dirs = (
        home / '.pki' / 'nssdb',
        home / 'snap' / 'chromium' / 'current' / '.pki' / 'nssdb',
        home / 'snap' / 'firefox' / 'common' / '.pki' / 'nssdb',
    )
    for directory in direct_dirs:
        if directory.is_dir():
            candidates.add(directory)

    profile_roots = (
        home / '.mozilla' / 'firefox',
        home / '.mozilla' / 'librewolf',
        home / '.waterfox',
        home / '.config' / 'google-chrome',
        home / '.config' / 'chromium',
        home / '.config' / 'BraveSoftware' / 'Brave-Browser',
        home / '.config' / 'microsoft-edge',
        home / '.config' / 'vivaldi',
        home / 'snap' / 'firefox' / 'common' / '.mozilla' / 'firefox',
        home / 'snap' / 'chromium' / 'current' / '.config' / 'chromium',
        home / '.var' / 'app' / 'org.mozilla.firefox' / '.mozilla' / 'firefox',
        home / '.var' / 'app' / 'io.gitlab.librewolf-community' / '.librewolf',
        home / '.var' / 'app' / 'com.google.Chrome' / 'config' / 'google-chrome',
        home / '.var' / 'app' / 'org.chromium.Chromium' / 'config' / 'chromium',
        home / '.var' / 'app' / 'com.brave.Browser' / 'config' / 'BraveSoftware' / 'Brave-Browser',
    )
    for root in profile_roots:
        if not root.is_dir():
            continue
        try:
            if (root / 'cert9.db').exists():
                candidates.add(root)
            candidates.update(cert_db.parent for cert_db in root.glob('*/cert9.db'))
        except OSError:
            pass

    return sorted(candidates)


def _ensure_shared_nss_db(home: Path) -> Path | None:
    """Create Chromium-family shared NSS DB when certutil is available."""
    certutil = shutil.which('certutil')
    if not certutil:
        return None
    nssdb = home / '.pki' / 'nssdb'
    if (nssdb / 'cert9.db').exists():
        return nssdb
    try:
        nssdb.mkdir(parents=True, exist_ok=True)
        result = _run_host_command(
            [certutil, '-N', '--empty-password', '-d', f'sql:{nssdb}'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10,
        )
    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log(
            'Certificate',
            f'Could not create shared NSS certificate DB at {nssdb}: {exc}',
        )
        return None
    if result.returncode == 0 or (nssdb / 'cert9.db').exists():
        return nssdb
    err = (result.stderr or result.stdout or '').strip()
    log_buffer.log(
        'Certificate',
        f'Could not create shared NSS certificate DB at {nssdb}: {err or result.returncode}',
    )
    return None


def _normalize_pem_for_compare(text: str) -> str:
    blocks = _PEM_CERT_BLOCK_RE.findall(text.replace('\r\n', '\n').replace('\r', '\n'))
    if blocks:
        return '\n'.join(block.strip() for block in blocks) + '\n'
    return text.strip() + '\n'


def _nss_db_fleasion_ca_status(certutil: str, db_dir: Path, ca_cert_path: Path) -> str:
    """Return missing/current/stale for Fleasion's CA nickname in an NSS DB."""
    try:
        ca_pem = _normalize_pem_for_compare(ca_cert_path.read_text(encoding='utf-8'))
        result = _run_host_command(
            [
                certutil,
                '-L',
                '-d',
                f'sql:{db_dir}',
                '-n',
                NSS_CERT_NICKNAME,
                '-a',
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10,
        )
    except Exception:  # ruff: ignore[blind-except]
        return 'missing'
    if result.returncode != 0:
        return 'missing'
    stored_pem = _normalize_pem_for_compare(result.stdout or '')
    return 'current' if stored_pem == ca_pem else 'stale'


def _install_ca_into_nss_db(certutil: str, db_dir: Path, ca_cert_path: Path) -> JsonObject:
    db_arg = f'sql:{db_dir}'
    status = _nss_db_fleasion_ca_status(certutil, db_dir, ca_cert_path)
    if status == 'current':
        return {'db': str(db_dir), 'ok': True, 'status': 'already_current'}

    _run_host_command(
        [certutil, '-D', '-d', db_arg, '-n', NSS_CERT_NICKNAME],
        capture_output=True,
        timeout=10,
    )
    try:
        result = _run_host_command(
            [
                certutil,
                '-A',
                '-d',
                db_arg,
                '-n',
                NSS_CERT_NICKNAME,
                '-t',
                'C,,',
                '-i',
                str(ca_cert_path),
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10,
        )
    except Exception as exc:  # ruff: ignore[blind-except]
        return {'db': str(db_dir), 'ok': False, 'error': str(exc)}

    if result.returncode == 0:
        return {
            'db': str(db_dir),
            'ok': True,
            'status': 'refreshed' if status == 'stale' else 'installed',
        }
    err = (result.stderr or result.stdout or '').strip()
    return {'db': str(db_dir), 'ok': False, 'error': err or str(result.returncode)}


def _install_ca_into_browser_nss(ca_cert_path: Path) -> list[JsonObject]:
    certutil = shutil.which('certutil')
    if not certutil:
        log_buffer.log('Certificate', 'Skipping Linux browser NSS trust import: certutil not found')
        return [{'ok': False, 'error': 'certutil_not_found'}]

    home = _user_home()
    shared_db = _ensure_shared_nss_db(home)
    dbs = set(_existing_nss_dbs(home))
    if shared_db is not None:
        dbs.add(shared_db)
    if not dbs:
        log_buffer.log('Certificate', 'No Linux browser NSS certificate databases found')
        return []

    results = [_install_ca_into_nss_db(certutil, db, ca_cert_path) for db in sorted(dbs)]
    ok_count = sum(1 for item in results if item.get('ok'))
    already_count = sum(1 for item in results if item.get('status') == 'already_current')
    installed_count = sum(1 for item in results if item.get('status') == 'installed')
    refreshed_count = sum(1 for item in results if item.get('status') == 'refreshed')
    fail_count = len(results) - ok_count
    if already_count:
        log_buffer.log(
            'Certificate',
            f'CA already trusted in {format_count(already_count, "Linux browser NSS database")}',
        )
    if installed_count:
        log_buffer.log(
            'Certificate',
            f'Installed CA into {format_count(installed_count, "Linux browser NSS database")}',
        )
    if refreshed_count:
        log_buffer.log(
            'Certificate',
            f'Refreshed CA in {format_count(refreshed_count, "Linux browser NSS database")}',
        )
    if fail_count:
        log_buffer.log(
            'Certificate',
            f'Failed to install CA into {format_count(fail_count, "Linux browser NSS database")}',
        )
        for item in results:
            if not item.get('ok'):
                log_buffer.log(
                    'Certificate',
                    f'Linux browser NSS import failed for {item.get("db")}: {item.get("error")}',
                )
    return results


def linux_system_ca_is_current(ca_cert_path: Path) -> bool:
    """Return True when a supported Linux system CA target already matches."""
    try:
        ca_bytes = ca_cert_path.read_bytes()
    except OSError:
        return False

    supported_targets = [
        directory / SYSTEM_CA_NAME for directory in SYSTEM_CA_DIRS if directory.is_dir()
    ]
    if not supported_targets:
        return False

    for target in supported_targets:
        try:
            if target.read_bytes() == ca_bytes:
                return True
        except OSError:
            pass
    return False


def linux_system_ca_store_supported() -> bool:
    """Return True when this host has a system trust store Fleasion can refresh."""
    return any(
        bool(shutil.which(command)) and directory.is_dir()
        for command, directory in zip(  # ruff: ignore[zip-without-explicit-strict]
            ('update-ca-certificates', 'update-ca-trust'),
            SYSTEM_CA_DIRS,
        )
    )


def linux_system_ca_needs_install(ca_cert_path: Path) -> bool:
    """Return True when a supported Linux system CA target is missing/stale."""
    if not linux_system_ca_store_supported():
        return False
    if linux_system_ca_is_current(ca_cert_path):  # ruff: ignore[needless-bool]
        return False
    return True


def _system_ca_error_is_unsupported(error: object) -> bool:
    error_details = _json_object(error)
    if error_details is not None:
        return _system_ca_error_is_unsupported(error_details.get('error'))
    return 'no_supported_system_trust_store' in str(error or '')


def _install_ca_into_linux_system_store(ca_cert_path: Path) -> JsonObject:
    pkexec = shutil.which('pkexec')
    if not pkexec:
        log_buffer.log('Certificate', 'Skipping Linux system trust-store install: pkexec not found')
        return {'ok': False, 'error': 'pkexec_not_found'}

    cmd = [
        pkexec,
        *_helper_command(),
        '--install-system-ca',
        '--ca-cert',
        str(ca_cert_path),
    ]
    try:
        result = _run_host_command(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=120,
        )
    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log('Certificate', f'Failed to install CA into Linux system trust store: {exc}')
        return {'ok': False, 'error': str(exc)}

    output = (result.stdout or '').strip()
    details: JsonObject
    try:
        details_payload: object = json.loads(output) if output else {}
        details = _json_object(details_payload) or {}
    except json.JSONDecodeError:
        details = {'output': output}
    details.setdefault('ok', result.returncode == 0)
    if result.returncode == 0 and details.get('ok'):
        store_names = _string_list(details.get('stores'))
        stores = ', '.join(store_names)
        if store_names and all(str(store).endswith(':already-current') for store in store_names):
            log_buffer.log(
                'Certificate',
                f'CA already trusted in Linux system trust store{f" ({stores})" if stores else ""}',
            )
        else:
            log_buffer.log(
                'Certificate',
                f'Installed CA into Linux system trust store{f" ({stores})" if stores else ""}',
            )
    else:
        err = details.get('error') or (result.stderr or output or str(result.returncode)).strip()
        log_buffer.log('Certificate', f'Failed to install CA into Linux system trust store: {err}')
        details['error'] = err
    return details


def install_ca_into_linux_trust(
    ca_cert_path: Path,
    *,
    install_system: bool = True,
    install_nss: bool = True,
) -> JsonObject:
    """Trust Fleasion's CA for Linux browsers and system TLS clients."""
    if not sys.platform.startswith('linux'):
        return {'ok': True, 'skipped': 'not_linux'}

    system: JsonObject
    if (
        install_system
        and not linux_system_ca_store_supported()
        and not linux_system_ca_is_current(ca_cert_path)
    ):
        system = {
            'ok': False,
            'skipped': 'unsupported',
            'error': 'no_supported_system_trust_store',
        }
        log_buffer.log(
            'Certificate',
            'Skipping Linux system trust-store install: no supported system trust store found',
        )
    elif install_system and linux_system_ca_needs_install(ca_cert_path):
        system = _install_ca_into_linux_system_store(ca_cert_path)
    elif install_system:
        system = {'ok': True, 'skipped': 'already_installed'}
        log_buffer.log('Certificate', 'CA already trusted in Linux system trust store')
    else:
        system = {'ok': False, 'skipped': 'handled_by_privileged_helper'}
    nss = _install_ca_into_browser_nss(ca_cert_path) if install_nss else []
    return {
        'ok': bool(system.get('ok')) or any(item.get('ok') for item in nss),
        'system': system,
        'nss': _json_values(nss),
    }
