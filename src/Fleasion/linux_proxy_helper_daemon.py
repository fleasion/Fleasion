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
PROXY_PORT = 443
BACKEND_PORT = 58443
CONFIG_DIR_NAME = 'Fleasion'
HELPER_READY_NAME = 'linux_proxy_helper.ready'
HELPER_STOP_NAME = 'linux_proxy_helper.stop'
HELPER_HOSTS_NAME = 'linux_proxy_helper.hosts.json'
PROXY_CA_RELATIVE = Path('proxy_ca') / 'ca.crt'
PROFILE_API_HOST = 'apis.roblox.com'
ALLOWED_PROXY_HOSTS = frozenset({
    'apis.roblox.com',
    'assetdelivery.roblox.com',
    'contentdelivery.roblox.com',
    'fts.rbxcdn.com',
    'gamejoin.roblox.com',
})
SYSTEM_CA_NAME = 'fleasion-proxy-ca.crt'
SYSTEM_CA_DIRS = (
    Path('/usr/local/share/ca-certificates'),
    Path('/etc/pki/ca-trust/source/anchors'),
)
BOOT_GUARD_SERVICE = 'fleasion-hosts-restore.service'
BOOT_GUARD_PATH = Path('/etc/systemd/system') / BOOT_GUARD_SERVICE
INSTALLED_HELPER_PATH = Path('/usr/local/libexec/fleasion-linux-proxy-helper')
INSTALLED_HELPER_SCRIPT_PATH = Path('/usr/local/libexec/fleasion-linux-proxy-helper.py')
POLKIT_ACTION_NAMESPACE = 'com.fleasion.proxy-helper'
POLKIT_RUN_ACTION_ID = f'{POLKIT_ACTION_NAMESPACE}.run'
POLKIT_INSTALL_CA_ACTION_ID = f'{POLKIT_ACTION_NAMESPACE}.install-system-ca'
POLKIT_POLICY_PATH = Path('/usr/share/polkit-1/actions') / f'{POLKIT_ACTION_NAMESPACE}.policy'
LEGACY_POLKIT_POLICY_PATH = Path('/usr/local/share/polkit-1/actions') / f'{POLKIT_ACTION_NAMESPACE}.policy'
POLKIT_PROMPTLESS_RULE_PATH = Path('/etc/polkit-1/rules.d/49-fleasion-proxy-helper.rules')


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


def _polkit_policy_xml() -> str:
    helper = str(INSTALLED_HELPER_PATH)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD polkit Policy Configuration 1.0//EN"
"http://www.freedesktop.org/software/polkit/policyconfig-1.dtd">
<policyconfig>
  <vendor>Fleasion</vendor>
  <vendor_url>https://github.com/fleasion/Fleasion</vendor_url>

  <action id="{POLKIT_RUN_ACTION_ID}">
    <description>Run the Fleasion Linux proxy helper</description>
    <message>Authentication is required to let Fleasion update Roblox proxy hosts and run its local port-443 relay.</message>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>yes</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">{helper}</annotate>
    <annotate key="org.freedesktop.policykit.exec.argv1">--backend-port</annotate>
  </action>

  <action id="{POLKIT_INSTALL_CA_ACTION_ID}">
    <description>Install the Fleasion proxy CA into Linux system trust</description>
    <message>Authentication is required to trust Fleasion's proxy CA for system WebView traffic.</message>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">{helper}</annotate>
    <annotate key="org.freedesktop.policykit.exec.argv1">--install-system-ca</annotate>
  </action>
