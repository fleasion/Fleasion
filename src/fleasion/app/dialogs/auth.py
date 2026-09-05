"""Browser authentication selection and startup token checks."""

from __future__ import annotations

import html
import importlib
import sys
import webbrowser
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from fleasion.app.compatibility import CompatibilityBoundaryError, call_compatibility_boundary
from fleasion.app.dialogs.common import (
    ForcedAcknowledgeMessageBox,
    MacOSAuthSourceDialog,
    quit_after_modal_closes,
)
from fleasion.app.error_details import (
    ErrorDetails,
    is_object_list as _is_object_list,
)
from fleasion.localization import tr
from fleasion.utils import (
    APP_DISCORD,
    get_icon_path,
    launch_as_standard_user,
    log_buffer,
    run_in_thread,
)

if TYPE_CHECKING:
    from fleasion.app.tray import SystemTray
    from fleasion.config import ConfigManager


AUTH_SKIP_SELECTION_KEY = 'continue_without_token'


def choose_macos_auth_source_on_launch(
    config_manager: ConfigManager, tray: SystemTray | None = None, *, force: bool = False
) -> str:
    """Ask macOS users which browser should be queried for Roblox auth."""
    if sys.platform != 'darwin':
        return 'unavailable'
    if config_manager.macos_auth_source and not force:

        def _configured_auth_source_is_valid() -> bool:
            roblox_auth = importlib.import_module('fleasion.utils.roblox_auth')
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
            if call_compatibility_boundary(_configured_auth_source_is_valid):
                return 'already-configured'
        except CompatibilityBoundaryError as wrapped:
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

    dialog = MacOSAuthSourceDialog(parent)
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
        quit_after_modal_closes(dialog, tray, selected)

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
            quit_after_modal_closes(dialog, tray, selected)

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
            roblox_auth = importlib.import_module('fleasion.utils.roblox_auth')
            cookie, source = roblox_auth.discover_browser_roblosecurity(
                include_keychain=True,
                explicit_import=True,
                browser=browser,
            )
            return cookie, source

        try:
            cookie, source = call_compatibility_boundary(_discover_browser_auth)
        except CompatibilityBoundaryError as wrapped:
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
        lazy_module = importlib.import_module('fleasion.gui.rando_stuff_tab')
        add_account_dialog_cls = lazy_module.AddAccountDialog
        roblox_auth = importlib.import_module('fleasion.utils.roblox_auth')

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
        selected[AUTH_SKIP_SELECTION_KEY] = '1'
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
        roblox_auth = importlib.import_module('fleasion.utils.roblox_auth')
        roblox_auth.notify_auth_source_changed()
        if tray is not None:
            tray.refresh_settings_tab()
        return 'selected'
    if selected.get('continue_without_token'):
        return 'skipped'
    if selected.get('exit'):
        return 'exiting'
    return 'dismissed'


def windows_auth_profile_matches_username(details: ErrorDetails) -> bool:
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


def show_auth_cookie_unavailable_dialog(
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
            if windows_auth_profile_matches_username(details)
            else 'app.auth_warning.windows_guidance'
        )

    msg = ForcedAcknowledgeMessageBox(parent)
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
        quit_after_modal_closes(msg, tray)

    exit_button.clicked.connect(_exit_from_warning)

    msg.exec()
    if msg.clickedButton() == exit_button:
        return
    if open_login_button is not None and msg.clickedButton() == open_login_button:
        if sys.platform.startswith('linux'):
            launch_as_standard_user('https://www.roblox.com/login')
        else:
            webbrowser.open('https://www.roblox.com/login')


class AuthCheckInvoker(QObject):
    """Main-thread bridge for the potentially prompting browser auth check."""

    completed = Signal(bool, dict)


def schedule_startup_auth_check(
    config_manager: ConfigManager, tray: SystemTray, parent: QObject
) -> None:
    """Schedule browser discovery and deliver its result on the GUI thread."""
    auth_prompt_shown = False
    auth_check_invoker = AuthCheckInvoker(parent)

    def _retry_macos_auth(details: dict[str, object]) -> tuple[dict[str, object], bool]:
        roblox_auth = importlib.import_module('fleasion.utils.roblox_auth')
        if not config_manager.macos_auth_source:
            return details, False

        log_buffer.log(
            'Auth',
            f'Configured Roblox login source {config_manager.macos_auth_source} did not produce a valid token; reopening browser picker',
        )
        config_manager.macos_auth_source = ''
        roblox_auth.notify_auth_source_changed()
        choice_result = choose_macos_auth_source_on_launch(config_manager, tray, force=True)
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
                details, resolved = call_compatibility_boundary(lambda: _retry_macos_auth(details))
            except CompatibilityBoundaryError as wrapped:
                exc = wrapped.cause
                log_buffer.log(
                    'Auth',
                    f'Unexpected error while retrying macOS auth picker: {type(exc).__name__}: {exc}',
                )
            else:
                if resolved:
                    return
        show_auth_cookie_unavailable_dialog(details, tray)

    auth_check_invoker.completed.connect(_handle_auth_check_complete)
    initial_auth_choice = choose_macos_auth_source_on_launch(config_manager, tray)
    if initial_auth_choice == 'skipped':
        auth_prompt_shown = True

        def _load_skip_details() -> ErrorDetails:
            roblox_auth = importlib.import_module('fleasion.utils.roblox_auth')
            roblox_auth.get_roblosecurity(include_keychain_browsers=False)
            return roblox_auth.get_auth_failure_details()

        try:
            skip_details = call_compatibility_boundary(_load_skip_details)
        except CompatibilityBoundaryError as wrapped:
            exc = wrapped.cause
            log_buffer.log(
                'Auth',
                f'Unexpected error while preparing token-skip warning: {type(exc).__name__}: {exc}',
            )
            skip_details = {}
        skip_details['user_skipped_token'] = True
        show_auth_cookie_unavailable_dialog(skip_details, tray)

    def _check_auth_cookie_once() -> None:
        def _load_auth_cookie() -> tuple[object, ErrorDetails]:
            roblox_auth = importlib.import_module('fleasion.utils.roblox_auth')
            if sys.platform == 'darwin':
                log_buffer.log('Auth', 'Running startup Roblox login discovery')
            cookie = roblox_auth.get_roblosecurity(
                include_keychain_browsers=sys.platform == 'darwin'
                or sys.platform.startswith('linux')
            )
            details = roblox_auth.get_auth_failure_details()
            return cookie, details

        try:
            cookie, details = call_compatibility_boundary(_load_auth_cookie)
        except CompatibilityBoundaryError as wrapped:
            exc = wrapped.cause
            log_buffer.log(
                'Auth',
                f'Unexpected error during startup auth check: {type(exc).__name__}: {exc}',
            )
            return
        auth_check_invoker.completed.emit(bool(cookie), details)

    QTimer.singleShot(1500, run_in_thread(_check_auth_cookie_once))
