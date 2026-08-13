"""Non-visual command dispatcher for Fleasion's one-shot helper modes."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, TextIO

HELPER_FLAGS: Final[frozenset[str]] = frozenset(
    {
        '--repair-autostart',
        '--disable-autostart',
        '--repair-roblox-permissions',
        '--repair-firewall',
        '--cleanup-hosts',
        '--fleasion-gdk-debugger',
        '--install-linux-privileged-helper',
    }
)

_HOSTS_CLEANUP_NOT_ADMIN_EXIT: Final = 10
_HOSTS_CLEANUP_WRITE_FAILED_EXIT: Final = 11
_HOSTS_CLEANUP_UNEXPECTED_EXIT: Final = 12


def _log(category: str, message: str) -> None:
    from .utils.logging import log_buffer

    log_buffer.log(category, message)


def _config_dir() -> Path:
    from .utils.paths import CONFIG_DIR

    return CONFIG_DIR


def _is_admin() -> bool:
    """Return whether the helper process has administrator/root privileges."""
    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        return hasattr(os, 'geteuid') and os.geteuid() == 0

    import ctypes

    try:
        windll = getattr(ctypes, 'windll', None)
        return bool(windll and windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _cleanup_hosts_once() -> int:
    """Remove only Fleasion-owned hosts entries in an elevated child."""
    if not _is_admin():
        _log(
            'Hosts',
            'Elevated hosts cleanup child rejected: administrator access is required',
        )
        return _HOSTS_CLEANUP_NOT_ADMIN_EXIT

    from .proxy.master import (
        INTERCEPT_HOSTS,
        _cancel_hosts_cleanup_on_reboot,
        _flush_dns,
        _remove_hosts_entries,
    )

    error_details: dict[str, object] = {}
    try:
        if not _remove_hosts_entries(set(INTERCEPT_HOSTS), error_details=error_details):
            detail = error_details.get('error') or 'unknown hosts write failure'
            _log(
                'Hosts',
                f'Elevated hosts cleanup child could not update the hosts file: {detail}',
            )
            return _HOSTS_CLEANUP_WRITE_FAILED_EXIT
        _flush_dns()
        _cancel_hosts_cleanup_on_reboot()
    except Exception as exc:
        _log('Hosts', f'Elevated hosts cleanup child crashed: {exc!r}')
        return _HOSTS_CLEANUP_UNEXPECTED_EXIT

    _log('Hosts', 'Elevated one-shot hosts cleanup completed')
    return 0


def _repair_autostart_once(
    requesting_user_sid: str | None = None,
    *,
    enabled: bool = True,
) -> int:
    """Repair or remove Windows autostart in an elevated child."""
    if sys.platform != 'win32' or not _is_admin():
        _log(
            'Autostart',
            'Elevated autostart repair rejected: administrator access is required',
        )
        return 1

    from .utils.autostart import sync_autostart

    windows_user_id: str | None = None
    if enabled:
        from .utils.windows_permissions import windows_user_id_from_sid

        if not requesting_user_sid:
            _log('Autostart', 'Elevated autostart repair has no requesting user identity')
            return 1
        try:
            windows_user_id = windows_user_id_from_sid(requesting_user_sid)
        except Exception as exc:
            _log('Autostart', f'Invalid requesting Windows identity: {exc}')
            return 1

    try:
        from .config.manager import ConfigManager

        proxy_mode = ConfigManager().proxy_mode
    except Exception:
        proxy_mode = None

    if sync_autostart(
        enabled,
        _config_dir(),
        windows_user_id=windows_user_id,
        proxy_mode=proxy_mode,
    ):
        _log(
            'Autostart',
            'Elevated autostart repair completed'
            if enabled
            else 'Elevated legacy autostart-task removal completed',
        )
        return 0

    _log(
        'Autostart',
        'Elevated autostart repair failed'
        if enabled
        else 'Elevated legacy autostart-task removal failed',
    )
    return 1


def _repair_roblox_permissions_once(requesting_user_sid: str | None = None) -> int:
    """Apply a pending targeted Roblox ACL repair in an elevated child."""
    from .utils.windows_permissions import (
        clear_pending_repair,
        grant_current_user_modify_access,
        read_pending_repair,
        write_repair_result,
    )

    if sys.platform != 'win32' or not _is_admin():
        _log(
            'RobloxPermissions',
            'Elevated Roblox ACL repair rejected: administrator access is required',
        )
        return 1
    if not requesting_user_sid:
        _log(
            'RobloxPermissions',
            'Elevated Roblox ACL repair has no requesting user identity',
        )
        return 1

    config_dir = _config_dir()
    paths = read_pending_repair(config_dir)
    if not paths:
        result: dict[str, object] = {
            'ok': False,
            'granted': [],
            'failed': [],
            'error': 'No pending Roblox installation permission repair was found',
        }
    else:
        try:
            result = grant_current_user_modify_access(paths, user_sid=requesting_user_sid)
        except Exception as exc:
            result = {
                'ok': False,
                'granted': [],
                'failed': [],
                'error': f'Unexpected ACL repair error: {exc}',
            }

    try:
        write_repair_result(result, config_dir)
    finally:
        clear_pending_repair(config_dir)

    if result.get('ok'):
        _log('RobloxPermissions', 'Elevated Roblox ACL repair completed')
        return 0
    _log('RobloxPermissions', f'Elevated Roblox ACL repair failed: {result}')
    return 1


def _repair_windows_firewall_once() -> int:
    """Apply a pending Fleasion firewall repair in an elevated child."""
    from .utils.windows_firewall import (
        clear_pending_repair,
        install_fleasion_firewall_rules,
        read_pending_repair,
        write_repair_result,
    )

    config_dir = _config_dir()
    if sys.platform != 'win32' or not _is_admin():
        result: dict[str, object] = {
            'ok': False,
            'rules': [],
            'failed': [],
            'error': 'Administrator permission is required to update Windows Firewall',
        }
    elif not read_pending_repair(config_dir):
        result = {
            'ok': False,
            'rules': [],
            'failed': [],
            'error': 'No pending Fleasion firewall repair was found',
        }
    else:
        try:
            result = install_fleasion_firewall_rules()
        except Exception as exc:
            result = {
                'ok': False,
                'rules': [],
                'failed': [],
                'error': f'Unexpected Windows Firewall repair error: {exc}',
            }

    try:
        write_repair_result(result, config_dir)
    finally:
        clear_pending_repair(config_dir)
    _log(
        'WindowsFirewall',
        'Elevated Fleasion firewall repair completed'
        if result.get('ok')
        else f'Elevated Fleasion firewall repair failed: {result}',
    )

    # Preserve the historical helper contract: the result file carries failure details
    return 0


def _run_gdk_debugger() -> int:
    from .utils.platform_windows import run_gdk_debugger_command_line

    return run_gdk_debugger_command_line()


def _install_linux_privileged_helper(
    *,
    enable_promptless: bool,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if not sys.platform.startswith('linux'):
        stderr.write('Linux privileged helper installation is only supported on Linux.\n')
        return 1

    from .utils.linux_proxy_helper import install_privileged_helper

    result = install_privileged_helper(enable_promptless=enable_promptless)
    if not result.get('ok'):
        stderr.write(
            f'Failed to install Linux privileged helper: {result.get("error") or result}\n'
        )
        return 1

    stdout.write(f'Installed Linux privileged helper: {result["helper"]}\n')
    stdout.write(f'Installed Polkit policy: {result["policy"]}\n')
    if promptless_rule := result.get('promptless_rule'):
        stdout.write(f'Installed promptless Polkit rule: {promptless_rule}\n')
    return 0


def _parse_helper_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--fleasion-user-localappdata')
    parser.add_argument('--fleasion-requesting-user-sid')
    parser.add_argument('--repair-autostart', action='store_true')
    parser.add_argument('--disable-autostart', action='store_true')
    parser.add_argument('--repair-roblox-permissions', action='store_true')
    parser.add_argument('--repair-firewall', action='store_true')
    parser.add_argument('--cleanup-hosts', action='store_true')
    parser.add_argument('--fleasion-gdk-debugger', action='store_true')
    parser.add_argument('--install-linux-privileged-helper', action='store_true')
    parser.add_argument('--linux-helper-promptless', action='store_true')
    args, _unknown = parser.parse_known_args(argv)
    return args


def dispatch_helper_mode(
    argv: Sequence[str],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int | None:
    """Run a requested one-shot helper and return its exit code.

    ``None`` means no helper flag was requested and the visual application may
    continue starting. Helper modes deliberately avoid constructing a Qt
    application because they communicate through logs, result files, and the
    command-line streams only.
    """
    if not HELPER_FLAGS.intersection(argv):
        return None

    args = _parse_helper_args(argv)
    if args.fleasion_gdk_debugger:
        return _run_gdk_debugger()
    if args.cleanup_hosts:
        return _cleanup_hosts_once()
    if args.repair_autostart:
        return _repair_autostart_once(
            args.fleasion_requesting_user_sid,
            enabled=not args.disable_autostart,
        )
    if args.repair_roblox_permissions:
        return _repair_roblox_permissions_once(args.fleasion_requesting_user_sid)
    if args.repair_firewall:
        return _repair_windows_firewall_once()
    if args.install_linux_privileged_helper:
        return _install_linux_privileged_helper(
            enable_promptless=args.linux_helper_promptless,
            stdout=stdout or sys.stdout,
            stderr=stderr or sys.stderr,
        )
    return None


__all__ = ['HELPER_FLAGS', 'dispatch_helper_mode']
