"""One-shot privileged Linux helper for hosts entries and port 443 relay."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import pwd
import signal
import subprocess
import sys
import time
from pathlib import Path


HOSTS_FILE = Path('/etc/hosts')
HOSTS_MARKER = '# Fleasion proxy entry'
BUFFER_SIZE = 256 * 1024


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
            result = subprocess.run(cmd, capture_output=True, timeout=5)
            if result.returncode == 0:
                _log(f'Flushed DNS with {cmd[0]}')
                return
        except Exception:
            pass
    _log('DNS flush skipped: no supported command succeeded')


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
    _clear_hosts()
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
        ready_file.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)
        _log('Cleaned hosts entries and stopped')

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description='Fleasion privileged Linux proxy helper')
    parser.add_argument('--backend-host', default='127.0.0.1')
    parser.add_argument('--backend-port', type=int, required=True)
    parser.add_argument('--listen-host', default='127.0.0.1')
    parser.add_argument('--listen-port', type=int, default=443)
    parser.add_argument('--hosts', required=True)
    parser.add_argument('--stop-file', required=True)
    parser.add_argument('--ready-file', required=True)
    parser.add_argument('--config-dir', required=True)
    parser.add_argument('--owner-uid', type=int, required=True)
    parser.add_argument('--owner-gid', type=int, required=True)
    parser.add_argument('--parent-pid', type=int, default=0)
    args = parser.parse_args()

    if hasattr(os, 'geteuid') and os.geteuid() != 0:
        raise SystemExit('Fleasion Linux proxy helper must run as root')

    shutting_down = False

    def _handle_signal(_signum, _frame) -> None:
        nonlocal shutting_down
        shutting_down = True
        Path(args.stop_file).touch()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        raise SystemExit(asyncio.run(_serve(args)))
    except Exception as exc:
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
