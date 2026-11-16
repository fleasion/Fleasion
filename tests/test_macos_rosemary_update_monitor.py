"""Roblox log and process signals used for managed/native updater handoffs."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from fleasion.utils import macos_rosemary_update_monitor as monitor

if TYPE_CHECKING:
    from pathlib import Path


def test_probe_ignores_prior_logs_and_recognizes_new_update_markers(tmp_path: Path) -> None:
    old = tmp_path / 'old_Player_old_last.log'
    old.write_bytes(b'Found new version and the updater launched\n')
    observer = monitor.PlayerLogProbe(tmp_path)
    assert observer.poll() == []
    new = tmp_path / 'new_Player_new_last.log'
    with new.open('wb') as log:
        log.write(b'Update check thread: updateRequired TRUE\n')
    observed = observer.poll()
    assert len(observed) == 1
    assert observed[0].updating
    assert not observed[0].updater_started
    with new.open('ab') as log:
        log.write(b'Found new version and the updater launched\n')
    assert observer.poll()[0].updater_started
    assert not observer.poll()[0].current


def test_probe_matches_split_log_lines_and_ignores_crash_handler(tmp_path: Path) -> None:
    probe = monitor.PlayerLogProbe(tmp_path)
    crash = tmp_path / 'test_Player_new_CrashHandler_last.log'
    crash.write_bytes(b'MacUpdateController::launchFinalizeUpdate')
    good = tmp_path / 'test_Player_new_last.log'
    good.write_bytes(b'Update check thread: updateRequir')
    assert len(probe.poll()) == 1
    assert not probe.poll()[0].current
    with good.open('ab') as log:
        log.write(b'ed FALSE\nMacUpdateController::launchFinalizeUpdate')
    state = probe.poll()[0]
    assert state.current
    assert state.updater_started


def test_installer_is_scoped_to_our_runtime_and_exact_roblox_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / 'Rosemary/bin/rosemary'
    app = tmp_path / 'Roblox.app'
    installer = app / 'Contents/MacOS/RobloxPlayerInstaller.app/Contents/MacOS/RobloxPlayerInstaller'
    owned = f'{runtime} run {installer} -VersionGuid version-example'
    unrelated = f'{tmp_path}/other/rosemary run {installer}'
    someone_else = f'{runtime} run {tmp_path}/Other.app/Contents/MacOS/RobloxPlayerInstaller.app/Contents/MacOS/RobloxPlayerInstaller'
    output = f'500 {owned}\n501 {unrelated}\n502 {someone_else}\n503 {runtime} server\n'
    calls: list[list[str]] = []

    def fake_ps(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(cmd)
        if '-axo' in cmd:
            return SimpleNamespace(returncode=0, stdout=output)
        if '-p' in cmd:
            return (SimpleNamespace(returncode=1, stdout='') if killed else SimpleNamespace(returncode=0, stdout=owned + '\n'))
        pytest.fail(f'unexpected process inspection {cmd}')

    killed: list[int] = []
    monkeypatch.setattr(monitor.subprocess, 'run', fake_ps)
    monkeypatch.setattr(monitor.os, 'kill', lambda pid, _sig: killed.append(pid))
    assert monitor.managed_installer_running(runtime, app)
    monitor.terminate_managed_installers(runtime, app)
    assert killed == [500]
    assert any('-p' in cmd for cmd in calls)


def test_pid_identity_mismatch_never_kills_another_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / 'rosemary'
    app = tmp_path / 'Roblox.app'
    installer = app / 'Contents/MacOS/RobloxPlayerInstaller.app/Contents/MacOS/RobloxPlayerInstaller'
    command = f'{runtime} run {installer}'
    def fake_ps(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=f'1000 {command}' if '-axo' in cmd else 'a-reused-process',
        )
    monkeypatch.setattr(monitor.subprocess, 'run', fake_ps)
    monkeypatch.setattr(
        monitor.os, 'kill', lambda *_args: pytest.fail('unexpected process termination')
    )
    monitor.terminate_managed_installers(runtime, app)


def test_failed_process_inspection_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        monitor.subprocess, 'run',
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=''),
    )
    with pytest.raises(OSError, match='inspect Rosemary installer'):
        monitor.managed_installer_running(tmp_path / 'rosemary', tmp_path / 'Roblox.app')


def test_native_installer_observer_only_accepts_new_completed_install(tmp_path: Path) -> None:
    old = tmp_path / 'RobloxPlayerInstaller_2026-11-16_09:00:00.log'
    old.write_bytes(b'Installation succeed!\n')
    probe = monitor.NativeInstallerLogProbe(tmp_path)
    assert not probe.completed()
    current = tmp_path / 'RobloxPlayerInstaller_2026-11-16_09:01:00.log'
    current.write_bytes(b'Preparing package\nInstallati')
    assert not probe.completed()
    with current.open('ab') as writer:
        writer.write(b'on succeed!\n')
    assert probe.completed()
    assert probe.completed()


def test_failed_installer_does_not_signal_completion(tmp_path: Path) -> None:
    probe = monitor.NativeInstallerLogProbe(tmp_path)
    (tmp_path / 'RobloxPlayerInstaller_2026-11-16_09:02:00.log').write_bytes(
        b'Installer failed!\n'
    )
    assert not probe.completed()