</policyconfig>
'''


def _polkit_promptless_rule() -> str:
    return f'''polkit.addRule(function(action, subject) {{
    if (action.id == "{POLKIT_RUN_ACTION_ID}" &&
        subject.local && subject.active &&
        (subject.isInGroup("sudo") || subject.isInGroup("wheel"))) {{
        return polkit.Result.YES;
    }}
}});
'''


def _write_root_file(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    os.chown(path, 0, 0)
    path.chmod(mode)


def _copy_root_file(source: Path, target: Path, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    os.chown(target, 0, 0)
    target.chmod(mode)


def _install_privileged_helper(
    source_helper: str | None,
    *,
    enable_promptless: bool = False,
    source_helper_needs_dispatch_flag: bool = False,
) -> dict:
    source = Path(source_helper or '').resolve(strict=False)
    if not source.is_file() or source.is_symlink():
        return {'ok': False, 'error': f'helper source is not a real file: {source}'}

    try:
        if source_helper_needs_dispatch_flag:
            _copy_root_file(source, INSTALLED_HELPER_SCRIPT_PATH, 0o755)
            wrapper = (
                '#!/bin/sh\n'
                f'exec {shlex.quote(str(INSTALLED_HELPER_SCRIPT_PATH))} --linux-proxy-helper "$@"\n'
            )
            _write_root_file(INSTALLED_HELPER_PATH, wrapper, 0o755)
        elif source.suffix == '.py':
            python = shutil.which('python3') or '/usr/bin/python3'
            _copy_root_file(source, INSTALLED_HELPER_SCRIPT_PATH, 0o755)
            wrapper = f'#!/bin/sh\nexec {shlex.quote(python)} {shlex.quote(str(INSTALLED_HELPER_SCRIPT_PATH))} "$@"\n'
            _write_root_file(INSTALLED_HELPER_PATH, wrapper, 0o755)
        else:
            _copy_root_file(source, INSTALLED_HELPER_PATH, 0o755)

        policy_xml = _polkit_policy_xml()
        _write_root_file(POLKIT_POLICY_PATH, policy_xml, 0o644)
        _write_root_file(LEGACY_POLKIT_POLICY_PATH, policy_xml, 0o644)

        return {
            'ok': True,
            'helper': str(INSTALLED_HELPER_PATH),
            'policy': str(POLKIT_POLICY_PATH),
            'legacy_policy': str(LEGACY_POLKIT_POLICY_PATH),
            'promptless_rule': None,
        }
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}


def _linux_process_state_and_start_time(pid: int) -> tuple[str | None, str | None]:
    content = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8', errors='replace')
    try:
        _before, after_comm = content.rsplit(')', 1)
    except ValueError:
        return None, None
    fields = after_comm.strip().split()
    state = fields[0] if fields else None
    start_time = fields[19] if len(fields) > 19 else None
    return state, start_time


def _parent_alive(pid: int, expected_start_time: str | None = None) -> bool:
    if pid <= 0:
        return True
    if sys.platform.startswith('linux'):
        try:
            state, start_time = _linux_process_state_and_start_time(pid)
        except FileNotFoundError:
            return False
        except OSError:
            state, start_time = None, None
        if state == 'Z':
            return False
        if expected_start_time and start_time and start_time != str(expected_start_time):
            return False
        if state is not None:
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _path_is_within(path: Path, parent: Path) -> bool:
    return parent in (path, *path.parents)


def _reject_symlink(path: Path, label: str) -> None:
    try:
        if path.is_symlink():
            raise RuntimeError(f'{label} must not be a symlink: {path}')
    except OSError as exc:
        raise RuntimeError(f'could not inspect {label}: {exc}') from exc


def _pkexec_uid() -> int | None:
    raw_uid = os.environ.get('PKEXEC_UID')
    if not raw_uid:
        return None
    try:
        return int(raw_uid)
    except ValueError as exc:
        raise RuntimeError(f'invalid PKEXEC_UID: {raw_uid}') from exc


def _validate_user_context(args: argparse.Namespace) -> tuple[int, int, Path]:
    expected_uid = _pkexec_uid()
    owner_uid = int(args.owner_uid)
    if expected_uid is not None and owner_uid != expected_uid:
        raise RuntimeError('owner uid does not match invoking user')

    try:
        pw_entry = pwd.getpwuid(owner_uid)
    except KeyError as exc:
        raise RuntimeError(f'unknown owner uid: {owner_uid}') from exc

    owner_gid = int(args.owner_gid)
    if owner_gid != int(pw_entry.pw_gid):
        raise RuntimeError('owner gid does not match invoking user primary group')

    user_home = Path(pw_entry.pw_dir).resolve()
    if user_home == Path('/'):
        raise RuntimeError('refusing to use / as invoking user home')
    return owner_uid, owner_gid, user_home


def _validate_config_paths(args: argparse.Namespace, user_home: Path) -> Path:
    config_dir = Path(args.config_dir).resolve(strict=False)
    if config_dir.name != CONFIG_DIR_NAME or not _path_is_within(config_dir, user_home):
        raise RuntimeError(f'config dir must be the invoking user Fleasion config dir: {config_dir}')
    if config_dir.exists() and (not config_dir.is_dir() or config_dir.is_symlink()):
        raise RuntimeError(f'config dir must be a real directory: {config_dir}')

    expected_paths = {
        'ready file': config_dir / HELPER_READY_NAME,
        'stop file': config_dir / HELPER_STOP_NAME,
        'hosts update file': config_dir / HELPER_HOSTS_NAME,
    }
    provided_paths = {
        'ready file': Path(args.ready_file).resolve(strict=False),
        'stop file': Path(args.stop_file).resolve(strict=False),
        'hosts update file': Path(args.hosts_file).resolve(strict=False) if getattr(args, 'hosts_file', None) else None,
    }
    for label, expected in expected_paths.items():
        provided = provided_paths[label]
        if provided != expected:
            raise RuntimeError(f'{label} must be {expected}')
        _reject_symlink(expected, label)

    ca_cert = getattr(args, 'ca_cert', None)
    if ca_cert:
        ca_path = Path(ca_cert).resolve(strict=False)
        expected_ca = config_dir / PROXY_CA_RELATIVE
        if ca_path != expected_ca:
            raise RuntimeError(f'CA certificate must be {expected_ca}')
        _reject_symlink(ca_path, 'CA certificate')
    return config_dir


def _safe_write_user_file(path: Path, content: str, uid: int, gid: int, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, mode)
    try:
        data = content.encode('utf-8')
        os.write(fd, data)
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)


def _validate_runtime_args(args: argparse.Namespace) -> tuple[int, int]:
    if args.backend_host != '127.0.0.1':
        raise RuntimeError('backend host must be 127.0.0.1')
    if args.backend_port != BACKEND_PORT:
        raise RuntimeError(f'backend port must be {BACKEND_PORT}')
    if args.listen_host != '127.0.0.1':
        raise RuntimeError('listen host must be 127.0.0.1')
    if args.listen_port != PROXY_PORT:
        raise RuntimeError(f'listen port must be {PROXY_PORT}')

    owner_uid, owner_gid, user_home = _validate_user_context(args)
    _validate_config_paths(args, user_home)
    return owner_uid, owner_gid


def _validate_install_system_ca_args(ca_cert: str | None) -> Path:
    ca_path = Path(ca_cert or '').resolve(strict=False)
    invoking_uid = _pkexec_uid()
    if invoking_uid is None:
        _reject_symlink(ca_path, 'CA certificate')
        return ca_path

    try:
        user_home = Path(pwd.getpwuid(invoking_uid).pw_dir).resolve()
    except KeyError as exc:
        raise RuntimeError(f'unknown invoking uid: {invoking_uid}') from exc
    if (
        ca_path.name != PROXY_CA_RELATIVE.name
        or ca_path.parent.name != PROXY_CA_RELATIVE.parent.name
        or ca_path.parent.parent.name != CONFIG_DIR_NAME
        or not _path_is_within(ca_path, user_home)
    ):
        raise RuntimeError('CA certificate must be the invoking user Fleasion proxy CA')
    _reject_symlink(ca_path, 'CA certificate')
    return ca_path


def _validate_hosts(hosts: set[str]) -> set[str]:
    normalized = {str(host).strip().lower() for host in hosts if str(host).strip()}
    if not normalized:
        raise RuntimeError('no hosts supplied')
    invalid = sorted(normalized - ALLOWED_PROXY_HOSTS)
    if invalid:
        raise RuntimeError(f'unsupported hosts requested: {", ".join(invalid)}')
    return normalized


def _read_hosts_update(path: Path) -> set[str]:
    _reject_symlink(path, 'hosts update file')
    payload = json.loads(path.read_text(encoding='utf-8'))
    raw_hosts = payload.get('hosts') if isinstance(payload, dict) else payload
    if not isinstance(raw_hosts, list):
        raise RuntimeError('hosts update must contain a hosts list')
    return _validate_hosts({str(host) for host in raw_hosts})


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


def _system_ca_is_current(ca_cert: Path) -> bool:
    return any(
        target.is_file() and _target_has_ca(ca_cert, target)
        for target in (directory / SYSTEM_CA_NAME for directory in SYSTEM_CA_DIRS if directory.is_dir())
    )


def _ensure_system_ca_for_hosts(hosts: set[str], ca_cert: str | None, *, install: bool = False) -> dict | None:
    """Install/verify system trust when WebKit-visible API hosts are requested."""
    if PROFILE_API_HOST not in hosts:
        return None
    if not ca_cert:
        return {'ok': False, 'error': 'missing_ca_cert_for_profile_api'}
    ca_path = Path(ca_cert)
    if install:
        return _install_system_ca(ca_path)
    if _system_ca_is_current(ca_path):
        return {'ok': True, 'stores': ['system-ca:already-current']}
    return {'ok': False, 'error': 'system_ca_not_installed'}


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
    owner_uid, owner_gid = _validate_runtime_args(args)
    hosts = _validate_hosts({host.strip().lower() for host in args.hosts.split(',') if host.strip()})

    stop_file = Path(args.stop_file)
    ready_file = Path(args.ready_file)
    ready_file.unlink(missing_ok=True)
    stop_file.unlink(missing_ok=True)

    _repair_config_ownership(Path(args.config_dir), owner_uid, owner_gid)
    _repair_sober_cert_ownership(owner_uid, owner_gid)
    system_ca_details = _ensure_system_ca_for_hosts(hosts, args.ca_cert, install=False)
    if system_ca_details is not None:
        details = system_ca_details
        if details.get('ok'):
            stores = ', '.join(details.get('stores') or [])
            _log(f'Linux system trust store ready{f" ({stores})" if stores else ""}')
        else:
            error = details.get('error') or details
            _log(f'Linux system trust-store install skipped/failed: {error}')
            if args.require_system_ca:
                raise RuntimeError(f'Linux system trust-store install failed: {error}')
    elif args.require_system_ca:
        raise RuntimeError('Linux system trust-store install failed: missing CA certificate')

    server = await asyncio.start_server(
        lambda reader, writer: _relay_client(reader, writer, args.backend_host, args.backend_port),
        args.listen_host,
        args.listen_port,
    )
    sockets = ', '.join(str(sock.getsockname()) for sock in (server.sockets or ()))
    _log(f'Listening on {sockets}; relaying to {args.backend_host}:{args.backend_port}')

    _clear_hosts()
    _install_boot_guard()
    _apply_hosts(hosts)
    _flush_dns()
    ready_payload = {'ok': True, 'pid': os.getpid()}
    if system_ca_details is not None:
        ready_payload['system_ca'] = system_ca_details
    _safe_write_user_file(ready_file, json.dumps(ready_payload), owner_uid, owner_gid)
    _log(f'Applied {len(hosts)} hosts entries')

    current_hosts = set(hosts)
    hosts_file = Path(args.hosts_file) if args.hosts_file else None
    hosts_file_mtime_ns: int | None = None

    try:
        shutdown_requested = getattr(args, 'shutdown_requested', lambda: False)
        while (
            not shutdown_requested()
            and not stop_file.exists()
            and _parent_alive(args.parent_pid, getattr(args, 'parent_start_time', None))
        ):
            if hosts_file is not None:
                try:
                    stat_result = hosts_file.stat()
                    if stat_result.st_mtime_ns != hosts_file_mtime_ns:
                        hosts_file_mtime_ns = stat_result.st_mtime_ns
                        updated_hosts = _read_hosts_update(hosts_file)
                        if updated_hosts != current_hosts:
                            update_ca_details = _ensure_system_ca_for_hosts(
                                updated_hosts,
                                args.ca_cert,
                                install=False,
                            )
                            if update_ca_details is not None and not update_ca_details.get('ok'):
                                _log(
                                    'Skipped Linux hosts update because system trust-store '
                                    f'install failed: {update_ca_details.get("error") or update_ca_details}'
                                )
                            else:
                                _clear_hosts()
                                _apply_hosts(updated_hosts)
                                _flush_dns()
                                current_hosts = set(updated_hosts)
                                _log(f'Applied live hosts update: {", ".join(sorted(current_hosts))}')
                except FileNotFoundError:
                    pass
                except Exception as exc:
                    _log(f'Ignored invalid Linux helper hosts update: {exc}')
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
    parser.add_argument('--install-privileged-helper', action='store_true')
    parser.add_argument('--source-helper')
    parser.add_argument('--source-helper-needs-dispatch-flag', action='store_true')
    parser.add_argument('--enable-promptless', action='store_true')
    parser.add_argument('--require-system-ca', action='store_true')
    parser.add_argument('--ca-cert')
    parser.add_argument('--backend-host', default='127.0.0.1')
    parser.add_argument('--backend-port', type=int)
    parser.add_argument('--listen-host', default='127.0.0.1')
    parser.add_argument('--listen-port', type=int, default=443)
    parser.add_argument('--hosts')
    parser.add_argument('--stop-file')
    parser.add_argument('--ready-file')
    parser.add_argument('--hosts-file')
    parser.add_argument('--config-dir')
    parser.add_argument('--owner-uid', type=int)
    parser.add_argument('--owner-gid', type=int)
    parser.add_argument('--parent-pid', type=int, default=0)
    parser.add_argument('--parent-start-time')
    args = parser.parse_args()

    if hasattr(os, 'geteuid') and os.geteuid() != 0:
        raise SystemExit('Fleasion Linux proxy helper must run as root')

    if args.install_privileged_helper:
        details = _install_privileged_helper(
            args.source_helper,
            enable_promptless=args.enable_promptless,
            source_helper_needs_dispatch_flag=args.source_helper_needs_dispatch_flag,
        )
        print(json.dumps(details), flush=True)
        raise SystemExit(0 if details.get('ok') else 1)

    if args.install_system_ca:
        try:
            ca_cert = _validate_install_system_ca_args(args.ca_cert)
        except Exception as exc:
            print(json.dumps({'ok': False, 'error': str(exc)}), flush=True)
            raise SystemExit(1)
        details = _install_system_ca(ca_cert)
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

    try:
        owner_uid, owner_gid = _validate_runtime_args(args)
    except Exception as exc:
        raise SystemExit(str(exc))

    shutting_down = False

    def _handle_signal(_signum, _frame) -> None:
        nonlocal shutting_down
        shutting_down = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    args.shutdown_requested = lambda: shutting_down

    try:
        raise SystemExit(asyncio.run(_serve(args)))
    except Exception as exc:
        if args.ready_file:
            ready_file = Path(args.ready_file)
            with contextlib.suppress(OSError):
                _safe_write_user_file(
                    ready_file,
                    json.dumps({'ok': False, 'error': str(exc)}),
                    owner_uid,
                    owner_gid,
                )
        if not shutting_down:
            _log(f'Helper failed: {exc}')
        with contextlib.suppress(Exception):
            _clear_hosts()
            _flush_dns()
        raise SystemExit(1)


if __name__ == '__main__':
    main()
