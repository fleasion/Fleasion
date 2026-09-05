"""Roblox permission and Windows Firewall repair prompts."""

from __future__ import annotations

import importlib
import sys
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMessageBox,
    QWidget,
)

from fleasion.app.dialogs.common import visible_parent_widget, window_handle
from fleasion.app.elevation import relaunch_as_admin
from fleasion.app.error_details import (
    ErrorDetails,
    is_object_list as _is_object_list,
)
from fleasion.app.process_control import (
    resolve_executable as _resolve_executable,
)
from fleasion.app.restart import spawn_trusted_command
from fleasion.localization import tr
from fleasion.utils import (
    APP_DISCORD,
    CONFIG_DIR,
    get_icon_path,
    log_buffer,
    run_in_thread,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fleasion.app.compatibility import VoidCallback
    from fleasion.modifications import ModificationManager


WINDOWS_REPAIR_RESULT_TIMEOUT_SECONDS = 120.0


WINDOWS_FIREWALL_REPAIR_RESULT_TIMEOUT_SECONDS = 120.0


def show_roblox_permission_failure(
    parent: QWidget | None,
    denied_dirs: Iterable[Path],
    mod_manager: ModificationManager | None = None,
    *,
    on_repaired: VoidCallback | None = None,
    failure_text: str | None = None,
) -> None:
    """Ask before permanently granting this Windows user access to failed installs."""
    if sys.platform != 'win32':
        return
    paths = sorted(
        {Path(path).resolve() for path in denied_dirs}, key=lambda value: str(value).lower()
    )
    if not paths:
        return

    msg = QMessageBox(parent)
    msg.setWindowTitle(tr('app.roblox_installation_permission_required'))
    msg.setIcon(QMessageBox.Icon.Warning)
    listed_paths = '\n'.join(tr('app.common.bullet_path', path=path) for path in paths)
    failure_text = failure_text or tr('app.roblox_permissions.default_failure')
    msg.setText(
        tr(
            'app.value_value_would_you_like_to_permanently',
            value0=failure_text,
            value1=listed_paths,
        )
    )
    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))

    grant_button = msg.addButton(
        tr('app.grant_access_for_this_windows_user'), QMessageBox.ButtonRole.AcceptRole
    )
    ignore_button = msg.addButton(tr('app.ignore'), QMessageBox.ButtonRole.RejectRole)
    msg.setDefaultButton(ignore_button)
    msg.exec()

    if msg.clickedButton() != grant_button:
        return

    windows_permissions = importlib.import_module('fleasion.utils.windows_permissions')
    clear_pending_repair = windows_permissions.clear_pending_repair
    clear_repair_result = windows_permissions.clear_repair_result
    write_pending_repair = windows_permissions.write_pending_repair

    try:
        clear_repair_result(CONFIG_DIR)
        if not write_pending_repair(paths, CONFIG_DIR):
            msg_0 = 'No valid Roblox installation folders were selected'
            raise OSError(msg_0)
        relaunched = relaunch_as_admin(
            extra_args='--repair-roblox-permissions',
            parent_hwnd=window_handle(parent),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        clear_pending_repair(CONFIG_DIR)
        log_buffer.log('RobloxPermissions', f'Could not start elevated ACL repair: {exc}')
        return

    if relaunched:
        log_buffer.log(
            'RobloxPermissions',
            'Elevated ACL repair started for the selected Roblox installations',
        )
        if mod_manager is not None:
            deadline = time.monotonic() + WINDOWS_REPAIR_RESULT_TIMEOUT_SECONDS
            QTimer.singleShot(
                500,
                lambda: poll_roblox_permission_repair(
                    mod_manager,
                    deadline,
                    on_repaired=on_repaired,
                ),
            )
        elif on_repaired is not None:
            deadline = time.monotonic() + WINDOWS_REPAIR_RESULT_TIMEOUT_SECONDS
            QTimer.singleShot(
                500,
                lambda: poll_roblox_permission_repair(
                    None,
                    deadline,
                    on_repaired=on_repaired,
                ),
            )
    else:
        clear_pending_repair(CONFIG_DIR)


def poll_roblox_permission_repair(
    mod_manager: ModificationManager | None,
    deadline: float,
    *,
    on_repaired: VoidCallback | None = None,
) -> None:
    """Consume a one-shot elevated ACL result and retry the normal write path."""
    from fleasion.utils.windows_permissions import (  # ruff: ignore[import-outside-top-level]
        clear_pending_repair,
        clear_repair_result,
        read_repair_result,
    )

    result = read_repair_result(CONFIG_DIR)
    if result is None:
        if time.monotonic() < deadline:
            QTimer.singleShot(
                500,
                lambda: poll_roblox_permission_repair(
                    mod_manager,
                    deadline,
                    on_repaired=on_repaired,
                ),
            )
            return
        clear_pending_repair(CONFIG_DIR)
        clear_repair_result(CONFIG_DIR)
        log_buffer.log(
            'RobloxPermissions',
            'Timed out waiting for the elevated Roblox permission repair',
        )
        QMessageBox.warning(
            visible_parent_widget(),
            tr('app.roblox_permission_repair_timed_out'),
            tr('app.fleasion_did_not_receive_a_result_from'),
        )
        return

    clear_repair_result(CONFIG_DIR)
    clear_pending_repair(CONFIG_DIR)
    if result.get('ok'):
        granted = result.get('granted')
        granted_count = len(granted) if _is_object_list(granted) else 0
        log_buffer.log(
            'RobloxPermissions',
            f'Granted Modify access to {granted_count} Roblox installation(s)',
        )
        if mod_manager is not None:
            run_in_thread(mod_manager.reapply_all)()
        if on_repaired is not None:
            on_repaired()
        return

    detail = (
        result.get('error')
        or result.get('failed')
        or tr('app.roblox_permissions.acl_update_failed')
    )
    log_buffer.log('RobloxPermissions', f'ACL repair failed: {detail}')
    msg = QMessageBox(visible_parent_widget())
    msg.setWindowTitle(tr('app.roblox_permission_repair_failed'))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(tr('app.fleasion_could_not_update_the_permissions_for', value0=detail))
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg.exec()


def poll_windows_firewall_repair(deadline: float) -> None:
    """Consume a one-shot elevated firewall result and explain the outcome."""
    from fleasion.utils.windows_firewall import (  # ruff: ignore[import-outside-top-level]
        clear_pending_repair,
        clear_repair_result,
        read_repair_result,
    )

    result = read_repair_result(CONFIG_DIR)
    if result is None:
        if time.monotonic() < deadline:
            QTimer.singleShot(500, lambda: poll_windows_firewall_repair(deadline))
            return
        clear_pending_repair(CONFIG_DIR)
        clear_repair_result(CONFIG_DIR)
        log_buffer.log('WindowsFirewall', 'Timed out waiting for the elevated firewall repair')
        QMessageBox.warning(
            visible_parent_widget(),
            tr('app.fleasion_firewall_repair_timed_out'),
            tr('app.fleasion_did_not_receive_a_result_from_2'),
        )
        return

    clear_repair_result(CONFIG_DIR)
    clear_pending_repair(CONFIG_DIR)
    if result.get('ok'):
        rules = result.get('rules')
        rule_count = len(rules) if _is_object_list(rules) else 0
        log_buffer.log(
            'WindowsFirewall',
            f'Added {rule_count} Fleasion firewall rule(s)',
        )
        QMessageBox.information(
            visible_parent_widget(),
            tr('app.fleasion_firewall_updated'),
            tr('app.windows_firewall_now_allows_fleasion_on_private'),
        )
        return

    detail = result.get('error') or result.get('failed') or tr('app.firewall.rules_update_failed')
    log_buffer.log('WindowsFirewall', f'Firewall repair failed: {detail}')
    QMessageBox.warning(
        visible_parent_widget(),
        tr('app.fleasion_firewall_repair_failed'),
        tr('app.fleasion_could_not_update_windows_firewall_value', value0=detail),
    )


def show_windows_upstream_firewall_dialog(details: ErrorDetails) -> None:
    """Explain a blocked upstream connection and offer a targeted UAC repair."""
    if sys.platform != 'win32':
        return

    windows_firewall = importlib.import_module('fleasion.utils.windows_firewall')

    host = str(details.get('host') or tr('app.firewall.default_content_server'))
    parent = visible_parent_widget()
    try:
        firewall_status = windows_firewall.get_fleasion_firewall_rule_status()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log_buffer.log('WindowsFirewall', f'Could not inspect Fleasion firewall rules: {exc}')
        firewall_status = {'ok': False}

    rules_already_present = bool(firewall_status.get('ok'))
    msg = QMessageBox(parent)
    msg.setWindowTitle(
        tr('app.fleasion_connection_still_blocked')
        if rules_already_present
        else tr('app.fleasion_connection_blocked')
    )
    msg.setIcon(QMessageBox.Icon.Warning)
    if rules_already_present:
        msg.setText(tr('app.fleasion_still_cannot_connect_securely_to_value', value0=host))
        msg.setInformativeText(tr('app.fleasion_s_windows_firewall_rules_are_already'))
        repair_button = None
        help_button = msg.addButton(
            tr('app.get_help_on_discord'), QMessageBox.ButtonRole.ActionRole
        )
    else:
        msg.setText(tr('app.fleasion_could_not_connect_securely_to_value', value0=host))
        msg.setInformativeText(tr('app.fleasion_needs_to_reach_roblox_over_https'))
        repair_button = msg.addButton(
            tr('app.allow_fleasion_through_firewall'), QMessageBox.ButtonRole.AcceptRole
        )
        help_button = None
    settings_button = msg.addButton(
        tr('app.open_firewall_settings'), QMessageBox.ButtonRole.ActionRole
    )
    msg.addButton(tr('app.not_now'), QMessageBox.ButtonRole.RejectRole)
    msg.setDefaultButton(help_button or settings_button)
    msg.exec()

    if help_button is not None and msg.clickedButton() == help_button:
        discord_url = APP_DISCORD
        if not discord_url.startswith(('http://', 'https://')):
            discord_url = f'https://{discord_url}'
        webbrowser.open(discord_url)
        return

    if msg.clickedButton() == repair_button:
        clear_pending_repair = windows_firewall.clear_pending_repair
        clear_repair_result = windows_firewall.clear_repair_result
        write_pending_repair = windows_firewall.write_pending_repair

        try:
            clear_repair_result(CONFIG_DIR)
            write_pending_repair(CONFIG_DIR)
            relaunched = relaunch_as_admin(
                extra_args='--repair-firewall',
                parent_hwnd=window_handle(parent),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            clear_pending_repair(CONFIG_DIR)
            log_buffer.log('WindowsFirewall', f'Could not start elevated firewall repair: {exc}')
            relaunched = False

        if relaunched:
            log_buffer.log('WindowsFirewall', 'Elevated Fleasion firewall repair started')
            deadline = time.monotonic() + WINDOWS_FIREWALL_REPAIR_RESULT_TIMEOUT_SECONDS
            QTimer.singleShot(500, lambda: poll_windows_firewall_repair(deadline))
        else:
            clear_pending_repair(CONFIG_DIR)
            QMessageBox.warning(
                parent,
                tr('app.fleasion_firewall_repair_not_started'),
                tr('app.fleasion_could_not_obtain_administrator_permission_so'),
            )
        return

    if msg.clickedButton() == settings_button:
        try:
            spawn_trusted_command(
                [
                    _resolve_executable('control.exe'),
                    '/name',
                    'Microsoft.WindowsFirewall',
                    '/page',
                    'pageConfigureApps',
                ]
            )
        except OSError as exc:
            QMessageBox.warning(
                msg.parentWidget(),
                tr('app.fleasion'),
                tr('app.could_not_open_firewall_settings_value', value0=exc),
            )
        return
