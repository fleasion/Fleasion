"""Conservative Bloxstrap Rich Presence handoff after a Windows Player replacement."""

from __future__ import annotations

import base64
import binascii
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from threading import Event

_WATCHER_ARG = re.compile(
    r'(?:^|\s)-watcher\s+(?:"([A-Za-z0-9+/=]+)"|([A-Za-z0-9+/=]+))', re.IGNORECASE
)
_HANDOFF_TIMEOUT_SECONDS = 19.0
_HANDOFF_POLL_SECONDS = 0.15


@dataclass(frozen=True)
class BloxstrapProcess:
    """A Bloxstrap process observed in the current Windows session."""

    pid: int
    executable: Path
    command_line: str


@dataclass(frozen=True)
class WatcherHandoff:
    """Verified, single-session Bloxstrap watcher replacement context."""

    old_player_pid: int
    bootstrap_pid: int | None
    executable: Path
    log_dir: Path
    old_log_names: frozenset[str]


def _watched_pid(command_line: str) -> tuple[int, bool] | None:
    """Parse Bloxstrap's ``-watcher`` payload, including auto-close metadata."""
    match = _WATCHER_ARG.search(command_line)
    if match is None:
        return None
    encoded = match.group(1) or match.group(2)
    if len(encoded) > 4096:
        return None
    try:
        raw_data: object = json.loads(base64.b64decode(encoded, validate=True))
    except binascii.Error, UnicodeDecodeError, ValueError:
        return None
    if not isinstance(raw_data, dict):
        return None
    data = cast('dict[str, object]', raw_data)
    pid = data.get('ProcessId')
    autoclose = data.get('AutoclosePids')
    if (
        type(pid) is not int
        or pid <= 0
        or (autoclose is not None and not isinstance(autoclose, list))
    ):
        return None
    return pid, bool(cast('list[object]', autoclose)) if autoclose is not None else False


def _is_watcher(process: BloxstrapProcess) -> bool:
    return bool(re.search(r'(?:^|\s)-watcher(?:\s|$)', process.command_line, re.IGNORECASE))


def _same_executable(left: Path, right: Path) -> bool:
    return str(left).casefold() == str(right).casefold()


def _rich_presence_enabled(executable: Path) -> bool:  # ruff: ignore[too-many-return-statements]
    """Fail closed for unknown settings and auto-close integration lifecycle."""
    if executable.name.casefold() != 'bloxstrap.exe' or not executable.is_file():
        return False
    try:
        raw_settings: object = json.loads(
            (executable.parent / 'Settings.json').read_text(encoding='utf-8-sig')
        )
    except OSError, UnicodeError, ValueError:
        return False
    if not isinstance(raw_settings, dict):
        return False
    settings = cast('dict[str, object]', raw_settings)
    # Bloxstrap's persisted Settings defaults both of these properties to true
    for key in ('EnableActivityTracking', 'UseDiscordRichPresence'):
        if settings.get(key, True) is not True:
            return False
    integrations = settings.get('CustomIntegrations', [])
    if not isinstance(integrations, list):
        return False
    for entry in cast('list[object]', integrations):
        if (
            not isinstance(entry, dict)
            or cast('dict[str, object]', entry).get('AutoClose', True) is not False
        ):
            # The original watcher owns these integration PIDs. Do not copy
            # unknown cleanup responsibilities to a new watcher
            return False
    return True


def _player_log_names(log_dir: Path) -> frozenset[str]:
    try:
        return frozenset(
            path.name
            for path in log_dir.iterdir()
            if path.is_file()
            and path.suffix.casefold() == '.log'
            and 'player' in path.name.casefold()
        )
    except OSError:
        return frozenset()


def capture_handoff(  # ruff: ignore[too-many-return-statements]
    old_player_pid: int,
    *,
    parent_pid: int | None,
    processes: list[BloxstrapProcess],
    log_dir: Path,
) -> WatcherHandoff | None:
    """Qualify only a Bloxstrap-launched Player or its existing watcher."""
    if old_player_pid <= 0 or not log_dir.is_dir():
        return None

    parent = next(
        (
            process
            for process in processes
            if process.pid == parent_pid and not _is_watcher(process)
        ),
        None,
    )
    old_watchers: list[BloxstrapProcess] = []
    for process in processes:
        if not _is_watcher(process):
            continue
        watcher = _watched_pid(process.command_line)
        if watcher is None or watcher[0] != old_player_pid:
            # An unknown or unrelated watcher owns Bloxstrap's global mutex
            return None
        if watcher[1]:
            return None
        old_watchers.append(process)

    source = parent or next(iter(old_watchers), None)
    if source is None or not _rich_presence_enabled(source.executable):
        return None
    if '-testmode' in source.command_line.casefold() or any(
        '-testmode' in process.command_line.casefold() for process in old_watchers
    ):
        return None
    if any(not _same_executable(source.executable, item.executable) for item in old_watchers):
        return None
    return WatcherHandoff(
        old_player_pid=old_player_pid,
        bootstrap_pid=parent.pid if parent is not None else None,
        executable=source.executable,
        log_dir=log_dir,
        old_log_names=_player_log_names(log_dir),
    )


