"""Application entrypoint."""

from __future__ import annotations

import argparse
import atexit
import contextlib
import ctypes
import ctypes.wintypes
import html
import importlib
import ipaddress
import json
import os
import platform
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Protocol, TypedDict, TypeIs, cast, override

from PySide6.QtCore import QEvent, QObject, QSharedMemory, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyle,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import ConfigFolderWatcher, ConfigManager
from .localization import available_languages, set_language, tr, verbatim
from .modifications import ModificationManager
from .prejsons import download_prejsons
from .proxy import ProxyMaster, check_and_patch_running_roblox_ca
from .tray import SystemTray
from .utils import (
    APP_DISCORD,
    APP_NAME,
    CONFIG_DIR,
    LOG_FILE,
    delete_cache,
    get_icon_path,
    get_roblox_player_exe_path,
    get_roblox_process_identity,
    get_roblox_studio_exe_path,
    is_roblox_running,
    is_studio_running,
    launch_as_standard_user,
    log_buffer,
    open_folder,
    run_in_thread,
    start_update_check,
    terminate_roblox,
    time_tracker,
    wait_for_roblox_exit,
)
from .utils.microprofiler import start_microprofiler
from .utils.qt_diagnostics import install_qt_message_logging

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent, QFileOpenEvent, QScreen, QSessionManager, QShowEvent

    from .proxy.env_lifecycle import EnvProxyLifecycleController
    from .utils.platform_macos import MacOSRobloxPlayerLaunch


type ErrorDetails = dict[str, object]
type VoidCallback = Callable[[], object]


class _RelaunchCompletion(TypedDict, total=False):
    wait_result: int
    exit_code_read: bool
    exit_code: int | None


def _resolve_executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(name)
    return path


def _run_trusted_text_command(
    args: list[str],
    *,
    timeout: float,
    creationflags: int = 0,
    encoding: str | None = None,
    errors: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding=encoding,
        errors=errors,
        timeout=timeout,
        creationflags=creationflags,
        check=False,
        shell=False,
    )


def _run_trusted_command(
    args: list[str], *, timeout: float, creationflags: int = 0
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        creationflags=creationflags,
        check=False,
        shell=False,
    )


def _spawn_trusted_command(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    start_new_session: bool = False,
    creationflags: int = 0,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        args,
        shell=False,
        env=env,
        start_new_session=start_new_session,
        creationflags=creationflags,
    )


class _CompatibilityBoundaryError(RuntimeError):
    """Wrap failures from dynamic/native compatibility boundaries."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


def _call_compatibility_boundary[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except Exception as exc:
        raise _CompatibilityBoundaryError(exc) from exc


_UNSPECIFIED_LOCAL_ADDRESS = str(ipaddress.IPv4Address(0))
_AUTH_SKIP_SELECTION_KEY = 'continue_without_token'


def _get_int_detail(details: ErrorDetails, key: str, default: int) -> int:
    value = details.get(key) or default
    if not isinstance(value, str | int | float):
        return default
    try:
        return int(value)
    except ValueError, OverflowError:
        return default


def _is_error_details(value: object) -> TypeIs[ErrorDetails]:
    if not isinstance(value, dict):
        return False
    details = cast('dict[object, object]', value)
    return all(isinstance(key, str) for key in details)


def _is_object_list(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


class _ProcessHandle(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


_SINGLE_INSTANCE_KEY = 'FleasionSingleInstance'
_SINGLE_INSTANCE_CONTROL_SERVER = 'FleasionSingleInstanceControl'
_WINDOWS_REPAIR_RESULT_TIMEOUT_SECONDS = 120.0
_WINDOWS_FIREWALL_REPAIR_RESULT_TIMEOUT_SECONDS = 120.0
_WINDOWS_HOSTS_CLEANUP_TIMEOUT_SECONDS = 15 * 60
_WINDOWS_WAIT_TIMEOUT = 0x102
_RESTART_HANDOFF_TIMEOUT_SECONDS = 45.0
_RESTART_ABORT_TIMEOUT_SECONDS = 10.0
_RESTART_HANDOFF_PHASES = frozenset({'prepared', 'release', 'ready', 'abort'})
_HOSTS_CLEANUP_NOT_ADMIN_EXIT = 10
_HOSTS_CLEANUP_WRITE_FAILED_EXIT = 11
_HOSTS_CLEANUP_UNEXPECTED_EXIT = 12
_MACOS_PLAIN_LAUNCH_CLASSIFICATION_SECONDS = 2.0


@dataclass(slots=True)
class _SingleInstanceState:
    shared_memory: QSharedMemory | None = None
    control_server: QLocalServer | None = None
    app: QApplication | None = None
    tray: SystemTray | None = None


_single_instance_state = _SingleInstanceState()


class RestartHandoffUncertainError(RuntimeError):
    """The old process cannot safely reclaim state from a failed replacement."""


RestartHandoffUncertain = RestartHandoffUncertainError


def _linux_client_launch_path() -> Path:
    """Return the selected Linux client's stable launch identity."""
    platform_linux = importlib.import_module('.utils.platform_linux', __package__)
    return Path(platform_linux.selected_linux_client_app_id())


def _linux_client_display_name() -> str:
    platform_linux = importlib.import_module('.utils.platform_linux', __package__)
    return platform_linux.selected_linux_client_display_name()


class _FirstTimeSetupDialog(QDialog):
    """Scrollable first-run guide that always keeps its acknowledgement visible."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._can_accept = False
        self.setModal(True)

        layout = QVBoxLayout(self)
        content_layout = QHBoxLayout()

        icon_label = QLabel(self)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        icon_label.setPixmap(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation).pixmap(32, 32)
        )
        content_layout.addWidget(icon_label, 0)

        self._body = QTextBrowser(self)
        self._body.setReadOnly(True)
        self._body.setOpenExternalLinks(False)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setMinimumSize(0, 0)
        content_layout.addWidget(self._body, 1)
        layout.addLayout(content_layout, 1)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.ok_button = QPushButton(self)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        button_layout.addWidget(self.ok_button)
        layout.addLayout(button_layout)

    def set_text(self, text: str) -> None:
        self._body.setPlainText(text)

    def setText(self, text: str) -> None:  # ruff: ignore[invalid-function-name]
        self.set_text(text)

    def allow_accept(self) -> None:
        self._can_accept = True

    def _fit_to_available_screen(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        screen = cast('QScreen | None', screen)
        if screen is None:
            return

        available = screen.availableGeometry()
        max_width = max(1, int(available.width() * 0.90))
        max_height = max(1, int(available.height() * 0.85))
        self.setMaximumSize(max_width, max_height)
        self.resize(min(680, max_width), min(620, max_height))

        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._fit_to_available_screen()

    def accept(self) -> None:
        if self._can_accept:
            super().accept()

    def reject(self) -> None:
        return

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()


class _ForcedAcknowledgeMessageBox(QMessageBox):
    """Message box that cannot be dismissed until explicitly allowed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._can_close = False

    def allow_close(self) -> None:
        self._can_close = True

    def done(self, result: int) -> None:
        if self._can_close:
            super().done(result)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._can_close:
            event.accept()
        else:
            event.ignore()


def _prepare_env_proxy_migration(config_manager: ConfigManager) -> bool:
    """Select Env Proxy before privilege gates and report a legacy migration."""
    if config_manager.env_proxy_migration_v1_complete:
        return False

    # Assign even when the merged in-memory default is already Env so the new
    # mode is durable on disk before any acknowledgement dialog can appear.
    config_manager.proxy_mode = 'env'

    # First-time users learn about Env Proxy in the setup guide. Existing
    # users receive the dedicated migration acknowledgement later in startup.
    return bool(config_manager.first_time_setup_complete)


def _show_env_proxy_migration(
    config_manager: ConfigManager, roblox_monitor: RobloxExitMonitor
) -> None:
    """Acknowledge the forced legacy migration and apply it to Player."""
    player_running = bool(roblox_monitor.is_player_running())
    if player_running:
        # Do not let the process monitor interpret a Player that predates this
        # startup as a fresh launch and relaunch it without the user's choice.
        roblox_monitor.mark_player_running_at_startup()

    msg = QMessageBox(_visible_parent_widget())
    msg.setWindowTitle(tr('app.new_default_roblox_env_proxy'))
    msg.setIcon(QMessageBox.Icon.Information)
    msg.setText(tr('app.fleasion_has_switched_your_saved_proxy_mode'))
    details = tr('app.env_proxy_migration.details')
    restart_button = None
    if player_running and config_manager.proxy_features_enabled:
        if sys.platform.startswith('linux'):
            details += tr(
                'app.env_proxy_migration.linux_future', client_name=_linux_client_display_name()
            )
            restart_button = msg.addButton(
                tr('app.apply_for_future_launches'), QMessageBox.ButtonRole.AcceptRole
            )
            later_button = msg.addButton(tr('app.apply_later'), QMessageBox.ButtonRole.RejectRole)
        else:
            details += tr('app.env_proxy_migration.player_running')
            restart_button = msg.addButton(
                tr('app.restart_roblox_now'), QMessageBox.ButtonRole.AcceptRole
            )
            later_button = msg.addButton(
                tr('app.restart_roblox_later'), QMessageBox.ButtonRole.RejectRole
            )
        msg.setDefaultButton(restart_button)
        msg.setEscapeButton(later_button)
    else:
        if player_running:
            details += tr('app.env_proxy_migration.features_disabled')
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg.setInformativeText(details)
    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))

    msg.exec()
    config_manager.env_proxy_migration_v1_complete = True

    if restart_button is None or msg.clickedButton() is not restart_button:
        return
    lifecycle = getattr(roblox_monitor, 'env_lifecycle', None)
    if lifecycle is None:
        return
    if sys.platform.startswith('linux'):
        run_in_thread(lifecycle.handle_adopted_player_launch)(_linux_client_launch_path())
    else:
        run_in_thread(lifecycle.handle_player_launch)(get_roblox_player_exe_path())


