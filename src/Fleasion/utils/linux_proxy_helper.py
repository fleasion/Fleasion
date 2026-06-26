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
HELPER_LOG_FILE = CONFIG_DIR / 'linux_proxy_helper.log'
NSS_CERT_NICKNAME = 'Fleasion Proxy CA'
SYSTEM_CA_NAME = 'fleasion-proxy-ca.crt'
HELPER_BUNDLED_EXECUTABLE_NAME = 'fleasion-linux-proxy-helper'
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


def _helper_command() -> list[str]:
    """Return a Python-free helper command for frozen builds when possible."""
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


def start_helper(
    hosts: set[str],
    backend_port: int = MACOS_PROXY_BACKEND_PORT,
    timeout: float = 120.0,
    ca_cert_path: Path | None = None,
) -> bool:
    """Start the privileged Linux port/hosts helper and wait until it is ready."""
    pkexec = shutil.which('pkexec')
    if not pkexec:
        log_buffer.log('ProxyHelper', 'Linux proxy helper failed: pkexec not found')
        return False

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        HELPER_READY_FILE.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        HELPER_STOP_FILE.unlink(missing_ok=True)

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
        '--config-dir',
        str(CONFIG_DIR),
        '--owner-uid',
        str(os.getuid()),
        '--owner-gid',
        str(os.getgid()),
        '--parent-pid',
        str(os.getpid()),
    ]
    if ca_cert_path is not None:
        cmd.extend(['--ca-cert', str(ca_cert_path)])

    log_buffer.log('ProxyHelper', 'Requesting Linux Polkit approval for proxy relay and hosts entries')
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


def linux_system_ca_needs_install(ca_cert_path: Path) -> bool:
    """Return True when a supported Linux system CA target is missing/stale."""
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
                return False
        except OSError:
            pass
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
