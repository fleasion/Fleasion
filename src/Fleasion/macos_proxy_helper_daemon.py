#!/usr/bin/env python3
"""Privileged macOS relay and hosts-file helper.

This module is installed root-owned under /Library/PrivilegedHelperTools. It
intentionally uses only the Python standard library so the privileged surface
stays small and independent from Fleasion's GUI and replacement engine.
"""

import argparse
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import ssl
import stat
from pathlib import Path
import re
import signal
import socket
import socketserver
import subprocess
import tempfile
import threading
import time


HELPER_VERSION = 4
HELPER_CAPABILITIES = ("hosts", "relay", "patch_ca")
HOSTS_FILE = "/etc/hosts"
HOSTS_MARKER = "# Fleasion proxy entry"
ALLOWED_HOSTS = {
    "apis.roblox.com",
    "assetdelivery.roblox.com",
    "contentdelivery.roblox.com",
    "fts.rbxcdn.com",
    "gamejoin.roblox.com",
}
LEASE_SECONDS = 20.0
_PEM_CERT_BLOCK_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----\s*",
    re.DOTALL,
)
_ALLOWED_ROBLOX_APPS = {
    "Roblox.app": "RobloxPlayer",
    "RobloxStudio.app": "RobloxStudio",
}

_state_lock = threading.Lock()
_active_hosts = set()
_last_heartbeat = 0.0
_stop_event = threading.Event()
_token_file = ""
_backend_port = 58443

logger = logging.getLogger("fleasion-proxy-helper")


def _default_hosts_content():
    return (
        "##\n"
        "# Host Database\n"
        "#\n"
        "# localhost is used to configure the loopback interface\n"
        "# when the system is booting.  Do not change this entry.\n"
        "##\n"
        "127.0.0.1\tlocalhost\n"
        "255.255.255.255\tbroadcasthost\n"
        "::1             localhost\n"
    )


def _configure_logging(log_path):
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)


def _read_token():
    with open(_token_file, "r", encoding="utf-8") as handle:
        token = handle.read().strip()
    if len(token) < 32:
        raise RuntimeError("helper token is missing or invalid")
    return token


def _line_targets_allowed_host(raw_line):
    active = raw_line.split("#", 1)[0].strip()
    parts = active.split()
    if len(parts) < 2 or parts[0] != "127.0.0.1":
        return False
    return any(host.lower() in ALLOWED_HOSTS for host in parts[1:])


def _parse_entries(content):
    entries = {}
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        active = raw_line.split("#", 1)[0].strip()
        parts = active.split()
        if len(parts) < 2:
            continue
        for host in parts[1:]:
            entries.setdefault(host.lower(), []).append((parts[0], line_no, raw_line))
    return entries


def _flush_dns():
    for cmd in (["/usr/bin/dscacheutil", "-flushcache"], ["/usr/bin/killall", "-HUP", "mDNSResponder"]):
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass


def _set_hosts(hosts):
    global _active_hosts, _last_heartbeat

    Path(HOSTS_FILE).parent.mkdir(exist_ok=True)
    requested = {str(host).strip().lower() for host in hosts}
    if not requested.issubset(ALLOWED_HOSTS):
        raise ValueError("request contains a host outside the Fleasion allowlist")

    try:
        with open(HOSTS_FILE, "r", encoding="utf-8", errors="replace") as handle:
            existing = handle.read()
    except FileNotFoundError:
        existing = _default_hosts_content()

    entries = _parse_entries(existing)
    for host in sorted(requested):
        for ip, line_no, raw_line in entries.get(host, []):
            if ip != "127.0.0.1":
                raise RuntimeError("hosts conflict for {} at line {}: {}".format(host, line_no, raw_line))

    filtered = [
        line
        for line in existing.splitlines(keepends=True)
        if HOSTS_MARKER not in line and not _line_targets_allowed_host(line)
    ]
    content = "".join(filtered).rstrip("\n")
    if requested:
        additions = "\n".join(
            "127.0.0.1 {} {}".format(host, HOSTS_MARKER) for host in sorted(requested)
        )
        content = (content + "\n" if content else "") + additions
    content += "\n"

    with open(HOSTS_FILE, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())

    _flush_dns()
    with _state_lock:
        _active_hosts = requested
        _last_heartbeat = time.monotonic() if requested else 0.0
    logger.info("active hosts updated: %s", ", ".join(sorted(requested)) or "none")