class _MacOSAuthSourceDialog(QDialog):
    """Browser-token startup prompt that only closes through explicit choices."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.allow_reject = False

    def reject(self) -> None:
        if self.allow_reject:
            super().reject()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.allow_reject:
            event.accept()
        else:
            event.ignore()


def _quit_after_modal_closes(
    modal: _MacOSAuthSourceDialog | _ForcedAcknowledgeMessageBox,
    tray: SystemTray | None = None,
    selected: dict[str, str] | None = None,
) -> None:
    """Close the active modal first, then run the normal quit path."""
    if QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()
    if selected is not None:
        selected['exit'] = '1'
    if isinstance(modal, _MacOSAuthSourceDialog):
        modal.allow_reject = True
    if isinstance(modal, _ForcedAcknowledgeMessageBox):
        modal.allow_close()
    try:
        modal.reject()
    except RuntimeError as exc:
        log_buffer.log('App', f'Could not close modal before quit: {exc}')

    def _quit() -> None:
        if tray is not None:
            tray.exit_app()
        else:
            QApplication.quit()

    QTimer.singleShot(0, _quit)


# UAC / elevation helpers


def _is_admin() -> bool:
    """Return True if the current process has administrator/root privileges."""
    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        return hasattr(os, 'geteuid') and os.geteuid() == 0

    try:
        if TYPE_CHECKING:
            shell32 = ctypes.CDLL('shell32')
        else:
            shell32 = ctypes.windll.shell32
        return bool(shell32.IsUserAnAdmin())
    except AttributeError, OSError:
        return False


def _cleanup_hosts_once() -> int:
    """Remove Fleasion hosts entries from a one-shot elevated child."""
    if not _is_admin():
        log_buffer.log(
            'Hosts', 'Elevated hosts cleanup child rejected: administrator access is required'
        )
        return _HOSTS_CLEANUP_NOT_ADMIN_EXIT

    proxy_master = importlib.import_module('.proxy.master', __package__)

    error_details: ErrorDetails = {}

    def _perform_cleanup() -> int:
        if not proxy_master.cleanup_hosts_entries(
            set(proxy_master.INTERCEPT_HOSTS), error_details=error_details
        ):
            detail = error_details.get('error') or tr('app.hosts.unknown_write_failure')
            log_buffer.log(
                'Hosts', f'Elevated hosts cleanup child could not update the hosts file: {detail}'
            )
            return _HOSTS_CLEANUP_WRITE_FAILED_EXIT
        return 0

    try:
        result = _call_compatibility_boundary(_perform_cleanup)
    except _CompatibilityBoundaryError as wrapped:
        exc = wrapped.cause
        log_buffer.log('Hosts', f'Elevated hosts cleanup child crashed: {exc!r}')
        return _HOSTS_CLEANUP_UNEXPECTED_EXIT
    if result == 0:
        log_buffer.log('Hosts', 'Elevated one-shot hosts cleanup completed')
    return result


def _run_privileged_hosts_cleanup(parent: QWidget | None = None) -> bool:
    """Run the short-lived administrator/root child used for Env Proxy repair."""
    if _is_admin():
        return _cleanup_hosts_once() == 0

    if sys.platform.startswith('linux'):
        linux_proxy_helper = importlib.import_module('.utils.linux_proxy_helper', __package__)
        return linux_proxy_helper.cleanup_hosts_with_pkexec()

    completion: _RelaunchCompletion = {}
    completed = _relaunch_as_admin(
        extra_args='--cleanup-hosts',
        parent_hwnd=_window_handle(parent),
        wait_for_completion=True,
        wait_timeout_ms=int(_WINDOWS_HOSTS_CLEANUP_TIMEOUT_SECONDS * 1000),
        completion=completion,
    )
    if completed:
        return True

    exit_code = completion.get('exit_code')
    if completion.get('wait_result') == _WINDOWS_WAIT_TIMEOUT:
        log_buffer.log(
            'Hosts',
            'Privileged cleanup child is still running after the extended wait; '
            'the hosts file may still be under repair',
        )
        return False
    reasons = {
        _HOSTS_CLEANUP_NOT_ADMIN_EXIT: 'the child did not receive an administrator token',
        _HOSTS_CLEANUP_WRITE_FAILED_EXIT: 'Windows or security software blocked the hosts write',
        _HOSTS_CLEANUP_UNEXPECTED_EXIT: 'the cleanup child raised an unexpected exception',
    }
    if exit_code in reasons:
        log_buffer.log('Hosts', f'Privileged cleanup failed because {reasons[exit_code]}')
    elif exit_code is not None:
        log_buffer.log(
            'Hosts',
            f'Privileged cleanup child exited before reporting a known outcome (exit={exit_code})',
        )
    return False


def _show_oversized_hosts_file_dialog(
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
    parent = _visible_parent_widget()

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
            repaired = _run_privileged_hosts_cleanup(msg)
        finally:
            msg.hide()

        if repaired:
            proxy_master = importlib.import_module('.proxy.master', __package__)
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
        _show_hosts_write_exhausted_dialog(failure_details)
        return False


def _show_hosts_capacity_dialog(details: ErrorDetails) -> None:
    """Explain that a normal-sized hosts file cannot fit new mappings safely."""

    hosts_path = str(details.get('hosts_path') or r'C:\Windows\System32\drivers\etc\hosts')
    hosts_directory = str(
        details.get('hosts_directory')
        or Path(hosts_path).parent
        or r'C:\Windows\System32\drivers\etc'
    )
    limit = _get_int_detail(details, 'hosts_size_limit_bytes', 512 * 1024)
    candidate_size = _get_int_detail(details, 'hosts_size_bytes', 0)
    msg = QMessageBox(_visible_parent_widget())
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


def _show_env_proxy_stale_hosts_dialog() -> bool:
    """Offer a one-shot privileged repair for oversized or stale Env Proxy hosts entries."""
    proxy_master = importlib.import_module('.proxy.master', __package__)

    if proxy_master.other_proxy_owner_alive():
        log_buffer.log(
            'Hosts',
            'Skipped Env Proxy stale hosts prompt because another proxy owns the hosts file',
        )
        return True
    oversized_details: ErrorDetails = {}
    if proxy_master.hosts_file_is_oversized(oversized_details):
        return _show_oversized_hosts_file_dialog(oversized_details)
    if not proxy_master.has_stale_hosts_entries(set(proxy_master.INTERCEPT_HOSTS)):
        return True

    parent = _visible_parent_widget()
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
        repaired = _run_privileged_hosts_cleanup(msg)
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


def _should_sync_autostart_on_launch(run_on_boot: bool) -> bool:
    if not run_on_boot:
        return False
    if sys.platform == 'darwin':
        return not _is_admin()
    if sys.platform.startswith('linux'):
        return True
    if sys.platform == 'win32':
        return True
    return False


def _refresh_run_on_boot_ui(tray: SystemTray | None, enabled: bool) -> None:
    if tray is not None and hasattr(tray, 'run_on_boot_action'):
        tray.run_on_boot_action.setChecked(enabled)
    if tray is not None:
        tray.refresh_settings_tab()


def _refresh_desktop_integration_ui(tray: SystemTray | None, enabled: bool) -> None:
    if tray is not None and hasattr(tray, 'desktop_integration_action'):
        tray.desktop_integration_action.setChecked(enabled)
    if tray is not None:
        tray.refresh_settings_tab()


def _show_run_on_boot_failure(
    parent: QWidget | None, proxy_mode: str | None = None, *, enabled: bool = True
) -> bool:
    msg = QMessageBox(parent)
    msg.setWindowTitle(
        tr('app.run_on_boot_needs_repair')
        if enabled
        else tr('app.run_on_boot_could_not_be_disabled')
    )
    msg.setIcon(QMessageBox.Icon.Warning)
    if sys.platform == 'win32':
        autostart = importlib.import_module('.utils.autostart', __package__)
        if enabled:
            msg.setText(
                tr(
                    'app.fleasion_could_not_update_its_run_on',
                    value0=autostart.windows_autostart_privilege_hint(proxy_mode),
                )
            )
        else:
            msg.setText(tr('app.fleasion_could_not_remove_its_legacy_run'))
    else:
        msg.setText(tr('app.failed_to_register_autostart_check_the_application'))
    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))

    repair_button = None
    if sys.platform == 'win32':
        repair_label = tr('app.autostart.repair_now') if enabled else tr('app.autostart.remove_now')
        repair_button = msg.addButton(repair_label, QMessageBox.ButtonRole.AcceptRole)
        ignore_button = msg.addButton(
            tr('app.keep_run_on_boot_enabled'), QMessageBox.ButtonRole.RejectRole
        )
        msg.setDefaultButton(ignore_button)
    else:
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg.exec()

    if repair_button is not None and msg.clickedButton() == repair_button:
        repair_args = '--repair-autostart' if enabled else '--repair-autostart --disable-autostart'
        if _relaunch_as_admin(
            extra_args=repair_args,
            parent_hwnd=_window_handle(parent),
            wait_for_completion=True,
        ):
            log_buffer.log(
                'Autostart',
                'Elevated autostart repair completed'
                if enabled
                else 'Elevated legacy autostart-task removal completed',
            )
            msg.setWindowTitle(
                tr('app.run_on_boot_fixed') if enabled else tr('app.run_on_boot_disabled')
            )
            msg.setIcon(QMessageBox.Icon.Information)
            if enabled:
                msg.setText(tr('app.it_worked_fleasion_repaired_the_run_on'))
            else:
                msg.setText(tr('app.it_worked_fleasion_removed_the_legacy_run'))
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()
            return True
        log_buffer.log(
            'Autostart',
            'Elevated autostart repair did not complete successfully'
            if enabled
            else 'Elevated legacy autostart-task removal did not complete successfully',
        )
        msg.setWindowTitle(
            tr('app.run_on_boot_repair_incomplete')
            if enabled
            else tr('app.run_on_boot_disable_incomplete')
        )
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(tr('app.fleasion_could_not_confirm_that_the_run'))
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()
    return False


def _show_roblox_permission_failure(
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

    windows_permissions = importlib.import_module('.utils.windows_permissions', __package__)
    clear_pending_repair = windows_permissions.clear_pending_repair
    clear_repair_result = windows_permissions.clear_repair_result
    write_pending_repair = windows_permissions.write_pending_repair

    try:
        clear_repair_result(CONFIG_DIR)
        if not write_pending_repair(paths, CONFIG_DIR):
            msg_0 = 'No valid Roblox installation folders were selected'
            raise OSError(msg_0)
        relaunched = _relaunch_as_admin(
            extra_args='--repair-roblox-permissions',
            parent_hwnd=_window_handle(parent),
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
            deadline = time.monotonic() + _WINDOWS_REPAIR_RESULT_TIMEOUT_SECONDS
            QTimer.singleShot(
                500,
                lambda: _poll_roblox_permission_repair(
                    mod_manager,
                    deadline,
                    on_repaired=on_repaired,
                ),
            )
        elif on_repaired is not None:
            deadline = time.monotonic() + _WINDOWS_REPAIR_RESULT_TIMEOUT_SECONDS
            QTimer.singleShot(
                500,
                lambda: _poll_roblox_permission_repair(
                    None,
                    deadline,
                    on_repaired=on_repaired,
                ),
            )
    else:
        clear_pending_repair(CONFIG_DIR)


def _poll_roblox_permission_repair(
    mod_manager: ModificationManager | None,
    deadline: float,
    *,
    on_repaired: VoidCallback | None = None,
) -> None:
    """Consume a one-shot elevated ACL result and retry the normal write path."""
    if TYPE_CHECKING:

        def clear_pending_repair(config_dir: Path | None = None) -> None: ...

        def clear_repair_result(config_dir: Path | None = None) -> None: ...

        def read_repair_result(config_dir: Path | None = None) -> ErrorDetails | None: ...
    else:
        windows_permissions = importlib.import_module('.utils.windows_permissions', __package__)
        clear_pending_repair = windows_permissions.clear_pending_repair
        clear_repair_result = windows_permissions.clear_repair_result
        read_repair_result = windows_permissions.read_repair_result

    result = read_repair_result(CONFIG_DIR)
    if result is None:
        if time.monotonic() < deadline:
            QTimer.singleShot(
                500,
                lambda: _poll_roblox_permission_repair(
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
            _visible_parent_widget(),
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
    msg = QMessageBox(_visible_parent_widget())
    msg.setWindowTitle(tr('app.roblox_permission_repair_failed'))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(tr('app.fleasion_could_not_update_the_permissions_for', value0=detail))
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg.exec()


def _poll_windows_firewall_repair(deadline: float) -> None:
    """Consume a one-shot elevated firewall result and explain the outcome."""
    if TYPE_CHECKING:

        def clear_pending_repair(config_dir: Path | None = None) -> None: ...

        def clear_repair_result(config_dir: Path | None = None) -> None: ...

        def read_repair_result(config_dir: Path | None = None) -> ErrorDetails | None: ...
    else:
        windows_firewall = importlib.import_module('.utils.windows_firewall', __package__)
        clear_pending_repair = windows_firewall.clear_pending_repair
        clear_repair_result = windows_firewall.clear_repair_result
        read_repair_result = windows_firewall.read_repair_result

    result = read_repair_result(CONFIG_DIR)
    if result is None:
        if time.monotonic() < deadline:
            QTimer.singleShot(500, lambda: _poll_windows_firewall_repair(deadline))
            return
        clear_pending_repair(CONFIG_DIR)
        clear_repair_result(CONFIG_DIR)
        log_buffer.log('WindowsFirewall', 'Timed out waiting for the elevated firewall repair')
        QMessageBox.warning(
            _visible_parent_widget(),
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
            _visible_parent_widget(),
            tr('app.fleasion_firewall_updated'),
            tr('app.windows_firewall_now_allows_fleasion_on_private'),
        )
        return

    detail = result.get('error') or result.get('failed') or tr('app.firewall.rules_update_failed')
    log_buffer.log('WindowsFirewall', f'Firewall repair failed: {detail}')
    QMessageBox.warning(
        _visible_parent_widget(),
        tr('app.fleasion_firewall_repair_failed'),
        tr('app.fleasion_could_not_update_windows_firewall_value', value0=detail),
    )


def _show_desktop_integration_failure(parent: QWidget | None) -> None:
    msg = QMessageBox(parent)
    msg.setWindowTitle(tr('app.desktop_integration_failed'))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(tr('app.failed_to_create_desktop_start_menu_integration'))
    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))
    msg.exec()


def _prompt_first_time_language(config_manager: ConfigManager) -> None:
    """Make language the first choice shown to a new user."""
    top = QApplication.topLevelWidgets()
    parent = next((w for w in top if w.isVisible()), None)
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )

    dialog = QDialog(parent)
    dialog.setModal(True)
    dialog.setWindowTitle(tr('language.picker.title'))
    if on_top:
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    if icon_path := get_icon_path():
        dialog.setWindowIcon(QIcon(str(icon_path)))

    layout = QVBoxLayout(dialog)
    prompt = QLabel(tr('language.picker.prompt'), dialog)
    prompt.setWordWrap(True)
    layout.addWidget(prompt)

    row = QHBoxLayout()
    row.addWidget(QLabel(tr('language.picker.label'), dialog))
    combo = QComboBox(dialog)
    for code, display_name in available_languages():
        combo.addItem(display_name, code)
    current_index = combo.findData(config_manager.language)
    combo.setCurrentIndex(max(0, current_index))
    row.addWidget(combo, 1)
    layout.addLayout(row)

    button_row = QHBoxLayout()
    continue_button = QPushButton(tr('language.picker.continue'), dialog)
    continue_button.setDefault(True)
    continue_button.setAutoDefault(True)
    button_row.addStretch(1)
    button_row.addWidget(continue_button)
    layout.addLayout(button_row)
    continue_button.clicked.connect(dialog.accept)

    dialog.exec()
    selected = str(combo.currentData() or 'en')
    config_manager.language = selected
    set_language(config_manager.language)


def _prompt_first_time_startup_options(
    config_manager: ConfigManager, tray: SystemTray | None = None
) -> None:
    """Ask first-time users which startup integrations Fleasion should create."""
    top = QApplication.topLevelWidgets()
    parent = next((w for w in top if w.isVisible()), None)
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )

    dialog = QDialog(parent)
    dialog.setModal(True)
    dialog.setWindowTitle(tr('app.startup_integration'))
    if on_top:
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    if icon_path := get_icon_path():
        dialog.setWindowIcon(QIcon(str(icon_path)))

    layout = QVBoxLayout(dialog)
    label = QLabel(dialog)
    label.setWordWrap(True)
    message = tr('app.startup_integration.body')
    if sys.platform == 'win32':
        message += tr('app.startup_integration.windows_note')
    label.setText(message)
    layout.addWidget(label)

    checkbox_row = QHBoxLayout()
    run_on_boot_chk = QCheckBox(tr('app.run_on_boot'), dialog)
    desktop_integration_chk = QCheckBox(tr('app.desktop_start_menu_integration'), dialog)
    run_on_boot_chk.setChecked(config_manager.run_on_boot)
    desktop_integration_chk.setChecked(config_manager.desktop_integration)
    checkbox_row.addWidget(run_on_boot_chk)
    checkbox_row.addWidget(desktop_integration_chk)
    checkbox_row.addStretch(1)
    layout.addLayout(checkbox_row)

    button_row = QHBoxLayout()
    ok_button = QPushButton(tr('app.ok'), dialog)
    ok_button.setDefault(True)
    ok_button.setAutoDefault(True)
    button_row.addStretch(1)
    button_row.addWidget(ok_button)
    layout.addLayout(button_row)
    ok_button.clicked.connect(dialog.accept)

    ok_button.setFocus(Qt.FocusReason.OtherFocusReason)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    dialog.exec()

    enable_run_on_boot = run_on_boot_chk.isChecked()
    enable_desktop_integration = desktop_integration_chk.isChecked()
    try:
        desktop_integration = importlib.import_module('.utils.desktop_integration', __package__)
        desktop_ok = desktop_integration.sync_desktop_integration(
            enabled=enable_desktop_integration
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        desktop_ok = False
        log_buffer.log('DesktopIntegration', f'First-time desktop integration prompt failed: {exc}')

    try:
        autostart = importlib.import_module('.utils.autostart', __package__)
        boot_ok = autostart.sync_autostart(
            enabled=enable_run_on_boot,
            config_dir=CONFIG_DIR,
            proxy_mode=config_manager.proxy_mode,
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        boot_ok = False
        log_buffer.log('Autostart', f'First-time run-on-boot prompt failed: {exc}')

    config_manager.run_on_boot = enable_run_on_boot if boot_ok else False
    config_manager.desktop_integration = enable_desktop_integration if desktop_ok else False
    _refresh_run_on_boot_ui(tray, config_manager.run_on_boot)
    _refresh_desktop_integration_ui(tray, config_manager.desktop_integration)

    if enable_run_on_boot and not boot_ok:
        _show_run_on_boot_failure(dialog.parentWidget(), config_manager.proxy_mode)
    if enable_desktop_integration and not desktop_ok:
        _show_desktop_integration_failure(dialog.parentWidget())


def _append_windows_requesting_user_args(existing_args: list[str]) -> bool:
    """Carry the pre-UAC desktop identity into a one-shot elevated child."""
    if sys.platform != 'win32':
        return True
    if any(arg.startswith('--fleasion-requesting-user-sid=') for arg in existing_args):
        return True
    try:
        windows_permissions = importlib.import_module('.utils.windows_permissions', __package__)
        sid, _account_name = windows_permissions.current_windows_user_identity()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log_buffer.log('UAC', f'Could not capture requesting Windows identity: {exc}')
        return False
    existing_args.extend(
        [
            f'--fleasion-requesting-user-sid={sid}',
        ]
    )
    return True


def _relaunch_as_admin_macos(*, extra_args: str, wait_for_completion: bool) -> bool:
    existing_args = _strip_restart_handoff_args(list(sys.argv[1:]))
    if not any(arg.startswith('--fleasion-user-localappdata=') for arg in existing_args):
        existing_args.append(f'--fleasion-user-localappdata={CONFIG_DIR.parent}')
    if extra_args.strip():
        existing_args.extend(extra_args.strip().split())

    if getattr(sys, 'frozen', False):
        launch = [sys.executable, *existing_args]
        redirect = ' >/tmp/fleasion-admin.log 2>&1'
        if not wait_for_completion:
            redirect += ' &'
        shell_cmd = (
            f'FLEASION_USER_HOME={shlex.quote(str(Path.home()))} {shlex.join(launch)}{redirect}'
        )
    else:
        project_root = Path(__file__).resolve().parents[2]
        launcher = project_root / 'launcher.py'
        python_exe = Path(sys.executable)
        launch = [str(python_exe), str(launcher), *existing_args]
        redirect = ' >/tmp/fleasion-admin.log 2>&1'
        if not wait_for_completion:
            redirect += ' &'
        shell_cmd = (
            f'cd {shlex.quote(str(project_root))} && '
            f'FLEASION_USER_HOME={shlex.quote(str(Path.home()))} '
            f'PYTHONPATH={shlex.quote(str(project_root / "src"))} '
            f'{shlex.join(launch)}{redirect}'
        )

    script = 'do shell script ' + json.dumps(shell_cmd) + ' with administrator privileges'
    try:
        result = _run_trusted_text_command(
            [_resolve_executable('osascript'), '-e', script],
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('UAC', f'macOS administrator relaunch failed: {exc}')
        return False

    if result.returncode != 0:
        err = (result.stderr or result.stdout or '').strip()
        log_buffer.log(
            'UAC',
            f'macOS administrator relaunch was cancelled or failed: {err or result.returncode}',
        )
        return False
    return True


def _relaunch_as_admin_windows(
    extra_args: str,
    parent_hwnd: int | None,
    *,
    wait_for_completion: bool,
    wait_timeout_ms: int,
    completion: _RelaunchCompletion | None,
    restart_handoff_token: str | None,
    restart_handoff_parent_pid: int | None,
) -> bool:
    existing_args = _strip_restart_handoff_args(list(sys.argv[1:]))
    if restart_handoff_token:
        existing_args = [arg for arg in existing_args if arg != '--kill-others']
        existing_args.extend(
            [
                '--restart-handoff-token',
                restart_handoff_token,
                '--restart-handoff-parent-pid',
                str(restart_handoff_parent_pid),
            ]
        )
    if not any(arg.startswith('--fleasion-user-localappdata=') for arg in existing_args):
        local_appdata = os.environ.get('LOCALAPPDATA') or str(CONFIG_DIR.parent)
        existing_args.append(f'--fleasion-user-localappdata={local_appdata}')
    if extra_args.strip():
        existing_args.extend(extra_args.strip().split())
    requesting_identity_captured = _append_windows_requesting_user_args(existing_args)
    if extra_args.strip().startswith(
        ('--repair-autostart', '--repair-roblox-permissions')
    ) and not (requesting_identity_captured):
        return False
    # Normal elevation asks the old process to exit before claiming the slot.
    # Verified restart handoffs are different: the parent keeps its working
    # proxy alive and explicitly transfers the single-instance slot only after
    # this final elevated child reaches the prepared gate.
    if not restart_handoff_token and '--kill-others' not in existing_args:
        existing_args.append('--kill-others')

    frozen = bool(getattr(sys, 'frozen', False))
    if frozen:
        # Compiled .exe — sys.executable is the .exe itself
        exe = sys.executable
        params = subprocess.list2cmdline(existing_args) if existing_args else None
    else:
        # Dev / uv run — locate the uv executable and replay the original
        # invocation through it.  Running the Python interpreter directly in
        # the elevated process would miss the uv-managed virtualenv entirely,
        # causing import failures and a silent crash.

        uv_exe = shutil.which('uv') or shutil.which('uv.exe')
        if uv_exe:
            # Reconstruct:  uv run fleasion  (the original entry-point)
            exe = uv_exe
            # Pass the project directory so uv finds pyproject.toml correctly
            cwd = str(Path(__file__).resolve().parents[2])
            # ShellExecuteW doesn't let us set cwd directly for the child, but
            # we can pass --project to tell uv where to look.
            params = subprocess.list2cmdline(['--project', cwd, 'run', 'fleasion', *existing_args])
        else:
            # Fallback: plain interpreter (may fail if venv is not activated,
            # but it's the best we can do without uv)
            exe = sys.executable
            params = subprocess.list2cmdline([sys.argv[0], *existing_args])

    # Use ShellExecuteExW with SEE_MASK_NO_CONSOLE so the elevated process
    # (which may be uv.exe, a console app) never spawns a visible cmd window.

    see_mask_no_console = 0x00008000
    see_mask_nocloseprocess = 0x00000040

    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ('cbSize', ctypes.wintypes.DWORD),
            ('fMask', ctypes.wintypes.ULONG),
            ('hwnd', ctypes.wintypes.HWND),
            ('lpVerb', ctypes.wintypes.LPCWSTR),
            ('lpFile', ctypes.wintypes.LPCWSTR),
            ('lpParameters', ctypes.wintypes.LPCWSTR),
            ('lpDirectory', ctypes.wintypes.LPCWSTR),
            ('nShow', ctypes.c_int),
            ('hInstApp', ctypes.wintypes.HINSTANCE),
            ('lpIDList', ctypes.c_void_p),
            ('lpClass', ctypes.wintypes.LPCWSTR),
            ('hkeyClass', ctypes.wintypes.HKEY),
            ('dwHotKey', ctypes.wintypes.DWORD),
            ('hIconOrMonitor', ctypes.wintypes.HANDLE),
            ('hProcess', ctypes.wintypes.HANDLE),
        ]

    sei = _SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    sei.fMask = see_mask_no_console | see_mask_nocloseprocess
    sei.hwnd = parent_hwnd
    sei.lpVerb = 'runas'
    sei.lpFile = exe
    sei.lpParameters = params
    sei.lpDirectory = str(Path(exe).resolve().parent)
    # SW_HIDE (0) for dev/uv mode: hides the uv.exe console wrapper.
    # SW_SHOWNORMAL (1) for compiled .exe: the exe IS the app, we need windows to show.
    sei.nShow = 1 if frozen else 0
    sei.hInstApp = None

    if TYPE_CHECKING:
        shell32 = ctypes.CDLL('shell32')
    else:
        shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = ctypes.wintypes.BOOL

    reset_env_key = 'PYINSTALLER_RESET_ENVIRONMENT'
    old_reset_env = os.environ.get(reset_env_key)
    if frozen:
        os.environ[reset_env_key] = '1'
    try:
        ok = shell32.ShellExecuteExW(ctypes.byref(sei))
    finally:
        if frozen:
            if old_reset_env is None:
                os.environ.pop(reset_env_key, None)
            else:
                os.environ[reset_env_key] = old_reset_env
    if not ok:
        if TYPE_CHECKING:
            err = ctypes.get_errno()
            error_text = os.strerror(err)
        else:
            err = ctypes.get_last_error()
            error_text = ctypes.FormatError(err)
        if err == 1223:  # ERROR_CANCELLED: user declined UAC
            log_buffer.log('UAC', 'Administrator relaunch was cancelled by the user')
        else:
            log_buffer.log(
                'UAC',
                f'Administrator relaunch failed: WinError {err}: {error_text}',
            )
        return False

    if TYPE_CHECKING:
        kernel32 = ctypes.CDLL('kernel32')
    else:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.wintypes.BOOL
    kernel32.GetProcessId.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.GetProcessId.restype = ctypes.wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.UINT]
    kernel32.TerminateProcess.restype = ctypes.wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

    if restart_handoff_token:
        child_pid = int(kernel32.GetProcessId(sei.hProcess))
        if child_pid <= 0:
            log_buffer.log('Restart', 'Could not identify elevated replacement process')
            kernel32.CloseHandle(sei.hProcess)
            return False

        def _elevated_child_alive() -> bool:
            return kernel32.WaitForSingleObject(sei.hProcess, 0) == _WINDOWS_WAIT_TIMEOUT

        def _terminate_elevated_child() -> None:
            if not _elevated_child_alive():
                return
            if kernel32.TerminateProcess(sei.hProcess, 1):
                kernel32.WaitForSingleObject(sei.hProcess, 2_000)

        try:
            return _run_restart_handoff_parent(
                restart_handoff_token,
                child_pid,
                is_launcher_alive=_elevated_child_alive,
                terminate_launcher=_terminate_elevated_child,
            )
        finally:
            kernel32.CloseHandle(sei.hProcess)

    if not wait_for_completion:
        kernel32.CloseHandle(sei.hProcess)
        return True

    wait_result = kernel32.WaitForSingleObject(sei.hProcess, wait_timeout_ms)
    exit_code = ctypes.wintypes.DWORD()
    exit_code_read = False
    if wait_result != _WINDOWS_WAIT_TIMEOUT:
        exit_code_read = bool(kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code)))
    completed = wait_result == 0 and exit_code_read and exit_code.value == 0
    kernel32.CloseHandle(sei.hProcess)
    if completion is not None:
        completion.update(
            {
                'wait_result': wait_result,
                'exit_code_read': exit_code_read,
                'exit_code': exit_code.value if exit_code_read else None,
            }
        )
    if not completed:
        if wait_result == _WINDOWS_WAIT_TIMEOUT:
            log_buffer.log(
                'UAC',
                f'Elevated child is still running after {wait_timeout_ms / 1000:.0f}s; '
                'the synchronous wait timed out',
            )
        else:
            log_buffer.log(
                'UAC',
                f'Elevated child did not complete successfully (wait={wait_result}, '
                f'exit={exit_code.value if exit_code_read else "unknown"})',
            )
    return completed


def _relaunch_as_admin(
    extra_args: str = '',
    parent_hwnd: int | None = None,
    *,
    wait_for_completion: bool = False,
    wait_timeout_ms: int = 120_000,
    completion: _RelaunchCompletion | None = None,
    restart_handoff_token: str | None = None,
    restart_handoff_parent_pid: int | None = None,
) -> bool:
    """Silently attempt to relaunch elevated via the platform prompt.

    Shows only the standard Windows UAC or macOS administrator prompt.
    Returns True if the elevated process was spawned (caller should exit), or,
    when ``wait_for_completion`` is set, if the elevated child completed with
    exit code zero. ``completion`` receives the native wait and exit-code
    details for synchronous callers that need a more specific failure reason.
    Returns False if the user declined or the relaunch failed. A restart
    handoff token enables the verified parent/child protocol used by mode
    switches; it cannot be combined with ``wait_for_completion``.
    """
    if restart_handoff_token and (
        sys.platform != 'win32'
        or wait_for_completion
        or not restart_handoff_parent_pid
        or restart_handoff_parent_pid <= 0
    ):
        log_buffer.log('Restart', 'Invalid administrator restart handoff request')
        return False

    if sys.platform == 'darwin':
        return _relaunch_as_admin_macos(
            extra_args=extra_args, wait_for_completion=wait_for_completion
        )

    if sys.platform.startswith('linux'):
        log_buffer.log(
            'UAC',
            'Linux administrator relaunch skipped: proxy uses the privileged helper instead',
        )
        return False

    return _relaunch_as_admin_windows(
        extra_args,
        parent_hwnd,
        wait_for_completion=wait_for_completion,
        wait_timeout_ms=wait_timeout_ms,
        completion=completion,
        restart_handoff_token=restart_handoff_token,
        restart_handoff_parent_pid=restart_handoff_parent_pid,
    )


def _repair_autostart_once(requesting_user_sid: str | None = None, *, enabled: bool = True) -> int:
    """Repair or remove Windows autostart from a one-shot elevated process."""
    if sys.platform != 'win32' or not _is_admin():
        log_buffer.log(
            'Autostart', 'Elevated autostart repair rejected: administrator access is required'
        )
        return 1

    autostart = importlib.import_module('.utils.autostart', __package__)
    windows_user_id = None
    if enabled:
        windows_permissions = importlib.import_module('.utils.windows_permissions', __package__)
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


def _repair_roblox_permissions_once(requesting_user_sid: str | None = None) -> int:
    """Apply a pending targeted Roblox ACL repair from a one-shot UAC child."""
    if TYPE_CHECKING:

        def clear_pending_repair(config_dir: Path | None = None) -> None: ...

        def read_pending_repair(config_dir: Path | None = None) -> list[Path]: ...

        def grant_current_user_modify_access(
            paths: Iterable[Path], *, user_sid: str | None = None
        ) -> ErrorDetails: ...

        def write_repair_result(result: ErrorDetails, config_dir: Path | None = None) -> None: ...
    else:
        windows_permissions = importlib.import_module('.utils.windows_permissions', __package__)
        clear_pending_repair = windows_permissions.clear_pending_repair
        grant_current_user_modify_access = windows_permissions.grant_current_user_modify_access
        read_pending_repair = windows_permissions.read_pending_repair
        write_repair_result = windows_permissions.write_repair_result

    if sys.platform != 'win32' or not _is_admin():
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
            result = _call_compatibility_boundary(
                lambda: grant_current_user_modify_access(
                    paths,
                    user_sid=requesting_user_sid,
                )
            )
        except _CompatibilityBoundaryError as wrapped:
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


def _repair_windows_firewall_once() -> int:
    """Apply a pending Fleasion firewall repair from a one-shot UAC child."""
    if TYPE_CHECKING:

        def clear_pending_repair(config_dir: Path | None = None) -> None: ...

        def read_pending_repair(config_dir: Path | None = None) -> bool: ...

        def install_fleasion_firewall_rules(
            program_path: str | Path | None = None,
        ) -> ErrorDetails: ...

        def write_repair_result(result: ErrorDetails, config_dir: Path | None = None) -> None: ...
    else:
        windows_firewall = importlib.import_module('.utils.windows_firewall', __package__)
        clear_pending_repair = windows_firewall.clear_pending_repair
        install_fleasion_firewall_rules = windows_firewall.install_fleasion_firewall_rules
        read_pending_repair = windows_firewall.read_pending_repair
        write_repair_result = windows_firewall.write_repair_result

    if sys.platform != 'win32' or not _is_admin():
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
            result = _call_compatibility_boundary(install_fleasion_firewall_rules)
        except _CompatibilityBoundaryError as wrapped:
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


def _restart_handoff_path(token: str, phase: str = 'ready') -> Path | None:
    """Resolve a restart protocol marker without accepting arbitrary paths."""
    token = str(token or '')
    phase = str(phase or '')
    if phase not in _RESTART_HANDOFF_PHASES:
        return None
    if len(token) != 32 or any(character not in '0123456789abcdef' for character in token):
        return None
    return CONFIG_DIR / f'.restart-{phase}-{token}'


def _cleanup_restart_handoff(token: str, *, preserve_abort: bool = False) -> None:
    for phase in _RESTART_HANDOFF_PHASES:
        if preserve_abort and phase == 'abort':
            continue
        marker = _restart_handoff_path(token, phase)
        if marker is None:
            continue
        with contextlib.suppress(OSError):
            marker.unlink(missing_ok=True)


def _write_restart_marker_file(marker: Path, value: int) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(str(value))
        handle.flush()
        os.fsync(handle.fileno())


def _write_restart_handoff_marker(token: str, phase: str, value: int) -> bool:
    marker = _restart_handoff_path(token, phase)
    if marker is None or value <= 0:
        log_buffer.log('Restart', 'Rejected invalid restart handoff marker')
        return False
    try:
        _write_restart_marker_file(marker, value)
    except OSError as exc:
        log_buffer.log('Restart', f'Could not publish restart {phase} marker: {exc}')
        return False
    return True


def _publish_restart_handoff(token: str) -> bool:
    """Publish final readiness from the replacement process."""
    return _write_restart_handoff_marker(token, 'ready', os.getpid())


def _unix_pid_is_alive(pid: int) -> bool:
    """Probe a Unix PID without requiring termination rights."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    else:
        return True


