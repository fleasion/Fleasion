"""Bloxstrap-specific Rich Presence handoff boundary and lifecycle tests."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Never

import pytest

from fleasion.utils.bloxstrap_watcher import (
    BloxstrapProcess,
    WatcherHandoff,
    _watched_pid,  # pyright: ignore[reportPrivateUsage]
    _watcher_args,  # pyright: ignore[reportPrivateUsage]
    capture_handoff,
    run_handoff,
)

def _unexpected_spawn(_path: Path, _args: list[str]) -> Never:
    pytest.fail('Watcher should not spawn')


def _installation(tmp_path: Path, settings: dict[str, object] | None = None) -> tuple[Path, Path]:
    root = tmp_path / 'Bloxstrap'
    root.mkdir()
    executable = root / 'Bloxstrap.exe'
    executable.write_bytes(b'executable')
    (root / 'Settings.json').write_text(
        json.dumps(
            {'EnableActivityTracking': True, 'UseDiscordRichPresence': True}
            if settings is None
            else settings
        ),
        encoding='utf-8',
    )
    logs = tmp_path / 'Roblox' / 'logs'
    logs.mkdir(parents=True)
    (logs / 'old_Player_last.log').write_text('previous launch', encoding='utf-8')
    return executable, logs


def _watcher(executable: Path, watched_pid: int, *, autoclose: list[int] | None = None) -> BloxstrapProcess:
    payload = {'ProcessId': watched_pid, 'LogFile': 'old_Player_last.log', 'AutoclosePids': autoclose or []}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    return BloxstrapProcess(200, executable, f'"{executable}" -watcher "{encoded}"')


def _plan(
    executable: Path,
    log_dir: Path,
    *,
    parent_pid: int | None = 100,
    processes: list[BloxstrapProcess] | None = None,
) -> WatcherHandoff | None:
    return capture_handoff(
        300,
        parent_pid=parent_pid,
        processes=processes if processes is not None else [BloxstrapProcess(100, executable, '-player')],
        log_dir=log_dir,
    )


def test_parent_launch_with_enabled_presence_qualifies(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    plan = _plan(executable, log_dir)
    assert plan is not None
    assert plan.old_player_pid == 300
    assert plan.bootstrap_pid == 100
    assert plan.old_log_names == {'old_Player_last.log'}


def test_existing_watcher_qualifies_when_parent_has_exited(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    plan = _plan(executable, log_dir, parent_pid=None, processes=[_watcher(executable, 300)])
    assert plan is not None
    assert plan.bootstrap_pid is None


@pytest.mark.parametrize(
    'settings',
    [
        {'EnableActivityTracking': False, 'UseDiscordRichPresence': True},
        {'EnableActivityTracking': True, 'UseDiscordRichPresence': False},
        {'EnableActivityTracking': 'true', 'UseDiscordRichPresence': True},
        {'EnableActivityTracking': True, 'UseDiscordRichPresence': True,
         'CustomIntegrations': [{'AutoClose': True}]},
        {'EnableActivityTracking': True, 'UseDiscordRichPresence': True,
         'CustomIntegrations': 'broken'},
        {'EnableActivityTracking': True, 'UseDiscordRichPresence': True,
         'CustomIntegrations': [{'Name': 'default auto-close'}]},
    ],
)
def test_disabled_or_unsafe_bloxstrap_settings_never_qualify(
    tmp_path: Path, settings: dict[str, object]
) -> None:
    executable, log_dir = _installation(tmp_path, settings)
    assert _plan(executable, log_dir) is None


def test_invalid_or_missing_settings_never_qualify(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    (executable.parent / 'Settings.json').unlink()
    assert _plan(executable, log_dir) is None
    (executable.parent / 'Settings.json').write_text('{bad json', encoding='utf-8')
    assert _plan(executable, log_dir) is None


def test_non_bloxstrap_launcher_never_qualifies(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    assert _plan(executable, log_dir, parent_pid=999) is None
    renamed = executable.with_name('OtherBootstrapper.exe')
    renamed.write_bytes(b'exe')
    assert _plan(renamed, log_dir) is None
    assert _plan(executable, log_dir, processes=[]) is None


def test_unknown_or_unrelated_watcher_blocks_handoff(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    original = BloxstrapProcess(100, executable, '-player')
    assert _plan(executable, log_dir, processes=[original, _watcher(executable, 999)]) is None
    unknown = BloxstrapProcess(200, executable, 'Bloxstrap.exe -watcher invalid-base64')
    assert _plan(executable, log_dir, processes=[original, unknown]) is None


def test_autoclose_processes_block_watcher_clone(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    assert _plan(
        executable, log_dir, processes=[_watcher(executable, 300, autoclose=[501])], parent_pid=None
    ) is None


def test_watcher_data_rejects_malformed_or_non_integer_pid() -> None:
    assert _watched_pid('Bloxstrap.exe -player') is None
    assert _watched_pid('Bloxstrap.exe -watcher invalid') is None
    for pid in [True, '300', -1]:
        payload = base64.b64encode(json.dumps({'ProcessId': pid}).encode()).decode()
        assert _watched_pid(f'Bloxstrap.exe -watcher "{payload}"') is None
    args = _watcher_args(123, Path(r'C:\Users\me\Roblox\logs\new_Player.log'))
    assert args[0] == '-watcher'
    assert _watched_pid('Bloxstrap.exe -watcher ' + args[1]) == (123, False)


def test_handoff_waits_for_launcher_and_old_watcher_then_uses_new_log(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    plan = _plan(executable, log_dir)
    assert plan is not None
    launched: list[tuple[Path, list[str]]] = []
    events: list[str] = []
    samples = iter([
        [BloxstrapProcess(100, executable, '-player')],
        [_watcher(executable, 300)],
        [],
    ])

    def query() -> list[BloxstrapProcess]:
        events.append('check')
        return next(samples, list[BloxstrapProcess]())

    def sleep(_delay: float) -> None:
        events.append('wait')
        if len([event for event in events if event == 'wait']) == 2:
            (log_dir / 'new_Player_last.log').write_text('new launch', encoding='utf-8')

    def spawn(path: Path, args: list[str]) -> None:
        events.append('spawn')
        launched.append((path, args))

    assert run_handoff(
        plan, 400, query_processes=query, player_running=lambda _pid: True,
        spawn=spawn, cancelled=lambda: False, report=lambda msg: events.append(msg), sleep=sleep,
    )
    assert events[:5] == ['check', 'wait', 'check', 'wait', 'check']
    assert len(launched) == 1
    assert launched[0][0] == executable
    assert _watched_pid('Bloxstrap.exe ' + ' '.join(launched[0][1])) == (400, False)
    payload = json.loads(base64.b64decode(launched[0][1][1]))
    assert payload == {
        'ProcessId': 400,
        'LogFile': str(log_dir / 'new_Player_last.log'),
        'AutoclosePids': [],
    }


def test_handoff_never_spawns_for_stopped_player_or_cancelled_operation(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    plan = _plan(executable, log_dir)
    assert plan is not None
    for cancelled, running in [(True, True), (False, False)]:
        assert not run_handoff(
            plan, 400, query_processes=lambda: [], player_running=lambda _pid: running,
            spawn=_unexpected_spawn,
            cancelled=lambda: cancelled, report=lambda _msg: None,
        )


def test_handoff_never_uses_old_log_or_spawns_while_other_watcher_alive(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    plan = _plan(executable, log_dir)
    assert plan is not None
    (log_dir / 'new_Player_last.log').write_text('new', encoding='utf-8')
    for processes in [[_watcher(executable, 300)], []]:
        if not processes:
            (log_dir / 'new_Player_last.log').unlink()
        clock = [0.0]

        def advance(duration: float) -> None:
            clock[0] += duration

        assert not run_handoff(
            plan, 400, query_processes=lambda: processes,
            player_running=lambda _pid: True,
            spawn=_unexpected_spawn,
            cancelled=lambda: False, report=lambda _msg: None,
            monotonic=lambda: clock[0], sleep=advance,
        )
        assert clock[0] >= 19


def test_handoff_spawn_error_is_nonfatal(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    plan = _plan(executable, log_dir)
    assert plan is not None
    (log_dir / 'new_Player_last.log').write_text('new', encoding='utf-8')
    messages: list[str] = []

    def spawn(_path: Path, _args: list[str]) -> None:
        raise OSError('access denied')

    assert not run_handoff(
        plan, 400, query_processes=lambda: [], player_running=lambda _pid: True,
        spawn=spawn, cancelled=lambda: False, report=messages.append,
    )
    assert any('handoff failed' in message for message in messages)


def test_testmode_launch_does_not_get_a_second_watcher(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    launcher = BloxstrapProcess(100, executable, '-player -testmode')
    assert _plan(executable, log_dir, processes=[launcher]) is None


def test_presence_disabled_after_capture_prevents_watch_restart(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    plan = _plan(executable, log_dir)
    assert plan is not None
    (log_dir / 'new_Player_last.log').write_text('new', encoding='utf-8')
    (executable.parent / 'Settings.json').write_text(
        json.dumps({'EnableActivityTracking': False, 'UseDiscordRichPresence': True}),
        encoding='utf-8',
    )
    messages: list[str] = []
    assert not run_handoff(
        plan, 400, query_processes=lambda: [], player_running=lambda _pid: True,
        spawn=_unexpected_spawn, cancelled=lambda: False, report=messages.append,
    )
    assert any('settings changed' in message for message in messages)


def test_existing_replacement_watcher_never_gets_duplicated(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    plan = _plan(executable, log_dir)
    assert plan is not None
    messages: list[str] = []
    assert run_handoff(
        plan, 400, query_processes=lambda: [_watcher(executable, 400)],
        player_running=lambda _pid: True,
        spawn=_unexpected_spawn, cancelled=lambda: False, report=messages.append,
    )
    assert any('already tracks' in message for message in messages)


def test_unrelated_bloxstrap_installation_does_not_race_mutex(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    plan = _plan(executable, log_dir)
    assert plan is not None
    (log_dir / 'new_Player_last.log').write_text('new', encoding='utf-8')
    other = BloxstrapProcess(888, tmp_path / 'AnotherInstall' / 'Bloxstrap.exe', '-menu')
    clock = [0.0]

    def advance(duration: float) -> None:
        clock[0] += duration

    assert not run_handoff(
        plan, 400, query_processes=lambda: [other], player_running=lambda _pid: True,
        spawn=_unexpected_spawn, cancelled=lambda: False, report=lambda _msg: None,
        monotonic=lambda: clock[0], sleep=advance,
    )


def test_process_enumeration_failure_is_nonfatal(tmp_path: Path) -> None:
    executable, log_dir = _installation(tmp_path)
    plan = _plan(executable, log_dir)
    assert plan is not None
    messages: list[str] = []

    def query() -> list[BloxstrapProcess]:
        raise OSError('Process list unavailable')

    assert not run_handoff(
        plan, 400, query_processes=query, player_running=lambda _pid: True,
        spawn=_unexpected_spawn, cancelled=lambda: False, report=messages.append,
    )
    assert any('process query failed' in message for message in messages)
