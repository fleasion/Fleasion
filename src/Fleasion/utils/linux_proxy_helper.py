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


HELPER_READY_FILE = CONFIG_DIR / 'linux_proxy_helper.ready'
HELPER_STOP_FILE = CONFIG_DIR / 'linux_proxy_helper.stop'
HELPER_LOG_FILE = CONFIG_DIR / 'linux_proxy_helper.log'


def _source_helper_path() -> Path:
    frozen_root = Path(getattr(sys, '_MEIPASS', ''))
    if frozen_root:
        bundled = frozen_root / 'linux_proxy_helper_daemon.py'
        if bundled.exists():
            return bundled
    return Path(__file__).resolve().parents[1] / 'linux_proxy_helper_daemon.py'


def _read_ready() -> dict | None:
    try:
        return json.loads(HELPER_READY_FILE.read_text(encoding='utf-8'))
    except Exception:
        return None


def start_helper(hosts: set[str], backend_port: int = MACOS_PROXY_BACKEND_PORT, timeout: float = 120.0) -> bool:
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

    helper_path = _source_helper_path()
    cmd = [
        pkexec,
        sys.executable,
        str(helper_path),
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

    log_buffer.log('ProxyHelper', 'Requesting Linux Polkit approval for proxy relay and hosts entries')
    try:
        log_file = HELPER_LOG_FILE.open('ab')
    except OSError as exc:
        log_buffer.log('ProxyHelper', f'Could not open Linux helper log: {exc}')
        return False

    with log_file:
        try:
            process = subprocess.Popen(cmd, stdout=log_file, stderr=log_file, start_new_session=True)
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