def _pid_is_alive(pid: int) -> bool:
    """Return whether an application PID is alive without requiring termination rights."""
    if pid <= 0:
        return False
    if sys.platform != 'win32':
        return _unix_pid_is_alive(pid)

    process_query_limited_information = 0x1000
    still_active = 259
    if TYPE_CHECKING:
        kernel32 = ctypes.CDLL('kernel32')
    else:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        ctypes.wintypes.DWORD,
        ctypes.wintypes.BOOL,
        ctypes.wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

    inherit_handle = False
    handle = kernel32.OpenProcess(process_query_limited_information, inherit_handle, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
            exit_code.value == still_active
        )
    finally:
        kernel32.CloseHandle(handle)


def _read_restart_marker_value(token: str, phase: str) -> int | None:
    marker = _restart_handoff_path(token, phase)
    if marker is None or not marker.is_file():
        return None
    try:
        raw_value = marker.read_text(encoding='utf-8').strip()
        value = int(raw_value)
    except OSError, ValueError:
        return None
    return value if value > 0 else None


def _wait_for_restart_marker(
    token: str,
    phase: str,
    *,
    is_launcher_alive: Callable[[], bool],
    expected_value: int | None = None,
    timeout: float = _RESTART_HANDOFF_TIMEOUT_SECONDS,
) -> int | None:
    """Wait for a token-authenticated protocol marker and return its app PID/value.

    The process created by Popen/ShellExecute is only a launcher-liveness signal.
    PyInstaller one-file builds use a bootloader parent whose PID differs from
    the Python application child, so launcher PID is deliberately not protocol
    identity. The random token identifies this handoff; ``prepared`` reports
    the actual application PID, and later phases can require that same value.
    """
    marker = _restart_handoff_path(token, phase)
    if marker is None or (expected_value is not None and expected_value <= 0):
        return None
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not is_launcher_alive():
            log_buffer.log('Restart', f'Replacement launcher exited before {phase} handoff')
            return None
        marker_value = _read_restart_marker_value(token, phase)
        if marker_value is not None and (expected_value is None or marker_value == expected_value):
            # Re-check the outer launcher after reading to reject a marker that
            # raced with immediate launcher/application teardown.
            if not is_launcher_alive():
                log_buffer.log(
                    'Restart',
                    f'Replacement launcher exited immediately after {phase} handoff',
                )
                return None
            return marker_value
        time.sleep(0.05)
    log_buffer.log('Restart', f'Replacement Fleasion timed out before {phase} handoff')
    return None


def _restart_abort_requested(token: str, parent_pid: int) -> bool:
    return _read_restart_marker_value(token, 'abort') == parent_pid


def _request_restart_abort(token: str, parent_pid: int) -> bool:
    """Ask the application child to abandon the handoff and exit cleanly."""
    marker = _restart_handoff_path(token, 'abort')
    if marker is None or parent_pid <= 0:
        return False
    existing = _read_restart_marker_value(token, 'abort')
    if existing is not None:
        return existing == parent_pid
    return _write_restart_handoff_marker(token, 'abort', parent_pid)


def _wait_for_restart_release(token: str, parent_pid: int) -> bool:
    """Child-side gate: do not touch single-instance ownership until released."""
    marker = _restart_handoff_path(token, 'release')
    if marker is None or parent_pid <= 0:
        return False
    deadline = time.monotonic() + _RESTART_HANDOFF_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _restart_abort_requested(token, parent_pid):
            log_buffer.log('Restart', 'Parent aborted restart before ownership transfer')
            abort_marker = _restart_handoff_path(token, 'abort')
            if abort_marker is not None:
                with contextlib.suppress(OSError):
                    abort_marker.unlink(missing_ok=True)
            return False
        if marker.is_file():
            try:
                released_by = marker.read_text(encoding='utf-8').strip()
            except OSError:
                released_by = ''
            if released_by == str(parent_pid):
                with contextlib.suppress(OSError):
                    marker.unlink(missing_ok=True)
                return True
        time.sleep(0.05)
    log_buffer.log('Restart', 'Parent never released single-instance ownership')
    return False


def _join_restart_handoff(token: str, parent_pid: int) -> bool:
    """Child-side first phase of the verified restart protocol."""
    if not _write_restart_handoff_marker(token, 'prepared', os.getpid()):
        return False
    return _wait_for_restart_release(token, parent_pid)


def _suspend_single_instance_for_handoff() -> bool:
    """Temporarily transfer the single-instance slot while keeping the proxy alive."""
    shared_memory = _single_instance_state.shared_memory
    if shared_memory is None or not shared_memory.isAttached():
        log_buffer.log(
            'Restart', 'Cannot transfer restart ownership: single-instance lock is absent'
        )
        return False

    server = _single_instance_state.control_server
    if server is not None:
        server.close()
        QLocalServer.removeServer(_SINGLE_INSTANCE_CONTROL_SERVER)
        _single_instance_state.control_server = None

    if shared_memory.detach():
        return True

    log_buffer.log(
        'Restart', f'Could not release single-instance lock: {shared_memory.errorString()}'
    )
    if not _resume_single_instance_after_handoff_failure():
        msg = 'Original process could not restore single-instance ownership after release failure'
        raise RestartHandoffUncertain(msg)
    return False


def _resume_single_instance_after_handoff_failure() -> bool:
    """Reclaim both single-instance ownership surfaces after a failed restart."""
    shared_memory = _single_instance_state.shared_memory
    if shared_memory is None or not shared_memory.isAttached():
        replacement_lock = QSharedMemory(_SINGLE_INSTANCE_KEY)
        if not replacement_lock.create(1):
            log_buffer.log(
                'Restart',
                'Could not reclaim single-instance lock after failed restart: '
                f'{replacement_lock.errorString()}',
            )
            return False
        _single_instance_state.shared_memory = replacement_lock

    if _single_instance_state.app is None or _single_instance_state.tray is None:
        log_buffer.log(
            'Restart',
            'Could not restore single-instance control endpoint: application state is unavailable',
        )
        return False

    control_server = _start_single_instance_control_server(
        _single_instance_state.app,
        _single_instance_state.tray,
    )
    if control_server is None:
        log_buffer.log(
            'Restart',
            'Could not restore single-instance control endpoint after failed restart',
        )
        return False
    _single_instance_state.control_server = control_server
    return True


def _abort_restart_child_and_wait(
    token: str,
    parent_pid: int,
    application_pid: int | None,
    *,
    is_launcher_alive: Callable[[], bool],
    terminate_launcher: Callable[[], None],
    timeout: float = _RESTART_ABORT_TIMEOUT_SECONDS,
) -> bool:
    """Abort a failed replacement and prove the Python application is gone.

    The outer launcher may be a PyInstaller one-file bootloader, so launcher
    termination alone is never treated as proof that the application child
    exited. Once ``prepared`` reports an application PID, rollback is allowed
    only after that PID is no longer alive.
    """
    _request_restart_abort(token, parent_pid)
    deadline = time.monotonic() + max(0.0, timeout)
    outer_terminated = False
    while time.monotonic() < deadline:
        if application_pid is not None:
            if not _pid_is_alive(application_pid):
                return True
        elif not is_launcher_alive():
            return True

        # Give the child a short opportunity to observe the abort marker and
        # unwind itself before terminating the outer launcher as a fallback.
        if not outer_terminated and time.monotonic() + 2.0 >= deadline:
            terminate_launcher()
            outer_terminated = True
        time.sleep(0.05)

    if application_pid is not None and not _pid_is_alive(application_pid):
        return True
    if application_pid is None and not is_launcher_alive():
        return True

    log_buffer.log(
        'Restart',
        'Replacement application termination could not be confirmed; '
        'single-instance ownership will not be reclaimed',
    )
    return False


def _run_restart_handoff_parent(
    token: str,
    launcher_pid: int,
    *,
    is_launcher_alive: Callable[[], bool],
    terminate_launcher: Callable[[], None],
) -> bool:
    """Parent-side prepared -> release -> ready restart state machine.

    ``launcher_pid`` is diagnostic only. PyInstaller one-file creates a
    bootloader parent plus a Python application child, so protocol identity is
    the random token and ``prepared`` supplies the actual application PID.
    """
    del launcher_pid
    parent_pid = os.getpid()
    ownership_released = False
    handoff_succeeded = False
    application_pid: int | None = None
    try:
        prepared_pid = _wait_for_restart_marker(
            token,
            'prepared',
            is_launcher_alive=is_launcher_alive,
        )
        if prepared_pid is None or not _pid_is_alive(prepared_pid):
            return False
        application_pid = prepared_pid

        if not _suspend_single_instance_for_handoff():
            return False
        ownership_released = True

        if not _pid_is_alive(application_pid):
            return False

        if not _write_restart_handoff_marker(token, 'release', parent_pid):
            return False

        ready_pid = _wait_for_restart_marker(
            token,
            'ready',
            is_launcher_alive=lambda: _pid_is_alive(application_pid),
            expected_value=application_pid,
        )
        if ready_pid != application_pid:
            return False

        handoff_succeeded = True
        return True
    finally:
        if not handoff_succeeded:
            terminated = _abort_restart_child_and_wait(
                token,
                parent_pid,
                application_pid,
                is_launcher_alive=is_launcher_alive,
                terminate_launcher=terminate_launcher,
            )
            if ownership_released:
                if terminated:
                    if not _resume_single_instance_after_handoff_failure():
                        log_buffer.log(
                            'Restart',
                            'Replacement exited, but the original process could not restore '
                            'single-instance ownership completely',
                        )
                        _cleanup_restart_handoff(token)
                        msg = 'Original process could not restore single-instance ownership'
                        raise RestartHandoffUncertain(msg)
                else:
                    log_buffer.log(
                        'Restart',
                        'Rollback is intentionally incomplete because the replacement '
                        'application may still own single-instance/proxy resources',
                    )
                    _cleanup_restart_handoff(token, preserve_abort=True)
                    msg = 'Replacement application may still own restart resources'
                    raise RestartHandoffUncertain(msg)
        _cleanup_restart_handoff(token)


def _terminate_popen_child(process: _ProcessHandle) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=2.0)
        except OSError, subprocess.TimeoutExpired:
            pass
    except OSError:
        pass


def _strip_restart_handoff_args(args: list[str]) -> list[str]:
    """Drop stale protocol credentials before constructing a new relaunch."""
    cleaned: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in {'--restart-handoff-token', '--restart-handoff-parent-pid'}:
            skip_next = True
            continue
        if arg.startswith(('--restart-handoff-token=', '--restart-handoff-parent-pid=')):
            continue
        cleaned.append(arg)
    return cleaned


