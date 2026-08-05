"""Targeted Windows ACL repair for protected Roblox Player installations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .paths import CONFIG_DIR
from .roblox_dirs import _normalise_roblox_dir, is_roblox_studio_resource_dir

PENDING_REPAIR_FILENAME = 'roblox_permission_repair.json'
RESULT_REPAIR_FILENAME = 'roblox_permission_repair_result.json'


def _state_path(config_dir: Path | None, filename: str) -> Path:
    return Path(config_dir or CONFIG_DIR) / filename


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.tmp')
    temporary.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def _serialise_paths(paths: Iterable[Path]) -> list[str]:
    seen: set[str] = set()
    serialised: list[str] = []
    for raw in paths:
        path = Path(raw)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        key = os.path.normcase(os.path.abspath(str(resolved)))
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
    path = _state_path(config_dir, PENDING_REPAIR_FILENAME)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    raw_paths = payload.get('paths', []) if isinstance(payload, dict) else []
    if not isinstance(raw_paths, list):
        return []
    return [Path(value) for value in raw_paths if isinstance(value, str) and value]


def clear_pending_repair(config_dir: Path | None = None) -> None:
    try:
        _state_path(config_dir, PENDING_REPAIR_FILENAME).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def write_repair_result(result: dict, config_dir: Path | None = None) -> None:
    """Publish the one-shot elevated result for the normal process to read."""
    _atomic_write_json(_state_path(config_dir, RESULT_REPAIR_FILENAME), result)


def read_repair_result(config_dir: Path | None = None) -> dict | None:
    path = _state_path(config_dir, RESULT_REPAIR_FILENAME)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def clear_repair_result(config_dir: Path | None = None) -> None:
    try:
        _state_path(config_dir, RESULT_REPAIR_FILENAME).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _validated_install_dirs(paths: Iterable[Path]) -> tuple[list[Path], list[dict]]:
    valid: list[Path] = []
    rejected: list[dict] = []
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
        try:
            normalised = normalised.resolve()
        except OSError:
            pass
        # The request must name the installation directory itself.  Do not
        # accept an executable path or a parent Roblox directory by silently
        # widening the ACL target.
        if os.path.normcase(os.path.abspath(str(normalised))) != os.path.normcase(
            os.path.abspath(str(raw_resolved))
        ):
            rejected.append({'path': str(raw_path), 'error': 'path is not the installation directory'})
            continue
        if not normalised.is_dir() or is_roblox_studio_resource_dir(normalised):
            rejected.append({'path': str(raw_path), 'error': 'installation is not Roblox Player'})
            continue
        key = os.path.normcase(os.path.abspath(str(normalised)))
        if key not in seen:
            seen.add(key)
            valid.append(normalised)

    return valid, rejected


def _current_user_sid() -> str:
    """Return the SID of the interactive account running Fleasion."""
    import win32api
    import win32security

    username = win32api.GetUserName()
    sid, _domain, _account_type = win32security.LookupAccountName(None, username)
    return str(win32security.ConvertSidToStringSid(sid))


def grant_current_user_modify_access(paths: Iterable[Path]) -> dict:
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
        sid = _current_user_sid()
    except Exception as exc:
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
            completed = subprocess.run(
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
        failed.append({'path': str(path), 'error': detail or f'icacls exit code {completed.returncode}'})

    return {
        'ok': bool(valid) and not failed,
        'granted': granted,
        'failed': failed,
        'sid': sid,
    }
