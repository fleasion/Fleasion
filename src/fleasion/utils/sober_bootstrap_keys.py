"""Private native Sober TLS key-log lifecycle for first-boot ClientSettings."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import TypedDict

from .json_types import as_json_object


class KeylogOverrideState(TypedDict):
    previous_present: bool
    previous_value: str


def native_keylog_path(runtime_dir: Path | None = None) -> Path:
    """Return the Sober-visible, host-visible private tmpfs key-log path."""
    if runtime_dir is None:
        runtime_dir = Path(
            os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
        )
    return runtime_dir / 'app' / 'org.vinegarhq.Sober' / '.fleasion-native-tls.keys'


def previous_keylog_override(text: str) -> KeylogOverrideState:
    """Read only the explicit SSLKEYLOGFILE value from Flatpak's user override."""
    in_environment = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith('[') and line.endswith(']'):
            in_environment = line == '[Environment]'
        elif in_environment and line.startswith('SSLKEYLOGFILE='):
            return {'previous_present': True, 'previous_value': line.partition('=')[2]}
    return {'previous_present': False, 'previous_value': ''}


def _valid_owned_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and metadata.st_nlink == 1
    )


def _private_parent(path: Path, *, strict: bool = True) -> bool:
    parent = path.parent
    try:
        parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = parent.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.getuid()
        and (not strict or info.st_mode & 0o077 == 0)
    )


def read_state(state_path: Path) -> KeylogOverrideState | None:
    """Read only a correctly typed ownership record from Fleasion."""
    try:
        if not _valid_owned_file(state_path):
            if state_path.exists() or state_path.is_symlink():
                msg = 'Sober key-log override state is not an owned regular file'
                raise ValueError(msg)
            return None
        value: object = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        msg = 'Cannot read Sober key-log override state'
        raise ValueError(msg) from exc
    mapping = as_json_object(value)
    if mapping is None:
        msg = 'Malformed Sober key-log override state'
        raise TypeError(msg)
    present = mapping.get('previous_present')
    previous = mapping.get('previous_value')
    if not isinstance(present, bool) or not isinstance(previous, str):
        msg = 'Malformed Sober key-log override state'
        raise TypeError(msg)
    return {'previous_present': present, 'previous_value': previous}


def _create_private_file(path: Path) -> int:
    return os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600
    )


def arm(path: Path, state_path: Path, previous: KeylogOverrideState) -> bool:
    """Create an empty 0600 key log and a crash-recovery ownership record."""
    if path.exists() or path.is_symlink() or state_path.exists() or state_path.is_symlink():
        return False
    if not _private_parent(path) or not _private_parent(state_path, strict=False):
        return False
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        state_fd = _create_private_file(state_path)
        with os.fdopen(state_fd, 'w', encoding='utf-8') as destination:
            json.dump(previous, destination)
            destination.write('\n')
        key_fd = _create_private_file(path)
        try:
            os.fchmod(key_fd, 0o600)
        finally:
            os.close(key_fd)
    except OSError:
        if _valid_owned_file(path):
            path.unlink(missing_ok=True)
        if _valid_owned_file(state_path):
            state_path.unlink(missing_ok=True)
        return False
    else:
        return True


def restore_option(state_path: Path) -> str | None:
    """Return the original Flatpak SSLKEYLOGFILE setting for scoped cleanup."""
    previous = read_state(state_path)
    if previous is None:
        return None
    if previous['previous_present']:
        return f"--env=SSLKEYLOGFILE={previous['previous_value']}"
    return '--unset-env=SSLKEYLOGFILE'


def clear(path: Path, state_path: Path) -> bool:
    """Delete only Fleasion-owned private TLS secrets after Flatpak is restored."""
    try:
        state = read_state(state_path)
    except (ValueError, TypeError):
        return False
    if state is None:
        return True
    if path.exists() or path.is_symlink():
        if not _valid_owned_file(path):
            return False
        try:
            path.unlink()
        except OSError:
            return False
    try:
        state_path.unlink()
    except OSError:
        return False
    return True