def _new_player_log(plan: WatcherHandoff) -> Path | None:
    try:
        candidates = [
            path
            for path in plan.log_dir.iterdir()
            if path.is_file()
            and path.name not in plan.old_log_names
            and path.suffix.casefold() == '.log'
            and 'player' in path.name.casefold()
        ]
        return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None
    except OSError:
        return None


def _watcher_args(replacement_pid: int, log_path: Path) -> list[str]:
    payload: dict[str, object] = {
        'ProcessId': replacement_pid,
        'LogFile': str(log_path),
        'AutoclosePids': [],
    }
    encoded = base64.b64encode(json.dumps(payload, separators=(',', ':')).encode('utf-8')).decode(
        'ascii'
    )
    return ['-watcher', encoded]


def run_handoff(  # ruff: ignore[too-many-return-statements]
    plan: WatcherHandoff,
    replacement_pid: int,
    *,
    query_processes: Callable[[], list[BloxstrapProcess]],
    player_running: Callable[[int], bool],
    spawn: Callable[[Path, list[str]], None],
    cancelled: Callable[[], bool],
    report: Callable[[str], None],
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Allow Bloxstrap to complete its own launch before restoring Rich Presence."""
    deadline = monotonic() + _HANDOFF_TIMEOUT_SECONDS
    while monotonic() < deadline:
        if cancelled() or not player_running(replacement_pid):
            return False
        try:
            processes = query_processes()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            report(
                f'Bloxstrap Rich Presence handoff skipped: process query failed ({type(exc).__name__})'
            )
            return False
        if any(
            (watched := _watched_pid(process.command_line)) is not None
            and watched[0] == replacement_pid
            for process in processes
        ):
            report(f'Bloxstrap watcher already tracks replacement Player PID {replacement_pid}')
            return True
        if plan.bootstrap_pid is not None and any(
            p.pid == plan.bootstrap_pid and _same_executable(p.executable, plan.executable)
            for p in processes
        ):
            sleep(_HANDOFF_POLL_SECONDS)
            continue
        if processes:
            # Every Bloxstrap installation shares the Bloxstrap-Watcher mutex
            # An active watcher or unclassified launcher must finish on its own
            sleep(_HANDOFF_POLL_SECONDS)
            continue
        log_path = _new_player_log(plan)
        if log_path is None:
            sleep(_HANDOFF_POLL_SECONDS)
            continue
        if cancelled() or not player_running(replacement_pid):
            return False
        if not _rich_presence_enabled(plan.executable):
            report('Bloxstrap Rich Presence handoff skipped: settings changed during relaunch')
            return False
        try:
            spawn(plan.executable, _watcher_args(replacement_pid, log_path))
        except (OSError, RuntimeError, ValueError) as exc:
            report(f'Bloxstrap Rich Presence handoff failed: {type(exc).__name__}')
            return False
        report(
            f'Bloxstrap Rich Presence handoff started watcher for replacement Player PID {replacement_pid}'
        )
        return True
    report(
        'Bloxstrap Rich Presence handoff skipped: launcher, watcher, or Player log was not ready'
    )
    return False


def schedule_handoff(
    plan: WatcherHandoff,
    replacement_pid: int,
    *,
    query_processes: Callable[[], list[BloxstrapProcess]],
    player_running: Callable[[int], bool],
    spawn: Callable[[Path, list[str]], None],
    cancel_event: Event | None,
    report: Callable[[str], None],
) -> None:
    """Run only after the replacement Player successfully starts."""

    def _worker() -> None:
        try:
            run_handoff(
                plan,
                replacement_pid,
                query_processes=query_processes,
                player_running=player_running,
                spawn=spawn,
                cancelled=cancel_event.is_set if cancel_event is not None else lambda: False,
                report=report,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            report(f'Bloxstrap Rich Presence handoff stopped: {type(exc).__name__}')

    threading.Thread(
        target=_worker,
        name='FleasionBloxstrapWatcherHandoff',
        daemon=True,
    ).start()
