"""One-shot privileged Linux helper for hosts entries and port 443 relay."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import pwd
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


HOSTS_FILE = Path('/etc/hosts')
HOSTS_MARKER = '# Fleasion proxy entry'
BUFFER_SIZE = 256 * 1024
SYSTEM_CA_NAME = 'fleasion-proxy-ca.crt'
SYSTEM_CA_DIRS = (
    Path('/usr/local/share/ca-certificates'),
    Path('/etc/pki/ca-trust/source/anchors'),
)
BOOT_GUARD_SERVICE = 'fleasion-hosts-restore.service'
BOOT_GUARD_PATH = Path('/etc/systemd/system') / BOOT_GUARD_SERVICE


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


def _log(message: str) -> None:
    print(message, flush=True)


def _parent_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _clean_hosts_content(content: str) -> str:
    return ''.join(
        line for line in content.splitlines(keepends=True)
        if HOSTS_MARKER not in line
    )


def _write_hosts(content: str) -> None:
    HOSTS_FILE.write_text(content, encoding='utf-8')


def _clear_hosts() -> None:
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return
    cleaned = _clean_hosts_content(existing)
    if cleaned != existing:
        _write_hosts(cleaned)


def _systemctl() -> str | None:
    systemctl = shutil.which('systemctl')
    if systemctl and BOOT_GUARD_PATH.parent.is_dir():
        return systemctl
    return None


def _boot_guard_command() -> str:
    hosts_file = shlex.quote(str(HOSTS_FILE))
    marker = shlex.quote(HOSTS_MARKER)
    service = shlex.quote(BOOT_GUARD_SERVICE)
    unit_path = shlex.quote(str(BOOT_GUARD_PATH))
    return (
        'tmp=$(mktemp) && '
        f"grep -vF -- {marker} {hosts_file} > \"$tmp\" || true; "
        f"cat \"$tmp\" > {hosts_file}; "
        'rm -f "$tmp"; '
        f'systemctl disable {service} >/dev/null 2>&1 || true; '
        f'rm -f {unit_path}; '
        'systemctl daemon-reload >/dev/null 2>&1 || true'
    )


def _install_boot_guard() -> bool:
    """Install a one-shot boot cleanup for power-loss/system-crash recovery."""
    systemctl = _systemctl()
    if not systemctl:
        _log('Linux hosts boot guard skipped: systemd/systemctl not available')
        return False

    unit = f"""[Unit]
Description=Restore /etc/hosts after an unclean Fleasion proxy shutdown
DefaultDependencies=no
After=local-fs.target
Before=network-pre.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c {shlex.quote(_boot_guard_command())}

