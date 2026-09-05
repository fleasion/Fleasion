"""Proxy startup failure dialogs and the main-thread error bridge."""

from __future__ import annotations

import html
import importlib
import ipaddress
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
)

from fleasion.app.dialogs.common import visible_parent_widget
from fleasion.app.dialogs.hosts import (
    show_hosts_capacity_dialog,
    show_hosts_write_exhausted_dialog,
    show_linux_hosts_read_only_dialog,
    show_oversized_hosts_file_dialog,
)
from fleasion.app.dialogs.permissions import (
    show_roblox_permission_failure,
    show_windows_upstream_firewall_dialog,
)
from fleasion.app.error_details import (
    ErrorDetails,
    get_int_detail as _get_int_detail,
    is_error_details as _is_error_details,
    is_object_list as _is_object_list,
)
from fleasion.localization import tr
from fleasion.utils import (
    APP_DISCORD,
    get_icon_path,
    log_buffer,
    open_folder,
)

if TYPE_CHECKING:
    from fleasion.app.tray import SystemTray
    from fleasion.config import ConfigManager


UNSPECIFIED_LOCAL_ADDRESS = str(ipaddress.IPv4Address(0))


def show_proxy_bind_error_dialog(details: ErrorDetails) -> None:
    """Show a user-facing popup when Fleasion cannot bind its proxy port."""
    port = _get_int_detail(details, 'port', 443)
    owners_value = details.get('owners')
    owners = owners_value if _is_object_list(owners_value) else []
    bind_reason = str(details.get('bind_reason') or '')

    top = QApplication.topLevelWidgets()
    parent = next((w for w in top if w.isVisible()), None)
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )

    msg = QMessageBox(parent)
    if on_top:
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    msg.setWindowTitle(tr('app.proxy_port_conflict'))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(tr('app.fleasion_could_not_start_its_local_proxy', value0=port))

    if bind_reason == 'access_denied_or_reserved':
        owners_html = tr('app.proxy_bind.access_denied', port=port)
    else:
        owner_lines = [
            tr(
                'app.proxy_bind.owner_entry',
                process_name=html.escape(
                    str(owner.get('process_name') or tr('app.common.unknown'))
                ),
                pid=_get_int_detail(owner, 'pid', 0),
                local_address=html.escape(
                    str(owner.get('local_address') or UNSPECIFIED_LOCAL_ADDRESS)
                ),
                port=port,
            )
            for owner in owners
            if _is_error_details(owner)
        ]
        owners_html = (
            tr('app.proxy_bind.owners', port=port, owner_lines='<br>'.join(owner_lines))
            if owner_lines
            else tr('app.proxy_bind.other_owner', port=port)
        )

    discord_url = APP_DISCORD
    if not discord_url.startswith(('http://', 'https://')):
        discord_url = f'https://{discord_url}'

    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setInformativeText(
        tr(
            'app.value_close_the_conflicting_process_then_relaunch',
            value0=owners_html,
            value1=html.escape(discord_url),
            value2=html.escape(APP_DISCORD),
        )
    )
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))

    for label in msg.findChildren(QLabel):
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        label.setOpenExternalLinks(True)

    msg.exec()


