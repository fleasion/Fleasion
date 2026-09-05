"""Hosts-file repair prompts and platform-specific guidance."""

from __future__ import annotations

import html
import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
)

from fleasion.app.dialogs.common import visible_parent_widget
from fleasion.app.error_details import (
    ErrorDetails,
    get_int_detail as _get_int_detail,
    is_object_list as _is_object_list,
)
from fleasion.app.repairs import run_privileged_hosts_cleanup
from fleasion.localization import tr
from fleasion.utils import (
    APP_DISCORD,
    get_icon_path,
    log_buffer,
    open_folder,
)

if TYPE_CHECKING:
    from fleasion.app.compatibility import VoidCallback


def show_oversized_hosts_file_dialog(
    details: ErrorDetails, on_repaired: VoidCallback | None = None
) -> bool:
    """Offer a streaming repair for an abnormally large system hosts file."""

    hosts_path = str(details.get('hosts_path') or r'C:\Windows\System32\drivers\etc\hosts')
    hosts_directory = str(
        details.get('hosts_directory')
        or Path(hosts_path).parent
        or r'C:\Windows\System32\drivers\etc'
    )
    size = _get_int_detail(details, 'hosts_size_bytes', 0)
    limit = _get_int_detail(details, 'hosts_size_limit_bytes', 512 * 1024)
    size_mib = size / (1024 * 1024)
    limit_kib = limit / 1024
    parent = visible_parent_widget()

    while True:
        msg = QMessageBox(parent)
        msg.setWindowTitle(tr('app.fleasion_hosts_file_is_abnormally_large'))
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(tr('app.your_system_hosts_file_is_abnormally_large', value0=size_mib))
        msg.setInformativeText(
            tr('app.fleasion_will_not_load_hosts_files_larger', value0=limit_kib)
        )
        repair_button = msg.addButton(
            tr('app.attempt_safe_repair_recommended'), QMessageBox.ButtonRole.AcceptRole
        )
        open_dir_button = msg.addButton(
            tr('app.open_hosts_directory'), QMessageBox.ButtonRole.ActionRole
        )
        cancel_button = msg.addButton(tr('app.cancel'), QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(repair_button)
        msg.exec()

        if msg.clickedButton() == open_dir_button:
            try:
                open_folder(Path(hosts_directory))
            except OSError as exc:
                log_buffer.log('Hosts', f'Could not open hosts directory: {exc}')
            continue
        if msg.clickedButton() != repair_button:
            log_buffer.log('Hosts', 'User declined oversized hosts file repair')
            return False

        repair_button.setEnabled(False)
        open_dir_button.setEnabled(False)
        cancel_button.setEnabled(False)
        msg.setText(tr('app.waiting_for_administrator_permission'))
        msg.setInformativeText(tr('app.approve_the_operating_system_permission_prompt_fleasion'))
        msg.show()
        msg.raise_()
        msg.activateWindow()
        QApplication.processEvents()
        try:
            repaired = run_privileged_hosts_cleanup(msg)
        finally:
            msg.hide()

        if repaired:
            proxy_master = importlib.import_module('fleasion.proxy.master')
            repaired_size = proxy_master.hosts_file_size()
            if (
                repaired_size is not None
                and repaired_size <= limit
                and not proxy_master.has_stale_hosts_entries()
            ):
                log_buffer.log('Hosts', 'Verified oversized hosts file was repaired successfully')
                if on_repaired is not None:
                    on_repaired()
                return True

        failure_details = dict(details)
        failure_details.update(
            {
                'error_code': 'hosts_file_repair_failed',
                'error': tr('app.hosts.oversized.repair_failed_manual'),
            }
        )
        log_buffer.log('Hosts', 'Safe repair did not produce a usable hosts file')
        show_hosts_write_exhausted_dialog(failure_details)
        return False


def show_hosts_capacity_dialog(details: ErrorDetails) -> None:
    """Explain that a normal-sized hosts file cannot fit new mappings safely."""

    hosts_path = str(details.get('hosts_path') or r'C:\Windows\System32\drivers\etc\hosts')
    hosts_directory = str(
        details.get('hosts_directory')
        or Path(hosts_path).parent
        or r'C:\Windows\System32\drivers\etc'
    )
    limit = _get_int_detail(details, 'hosts_size_limit_bytes', 512 * 1024)
    candidate_size = _get_int_detail(details, 'hosts_size_bytes', 0)
    msg = QMessageBox(visible_parent_widget())
    msg.setWindowTitle(tr('app.fleasion_hosts_file_near_safety_limit'))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(tr('app.fleasion_did_not_modify_your_hosts_file'))
    msg.setInformativeText(
        tr(
            'app.adding_fleasion_entries_would_make_it_value',
            value0=candidate_size / (1024 * 1024),
            value1=limit / 1024,
            value2=hosts_path,
        )
    )
    open_dir_button = msg.addButton(
        tr('app.open_hosts_directory'), QMessageBox.ButtonRole.ActionRole
    )
    msg.addButton(QMessageBox.StandardButton.Ok)
    msg.exec()
    if msg.clickedButton() == open_dir_button:
        try:
            open_folder(Path(hosts_directory))
        except OSError as exc:
            log_buffer.log('Hosts', f'Could not open hosts directory: {exc}')


def show_env_proxy_stale_hosts_dialog() -> bool:
    """Offer a one-shot privileged repair for oversized or stale Env Proxy hosts entries."""
    proxy_master = importlib.import_module('fleasion.proxy.master')

    if proxy_master.other_proxy_owner_alive():
        log_buffer.log(
            'Hosts',
            'Skipped Env Proxy stale hosts prompt because another proxy owns the hosts file',
        )
        return True
    oversized_details: ErrorDetails = {}
    if proxy_master.hosts_file_is_oversized(oversized_details):
        return show_oversized_hosts_file_dialog(oversized_details)
    if not proxy_master.has_stale_hosts_entries(set(proxy_master.INTERCEPT_HOSTS)):
        return True

    parent = visible_parent_widget()
    msg = QMessageBox(parent)
    msg.setWindowTitle(tr('app.fleasion_stale_hosts_file_entries'))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(tr('app.fleasion_found_stale_env_proxy_entries_in'))
    msg.setInformativeText(tr('app.the_hosts_file_is_protected_by_the'))
    fix_button = msg.addButton(
        tr('app.fix_hosts_file_recommended'), QMessageBox.ButtonRole.AcceptRole
    )
    continue_button = msg.addButton(
        tr('app.continue_without_fixing'), QMessageBox.ButtonRole.RejectRole
    )
    msg.setDefaultButton(fix_button)
    msg.exec()

    if msg.clickedButton() != fix_button:
        log_buffer.log('Hosts', 'User deferred privileged Env Proxy stale hosts cleanup')
        return True

    fix_button.setEnabled(False)
    continue_button.setEnabled(False)
    if sys.platform == 'win32':
        msg.setText(tr('app.waiting_for_windows_administrator_permission'))
        msg.setInformativeText(tr('app.approve_the_uac_prompt_to_remove_the'))
    else:
        msg.setText(tr('app.waiting_for_administrator_root_permission'))
        msg.setInformativeText(tr('app.approve_the_operating_system_permission_prompt_to'))
    msg.show()
    msg.raise_()
    msg.activateWindow()
    QApplication.processEvents()
    try:
        repaired = run_privileged_hosts_cleanup(msg)
    finally:
        msg.hide()

    if repaired:
        if not proxy_master.has_stale_hosts_entries(set(proxy_master.INTERCEPT_HOSTS)):
            log_buffer.log('Hosts', 'Verified stale Env Proxy hosts entries were removed')
            return True
        detail = tr('app.hosts.stale.cleanup_still_present')
    else:
        detail = tr('app.hosts.stale.cleanup_failed')

    log_buffer.log('Hosts', f'Privileged Env Proxy stale hosts cleanup failed: {detail}')
    QMessageBox.warning(
        parent,
        tr('app.fleasion_hosts_file_still_needs_repair'),
        tr('app.value_env_proxy_may_start_but_the', value0=detail),
    )
    return True


def show_hosts_write_exhausted_dialog(details: ErrorDetails) -> None:
    """Show a user-facing popup when hosts writes fail after all retries."""

    default_hosts_path = (
        '/etc/hosts'
        if sys.platform == 'darwin' or sys.platform.startswith('linux')
        else r'C:\Windows\System32\drivers\etc\hosts'
    )
    default_hosts_dir = (
        '/etc'
        if sys.platform == 'darwin' or sys.platform.startswith('linux')
        else r'C:\Windows\System32\drivers\etc'
    )
    hosts_path = str(details.get('hosts_path') or default_hosts_path)
    hosts_directory = str(
        details.get('hosts_directory') or Path(hosts_path).parent or default_hosts_dir
    )
    raw_error = str(details.get('error') or '').strip()

    top = QApplication.topLevelWidgets()
    parent = next((w for w in top if w.isVisible()), None)
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )

    discord_url = APP_DISCORD
    if not discord_url.startswith(('http://', 'https://')):
        discord_url = f'https://{discord_url}'

    while True:
        msg = QMessageBox(parent)
        if on_top:
            msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        msg.setWindowTitle(tr('app.hosts_file_write_failed'))
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(tr('app.fleasion_could_not_modify_the_system_hosts'))

        diagnostics_html = ''
        if raw_error:
            diagnostics_html = tr('app.hosts.write_failed.technical', error=html.escape(raw_error))

        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setInformativeText(
            tr(
                'app.most_likely_causes_br_a_antivirus_security',
                value0=html.escape(hosts_path),
                value1=diagnostics_html,
                value2=html.escape(discord_url),
                value3=html.escape(APP_DISCORD),
            )
        )

        open_dir_button = msg.addButton(
            tr('app.click_here_to_open_directory'), QMessageBox.ButtonRole.ActionRole
        )
        msg.addButton(QMessageBox.StandardButton.Ok)

        if icon_path := get_icon_path():
            msg.setWindowIcon(QIcon(str(icon_path)))

        for label in msg.findChildren(QLabel):
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextBrowserInteraction
                | Qt.TextInteractionFlag.TextSelectableByMouse
            )
            label.setOpenExternalLinks(True)

        msg.exec()

        if msg.clickedButton() == open_dir_button:
            try:
                open_folder(Path(hosts_directory))
            except OSError as exc:
                log_buffer.log('Hosts', f'Could not open hosts directory: {exc}')
            continue
        break


