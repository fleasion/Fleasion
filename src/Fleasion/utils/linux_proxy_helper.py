"""Client for the one-shot privileged Linux proxy helper."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

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
POLKIT_ACTION_NAMESPACE = 'com.fleasion.proxy-helper'
POLKIT_POLICY_PATH = Path('/usr/share/polkit-1/actions') / f'{POLKIT_ACTION_NAMESPACE}.policy'
LEGACY_POLKIT_POLICY_PATH = Path('/usr/local/share/polkit-1/actions') / f'{POLKIT_ACTION_NAMESPACE}.policy'
SYSTEM_CA_DIRS = (
    Path('/usr/local/share/ca-certificates'),
    Path('/etc/pki/ca-trust/source/anchors'),
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
            entry for entry in library_path.split(os.pathsep)
            if entry and Path(entry).resolve() != Path(bundle_root).resolve()
        ]
        if entries:
            env['LD_LIBRARY_PATH'] = os.pathsep.join(entries)
        else:
            env.pop('LD_LIBRARY_PATH', None)
    return env


def _run_host_command(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=_host_subprocess_env(), **kwargs)


def _popen_host_command(cmd: list[str], **kwargs) -> subprocess.Popen:
    return subprocess.Popen(cmd, env=_host_subprocess_env(), **kwargs)


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


def _policy_file_is_current(path: Path) -> bool:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return False
    return (
        f'id="{POLKIT_ACTION_NAMESPACE}.run"' in text
        and '<allow_active>yes</allow_active>' in text
        and f'<annotate key="org.freedesktop.policykit.exec.path">{INSTALLED_HELPER_PATH}</annotate>' in text
    )


def _installed_policy_is_current() -> bool:
    return (
        _policy_file_is_current(POLKIT_POLICY_PATH)
        and _policy_file_is_current(LEGACY_POLKIT_POLICY_PATH)
    )


def _helper_command() -> list[str]:
    """Return a Python-free helper command for frozen builds when possible."""
    if _is_trusted_installed_helper():
        return [str(INSTALLED_HELPER_PATH)]
    helper_path = _source_helper_path()
    if helper_path.name == HELPER_BUNDLED_EXECUTABLE_NAME:
        return [str(helper_path)]
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--linux-proxy-helper']
    return [sys.executable, str(helper_path)]


def _source_helper_command() -> list[str]:
    helper_path = _source_helper_path()
    if helper_path.name == HELPER_BUNDLED_EXECUTABLE_NAME:
        return [str(helper_path)]
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--linux-proxy-helper']
    return [sys.executable, str(helper_path)]


def _read_ready() -> dict | None:
    try:
        return json.loads(HELPER_READY_FILE.read_text(encoding='utf-8'))
    except Exception:
        return None


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
        return True
    except OSError as exc:
        log_buffer.log('ProxyHelper', f'Failed to request Linux helper hosts update: {exc}')
        return False


def install_privileged_helper(*, enable_promptless: bool = False, timeout: float = 120.0) -> dict:
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

    try:
        result = _run_host_command(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}

    output = (result.stdout or '').strip()
    try:
        details = json.loads(output) if output else {}
    except json.JSONDecodeError:
        details = {'output': output}
    details.setdefault('ok', result.returncode == 0)
    if not details.get('ok'):
        details.setdefault('error', (result.stderr or output or str(result.returncode)).strip())
    return details


def ensure_privileged_helper_installed(*, enable_promptless: bool = True) -> bool:
    """Ensure runtime launches use Fleasion's installed Polkit action."""
    if _is_trusted_installed_helper() and _installed_policy_is_current():
        return True

    log_buffer.log('ProxyHelper', 'Installing Fleasion Linux privileged helper for persistent proxy permissions')
    details = install_privileged_helper(enable_promptless=enable_promptless)
    if not details.get('ok'):
        log_buffer.log(
            'ProxyHelper',
            f'Linux privileged helper install failed: {details.get("error") or details}',
        )
        return False

    if not _is_trusted_installed_helper():
        log_buffer.log('ProxyHelper', 'Linux privileged helper install finished but installed helper was not trusted')
        return False
    if not _installed_policy_is_current():
        log_buffer.log('ProxyHelper', 'Linux privileged helper install finished but Polkit policy was not current')
        return False

    if details.get('promptless_rule'):
        log_buffer.log('ProxyHelper', 'Installed promptless Polkit rule for Fleasion proxy helper')
    else:
        log_buffer.log('ProxyHelper', 'Installed Fleasion proxy helper Polkit action')
    return True


