"""Targeted Windows Defender Firewall repair for Fleasion."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from .paths import CONFIG_DIR

PENDING_REPAIR_FILENAME = 'fleasion_firewall_repair.json'
RESULT_REPAIR_FILENAME = 'fleasion_firewall_repair_result.json'

type ResultObject = dict[str, object]


if TYPE_CHECKING:

    def _object_dict(value: object) -> ResultObject | None: ...
else:

    def _object_dict(value: object) -> ResultObject | None:
        return value if isinstance(value, dict) else None


_RULES = (
    ('in', 'Fleasion - Allow inbound (Private,Public)'),
    ('out', 'Fleasion - Allow outbound (Private,Public)'),
)


def _state_path(config_dir: Path | None, filename: str) -> Path:
    return Path(config_dir or CONFIG_DIR) / filename


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.tmp')
    temporary.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    Path(temporary).replace(path)


def write_pending_repair(config_dir: Path | None = None) -> None:
    """Mark that the elevated child should repair Fleasion's firewall rules."""
    _atomic_write_json(_state_path(config_dir, PENDING_REPAIR_FILENAME), {'requested': True})


def read_pending_repair(config_dir: Path | None = None) -> bool:
    path = _state_path(config_dir, PENDING_REPAIR_FILENAME)
    try:
        payload_value: object = json.loads(path.read_text(encoding='utf-8'))
        payload = _object_dict(payload_value)
    except OSError, json.JSONDecodeError:
        return False
    return payload is not None and payload.get('requested') is True


def clear_pending_repair(config_dir: Path | None = None) -> None:
    try:
        _state_path(config_dir, PENDING_REPAIR_FILENAME).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def write_repair_result(result: Mapping[str, object], config_dir: Path | None = None) -> None:
    """Publish the one-shot elevated result for the normal process to read."""
    _atomic_write_json(_state_path(config_dir, RESULT_REPAIR_FILENAME), result)


def read_repair_result(config_dir: Path | None = None) -> ResultObject | None:
    path = _state_path(config_dir, RESULT_REPAIR_FILENAME)
    try:
        payload_value: object = json.loads(path.read_text(encoding='utf-8'))
        payload = _object_dict(payload_value)
    except OSError, json.JSONDecodeError:
        return None
    return payload


def clear_repair_result(config_dir: Path | None = None) -> None:
    try:
        _state_path(config_dir, RESULT_REPAIR_FILENAME).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _is_admin() -> bool:
    if sys.platform != 'win32':
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False



def _netsh_executable() -> str:
    system_root = Path(os.environ.get('SYSTEMROOT', r'C:\Windows'))
    return str(system_root / 'System32' / 'netsh.exe')


def _run_netsh(arguments: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [_netsh_executable(), *arguments],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        timeout=timeout,
    )


def get_fleasion_firewall_rule_status(
    program_path: str | Path | None = None,
) -> ResultObject:
    """Check whether both Fleasion rules exist for the current executable."""
    if sys.platform != 'win32':
        return {'ok': False, 'rules': [], 'missing': [], 'error': 'Windows is required'}

    executable = Path(program_path or sys.executable).resolve()
    present: list[str] = []
    missing: list[str] = []
    errors: list[str] = []
    for _direction, rule_name in _RULES:
        try:
            completed = _run_netsh(
                [
                    'advfirewall',
                    'firewall',
                    'show',
                    'rule',
                    f'name={rule_name}',
                    'verbose',
                ],
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
            continue

        output = f'{completed.stdout}\n{completed.stderr}'.casefold()
        if (
            completed.returncode == 0
            and rule_name.casefold() in output
            and str(executable).casefold() in output
        ):
            present.append(rule_name)
        else:
            missing.append(rule_name)

    return {
        'ok': len(present) == len(_RULES),
        'rules': present,
        'missing': missing,
        'error': '; '.join(errors) if errors else None,
        'program': str(executable),
    }


def install_fleasion_firewall_rules(
    program_path: str | Path | None = None,
) -> ResultObject:
    """Allow Fleasion's executable on private and public Windows networks.

    The rule names are stable, so running the repair again updates the same
    rules instead of accumulating duplicates.  The executable path is taken
    from the elevated child by default rather than from user-controlled state.
    """
    if sys.platform != 'win32':
        return {'ok': False, 'rules': [], 'failed': [], 'error': 'Windows is required'}
    if not _is_admin():
        return {
            'ok': False,
            'rules': [],
            'failed': [],
            'error': 'Administrator permission is required to update Windows Firewall',
        }

    executable = Path(program_path or sys.executable).resolve()
    added: list[str] = []
    failed: list[dict[str, str]] = []
    for direction, rule_name in _RULES:
        command = [
            'advfirewall',
            'firewall',
            'add',
            'rule',
            f'name={rule_name}',
            f'program={executable}',
            f'dir={direction}',
            'action=allow',
            'enable=yes',
            'profile=private,public',
            'protocol=any',
        ]
        try:
            completed = _run_netsh(command, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            failed.append({'rule': rule_name, 'error': str(exc)})
            continue

        if completed.returncode == 0:
            added.append(rule_name)
            continue
        detail = (completed.stderr or completed.stdout or '').strip()
        failed.append(
            {
                'rule': rule_name,
                'error': detail or f'netsh exit code {completed.returncode}',
            }
        )

    return {
        'ok': not failed,
        'rules': added,
        'failed': failed,
        'program': str(executable),
    }
