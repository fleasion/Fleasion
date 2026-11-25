"""Read-only Yama checks and per-Player notices for Rosemary TLS memory access."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.utils.logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Callable

_YAMA_SCOPE = Path('/proc/sys/kernel/yama/ptrace_scope')
_lock = threading.Lock()
_callbacks: dict[str, Callable[[int, str], None]] = {}
_reported_generations: set[tuple[int, int]] = set()


def read_ptrace_scope(*, path: Path = _YAMA_SCOPE) -> int | None:
    """Return Yama's current mode when Linux exposes a valid setting."""
    if sys.platform != 'linux':
        return None
    try:
        value = int(path.read_text(encoding='ascii').strip())
    except OSError, ValueError, UnicodeError:
        return None
    return value if value in {0, 1, 2, 3} else None


def set_warning_callback(callback: Callable[[int, str], None] | None) -> None:
    """Register a UI event bridge; no worker thread may create a Qt widget."""
    with _lock:
        _callbacks.clear()
        if callback is not None:
            _callbacks['ui'] = callback
        _reported_generations.clear()


def report_memory_access_denied(
    pid: int,
    *,
    scope_path: Path = _YAMA_SCOPE,
    proc_root: Path = Path('/proc'),
) -> bool:
    """Report a restricted Player once per PID/start generation, not per TLS connection."""
    scope = read_ptrace_scope(path=scope_path)
    if scope is None or scope == 0 or pid <= 0:
        return False
    try:
        stat = (proc_root / str(pid) / 'stat').read_text(encoding='ascii')
        generation = int(stat.rsplit(')', 1)[1].split()[19])
    except OSError, IndexError, ValueError:
        return False
    with _lock:
        if 'ui' not in _callbacks:
            return False
        identity = (pid, generation)
        if identity in _reported_generations:
            return False
        if len(_reported_generations) >= 128:
            _reported_generations.clear()
        _reported_generations.add(identity)
        notify = _callbacks['ui']
    log_buffer.log(
        'TLS',
        f'Rosemary process memory access denied (pid={pid}, ptrace_scope={scope}); '
        'Linux security restrictions may prevent TLS key recovery',
    )
    notify(scope, 'access_denied')
    return True