[Install]
WantedBy=multi-user.target
"""
    try:
        BOOT_GUARD_PATH.write_text(unit, encoding='utf-8')
        BOOT_GUARD_PATH.chmod(0o644)
        for cmd in ([systemctl, 'daemon-reload'], [systemctl, 'enable', BOOT_GUARD_SERVICE]):
            result = _run_host_command(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or str(result.returncode)).strip()
                _log(f'Linux hosts boot guard command failed ({cmd[1]}): {err}')
                return False
    except Exception as exc:
        _log(f'Linux hosts boot guard install failed: {exc}')
        return False

    _log(f'Linux hosts boot guard installed: {BOOT_GUARD_PATH}')
    return True


def _remove_boot_guard() -> bool:
    systemctl = _systemctl()
    ok = True
    if systemctl:
        try:
            result = _run_host_command(
                [systemctl, 'disable', BOOT_GUARD_SERVICE],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )
            ok = ok and result.returncode == 0
        except Exception:
            ok = False
    try:
        BOOT_GUARD_PATH.unlink(missing_ok=True)
    except OSError:
        ok = False
    if systemctl:
        try:
            result = _run_host_command(
                [systemctl, 'daemon-reload'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )
            ok = ok and result.returncode == 0
        except Exception:
            ok = False
    if ok:
        _log('Linux hosts boot guard removed')
    else:
        _log('Linux hosts boot guard removal was incomplete')
    return ok


def _apply_hosts(hosts: set[str]) -> None:
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        existing = ''

    cleaned = _clean_hosts_content(existing).rstrip('\n')
    entries = '\n'.join(f'127.0.0.1 {host} {HOSTS_MARKER}' for host in sorted(hosts))
    new_content = f'{cleaned}\n{entries}\n' if cleaned else f'{entries}\n'
    _write_hosts(new_content)


def _flush_dns() -> None:
    for cmd in (
        ['resolvectl', 'flush-caches'],
        ['systemd-resolve', '--flush-caches'],
        ['service', 'nscd', 'restart'],
    ):
        try:
            result = _run_host_command(cmd, capture_output=True, timeout=5)
            if result.returncode == 0:
                _log(f'Flushed DNS with {cmd[0]}')
                return
        except Exception:
            pass
    _log('DNS flush skipped: no supported command succeeded')


def _run_trust_update(cmd: list[str]) -> tuple[bool, str]:
    try:
        result = _run_host_command(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=60,
        )
    except Exception as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, ''
    return False, (result.stderr or result.stdout or str(result.returncode)).strip()


def _copy_ca(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    target.chmod(0o644)


def _target_has_ca(source: Path, target: Path) -> bool:
    try:
        return target.read_bytes() == source.read_bytes()
    except OSError:
        return False


def _install_system_ca(ca_cert: Path) -> dict:
    """Install Fleasion's CA into common Linux system trust stores."""
    if not ca_cert.is_file():
        return {'ok': False, 'error': f'CA certificate not found: {ca_cert}'}

    stores: list[str] = []
    failures: list[dict] = []
    update_ca_certificates = shutil.which('update-ca-certificates')
    update_ca_trust = shutil.which('update-ca-trust')

    if update_ca_certificates and SYSTEM_CA_DIRS[0].is_dir():
        target = SYSTEM_CA_DIRS[0] / SYSTEM_CA_NAME
        try:
            if _target_has_ca(ca_cert, target):
                stores.append('update-ca-certificates:already-current')
                ok, err = True, ''
            else:
                _copy_ca(ca_cert, target)
                ok, err = _run_trust_update([update_ca_certificates])
            if ok:
                if not stores or stores[-1] != 'update-ca-certificates:already-current':
                    stores.append('update-ca-certificates')
            else:
                failures.append({'store': 'update-ca-certificates', 'error': err})
        except Exception as exc:
            failures.append({'store': 'update-ca-certificates', 'error': str(exc)})

    if update_ca_trust and SYSTEM_CA_DIRS[1].is_dir():
        target = SYSTEM_CA_DIRS[1] / SYSTEM_CA_NAME
        try:
            if _target_has_ca(ca_cert, target):
                stores.append('update-ca-trust:already-current')
                ok, err = True, ''
            else:
                _copy_ca(ca_cert, target)
                ok, err = _run_trust_update([update_ca_trust, 'extract'])
            if ok:
                if not stores or stores[-1] != 'update-ca-trust:already-current':
                    stores.append('update-ca-trust')
            else:
                failures.append({'store': 'update-ca-trust', 'error': err})
        except Exception as exc:
            failures.append({'store': 'update-ca-trust', 'error': str(exc)})

    if stores:
        return {'ok': True, 'stores': stores, 'failures': failures}
    if failures:
        return {'ok': False, 'failures': failures, 'error': failures[0].get('error')}
    return {'ok': False, 'error': 'no_supported_system_trust_store'}


def _repair_config_ownership(config_dir: Path, uid: int, gid: int) -> None:
    """Return stale root-owned Fleasion config files to the interactive user."""
    if uid <= 0 or gid < 0:
        return
    try:
        user_home = Path(pwd.getpwuid(uid).pw_dir).resolve()
        config_resolved = config_dir.resolve()
    except Exception as exc:
        _log(f'Skipped config ownership repair: {exc}')
        return

    if user_home == Path('/') or user_home not in (config_resolved, *config_resolved.parents):
        _log(f'Skipped config ownership repair outside user home: {config_resolved}')
        return

    repaired = 0
    paths = [config_resolved]
    try:
        paths.extend(config_resolved.rglob('*'))
    except OSError as exc:
        _log(f'Could not scan config ownership: {exc}')
        return

    for path in paths:
        try:
            stat_result = path.lstat()
            if stat_result.st_uid == uid and stat_result.st_gid == gid:
                continue
            os.chown(path, uid, gid, follow_symlinks=False)
            repaired += 1
        except OSError:
            pass
    if repaired:
        _log(f'Repaired ownership for {repaired} Fleasion config paths')


def _repair_sober_cert_ownership(uid: int, gid: int) -> None:
    """Return Sober cert bundle paths to the interactive user if needed."""
    if uid <= 0 or gid < 0:
        return
    try:
        user_home = Path(pwd.getpwuid(uid).pw_dir).resolve()
    except Exception as exc:
        _log(f'Skipped Sober cert ownership repair: {exc}')
        return

    sober_data = user_home / '.var' / 'app' / 'org.vinegarhq.Sober' / 'data' / 'sober'
    candidates = []
    for resource_dir in (sober_data / 'asset_overlay', sober_data / 'exe'):
        candidates.extend((resource_dir, resource_dir / 'ssl', resource_dir / 'ssl' / 'cacert.pem'))

    repaired = 0
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if user_home == Path('/') or user_home not in (resolved, *resolved.parents):
            continue
        try:
            stat_result = path.lstat()
            if stat_result.st_uid == uid and stat_result.st_gid == gid:
                continue
            os.chown(path, uid, gid, follow_symlinks=False)
            repaired += 1
        except OSError:
            pass
    if repaired:
        _log(f'Repaired ownership for {repaired} Sober cert paths')


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(BUFFER_SIZE):
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def _relay_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    backend_host: str,
    backend_port: int,
) -> None:
    try:
        backend_reader, backend_writer = await asyncio.open_connection(backend_host, backend_port)
    except Exception:
        client_writer.close()
        with contextlib.suppress(Exception):
            await client_writer.wait_closed()
        return

    await asyncio.gather(
        _pipe(client_reader, backend_writer),
        _pipe(backend_reader, client_writer),
    )


