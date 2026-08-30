"""Targeted Windows ACL repair for protected Roblox Player installations."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from collections.abc import (
    Callable,  # ruff: ignore[typing-only-standard-library-import]
    Iterable,  # ruff: ignore[typing-only-standard-library-import]
)
from pathlib import Path
from typing import Protocol, cast

from .paths import CONFIG_DIR
from .roblox_dirs import (
    _normalise_roblox_dir,  # pyright: ignore[reportPrivateUsage]
    is_roblox_studio_resource_dir,
)

PENDING_REPAIR_FILENAME = 'roblox_permission_repair.json'
RESULT_REPAIR_FILENAME = 'roblox_permission_repair_result.json'

type ErrorDetails = dict[str, object]
type PathFailure = dict[str, str]


class _Win32ApiLike(Protocol):
    GetUserName: Callable[[], str]


class _Win32SecurityLike(Protocol):
    SidTypeUser: int
    LookupAccountName: Callable[[object | None, str], tuple[object, str, int]]
    ConvertSidToStringSid: Callable[[object], str]
    ConvertStringSidToSid: Callable[[str], object]
    LookupAccountSid: Callable[[object | None, object], tuple[str, str, int]]


def _win32api_module() -> _Win32ApiLike:
    return cast('_Win32ApiLike', __import__('win32api'))


def _win32security_module() -> _Win32SecurityLike:
    return cast('_Win32SecurityLike', __import__('win32security'))


def _state_path(config_dir: Path | None, filename: str) -> Path:
    return Path(config_dir or CONFIG_DIR) / filename


def _atomic_write_json(path: Path, payload: ErrorDetails) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.tmp')
    temporary.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    os.replace(temporary, path)  # ruff: ignore[os-replace]


def _read_json_object(path: Path) -> ErrorDetails | None:
    try:
        payload: object = json.loads(path.read_text(encoding='utf-8'))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    # JSON object keys are always strings.
    return cast('ErrorDetails', payload)


def _serialise_paths(paths: Iterable[Path]) -> list[str]:
    seen: set[str] = set()
    serialised: list[str] = []
    for raw in paths:
        path = Path(raw)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        key = os.path.normcase(os.path.abspath(str(resolved)))  # ruff: ignore[os-path-abspath]
        if key in seen:
            continue
        seen.add(key)
        serialised.append(str(resolved))
    return serialised


def write_pending_repair(paths: Iterable[Path], config_dir: Path | None = None) -> bool:
    """Persist the exact install folders selected for the elevated repair."""
    serialised = _serialise_paths(paths)
    if not serialised:
        return False
    _atomic_write_json(
        _state_path(config_dir, PENDING_REPAIR_FILENAME),
        {'paths': serialised},
    )
    return True


def read_pending_repair(config_dir: Path | None = None) -> list[Path]:
    payload = _read_json_object(_state_path(config_dir, PENDING_REPAIR_FILENAME))
    if payload is None:
        return []
    raw_paths: object = payload.get('paths', [])
    if not isinstance(raw_paths, list):
        return []
    values = cast('list[object]', raw_paths)
    return [Path(value) for value in values if isinstance(value, str) and value]


def clear_pending_repair(config_dir: Path | None = None) -> None:
    try:
        _state_path(config_dir, PENDING_REPAIR_FILENAME).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def write_repair_result(result: ErrorDetails, config_dir: Path | None = None) -> None:
    """Publish the one-shot elevated result for the normal process to read."""
    _atomic_write_json(_state_path(config_dir, RESULT_REPAIR_FILENAME), result)


def read_repair_result(config_dir: Path | None = None) -> ErrorDetails | None:
    return _read_json_object(_state_path(config_dir, RESULT_REPAIR_FILENAME))


def clear_repair_result(config_dir: Path | None = None) -> None:
    try:
        _state_path(config_dir, RESULT_REPAIR_FILENAME).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _validated_install_dirs(paths: Iterable[Path]) -> tuple[list[Path], list[PathFailure]]:
    valid: list[Path] = []
    rejected: list[PathFailure] = []
    seen: set[str] = set()

    for raw in paths:
        raw_path = Path(raw)
        try:
            raw_resolved = raw_path.resolve()
        except OSError:
            raw_resolved = raw_path
        normalised = _normalise_roblox_dir(raw_resolved)
        if normalised is None:
            rejected.append({'path': str(raw_path), 'error': 'not a Roblox Player installation'})
            continue
        with contextlib.suppress(OSError):
            normalised = normalised.resolve()
        # The request must name the installation directory itself.  Do not
        # accept an executable path or a parent Roblox directory by silently
        # widening the ACL target.
        if os.path.normcase(os.path.abspath(str(normalised))) != os.path.normcase(  # ruff: ignore[os-path-abspath]
            os.path.abspath(str(raw_resolved))  # ruff: ignore[os-path-abspath]
        ):
            rejected.append(
                {'path': str(raw_path), 'error': 'path is not the installation directory'}
            )
            continue
        if not normalised.is_dir() or is_roblox_studio_resource_dir(normalised):
            rejected.append({'path': str(raw_path), 'error': 'installation is not Roblox Player'})
            continue
        key = os.path.normcase(os.path.abspath(str(normalised)))  # ruff: ignore[os-path-abspath]
        if key not in seen:
            seen.add(key)
            valid.append(normalised)

    return valid, rejected


def current_windows_user_identity() -> tuple[str, str]:
    """Return the current process account as ``(SID, DOMAIN\\name)``."""
    win32api = _win32api_module()
    win32security = _win32security_module()

    username = win32api.GetUserName()
    sid, domain, _account_type = win32security.LookupAccountName(None, username)
    sid_text = str(win32security.ConvertSidToStringSid(sid))
    account_name = f'{domain}\\{username}' if domain else username
    return sid_text, account_name


def _validated_user_sid(requested_sid: str | None = None) -> str:
    """Validate an initiating user's SID or resolve the current process SID."""
    win32security = _win32security_module()

    if not requested_sid:
        return current_windows_user_identity()[0]

    sid = win32security.ConvertStringSidToSid(str(requested_sid))
    canonical = str(win32security.ConvertSidToStringSid(sid))
    _name, _domain, account_type = win32security.LookupAccountSid(None, sid)
    if account_type != win32security.SidTypeUser:
        msg = 'The requested Windows identity is not a user account'
        raise ValueError(msg)
    return canonical


