#!/usr/bin/env python3  # ruff: ignore[shebang-not-executable]
"""Privileged macOS relay and hosts-file helper.

This module is installed root-owned under /Library/PrivilegedHelperTools. It
intentionally uses only the Python standard library so the privileged surface
stays small and independent from Fleasion's GUI and replacement engine.
"""

import argparse
import contextlib
import hmac
import json
import logging
import os
import re
import signal
import socket
import socketserver
import ssl
import stat
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import threading
import time
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from types import FrameType

HELPER_VERSION = 7
HELPER_CAPABILITIES = ('hosts', 'relay', 'patch_ca', 'probe_backend')
HOSTS_FILE = '/etc/hosts'
HOSTS_MARKER = '# Fleasion proxy entry'
ALLOWED_HOSTS = {
    'apis.roblox.com',
    'assetdelivery.roblox.com',
    'contentdelivery.roblox.com',
    'clientsettings.roblox.com',
    'clientsettingscdn.roblox.com',
    'fts.rbxcdn.com',
    'gamejoin.roblox.com',
}
LEASE_SECONDS = 20.0
_PEM_CERT_BLOCK_RE = re.compile(
    r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----\s*',
    re.DOTALL,
)
_ALLOWED_ROBLOX_APPS = {
    'Roblox.app': 'RobloxPlayer',
    'RobloxStudio.app': 'RobloxStudio',
}
_USERS_ROOT = Path('/Users')
_PATH_ERRORS = (OSError, ValueError)

_state_lock = threading.Lock()
_active_hosts: set[str] = set()
_last_heartbeat = 0.0
_stop_event = threading.Event()
_token_file = ''
_backend_port = 58443

logger = logging.getLogger('fleasion-proxy-helper')

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]
type HostsEntry = tuple[str, int, str]
type CertificateName = tuple[tuple[tuple[str, str], ...], ...]


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in cast('list[object]', value)]
    if isinstance(value, dict):
        result: JsonObject = {}
        for key, item in cast('dict[object, object]', value).items():
            if not isinstance(key, str):
                msg = 'JSON object keys must be strings'
                raise TypeError(msg)
            result[key] = _json_value(item)
        return result
    msg = f'unsupported JSON value type: {type(value).__name__}'
    raise TypeError(msg)


def _json_list(values: Iterable[JsonValue]) -> list[JsonValue]:
    return list(values)


def _request_object(request: JsonValue) -> JsonObject:
    if isinstance(request, dict):
        return request
    msg = f"'{type(request).__name__}' object has no attribute 'get'"
    raise AttributeError(msg)


def _as_iterable(value: object) -> Iterable[object]:
    if isinstance(value, Iterable):
        return cast('Iterable[object]', value)
    msg = f"'{type(value).__name__}' object is not iterable"
    raise TypeError(msg)


def _certificate_name(value: object) -> CertificateName:
    if not isinstance(value, tuple):
        return ()
    name: list[tuple[tuple[str, str], ...]] = []
    for raw_rdn in cast('tuple[object, ...]', value):
        if not isinstance(raw_rdn, tuple):
            return ()
        rdn: list[tuple[str, str]] = []
        for raw_attr in cast('tuple[object, ...]', raw_rdn):
            if not isinstance(raw_attr, tuple):
                return ()
            attr = cast('tuple[object, ...]', raw_attr)
            if len(attr) != 2:  # ruff: ignore[magic-value-comparison]
                return ()
            attr_key, attr_value = attr
            if not isinstance(attr_key, str) or not isinstance(attr_value, str):
                return ()
            rdn.append((attr_key, attr_value))
        name.append(tuple(rdn))
    return tuple(name)


def _name_value(entries: CertificateName, key: str) -> str:
    for rdn in entries:
        for attr_key, attr_value in rdn:
            if attr_key == key:
                return attr_value
    return ''


def _default_hosts_content() -> str:
    return (
        '##\n'
        '# Host Database\n'
        '#\n'
        '# localhost is used to configure the loopback interface\n'
        '# when the system is booting.  Do not change this entry.\n'
        '##\n'
        '127.0.0.1\tlocalhost\n'
        '255.255.255.255\tbroadcasthost\n'
        '::1             localhost\n'
    )