def start_helper(
    hosts: set[str],
    backend_port: int = MACOS_PROXY_BACKEND_PORT,
    timeout: float = 120.0,
    ca_cert_path: Path | None = None,
    require_system_ca: bool = False,
) -> bool:
    """Start the privileged Linux port/hosts helper and wait until it is ready."""
    pkexec = shutil.which('pkexec')
    if not pkexec:
        log_buffer.log('ProxyHelper', 'Linux proxy helper failed: pkexec not found')
        return False
    if require_system_ca and ca_cert_path is None:
        log_buffer.log('ProxyHelper', 'Linux proxy helper failed: system CA trust is required but no CA cert was supplied')
        return False
    if not ensure_privileged_helper_installed(enable_promptless=True):
        return False
    if require_system_ca and ca_cert_path is not None and not linux_system_ca_is_current(ca_cert_path):
        details = _install_ca_into_linux_system_store(ca_cert_path)
        if not details.get('ok'):
            log_buffer.log(
                'ProxyHelper',
                f'Linux proxy helper failed: system CA trust could not be installed: {details.get("error") or details}',
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
    if require_system_ca:
        cmd.append('--require-system-ca')

    log_buffer.log('ProxyHelper', 'Requesting Linux Polkit approval for Fleasion hosts entries and port-443 relay')
    try:
        log_file = HELPER_LOG_FILE.open('ab')
    except OSError as exc:
        log_buffer.log('ProxyHelper', f'Could not open Linux helper log: {exc}')
        return False

    with log_file:
        try:
            process = _popen_host_command(cmd, stdout=log_file, stderr=log_file, start_new_session=True)
        except Exception as exc:
            log_buffer.log('ProxyHelper', f'Could not start Linux proxy helper: {exc}')
            return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        ready = _read_ready()
        if ready:
            if ready.get('ok'):
                if require_system_ca and not (ready.get('system_ca') or {}).get('ok'):
                    log_buffer.log('ProxyHelper', 'Linux proxy helper failed: system CA trust was not confirmed')
                    return False
                log_buffer.log('ProxyHelper', f'Linux proxy helper ready on port {PROXY_PORT}')
                return True
            log_buffer.log('ProxyHelper', f'Linux proxy helper failed: {ready.get("error") or "unknown error"}')
            return False

        returncode = process.poll()
        if returncode is not None:
            log_buffer.log(
                'ProxyHelper',
                f'Linux proxy helper exited before becoming ready with code {returncode}; log: {HELPER_LOG_FILE}',
            )
            return False
        time.sleep(0.2)

    log_buffer.log('ProxyHelper', f'Linux proxy helper timed out waiting for readiness; log: {HELPER_LOG_FILE}')
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
            for cert_db in root.glob('*/cert9.db'):
                candidates.add(cert_db.parent)
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
    except Exception as exc:
        log_buffer.log('Certificate', f'Could not create shared NSS certificate DB at {nssdb}: {exc}')
        return None
    if result.returncode == 0 or (nssdb / 'cert9.db').exists():
        return nssdb
    err = (result.stderr or result.stdout or '').strip()
    log_buffer.log('Certificate', f'Could not create shared NSS certificate DB at {nssdb}: {err or result.returncode}')
    return None


def _install_ca_into_nss_db(certutil: str, db_dir: Path, ca_cert_path: Path) -> dict:
    db_arg = f'sql:{db_dir}'
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
    except Exception as exc:
        return {'db': str(db_dir), 'ok': False, 'error': str(exc)}

    if result.returncode == 0:
        return {'db': str(db_dir), 'ok': True}
    err = (result.stderr or result.stdout or '').strip()
    return {'db': str(db_dir), 'ok': False, 'error': err or str(result.returncode)}


def _install_ca_into_browser_nss(ca_cert_path: Path) -> list[dict]:
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
    fail_count = len(results) - ok_count
    if ok_count:
        log_buffer.log('Certificate', f'Installed CA into {format_count(ok_count, "Linux browser NSS database")}')
    if fail_count:
        log_buffer.log('Certificate', f'Failed to install CA into {format_count(fail_count, "Linux browser NSS database")}')
        for item in results:
            if not item.get('ok'):
                log_buffer.log('Certificate', f'Linux browser NSS import failed for {item.get("db")}: {item.get("error")}')
    return results


def linux_system_ca_is_current(ca_cert_path: Path) -> bool:
    """Return True when a supported Linux system CA target already matches."""
    try:
        ca_bytes = ca_cert_path.read_bytes()
    except OSError:
        return False

    supported_targets = [
        directory / SYSTEM_CA_NAME
        for directory in SYSTEM_CA_DIRS
        if directory.is_dir()
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


def linux_system_ca_needs_install(ca_cert_path: Path) -> bool:
    """Return True when a supported Linux system CA target is missing/stale."""
    supported_targets = [
        directory / SYSTEM_CA_NAME
        for directory in SYSTEM_CA_DIRS
        if directory.is_dir()
    ]
    if not supported_targets:
        return False
    if linux_system_ca_is_current(ca_cert_path):
        return False
    return True


def _install_ca_into_linux_system_store(ca_cert_path: Path) -> dict:
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
    except Exception as exc:
        log_buffer.log('Certificate', f'Failed to install CA into Linux system trust store: {exc}')
        return {'ok': False, 'error': str(exc)}

    output = (result.stdout or '').strip()
    details: dict
    try:
        details = json.loads(output) if output else {}
    except json.JSONDecodeError:
        details = {'output': output}
    details.setdefault('ok', result.returncode == 0)
    if result.returncode == 0 and details.get('ok'):
        stores = ', '.join(details.get('stores') or [])
        log_buffer.log('Certificate', f'Installed CA into Linux system trust store{f" ({stores})" if stores else ""}')
    else:
        err = details.get('error') or (result.stderr or output or str(result.returncode)).strip()
        log_buffer.log('Certificate', f'Failed to install CA into Linux system trust store: {err}')
        details['error'] = err
    return details


def install_ca_into_linux_trust(ca_cert_path: Path, *, install_system: bool = True) -> dict:
    """Trust Fleasion's CA for Linux browsers and system TLS clients."""
    if not sys.platform.startswith('linux'):
        return {'ok': True, 'skipped': 'not_linux'}

    if install_system and linux_system_ca_needs_install(ca_cert_path):
        system = _install_ca_into_linux_system_store(ca_cert_path)
    elif install_system:
        system = {'ok': True, 'skipped': 'already_installed'}
    else:
        system = {'ok': False, 'skipped': 'handled_by_privileged_helper'}
    nss = _install_ca_into_browser_nss(ca_cert_path)
    return {
        'ok': bool(system.get('ok')) or any(item.get('ok') for item in nss),
        'system': system,
        'nss': nss,
    }
