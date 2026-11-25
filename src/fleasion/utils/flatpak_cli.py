"""Run the host `flatpak` CLI without disturbing polkit.

The `flatpak` CLI registers a text-mode polkit authentication agent on
startup whenever it can open a controlling terminal. Every registration makes
polkitd broadcast `Changed`, which prompts desktop services (PowerDevil, kded)
to re-check their authorizations. Polling `flatpak` from a terminal-launched
Fleasion therefore keeps polkitd and systemd busy. Detaching the child from
the terminal prevents the agent registration, and caching `flatpak info`
removes the polling itself.
"""

from __future__ import annotations

__all__ = [
    'INFO_CACHE_TTL_SECONDS',
    'clear_flatpak_info_cache',
    'flatpak_app_installed',
    'is_flatpak_command',
    'run_flatpak_query',
]

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


# Info results are re-validated after this long even if no watched folder changed
INFO_CACHE_TTL_SECONDS: Final = 30.0

_FINGERPRINT_ENV_KEYS: Final = (
    'HOME',
    'XDG_DATA_HOME',
    'FLATPAK_USER_DIR',
    'FLATPAK_SYSTEM_DIR',
    'FLATPAK_CONFIG_DIR',
)

type _StatStamp = tuple[int, int] | None
type _Fingerprint = tuple[_StatStamp, ...]
type _CacheKey = tuple[str, str, tuple[str | None, ...]]


@dataclass(frozen=True, slots=True)
class _InfoResult:
    installed: bool
    fingerprint: _Fingerprint
    checked_at: float


_info_cache: dict[_CacheKey, _InfoResult] = {}
_info_cache_lock = threading.Lock()


def is_flatpak_command(args: Sequence[str]) -> bool:
    """Return whether `args` invokes the `flatpak` CLI."""
    return bool(args) and Path(args[0]).name == 'flatpak'


def run_flatpak_query(
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run a non-interactive `flatpak` command detached from the terminal."""
    return subprocess.run(
        list(args),
        env=None if env is None else dict(env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        # Without a controlling terminal flatpak skips its polkit agent
        start_new_session=True,
    )


# Installation fingerprint
# Installing or removing an app adds or deletes `<installation>/app/<id>`,
# which changes the mtime of the `app` folder itself


def _installation_app_dirs(env: Mapping[str, str]) -> tuple[Path, ...]:
    system_dir = Path(env.get('FLATPAK_SYSTEM_DIR') or '/var/lib/flatpak')
    home = Path(env.get('HOME') or Path.home())
    data_home = Path(env.get('XDG_DATA_HOME') or home / '.local' / 'share')
    user_dir = Path(env.get('FLATPAK_USER_DIR') or data_home / 'flatpak')
    return system_dir / 'app', user_dir / 'app'


def _stat_stamp(path: Path) -> _StatStamp:
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return stat_result.st_ino, stat_result.st_mtime_ns


def _installation_fingerprint(env: Mapping[str, str]) -> _Fingerprint:
    config_dir = Path(env.get('FLATPAK_CONFIG_DIR') or '/etc/flatpak')
    watched = (*_installation_app_dirs(env), config_dir / 'installations.d')
    return tuple(_stat_stamp(path) for path in watched)


# Cached `flatpak info`


def flatpak_app_installed(
    executable: str | Path,
    app_id: str,
    *,
    env: Mapping[str, str],
) -> bool:
    """Return whether `flatpak info <app_id>` succeeds, reusing recent results.

    A result is reused until an installation folder changes or
    `INFO_CACHE_TTL_SECONDS` passes. Failures to run `flatpak` are not cached.
    """
    key: _CacheKey = (
        str(executable),
        app_id,
        tuple(env.get(name) for name in _FINGERPRINT_ENV_KEYS),
    )
    fingerprint = _installation_fingerprint(env)
    now = time.monotonic()

    with _info_cache_lock:
        cached = _info_cache.get(key)
    if (
        cached is not None
        and cached.fingerprint == fingerprint
        and now - cached.checked_at < INFO_CACHE_TTL_SECONDS
    ):
        return cached.installed

    try:
        result = run_flatpak_query([str(executable), 'info', app_id], env=env, timeout=5)
    except OSError, subprocess.SubprocessError:
        return False

    installed = result.returncode == 0
    with _info_cache_lock:
        _info_cache[key] = _InfoResult(installed, fingerprint, now)
    return installed


def clear_flatpak_info_cache() -> None:
    """Forget cached `flatpak info` results."""
    with _info_cache_lock:
        _info_cache.clear()