def show_macos_ca_patch_failed_dialog(details: ErrorDetails) -> str | None:
    """Show a user-facing popup when Roblox cacert.pem cannot be verified."""
    top = QApplication.topLevelWidgets()
    parent = next((w for w in top if w.isVisible()), None)
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )

    failed = details.get('failed')
    verified = details.get('verified')
    failed_items = failed if _is_object_list(failed) else []
    verified_items = verified if _is_object_list(verified) else []
    failed_lines: list[str] = []
    for item in failed_items[:6]:
        if _is_error_details(item):
            path = item.get('ca_file') or item.get('resource_dir') or tr('app.common.unknown_path')
            error = item.get('error') or item.get('status') or tr('app.common.unknown_error')
            failed_lines.append(
                tr(
                    'app.common.bullet_path_error',
                    path=html.escape(str(path)),
                    error=html.escape(str(error)),
                )
            )

    unhealthy_lines: list[str] = []
    for item in verified_items[:6]:
        if _is_error_details(item):
            if item.get('healthy'):
                continue
            path = item.get('path') or tr('app.common.unknown_path')
            error = item.get('error') or tr('app.common.verification_failed')
            unhealthy_lines.append(
                tr(
                    'app.common.bullet_path_error',
                    path=html.escape(str(path)),
                    error=html.escape(str(error)),
                )
            )

    diagnostics_html = ''
    if failed_lines or unhealthy_lines:
        diagnostics_html = tr(
            'app.macos_ca_patch.diagnostics', lines='<br>'.join(failed_lines + unhealthy_lines)
        )

    msg = QMessageBox(parent)
    if on_top:
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    msg.setWindowTitle(tr('app.macos_roblox_ca_patch_failed'))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(tr('app.fleasion_could_not_verify_roblox_ssl_trust'))
    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setInformativeText(
        tr('app.roblox_would_reject_fleasion_proxy_certificates_until', value0=diagnostics_html)
    )
    install_button = None
    if details.get('helper_required'):
        install_button = msg.addButton(
            tr('app.install_helper_and_retry'), QMessageBox.ButtonRole.AcceptRole
        )
        msg.addButton(tr('app.not_now'), QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(install_button)
    else:
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))

    for label in msg.findChildren(QLabel):
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )

    msg.exec()
    return (
        'install_helper'
        if install_button is not None and msg.clickedButton() == install_button
        else None
    )


def show_macos_ca_trust_failed_dialog(details: ErrorDetails) -> None:
    """Explain why launcher traffic cannot be intercepted safely."""
    raw_error = html.escape(
        str(details.get('error') or tr('app.macos_ca_trust.unknown_helper_error'))
    )
    top = QApplication.topLevelWidgets()
    parent = next((w for w in top if w.isVisible()), None)

    msg = QMessageBox(parent)
    msg.setWindowTitle(tr('app.macos_launcher_ca_trust_failed'))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(tr('app.fleasion_could_not_establish_macos_trust_for'))
    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setInformativeText(
        tr('app.froststrap_and_appleblox_can_contact_intercepted_roblox', value0=raw_error)
    )
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))
    msg.exec()


def show_roblox_ca_patch_failed_dialog(details: ErrorDetails) -> None:
    failed = details.get('failed')
    failed_items = failed if _is_object_list(failed) else []
    lines: list[str] = []
    for item in failed_items[:8]:
        if not _is_error_details(item):
            continue
        item_details = item
        path = (
            item_details.get('ca_file')
            or item_details.get('resource_dir')
            or tr('app.common.unknown_path')
        )
        error = (
            item_details.get('error')
            or item_details.get('status')
            or tr('app.common.verification_failed')
        )
        lines.append(tr('app.common.path_error', path=path, error=error))
    diagnostics = '\n'.join(lines) or str(
        details.get('error') or tr('app.roblox_ca_protected.no_writable')
    )
    QMessageBox.critical(
        visible_parent_widget(),
        tr('app.fleasion_proxy_startup_failed'),
        tr('app.fleasion_could_not_prepare_roblox_player_for', value0=diagnostics),
    )


def windows_ca_permission_denied_dirs(details: ErrorDetails) -> list[Path]:
    """Return install directories whose CA patch failed due to Windows ACLs."""
    if sys.platform != 'win32':
        return []

    denied: set[Path] = set()
    failed = details.get('failed')
    failed_items = failed if _is_object_list(failed) else []
    for item in failed_items:
        if not _is_error_details(item):
            continue
        error = str(item.get('error') or '').lower()
        if not any(
            marker in error
            for marker in ('permission denied', 'access is denied', 'winerror 5', 'errno 13')
        ):
            continue
        resource_dir = item.get('resource_dir')
        if resource_dir:
            denied.add(Path(resource_dir))
            continue
        ca_file = item.get('ca_file')
        if ca_file:
            path = Path(ca_file)
            denied.add(path.parent.parent if path.parent.name.lower() == 'ssl' else path.parent)

    return sorted(denied, key=lambda path: str(path).lower())


