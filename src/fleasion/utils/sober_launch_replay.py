"""Safely replay one newly launched Sober Flatpak after an Auto proxy switch.

Only the original Sober application arguments are retained in memory. A
one-time Roblox URI is never logged, serialized, or reconstructed from a
partially available launch command.
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
    SOBER_CLIENT,
    running_sober_launchers,
    sober_flatpak_session_identity,
)
from .logging import log_buffer

_MAX_ARGUMENTS = 32
_MAX_ARGUMENT_BYTES = 64 * 1024
_EXIT_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class SoberLaunchSnapshot:
    """One exact Flatpak scope, native launcher generation, and application argv."""

    scope: str
    launcher_pid: int
    launcher_start_ticks: int
    args: tuple[str, ...]
    cwd: Path


def capture_sober_launch(  # ruff: ignore[too-many-return-statements]
    *, proc_root: Path = Path('/proc')
) -> SoberLaunchSnapshot | None:
    """Capture the initial Sober invocation without reading sandbox secrets."""
    scope = sober_flatpak_session_identity(proc_root=proc_root)
    if scope is None:
        return None
    launchers = running_sober_launchers(proc_root=proc_root)
    if not launchers:
        return None
    observed: set[tuple[tuple[str, ...], Path]] = set()
    for launcher in launchers:
        process = proc_root / str(launcher.pid)
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            cgroup = (process / 'cgroup').read_text(encoding='utf-8', errors='replace')
            if scope not in cgroup.replace('\n', '/').split('/'):
                # Each launcher must be in the exact verified Flatpak scope
                return None
            raw = (process / 'cmdline').read_bytes()
            if not raw.endswith(b'\0') or len(raw) > _MAX_ARGUMENT_BYTES:
                return None
            argv = tuple(os.fsdecode(arg) for arg in raw.split(b'\0') if arg)
            if len(argv) < 2 or argv[1] != '--' or len(argv) > _MAX_ARGUMENTS + 2:
                return None
            cwd = (process / 'cwd').readlink()
            if not cwd.is_absolute() or not cwd.is_dir():
                return None
        except OSError, ValueError, UnicodeError, IndexError:
            return None
        observed.add((argv[2:], cwd))
    if len(observed) != 1:
        return None
    args, cwd = observed.pop()
    first = min(launchers, key=lambda session: session.start_ticks)
    return SoberLaunchSnapshot(scope, first.pid, first.start_ticks, args, cwd)


def _sober_instances(flatpak: str) -> tuple[str, ...] | None:
    """Read exact Flatpak instance IDs, returning None on any query error."""
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
        if len(fields := line.split('\t')) == 2 and fields[1] == SOBER_CLIENT.app_id
    )
    return instances if all(instance.isdecimal() for instance in instances) else None


def _exact_sober_instance(flatpak: str, expected_scope: str) -> str | None:
    """Require a single Flatpak instance matching the verified cgroup scope."""
    instances = _sober_instances(flatpak)
    if instances is None or len(instances) != 1:
        return None
    instance = instances[0]
    expected = f'{SOBER_CLIENT.cgroup_marker}-{instance}.scope'
    return instance if expected == expected_scope else None


def replay_sober_launch(  # ruff: ignore[too-many-return-statements]
    snapshot: SoberLaunchSnapshot, *, proc_root: Path = Path('/proc')
) -> bool:
    """Replace only the captured generation, after the Sober override is armed."""
    if sober_flatpak_session_identity(proc_root=proc_root) != snapshot.scope:
        return False
    # A native launcher can disappear during startup; never kill a new session
    current = running_sober_launchers(proc_root=proc_root)
    if not any(
        session.pid == snapshot.launcher_pid
        and session.start_ticks == snapshot.launcher_start_ticks
        for session in current
    ):
        return False
    flatpak = shutil.which('flatpak')
    if flatpak is None:
        return False
    instance = _exact_sober_instance(flatpak, snapshot.scope)
    if (
        instance is None
        or sober_flatpak_session_identity(proc_root=proc_root) != snapshot.scope
        or capture_sober_launch(proc_root=proc_root) != snapshot
    ):
        # The original command or scope may have changed during proxy restart
        return False
    try:
        result = platform_linux._standard_user_run(  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
            [flatpak, 'kill', instance],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError, subprocess.SubprocessError:
        return False
    if result.returncode != 0:
        return False
    deadline = time.monotonic() + _EXIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        # Flatpak's instance registry can disappear before the engine exits
        # Wait for both, and reject another instance starting during shutdown
        instances = _sober_instances(flatpak)
        if instances is None or any(value != instance for value in instances):
            return False
        current_scope = sober_flatpak_session_identity(proc_root=proc_root)
        if current_scope not in {None, snapshot.scope}:
            return False
        if not instances and current_scope is None:
            break
        time.sleep(0.05)
    else:
        log_buffer.log('Launcher', 'Sober Auto replay aborted: old Flatpak instance did not exit')
        return False
    try:
        platform_linux._standard_user_popen(  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
            [flatpak, 'run', SOBER_CLIENT.app_id, *snapshot.args], cwd=snapshot.cwd
        )
    except OSError, subprocess.SubprocessError:
        log_buffer.log('Launcher', 'Sober Auto replay failed to launch captured invocation')
        return False
    log_buffer.log('Launcher', 'Replayed the original Sober invocation with newly armed Env Proxy')
    return True