def restart_fleasion_normally(
    *,
    preserve_env_proxy_player: bool = False,
    verify_startup: bool = False,
    require_admin: bool = False,
) -> bool:
    """Relaunch Fleasion and optionally verify the final replacement.

    Verified restarts use a three-phase handoff. The child first proves it
    survived imports/elevation (``prepared``), then waits while the parent
    releases only the single-instance slot (the working proxy stays alive).
    The parent exits only after the child has claimed that slot and published
    ``ready`` after Hosts-mode proxy startup succeeds.
    """
    existing_args = _strip_restart_handoff_args(list(sys.argv[1:]))
    existing_args = [arg for arg in existing_args if arg != '--preserve-env-proxy-player']

    handoff_token = secrets.token_hex(16) if verify_startup else ''
    handoff_parent_pid = os.getpid() if handoff_token else 0
    if handoff_token:
        _cleanup_restart_handoff(handoff_token)
        existing_args = [arg for arg in existing_args if arg != '--kill-others']
        existing_args.extend(
            [
                '--restart-handoff-token',
                handoff_token,
                '--restart-handoff-parent-pid',
                str(handoff_parent_pid),
            ]
        )
    elif '--kill-others' not in existing_args:
        existing_args.append('--kill-others')

    if preserve_env_proxy_player:
        existing_args.append('--preserve-env-proxy-player')

    if require_admin:
        if sys.platform != 'win32':
            log_buffer.log(
                'Restart', 'Administrator restart was requested on a non-Windows platform'
            )
            return False
        if _is_admin():
            require_admin = False
        else:
            if not handoff_token:
                log_buffer.log('Restart', 'Refusing unverified administrator restart')
                return False
            return _relaunch_as_admin(
                extra_args=('--preserve-env-proxy-player' if preserve_env_proxy_player else ''),
                parent_hwnd=_window_handle(_visible_parent_widget()),
                restart_handoff_token=handoff_token,
                restart_handoff_parent_pid=handoff_parent_pid,
            )

    creationflags = 0
    child_env: dict[str, str] | None = None
    start_new_session = False

    if getattr(sys, 'frozen', False):
        launch = [sys.executable, *existing_args]
        # PyInstaller one-file children must start a fresh extraction/runtime
        # environment. Reusing the current bootloader environment can make an
        # independent relaunch import from the old temporary directory and die
        # with missing stdlib/native modules after the parent exits.
        child_env = os.environ.copy()
        child_env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        if sys.platform != 'win32':
            start_new_session = True
    elif sys.platform == 'win32':
        uv_exe = shutil.which('uv') or shutil.which('uv.exe')
        if uv_exe:
            cwd = str(Path(__file__).resolve().parents[2])
            launch = [uv_exe, '--project', cwd, 'run', 'fleasion', *existing_args]
        else:
            launch = [sys.executable, sys.argv[0], *existing_args]
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        project_root = Path(__file__).resolve().parents[2]
        launcher = project_root / 'launcher.py'
        if launcher.exists():
            launch = [sys.executable, str(launcher), *existing_args]
        else:
            launch = [sys.executable, sys.argv[0], *existing_args]
        start_new_session = True

    try:
        process = _spawn_trusted_command(
            launch,
            env=child_env,
            start_new_session=start_new_session,
            creationflags=creationflags,
        )
    except OSError as exc:
        log_buffer.log('Restart', f'Failed to relaunch Fleasion: {exc}')
        return False

    if handoff_token and not _run_restart_handoff_parent(
        handoff_token,
        process.pid,
        is_launcher_alive=lambda: process.poll() is None,
        terminate_launcher=lambda: _terminate_popen_child(process),
    ):
        return False

    log_buffer.log('Restart', 'Relaunching Fleasion to apply a setting change')
    return True


def _attempt_silent_elevation(  # pyright: ignore[reportUnusedFunction] - retained compatibility helper
    extra_args: str = '', parent_hwnd: int | None = None
) -> bool:
    """Try to elevate silently on startup.

    If already admin, returns True immediately.
    Otherwise fires the UAC prompt. If the user accepts, the elevated
    copy launches and this function calls sys.exit(0) to close the
    non-elevated instance.  If the user declines, returns False so the
    caller continues in read-only mode — no extra dialog shown.
    """
    if _is_admin():
        return True

    success = _relaunch_as_admin(extra_args=extra_args, parent_hwnd=parent_hwnd)
    if success:
        # Elevated copy is now starting up — close this instance silently
        sys.exit(0)

    # User clicked "No" on UAC — stay open in read-only mode
    return False


def _visible_parent_widget() -> QWidget | None:
    """Return the best visible Qt parent for startup dialogs."""
    top = QApplication.topLevelWidgets()
    return next((w for w in top if w.isVisible()), QApplication.activeWindow())


def _window_handle(widget: QWidget | None) -> int | None:
    """Return a native window handle for ShellExecuteExW, if Qt has one."""
    if widget is None:
        return None
    try:
        return int(widget.winId())
    except OverflowError, RuntimeError, TypeError, ValueError:
        return None


def _show_admin_required_dialog(parent: QWidget | None = None) -> None:
    """Warn that the non-elevated instance cannot provide Fleasion's core behavior."""
    top = QApplication.topLevelWidgets()
    parent_ = parent or _visible_parent_widget()
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )

    msg = QMessageBox(parent_)
    if on_top:
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    msg.setWindowTitle(tr('app.administrator_mode_required'))
    msg.setIcon(QMessageBox.Icon.Warning)
    if sys.platform == 'darwin':
        msg.setText(tr('app.fleasion_needs_its_macos_proxy_helper_before'))
        msg.setInformativeText(tr('app.run_fleasion_as_your_normal_macos_user'))
    elif sys.platform.startswith('linux'):
        msg.setText(tr('app.fleasion_needs_administrator_permission_for_linux_interception'))
        msg.setInformativeText(tr('app.linux_support_targets_the_sober_flatpak_client'))
    else:
        msg.setText(tr('app.fleasion_won_t_work_unless_you_re'))
        msg.setInformativeText(tr('app.windows_did_not_start_fleasion_with_administrator'))
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))
    msg.exec()


def _show_proxy_bind_error_dialog(details: ErrorDetails) -> None:
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
                    str(owner.get('local_address') or _UNSPECIFIED_LOCAL_ADDRESS)
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


def _show_hosts_write_exhausted_dialog(details: ErrorDetails) -> None:
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


def _linux_hosts_nix_snippet(details: ErrorDetails) -> str:
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


def _show_linux_hosts_read_only_dialog(details: ErrorDetails) -> None:
    """Show Nix/NixOS guidance when /etc/hosts cannot be edited at runtime."""
    nix_snippet = _linux_hosts_nix_snippet(details)
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


def _show_macos_ca_patch_failed_dialog(details: ErrorDetails) -> str | None:
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


def _show_macos_ca_trust_failed_dialog(details: ErrorDetails) -> None:
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


def _show_roblox_ca_patch_failed_dialog(details: ErrorDetails) -> None:
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
        _visible_parent_widget(),
        tr('app.fleasion_proxy_startup_failed'),
        tr('app.fleasion_could_not_prepare_roblox_player_for', value0=diagnostics),
    )


def _windows_ca_permission_denied_dirs(details: ErrorDetails) -> list[Path]:
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


def _show_macos_relay_failed_dialog(details: ErrorDetails) -> str:
    """Explain a failed privileged relay and return the requested recovery action."""
    lazy_module = importlib.import_module('.utils.macos_proxy_helper', __package__)
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


