"""Observe Roblox's own updater decisions without querying version endpoints."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from itertools import starmap
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_PLAYER_LOG_PATTERN = '*_Player_*_last.log'
_UPDATE_MARKERS = (
    b'Found new version and the updater launched',
    b'MacUpdateController::launchFinalizeUpdate',
)
_CURRENT_MARKER = b'Update check thread: updateRequired FALSE'
_UPDATING_MARKER = b'Update check thread: updateRequired TRUE'
_READ_LIMIT = 128 * 1024


@dataclass(slots=True)
class PlayerLogState:
    """Update decisions observed in one newly launched Roblox player."""

    path: Path
    created_at: float
    current: bool = False
    updating: bool = False
    updater_started: bool = False
    offset: int = 0
    tail: bytes = b''


class PlayerLogProbe:
    """Follow only player logs created after this probe was initialized."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path.home() / 'Library/Logs/Roblox'
        self._baseline = {path.name for path in self._logs()}
        self._observed: dict[str, PlayerLogState] = {}

    def _logs(self) -> Iterable[Path]:
        return (
            path
            for path in self.directory.glob(_PLAYER_LOG_PATTERN)
            if 'CrashHandler' not in path.name
        )

    def poll(self) -> list[PlayerLogState]:
        """Read only appended bytes, preserving matches across read boundaries."""
        for path in self._logs():
            if path.name in self._baseline:
                continue
            state = self._observed.get(path.name)
            if state is None:
                try:
                    info = path.stat()
                except OSError:
                    continue
                state = PlayerLogState(path=path, created_at=info.st_ctime)
                self._observed[path.name] = state
            try:
                with path.open('rb') as source:
                    source.seek(state.offset)
                    new_bytes = source.read(_READ_LIMIT)
                    state.offset = source.tell()
            except OSError:
                continue
            combined = state.tail + new_bytes
            state.current |= _CURRENT_MARKER in combined
            state.updating |= _UPDATING_MARKER in combined
            state.updater_started |= any(marker in combined for marker in _UPDATE_MARKERS)
            state.tail = combined[-128:]
        return sorted(
            self._observed.values(), key=lambda entry: (entry.created_at, entry.path.name)
        )


def _rosemary_installer_processes(runtime: Path, app: Path) -> dict[int, str]:
    """Locate only installers launched by the selected private Rosemary binary."""
    result = subprocess.run(
        ['/bin/ps', '-axo', 'pid=,command='],
        capture_output=True,
        text=True,
        check=False,
        timeout=4,
    )
    if result.returncode:
        msg = 'Could not inspect Rosemary installer processes'
        raise OSError(msg)
    expected = str(
        app / 'Contents/MacOS/RobloxPlayerInstaller.app/Contents/MacOS/RobloxPlayerInstaller'
    )
    guest_expected = '/Volumes/Host' + expected
    matches: dict[int, str] = {}
    for line in result.stdout.splitlines():
        pid_part, _, command = line.strip().partition(' ')
        try:
            pid = int(pid_part)
            parts = shlex.split(command.strip())
        except ValueError:
            continue
        if len(parts) < 3 or parts[0] != str(runtime) or parts[1] != 'run':
            continue
        if expected in parts or guest_expected in parts:
            matches[pid] = command.strip()
    return matches


def managed_installer_running(runtime: Path, app: Path) -> bool:
    """Identify an updater started by the managed guest, not another Roblox install."""
    return bool(_rosemary_installer_processes(runtime, app))


def _process_still_matches(pid: int, command: str) -> bool:
    """Refuse to signal a recycled PID or a different user's process."""
    try:
        current = subprocess.run(
            ['/bin/ps', '-p', str(pid), '-o', 'command='],
            capture_output=True,
            text=True,
            check=False,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = 'Could not verify the managed Roblox updater identity'
        raise OSError(msg) from exc
    return current.returncode == 0 and current.stdout.strip() == command


def terminate_managed_installers(runtime: Path, app: Path) -> None:
    """Stop all verified Rosemary updater helpers before native handoff."""
    _terminate_verified_installers(_rosemary_installer_processes(runtime, app))


def _terminate_verified_installers(candidates: dict[int, str]) -> None:
    """Stop verified Rosemary guest updaters, escalating only an unchanged PID."""
    if not candidates:
        return
    for pid, command in candidates.items():
        if _process_still_matches(pid, command):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not any(starmap(_process_still_matches, candidates.items())):
            return
        time.sleep(0.1)
    for pid, command in candidates.items():
        if _process_still_matches(pid, command):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not any(starmap(_process_still_matches, candidates.items())):
            return
        time.sleep(0.1)
    msg = 'Verified Rosemary updater did not exit after termination'
    raise OSError(msg)


class NativeInstallerLogProbe:
    """Observe completed official installer runs after the native handoff began."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path.home() / 'Library/Logs/Roblox'
        self._baseline = {path.name for path in self.directory.glob('RobloxPlayerInstaller_*.log')}
        self._positions: dict[str, int] = {}
        self._tails: dict[str, bytes] = {}
        self._completed = False

    def completed(self) -> bool:
        """Accept only a new installer log explicitly recording installation success."""
        for path in self.directory.glob('RobloxPlayerInstaller_*.log'):
            if path.name in self._baseline:
                continue
            try:
                with path.open('rb') as source:
                    source.seek(self._positions.get(path.name, 0))
                    data = source.read(_READ_LIMIT)
                    self._positions[path.name] = source.tell()
            except OSError:
                continue
            combined = self._tails.get(path.name, b'') + data
            self._tails[path.name] = combined[-128:]
            if b'Installation succeed!' in combined or b'Installer exit success' in combined:
                self._completed = True
        return self._completed
