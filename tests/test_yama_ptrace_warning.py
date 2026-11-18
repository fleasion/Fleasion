"""Yama warnings are safe, actionable and deduplicated by Player generation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.utils import yama_ptrace

if TYPE_CHECKING:
    import pytest


def _stat_file(proc: Path, pid: int, start: int) -> Path:
    directory = proc / str(pid)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'stat'
    path.write_text(f'{pid} (Roblox Main) S ' + ' '.join(['0'] * 18 + [str(start), '0']))
    return path


def test_read_scope_is_read_only_and_handles_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')
    scope = tmp_path / 'scope'
    assert yama_ptrace.read_ptrace_scope(path=scope) is None
    for value in (0, 1, 2, 3):
        scope.write_text(f'{value}\n')
        assert yama_ptrace.read_ptrace_scope(path=scope) == value
    for invalid in ('', '4', '-1', 'not a mode'):
        scope.write_text(invalid)
        assert yama_ptrace.read_ptrace_scope(path=scope) is None
    monkeypatch.setattr(sys, 'platform', 'win32')
    scope.write_text('1')
    assert yama_ptrace.read_ptrace_scope(path=scope) is None


def test_permission_denied_once_per_player_and_again_for_new_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')
    proc = tmp_path / 'proc'
    scope = tmp_path / 'scope'
    scope.write_text('1')
    stat = _stat_file(proc, 501, 100)
    reported: list[tuple[int, str]] = []
    yama_ptrace.set_warning_callback(lambda mode, reason: reported.append((mode, reason)))
    try:
        assert yama_ptrace.report_memory_access_denied(501, scope_path=scope, proc_root=proc)
        assert not yama_ptrace.report_memory_access_denied(501, scope_path=scope, proc_root=proc)
        assert reported == [(1, 'access_denied')]
        stat.write_text(stat.read_text().replace('100 ', '101 '))
        assert yama_ptrace.report_memory_access_denied(501, scope_path=scope, proc_root=proc)
        assert reported == [(1, 'access_denied'), (1, 'access_denied')]
        scope.write_text('0')
        assert not yama_ptrace.report_memory_access_denied(501, scope_path=scope, proc_root=proc)
        assert len(reported) == 2
        scope.write_text('2')
        stat.unlink()
        assert not yama_ptrace.report_memory_access_denied(501, scope_path=scope, proc_root=proc)
        assert len(reported) == 2
    finally:
        yama_ptrace.set_warning_callback(None)


def test_notifier_requires_active_listener(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')
    scope = tmp_path / 'scope'
    scope.write_text('3')
    proc = tmp_path / 'proc'
    _stat_file(proc, 501, 50)
    yama_ptrace.set_warning_callback(None)
    assert not yama_ptrace.report_memory_access_denied(501, scope_path=scope, proc_root=proc)


def test_yama_warning_dialog_is_nonblocking_and_does_not_mutate_sysctl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QMessageBox

    from fleasion.app.dialogs import yama

    app = QApplication.instance() or QApplication([])
    dialogs = yama.YamaWarningInvoker()
    dialogs._show_warning(1, 'startup')  # pyright: ignore[reportPrivateUsage]
    alert = dialogs._dialog  # pyright: ignore[reportPrivateUsage]
    assert isinstance(alert, QMessageBox)
    assert alert.isVisible()
    assert 'ptrace_scope=0' in alert.informativeText()
    assert 'THROUGH Fleasion' in alert.informativeText()
    dialogs._show_warning(1, 'access_denied')  # pyright: ignore[reportPrivateUsage]
    assert dialogs._dialog is alert  # pyright: ignore[reportPrivateUsage]
    alert.accept()
    app.processEvents()
    assert dialogs._dialog is None  # pyright: ignore[reportPrivateUsage]
    dialogs._show_warning(2, 'access_denied')  # pyright: ignore[reportPrivateUsage]
    next_dialog = dialogs._dialog  # pyright: ignore[reportPrivateUsage]
    assert isinstance(next_dialog, QMessageBox)
    assert 'scopes 2 and 3' in next_dialog.informativeText()
    next_dialog.accept()
    app.processEvents()


def test_worker_thread_signal_displays_warning_on_gui_thread() -> None:
    import threading

    from PySide6.QtWidgets import QApplication, QMessageBox

    from fleasion.app.dialogs.yama import YamaWarningInvoker

    app = QApplication.instance() or QApplication([])
    invoker = YamaWarningInvoker()

    worker = threading.Thread(target=lambda: invoker.show_warning.emit(1, 'access_denied'))
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    app.processEvents()
    popup = invoker._dialog  # pyright: ignore[reportPrivateUsage]
    assert isinstance(popup, QMessageBox)
    assert 'denied access' in popup.text()
    popup.accept()
    app.processEvents()


def test_rosemary_scanner_warns_only_on_permission_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the useful diagnostic hook without the abandoned scan instrumentation."""
    import errno  # ruff: ignore[import-outside-top-level]

    from fleasion.proxy import rosemary_keys  # ruff: ignore[import-outside-top-level]

    warnings: list[int] = []

    class UnreadableMemory:
        error = errno.EPERM

        def open(self, _mode: str) -> None:
            raise OSError(self.error, 'unreadable')

    memory = UnreadableMemory()

    def fake_path(_path: str) -> UnreadableMemory:
        return memory

    def bounds(_pid: int) -> tuple[int, int]:
        return 0, 0

    def warning(pid: int) -> bool:
        warnings.append(pid)
        return True

    monkeypatch.setattr(rosemary_keys, 'Path', fake_path)
    monkeypatch.setattr(rosemary_keys, '_module_bounds', bounds)
    monkeypatch.setattr(rosemary_keys, 'report_memory_access_denied', warning)
    assert rosemary_keys.scan(9191, client_random='a1' * 32) == {}
    assert warnings == [9191]

    memory.error = errno.EIO
    assert rosemary_keys.scan(9191, client_random='a1' * 32) == {}
    assert warnings == [9191]
