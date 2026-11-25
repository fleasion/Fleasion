from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from fleasion.utils import flatpak_cli, platform_linux
from fleasion.utils.flatpak_cli import (
    clear_flatpak_info_cache,
    flatpak_app_installed,
    is_flatpak_command,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


type _Call = tuple[list[str], dict[str, object]]


@pytest.fixture(autouse=True)
def fresh_flatpak_cache() -> Iterator[None]:
    clear_flatpak_info_cache()
    yield
    clear_flatpak_info_cache()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[_Call]:
    recorded: list[_Call] = []

    def run(args: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded.append((list(args), kwargs))
        return subprocess.CompletedProcess(list(args), 0, '', '')

    monkeypatch.setattr(flatpak_cli.subprocess, 'run', run)
    return recorded


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        'HOME': str(tmp_path / 'home'),
        'FLATPAK_SYSTEM_DIR': str(tmp_path / 'system'),
        'FLATPAK_CONFIG_DIR': str(tmp_path / 'etc'),
    }


# Terminal detachment


def test_is_flatpak_command_matches_only_the_flatpak_binary() -> None:
    assert is_flatpak_command(['/usr/bin/flatpak', 'info', 'org.example.App'])
    assert is_flatpak_command(['flatpak', 'ps'])
    assert not is_flatpak_command(['/usr/bin/pgrep', '-x', 'flatpak'])
    assert not is_flatpak_command([])


def test_info_probe_runs_without_a_controlling_terminal(
    tmp_path: Path, calls: list[_Call]
) -> None:
    assert flatpak_app_installed('/usr/bin/flatpak', 'org.example.App', env=_env(tmp_path))

    (args, kwargs), = calls
    assert args == ['/usr/bin/flatpak', 'info', 'org.example.App']
    assert kwargs['start_new_session'] is True
    assert kwargs['stdin'] == subprocess.DEVNULL


def test_platform_runner_detaches_only_flatpak(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[_Call] = []

    def run(args: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded.append((list(args), kwargs))
        return subprocess.CompletedProcess(list(args), 0, '', '')

    monkeypatch.setattr(platform_linux.subprocess, 'run', run)

    platform_linux._run_subprocess(['/usr/bin/flatpak', 'kill', 'org.example.App'], text=True)  # pyright: ignore[reportPrivateUsage]
    platform_linux._run_subprocess(['/usr/bin/pgrep', '-x', 'sober'], text=True)  # pyright: ignore[reportPrivateUsage]

    flatpak_kwargs, pgrep_kwargs = (kwargs for _args, kwargs in recorded)
    assert flatpak_kwargs['start_new_session'] is True
    assert flatpak_kwargs['stdin'] == subprocess.DEVNULL
    assert 'start_new_session' not in pgrep_kwargs
    assert 'stdin' not in pgrep_kwargs


# Info cache


def test_info_result_is_reused_while_installations_are_unchanged(
    tmp_path: Path, calls: list[_Call]
) -> None:
    env = _env(tmp_path)

    for _ in range(5):
        assert flatpak_app_installed('/usr/bin/flatpak', 'org.example.App', env=env)

    assert len(calls) == 1


def test_installing_an_app_invalidates_the_cached_result(
    tmp_path: Path, calls: list[_Call]
) -> None:
    env = _env(tmp_path)
    app_dir = tmp_path / 'system' / 'app'
    app_dir.mkdir(parents=True)

    flatpak_app_installed('/usr/bin/flatpak', 'org.example.App', env=env)
    (app_dir / 'org.example.App').mkdir()
    flatpak_app_installed('/usr/bin/flatpak', 'org.example.App', env=env)

    assert len(calls) == 2


def test_cached_result_expires_after_the_ttl(
    tmp_path: Path, calls: list[_Call], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(flatpak_cli.time, 'monotonic', lambda: clock[0])

    flatpak_app_installed('/usr/bin/flatpak', 'org.example.App', env=env)
    clock[0] += flatpak_cli.INFO_CACHE_TTL_SECONDS - 1
    flatpak_app_installed('/usr/bin/flatpak', 'org.example.App', env=env)
    clock[0] += 2
    flatpak_app_installed('/usr/bin/flatpak', 'org.example.App', env=env)

    assert len(calls) == 2


def test_missing_app_is_cached_but_launch_failures_are_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(tmp_path)
    outcomes: list[int | OSError] = [1, OSError('flatpak vanished'), 0]
    attempts: list[list[str]] = []

    def run(args: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        attempts.append(list(args))
        outcome = outcomes.pop(0)
        if isinstance(outcome, OSError):
            raise outcome
        return subprocess.CompletedProcess(list(args), outcome, '', '')

    monkeypatch.setattr(flatpak_cli.subprocess, 'run', run)

    # A clean non-zero exit is a real answer and is cached
    assert not flatpak_app_installed('/usr/bin/flatpak', 'org.example.Missing', env=env)
    assert not flatpak_app_installed('/usr/bin/flatpak', 'org.example.Missing', env=env)
    assert len(attempts) == 1

    # A launch failure is retried on the next call
    assert not flatpak_app_installed('/usr/bin/flatpak', 'org.example.Other', env=env)
    assert flatpak_app_installed('/usr/bin/flatpak', 'org.example.Other', env=env)
    assert len(attempts) == 3


def test_results_are_kept_apart_per_app_and_home(tmp_path: Path, calls: list[_Call]) -> None:
    env = _env(tmp_path)
    other_home = {**env, 'HOME': str(tmp_path / 'other')}

    flatpak_app_installed('/usr/bin/flatpak', 'org.example.App', env=env)
    flatpak_app_installed('/usr/bin/flatpak', 'org.example.Other', env=env)
    flatpak_app_installed('/usr/bin/flatpak', 'org.example.App', env=other_home)
    flatpak_app_installed('/usr/bin/flatpak', 'org.example.App', env=env)

    assert len(calls) == 3