def _normalize_newlines(text):
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _normalize_pem_block(pem):
    return _normalize_newlines(pem).strip() + "\n"


def _is_fleasion_ca_cert_block(pem_block):
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(_normalize_pem_block(pem_block))
            temp_path = handle.name
        try:
            cert = ssl._ssl._test_decode_cert(temp_path)
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        subject = cert.get("subject") or ()
        issuer = cert.get("issuer") or ()

        def _name_value(entries, key):
            for rdn in entries:
                for attr_key, attr_value in rdn:
                    if attr_key == key:
                        return attr_value
            return ""

        return (
            subject == issuer
            and _name_value(subject, "commonName") == "Fleasion Proxy CA"
            and _name_value(subject, "organizationName") == "Fleasion"
        )
    except Exception:
        return False


def _is_relative_to(child, parent):
    try:
        return os.path.commonpath([str(child), str(parent)]) == str(parent)
    except (OSError, ValueError):
        return False


def _validate_resource_root(raw_resource_dir):
    resource_dir = Path(str(raw_resource_dir or "")).expanduser()
    if not resource_dir.is_absolute():
        raise ValueError("resource_dir must be absolute")
    resource_root = resource_dir.resolve(strict=True)
    contents_dir = resource_root.parent
    app_root = contents_dir.parent
    if resource_root.name != "Resources" or contents_dir.name != "Contents":
        raise ValueError("resource_dir is not a Roblox app Resources directory")
    executable_name = _ALLOWED_ROBLOX_APPS.get(app_root.name)
    if executable_name is None:
        raise ValueError("resource_dir is not under a supported Roblox app bundle")
    executable = app_root / "Contents" / "MacOS" / executable_name
    if not executable.is_file():
        raise ValueError("Roblox app executable was not found")
    return resource_root


def _safe_cacert_path(resource_root):
    ssl_dir = resource_root / "ssl"
    if ssl_dir.is_symlink():
        raise ValueError("Roblox ssl directory is a symlink")
    if ssl_dir.exists():
        if not ssl_dir.is_dir():
            raise ValueError("Roblox ssl path is not a directory")
        resolved_ssl = ssl_dir.resolve(strict=True)
        if not _is_relative_to(resolved_ssl, resource_root):
            raise ValueError("Roblox ssl directory escapes the app resources root")
    else:
        ssl_dir.mkdir(mode=0o755, exist_ok=True)

    ca_file = ssl_dir / "cacert.pem"
    if ca_file.is_symlink():
        raise ValueError("Roblox cacert.pem is a symlink")
    if ca_file.exists() and not ca_file.is_file():
        raise ValueError("Roblox cacert.pem is not a regular file")
    resolved_ca_parent = ca_file.parent.resolve(strict=True)
    if not _is_relative_to(resolved_ca_parent, resource_root):
        raise ValueError("Roblox cacert.pem parent escapes the app resources root")
    return ca_file


def _strip_requested_pem_blocks(cacert_text, remove_pems, *, strip_all_fleasion_ca=False):
    normalized = _normalize_newlines(cacert_text)
    remove_set = {
        _normalize_pem_block(pem)
        for pem in remove_pems
        if isinstance(pem, str) and pem.strip()
    }
    if not remove_set and not strip_all_fleasion_ca:
        return normalized

    pieces = []
    last_end = 0
    for match in _PEM_CERT_BLOCK_RE.finditer(normalized):
        pieces.append(normalized[last_end:match.start()])
        block = _normalize_pem_block(match.group(0))
        if block not in remove_set and not (strip_all_fleasion_ca and _is_fleasion_ca_cert_block(block)):
            pieces.append(match.group(0))
        last_end = match.end()
    pieces.append(normalized[last_end:])
    return "".join(pieces)


