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
import signal
import socket
import socketserver
import subprocess
import threading
import time


HELPER_VERSION = 1
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

_state_lock = threading.Lock()
_active_hosts = set()
_last_heartbeat = 0.0
_stop_event = threading.Event()
_token_file = ""
_backend_port = 58443

logger = logging.getLogger("fleasion-proxy-helper")


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

    requested = {str(host).strip().lower() for host in hosts}
    if not requested.issubset(ALLOWED_HOSTS):
        raise ValueError("request contains a host outside the Fleasion allowlist")

    try:
        with open(HOSTS_FILE, "r", encoding="utf-8", errors="replace") as handle:
            existing = handle.read()
    except FileNotFoundError:
        existing = ""

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


def _status():
    with _state_lock:
        active_hosts = sorted(_active_hosts)
        lease_remaining = max(0.0, LEASE_SECONDS - (time.monotonic() - _last_heartbeat)) if active_hosts else 0.0
    return {
        "ok": True,
        "version": HELPER_VERSION,
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
    return {"ok": False, "error": "unsupported action"}


class _ControlHandler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            raw = self.rfile.readline(65536)
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
    _read_token()

    try:
        _set_hosts([])
    except Exception as exc:
        logger.error("startup hosts cleanup failed: %s", exc)

    control = _ThreadingTCPServer(("127.0.0.1", args.control_port), _ControlHandler)
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
    try:
        relay.serve_forever()
    finally:
        _stop_event.set()
        try:
            _set_hosts([])
        except Exception as exc:
            logger.error("shutdown hosts cleanup failed: %s", exc)
        control.server_close()
        relay.server_close()


if __name__ == "__main__":
    main()
