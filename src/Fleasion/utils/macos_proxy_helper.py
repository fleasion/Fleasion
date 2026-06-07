"""Client and one-time installer for the privileged macOS proxy helper."""

from __future__ import annotations

import json
import os
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

from .logging import log_buffer
from .paths import CONFIG_DIR, MACOS_PROXY_BACKEND_PORT, MACOS_PROXY_HELPER_CONTROL_PORT


HELPER_ID = "com.fleasion.proxy-helper"
HELPER_INSTALL_PATH = Path("/Library/PrivilegedHelperTools") / HELPER_ID
HELPER_PLIST_PATH = Path("/Library/LaunchDaemons") / f"{HELPER_ID}.plist"
HELPER_TOKEN_FILE = CONFIG_DIR / "proxy-helper.token"
HELPER_LOG_PATH = Path("/Library/Logs/Fleasion.proxy-helper.log")
REQUIRED_HELPER_VERSION = 3
REQUIRED_HELPER_CAPABILITIES = {"patch_ca"}


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


def _request(
    action: str,
    hosts: set[str] | None = None,
    timeout: float = 3.0,
    *,
    raise_on_error: bool = True,
    **payload,
) -> dict:
    token = _ensure_token()
    request = {"token": token, "action": action}
    if hosts is not None:
        request["hosts"] = sorted(hosts)
    request.update(payload)

    with socket.create_connection(("127.0.0.1", MACOS_PROXY_HELPER_CONTROL_PORT), timeout=timeout) as sock:
        sock.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
        sock_file = sock.makefile("rb")
        raw = sock_file.readline(1024 * 1024)
    response = json.loads(raw.decode("utf-8"))
    if raise_on_error and not response.get("ok"):
        raise RuntimeError(str(response.get("error") or "macOS proxy helper request failed"))
    return response


def helper_status(timeout: float = 1.0) -> dict | None:
    try:
        return _request("status", timeout=timeout)
    except Exception:
        return None


def helper_has_required_ca_patch(status: dict | None) -> bool:
    if not status:
        return False
    try:
        version_ok = int(status.get("version", 0)) >= REQUIRED_HELPER_VERSION
    except (TypeError, ValueError):
        version_ok = False
    capabilities = {str(value) for value in status.get("capabilities") or []}
    return version_ok and REQUIRED_HELPER_CAPABILITIES.issubset(capabilities)


def helper_is_ready(*, require_ca_patch: bool = True) -> bool:
    status = helper_status()
    if not status:
        return False
    try:
        backend_ok = int(status.get("backend_port", 0)) == MACOS_PROXY_BACKEND_PORT
    except (TypeError, ValueError):
        backend_ok = False
    if not backend_ok:
        return False
    if require_ca_patch and not helper_has_required_ca_patch(status):
        log_buffer.log(
            "ProxyHelper",
            "Installed macOS proxy helper is missing CA patch support; reinstalling/upgrading helper",
        )
        return False
    return True


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


def helper_patch_ca(ca_pem: str, installs: list[dict]) -> dict | None:
    try:
        response = _request(
            "patch_ca",
            timeout=10.0,
            raise_on_error=False,
            ca_pem=ca_pem,
            installs=installs,
        )
    except Exception as exc:
        log_buffer.log("ProxyHelper", f"Failed to request macOS Roblox CA patch: {exc}")
        return None

    if not response.get("ok"):
        log_buffer.log(
            "ProxyHelper",
            f"macOS Roblox CA patch reported failure: {response.get('error') or 'unknown error'}",
        )
    return response


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


def _stage_installer_payload(source: Path) -> tuple[Path, Path, Path]:
    """Copy helper install inputs outside TCC-protected project paths."""
    staging_dir = Path(tempfile.mkdtemp(prefix=f"{HELPER_ID}."))
    staging_dir.chmod(0o755)

    staging_helper = staging_dir / HELPER_ID
    staging_helper.write_bytes(source.read_bytes())
    staging_helper.chmod(0o644)

    staging_plist = staging_dir / f"{HELPER_ID}.plist"
    staging_plist.write_bytes(_build_plist())
    staging_plist.chmod(0o644)
    return staging_dir, staging_helper, staging_plist


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

    try:
        staging_dir, staging_helper, staging_plist = _stage_installer_payload(source)
    except Exception as exc:
        return False, f"Could not stage the macOS proxy helper installer: {exc}"

    commands = [
        ["/usr/bin/install", "-d", "-o", "root", "-g", "wheel", "-m", "755", str(HELPER_INSTALL_PATH.parent)],
        ["/usr/bin/install", "-o", "root", "-g", "wheel", "-m", "755", str(staging_helper), str(HELPER_INSTALL_PATH)],
        ["/usr/bin/install", "-o", "root", "-g", "wheel", "-m", "644", str(staging_plist), str(HELPER_PLIST_PATH)],
    ]
    xattr_cmd = shlex.join(["/usr/bin/xattr", "-c", str(HELPER_INSTALL_PATH), str(HELPER_PLIST_PATH)])
    bootstrap_cmd = shlex.join(["/bin/launchctl", "bootstrap", "system", str(HELPER_PLIST_PATH)])
    load_cmd = shlex.join(["/bin/launchctl", "load", "-w", str(HELPER_PLIST_PATH)])
    bootout_label = shlex.join(["/bin/launchctl", "bootout", f"system/{HELPER_ID}"])
    bootout_plist = shlex.join(["/bin/launchctl", "bootout", "system", str(HELPER_PLIST_PATH)])
    enable_cmd = shlex.join(["/bin/launchctl", "enable", f"system/{HELPER_ID}"])
    install_cmds = " && ".join(shlex.join(command) for command in commands)
    shell_cmd = f"""
set -e
{bootout_label} >/dev/null 2>&1 || true
{bootout_plist} >/dev/null 2>&1 || true
/bin/sleep 0.2
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
exit 0
""".strip()
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
            shutil.rmtree(staging_dir, ignore_errors=True)
        except OSError:
            pass

    install_output = (result.stdout or result.stderr or "").strip()
    if install_output:
        log_buffer.log("ProxyHelper", f"macOS helper installer output: {install_output}")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or f"Helper installer exited with code {result.returncode}."

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if helper_is_ready():
            log_buffer.log("ProxyHelper", "macOS proxy helper installed and ready")
            return True, ""
        time.sleep(0.25)
    detail = f"The helper was installed but did not become ready. Check {HELPER_LOG_PATH}."
    if install_output:
        detail += f"\n\nLaunch output:\n{install_output}"
    return False, detail
