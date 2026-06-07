"""Client and one-time installer for the privileged macOS proxy helper."""

from __future__ import annotations

import json
import os
import plistlib
import secrets
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

from .logging import log_buffer
from .paths import CONFIG_DIR, MACOS_PROXY_BACKEND_PORT, MACOS_PROXY_HELPER_CONTROL_PORT


HELPER_ID = "com.fleasion.proxy-helper"
HELPER_INSTALL_PATH = Path("/Library/PrivilegedHelperTools") / HELPER_ID
HELPER_PLIST_PATH = Path("/Library/LaunchDaemons") / f"{HELPER_ID}.plist"
HELPER_TOKEN_FILE = CONFIG_DIR / "proxy-helper.token"
HELPER_LOG_PATH = Path("/Library/Logs/Fleasion.proxy-helper.log")


def _ensure_token() -> str:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if HELPER_TOKEN_FILE.exists():
        token = HELPER_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            try:
                HELPER_TOKEN_FILE.chmod(0o600)
            except OSError:
                pass
            return token

    token = secrets.token_urlsafe(48)
    HELPER_TOKEN_FILE.write_text(token, encoding="utf-8")
    HELPER_TOKEN_FILE.chmod(0o600)
    return token


def _request(action: str, hosts: set[str] | None = None, timeout: float = 3.0) -> dict:
    token = _ensure_token()
    request = {"token": token, "action": action}
    if hosts is not None:
        request["hosts"] = sorted(hosts)

    with socket.create_connection(("127.0.0.1", MACOS_PROXY_HELPER_CONTROL_PORT), timeout=timeout) as sock:
        sock.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
        sock_file = sock.makefile("rb")
        raw = sock_file.readline(65536)
    response = json.loads(raw.decode("utf-8"))
    if not response.get("ok"):
        raise RuntimeError(str(response.get("error") or "macOS proxy helper request failed"))
    return response


def helper_status(timeout: float = 1.0) -> dict | None:
    try:
        return _request("status", timeout=timeout)
    except Exception:
        return None


def helper_is_ready() -> bool:
    status = helper_status()
    return bool(status and int(status.get("backend_port", 0)) == MACOS_PROXY_BACKEND_PORT)


def helper_apply_hosts(hosts: set[str]) -> bool:
    try:
        _request("apply", set(hosts), timeout=5.0)
        return True
    except Exception as exc:
        log_buffer.log("ProxyHelper", f"Failed to apply macOS hosts entries: {exc}")
        return False


def helper_clear_hosts() -> bool:
    try:
        _request("clear", timeout=5.0)
        return True
    except Exception as exc:
        log_buffer.log("ProxyHelper", f"Failed to clear macOS hosts entries: {exc}")
        return False


def helper_heartbeat() -> bool:
    try:
        _request("heartbeat", timeout=2.0)
        return True
    except Exception:
        return False


def _source_helper_path() -> Path:
    frozen_root = Path(getattr(sys, "_MEIPASS", ""))
    if frozen_root:
        bundled = frozen_root / "macos_proxy_helper_daemon.py"
        if bundled.exists():
            return bundled
    return Path(__file__).resolve().parents[1] / "macos_proxy_helper_daemon.py"


def _build_plist() -> bytes:
    return plistlib.dumps(
        {
            "Label": HELPER_ID,
            "ProgramArguments": [
                "/usr/bin/python3",
                str(HELPER_INSTALL_PATH),
                "--token-file",
                str(HELPER_TOKEN_FILE),
                "--backend-port",
                str(MACOS_PROXY_BACKEND_PORT),
                "--control-port",
                str(MACOS_PROXY_HELPER_CONTROL_PORT),
                "--log-path",
                str(HELPER_LOG_PATH),
            ],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Background",
            "ThrottleInterval": 2,
            "StandardOutPath": str(HELPER_LOG_PATH),
            "StandardErrorPath": str(HELPER_LOG_PATH),
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


def install_helper() -> tuple[bool, str]:
    """Install/start the root helper with one macOS administrator approval."""
    if sys.platform != "darwin":
        return False, "The macOS proxy helper is only available on macOS."
    if not Path("/usr/bin/python3").exists():
        return False, "macOS system Python 3 is unavailable."

    _ensure_token()
    source = _source_helper_path()
    if not source.exists():
        return False, f"Bundled helper source is missing: {source}"

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    staging_plist = CONFIG_DIR / f"{HELPER_ID}.plist.installing"
    staging_plist.write_bytes(_build_plist())
    staging_plist.chmod(0o600)

    commands = [
        ["/usr/bin/install", "-d", "-o", "root", "-g", "wheel", "-m", "755", str(HELPER_INSTALL_PATH.parent)],
        ["/usr/bin/install", "-o", "root", "-g", "wheel", "-m", "755", str(source), str(HELPER_INSTALL_PATH)],
        ["/usr/bin/install", "-o", "root", "-g", "wheel", "-m", "644", str(staging_plist), str(HELPER_PLIST_PATH)],
    ]
    shell_cmd = "set -e; " + " && ".join(shlex.join(command) for command in commands)
    shell_cmd += (
        f"; /bin/launchctl bootout system/{HELPER_ID} >/dev/null 2>&1 || true"
        f"; /bin/launchctl bootstrap system {shlex.quote(str(HELPER_PLIST_PATH))}"
        f"; /bin/launchctl enable system/{HELPER_ID}"
    )
    apple_script = "do shell script " + json.dumps(shell_cmd) + " with administrator privileges"

    log_buffer.log("ProxyHelper", "Requesting one-time administrator approval to install the macOS proxy helper")
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", apple_script],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except Exception as exc:
        return False, f"Could not run the helper installer: {exc}"
    finally:
        try:
            staging_plist.unlink(missing_ok=True)
        except OSError:
            pass

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or f"Helper installer exited with code {result.returncode}."

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if helper_is_ready():
            log_buffer.log("ProxyHelper", "macOS proxy helper installed and ready")
            return True, ""
        time.sleep(0.25)
    return False, f"The helper was installed but did not become ready. Check {HELPER_LOG_PATH}."
