"""Replay verified Nightcap and Cordial Flatpak instances after Auto handoff.

Each independent Flatpak profile is replaced by its own exact instance ID.
Ambiguous launchers sharing one instance are left untouched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import platform_linux
from .linux_clients import (
    CORDIAL_CLIENT,
    LinuxClientInstallation,
    MocktailSession,
    running_community_launchers,
    running_cordial_players,
    running_mocktail_players,
)
from .logging import log_buffer

_MAX_ARGUMENT_BYTES = 64 * 1024
_MAX_ARGS = 64
_EXIT_TIMEOUT = 4.0


@dataclass(frozen=True, slots=True)
class CommunityFlatpakSnapshot:
    """Exact Flatpak scope, launcher generation, and application arguments."""

    key: str
    app_id: str
    scope: str
    launcher_pid: int
    launcher_start_ticks: int
    args: tuple[str, ...]
    cwd: Path
    already_proxied: bool


def _sessions(
    installation: LinuxClientInstallation, *, proc_root: Path
) -> tuple[MocktailSession, ...]:
    if installation.key == 'cordial':
        return running_community_launchers(
            'cordial-shell', proc_root=proc_root, flatpak_id=installation.app_id
        )
    launchers = running_community_launchers(
        'mocktail', proc_root=proc_root, flatpak_id=installation.app_id
    )
    engines = running_mocktail_players(proc_root=proc_root, flatpak_id=installation.app_id)
    return tuple({(s.pid, s.start_ticks): s for s in (*launchers, *engines)}.values())


def _scope(process: Path, app_id: str) -> str | None:
    try:
        data = (process / 'cgroup').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    prefix = f'app-flatpak-{app_id}-'
    scopes = {
        component
        for line in data.splitlines()
        for component in line.split('/')
        if component.startswith(prefix) and component.endswith('.scope')
    }
    return next(iter(scopes)) if len(scopes) == 1 else None


def flatpak_community_scopes(
    installation: LinuxClientInstallation, *, proc_root: Path = Path('/proc')
) -> frozenset[str]:
    """Return verified scopes, also used to exclude preexisting instances."""
    if installation.client.runtime != 'flatpak' or installation.key not in {'nightcap', 'cordial'}:
        return frozenset()
    sessions = _sessions(installation, proc_root=proc_root)
    if installation.key == 'cordial':
        # The detached game process can survive its GTK shell
        sessions = (
            *sessions,
            *running_cordial_players(proc_root=proc_root, flatpak_id=installation.app_id),
        )
    return frozenset(
        scope
        for session in sessions
        if (scope := _scope(proc_root / str(session.pid), installation.app_id)) is not None
    )


def _capture_session(  # ruff: ignore[too-many-return-statements]
    installation: LinuxClientInstallation,
    session: MocktailSession,
    *,
    proxy_url: str,
    proc_root: Path,
) -> CommunityFlatpakSnapshot | None:
    """Capture one application process without logging its one-use URI."""
    proc = proc_root / str(session.pid)
    binary = 'cordial-shell' if installation.key == CORDIAL_CLIENT.key else 'mocktail'
    scope = _scope(proc, installation.app_id)
    if scope is None:
        return None
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if proc.stat().st_uid != os.getuid() or (proc / 'exe').readlink().name != binary:
            return None
        raw = (proc / 'cmdline').read_bytes()
        if not raw.endswith(b'\0') or not 0 < len(raw) <= _MAX_ARGUMENT_BYTES:
            return None
        argv = tuple(os.fsdecode(part) for part in raw.split(b'\0') if part)
        if not argv or len(argv) > _MAX_ARGS or Path(argv[0]).name != binary:
            return None
        cwd = (proc / 'cwd').readlink()
        if not cwd.is_absolute() or not cwd.is_dir():
            return None
        environment = (proc / 'environ').read_bytes()
        if len(environment) > 512 * 1024 or not environment.endswith(b'\0'):
            return None
        env = dict(
            os.fsdecode(pair).split('=', 1)
            for pair in environment.split(b'\0')
            if pair and b'=' in pair
        )
    except OSError, ValueError, UnicodeError:
        return None
    proxied = any(
        env.get(name) == proxy_url
        for name in ('HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy')
    )
    return CommunityFlatpakSnapshot(
        installation.key,
        installation.app_id,
        scope,
        session.pid,
        session.start_ticks,
        argv[1:],
        cwd,
        proxied,
    )


def capture_community_flatpak_launches(
    installation: LinuxClientInstallation,
    *,
    proxy_url: str,
    proc_root: Path = Path('/proc'),
) -> tuple[CommunityFlatpakSnapshot, ...]:
    """Capture distinct profile instances, rejecting any ambiguous shared scope."""
    if installation.client.runtime != 'flatpak' or installation.key not in {'nightcap', 'cordial'}:
        return ()
    sessions = _sessions(installation, proc_root=proc_root)
    if not sessions:
        return ()
    snapshots: list[CommunityFlatpakSnapshot] = []
    seen_scopes: set[str] = set()
    for session in sessions:
        snapshot = _capture_session(installation, session, proxy_url=proxy_url, proc_root=proc_root)
        if snapshot is None or snapshot.scope in seen_scopes:
            return ()
        seen_scopes.add(snapshot.scope)
        snapshots.append(snapshot)
    return tuple(sorted(snapshots, key=lambda item: (item.scope, item.launcher_pid)))


def capture_community_flatpak_launch(
    installation: LinuxClientInstallation,
    *,
    proxy_url: str,
    proc_root: Path = Path('/proc'),
) -> CommunityFlatpakSnapshot | None:
    """Compatibility helper for a single Flatpak instance."""
    snapshots = capture_community_flatpak_launches(
        installation, proxy_url=proxy_url, proc_root=proc_root
    )
    return snapshots[0] if len(snapshots) == 1 else None


def _instances(flatpak: str, app_id: str) -> tuple[str, ...] | None:
    """Return all running instance IDs for this application, or fail closed."""
    try:
        result = platform_linux._standard_user_run(  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
            [flatpak, 'ps', '--columns=instance,application'],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return None
    instances = tuple(
        fields[0]
        for line in result.stdout.splitlines()
        if len(fields := line.split('\t')) == 2 and fields[1] == app_id
    )
    return instances if all(instance.isdecimal() for instance in instances) else None


def replay_community_flatpak_launch(  # ruff: ignore[too-many-return-statements]
    installation: LinuxClientInstallation,
    snapshot: CommunityFlatpakSnapshot,
    *,
    proxy_url: str,
    proc_root: Path = Path('/proc'),
) -> bool:
    """Replace the captured Flatpak instance only after its override is armed."""
    if (
        installation.client.runtime != 'flatpak'
        or installation.key != snapshot.key
        or installation.app_id != snapshot.app_id
        or snapshot.already_proxied
        or not proxy_url.startswith('http://127.0.0.1:')
    ):
        return False
    flatpak = shutil.which('flatpak')
    if flatpak is None:
        return False
    instances = _instances(flatpak, snapshot.app_id)
    prefix = f'app-flatpak-{snapshot.app_id}-'
    if not snapshot.scope.startswith(prefix) or not snapshot.scope.endswith('.scope'):
        return False
    instance = snapshot.scope[len(prefix) : -len('.scope')]
    if instances is None or not instance.isdecimal() or instance not in instances:
        return False
    current = capture_community_flatpak_launches(
        installation, proxy_url=proxy_url, proc_root=proc_root
    )
    if snapshot not in current:
        return False
    original_scopes = flatpak_community_scopes(installation, proc_root=proc_root)
    if snapshot.scope not in original_scopes:
        return False
    peer_instances = set(instances) - {instance}
    peer_scopes = original_scopes - {snapshot.scope}
    try:
        result = platform_linux._standard_user_run(  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
            [flatpak, 'kill', instance], capture_output=True, text=True, timeout=5
        )
    except OSError, subprocess.SubprocessError:
        return False
    if result.returncode != 0:
        return False
    deadline = time.monotonic() + _EXIT_TIMEOUT
    while time.monotonic() < deadline:
        instances = _instances(flatpak, snapshot.app_id)
        if instances is None or set(instances) - {instance} != peer_instances:
            return False
        scopes = flatpak_community_scopes(installation, proc_root=proc_root)
        if scopes - {snapshot.scope} != peer_scopes:
            return False
        if instance not in instances and snapshot.scope not in scopes:
            break
        time.sleep(0.05)
    else:
        log_buffer.log(
            'Launcher', f'{installation.display_name} replay aborted: instance did not exit'
        )
        return False
    try:
        platform_linux._standard_user_popen(  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
            [flatpak, 'run', snapshot.app_id, *snapshot.args], cwd=snapshot.cwd
        )
    except OSError, subprocess.SubprocessError:
        log_buffer.log('Launcher', f'{installation.display_name} replay failed to launch')
        return False
    log_buffer.log(
        'Launcher', f'Replayed {installation.display_name} with the newly armed Env Proxy'
    )
    return True
