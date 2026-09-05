from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.app import elevation, restart

if TYPE_CHECKING:
    import pytest


def test_windows_development_restart_uses_repository_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[list[str]] = []

    def spawn(args: list[str], **_kwargs: object) -> object:
        launches.append(args)
        return object()

    def find_uv(_command: str) -> str:
        return '/tools/uv'

    monkeypatch.setattr(restart.sys, 'platform', 'win32')
    monkeypatch.setattr(restart.sys, 'frozen', False, raising=False)
    monkeypatch.setattr(restart.sys, 'argv', ['fleasion', '--no-dashboard'])
    monkeypatch.setattr(restart.shutil, 'which', find_uv)
    monkeypatch.setattr(restart.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(restart, 'spawn_trusted_command', spawn)

    assert restart.restart_fleasion_normally()
    project_root = Path(__file__).resolve().parents[1]
    assert launches == [
        [
            '/tools/uv',
            '--project',
            str(project_root),
            'run',
            'fleasion',
            '--no-dashboard',
            '--kill-others',
        ]
    ]
    assert (project_root / 'pyproject.toml').is_file()


def test_macos_development_elevation_uses_repository_source_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, '', '')

    def find_osascript(_command: str) -> str:
        return '/usr/bin/osascript'

    monkeypatch.setattr(elevation.sys, 'frozen', False, raising=False)
    monkeypatch.setattr(elevation.sys, 'argv', ['fleasion'])
    monkeypatch.setattr(elevation, '_resolve_executable', find_osascript)
    monkeypatch.setattr(elevation, '_run_trusted_text_command', run)

    assert elevation.relaunch_as_admin_macos(extra_args='', wait_for_completion=True)
    script = commands[0][2]
    command = json.loads(
        script.removeprefix('do shell script ').removesuffix(' with administrator privileges')
    )
    project_root = Path(__file__).resolve().parents[1]
    assert command.startswith(f'cd {shlex.quote(str(project_root))} && ')
    assert f'PYTHONPATH={shlex.quote(str(project_root / "src"))}' in command