def _configure_logging(log_path: str | os.PathLike[str]) -> None:
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=2)
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(handler)


def _read_token() -> str:
    with open(_token_file, encoding='utf-8') as handle:  # ruff: ignore[builtin-open, read-whole-file]
        token = handle.read().strip()
    if len(token) < 32:  # ruff: ignore[magic-value-comparison]
        msg = 'helper token is missing or invalid'
        raise RuntimeError(msg)
    return token


def _line_targets_allowed_host(raw_line: str) -> bool:
    active = raw_line.split('#', 1)[0].strip()
    parts = active.split()
    if len(parts) < 2 or parts[0] != '127.0.0.1':  # ruff: ignore[magic-value-comparison]
        return False
    return any(host.lower() in ALLOWED_HOSTS for host in parts[1:])


def _parse_entries(content: str) -> dict[str, list[HostsEntry]]:
    entries: dict[str, list[HostsEntry]] = {}
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        active = raw_line.split('#', 1)[0].strip()
        parts = active.split()
        if len(parts) < 2:  # ruff: ignore[magic-value-comparison]
            continue
        for host in parts[1:]:
            entries.setdefault(host.lower(), []).append((parts[0], line_no, raw_line))
    return entries


def _flush_dns() -> None:
    for cmd in (
        ['/usr/bin/dscacheutil', '-flushcache'],
        ['/usr/bin/killall', '-HUP', 'mDNSResponder'],
    ):
        try:  # ruff: ignore[suppressible-exception]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass


def _set_hosts(hosts: Iterable[object]) -> None:
    global _active_hosts, _last_heartbeat  # ruff: ignore[global-statement]

    Path(HOSTS_FILE).parent.mkdir(exist_ok=True)
    requested = {str(host).strip().lower() for host in hosts}
    if not requested.issubset(ALLOWED_HOSTS):
        msg = 'request contains a host outside the Fleasion allowlist'
        raise ValueError(msg)

    try:
        existing = Path(HOSTS_FILE).read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        existing = _default_hosts_content()

    entries = _parse_entries(existing)
    for host in sorted(requested):
        for ip, line_no, raw_line in entries.get(host, []):
            if ip != '127.0.0.1':
                msg = f'hosts conflict for {host} at line {line_no}: {raw_line}'
                raise RuntimeError(msg)

    filtered = [
        line
        for line in existing.splitlines(keepends=True)
        if HOSTS_MARKER not in line and not _line_targets_allowed_host(line)
    ]
    content = ''.join(filtered).rstrip('\n')
    if requested:
        additions = '\n'.join(f'127.0.0.1 {host} {HOSTS_MARKER}' for host in sorted(requested))
        content = (content + '\n' if content else '') + additions
    content += '\n'

    with open(HOSTS_FILE, 'w', encoding='utf-8') as handle:  # ruff: ignore[builtin-open]
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())

    _flush_dns()
    with _state_lock:
        _active_hosts = requested
        _last_heartbeat = time.monotonic() if requested else 0.0
    logger.info('active hosts updated: %s', ', '.join(sorted(requested)) or 'none')


def _normalize_newlines(text: object) -> str:
    return str(text or '').replace('\r\n', '\n').replace('\r', '\n')


def _normalize_pem_block(pem: object) -> str:
    return _normalize_newlines(pem).strip() + '\n'