def show_macos_relay_failed_dialog(details: ErrorDetails) -> str:
    """Explain a failed privileged relay and return the requested recovery action."""
    lazy_module = importlib.import_module('fleasion.utils.macos_proxy_helper')
    helper_log_dir = lazy_module.HELPER_LOG_DIR

    top = QApplication.topLevelWidgets()
    parent = next((w for w in top if w.isVisible()), None)
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )

    backend_probe_value = details.get('backend_probe')
    backend_probe = backend_probe_value if _is_error_details(backend_probe_value) else {}
    reachable = bool(backend_probe.get('reachable'))
    if reachable:
        probe_html = tr('app.macos_relay.reachable')
    else:
        error_type = html.escape(
            str(backend_probe.get('error_type') or tr('app.macos_relay.connection_error'))
        )
        error_text = html.escape(
            str(backend_probe.get('error') or tr('app.common.no_details_reported'))
        )
        probe_html = tr(
            'app.macos_relay.unreachable',
            backend_port=_get_int_detail(details, 'backend_port', 58443),
            error_type=error_type,
            error_text=error_text,
        )

    attempts = _get_int_detail(details, 'attempts', 1)
    while True:
        msg = QMessageBox(parent)
        if on_top:
            msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        msg.setWindowTitle(tr('app.macos_proxy_relay_failed'))
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(tr('app.fleasion_could_not_start_its_privileged_local'))
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setInformativeText(
            tr('app.the_relay_was_tested_value_times_and', value0=attempts, value1=probe_html)
        )
        retry_button = msg.addButton(tr('app.retry'), QMessageBox.ButtonRole.AcceptRole)
        reinstall_button = msg.addButton(
            tr('app.reinstall_helper'), QMessageBox.ButtonRole.ActionRole
        )
        logs_button = msg.addButton(tr('app.open_helper_logs'), QMessageBox.ButtonRole.ActionRole)
        close_button = msg.addButton(QMessageBox.StandardButton.Close)
        msg.setDefaultButton(reinstall_button)
        if icon_path := get_icon_path():
            msg.setWindowIcon(QIcon(str(icon_path)))

        for label in msg.findChildren(QLabel):
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextBrowserInteraction
                | Qt.TextInteractionFlag.TextSelectableByMouse
            )

        msg.exec()
        clicked = msg.clickedButton()
        if clicked == retry_button:
            return 'retry'
        if clicked == reinstall_button:
            return 'reinstall'
        if clicked == logs_button:
            open_folder(helper_log_dir)
            continue
        if clicked == close_button:
            return 'close'
        return 'close'


def show_tls_self_test_failed_dialog(details: ErrorDetails) -> None:
    """Show a user-facing error when the proxy cannot pass startup TLS checks."""
    hosts_value = details.get('hosts')
    hosts = hosts_value if _is_object_list(hosts_value) else []
    host_text = ', '.join(str(host) for host in hosts) or tr('app.tls_self_test.default_routes')
    QMessageBox.critical(
        visible_parent_widget(),
        tr('app.fleasion_proxy_startup_failed'),
        tr('app.fleasion_could_not_start_its_local_proxy_2', value0=host_text),
    )


