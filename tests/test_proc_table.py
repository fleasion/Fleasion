from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fleasion.utils import proc_table

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _process(proc: Path, pid: int, *, comm: str | None, cmdline: bytes = b'') -> None:
    directory = proc / str(pid)
    directory.mkdir()
    if comm is not None:
        (directory / 'comm').write_text(f'{comm}\n', encoding='utf-8')
    (directory / 'cmdline').write_bytes(cmdline)


def test_entries_are_numeric_and_pid_ordered(tmp_path: Path) -> None:
    proc = tmp_path / 'proc'
    proc.mkdir()
    for pid in (300, 20, 1000):
        _process(proc, pid, comm='x')
    (proc / 'self').mkdir()

    assert [entry.pid for entry in proc_table.proc_entries(proc)] == [20, 300, 1000]


def test_fields_are_read_once_and_vanished_processes_are_none(tmp_path: Path) -> None:
    proc = tmp_path / 'proc'
    proc.mkdir()
    _process(proc, 7, comm='sober', cmdline=b'/usr/bin/sober\0--flag\0')
    _process(proc, 8, comm=None)
    first, second = proc_table.proc_entries(proc)

    assert first.comm() == 'sober'
    assert first.uid() == os.getuid()
    assert first.owned_by_current_user()
    assert first.cmdline_head() == b'/usr/bin/sober\0--flag\0'

    # Memoized for the lifetime of the table, like one consistent pass
    (proc / '7' / 'comm').write_text('renamed\n', encoding='utf-8')
    assert first.comm() == 'sober'
    assert second.comm() is None


def test_name_match_mirrors_pgrep_exact(tmp_path: Path) -> None:
    proc = tmp_path / 'proc'
    proc.mkdir()
    _process(proc, 5, comm='Main')
    _process(proc, 6, comm='Main2')
    _process(proc, 9, comm='Main')

    assert proc_table.process_pids_by_name('Main', proc_root=proc) == [5, 9]
    assert proc_table.process_pids_by_name('org.vinegarhq.Sober', proc_root=proc) == []


def test_only_the_system_root_is_shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[Path] = []
    original = proc_table._read_table  # pyright: ignore[reportPrivateUsage]

    def counted(path: Path) -> tuple[proc_table.ProcEntry, ...]:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(proc_table, '_read_table', counted)
    monkeypatch.setattr(proc_table, '_cached', None)
    proc = tmp_path / 'proc'
    proc.mkdir()

    proc_table.proc_entries(proc)
    proc_table.proc_entries(proc)
    system = proc_table._SYSTEM_PROC_ROOT  # pyright: ignore[reportPrivateUsage]
    proc_table.proc_entries(system)
    proc_table.proc_entries(system)

    assert reads == [proc, proc, system]