def windows_user_id_from_sid(requested_sid: str) -> str:
    """Return canonical ``DOMAIN\\name`` for a validated Windows user SID."""
    win32security = _win32security_module()

    canonical = _validated_user_sid(requested_sid)
    sid = win32security.ConvertStringSidToSid(canonical)
    name, domain, _account_type = win32security.LookupAccountSid(None, sid)
    return f'{domain}\\{name}' if domain else name


def grant_current_user_modify_access(
    paths: Iterable[Path],
    *,
    user_sid: str | None = None,
) -> ErrorDetails:
    """Grant only the current user's Modify access on validated install dirs.

    Existing ACL entries are preserved.  ``/grant:r`` replaces only an
    explicit entry for this SID, while ``(OI)(CI)M /T`` applies Modify access
    to the selected installation tree and its children.
    """
    if sys.platform != 'win32':
        return {'ok': False, 'granted': [], 'failed': [], 'error': 'Windows is required'}

    valid, rejected = _validated_install_dirs(paths)
    if not valid:
        return {
            'ok': False,
            'granted': [],
            'failed': rejected,
            'error': 'No valid Roblox Player installation folders were supplied',
        }

    try:
        sid = _validated_user_sid(user_sid)
    except Exception as exc:  # ruff: ignore[blind-except]
        return {
            'ok': False,
            'granted': [],
            'failed': rejected,
            'error': f'Could not resolve the current Windows user SID: {exc}',
        }

    granted: list[str] = []
    failed = list(rejected)
    for path in valid:
        command = [
            'icacls.exe',
            str(path),
            '/grant:r',
            f'*{sid}:(OI)(CI)M',
            '/T',
            '/C',
        ]
        try:
            completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                command,
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            failed.append({'path': str(path), 'error': str(exc)})
            continue

        if completed.returncode == 0:
            granted.append(str(path))
            continue
        detail = (completed.stderr or completed.stdout or '').strip()
        failed.append(
            {'path': str(path), 'error': detail or f'icacls exit code {completed.returncode}'}
        )

    return {
        'ok': bool(valid) and not failed,
        'granted': granted,
        'failed': failed,
        'sid': sid,
    }