def _is_fleasion_ca_cert_block(pem_block: object) -> bool:
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False) as handle:
            handle.write(_normalize_pem_block(pem_block))
            temp_path = handle.name
        try:
            ssl_impl: object = getattr(ssl, '_ssl')  # ruff: ignore[get-attr-with-constant]
            decode_cert = getattr(ssl_impl, '_test_decode_cert')  # ruff: ignore[get-attr-with-constant]
            if not callable(decode_cert):
                return False
            decoded: object = decode_cert(temp_path)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)  # ruff: ignore[os-unlink]
        if not isinstance(decoded, dict):
            return False
        cert = cast('dict[object, object]', decoded)
        subject = _certificate_name(cert.get('subject'))
        issuer = _certificate_name(cert.get('issuer'))
        return (
            subject == issuer
            and _name_value(subject, 'commonName') == 'Fleasion Proxy CA'
            and _name_value(subject, 'organizationName') == 'Fleasion'
        )
    except Exception:  # ruff: ignore[blind-except]
        return False


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([str(child), str(parent)]) == str(parent)
    # The source helper is also used by `uv run`, where launchd invokes the
    # system Python rather than Fleasion's Python 3.14 runtime.
    except _PATH_ERRORS:
        return False


def _validate_resource_root(raw_resource_dir: object) -> Path:
    resource_dir = Path(str(raw_resource_dir or '')).expanduser()
    if not resource_dir.is_absolute():
        msg = 'resource_dir must be absolute'
        raise ValueError(msg)
    resource_root = resource_dir.resolve(strict=True)
    contents_dir = resource_root.parent
    app_root = contents_dir.parent
    if resource_root.name != 'Resources' or contents_dir.name != 'Contents':
        msg = 'resource_dir is not a Roblox app Resources directory'
        raise ValueError(msg)
    executable_name = _ALLOWED_ROBLOX_APPS.get(app_root.name)
    if app_root.name == 'RobloxPlayer.app' and _is_froststrap_player_bundle(app_root):
        executable_name = 'RobloxPlayer'
    if executable_name is None:
        msg = 'resource_dir is not under a supported Roblox app bundle'
        raise ValueError(msg)
    executable = app_root / 'Contents' / 'MacOS' / executable_name
    if not executable.is_file():
        msg = 'Roblox app executable was not found'
        raise ValueError(msg)
    return resource_root


def _is_froststrap_player_bundle(app_root: Path) -> bool:
    """Only admit Froststrap's version-managed RobloxPlayer.app layout."""
    try:
        relative = app_root.relative_to(_USERS_ROOT)
    except ValueError:
        return False
    parts = relative.parts
    return (
        len(parts) == 7  # ruff: ignore[magic-value-comparison]
        and bool(parts[0])
        and parts[1:5]
        == (
            'Library',
            'Application Support',
            'Froststrap',
            'Versions',
        )
        and parts[5].startswith('version-')
        and len(parts[5]) > len('version-')
        and parts[6] == 'RobloxPlayer.app'
    )


def _safe_cacert_path(resource_root: Path) -> Path:
    ssl_dir = resource_root / 'ssl'
    if ssl_dir.is_symlink():
        msg = 'Roblox ssl directory is a symlink'
        raise ValueError(msg)
    if ssl_dir.exists():
        if not ssl_dir.is_dir():
            msg = 'Roblox ssl path is not a directory'
            raise ValueError(msg)
        resolved_ssl = ssl_dir.resolve(strict=True)
        if not _is_relative_to(resolved_ssl, resource_root):
            msg = 'Roblox ssl directory escapes the app resources root'
            raise ValueError(msg)
    else:
        ssl_dir.mkdir(mode=0o755, exist_ok=True)

    ca_file = ssl_dir / 'cacert.pem'
    if ca_file.is_symlink():
        msg = 'Roblox cacert.pem is a symlink'
        raise ValueError(msg)
    if ca_file.exists() and not ca_file.is_file():
        msg = 'Roblox cacert.pem is not a regular file'
        raise ValueError(msg)
    resolved_ca_parent = ca_file.parent.resolve(strict=True)
    if not _is_relative_to(resolved_ca_parent, resource_root):
        msg = 'Roblox cacert.pem parent escapes the app resources root'
        raise ValueError(msg)
    return ca_file


