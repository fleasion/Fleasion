"""First-run onboarding, proxy migration, and desktop startup settings."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fleasion.app.dialogs.common import FirstTimeSetupDialog, visible_parent_widget, window_handle
from fleasion.app.elevation import is_admin, relaunch_as_admin
from fleasion.localization import available_languages, set_language, tr
from fleasion.utils import (
    CONFIG_DIR,
    get_icon_path,
    get_roblox_player_exe_path,
    log_buffer,
    run_in_thread,
)
from fleasion.utils.platform_linux import (
    selected_linux_client_app_id,
    selected_linux_client_display_name,
)

if TYPE_CHECKING:
    from fleasion.app.roblox_monitor import RobloxExitMonitor
    from fleasion.app.tray import SystemTray
    from fleasion.config import ConfigManager


def prepare_env_proxy_migration(config_manager: ConfigManager) -> bool:
    """Select Env Proxy before privilege gates and report a legacy migration."""
    if config_manager.env_proxy_migration_v1_complete:
        return False

    # Assign even when the merged in-memory default is already Env so the new
    # mode is durable on disk before any acknowledgement dialog can appear
    config_manager.proxy_mode = 'env'

    # First-time users learn about Env Proxy in the setup guide. Existing
    # users receive the dedicated migration acknowledgement later in startup
    return bool(config_manager.first_time_setup_complete)


def show_env_proxy_migration(
    config_manager: ConfigManager, roblox_monitor: RobloxExitMonitor
) -> None:
    """Acknowledge the forced legacy migration and apply it to Player."""
    player_running = bool(roblox_monitor.is_player_running())
    if player_running:
        # Do not let the process monitor interpret a Player that predates this
        # startup as a fresh launch and relaunch it without the user's choice
        roblox_monitor.mark_player_running_at_startup()

    msg = QMessageBox(visible_parent_widget())
    msg.setWindowTitle(tr('app.new_default_roblox_env_proxy'))
    msg.setIcon(QMessageBox.Icon.Information)
    msg.setText(tr('app.fleasion_has_switched_your_saved_proxy_mode'))
    details = tr('app.env_proxy_migration.details')
    restart_button = None
    if player_running and config_manager.proxy_features_enabled:
        if sys.platform.startswith('linux'):
            details += tr(
                'app.env_proxy_migration.linux_future',
                client_name=selected_linux_client_display_name(),
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
        run_in_thread(lifecycle.handle_adopted_player_launch)(Path(selected_linux_client_app_id()))
    else:
        run_in_thread(lifecycle.handle_player_launch)(get_roblox_player_exe_path())


def should_sync_autostart_on_launch(run_on_boot: bool) -> bool:
    if not run_on_boot:
        return False
    if sys.platform == 'darwin':
        return not is_admin()
    if sys.platform.startswith('linux'):
        return True
    if sys.platform == 'win32':
        return True
    return False


def refresh_run_on_boot_ui(tray: SystemTray | None, enabled: bool) -> None:
    if tray is not None and hasattr(tray, 'run_on_boot_action'):
        tray.run_on_boot_action.setChecked(enabled)
    if tray is not None:
        tray.refresh_settings_tab()


def refresh_desktop_integration_ui(tray: SystemTray | None, enabled: bool) -> None:
    if tray is not None and hasattr(tray, 'desktop_integration_action'):
        tray.desktop_integration_action.setChecked(enabled)
    if tray is not None:
        tray.refresh_settings_tab()


def show_run_on_boot_failure(
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
        autostart = importlib.import_module('fleasion.utils.autostart')
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
        if relaunch_as_admin(
            extra_args=repair_args,
            parent_hwnd=window_handle(parent),
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


def show_desktop_integration_failure(parent: QWidget | None) -> None:
    msg = QMessageBox(parent)
    msg.setWindowTitle(tr('app.desktop_integration_failed'))
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(tr('app.failed_to_create_desktop_start_menu_integration'))
    if icon_path := get_icon_path():
        msg.setWindowIcon(QIcon(str(icon_path)))
    msg.exec()


def prompt_first_time_language(config_manager: ConfigManager) -> None:
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


def prompt_first_time_startup_options(
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
        desktop_integration = importlib.import_module('fleasion.utils.desktop_integration')
        desktop_ok = desktop_integration.sync_desktop_integration(
            enabled=enable_desktop_integration
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        desktop_ok = False
        log_buffer.log('DesktopIntegration', f'First-time desktop integration prompt failed: {exc}')

    try:
        autostart = importlib.import_module('fleasion.utils.autostart')
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
    refresh_run_on_boot_ui(tray, config_manager.run_on_boot)
    refresh_desktop_integration_ui(tray, config_manager.desktop_integration)

    if enable_run_on_boot and not boot_ok:
        show_run_on_boot_failure(dialog.parentWidget(), config_manager.proxy_mode)
    if enable_desktop_integration and not desktop_ok:
        show_desktop_integration_failure(dialog.parentWidget())


def complete_first_time_setup(config_manager: ConfigManager, tray: SystemTray) -> None:
    """Show the required welcome guide and persist the chosen startup settings."""
    top = QApplication.topLevelWidgets()
    parent = next((w for w in top if w.isVisible()), None)
    on_top = any(
        w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in top
    )
    welcome_box = FirstTimeSetupDialog(parent)
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
    prompt_first_time_startup_options(config_manager, tray)
    config_manager.env_proxy_migration_v1_complete = True
    config_manager.first_time_setup_complete = True
