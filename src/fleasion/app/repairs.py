"""One-shot privileged repairs for hosts, permissions, firewall, and autostart."""

from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING

from fleasion.app.compatibility import (
    CompatibilityBoundaryError,
    RelaunchCompletion,
    call_compatibility_boundary,
)
from fleasion.app.dialogs.common import window_handle
from fleasion.app.elevation import WINDOWS_WAIT_TIMEOUT, is_admin, relaunch_as_admin
from fleasion.config import ConfigManager
from fleasion.localization import tr
from fleasion.utils import (
    CONFIG_DIR,
    log_buffer,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import (
        QWidget,
    )

    from fleasion.app.error_details import (
        ErrorDetails,
    )


WINDOWS_HOSTS_CLEANUP_TIMEOUT_SECONDS = 15 * 60


HOSTS_CLEANUP_NOT_ADMIN_EXIT = 10


HOSTS_CLEANUP_WRITE_FAILED_EXIT = 11


HOSTS_CLEANUP_UNEXPECTED_EXIT = 12


def cleanup_hosts_once() -> int:
    """Remove Fleasion hosts entries from a one-shot elevated child."""
    if not is_admin():
        log_buffer.log(
            'Hosts', 'Elevated hosts cleanup child rejected: administrator access is required'
        )
        return HOSTS_CLEANUP_NOT_ADMIN_EXIT

    proxy_master = importlib.import_module('fleasion.proxy.master')

    error_details: ErrorDetails = {}

    def _perform_cleanup() -> int:
        if not proxy_master.cleanup_hosts_entries(
            set(proxy_master.INTERCEPT_HOSTS), error_details=error_details
        ):
            detail = error_details.get('error') or tr('app.hosts.unknown_write_failure')
            log_buffer.log(
                'Hosts', f'Elevated hosts cleanup child could not update the hosts file: {detail}'
            )
            return HOSTS_CLEANUP_WRITE_FAILED_EXIT
        return 0

    try:
        result = call_compatibility_boundary(_perform_cleanup)
    except CompatibilityBoundaryError as wrapped:
        exc = wrapped.cause
        log_buffer.log('Hosts', f'Elevated hosts cleanup child crashed: {exc!r}')
        return HOSTS_CLEANUP_UNEXPECTED_EXIT
    if result == 0:
        log_buffer.log('Hosts', 'Elevated one-shot hosts cleanup completed')
    return result


def run_privileged_hosts_cleanup(parent: QWidget | None = None) -> bool:
    """Run the short-lived administrator/root child used for Env Proxy repair."""
    if is_admin():
        return cleanup_hosts_once() == 0

    if sys.platform.startswith('linux'):
        linux_proxy_helper = importlib.import_module('fleasion.utils.linux_proxy_helper')
        return linux_proxy_helper.cleanup_hosts_with_pkexec()

    completion: RelaunchCompletion = {}
    completed = relaunch_as_admin(
        extra_args='--cleanup-hosts',
        parent_hwnd=window_handle(parent),
        wait_for_completion=True,
        wait_timeout_ms=int(WINDOWS_HOSTS_CLEANUP_TIMEOUT_SECONDS * 1000),
        completion=completion,
    )
    if completed:
        return True

    exit_code = completion.get('exit_code')
    if completion.get('wait_result') == WINDOWS_WAIT_TIMEOUT:
        log_buffer.log(
            'Hosts',
            'Privileged cleanup child is still running after the extended wait; '
            'the hosts file may still be under repair',
        )
        return False
    reasons = {
        HOSTS_CLEANUP_NOT_ADMIN_EXIT: 'the child did not receive an administrator token',
        HOSTS_CLEANUP_WRITE_FAILED_EXIT: 'Windows or security software blocked the hosts write',
        HOSTS_CLEANUP_UNEXPECTED_EXIT: 'the cleanup child raised an unexpected exception',
    }
    if exit_code in reasons:
        log_buffer.log('Hosts', f'Privileged cleanup failed because {reasons[exit_code]}')
    elif exit_code is not None:
        log_buffer.log(
            'Hosts',
            f'Privileged cleanup child exited before reporting a known outcome (exit={exit_code})',
        )
    return False


def repair_autostart_once(requesting_user_sid: str | None = None, *, enabled: bool = True) -> int:
    """Repair or remove Windows autostart from a one-shot elevated process."""
    if sys.platform != 'win32' or not is_admin():
        log_buffer.log(
            'Autostart', 'Elevated autostart repair rejected: administrator access is required'
        )
        return 1

    autostart = importlib.import_module('fleasion.utils.autostart')
    windows_user_id = None
    if enabled:
        windows_permissions = importlib.import_module('fleasion.utils.windows_permissions')
        if not requesting_user_sid:
            log_buffer.log('Autostart', 'Elevated autostart repair has no requesting user identity')
            return 1

        try:
            windows_user_id = windows_permissions.windows_user_id_from_sid(requesting_user_sid)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log_buffer.log('Autostart', f'Invalid requesting Windows identity: {exc}')
            return 1

    try:
        proxy_mode = ConfigManager().proxy_mode
    except OSError, RuntimeError, TypeError, ValueError:
        proxy_mode = None
    if autostart.sync_autostart(
        enabled=enabled,
        config_dir=CONFIG_DIR,
        windows_user_id=windows_user_id,
        proxy_mode=proxy_mode,
    ):
        log_buffer.log(
            'Autostart',
            'Elevated autostart repair completed'
            if enabled
            else 'Elevated legacy autostart-task removal completed',
        )
        return 0

    log_buffer.log(
        'Autostart',
        'Elevated autostart repair failed'
        if enabled
        else 'Elevated legacy autostart-task removal failed',
    )
    return 1


def repair_roblox_permissions_once(requesting_user_sid: str | None = None) -> int:
    """Apply a pending targeted Roblox ACL repair from a one-shot UAC child."""
    from fleasion.utils.windows_permissions import (  # ruff: ignore[import-outside-top-level]
        clear_pending_repair,
        grant_current_user_modify_access,
        read_pending_repair,
        write_repair_result,
    )

    if sys.platform != 'win32' or not is_admin():
        log_buffer.log(
            'RobloxPermissions',
            'Elevated Roblox ACL repair rejected: administrator access is required',
        )
        return 1

    if not requesting_user_sid:
        log_buffer.log(
            'RobloxPermissions',
            'Elevated Roblox ACL repair has no requesting user identity',
        )
        return 1

    paths = read_pending_repair(CONFIG_DIR)
    if not paths:
        result: ErrorDetails = {
            'ok': False,
            'granted': [],
            'failed': [],
            'error': 'No pending Roblox installation permission repair was found',
        }
    else:
        try:
            result = call_compatibility_boundary(
                lambda: grant_current_user_modify_access(
                    paths,
                    user_sid=requesting_user_sid,
                )
            )
        except CompatibilityBoundaryError as wrapped:
            exc = wrapped.cause
            result = {
                'ok': False,
                'granted': [],
                'failed': [],
                'error': f'Unexpected ACL repair error: {exc}',
            }

    try:
        write_repair_result(result, CONFIG_DIR)
    finally:
        clear_pending_repair(CONFIG_DIR)

    if result.get('ok'):
        log_buffer.log('RobloxPermissions', 'Elevated Roblox ACL repair completed')
        return 0
    log_buffer.log('RobloxPermissions', f'Elevated Roblox ACL repair failed: {result}')
    return 1


def repair_windows_firewall_once() -> int:
    """Apply a pending Fleasion firewall repair from a one-shot UAC child."""
    from fleasion.utils.windows_firewall import (  # ruff: ignore[import-outside-top-level]
        clear_pending_repair,
        install_fleasion_firewall_rules,
        read_pending_repair,
        write_repair_result,
    )

    if sys.platform != 'win32' or not is_admin():
        result: ErrorDetails = {
            'ok': False,
            'rules': [],
            'failed': [],
            'error': 'Administrator permission is required to update Windows Firewall',
        }
    elif not read_pending_repair(CONFIG_DIR):
        result = {
            'ok': False,
            'rules': [],
            'failed': [],
            'error': 'No pending Fleasion firewall repair was found',
        }
    else:
        try:
            result = call_compatibility_boundary(install_fleasion_firewall_rules)
        except CompatibilityBoundaryError as wrapped:
            exc = wrapped.cause
            result = {
                'ok': False,
                'rules': [],
                'failed': [],
                'error': f'Unexpected Windows Firewall repair error: {exc}',
            }

    try:
        write_repair_result(result, CONFIG_DIR)
    finally:
        clear_pending_repair(CONFIG_DIR)
    log_buffer.log(
        'WindowsFirewall',
        'Elevated Fleasion firewall repair completed'
        if result.get('ok')
        else f'Elevated Fleasion firewall repair failed: {result}',
    )
    return 0