def _strip_requested_pem_blocks(
    cacert_text: object,
    remove_pems: Iterable[object],
    *,
    strip_all_fleasion_ca: bool = False,
) -> str:
    normalized = _normalize_newlines(cacert_text)
    remove_set = {
        _normalize_pem_block(pem) for pem in remove_pems if isinstance(pem, str) and pem.strip()
    }
    if not remove_set and not strip_all_fleasion_ca:
        return normalized

    pieces: list[str] = []
    last_end = 0
    for match in _PEM_CERT_BLOCK_RE.finditer(normalized):
        pieces.append(normalized[last_end : match.start()])
        block = _normalize_pem_block(match.group(0))
        if block not in remove_set and not (
            strip_all_fleasion_ca and _is_fleasion_ca_cert_block(block)
        ):
            pieces.append(match.group(0))
        last_end = match.end()
    pieces.append(normalized[last_end:])
    return ''.join(pieces)


def _atomic_write_text(path: Path, content: str) -> None:
    fd, tmp_path = tempfile.mkstemp(prefix='.fleasion_cacert_', dir=str(path.parent))
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)  # ruff: ignore[os-replace]
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)  # ruff: ignore[os-unlink]
        raise


def _clear_write_barriers(path: Path) -> None:
    try:
        current_mode = path.stat().st_mode
    except OSError:
        return

    desired_mode = current_mode | stat.S_IWUSR
    if path.is_dir():
        desired_mode |= stat.S_IXUSR

    with contextlib.suppress(OSError):
        path.chmod(desired_mode)

    chflags = getattr(os, 'chflags', None)
    if not callable(chflags):
        return

    immutable_mask = 0
    for name in ('UF_IMMUTABLE', 'UF_APPEND', 'SF_IMMUTABLE', 'SF_APPEND'):
        immutable_mask |= getattr(stat, name, 0)
    if immutable_mask == 0:
        return

    try:
        current_flags = getattr(path.stat(), 'st_flags', 0)
    except OSError:
        return

    with contextlib.suppress(OSError):
        chflags(path, current_flags & ~immutable_mask)


def _prepare_cacert_target(resource_root: Path, ca_file: Path) -> None:
    _clear_write_barriers(resource_root)
    _clear_write_barriers(ca_file.parent)
    if ca_file.exists():
        _clear_write_barriers(ca_file)


def _normalize_cacert_permissions(ca_file: Path) -> None:
    with contextlib.suppress(OSError):
        ca_file.chmod(0o644)


def _patch_ca(ca_pem: object, installs: object) -> JsonObject:  # ruff: ignore[too-many-locals]
    current_ca = _normalize_pem_block(ca_pem)
    if not _PEM_CERT_BLOCK_RE.fullmatch(current_ca):
        msg = 'ca_pem is not a PEM certificate block'
        raise ValueError(msg)
    if not isinstance(installs, list):
        msg = 'installs must be a list'
        raise ValueError(msg)  # ruff: ignore[type-check-without-type-error]

    patched: list[JsonObject] = []
    skipped: list[JsonObject] = []
    failed: list[JsonObject] = []

    for item in cast('list[object]', installs):
        item_dict = cast('dict[object, object]', item) if isinstance(item, dict) else None
        raw_resource_dir = item_dict.get('resource_dir') if item_dict is not None else ''
        result: JsonObject = {'resource_dir': str(raw_resource_dir or '')}
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            resource_root = _validate_resource_root(raw_resource_dir)
            ca_file = _safe_cacert_path(resource_root)
            _prepare_cacert_target(resource_root, ca_file)
            result['resource_dir'] = str(resource_root)
            result['ca_file'] = str(ca_file)

            try:
                existing = (
                    ca_file.read_text(encoding='utf-8', errors='replace')
                    if ca_file.exists()
                    else ''
                )
            except PermissionError:
                _prepare_cacert_target(resource_root, ca_file)
                existing = (
                    ca_file.read_text(encoding='utf-8', errors='replace')
                    if ca_file.exists()
                    else ''
                )
            remove_pems = (
                list(_as_iterable(item_dict.get('remove_pems') or []))
                if item_dict is not None
                else []
            )
            remove_pems.append(current_ca)
            strip_all_fleasion_ca = (
                bool(item_dict.get('strip_all_fleasion_ca')) if item_dict is not None else False
            )
            cleaned = _strip_requested_pem_blocks(
                existing,
                remove_pems,
                strip_all_fleasion_ca=strip_all_fleasion_ca,
            ).rstrip('\n')
            updated = f'{cleaned}\n{current_ca}' if cleaned else current_ca
            if updated == _normalize_newlines(existing):
                _normalize_cacert_permissions(ca_file)
                result['status'] = 'already_current'
                result['changed'] = False
                skipped.append(result)
                continue

            try:
                _atomic_write_text(ca_file, updated)
            except PermissionError:
                _prepare_cacert_target(resource_root, ca_file)
                _atomic_write_text(ca_file, updated)
            _normalize_cacert_permissions(ca_file)
            result['status'] = 'patched'
            result['changed'] = True
            patched.append(result)
        except Exception as exc:  # ruff: ignore[blind-except]
            result['status'] = 'failed'
            result['error'] = str(exc)
            failed.append(result)
            logger.warning('CA patch failed for %s: %s', raw_resource_dir, exc)

    ok = not failed and bool(patched or skipped)
    return {
        'ok': ok,
        'version': HELPER_VERSION,
        'capabilities': _json_list(HELPER_CAPABILITIES),
        'patched': _json_list(patched),
        'skipped': _json_list(skipped),
        'failed': _json_list(failed),
        'error': '' if ok else 'one or more Roblox CA patches failed',
    }