async def _serve(args: argparse.Namespace) -> int:
    hosts = {host.strip().lower() for host in args.hosts.split(',') if host.strip()}
    if not hosts:
        raise RuntimeError('no hosts supplied')

    stop_file = Path(args.stop_file)
    ready_file = Path(args.ready_file)
    ready_file.unlink(missing_ok=True)
    stop_file.unlink(missing_ok=True)

    server = await asyncio.start_server(
        lambda reader, writer: _relay_client(reader, writer, args.backend_host, args.backend_port),
        args.listen_host,
        args.listen_port,
    )
    sockets = ', '.join(str(sock.getsockname()) for sock in (server.sockets or ()))
    _log(f'Listening on {sockets}; relaying to {args.backend_host}:{args.backend_port}')

    _repair_config_ownership(Path(args.config_dir), args.owner_uid, args.owner_gid)
    _repair_sober_cert_ownership(args.owner_uid, args.owner_gid)
    if args.ca_cert:
        details = _install_system_ca(Path(args.ca_cert))
        if details.get('ok'):
            stores = ', '.join(details.get('stores') or [])
            _log(f'Installed CA into Linux system trust store{f" ({stores})" if stores else ""}')
        else:
            _log(f'Linux system trust-store install skipped/failed: {details.get("error") or details}')
    _clear_hosts()
    _install_boot_guard()
    _apply_hosts(hosts)
    _flush_dns()
    ready_file.write_text(json.dumps({'ok': True, 'pid': os.getpid()}), encoding='utf-8')
    with contextlib.suppress(OSError):
        os.chown(ready_file, args.owner_uid, args.owner_gid)
    _log(f'Applied {len(hosts)} hosts entries')

    try:
        while not stop_file.exists() and _parent_alive(args.parent_pid):
            await asyncio.sleep(0.5)
    finally:
        server.close()
        await server.wait_closed()
        _clear_hosts()
        _flush_dns()
        _remove_boot_guard()
        ready_file.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)
        _log('Cleaned hosts entries and stopped')

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description='Fleasion privileged Linux proxy helper')
    parser.add_argument('--install-system-ca', action='store_true')
    parser.add_argument('--ca-cert')
    parser.add_argument('--backend-host', default='127.0.0.1')
    parser.add_argument('--backend-port', type=int)
    parser.add_argument('--listen-host', default='127.0.0.1')
    parser.add_argument('--listen-port', type=int, default=443)
    parser.add_argument('--hosts')
    parser.add_argument('--stop-file')
    parser.add_argument('--ready-file')
    parser.add_argument('--config-dir')
    parser.add_argument('--owner-uid', type=int)
    parser.add_argument('--owner-gid', type=int)
    parser.add_argument('--parent-pid', type=int, default=0)
    args = parser.parse_args()

    if hasattr(os, 'geteuid') and os.geteuid() != 0:
        raise SystemExit('Fleasion Linux proxy helper must run as root')

    if args.install_system_ca:
        details = _install_system_ca(Path(args.ca_cert or ''))
        print(json.dumps(details), flush=True)
        raise SystemExit(0 if details.get('ok') else 1)

    required = (
        args.backend_port,
        args.hosts,
        args.stop_file,
        args.ready_file,
        args.config_dir,
        args.owner_uid,
        args.owner_gid,
    )
    if any(value is None for value in required):
        raise SystemExit('missing required proxy helper arguments')

    shutting_down = False

    def _handle_signal(_signum, _frame) -> None:
        nonlocal shutting_down
        shutting_down = True
        if args.stop_file:
            Path(args.stop_file).touch()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        raise SystemExit(asyncio.run(_serve(args)))
    except Exception as exc:
        if args.ready_file:
            ready_file = Path(args.ready_file)
            ready_file.write_text(
                json.dumps({'ok': False, 'error': str(exc)}),
                encoding='utf-8',
            )
            with contextlib.suppress(OSError):
                os.chown(ready_file, args.owner_uid, args.owner_gid)
        if not shutting_down:
            _log(f'Helper failed: {exc}')
        with contextlib.suppress(Exception):
            _clear_hosts()
            _flush_dns()
        raise SystemExit(1)


if __name__ == '__main__':
    main()