def linux_hosts_nix_snippet(details: ErrorDetails) -> str:
    default_hosts = (
        'apis.roblox.com',
        'assetdelivery.roblox.com',
        'contentdelivery.roblox.com',
        'fts.rbxcdn.com',
        'gamejoin.roblox.com',
    )
    hosts_value = details.get('hosts')
    raw_hosts = hosts_value if _is_object_list(hosts_value) else default_hosts
    hosts = sorted({str(host).strip().lower() for host in raw_hosts if str(host).strip()})
    if not hosts:
        hosts = [
            'apis.roblox.com',
            'assetdelivery.roblox.com',
            'contentdelivery.roblox.com',
            'fts.rbxcdn.com',
            'gamejoin.roblox.com',
        ]

    extra_hosts = '\n'.join(f'  127.0.0.1 {host}' for host in hosts)
    return "networking.extraHosts =\n''\n" + extra_hosts + "\n'';"


def show_linux_hosts_read_only_dialog(details: ErrorDetails) -> None:
    """Show Nix/NixOS guidance when /etc/hosts cannot be edited at runtime."""
    nix_snippet = linux_hosts_nix_snippet(details)
    raw_error = str(details.get('error') or '').strip()
    hosts_path = str(details.get('hosts_path') or '/etc/hosts')

    top = QApplication.topLevelWidgets()
    parent = next((w for w in top if w.isVisible()), None)
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )

    msg = QMessageBox(parent)
    if on_top:
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    msg.setWindowTitle(tr('app.read_only_hosts_file'))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(tr('app.fleasion_cannot_edit_the_linux_hosts_file'))
    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setInformativeText(
        tr(
            'app.the_proxy_helper_got_a_read_only',
            value0=html.escape(hosts_path),
            value1=html.escape(nix_snippet),
            value2=(
                tr('app.linux_hosts_readonly.technical', error=html.escape(raw_error))
                if raw_error
                else ''
            ),
        )
    )
    copy_button = msg.addButton(tr('app.copy_nix_snippet'), QMessageBox.ButtonRole.ActionRole)
    msg.addButton(QMessageBox.StandardButton.Ok)

    def _copy_nix_snippet() -> None:
        QApplication.clipboard().setText(nix_snippet)
        copy_button.setText(tr('app.copied'))
        log_buffer.log('Hosts', 'Copied Nix extraHosts snippet to clipboard')

    copy_button.clicked.connect(_copy_nix_snippet)

    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))

    for label in msg.findChildren(QLabel):
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )

    msg.exec()