class ProxyErrorInvoker(QObject):
    """Main-thread bridge for proxy startup errors emitted from worker threads."""

    show_proxy_error = Signal(str, dict)
    disable_proxy_features = Signal(str)
    retry_proxy = Signal()

    @Slot(str, dict)
    def handle_proxy_error(self, code: str, details: dict[str, object]) -> None:
        if code == 'port_bind_failed':
            show_proxy_bind_error_dialog(details)
        elif code == 'hosts_write_exhausted':
            show_hosts_write_exhausted_dialog(details)
        elif code == 'hosts_entries_would_exceed_limit':
            show_hosts_capacity_dialog(details)
        elif code in {'hosts_file_too_large', 'hosts_file_repair_failed'}:
            show_oversized_hosts_file_dialog(details, on_repaired=self.retry_proxy.emit)
        elif code == 'linux_hosts_read_only':
            show_linux_hosts_read_only_dialog(details)
        elif code == 'macos_ca_patch_failed':
            if show_macos_ca_patch_failed_dialog(details) == 'install_helper':
                macos_proxy_helper = importlib.import_module('fleasion.utils.macos_proxy_helper')

                ok, detail = macos_proxy_helper.install_helper()
                if ok:
                    log_buffer.log(
                        'ProxyHelper',
                        'macOS proxy helper installed for protected cacert.pem; retrying proxy startup',
                    )
                    self.retry_proxy.emit()
                else:
                    log_buffer.log('ProxyHelper', f'macOS proxy helper install failed: {detail}')
                    QMessageBox.warning(
                        visible_parent_widget(),
                        tr('app.fleasion_proxy_helper_installation_failed'),
                        tr('app.fleasion_could_not_install_or_start_the', value0=detail),
                    )
        elif code == 'macos_ca_trust_failed':
            show_macos_ca_trust_failed_dialog(details)
        elif code == 'roblox_ca_patch_failed':
            denied_dirs = windows_ca_permission_denied_dirs(details)
            if denied_dirs:
                show_roblox_permission_failure(
                    visible_parent_widget(),
                    denied_dirs,
                    on_repaired=self.retry_proxy.emit,
                    failure_text=tr('app.roblox_permissions.env_proxy_failure'),
                )
            else:
                show_roblox_ca_patch_failed_dialog(details)
        elif code == 'macos_relay_failed':
            action = show_macos_relay_failed_dialog(details)
            if action == 'retry':
                self.retry_proxy.emit()
            elif action == 'reinstall':
                macos_proxy_helper = importlib.import_module('fleasion.utils.macos_proxy_helper')

                ok, detail = macos_proxy_helper.install_helper()
                if ok:
                    log_buffer.log(
                        'ProxyHelper',
                        'macOS proxy helper reinstalled after relay failure; retrying proxy startup',
                    )
                    self.retry_proxy.emit()
                else:
                    log_buffer.log(
                        'ProxyHelper',
                        f'macOS proxy helper reinstall failed: {detail}',
                    )
                    QMessageBox.warning(
                        visible_parent_widget(),
                        tr('app.fleasion_proxy_helper_reinstall_failed'),
                        tr('app.fleasion_could_not_reinstall_or_restart_the', value0=detail),
                    )
        elif code == 'upstream_connect_failed':
            show_windows_upstream_firewall_dialog(details)
        elif code == 'tls_self_test_failed':
            show_tls_self_test_failed_dialog(details)


def manual_upstream_credentials_missing(config_manager: ConfigManager) -> bool:
    mode = config_manager.upstream_transport_mode
    if mode == 'http_connect':
        return not bool(
            config_manager.upstream_http_connect_username.strip()
            or config_manager.upstream_http_connect_password
        )
    if mode == 'socks5':
        return not bool(
            config_manager.upstream_socks5_username.strip()
            or config_manager.upstream_socks5_password
        )
    return False


def disable_proxy_features_after_start_failure(
    config_manager: ConfigManager, tray: SystemTray | None, reason: str
) -> None:
    """Handle proxy startup failure without silently mutating the saved setting."""
    if not config_manager.proxy_features_enabled:
        return
    if sys.platform.startswith('linux'):
        log_buffer.log(
            'Proxy',
            f'Linux proxy helper start failed; leaving proxy features enabled: {reason}',
        )
        QMessageBox.warning(
            visible_parent_widget(),
            tr('app.fleasion_linux_proxy_helper_unavailable'),
            tr('app.fleasion_could_not_start_the_linux_proxy', value0=reason),
        )
        if tray is not None and hasattr(tray, 'update_status'):
            tray.update_status()
        return

    log_buffer.log('Proxy', f'Proxy features disabled after startup failure: {reason}')
    if tray is not None:
        tray.set_proxy_features_enabled(False)
    else:
        config_manager.proxy_features_enabled = False