def _status() -> JsonObject:
    with _state_lock:
        active_hosts = sorted(_active_hosts)
        lease_remaining = (
            max(0.0, LEASE_SECONDS - (time.monotonic() - _last_heartbeat)) if active_hosts else 0.0
        )
    return {
        'ok': True,
        'version': HELPER_VERSION,
        'capabilities': _json_list(HELPER_CAPABILITIES),
        'active_hosts': _json_list(active_hosts),
        'backend_port': _backend_port,
        'lease_remaining': lease_remaining,
    }


def _probe_backend() -> JsonObject:
    started_at = time.monotonic()
    try:
        backend = socket.create_connection(('127.0.0.1', _backend_port), timeout=2.0)
    except OSError as exc:
        return {
            'ok': True,
            'reachable': False,
            'backend_port': _backend_port,
            'elapsed_ms': round((time.monotonic() - started_at) * 1000),
            'error_type': type(exc).__name__,
            'errno': exc.errno,
            'error': str(exc),
        }

    try:
        return {
            'ok': True,
            'reachable': True,
            'backend_port': _backend_port,
            'elapsed_ms': round((time.monotonic() - started_at) * 1000),
            'error_type': '',
            'errno': None,
            'error': '',
        }
    finally:
        backend.close()


def _handle_request(request: JsonValue) -> JsonObject:  # ruff: ignore[too-many-return-statements]
    request_object = _request_object(request)
    supplied = str(request_object.get('token') or '')
    if not hmac.compare_digest(supplied, _read_token()):
        return {'ok': False, 'error': 'unauthorized'}

    action = str(request_object.get('action') or '')
    if action == 'status':
        return _status()
    if action == 'apply':
        _set_hosts(_as_iterable(request_object.get('hosts') or []))
        return _status()
    if action == 'clear':
        _set_hosts([])
        return _status()
    if action == 'heartbeat':
        global _last_heartbeat  # ruff: ignore[global-statement]
        with _state_lock:
            if _active_hosts:
                _last_heartbeat = time.monotonic()
        return _status()
    if action == 'probe_backend':
        return _probe_backend()
    if action == 'patch_ca':
        return _patch_ca(
            str(request_object.get('ca_pem') or ''),
            request_object.get('installs') or [],
        )
    return {'ok': False, 'error': 'unsupported action'}