def _atomic_write_text(path, content):
    fd, tmp_path = tempfile.mkstemp(prefix=".fleasion_cacert_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _clear_write_barriers(path):
    try:
        current_mode = path.stat().st_mode
    except OSError:
        return

    desired_mode = current_mode | stat.S_IWUSR
    if path.is_dir():
        desired_mode |= stat.S_IXUSR

    try:
        path.chmod(desired_mode)
    except OSError:
        pass

    if not hasattr(os, "chflags"):
        return

    immutable_mask = 0
    for name in ("UF_IMMUTABLE", "UF_APPEND", "SF_IMMUTABLE", "SF_APPEND"):
        immutable_mask |= getattr(stat, name, 0)
    if immutable_mask == 0:
        return

    try:
        current_flags = getattr(path.stat(), "st_flags", 0)
    except OSError:
        return

    try:
        os.chflags(path, current_flags & ~immutable_mask)
    except OSError:
        pass


def _prepare_cacert_target(resource_root, ca_file):
    _clear_write_barriers(resource_root)
    _clear_write_barriers(ca_file.parent)
    if ca_file.exists():
        _clear_write_barriers(ca_file)


def _normalize_cacert_permissions(ca_file):
    try:
        ca_file.chmod(0o644)
    except OSError:
        pass


def _patch_ca(ca_pem, installs):
    current_ca = _normalize_pem_block(ca_pem)
    if not _PEM_CERT_BLOCK_RE.fullmatch(current_ca):
        raise ValueError("ca_pem is not a PEM certificate block")
    if not isinstance(installs, list):
        raise ValueError("installs must be a list")

    patched = []
    skipped = []
    failed = []

    for item in installs:
        raw_resource_dir = item.get("resource_dir") if isinstance(item, dict) else ""
        result = {"resource_dir": str(raw_resource_dir or "")}
        try:
            resource_root = _validate_resource_root(raw_resource_dir)
            ca_file = _safe_cacert_path(resource_root)
            _prepare_cacert_target(resource_root, ca_file)
            result["resource_dir"] = str(resource_root)
            result["ca_file"] = str(ca_file)

            try:
                existing = ca_file.read_text(encoding="utf-8", errors="replace") if ca_file.exists() else ""
            except PermissionError:
                _prepare_cacert_target(resource_root, ca_file)
                existing = ca_file.read_text(encoding="utf-8", errors="replace") if ca_file.exists() else ""
            remove_pems = list(item.get("remove_pems") or []) if isinstance(item, dict) else []
            remove_pems.append(current_ca)
            strip_all_fleasion_ca = bool(item.get("strip_all_fleasion_ca")) if isinstance(item, dict) else False
            cleaned = _strip_requested_pem_blocks(
                existing,
                remove_pems,
                strip_all_fleasion_ca=strip_all_fleasion_ca,
            ).rstrip("\n")
            updated = f"{cleaned}\n{current_ca}" if cleaned else current_ca
            if updated == _normalize_newlines(existing):
                _normalize_cacert_permissions(ca_file)
                result["status"] = "already_current"
                result["changed"] = False
                skipped.append(result)
                continue

            try:
                _atomic_write_text(ca_file, updated)
            except PermissionError:
                _prepare_cacert_target(resource_root, ca_file)
                _atomic_write_text(ca_file, updated)
            _normalize_cacert_permissions(ca_file)
            result["status"] = "patched"
            result["changed"] = True
            patched.append(result)
        except Exception as exc:
            result["status"] = "failed"
            result["error"] = str(exc)
            failed.append(result)
            logger.warning("CA patch failed for %s: %s", raw_resource_dir, exc)

    ok = not failed and bool(patched or skipped)
    return {
        "ok": ok,
        "version": HELPER_VERSION,
        "capabilities": list(HELPER_CAPABILITIES),
        "patched": patched,
        "skipped": skipped,
        "failed": failed,
        "error": "" if ok else "one or more Roblox CA patches failed",
    }


def _status():
    with _state_lock:
        active_hosts = sorted(_active_hosts)
        lease_remaining = max(0.0, LEASE_SECONDS - (time.monotonic() - _last_heartbeat)) if active_hosts else 0.0
    return {
        "ok": True,
        "version": HELPER_VERSION,
        "capabilities": list(HELPER_CAPABILITIES),
        "active_hosts": active_hosts,
        "backend_port": _backend_port,
        "lease_remaining": lease_remaining,
    }


def _handle_request(request):
    supplied = str(request.get("token") or "")
    if not hmac.compare_digest(supplied, _read_token()):
        return {"ok": False, "error": "unauthorized"}

    action = str(request.get("action") or "")
    if action == "status":
        return _status()
    if action == "apply":
        _set_hosts(request.get("hosts") or [])
        return _status()
    if action == "clear":
        _set_hosts([])
        return _status()
    if action == "heartbeat":
        global _last_heartbeat
        with _state_lock:
            if _active_hosts:
                _last_heartbeat = time.monotonic()
        return _status()
    if action == "patch_ca":
        return _patch_ca(str(request.get("ca_pem") or ""), request.get("installs") or [])
    return {"ok": False, "error": "unsupported action"}


class _ControlHandler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            raw = self.rfile.readline(1024 * 1024)
            request = json.loads(raw.decode("utf-8"))
            response = _handle_request(request)
        except Exception as exc:
            logger.warning("control request failed: %s", exc)
            response = {"ok": False, "error": str(exc)}
        self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            backend = socket.create_connection(("127.0.0.1", _backend_port), timeout=3.0)
        except OSError:
            return

        client = self.request
        client.settimeout(None)
        backend.settimeout(None)

        def pump(source, destination):
            try:
                while True:
                    chunk = source.recv(65536)
                    if not chunk:
                        break
                    destination.sendall(chunk)
            except OSError:
                pass
            finally:
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        forward = threading.Thread(target=pump, args=(client, backend), daemon=True)
        forward.start()
        pump(backend, client)
        forward.join(timeout=2.0)
        backend.close()


class _ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _lease_monitor():
    while not _stop_event.wait(2.0):
        with _state_lock:
            expired = bool(_active_hosts) and time.monotonic() - _last_heartbeat > LEASE_SECONDS
        if expired:
            logger.warning("proxy heartbeat lease expired; clearing hosts entries")
            try:
                _set_hosts([])
            except Exception as exc:
                logger.error("failed to clear hosts after lease expiry: %s", exc)


def main():
    global _token_file, _backend_port

    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--backend-port", type=int, required=True)
    parser.add_argument("--control-port", type=int, required=True)
    parser.add_argument("--log-path", default="/Library/Logs/Fleasion.proxy-helper.log")
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("Fleasion proxy helper must run as root")

    _token_file = args.token_file
    _backend_port = args.backend_port
    _configure_logging(args.log_path)

    control = None
    relay = None
    try:
        logger.info(
            "helper starting: control 127.0.0.1:%d, relay 127.0.0.1:443 -> 127.0.0.1:%d",
            args.control_port,
            _backend_port,
        )
        _read_token()

        try:
            _set_hosts([])
        except Exception as exc:
            logger.error("startup hosts cleanup failed: %s", exc)

        logger.info("binding helper control 127.0.0.1:%d", args.control_port)
        control = _ThreadingTCPServer(("127.0.0.1", args.control_port), _ControlHandler)
        logger.info("binding helper relay 127.0.0.1:443")
        relay = _ThreadingTCPServer(("127.0.0.1", 443), _RelayHandler)

        def stop_handler(_signum, _frame):
            _stop_event.set()
            threading.Thread(target=control.shutdown, daemon=True).start()
            threading.Thread(target=relay.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop_handler)
        signal.signal(signal.SIGINT, stop_handler)

        threading.Thread(target=control.serve_forever, daemon=True, name="fleasion-helper-control").start()
        threading.Thread(target=_lease_monitor, daemon=True, name="fleasion-helper-lease").start()
        logger.info("helper ready: relay 127.0.0.1:443 -> 127.0.0.1:%d", _backend_port)
        relay.serve_forever()
    except Exception:
        logger.exception("helper startup failed")
        raise
    finally:
        _stop_event.set()
        try:
            _set_hosts([])
        except Exception as exc:
            logger.error("shutdown hosts cleanup failed: %s", exc)
        if control is not None:
            control.server_close()
        if relay is not None:
            relay.server_close()


if __name__ == "__main__":
    main()