def _choose_macos_auth_source_on_launch(
    config_manager: ConfigManager, tray: SystemTray | None = None, *, force: bool = False
) -> str:
    """Ask macOS users which browser should be queried for Roblox auth."""
    if sys.platform != 'darwin':
        return 'unavailable'
    if config_manager.macos_auth_source and not force:

        def _configured_auth_source_is_valid() -> bool:
            roblox_auth = importlib.import_module('.utils.roblox_auth', __package__)
            if roblox_auth.get_roblosecurity(include_keychain_browsers=True):
                return True
            log_buffer.log(
                'Auth',
                f'Configured Roblox login source {config_manager.macos_auth_source} did not produce a valid token; reopening browser picker',
            )
            config_manager.macos_auth_source = ''
            roblox_auth.notify_auth_source_changed()
            return False

        try:
            if _call_compatibility_boundary(_configured_auth_source_is_valid):
                return 'already-configured'
        except _CompatibilityBoundaryError as wrapped:
            exc = wrapped.cause
            log_buffer.log(
                'Auth',
                f'Unexpected error while validating configured macOS auth source: {type(exc).__name__}: {exc}',
            )
            config_manager.macos_auth_source = ''

    top = QApplication.topLevelWidgets()
    parent = next((w for w in top if w.isVisible()), None)
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )

    dialog = _MacOSAuthSourceDialog(parent)
    if on_top:
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    dialog.setWindowTitle(tr('app.roblox_login_source'))
    dialog.setMinimumWidth(620)

    selected: dict[str, str] = {}
    buttons: list[QPushButton] = []
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(14)

    title = QLabel(tr('app.which_browser_is_signed_in_to_roblox'))
    title.setStyleSheet('font-size: 18px; font-weight: 700;')
    layout.addWidget(title)

    warning = QLabel(tr('app.most_fleasion_account_aware_features_will_not'))
    warning.setWordWrap(True)
    warning.setStyleSheet('font-weight: 600; color: #e0a53a;')
    layout.addWidget(warning)

    body = QLabel(tr('app.choose_the_browser_where_roblox_com_is'))
    body.setWordWrap(True)
    layout.addWidget(body)

    status = QLabel('')
    status.setWordWrap(True)
    layout.addWidget(status)

    def _set_busy(browser: str) -> None:
        status.setText(tr('app.checking_value_for_a_valid_roblox_login', value0=browser))
        for btn in buttons:
            btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

    def _set_ready(message: str) -> None:
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        status.setText(message)
        for btn in buttons:
            btn.setEnabled(True)

    def _save_and_accept(source: str) -> None:
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        selected['browser'] = source
        dialog.accept()

    def _exit_from_auth_prompt() -> None:
        _quit_after_modal_closes(dialog, tray, selected)

    def _show_safari_unsupported() -> None:
        message = tr('app.auth_source.safari_message')
        msg = QMessageBox(dialog)
        msg.setWindowTitle(tr('app.safari_is_not_supported'))
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(tr('app.safari_cannot_be_used_for_fleasion_login'))
        msg.setInformativeText(message)
        exit_button = msg.addButton(tr('app.exit_fleasion'), QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton(QMessageBox.StandardButton.Ok)
        if icon_path := get_icon_path():
            msg.setWindowIcon(QIcon(str(icon_path)))
        msg.exec()
        if msg.clickedButton() == exit_button:
            _quit_after_modal_closes(dialog, tray, selected)

    grid = QGridLayout()
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(8)
    browsers = (
        ('Chrome', tr('app.auth_source.browser.chrome')),
        ('Safari', tr('app.auth_source.browser.safari')),
        ('Firefox', tr('app.auth_source.browser.firefox')),
        ('Brave', tr('app.auth_source.browser.brave')),
        ('Edge', tr('app.auth_source.browser.edge')),
        ('Chromium', tr('app.auth_source.browser.chromium')),
        ('Opera', tr('app.auth_source.browser.opera')),
        ('Vivaldi', tr('app.auth_source.browser.vivaldi')),
    )

    def _choose(browser: str) -> None:
        _set_busy(browser)

        def _discover_browser_auth() -> tuple[str | None, str | None]:
            roblox_auth = importlib.import_module('.utils.roblox_auth', __package__)
            cookie, source = roblox_auth.discover_browser_roblosecurity(
                include_keychain=True,
                explicit_import=True,
                browser=browser,
            )
            return cookie, source

        try:
            cookie, source = _call_compatibility_boundary(_discover_browser_auth)
        except _CompatibilityBoundaryError as wrapped:
            exc = wrapped.cause
            log_buffer.log(
                'Auth',
                f'Unexpected error while checking {browser}: {type(exc).__name__}: {exc}',
            )
            _set_ready(
                tr(
                    'app.auth_source.check_failed',
                    browser=browser,
                    error_type=type(exc).__name__,
                    error=exc,
                )
            )
            return
        if cookie:
            _save_and_accept(source or browser)
            return
        if browser == 'Safari':
            _set_ready(tr('app.auth_source.safari_ready'))
            _show_safari_unsupported()
            return
        _set_ready(tr('app.auth_source.no_token', browser=browser))

    for index, (browser, browser_label) in enumerate(browsers):
        button = QPushButton(browser_label)
        button.setMinimumHeight(34)
        button.clicked.connect(lambda _checked=False, value=browser: _choose(value))
        grid.addWidget(button, index // 4, index % 4)
        buttons.append(button)
    layout.addLayout(grid)

    footer = QHBoxLayout()
    footer.addStretch()
    exit_btn = QPushButton(tr('app.exit_fleasion'))
    footer.addWidget(exit_btn)
    manual_btn = QPushButton(tr('app.import_token_manually'))
    footer.addWidget(manual_btn)
    skip_btn = QPushButton(tr('app.continue_without_token'))
    footer.addWidget(skip_btn)
    layout.addLayout(footer)
    buttons.extend((manual_btn, skip_btn))

    def _manual_import() -> None:
        lazy_module = importlib.import_module('.gui.rando_stuff_tab', __package__)
        add_account_dialog_cls = lazy_module.AddAccountDialog
        roblox_auth = importlib.import_module('.utils.roblox_auth', __package__)

        dlg = add_account_dialog_cls(dialog, title=tr('app.auth_source.import_title'))
        dlg.set_ok_label(tr('app.auth_source.import_button'))
        if icon_path := get_icon_path():
            dlg.setWindowIcon(QIcon(str(icon_path)))
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result_cookie:
            return
        valid, detail = roblox_auth.validate_roblosecurity_for_import(dlg.result_cookie)
        if not valid:
            QMessageBox.warning(
                dialog,
                tr('app.invalid_roblox_token'),
                tr('app.fleasion_could_not_confirm_this_roblox_token', value0=detail),
            )
            return
        if not roblox_auth.store_manual_roblosecurity(dlg.result_cookie):
            QMessageBox.warning(
                dialog,
                tr('app.token_import_failed'),
                tr('app.fleasion_could_not_store_the_roblox_token'),
            )
            return
        _save_and_accept('manual')

    def _continue_without_token() -> None:
        selected[_AUTH_SKIP_SELECTION_KEY] = '1'
        dialog.allow_reject = True
        dialog.reject()

    manual_btn.clicked.connect(_manual_import)
    skip_btn.clicked.connect(_continue_without_token)
    exit_btn.clicked.connect(_exit_from_auth_prompt)

    if icon_path := get_icon_path():
        dialog.setWindowIcon(QIcon(str(icon_path)))

    dialog.exec()
    if selected_browser := selected.get('browser'):
        config_manager.macos_auth_source = selected_browser
        roblox_auth = importlib.import_module('.utils.roblox_auth', __package__)
        roblox_auth.notify_auth_source_changed()
        if tray is not None:
            tray.refresh_settings_tab()
        return 'selected'
    if selected.get('continue_without_token'):
        return 'skipped'
    if selected.get('exit'):
        return 'exiting'
    return 'dismissed'


def _windows_auth_profile_matches_username(details: ErrorDetails) -> bool:
    """Return whether Windows auth diagnostics describe one coherent user profile."""
    username = str(details.get('username') or '').strip()
    userprofile_text = str(details.get('userprofile') or '').strip()
    local_appdata_text = str(details.get('local_appdata') or '').strip()
    default_cookie_path_text = str(details.get('default_cookie_path') or '').strip()
    if not all((username, userprofile_text, local_appdata_text, default_cookie_path_text)):
        return False

    userprofile = PureWindowsPath(userprofile_text)
    local_appdata = PureWindowsPath(local_appdata_text)
    default_cookie_path = PureWindowsPath(default_cookie_path_text)
    if userprofile.name.casefold() != username.casefold():
        return False

    try:
        local_appdata.relative_to(userprofile)
        default_cookie_path.relative_to(local_appdata)
    except ValueError:
        return False
    return True


def _show_auth_cookie_unavailable_dialog(
    details: ErrorDetails, tray: SystemTray | None = None
) -> None:
    """Show a user-facing popup when no readable Roblox auth cookie can be found."""
    top = QApplication.topLevelWidgets()
    parent = next((w for w in top if w.isVisible()), None)
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )

    discord_url = APP_DISCORD
    if not discord_url.startswith(('http://', 'https://')):
        discord_url = f'https://{discord_url}'

    attempted_value = details.get('attempted_paths')
    existing_value = details.get('existing_paths')
    attempted = attempted_value if _is_object_list(attempted_value) else []
    existing = existing_value if _is_object_list(existing_value) else []

    existing_html = ''
    if existing:
        existing_html = tr(
            'app.auth_warning.existing_files',
            paths='<br>'.join(html.escape(str(path)) for path in existing[:8]),
        )

    skipped_token = bool(details.get('user_skipped_token'))
    if sys.platform == 'darwin':
        diagnostics_html = tr(
            'app.auth_warning.macos_diagnostics',
            home=html.escape(str(details.get('home') or tr('app.common.unknown'))),
            local_appdata=html.escape(
                str(details.get('local_appdata') or tr('app.common.unknown'))
            ),
            default_cookie_path=html.escape(
                str(details.get('default_cookie_path') or tr('app.common.unknown'))
            ),
            attempted_count=len(attempted),
        )
        most_likely_html = tr(
            'app.auth_warning.macos_guidance',
            lead=(
                tr('app.auth_warning.macos_skipped')
                if skipped_token
                else tr('app.auth_warning.macos_none')
            ),
        )
    elif sys.platform.startswith('linux'):
        diagnostics_html = tr(
            'app.auth_warning.linux_diagnostics',
            home=html.escape(str(details.get('home') or tr('app.common.unknown'))),
            local_appdata=html.escape(
                str(details.get('local_appdata') or tr('app.common.unknown'))
            ),
            default_cookie_path=html.escape(
                str(details.get('default_cookie_path') or tr('app.common.unknown'))
            ),
            attempted_count=len(attempted),
        )
        most_likely_html = tr(
            'app.auth_warning.linux_guidance',
            lead=(
                tr('app.auth_warning.linux_skipped')
                if skipped_token
                else tr('app.auth_warning.linux_none')
            ),
        )
    else:
        diagnostics_html = tr(
            'app.auth_warning.windows_diagnostics',
            username=html.escape(str(details.get('username') or tr('app.common.unknown'))),
            userprofile=html.escape(str(details.get('userprofile') or tr('app.common.unknown'))),
            local_appdata=html.escape(
                str(details.get('local_appdata') or tr('app.common.unknown'))
            ),
            default_cookie_path=html.escape(
                str(details.get('default_cookie_path') or tr('app.common.unknown'))
            ),
            attempted_count=len(attempted),
        )
        most_likely_html = tr(
            'app.auth_warning.windows_same_user_guidance'
            if _windows_auth_profile_matches_username(details)
            else 'app.auth_warning.windows_guidance'
        )

    msg = _ForcedAcknowledgeMessageBox(parent)
    if on_top:
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    msg.setWindowTitle(tr('app.roblox_token_not_readable'))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(
        tr('app.fleasion_is_continuing_without_a_roblox_login')
        if skipped_token
        else tr('app.fleasion_could_not_read_a_usable_roblox')
    )
    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setInformativeText(
        tr(
            'app.auth_warning.info_skipped'
            if skipped_token
            else 'app.auth_warning.info_unreadable',
            most_likely_html=most_likely_html,
            existing_html=existing_html,
            diagnostics_html=diagnostics_html,
            discord_url=html.escape(discord_url),
            discord_label=html.escape(APP_DISCORD),
        )
    )
    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        open_login_button = msg.addButton(
            tr('app.open_roblox_login'), QMessageBox.ButtonRole.ActionRole
        )
        exit_button = msg.addButton(tr('app.exit_fleasion'), QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton(QMessageBox.StandardButton.Ok)
    else:
        open_login_button = None
        exit_button = msg.addButton(tr('app.exit_fleasion'), QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton(QMessageBox.StandardButton.Ok)

    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))

    for label in msg.findChildren(QLabel):
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        label.setOpenExternalLinks(True)

    ack_buttons = list(msg.buttons())
    countdown_buttons = [button for button in ack_buttons if button is not exit_button]
    for button in countdown_buttons:
        button.setEnabled(False)
    ok_button = msg.button(QMessageBox.StandardButton.Ok)
    remaining_seconds = 5
    ok_button.setText(tr('app.ok_value_s', value0=remaining_seconds))

    countdown_timer = QTimer(msg)
    countdown_timer.setInterval(1000)

    def _update_auth_warning_countdown() -> None:
        nonlocal remaining_seconds
        remaining_seconds -= 1
        if remaining_seconds <= 0:
            countdown_timer.stop()
            msg.allow_close()
            for button in countdown_buttons:
                button.setEnabled(True)
            ok_button.setText(tr('app.ok'))
        else:
            ok_button.setText(tr('app.ok_value_s', value0=remaining_seconds))

    countdown_timer.timeout.connect(_update_auth_warning_countdown)
    countdown_timer.start()

    def _exit_from_warning() -> None:
        _quit_after_modal_closes(msg, tray)

    exit_button.clicked.connect(_exit_from_warning)

    msg.exec()
    if msg.clickedButton() == exit_button:
        return
    if open_login_button is not None and msg.clickedButton() == open_login_button:
        if sys.platform.startswith('linux'):
            launch_as_standard_user('https://www.roblox.com/login')
        else:
            webbrowser.open('https://www.roblox.com/login')


def _show_windows_upstream_firewall_dialog(details: ErrorDetails) -> None:
    """Explain a blocked upstream connection and offer a targeted UAC repair."""
    if sys.platform != 'win32':
        return

    windows_firewall = importlib.import_module('.utils.windows_firewall', __package__)

    host = str(details.get('host') or tr('app.firewall.default_content_server'))
    parent = _visible_parent_widget()
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
            relaunched = _relaunch_as_admin(
                extra_args='--repair-firewall',
                parent_hwnd=_window_handle(parent),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            clear_pending_repair(CONFIG_DIR)
            log_buffer.log('WindowsFirewall', f'Could not start elevated firewall repair: {exc}')
            relaunched = False

        if relaunched:
            log_buffer.log('WindowsFirewall', 'Elevated Fleasion firewall repair started')
            deadline = time.monotonic() + _WINDOWS_FIREWALL_REPAIR_RESULT_TIMEOUT_SECONDS
            QTimer.singleShot(500, lambda: _poll_windows_firewall_repair(deadline))
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
            _spawn_trusted_command(
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


def _show_tls_self_test_failed_dialog(details: ErrorDetails) -> None:
    """Show a user-facing error when the proxy cannot pass startup TLS checks."""
    hosts_value = details.get('hosts')
    hosts = hosts_value if _is_object_list(hosts_value) else []
    host_text = ', '.join(str(host) for host in hosts) or tr('app.tls_self_test.default_routes')
    QMessageBox.critical(
        _visible_parent_widget(),
        tr('app.fleasion_proxy_startup_failed'),
        tr('app.fleasion_could_not_start_its_local_proxy_2', value0=host_text),
    )


class _ProxyErrorInvoker(QObject):
    """Main-thread bridge for proxy startup errors emitted from worker threads."""

    show_proxy_error = Signal(str, dict)
    disable_proxy_features = Signal(str)
    retry_proxy = Signal()

    @Slot(str, dict)
    def handle_proxy_error(self, code: str, details: dict[str, object]) -> None:
        if code == 'port_bind_failed':
            _show_proxy_bind_error_dialog(details)
        elif code == 'hosts_write_exhausted':
            _show_hosts_write_exhausted_dialog(details)
        elif code == 'hosts_entries_would_exceed_limit':
            _show_hosts_capacity_dialog(details)
        elif code in {'hosts_file_too_large', 'hosts_file_repair_failed'}:
            _show_oversized_hosts_file_dialog(details, on_repaired=self.retry_proxy.emit)
        elif code == 'linux_hosts_read_only':
            _show_linux_hosts_read_only_dialog(details)
        elif code == 'macos_ca_patch_failed':
            if _show_macos_ca_patch_failed_dialog(details) == 'install_helper':
                macos_proxy_helper = importlib.import_module(
                    '.utils.macos_proxy_helper', __package__
                )

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
                        _visible_parent_widget(),
                        tr('app.fleasion_proxy_helper_installation_failed'),
                        tr('app.fleasion_could_not_install_or_start_the', value0=detail),
                    )
        elif code == 'macos_ca_trust_failed':
            _show_macos_ca_trust_failed_dialog(details)
        elif code == 'roblox_ca_patch_failed':
            denied_dirs = _windows_ca_permission_denied_dirs(details)
            if denied_dirs:
                _show_roblox_permission_failure(
                    _visible_parent_widget(),
                    denied_dirs,
                    on_repaired=self.retry_proxy.emit,
                    failure_text=tr('app.roblox_permissions.env_proxy_failure'),
                )
            else:
                _show_roblox_ca_patch_failed_dialog(details)
        elif code == 'macos_relay_failed':
            action = _show_macos_relay_failed_dialog(details)
            if action == 'retry':
                self.retry_proxy.emit()
            elif action == 'reinstall':
                macos_proxy_helper = importlib.import_module(
                    '.utils.macos_proxy_helper', __package__
                )

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
                        _visible_parent_widget(),
                        tr('app.fleasion_proxy_helper_reinstall_failed'),
                        tr('app.fleasion_could_not_reinstall_or_restart_the', value0=detail),
                    )
        elif code == 'upstream_connect_failed':
            _show_windows_upstream_firewall_dialog(details)
        elif code == 'tls_self_test_failed':
            _show_tls_self_test_failed_dialog(details)


def _manual_upstream_credentials_missing(config_manager: ConfigManager) -> bool:
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


def _disable_proxy_features_after_start_failure(
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
            _visible_parent_widget(),
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


class _AuthCheckInvoker(QObject):
    """Main-thread bridge for the potentially prompting browser auth check."""

    completed = Signal(bool, dict)


class _RobloxUrlEventFilter(QObject):
    """Receive Roblox URL open events delivered to the macOS app bundle."""

    roblox_uri_received = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ready = False
        self._pending: list[str] = []

    @staticmethod
    def _event_target(event: QEvent) -> str | None:
        if event.type() != QEvent.Type.FileOpen:
            return None
        file_open_event = cast('QFileOpenEvent', event)
        try:
            url = file_open_event.url()
            target = url.toString() if url.isValid() else ''
        except AttributeError:
            target = ''
        target = str(target).strip()
        if target.startswith(('roblox:', 'roblox-player:')):
            return target
        return None

    @override
    def eventFilter(self, _watched: QObject | None, event: QEvent) -> bool:
        target = self._event_target(event)
        if target is not None:
            if self._ready:
                self.roblox_uri_received.emit(target)
            else:
                self._pending.append(target)
        return False

    def start(self) -> None:
        self._ready = True
        pending = self._pending
        self._pending = []
        for target in pending:
            self.roblox_uri_received.emit(target)


class RobloxExitMonitor(QObject):
    """Monitors Roblox process and triggers cache deletion on exit."""

    _studio_detected = Signal()
    player_status_changed = Signal(
        bool
    )  # Emitted when RobloxPlayerBeta opens/closes (True = running)

    def __init__(
        self,
        config_manager: ConfigManager,
        proxy_master: ProxyMaster | None = None,
        mod_manager: ModificationManager | None = None,
        env_lifecycle: EnvProxyLifecycleController | None = None,
    ) -> None:
        super().__init__()
        self.config_manager = config_manager
        self._proxy_master = proxy_master
        self._mod_manager = mod_manager
        self.env_lifecycle = env_lifecycle
        self._status_lock = threading.Lock()
        adopted_running = bool(env_lifecycle and env_lifecycle.owns_player)
        self.was_running = adopted_running
        self._player_was_running = adopted_running
        self._suppress_next_player_exit_cache_delete = False
        self._studio_was_running = False
        self._studio_notified = False
        self._studio_suppress_session = False
        self._macos_uri_interceptor = None
        self._macos_plain_launch_lock = threading.Lock()
        self._macos_plain_launches: dict[int, Path] = {}
        macos_env_proxy_enabled = (
            sys.platform == 'darwin'
            and env_lifecycle is not None
            and proxy_master is not None
            and config_manager.proxy_mode == 'env'
            and config_manager.proxy_features_enabled
        )
        if macos_env_proxy_enabled and callable(
            getattr(proxy_master, 'wait_for_env_proxy_ready', None)
        ):
            try:
                lazy_module = importlib.import_module('.utils.platform_macos', __package__)
                macos_roblox_uri_interceptor_cls = lazy_module.MacOSRobloxUriInterceptor

                self._macos_uri_interceptor = macos_roblox_uri_interceptor_cls(
                    is_armed=self._macos_uri_interception_armed,
                    on_intercepted=self._handle_macos_uri_interception,
                )
                self._macos_uri_interceptor.start()
            except (
                AttributeError,
                ImportError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                log_buffer.log(
                    'Launcher',
                    f'Could not start macOS Roblox URI watcher: {type(exc).__name__}: {exc}',
                )
        self._studio_detected.connect(self._on_studio_detected)

    def mark_player_running_at_startup(self) -> None:
        """Record a Player that was already running before monitoring began."""
        self.was_running = True
        self._player_was_running = True

    def is_player_running(self) -> bool:
        """Return whether Roblox Player is currently running."""
        return is_roblox_running()

    def stop(self) -> None:
        """Release the macOS URI watcher during normal application shutdown."""
        with self._macos_plain_launch_lock:
            self._macos_plain_launches.clear()
        interceptor = self._macos_uri_interceptor
        if interceptor is not None:
            interceptor.stop()

    def _macos_uri_interception_armed(self) -> bool:
        """Return whether it is safe to stop and replay a browser URI launch."""
        if sys.platform != 'darwin' or self._proxy_master is None or self.env_lifecycle is None:
            return False
        if (
            self.config_manager.proxy_mode != 'env'
            or not self.config_manager.proxy_features_enabled
            or self.env_lifecycle.owns_player
            or self.env_lifecycle.operation_in_progress
        ):
            return False
        ready = getattr(self._proxy_master, 'wait_for_env_proxy_ready', None)
        try:
            return _call_compatibility_boundary(
                lambda: bool(callable(ready) and ready(timeout=0.0))
            )
        except _CompatibilityBoundaryError:
            return False

    def _handle_macos_uri_interception(self, launch: MacOSRobloxPlayerLaunch, target: str) -> None:
        """Run after the watcher has already SIGKILLed the original Player."""
        if self.env_lifecycle is None:
            return
        self._suppress_next_player_exit_cache_delete = True
        with self._macos_plain_launch_lock:
            self._macos_plain_launches.pop(int(launch.pid), None)
        self.env_lifecycle.handle_intercepted_player_launch(Path(launch.executable_path), target)

    def _schedule_macos_plain_launch_fallback(self, exe_path: Path) -> None:
        """Keep ordinary Dock/Finder launches on the existing Env Proxy path."""
        if self._macos_uri_interceptor is None:
            return
        identity = get_roblox_process_identity()
        if not isinstance(identity, tuple) or not identity:
            return
        try:
            pid = int(identity[0])
        except TypeError, ValueError:
            return
        with self._macos_plain_launch_lock:
            if pid in self._macos_plain_launches:
                return
            self._macos_plain_launches[pid] = Path(exe_path)

        def _fallback() -> None:
            time.sleep(_MACOS_PLAIN_LAUNCH_CLASSIFICATION_SECONDS)
            with self._macos_plain_launch_lock:
                pending_exe = self._macos_plain_launches.pop(pid, None)
            if pending_exe is None or self.env_lifecycle is None:
                return
            if (
                self._macos_uri_interceptor is not None
                and self._macos_uri_interceptor.has_claimed_pid(pid)
            ):
                return
            current_identity = get_roblox_process_identity()
            if not isinstance(current_identity, tuple) or not current_identity:
                return
            try:
                if int(current_identity[0]) != pid:
                    return
            except TypeError, ValueError:
                return
            if not self._macos_uri_interception_armed():
                return
            self._suppress_next_player_exit_cache_delete = True
            self.env_lifecycle.handle_player_launch(pending_exe)

        threading.Thread(
            target=_fallback,
            name='FleasionMacOSPlainLaunchFallback',
            daemon=True,
        ).start()

    @run_in_thread
    def check_roblox_status(self) -> None:
        """Coalesce timer ticks so process edges are handled exactly once."""
        if not self._status_lock.acquire(blocking=False):
            return
        try:
            self._check_roblox_status_locked()
        finally:
            self._status_lock.release()

    def _handle_player_launch_detected(self) -> None:
        if sys.platform.startswith('linux'):
            exe_path = _linux_client_launch_path()
            if self._mod_manager is not None:
                self._mod_manager.refresh_roblox_dirs(reapply_if_changed=True)
            proxy_features_enabled = self.config_manager.proxy_features_enabled
            if (
                self.config_manager.proxy_mode == 'env'
                and self._proxy_master is not None
                and proxy_features_enabled
            ):
                if self.env_lifecycle is not None:
                    run_in_thread(self.env_lifecycle.handle_adopted_player_launch)(exe_path)
            elif self._proxy_master is not None and proxy_features_enabled:
                run_in_thread(self._proxy_master.refresh_and_restart_roblox)(exe_path)
            elif self._proxy_master is None and proxy_features_enabled:
                run_in_thread(check_and_patch_running_roblox_ca)(exe_path)
            elif not proxy_features_enabled:
                log_buffer.log(
                    'Certificate',
                    f'{_linux_client_display_name()} launch detected: proxy features '
                    'disabled, skipping proxy CA refresh',
                )
        else:
            exe_path = get_roblox_player_exe_path()
            if exe_path is None:
                # Process may still be initializing — retry for up to 10 s
                for _ in range(10):
                    time.sleep(1.0)
                    exe_path = get_roblox_player_exe_path()
                    if exe_path is not None:
                        break
            if exe_path is not None:
                proxy_features_enabled = self.config_manager.proxy_features_enabled
                if self._mod_manager is not None:
                    self._mod_manager.refresh_roblox_dirs(reapply_if_changed=True)
                if (
                    sys.platform == 'win32'
                    and self.config_manager.proxy_mode == 'env'
                    and self._proxy_master is not None
                    and proxy_features_enabled
                ):
                    platform_windows = importlib.import_module(
                        '.utils.platform_windows', __package__
                    )

                    if platform_windows.is_roblox_gdk_exe_path(exe_path):
                        gdk_env_proxy_armed = (
                            platform_windows.is_roblox_gdk_env_proxy_armed()
                            or platform_windows.is_gdk_env_proxy_activation_in_progress()
                        )
                        if gdk_env_proxy_armed and self.env_lifecycle is not None:
                            log_buffer.log(
                                'Launcher',
                                'Xbox/GDK Env Proxy package activation supplied the '
                                'initial Player; handing it to Env Proxy lifecycle monitoring',
                            )
                            self._suppress_next_player_exit_cache_delete = True
                            run_in_thread(self.env_lifecycle.handle_adopted_player_launch)(exe_path)
                        else:
                            log_buffer.log(
                                'Launcher',
                                'Xbox/GDK Env Proxy package activation is unavailable; '
                                'leaving the initial package Player untouched',
                            )
                            self._suppress_next_player_exit_cache_delete = False
                    elif platform_windows.is_env_proxy_relaunched_player_running():
                        log_buffer.log(
                            'Launcher',
                            'Roblox Env Proxy Player already running; skipping duplicate launch handling',
                        )
                        self._suppress_next_player_exit_cache_delete = False
                    else:
                        self._suppress_next_player_exit_cache_delete = True

                        def _handle_env_proxy_player_launch() -> None:
                            lifecycle = self.env_lifecycle
                            started = bool(
                                lifecycle is not None and lifecycle.handle_player_launch(exe_path)
                            )
                            if not started:
                                self._suppress_next_player_exit_cache_delete = False

                        run_in_thread(_handle_env_proxy_player_launch)()
                elif (
                    self.config_manager.proxy_mode == 'env'
                    and self._proxy_master is not None
                    and proxy_features_enabled
                ):
                    if self.env_lifecycle is not None:
                        if sys.platform == 'darwin':
                            self._schedule_macos_plain_launch_fallback(exe_path)
                        else:
                            run_in_thread(self.env_lifecycle.handle_player_launch)(exe_path)
                elif self._proxy_master is not None and proxy_features_enabled:
                    run_in_thread(self._proxy_master.refresh_and_restart_roblox)(exe_path)
                elif self._proxy_master is None and proxy_features_enabled:
                    run_in_thread(check_and_patch_running_roblox_ca)(exe_path)
                elif not proxy_features_enabled:
                    log_buffer.log(
                        'Certificate',
                        'Roblox launch detected: proxy features disabled, skipping proxy CA refresh',
                    )
            else:
                log_buffer.log(
                    'Certificate',
                    'Roblox launch detected but could not resolve exe path for CA check',
                )

    def _check_roblox_status_locked(self) -> None:
        """Check if Roblox has exited and trigger cache deletion if needed."""
        is_running = is_roblox_running()
        player_status_observed_at = time.monotonic()
        intentional_player_exit = False

        # Roblox Player: player status changed signal
        if self._player_was_running != is_running:
            self.player_status_changed.emit(is_running)
            if self._proxy_master is not None:
                self._proxy_master.set_roblox_player_running(is_running)
            if self._player_was_running and not is_running and self.env_lifecycle is not None:
                intentional_player_exit = self.env_lifecycle.consume_intentional_player_exit(
                    player_status_observed_at
                )
                if not intentional_player_exit:
                    self.env_lifecycle.note_unexpected_player_exit()

        # Roblox Player: launch detection - check CA cert on new launch
        if not self._player_was_running and is_running:
            self._handle_player_launch_detected()
        self._player_was_running = is_running

        # Roblox Player: auto cache deletion on exit
        if self.config_manager.auto_delete_cache_on_exit:
            if self.was_running and not is_running:
                if intentional_player_exit or self._suppress_next_player_exit_cache_delete:
                    self._suppress_next_player_exit_cache_delete = False
                    log_buffer.log(
                        'Cache',
                        'Roblox exited during env-proxy relaunch; skipping auto cache deletion',
                    )
                else:
                    log_buffer.log('Cache', 'Roblox exited, deleting cache...')
                    run_in_thread(self._delete_cache_background)()
            self.was_running = is_running
        else:
            self.was_running = False

        # Roblox Studio: Env Proxy deliberately leaves Studio untouched
        studio_running = is_studio_running()

        if not self._studio_was_running and studio_running:
            env_proxy_mode = self.config_manager.proxy_mode == 'env'
            if env_proxy_mode:
                log_buffer.log(
                    'Launcher',
                    'Studio launch detected in Env Proxy mode; leaving Studio untouched',
                )
                studio_exe_path = None
            else:
                studio_exe_path = get_roblox_studio_exe_path()
                if studio_exe_path is None:
                    for _ in range(10):
                        time.sleep(1.0)
                        studio_exe_path = get_roblox_studio_exe_path()
                        if studio_exe_path is not None:
                            break

            if (
                not env_proxy_mode
                and studio_exe_path is not None
                and self.config_manager.proxy_features_enabled
            ):
                if sys.platform == 'darwin':
                    log_buffer.log(
                        'Certificate',
                        'Studio launch detected on macOS: skipping proxy CA refresh',
                    )
                else:
                    run_in_thread(check_and_patch_running_roblox_ca)(studio_exe_path)
            elif not env_proxy_mode and studio_exe_path is not None:
                log_buffer.log(
                    'Certificate',
                    'Studio launch detected: proxy features disabled, skipping proxy CA refresh',
                )
            elif not env_proxy_mode:
                log_buffer.log(
                    'Certificate',
                    'Studio launch detected but could not resolve exe path for CA check',
                )

            if (
                not env_proxy_mode
                and not self._studio_suppress_session
                and not self._studio_notified
            ):
                self._studio_notified = True
                self._studio_detected.emit()

        if self._studio_was_running and not studio_running:
            self._studio_notified = False

        self._studio_was_running = studio_running

    def _on_studio_detected(self) -> None:
        """Show the Roblox Studio warning dialog (called on the main thread via signal)."""
        top = QApplication.topLevelWidgets()
        parent = next((w for w in top if w.isVisible()), None)
        on_top = any(
            w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            for w in top
        )
        dialog = QDialog(parent)
        if on_top:
            dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        dialog.setWindowTitle(tr('app.roblox_studio_detected'))

        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        label = QLabel(tr('app.roblox_studio_is_currently_open_asset_modification'))
        label.setWordWrap(True)
        layout.addWidget(label)

        btn_layout = QHBoxLayout()
        suppress_btn = QPushButton(tr('app.don_t_show_for_session'))
        ok_btn = QPushButton(tr('app.ok'))
        ok_btn.setDefault(True)
        ok_btn.setFixedWidth(80)

        btn_layout.addWidget(suppress_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        if icon_path := get_icon_path():
            dialog.setWindowIcon(QIcon(str(icon_path)))

        ok_btn.clicked.connect(dialog.accept)

        def _suppress() -> None:
            self._studio_suppress_session = True
            dialog.accept()

        suppress_btn.clicked.connect(_suppress)
        dialog.exec()

    def _delete_cache_background(self) -> None:
        """Delete cache in background thread."""
        messages = delete_cache()
        for msg in messages:
            log_buffer.log('Cache', msg)


def _looks_like_fleasion_gui_command(command: str) -> bool:
    """Return whether a process command is a Fleasion GUI app/dev launch."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens or '--linux-proxy-helper' in tokens:
        return False
    if any(Path(argument).name == 'linux_proxy_helper_daemon.py' for argument in tokens):
        return False

    executable = Path(tokens[0]).name.lower()
    if executable == 'fleasion' or executable.startswith('fleasion-v'):
        return True

    # ``uv run fleasion`` and virtual-environment entry points are executed as
    # ``python …/bin/fleasion``.  The old check only considered argv[0], so
    # Linux's Kill Others action missed the already-running GUI and a second
    # instance later failed on the proxy backend port.
    if any(Path(argument).name.lower() == 'fleasion' for argument in tokens[1:]):
        return True

    return any(
        Path(argument).name == 'launcher.py'
        or (
            argument == '-m' and index + 1 < len(tokens) and tokens[index + 1].lower() == 'fleasion'
        )
        for index, argument in enumerate(tokens)
    )


def _looks_like_macos_fleasion_command(  # pyright: ignore[reportUnusedFunction] - compatibility import used by external tests
    command: str,
) -> bool:
    """Compatibility wrapper for tests and older imports."""
    return _looks_like_fleasion_gui_command(command)


def _parse_posix_fleasion_pids(output: str, safe_pids: set[int]) -> list[int]:
    pids: list[int] = []
    for raw in output.splitlines():
        try:
            pid_text, _ppid_text, command = raw.strip().split(None, 2)
            pid = int(pid_text)
        except ValueError, TypeError:
            continue
        if pid not in safe_pids and _looks_like_fleasion_gui_command(command):
            pids.append(pid)
    return pids


def _parse_tasklist_pids(output: str, safe_pids: set[int]) -> list[int]:
    pids: list[int] = []
    for raw_line in output.strip().splitlines():
        line = raw_line.strip().strip('"')
        parts = line.split('","')
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[1])
        except ValueError, IndexError:
            continue
        if pid not in safe_pids:
            pids.append(pid)
    return pids


def _parse_powershell_fleasion_pids(output: str, safe_pids: set[int]) -> list[int]:
    try:
        raw_data: object = json.loads(output)
    except json.JSONDecodeError, TypeError, ValueError:
        return []
    if isinstance(raw_data, dict):
        records: list[object] = [raw_data]
    elif isinstance(raw_data, list):
        records = cast('list[object]', raw_data)
    else:
        return []

    pids: list[int] = []
    for record in records:
        if not _is_error_details(record):
            continue
        pid = _get_int_detail(record, 'ProcessId', 0)
        cmdline = str(record.get('CommandLine') or '').lower()
        if (
            pid not in safe_pids
            and pid != 0
            and ('launcher.py' in cmdline or 'fleasion' in cmdline)
        ):
            pids.append(pid)
    return pids


def _other_fleasion_pids() -> list[int]:
    """Return PIDs of other Fleasion GUI processes (excludes current process and its parent)."""
    safe_pids = {os.getpid(), os.getppid()}
    exe_name = Path(sys.executable).name

    if sys.platform != 'win32':
        try:
            result = _run_trusted_text_command(
                [_resolve_executable('ps'), '-axo', 'pid=,ppid=,command='],
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log_buffer.log('App', f'Could not inspect running Fleasion processes: {exc}')
            return []
        return _parse_posix_fleasion_pids(result.stdout, safe_pids)

    if exe_name.lower() not in {'python.exe', 'python3.exe'}:
        try:
            result = _run_trusted_text_command(
                [
                    _resolve_executable('tasklist'),
                    '/FI',
                    f'IMAGENAME eq {exe_name}',
                    '/FO',
                    'CSV',
                    '/NH',
                ],
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log_buffer.log('App', f'Could not inspect running Fleasion processes: {exc}')
            return []
        return _parse_tasklist_pids(result.stdout, safe_pids)

    ps_cmd = (
        'Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" | '
        'Select-Object ProcessId, CommandLine | ConvertTo-Json -Depth 1'
    )
    try:
        result = _run_trusted_text_command(
            [_resolve_executable('powershell'), '-NoProfile', '-Command', ps_cmd],
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('App', f'Could not inspect running Fleasion processes: {exc}')
        return []
    return _parse_powershell_fleasion_pids(result.stdout, safe_pids)


def _should_reclaim_stale_single_instance(
    error: QSharedMemory.SharedMemoryError,
) -> bool:
    """Return True when a stale Qt singleton marker can be safely reclaimed."""
    if error != QSharedMemory.SharedMemoryError.AlreadyExists:
        return False
    if not (sys.platform == 'darwin' or sys.platform.startswith('linux')):
        return False
    return not _other_fleasion_pids()


def _send_running_instance_command(payload: str, timeout_ms: int) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(_SINGLE_INSTANCE_CONTROL_SERVER)
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(payload.encode())
    socket.waitForBytesWritten(1000)
    socket.disconnectFromServer()
    socket.waitForDisconnected(1000)
    return True


def _request_running_instance_exit(
    timeout_ms: int = 2000,
    *,
    preserve_env_proxy_player: bool = False,
) -> bool:
    """Ask the already-running Fleasion instance to exit through its Qt event loop."""
    command = 'quit-preserve-env-player' if preserve_env_proxy_player else 'quit'
    try:
        return _send_running_instance_command(f'{command}\n', timeout_ms)
    except OSError, RuntimeError:
        return False


def _roblox_uri_from_argv() -> str | None:
    """Return a Roblox deeplink passed by a desktop URI handler, if any."""
    for argument in sys.argv[1:]:
        target = str(argument).strip()
        if target.startswith(('roblox:', 'roblox-player:')):
            return target
    return None


def _request_running_instance_launch(target: str, timeout_ms: int = 5000) -> bool:
    """Forward a Roblox deeplink to the already-running Fleasion instance."""
    try:
        return _send_running_instance_command(f'launch-roblox\n{target}\n', timeout_ms)
    except OSError, RuntimeError:
        return False


def _launch_roblox_uri_for_instance(tray: SystemTray, target: str) -> bool:
    """Launch a URI through the active proxy mode."""
    if sys.platform.startswith('linux'):
        # Flatpak supplies Fleasion's Env Proxy variables to the selected client while the
        # proxy is active; do not replace a one-time URI with a synthetic launch.
        return launch_as_standard_user(target)

    config = tray.config_manager
    if sys.platform == 'darwin' and config.proxy_mode == 'env' and config.proxy_features_enabled:
        monitor = tray.roblox_monitor
        lifecycle = monitor.env_lifecycle if monitor is not None else None
        if lifecycle is not None:
            exe_path = (
                _linux_client_launch_path()
                if sys.platform.startswith('linux')
                else get_roblox_player_exe_path()
            )
            return lifecycle.handle_player_launch(exe_path, target)

    return launch_as_standard_user(target)


def _arm_windows_gdk_env_proxy_when_ready(proxy_master: ProxyMaster, timeout: float = 15.0) -> bool:
    """Arm Store/GDK activation with the proxy's finalized loopback port."""
    if not proxy_master.wait_for_env_proxy_ready(timeout=timeout):
        log_buffer.log(
            'Launcher',
            'Xbox/GDK Env Proxy activation was not armed because the proxy did not become ready',
        )
        return False

    platform_windows = importlib.import_module('.utils.platform_windows', __package__)
    if not platform_windows.arm_roblox_gdk_env_proxy(proxy_master.roblox_env_proxy_url()):
        return False
    atexit.register(platform_windows.disarm_roblox_gdk_env_proxy)
    return True


def _wait_for_other_fleasion_instances_to_exit(timeout_seconds: float = 8.0) -> bool:
    """Wait until no other Fleasion processes remain."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _other_fleasion_pids():
            return True
        time.sleep(0.1)
    return not _other_fleasion_pids()


def _request_other_fleasion_instances_exit(
    timeout_seconds: float = 8.0,
    *,
    preserve_env_proxy_player: bool = False,
) -> bool:
    """Return True if other instances were asked to exit and disappeared."""
    if not _other_fleasion_pids():
        return True
    if not _request_running_instance_exit(preserve_env_proxy_player=preserve_env_proxy_player):
        return False
    return _wait_for_other_fleasion_instances_to_exit(timeout_seconds)


def _handle_single_instance_command(socket: QLocalSocket, tray: SystemTray) -> None:
    try:
        command = bytes(socket.readAll().data()).decode('utf-8', errors='replace').strip()
    except RuntimeError:
        return
    if command.lower() == 'quit':
        tray.exit_app()
    elif command.lower() == 'quit-preserve-env-player':
        tray.exit_app(preserve_roblox=True)
    elif command.lower().startswith('launch-roblox\n'):
        target = command.split('\n', 1)[1].strip()
        if target.startswith(('roblox:', 'roblox-player:')):
            run_in_thread(_launch_roblox_uri_for_instance)(tray, target)


def _start_single_instance_control_server(
    app: QApplication, tray: SystemTray
) -> QLocalServer | None:
    """Start a local control endpoint for clean single-instance handoff."""
    server = QLocalServer(app)

    if not server.listen(_SINGLE_INSTANCE_CONTROL_SERVER):
        QLocalServer.removeServer(_SINGLE_INSTANCE_CONTROL_SERVER)
        if not server.listen(_SINGLE_INSTANCE_CONTROL_SERVER):
            log_buffer.log('App', 'Single-instance control server could not start')
            return None

    def _handle_connection() -> None:
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: _handle_single_instance_command(s, tray))
            if socket.bytesAvailable() > 0:
                _handle_single_instance_command(socket, tray)

    server.newConnection.connect(_handle_connection)
    return server


def _terminate_other_fleasion_pid(pid: int) -> None:
    if sys.platform != 'win32':
        os.kill(pid, signal.SIGTERM)
        return

    taskkill = _resolve_executable('taskkill')
    _run_trusted_command(
        [taskkill, '/PID', str(pid)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=5,
    )
    if _wait_for_other_fleasion_instances_to_exit(2.0):
        return
    _run_trusted_command(
        [taskkill, '/F', '/PID', str(pid)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=5,
    )


def kill_other_fleasion_instances() -> None:
    """Kill all other Fleasion instances except the current process."""

    if _request_other_fleasion_instances_exit():
        return

    for pid in _other_fleasion_pids():
        try:
            _terminate_other_fleasion_pid(pid)
        except (OSError, subprocess.SubprocessError) as exc:
            log_buffer.log('App', f'Could not terminate Fleasion process {pid}: {exc}')


def _configure_opengl_for_legacy_viewers() -> None:
    """Set only platform policy needed by future OpenGL preview widgets."""
    if sys.platform.startswith('linux'):
        os.environ.setdefault('QT_OPENGL', 'desktop')
    # Do not enable AA_ShareOpenGLContexts or set a global QSurfaceFormat here.
    # Either action is unnecessary for Fleasion's independent preview widgets;
    # each viewer sets its own format before its context is created. Keeping the
    # application startup GL-free avoids touching fragile Windows GPU paths.


def _check_linux_gui_dependencies() -> bool:
    """Report native Linux GUI dependencies that Python packaging cannot supply."""
    if not sys.platform.startswith('linux'):
        return True

    platform_linux = importlib.import_module('.utils.platform_linux', __package__)
    missing = platform_linux.missing_linux_gui_packages()
    if not missing:
        return True

    package_list = ' '.join(missing)
    install_command = verbatim(f'sudo pacman -S --needed {package_list}')
    log_buffer.log(
        'Linux GUI',
        'A required Arch Linux GUI package is missing.\n'
        f'  Package: {package_list}\n'
        f'  Impact: Fleasion cannot reliably publish its system tray icon.\n'
        f'  Install: {install_command}',
    )
    QMessageBox.critical(
        None,
        tr('app.value_system_package_required', value0=APP_NAME),
        tr(
            'app.fleasion_needs_a_system_package_before_its',
            value0=package_list,
            value1=install_command,
        ),
        QMessageBox.StandardButton.Ok,
    )
    return False


def _install_gui_sigint_handler(app: QApplication) -> QTimer:
    """Exit the Qt event loop cleanly when the console receives Ctrl+C."""

    def _handle_sigint(signum: int, _frame: object) -> None:
        app.exit(128 + signum)

    def _poll_python_signals() -> None:
        # Enter Python periodically while Qt is otherwise idle so CPython can
        # dispatch pending console signals instead of injecting KeyboardInterrupt
        # into an arbitrary Qt callback.
        return None

    signal.signal(signal.SIGINT, _handle_sigint)
    timer = QTimer(app)
    timer.setInterval(200)
    timer.timeout.connect(_poll_python_signals)
    timer.start()
    return timer


def main() -> None:
    """Main application entry point."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        '--no-dashboard',
        action='store_true',
        help='Suppress dashboard on launch (used by autostart task)',
    )
    parser.add_argument(
        '--kill-others',
        action='store_true',
        help='Kill other Fleasion instances on startup (used when relaunching elevated)',
    )
    parser.add_argument(
        '--preserve-env-proxy-player',
        action='store_true',
        help=argparse.SUPPRESS,
    )
    parser.add_argument('--restart-handoff-token', help=argparse.SUPPRESS)
    parser.add_argument('--restart-handoff-parent-pid', type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        '--proxy-debug', '-proxy-debug', action='store_true', help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--proxy-debug-mode',
        choices=['a', 'b', 'c', 'd', 'e', 'full'],
        help=argparse.SUPPRESS,
    )
    parser.add_argument('--fleasion-user-localappdata', help=argparse.SUPPRESS)
    parser.add_argument('--fleasion-requesting-user-sid', help=argparse.SUPPRESS)
    parser.add_argument('--repair-autostart', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--disable-autostart', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--repair-roblox-permissions', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--repair-firewall', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--cleanup-hosts', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--fleasion-gdk-debugger', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--microprofile', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument(
        '--install-linux-privileged-helper',
        action='store_true',
        help='Install the root-owned Linux proxy helper and Polkit policy, then exit',
    )
    parser.add_argument(
        '--linux-helper-promptless',
        action='store_true',
        help='Allow active sudo/wheel users to run the Linux proxy helper without future prompts',
    )
    args, _ = parser.parse_known_args()
    if args.fleasion_gdk_debugger:
        platform_windows = importlib.import_module('.utils.platform_windows', __package__)
        sys.exit(platform_windows.run_gdk_debugger_command_line())
    if args.cleanup_hosts:
        sys.exit(_cleanup_hosts_once())
    if args.repair_autostart:
        sys.exit(
            _repair_autostart_once(
                args.fleasion_requesting_user_sid,
                enabled=not args.disable_autostart,
            )
        )
    if args.repair_roblox_permissions:
        sys.exit(_repair_roblox_permissions_once(args.fleasion_requesting_user_sid))
    if args.repair_firewall:
        sys.exit(_repair_windows_firewall_once())
    pending_roblox_uri = _roblox_uri_from_argv()
    if args.install_linux_privileged_helper:
        if not sys.platform.startswith('linux'):
            print(
                'Linux privileged helper installation is only supported on Linux.',
                file=sys.stderr,
            )
            sys.exit(1)
        if TYPE_CHECKING:

            def install_privileged_helper(
                *,
                enable_promptless: bool = False,
                timeout: float = 120,
                ca_cert_path: Path | None = None,
            ) -> ErrorDetails: ...
        else:
            lazy_module = importlib.import_module('.utils.linux_proxy_helper', __package__)
            install_privileged_helper = lazy_module.install_privileged_helper

        result = install_privileged_helper(enable_promptless=args.linux_helper_promptless)
        if not result.get('ok'):
            print(
                f'Failed to install Linux privileged helper: {result.get("error") or result}',
                file=sys.stderr,
            )
            sys.exit(1)
        print(f'Installed Linux privileged helper: {result["helper"]}')
        print(f'Installed Polkit policy: {result["policy"]}')
        if result.get('promptless_rule'):
            print(f'Installed promptless Polkit rule: {result["promptless_rule"]}')
        sys.exit(0)

    suppress_dashboard = args.no_dashboard
    log_buffer.log('App', f'Version {__version__}')

    # Frozen GUI builds do not have a useful console for Qt's native warnings.
    # Capture warnings/errors in the normal rotating log before Qt/OpenGL setup.
    install_qt_message_logging()
    log_buffer.log(
        'App',
        'Runtime '
        f'{platform.system()} {platform.release()} ({platform.version()}) '
        f'{platform.machine()}; Python {platform.python_version()}; '
        f'frozen={bool(getattr(sys, "frozen", False))}',
    )
    log_buffer.log(
        'Qt',
        'Graphics env: '
        f'QT_OPENGL={os.environ.get("QT_OPENGL", "<unset>")}, '
        f'QT_QPA_PLATFORM={os.environ.get("QT_QPA_PLATFORM", "<unset>")}, '
        f'QT_ANGLE_PLATFORM={os.environ.get("QT_ANGLE_PLATFORM", "<unset>")}',
    )

    current_platform = platform.system()
    if current_platform not in {'Windows', 'Darwin', 'Linux'}:
        app = QApplication(sys.argv)
        QMessageBox.critical(
            None,
            tr('app.unsupported_operating_system'),
            tr('app.fleasion_supports_windows_macos_and_linux_this'),
            QMessageBox.StandardButton.Ok,
        )
        sys.exit(1)

    # The profiler is opt-in so normal releases do not collect diagnostics.
    microprofiler = start_microprofiler(enabled=args.microprofile)
    if microprofiler is not None:
        log_buffer.log('MicroProfiler', f'Writing diagnostics to {microprofiler.output_path}')

    _configure_opengl_for_legacy_viewers()

    # Create Qt application
    app = QApplication(sys.argv)
    _sigint_timer = _install_gui_sigint_handler(app)
    _single_instance_state.app = app
    roblox_url_event_filter = _RobloxUrlEventFilter(app)
    app.installEventFilter(roblox_url_event_filter)
    # Qt normally follows each desktop's dialog conventions (GNOME/KDE/Windows),
    # which changes the visual order of standard buttons. Fleasion uses the
    # Windows order everywhere so confirmations have a stable layout.
    app.setStyleSheet('QDialogButtonBox, QMessageBox { button-layout: 0; }')
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    if icon_path := get_icon_path():
        app.setWindowIcon(QIcon(str(icon_path)))
        if sys.platform == 'darwin':
            platform_macos = importlib.import_module('.utils.platform_macos', __package__)
            platform_macos.set_application_icon(icon_path)

    if not _check_linux_gui_dependencies():
        sys.exit(1)

    if sys.platform == 'darwin' and _is_admin():
        QMessageBox.critical(
            None,
            tr('app.do_not_run_with_sudo'),
            tr('app.run_fleasion_as_your_normal_macos_user_2'),
        )
        sys.exit(1)

    # Verified replacements use a three-phase protocol. The final child first
    # proves it survived imports/platform initialization (and, on Windows, UAC)
    # but waits here while the old process still owns both its working Env Proxy
    # and the single-instance slot. Only the parent can release that slot.
    restart_handoff_requested = bool(args.restart_handoff_token or args.restart_handoff_parent_pid)
    restart_handoff_invalid = (
        not args.restart_handoff_token
        or not args.restart_handoff_parent_pid
        or args.restart_handoff_parent_pid <= 0
        or args.kill_others
    )
    if restart_handoff_requested and (
        restart_handoff_invalid
        or (sys.platform == 'win32' and not _is_admin())
        or not _join_restart_handoff(
            args.restart_handoff_token,
            args.restart_handoff_parent_pid,
        )
    ):
        log_buffer.log('Restart', 'Verified replacement could not enter the handoff protocol')
        sys.exit(1)

    # Gracefully release the existing instance before claiming shared memory.
    # The preserve command reaches the old lifecycle controller before its
    # proxy stops, so an Env Player can be adopted by this replacement.
    if args.kill_others and _other_fleasion_pids():
        _request_other_fleasion_instances_exit(
            preserve_env_proxy_player=args.preserve_env_proxy_player
        )

    # Single instance check.
    # When we've just been relaunched via UAC elevation, the non-elevated
    # instance may not have fully exited yet, leaving stale shared memory.
    # If we're admin, forcibly attach-and-detach to clear it so the
    # elevated instance can take over cleanly.
    if _is_admin():
        # If launched with --kill-others, kill before clearing stale memory so
        # the shared memory slot is freed by the time we try to claim it.
        if args.kill_others:
            kill_other_fleasion_instances()

            time.sleep(0.3)
        stale = QSharedMemory(_SINGLE_INSTANCE_KEY)
        if stale.attach():
            stale.detach()

    shared_memory = QSharedMemory(_SINGLE_INSTANCE_KEY)
    shared_memory_created = shared_memory.create(1)
    if not shared_memory_created and _should_reclaim_stale_single_instance(shared_memory.error()):
        # A hard termination can leave Qt's native shared-memory segment behind
        # on Unix-like platforms. Attach/detach removes it when no real
        # Fleasion GUI process still owns it; Linux proxy helpers are ignored by
        # _other_fleasion_pids() because they are not app instances.
        stale = QSharedMemory(_SINGLE_INSTANCE_KEY)
        if stale.attach():
            stale.detach()
        shared_memory = QSharedMemory(_SINGLE_INSTANCE_KEY)
        shared_memory_created = shared_memory.create(1)

    if shared_memory_created:
        _single_instance_state.shared_memory = shared_memory

    another_instance_exists = (
        not shared_memory_created
        and shared_memory.error() == QSharedMemory.SharedMemoryError.AlreadyExists
    )
    if another_instance_exists:
        # Another instance is already running.
        if pending_roblox_uri and _request_running_instance_launch(pending_roblox_uri):
            sys.exit(0)
        if suppress_dashboard:
            sys.exit(0)
        # Non-admin processes cannot use taskkill on elevated processes — it
        # silently does nothing.  Branch on whether WE are admin rather than
        # trying to inspect the other process's token cross-privilege.
        msg_box = QMessageBox()
        msg_box.setWindowTitle(tr('app.already_running'))
        msg_box.setText(tr('app.another_instance_of_fleasion_is_already_running'))
        msg_box.setIcon(QMessageBox.Icon.Warning)

        # Set icon if available
        if icon_path := get_icon_path():
            msg_box.setWindowIcon(QIcon(str(icon_path)))

        msg_box.setInformativeText(tr('app.do_you_want_to_run_another_instance'))

        if _is_admin() or sys.platform == 'darwin' or sys.platform.startswith('linux'):
            # Already elevated — can kill any process directly.
            kill_others_button = msg_box.addButton(
                tr('app.kill_others'), QMessageBox.ButtonRole.AcceptRole
            )
            kill_requires_elevation = False
        else:
            # Not admin — taskkill on an elevated process silently fails.
            # A single "Elevate & Kill Others" relaunches as admin with
            # --kill-others so the elevated copy handles it automatically.
            kill_others_button = msg_box.addButton(
                tr('app.elevate_kill_others_recommended'),
                QMessageBox.ButtonRole.AcceptRole,
            )
            kill_requires_elevation = True

        msg_box.addButton(tr('app.run_anyway_bad'), QMessageBox.ButtonRole.AcceptRole)
        cancel_button = msg_box.addButton(tr('app.cancel'), QMessageBox.ButtonRole.RejectRole)
        msg_box.setDefaultButton(cancel_button)

        msg_box.exec()

        if msg_box.clickedButton() == cancel_button:
            sys.exit(0)

        if msg_box.clickedButton() == kill_others_button:
            if kill_requires_elevation:
                # Relaunch elevated with --kill-others.  The elevated copy will
                # kill the running instance before claiming the shared memory
                # slot — no second dialog shown.
                launched = _relaunch_as_admin(extra_args='--kill-others')
                if launched:
                    sys.exit(0)
                # UAC denied — the existing admin instance is still running.
                # There is no point continuing as a read-only copy alongside it,
                # so exit cleanly.
                sys.exit(0)
            else:
                kill_other_fleasion_instances()

        # If "Run Anyway" or "Kill Others" (admin path) is clicked, we proceed.
        # Note: shared_memory object will be garbage collected or go out of scope,
        # but since we didn't successfully create it, we don't hold the lock.

    # Initialize config manager before the elevation gate so the non-elevated
    # process can still build the prompt UI and show a fallback dialog.
    config_manager = ConfigManager()
    set_language(config_manager.language)
    if not suppress_dashboard and not config_manager.first_time_setup_complete:
        _prompt_first_time_language(config_manager)
    if sys.platform.startswith('linux'):
        platform_linux = importlib.import_module('.utils.platform_linux', __package__)
        platform_linux.set_linux_client_preference(config_manager.linux_client)
    env_proxy_migration_pending = _prepare_env_proxy_migration(config_manager)
    config_manager.settings['_runtime_proxy_debug'] = bool(args.proxy_debug)
    config_manager.settings['_runtime_proxy_debug_mode'] = args.proxy_debug_mode or 'full'

    # Gate non-admin launches before opening the usable GUI. Some Windows setups
    # show UAC as a taskbar item instead of foregrounding it, so startup must
    # block here until UAC is accepted, denied, or fails.
    admin_prompt_needed = (
        sys.platform == 'win32'
        and config_manager.proxy_features_enabled
        and config_manager.proxy_mode != 'env'
        and not _is_admin()
    )
    if sys.platform == 'darwin' and config_manager.proxy_mode != 'env':
        macos_proxy_helper = importlib.import_module('.utils.macos_proxy_helper', __package__)
        start_proxy = config_manager.proxy_features_enabled and macos_proxy_helper.helper_is_ready()
    else:
        start_proxy = config_manager.proxy_features_enabled and not admin_prompt_needed

    # Start tracking time wasted from the stored total
    time_tracker.init(config_manager.time_wasted_seconds)
    if TYPE_CHECKING:

        def save_time_tracker(config_manager: ConfigManager) -> None: ...
    else:
        save_time_tracker = time_tracker.save
    atexit.register(save_time_tracker, config_manager)

    proxy_error_invoker = _ProxyErrorInvoker()
    proxy_error_invoker.show_proxy_error.connect(proxy_error_invoker.handle_proxy_error)
    tray_ref: dict[str, SystemTray | None] = {'tray': None}

    def _refresh_config_surfaces() -> None:
        config_manager.refresh_config_names()
        tray = tray_ref.get('tray')
        dashboard = getattr(tray, 'dashboard_window', None)
        if dashboard is not None:

            def _refresh_dashboard() -> None:
                dashboard.refresh_configs_from_disk()

            try:
                _call_compatibility_boundary(_refresh_dashboard)
            except _CompatibilityBoundaryError as wrapped:
                log_buffer.log(
                    'Config',
                    f'Failed to refresh Dashboard after config import: {wrapped.cause}',
                )

    config_folder_watcher = ConfigFolderWatcher(
        config_manager,
        parent=app,
        parent_provider=_visible_parent_widget,
    )
    config_folder_watcher.configs_changed.connect(_refresh_config_surfaces)
    app.aboutToQuit.connect(config_folder_watcher.stop)

    def _revert_uncredentialed_manual_upstream() -> None:
        if not _manual_upstream_credentials_missing(config_manager):
            return
        previous_mode = config_manager.upstream_transport_mode
        config_manager.upstream_transport_mode = 'auto'
        log_buffer.log(
            'Proxy',
            f'Reset upstream transport from {previous_mode} to auto after '
            '10 seconds without credentials',
        )
        tray = tray_ref.get('tray')
        dashboard = getattr(tray, 'dashboard_window', None)
        settings_tab = getattr(dashboard, '_settings_tab', None)
        if settings_tab is not None:
            settings_tab.refresh_from_config()
        if proxy_master.is_running:

            def _restart_proxy() -> None:
                proxy_master.stop()
                proxy_master.start()

            run_in_thread(_restart_proxy)()

    QTimer.singleShot(10_000, _revert_uncredentialed_manual_upstream)

    def _handle_proxy_features_start_failure(reason: str) -> None:
        _disable_proxy_features_after_start_failure(config_manager, tray_ref.get('tray'), reason)

    proxy_error_invoker.disable_proxy_features.connect(_handle_proxy_features_start_failure)

    def _on_proxy_start_error(code: str, details: dict[str, object]) -> None:
        if code == 'upstream_connect_failed':
            if sys.platform == 'win32':
                proxy_error_invoker.show_proxy_error.emit(code, dict(details))
            return
        if code == 'linux_hosts_read_only':
            proxy_error_invoker.show_proxy_error.emit(code, dict(details))
            return
        if code == 'linux_helper_unavailable':
            proxy_error_invoker.disable_proxy_features.emit(
                tr('app.linux_proxy_helper.start_denied')
            )
            return
        if code == 'tls_self_test_failed':
            proxy_error_invoker.show_proxy_error.emit(code, dict(details))
            return
        if code not in {
            'port_bind_failed',
            'hosts_write_exhausted',
            'hosts_entries_would_exceed_limit',
            'hosts_file_too_large',
            'hosts_file_repair_failed',
            'macos_ca_patch_failed',
            'roblox_ca_patch_failed',
            'macos_ca_trust_failed',
            'macos_relay_failed',
        }:
            return
        proxy_error_invoker.show_proxy_error.emit(code, dict(details))

    # Initialize proxy master
    proxy_master = ProxyMaster(config_manager, on_proxy_start_error=_on_proxy_start_error)
    proxy_error_invoker.retry_proxy.connect(proxy_master.start)

    # Initialize modification manager (pass cache_scraper for asset-id resolution)
    mod_manager = ModificationManager(
        cache_scraper=getattr(proxy_master, 'cache_scraper', None),
        read_only_lock_enabled=config_manager.lock_roblox_files_read_only,
    )
    if not config_manager.lock_roblox_files_read_only:
        if not config_manager.read_only_lock_migration_v1_complete:
            # One-time cleanup for persistent guards left by older builds,
            # including the old cacert.pem lock.
            mod_manager.clear_managed_file_read_only(
                (roblox_dir / 'ssl' / 'cacert.pem' for roblox_dir in mod_manager.roblox_dirs),
                clear_untracked=True,
            )
            config_manager.read_only_lock_migration_v1_complete = True
        else:
            # Exact original modes persisted by the new opt-in guard survive a
            # crash and can be restored without changing unrelated files.
            mod_manager.clear_managed_file_read_only(clear_untracked=False)
    macos_bootstrapper_bridge = None
    if sys.platform == 'darwin':
        lazy_module = importlib.import_module(
            '.modifications.macos_bootstrapper_bridge', __package__
        )
        mac_bootstrapper_bridge_cls = lazy_module.MacBootstrapperBridge

        macos_bootstrapper_bridge = mac_bootstrapper_bridge_cls(
            mod_manager,
            app,
            custom_fflag_seed=lambda: proxy_master.prime_custom_fflag_cache(allow_running=True),
            custom_fflag_prepare=proxy_master.prepare_custom_fflags_for_player_launch,
        )
        app.aboutToQuit.connect(macos_bootstrapper_bridge.stop)

    def _refresh_managed_read_only_guard() -> None:
        try:
            if config_manager.lock_roblox_files_read_only:
                _call_compatibility_boundary(mod_manager.protect_managed_files)
        except _CompatibilityBoundaryError as wrapped:
            log_buffer.log(
                'Modifications',
                f'Read-only guard refresh failed: {wrapped.cause}',
            )

    # Re-apply saved modifications on launch so the GUI state and Roblox files stay in sync.
    run_in_thread(mod_manager.reapply_all)()

    # Shutdown guards
    # Graceful Windows shutdown / log-off: Qt fires commitDataRequest before
    # the session ends, giving us a chance to clean up the hosts file.
    def _on_commit_data(_session: QSessionManager) -> None:
        with contextlib.suppress(NameError, AttributeError):
            env_lifecycle.cancel()
        mod_manager.clear_managed_file_read_only()
        proxy_master.stop()
        mod_manager.restore_all()

    app.commitDataRequest.connect(_on_commit_data)

    # Normal Python exit (sys.exit, end of main): last-resort fallback so
    # the hosts file is cleaned up even if the tray Exit path was bypassed.
    atexit.register(proxy_master.stop)
    atexit.register(mod_manager.clear_managed_file_read_only)
    atexit.register(mod_manager.restore_all)

    # Start PreJsons download in background
    run_in_thread(download_prejsons)()

    # Check for updates in the background
    start_update_check()

    # Sync launch integrations on every launch (updates if launch method changed).
    # All platforms use per-user launch entries and reconcile from the normal
    # non-elevated GUI process.
    autostart_launch_sync_failed = False
    desktop_integration_launch_sync_failed = False
    if config_manager.first_time_setup_complete and config_manager.desktop_integration:
        try:
            lazy_module = importlib.import_module('.utils.desktop_integration', __package__)
            sync_desktop_integration = lazy_module.sync_desktop_integration

            desktop_integration_launch_sync_failed = not sync_desktop_integration(enabled=True)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            desktop_integration_launch_sync_failed = True
            log_buffer.log('DesktopIntegration', f'Launch desktop integration sync failed: {exc}')

    if config_manager.first_time_setup_complete and _should_sync_autostart_on_launch(
        config_manager.run_on_boot
    ):
        try:
            autostart = importlib.import_module('.utils.autostart', __package__)
            autostart_launch_sync_failed = not autostart.sync_autostart(
                enabled=True,
                config_dir=CONFIG_DIR,
                proxy_mode=config_manager.proxy_mode,
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            autostart_launch_sync_failed = True
            log_buffer.log('Autostart', f'Launch autostart sync failed: {exc}')

    # Start proxy only if enabled and we have admin rights
    if (
        start_proxy
        and config_manager.proxy_features_enabled
        and config_manager.proxy_mode == 'env'
        and not _show_env_proxy_stale_hosts_dialog()
    ):
        start_proxy = False
        log_buffer.log(
            'Proxy',
            'Proxy startup cancelled because the oversized hosts file was not repaired',
        )

    if start_proxy:
        proxy_master.start()
        _refresh_managed_read_only_guard()
    elif not config_manager.proxy_features_enabled:
        log_buffer.log('Proxy', 'Proxy features disabled in settings: proxy not started')
    elif sys.platform == 'darwin':
        log_buffer.log('Proxy', 'Waiting for the macOS proxy helper before starting interception')
    else:
        log_buffer.log('Proxy', 'Read-only mode: proxy not started (no admin rights)')

    if (
        sys.platform == 'win32'
        and start_proxy
        and config_manager.proxy_mode == 'env'
        and config_manager.proxy_features_enabled
    ):
        _arm_windows_gdk_env_proxy_when_ready(proxy_master)

    # Env Proxy owns the selected Player/client lifecycle independently of Studio.
    lazy_module = importlib.import_module('.proxy.env_lifecycle', __package__)
    env_proxy_lifecycle_controller_cls = lazy_module.EnvProxyLifecycleController

    if sys.platform == 'win32':
        lazy_module = importlib.import_module('.utils.platform_windows', __package__)
        close_roblox_for_env_lifecycle = lazy_module.close_roblox_for_env_lifecycle
        relaunch_roblox_with_proxy_env = lazy_module.relaunch_roblox_with_proxy_env

        def _prepare_env_proxy_launch(path: Path) -> bool:
            result = proxy_master.ensure_env_proxy_roblox_ca(path, settle=False)
            if not result.get('success'):
                return False
            proxy_master.rearm_custom_fflag_delivery_for_player_launch()
            return True

        def _relaunch_env_player(
            proxy_url: str,
            _target: str | None,
            force: bool,
            cancel_event: threading.Event,
            _source_exe_path: Path | None,
            _player_already_stopped: bool,
        ) -> bool:
            return relaunch_roblox_with_proxy_env(
                proxy_url,
                force=force,
                cancel_event=cancel_event,
                prepare_launch=_prepare_env_proxy_launch,
            )

        terminate_env_player = close_roblox_for_env_lifecycle

    elif sys.platform == 'darwin':
        lazy_module = importlib.import_module('.utils.platform_macos', __package__)
        relaunch_roblox_with_proxy_env = lazy_module.relaunch_roblox_with_proxy_env

        def _prepare_env_proxy_launch(_path: Path) -> bool:
            proxy_master.rearm_custom_fflag_delivery_for_player_launch()
            return True

        def _relaunch_env_player(  # ruff: ignore[too-many-positional-arguments]
            proxy_url: str,
            target: str | None,
            force: bool,
            cancel_event: threading.Event,
            source_exe_path: Path | None,
            player_already_stopped: bool,
        ) -> bool:
            return relaunch_roblox_with_proxy_env(
                proxy_url,
                target,
                force=force,
                cancel_event=cancel_event,
                source_exe_path=source_exe_path,
                player_already_stopped=player_already_stopped,
                prepare_launch=_prepare_env_proxy_launch,
            )

        terminate_env_player = terminate_roblox

    else:

        def _relaunch_env_player(
            _proxy_url: str,
            _target: str | None,
            _force: bool,
            _cancel_event: threading.Event,
            _source_exe_path: Path | None,
            _player_already_stopped: bool,
        ) -> bool:
            log_buffer.log(
                'Launcher',
                'Linux client Env Proxy is supplied by the client launcher; '
                'synthetic relaunch skipped',
            )
            return False

        terminate_env_player = terminate_roblox

    env_lifecycle = env_proxy_lifecycle_controller_cls(
        config_manager=config_manager,
        proxy_master=proxy_master,
        resolve_player_exe=get_roblox_player_exe_path,
        relaunch_player=_relaunch_env_player,
        is_player_running=is_roblox_running,
        get_player_identity=get_roblox_process_identity,
        terminate_player=terminate_env_player,
        wait_for_player_exit=wait_for_roblox_exit,
        adopted_player=args.preserve_env_proxy_player,
        max_repairs=2,
    )
    atexit.register(env_lifecycle.cancel)

    # Setup Roblox exit monitor for auto cache deletion (before tray to pass to it)
    roblox_monitor = RobloxExitMonitor(config_manager, proxy_master, mod_manager, env_lifecycle)
    app.aboutToQuit.connect(roblox_monitor.stop)

    # Create system tray
    tray = SystemTray(app, config_manager, proxy_master, mod_manager, roblox_monitor)
    tray_ref['tray'] = tray
    _single_instance_state.tray = tray
    app.aboutToQuit.connect(tray.cleanup_tray_icon)
    single_instance_control_server = _start_single_instance_control_server(app, tray)
    _single_instance_state.control_server = single_instance_control_server

    if env_proxy_migration_pending:
        _show_env_proxy_migration(config_manager, roblox_monitor)

    def _handle_roblox_uri_event(target: str) -> None:
        run_in_thread(_launch_roblox_uri_for_instance)(tray, target)

    roblox_url_event_filter.roblox_uri_received.connect(_handle_roblox_uri_event)
    roblox_url_event_filter.start()
    if pending_roblox_uri:

        def _launch_pending_roblox_uri(target: str = pending_roblox_uri) -> None:
            run_in_thread(_launch_roblox_uri_for_instance)(tray, target)

        QTimer.singleShot(0, _launch_pending_roblox_uri)
    log_buffer.log('App', f'Persistent log file: {LOG_FILE}')
    if autostart_launch_sync_failed:
        QTimer.singleShot(
            0,
            lambda: _show_run_on_boot_failure(_visible_parent_widget(), config_manager.proxy_mode),
        )
    if desktop_integration_launch_sync_failed:
        QTimer.singleShot(0, lambda: _show_desktop_integration_failure(_visible_parent_widget()))

    def _check_roblox_permission_failures() -> None:
        denied_dirs = mod_manager.take_permission_denied_dirs()
        if denied_dirs:
            _show_roblox_permission_failure(_visible_parent_widget(), denied_dirs, mod_manager)
        QTimer.singleShot(500, _check_roblox_permission_failures)

    if sys.platform == 'win32':
        QTimer.singleShot(500, _check_roblox_permission_failures)
    admin_prompt_shown = False

    def _request_admin_once() -> None:
        nonlocal admin_prompt_shown
        if admin_prompt_shown or _is_admin():
            return
        admin_prompt_shown = True

        gate = QDialog(None)
        gate.setModal(True)
        gate.setWindowTitle(tr('app.administrator_permission_required'))
        gate.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        gate_layout = QVBoxLayout(gate)
        if sys.platform == 'darwin':
            gate_text = tr('app.admin_gate.macos')
        else:
            gate_text = tr('app.admin_gate.windows')
        gate_label = QLabel(gate_text)
        gate_label.setWordWrap(True)
        gate_layout.addWidget(gate_label)
        if icon_path := get_icon_path():
            gate.setWindowIcon(QIcon(str(icon_path)))
        gate.show()
        gate.raise_()
        gate.activateWindow()
        QApplication.processEvents()

        log_buffer.log('UAC', 'Requesting administrator relaunch from GUI startup path')
        if _relaunch_as_admin(parent_hwnd=_window_handle(gate)):
            gate.close()
            sys.exit(0)

        gate.close()
        _show_admin_required_dialog()

    if admin_prompt_needed:
        _request_admin_once()

    def _install_macos_helper_and_start_proxy() -> None:
        if (
            sys.platform != 'darwin'
            or not config_manager.proxy_features_enabled
            or proxy_master.is_running
        ):
            return

        macos_proxy_helper = importlib.import_module('.utils.macos_proxy_helper', __package__)
        helper_is_ready = macos_proxy_helper.helper_is_ready

        if helper_is_ready():
            proxy_master.start()
            _refresh_managed_read_only_guard()
            return
        if suppress_dashboard:
            log_buffer.log(
                'ProxyHelper',
                'Autostart launch skipped helper installation prompt; open Fleasion normally to install it',
            )
            return

        prompt = QMessageBox(_visible_parent_widget())
        prompt.setWindowTitle(tr('app.install_proxy_helper'))
        prompt.setIcon(QMessageBox.Icon.Information)
        prompt.setText(tr('app.install_the_fleasion_macos_proxy_helper'))
        prompt.setInformativeText(tr('app.macos_requires_a_small_root_service_to'))
        install_button = prompt.addButton(
            tr('app.install_helper'), QMessageBox.ButtonRole.AcceptRole
        )
        cancel_button = prompt.addButton(tr('app.not_now'), QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(install_button)
        prompt.exec()
        if prompt.clickedButton() == cancel_button:
            log_buffer.log('ProxyHelper', 'macOS proxy helper installation postponed')
            return

        ok, detail = macos_proxy_helper.install_helper()
        if ok:
            proxy_master.start()
            _refresh_managed_read_only_guard()
            return

        log_buffer.log('ProxyHelper', f'macOS proxy helper installation failed: {detail}')
        QMessageBox.warning(
            _visible_parent_widget(),
            tr('app.fleasion_proxy_helper_installation_failed'),
            tr('app.fleasion_could_not_install_or_start_the', value0=detail),
        )

    if sys.platform == 'darwin' and config_manager.proxy_features_enabled and not start_proxy:
        _install_macos_helper_and_start_proxy()

    if restart_handoff_requested:

        def restart_cancelled():
            return _restart_abort_requested(
                args.restart_handoff_token,
                args.restart_handoff_parent_pid,
            )

        replacement_ready = single_instance_control_server is not None
        if not replacement_ready:
            log_buffer.log(
                'Restart',
                'Replacement could not claim the single-instance control endpoint',
            )
        if replacement_ready and config_manager.proxy_features_enabled:
            if config_manager.proxy_mode == 'env':
                replacement_ready = proxy_master.wait_for_env_proxy_ready(
                    timeout=30.0,
                    cancelled=restart_cancelled,
                )
            else:
                replacement_ready = proxy_master.wait_for_hosts_proxy_ready(
                    timeout=30.0,
                    cancelled=restart_cancelled,
                )
        if replacement_ready and restart_cancelled():
            replacement_ready = False
        if not replacement_ready:
            log_buffer.log(
                'Restart',
                'Replacement did not establish the configured proxy before final handoff',
            )
            try:
                _call_compatibility_boundary(proxy_master.stop)
            except _CompatibilityBoundaryError as wrapped:
                log_buffer.log(
                    'Restart',
                    f'Replacement proxy cleanup failed: {wrapped.cause}',
                )
            if single_instance_control_server is not None:
                single_instance_control_server.close()
                QLocalServer.removeServer(_SINGLE_INSTANCE_CONTROL_SERVER)
            if shared_memory.isAttached():
                shared_memory.detach()
            sys.exit(1)
        if not _publish_restart_handoff(args.restart_handoff_token):
            sys.exit(1)

    # Warn if no Roblox installations can be found (same scan used for cert injection)
    lazy_module = importlib.import_module('.proxy.master', __package__)
    find_roblox_dirs = lazy_module.find_roblox_dirs

    if not find_roblox_dirs():
        top = QApplication.topLevelWidgets()
        parent = next((w for w in top if w.isVisible()), None)
        on_top = any(
            w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            for w in top
        )
        no_roblox_msg = QMessageBox(parent)
        if on_top:
            no_roblox_msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        no_roblox_msg.setWindowTitle(tr('app.roblox_not_found'))
        no_roblox_msg.setIcon(QMessageBox.Icon.Warning)
        no_roblox_msg.setText(tr('app.roblox_does_not_appear_to_be_installed'))
        no_roblox_msg.setInformativeText(tr('app.fleasion_could_not_find_any_roblox_installations'))
        no_roblox_msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        if icon_path := get_icon_path():
            no_roblox_msg.setWindowIcon(QIcon(str(icon_path)))
        no_roblox_msg.exec()

    # Setup periodic status update
    status_timer = QTimer()
    status_timer.timeout.connect(tray.update_status)
    status_timer.start(1000)  # Update every second

    # Setup Roblox check timer
    roblox_check_timer = QTimer()
    roblox_check_timer.timeout.connect(roblox_monitor.check_roblox_status)
    roblox_check_timer.start(500)  # Check every 0.5 seconds

    managed_read_only_timer = QTimer()
    managed_read_only_timer.timeout.connect(_refresh_managed_read_only_guard)
    managed_read_only_timer.start(1000)
    QTimer.singleShot(250, _refresh_managed_read_only_guard)
    QTimer.singleShot(1500, _refresh_managed_read_only_guard)

    # Show first-time setup guide if this is the first run.
    if not suppress_dashboard and not config_manager.first_time_setup_complete:
        top = QApplication.topLevelWidgets()
        parent = next((w for w in top if w.isVisible()), None)
        on_top = any(
            w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            for w in top
        )
        welcome_box = _FirstTimeSetupDialog(parent)
        if on_top:
            welcome_box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        welcome_box.setWindowTitle(tr('onboarding.welcome.title'))
        welcome_box.set_text(tr('onboarding.welcome.body'))
        ok_button = welcome_box.ok_button
        wait_seconds = 15
        remaining_seconds = wait_seconds
        ok_button.setEnabled(False)
        ok_button.setText(tr('onboarding.welcome.ok_countdown', seconds=remaining_seconds))

        countdown_timer = QTimer(welcome_box)
        countdown_timer.setInterval(1000)

        def _update_welcome_countdown() -> None:
            nonlocal remaining_seconds
            remaining_seconds -= 1
            if remaining_seconds <= 0:
                countdown_timer.stop()
                welcome_box.allow_accept()
                ok_button.setText(tr('onboarding.welcome.ok'))
                ok_button.setEnabled(True)
            else:
                ok_button.setText(tr('onboarding.welcome.ok_countdown', seconds=remaining_seconds))

        countdown_timer.timeout.connect(_update_welcome_countdown)
        countdown_timer.start()
        if icon_path := get_icon_path():
            welcome_box.setWindowIcon(QIcon(str(icon_path)))
        welcome_box.exec()
        _prompt_first_time_startup_options(config_manager, tray)
        config_manager.env_proxy_migration_v1_complete = True
        config_manager.first_time_setup_complete = True
        tray.show_replacer_config()
    elif not suppress_dashboard and config_manager.open_dashboard_on_launch:
        # Open dashboard on launch if enabled (suppressed when started by autostart task)
        tray.show_replacer_config()

    auth_prompt_shown = False
    auth_check_invoker = _AuthCheckInvoker()

    def _retry_macos_auth(details: dict[str, object]) -> tuple[dict[str, object], bool]:
        roblox_auth = importlib.import_module('.utils.roblox_auth', __package__)
        if not config_manager.macos_auth_source:
            return details, False

        log_buffer.log(
            'Auth',
            f'Configured Roblox login source {config_manager.macos_auth_source} did not produce a valid token; reopening browser picker',
        )
        config_manager.macos_auth_source = ''
        roblox_auth.notify_auth_source_changed()
        choice_result = _choose_macos_auth_source_on_launch(config_manager, tray, force=True)
        if choice_result in {'selected', 'already-configured'}:
            if roblox_auth.get_roblosecurity(include_keychain_browsers=True):
                return details, True
            return roblox_auth.get_auth_failure_details(), False
        if choice_result == 'skipped':
            details = dict(details)
            details['user_skipped_token'] = True
        return details, False

    def _handle_auth_check_complete(found: bool, details: dict[str, object]) -> None:
        nonlocal auth_prompt_shown
        if found or auth_prompt_shown:
            return
        auth_prompt_shown = True
        if sys.platform == 'darwin':
            try:
                details, resolved = _call_compatibility_boundary(lambda: _retry_macos_auth(details))
            except _CompatibilityBoundaryError as wrapped:
                exc = wrapped.cause
                log_buffer.log(
                    'Auth',
                    f'Unexpected error while retrying macOS auth picker: {type(exc).__name__}: {exc}',
                )
            else:
                if resolved:
                    return
        _show_auth_cookie_unavailable_dialog(details, tray)

    auth_check_invoker.completed.connect(_handle_auth_check_complete)
    initial_auth_choice = _choose_macos_auth_source_on_launch(config_manager, tray)
    if initial_auth_choice == 'skipped':
        auth_prompt_shown = True

        def _load_skip_details() -> ErrorDetails:
            roblox_auth = importlib.import_module('.utils.roblox_auth', __package__)
            roblox_auth.get_roblosecurity(include_keychain_browsers=False)
            return roblox_auth.get_auth_failure_details()

        try:
            skip_details = _call_compatibility_boundary(_load_skip_details)
        except _CompatibilityBoundaryError as wrapped:
            exc = wrapped.cause
            log_buffer.log(
                'Auth',
                f'Unexpected error while preparing token-skip warning: {type(exc).__name__}: {exc}',
            )
            skip_details = {}
        skip_details['user_skipped_token'] = True
        _show_auth_cookie_unavailable_dialog(skip_details, tray)

    def _check_auth_cookie_once() -> None:
        def _load_auth_cookie() -> tuple[object, ErrorDetails]:
            roblox_auth = importlib.import_module('.utils.roblox_auth', __package__)
            if sys.platform == 'darwin':
                log_buffer.log('Auth', 'Running startup Roblox login discovery')
            cookie = roblox_auth.get_roblosecurity(
                include_keychain_browsers=sys.platform == 'darwin'
                or sys.platform.startswith('linux')
            )
            details = roblox_auth.get_auth_failure_details()
            return cookie, details

        try:
            cookie, details = _call_compatibility_boundary(_load_auth_cookie)
        except _CompatibilityBoundaryError as wrapped:
            exc = wrapped.cause
            log_buffer.log(
                'Auth',
                f'Unexpected error during startup auth check: {type(exc).__name__}: {exc}',
            )
            return
        auth_check_invoker.completed.emit(bool(cookie), details)

    QTimer.singleShot(1500, run_in_thread(_check_auth_cookie_once))

    # Run application
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