class _ControlHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            raw = self.rfile.readline(1024 * 1024)
            decoded: object = json.loads(raw.decode('utf-8'))
            request = _json_value(decoded)
            response: JsonObject = _handle_request(request)
        except Exception as exc:  # ruff: ignore[blind-except]
            logger.warning('control request failed: %s', exc)
            response = {'ok': False, 'error': str(exc)}
        self.wfile.write((json.dumps(response, separators=(',', ':')) + '\n').encode('utf-8'))


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            backend = socket.create_connection(('127.0.0.1', _backend_port), timeout=3.0)
        except OSError as exc:
            logger.warning(
                'relay backend connection failed for client %r: 127.0.0.1:%d: %s: errno=%r: %s',
                self.client_address,
                _backend_port,
                type(exc).__name__,
                exc.errno,
                exc,
            )
            return

        client = self.request
        client.settimeout(None)
        backend.settimeout(None)

        def pump(source: socket.socket, destination: socket.socket) -> None:
            try:
                while True:
                    chunk = source.recv(65536)
                    if not chunk:
                        break
                    destination.sendall(chunk)
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    destination.shutdown(socket.SHUT_WR)

        forward = threading.Thread(target=pump, args=(client, backend), daemon=True)
        forward.start()
        pump(backend, client)
        forward.join(timeout=2.0)
        backend.close()


class _ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _lease_monitor() -> None:
    while not _stop_event.wait(2.0):
        with _state_lock:
            expired = bool(_active_hosts) and time.monotonic() - _last_heartbeat > LEASE_SECONDS
        if expired:
            logger.warning('proxy heartbeat lease expired; clearing hosts entries')
            try:
                _set_hosts([])
            except Exception as exc:  # ruff: ignore[blind-except]
                logger.error('failed to clear hosts after lease expiry: %s', exc)  # ruff: ignore[error-instead-of-exception]


def main() -> None:
    global _token_file, _backend_port  # ruff: ignore[global-statement]

    parser = argparse.ArgumentParser()
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--backend-port', type=int, required=True)
    parser.add_argument('--control-port', type=int, required=True)
    parser.add_argument('--log-path', default='/Library/Logs/Fleasion.proxy-helper.log')
    args = parser.parse_args()

    if os.geteuid() != 0:
        msg = 'Fleasion proxy helper must run as root'
        raise SystemExit(msg)

    _token_file = args.token_file
    _backend_port = args.backend_port
    _configure_logging(args.log_path)

    control = None
    relay = None
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        logger.info(
            'helper starting: control 127.0.0.1:%d, relay 127.0.0.1:443 -> 127.0.0.1:%d',
            args.control_port,
            _backend_port,
        )
        _read_token()

        try:
            _set_hosts([])
        except Exception as exc:  # ruff: ignore[blind-except]
            logger.error('startup hosts cleanup failed: %s', exc)  # ruff: ignore[error-instead-of-exception]

        logger.info('binding helper control 127.0.0.1:%d', args.control_port)
        control = _ThreadingTCPServer(('127.0.0.1', args.control_port), _ControlHandler)
        logger.info('binding helper relay 127.0.0.1:443')
        relay = _ThreadingTCPServer(('127.0.0.1', 443), _RelayHandler)

        def stop_handler(_signum: int, _frame: FrameType | None) -> None:
            _stop_event.set()
            threading.Thread(target=control.shutdown, daemon=True).start()
            threading.Thread(target=relay.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop_handler)
        signal.signal(signal.SIGINT, stop_handler)

        threading.Thread(
            target=control.serve_forever, daemon=True, name='fleasion-helper-control'
        ).start()
        threading.Thread(target=_lease_monitor, daemon=True, name='fleasion-helper-lease').start()
        logger.info('helper ready: relay 127.0.0.1:443 -> 127.0.0.1:%d', _backend_port)
        relay.serve_forever()
    except Exception:
        logger.exception('helper startup failed')
        raise
    finally:
        _stop_event.set()
        try:
            _set_hosts([])
        except Exception as exc:  # ruff: ignore[blind-except]
            logger.error('shutdown hosts cleanup failed: %s', exc)  # ruff: ignore[error-instead-of-exception]
        if control is not None:
            control.server_close()
        if relay is not None:
            relay.server_close()


if __name__ == '__main__':
    main()
