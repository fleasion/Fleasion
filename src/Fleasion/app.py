"""Application entrypoint."""

import atexit
import html
import json
import os
import platform
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QSharedMemory, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel

from . import __version__
from .config import ConfigManager
from .modifications import ModificationManager
from .prejsons import download_prejsons
from .proxy import ProxyMaster, check_and_patch_running_roblox_ca
from .tray import SystemTray
from .utils import APP_DISCORD, APP_NAME, CONFIG_DIR, LOG_FILE, delete_cache, get_icon_path, get_roblox_player_exe_path, get_roblox_studio_exe_path, is_roblox_running, is_studio_running, launch_as_standard_user, log_buffer, open_folder, run_in_thread, start_update_check, time_tracker


_SINGLE_INSTANCE_KEY = 'FleasionSingleInstance'
_SINGLE_INSTANCE_CONTROL_SERVER = 'FleasionSingleInstanceControl'



class _FirstTimeSetupMessageBox(QMessageBox):
    """Message box that must be acknowledged with OK."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._can_accept = False

    def allow_accept(self):
        self._can_accept = True

    def accept(self):
        if self._can_accept:
            super().accept()

    def reject(self):
        return

    def closeEvent(self, event):
        event.ignore()


class _MacOSAuthSourceDialog(QDialog):
    """Browser-token startup prompt that only closes through explicit choices."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.allow_reject = False

    def reject(self):
        if self.allow_reject:
            super().reject()

    def closeEvent(self, event):
        if self.allow_reject:
            event.accept()
        else:
            event.ignore()


# ---------------------------------------------------------------------------
# UAC / elevation helpers
# ---------------------------------------------------------------------------

def _is_admin() -> bool:
    """Return True if the current process has administrator/root privileges."""
    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        return hasattr(os, 'geteuid') and os.geteuid() == 0
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_as_admin(extra_args: str = '', parent_hwnd: int | None = None) -> bool:
    """Silently attempt to relaunch elevated via the platform prompt.

    Shows only the standard Windows UAC or macOS administrator prompt.
    Returns True if the elevated process was spawned (caller should exit).
    Returns False if the user declined or the relaunch failed.
    """
    if sys.platform == 'darwin':
        existing_args = sys.argv[1:]
        if not any(arg.startswith('--fleasion-user-localappdata=') for arg in existing_args):
            existing_args.append(f'--fleasion-user-localappdata={CONFIG_DIR.parent}')
        if extra_args.strip():
            existing_args.extend(extra_args.strip().split())

        if getattr(sys, 'frozen', False):
            launch = [sys.executable, *existing_args]
            shell_cmd = (
                f'FLEASION_USER_HOME={shlex.quote(str(Path.home()))} '
                f'{shlex.join(launch)} >/tmp/fleasion-admin.log 2>&1 &'
            )
        else:
            project_root = Path(__file__).resolve().parents[2]
            launcher = project_root / 'launcher.py'
            python_exe = Path(sys.executable)
            launch = [str(python_exe), str(launcher), *existing_args]
            shell_cmd = (
                f'cd {shlex.quote(str(project_root))} && '
                f'FLEASION_USER_HOME={shlex.quote(str(Path.home()))} '
                f'PYTHONPATH={shlex.quote(str(project_root / "src"))} '
                f'{shlex.join(launch)} >/tmp/fleasion-admin.log 2>&1 &'
            )

        script = 'do shell script ' + json.dumps(shell_cmd) + ' with administrator privileges'
        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception as exc:
            log_buffer.log('UAC', f'macOS administrator relaunch failed: {exc}')
            return False

        if result.returncode != 0:
            err = (result.stderr or result.stdout or '').strip()
            log_buffer.log('UAC', f'macOS administrator relaunch was cancelled or failed: {err or result.returncode}')
            return False
        return True

    if sys.platform.startswith('linux'):
        log_buffer.log('UAC', 'Linux administrator relaunch skipped: proxy uses the privileged helper instead')
        return False

    import ctypes

    existing_args = sys.argv[1:]
    if not any(arg.startswith('--fleasion-user-localappdata=') for arg in existing_args):
        local_appdata = os.environ.get('LOCALAPPDATA') or str(CONFIG_DIR.parent)
        existing_args.append(f'--fleasion-user-localappdata={local_appdata}')
    if extra_args.strip():
        existing_args.extend(extra_args.strip().split())

    if getattr(sys, 'frozen', False):
        # Compiled .exe — sys.executable is the .exe itself
        exe = sys.executable
        params = subprocess.list2cmdline(existing_args) if existing_args else None
    else:
        # Dev / uv run — locate the uv executable and replay the original
        # invocation through it.  Running the Python interpreter directly in
        # the elevated process would miss the uv-managed virtualenv entirely,
        # causing import failures and a silent crash.
        import shutil
        uv_exe = shutil.which('uv') or shutil.which('uv.exe')
        if uv_exe:
            # Reconstruct:  uv run fleasion  (the original entry-point)
            exe = uv_exe
            # Pass the project directory so uv finds pyproject.toml correctly
            cwd = os.path.dirname(os.path.abspath(sys.argv[0]))
            # Walk up from the script to find the dir containing pyproject.toml
            check = cwd
            for _ in range(6):
                if os.path.exists(os.path.join(check, 'pyproject.toml')):
                    cwd = check
                    break
                check = os.path.dirname(check)
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
    import ctypes.wintypes

    SEE_MASK_NO_CONSOLE    = 0x00008000
    SEE_MASK_NOCLOSEPROCESS = 0x00000040

    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ('cbSize',        ctypes.wintypes.DWORD),
            ('fMask',         ctypes.wintypes.ULONG),
            ('hwnd',          ctypes.wintypes.HWND),
            ('lpVerb',        ctypes.wintypes.LPCWSTR),
            ('lpFile',        ctypes.wintypes.LPCWSTR),
            ('lpParameters',  ctypes.wintypes.LPCWSTR),
            ('lpDirectory',   ctypes.wintypes.LPCWSTR),
            ('nShow',         ctypes.c_int),
            ('hInstApp',      ctypes.wintypes.HINSTANCE),
            ('lpIDList',      ctypes.c_void_p),
            ('lpClass',       ctypes.wintypes.LPCWSTR),
            ('hkeyClass',     ctypes.wintypes.HKEY),
            ('dwHotKey',      ctypes.wintypes.DWORD),
            ('hIconOrMonitor',ctypes.wintypes.HANDLE),
            ('hProcess',      ctypes.wintypes.HANDLE),
        ]

    sei = _SHELLEXECUTEINFOW()
    sei.cbSize       = ctypes.sizeof(_SHELLEXECUTEINFOW)
    sei.fMask        = SEE_MASK_NO_CONSOLE | SEE_MASK_NOCLOSEPROCESS
    sei.hwnd         = parent_hwnd
    sei.lpVerb       = 'runas'
    sei.lpFile       = exe
    sei.lpParameters = params
    sei.lpDirectory  = os.path.dirname(os.path.abspath(exe)) or None
    # SW_HIDE (0) for dev/uv mode: hides the uv.exe console wrapper.
    # SW_SHOWNORMAL (1) for compiled .exe: the exe IS the app, we need windows to show.
    sei.nShow        = 0 if not getattr(sys, 'frozen', False) else 1
    sei.hInstApp     = None

    shell32 = ctypes.WinDLL('shell32', use_last_error=True)

    reset_env_key = 'PYINSTALLER_RESET_ENVIRONMENT'
    old_reset_env = os.environ.get(reset_env_key)
    if getattr(sys, 'frozen', False):
        os.environ[reset_env_key] = '1'
    try:
        ok = shell32.ShellExecuteExW(ctypes.byref(sei))
    finally:
        if getattr(sys, 'frozen', False):
            if old_reset_env is None:
                os.environ.pop(reset_env_key, None)
            else:
                os.environ[reset_env_key] = old_reset_env
    if not ok:
        err = ctypes.get_last_error()
        if err == 1223:  # ERROR_CANCELLED: user declined UAC
            log_buffer.log('UAC', 'Administrator relaunch was cancelled by the user')
        else:
            log_buffer.log('UAC', f'Administrator relaunch failed: WinError {err}: {ctypes.FormatError(err)}')
    return bool(ok)


def _attempt_silent_elevation(extra_args: str = '', parent_hwnd: int | None = None) -> bool:
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


def _visible_parent_widget():
    """Return the best visible Qt parent for startup dialogs."""
    _top = QApplication.topLevelWidgets()
    return next((w for w in _top if w.isVisible()), QApplication.activeWindow())


def _window_handle(widget) -> int | None:
    """Return a native window handle for ShellExecuteExW, if Qt has one."""
    if widget is None:
        return None
    try:
        return int(widget.winId())
    except Exception:
        return None


def _show_admin_required_dialog(parent=None):
    """Warn that the non-elevated instance cannot provide Fleasion's core behavior."""
    _top = QApplication.topLevelWidgets()
    _parent = parent or _visible_parent_widget()
    _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)

    msg = QMessageBox(_parent)
    if _on_top:
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    msg.setWindowTitle('Fleasion - Administrator Mode Required')
    msg.setIcon(QMessageBox.Icon.Warning)
    if sys.platform == 'darwin':
        msg.setText('Fleasion needs its macOS proxy helper before interception can start.')
        msg.setInformativeText(
            'Run Fleasion as your normal macOS user, then approve the proxy-helper install prompt. '
            'The helper owns port 443, updates /etc/hosts, and patches Roblox SSL trust while the app stays unprivileged.'
        )
    elif sys.platform.startswith('linux'):
        msg.setText("Fleasion needs administrator permission for Linux/Sober interception.")
        msg.setInformativeText(
            'Linux support targets the Sober Flatpak client.\n\n'
            'Asset interception, scraping, replacement, hosts-file changes, and the local HTTPS proxy need root access '
            'because Fleasion writes /etc/hosts and listens on local port 443.'
        )
    else:
        msg.setText("Fleasion won't work unless you're in admin mode.")
        msg.setInformativeText(
            'Windows did not start Fleasion with administrator rights.\n\n'
            'Asset interception, scraping, replacement, hosts-file changes, and the local HTTPS proxy may not work.\n\n'
            'Close Fleasion and run it as Administrator.'
        )
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    if icon_path := get_icon_path():
        from PyQt6.QtGui import QIcon
        msg.setWindowIcon(QIcon(str(icon_path)))
    msg.exec()


def _show_proxy_bind_error_dialog(details: dict):
    """Show a user-facing popup when Fleasion cannot bind proxy port 443."""
    port = int(details.get('port') or 443)
    owners = details.get('owners') or []

    _top = QApplication.topLevelWidgets()
    _parent = next((w for w in _top if w.isVisible()), None)
    _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)

    msg = QMessageBox(_parent)
    if _on_top:
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    msg.setWindowTitle('Fleasion - Proxy Port Conflict')
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(f'Fleasion could not start its local proxy on port {port}.')

    if owners:
        owner_lines = '<br>'.join(
            f"- {html.escape(str(owner.get('process_name') or 'Unknown'))} "
            f"(PID {int(owner.get('pid') or 0)}) on "
            f"{html.escape(str(owner.get('local_address') or '0.0.0.0'))}:{port}"
            for owner in owners
        )
        owners_html = f'Port {port} is already in use by:<br>{owner_lines}<br><br>'
    else:
        owners_html = f'Port {port} is already in use by another process.<br><br>'

    discord_url = APP_DISCORD
    if not discord_url.startswith(('http://', 'https://')):
        discord_url = f'https://{discord_url}'

    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setInformativeText(
        owners_html
        + 'Close the conflicting process, then relaunch Fleasion.<br><br>'
        + f'Need help? <a href="{html.escape(discord_url)}">{html.escape(APP_DISCORD)}</a>'
    )
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    if icon_path := get_icon_path():
        from PyQt6.QtGui import QIcon
        msg.setWindowIcon(QIcon(str(icon_path)))

    for label in msg.findChildren(QLabel):
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        label.setOpenExternalLinks(True)

    msg.exec()


def _show_hosts_write_exhausted_dialog(details: dict):
    """Show a user-facing popup when hosts writes fail after all retries."""
    import os

    default_hosts_path = '/etc/hosts' if sys.platform == 'darwin' or sys.platform.startswith('linux') else r'C:\Windows\System32\drivers\etc\hosts'
    default_hosts_dir = '/etc' if sys.platform == 'darwin' or sys.platform.startswith('linux') else r'C:\Windows\System32\drivers\etc'
    hosts_path = str(details.get('hosts_path') or default_hosts_path)
    hosts_directory = str(
        details.get('hosts_directory')
        or os.path.dirname(hosts_path)
        or default_hosts_dir
    )
    raw_error = str(details.get('error') or '').strip()

    _top = QApplication.topLevelWidgets()
    _parent = next((w for w in _top if w.isVisible()), None)
    _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)

    discord_url = APP_DISCORD
    if not discord_url.startswith(('http://', 'https://')):
        discord_url = f'https://{discord_url}'

    while True:
        msg = QMessageBox(_parent)
        if _on_top:
            msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        msg.setWindowTitle('Fleasion - Hosts File Write Failed')
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText('Fleasion could not modify the system hosts file after every write attempt was exhausted.')

        diagnostics_html = ''
        if raw_error:
            diagnostics_html = (
                'Technical details:<br>'
                + html.escape(raw_error)
                + '<br><br>'
            )

        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setInformativeText(
            'Most likely causes:<br>'
            'A) Antivirus/security software is protecting the hosts file '
            '(for example Webroot or Kaspersky).<br>'
            'B) A restrictive system permission setting is blocking writes.<br><br>'
            + f'Hosts file path:<br>{html.escape(hosts_path)}<br><br>'
            + 'Quick fix:<br>'
            + '1) Click "Click Here to Open Directory".<br>'
            + '2) Rename the file named "hosts" (no extension) to anything, or delete it.<br>'
            + '3) Restart Fleasion.<br><br>'
            + diagnostics_html
            + f'Need help? <a href="{html.escape(discord_url)}">{html.escape(APP_DISCORD)}</a>'
        )

        open_dir_button = msg.addButton('Click Here to Open Directory', QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Ok)

        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
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


def _show_macos_ca_patch_failed_dialog(details: dict):
    """Show a user-facing popup when the helper cannot verify Roblox cacert.pem."""
    _top = QApplication.topLevelWidgets()
    _parent = next((w for w in _top if w.isVisible()), None)
    _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)

    failed = details.get('failed') or []
    verified = details.get('verified') or []
    failed_lines = []
    if isinstance(failed, list):
        for item in failed[:6]:
            if isinstance(item, dict):
                path = item.get('ca_file') or item.get('resource_dir') or '(unknown path)'
                error = item.get('error') or item.get('status') or 'unknown error'
                failed_lines.append(f'- {html.escape(str(path))}: {html.escape(str(error))}')

    unhealthy_lines = []
    if isinstance(verified, list):
        for item in verified[:6]:
            if isinstance(item, dict) and not item.get('healthy'):
                path = item.get('path') or '(unknown path)'
                error = item.get('error') or 'verification failed'
                unhealthy_lines.append(f'- {html.escape(str(path))}: {html.escape(str(error))}')

    diagnostics_html = ''
    if failed_lines or unhealthy_lines:
        diagnostics_html = (
            '<br><br>Diagnostics:<br>'
            + '<br>'.join(failed_lines + unhealthy_lines)
        )

    msg = QMessageBox(_parent)
    if _on_top:
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    msg.setWindowTitle('Fleasion - macOS Roblox CA Patch Failed')
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText('Fleasion could not verify Roblox SSL trust patching, so the proxy was not started.')
    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setInformativeText(
        'Roblox would reject Fleasion proxy certificates until its bundled '
        '<code>ssl/cacert.pem</code> contains the Fleasion CA exactly once.<br><br>'
        'Restart Fleasion and approve the helper install/upgrade if prompted. If this keeps happening, '
        'repair or reinstall Roblox, then start Fleasion again.'
        + diagnostics_html
    )
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    if icon_path := get_icon_path():
        from PyQt6.QtGui import QIcon
        msg.setWindowIcon(QIcon(str(icon_path)))

    for label in msg.findChildren(QLabel):
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )

    msg.exec()


def _choose_macos_auth_source_on_launch(config_manager, tray=None, *, force: bool = False) -> str:
    """Ask macOS users which browser should be queried for Roblox auth."""
    if sys.platform != 'darwin':
        return 'unavailable'
    if config_manager.macos_auth_source and not force:
        return 'already-configured'

    _top = QApplication.topLevelWidgets()
    _parent = next((w for w in _top if w.isVisible()), None)
    _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)

    dialog = _MacOSAuthSourceDialog(_parent)
    if _on_top:
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    dialog.setWindowTitle('Fleasion - Roblox Login Source')
    dialog.setMinimumWidth(620)

    selected: dict[str, str] = {}
    buttons: list[QPushButton] = []
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(14)

    title = QLabel('Which browser is signed in to roblox.com?')
    title.setStyleSheet('font-size: 18px; font-weight: 700;')
    layout.addWidget(title)

    warning = QLabel(
        'Most Fleasion account-aware features will not work until a valid Roblox token is available: '
        'private asset downloads, account launches, authenticated asset details, subplace metadata, '
        'and private-server flows may fail or wait.'
    )
    warning.setWordWrap(True)
    warning.setStyleSheet('font-weight: 600; color: #e0a53a;')
    layout.addWidget(warning)

    body = QLabel(
        'Choose the browser where roblox.com is already signed in. macOS may ask for browser-data access; '
        'choose Always Allow to avoid approving it every launch. Fleasion will test the browser before saving it.'
    )
    body.setWordWrap(True)
    layout.addWidget(body)

    status = QLabel('')
    status.setWordWrap(True)
    layout.addWidget(status)

    def _set_busy(browser: str):
        status.setText(f'Checking {browser} for a valid Roblox login...')
        for btn in buttons:
            btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

    def _set_ready(message: str):
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

    grid = QGridLayout()
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(8)
    browsers = ('Chrome', 'Safari', 'Firefox', 'Brave', 'Edge', 'Chromium', 'Opera', 'Vivaldi')

    def _choose(browser: str) -> None:
        _set_busy(browser)
        try:
            from .utils.roblox_auth import discover_browser_roblosecurity

            cookie, source = discover_browser_roblosecurity(
                include_keychain=True,
                explicit_import=True,
                browser=browser,
            )
        except Exception as exc:
            log_buffer.log('Auth', f'Unexpected error while checking {browser}: {type(exc).__name__}: {exc}')
            _set_ready(f'{browser} could not be checked: {type(exc).__name__}: {exc}')
            return
        if cookie:
            _save_and_accept(source or browser)
            return
        if browser == 'Safari':
            _set_ready(
                'No valid Roblox login token was found in Safari. If the log says Operation not permitted, macOS is '
                'blocking Safari app-data access; grant Fleasion Full Disk Access in System Settings > Privacy & '
                'Security, choose another browser, import a token manually, or continue without a token.'
            )
            return
        _set_ready(
            f'No valid Roblox login token was found in {browser}. Choose another browser, import a token manually, '
            'or continue without a token.'
        )

    for index, browser in enumerate(browsers):
        button = QPushButton(browser)
        button.setMinimumHeight(34)
        button.clicked.connect(lambda _checked=False, value=browser: _choose(value))
        grid.addWidget(button, index // 4, index % 4)
        buttons.append(button)
    layout.addLayout(grid)

    footer = QHBoxLayout()
    footer.addStretch()
    manual_btn = QPushButton('Import Token Manually')
    footer.addWidget(manual_btn)
    skip_btn = QPushButton('Continue Without Token')
    footer.addWidget(skip_btn)
    layout.addLayout(footer)
    buttons.extend((manual_btn, skip_btn))

    def _manual_import() -> None:
        from .gui.rando_stuff_tab import AddAccountDialog
        from .utils.roblox_auth import store_manual_roblosecurity

        dlg = AddAccountDialog(dialog, title='Import Roblox Token')
        dlg.set_ok_label('Import')
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
            dlg.setWindowIcon(QIcon(str(icon_path)))
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result_cookie:
            return
        if not store_manual_roblosecurity(dlg.result_cookie):
            QMessageBox.warning(
                dialog,
                'Token Import Failed',
                'Fleasion could not store the Roblox token encrypted.',
            )
            return
        _save_and_accept('manual')

    def _continue_without_token() -> None:
        selected['continue_without_token'] = '1'
        dialog.allow_reject = True
        dialog.reject()

    manual_btn.clicked.connect(_manual_import)
    skip_btn.clicked.connect(_continue_without_token)

    if icon_path := get_icon_path():
        from PyQt6.QtGui import QIcon
        dialog.setWindowIcon(QIcon(str(icon_path)))

    dialog.exec()
    if selected_browser := selected.get('browser'):
        config_manager.macos_auth_source = selected_browser
        try:
            from .utils.roblox_auth import notify_auth_source_changed
            notify_auth_source_changed()
        except Exception:
            pass
        if tray is not None and hasattr(tray, '_refresh_settings_tab'):
            tray._refresh_settings_tab()
        return 'selected'
    if selected.get('continue_without_token'):
        return 'skipped'
    return 'dismissed'


def _show_auth_cookie_unavailable_dialog(details: dict):
    """Show a user-facing popup when no readable Roblox auth cookie can be found."""
    _top = QApplication.topLevelWidgets()
    _parent = next((w for w in _top if w.isVisible()), None)
    _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)

    discord_url = APP_DISCORD
    if not discord_url.startswith(('http://', 'https://')):
        discord_url = f'https://{discord_url}'

    attempted = details.get('attempted_paths') or []
    existing = details.get('existing_paths') or []
    if not isinstance(attempted, list):
        attempted = []
    if not isinstance(existing, list):
        existing = []

    existing_html = ''
    if existing:
        existing_html = (
            'RobloxCookies.dat files were found here, but none could be used:<br>'
            + '<br>'.join(html.escape(str(path)) for path in existing[:8])
            + '<br><br>'
        )

    skipped_token = bool(details.get('user_skipped_token'))
    if sys.platform == 'darwin':
        diagnostics_html = (
            'Diagnostics:<br>'
            f'macOS home: {html.escape(str(details.get("home") or "Unknown"))}<br>'
            f'Fleasion config root: {html.escape(str(details.get("local_appdata") or "Unknown"))}<br>'
            f'Default cookie path: {html.escape(str(details.get("default_cookie_path") or "Unknown"))}<br>'
            f'Candidate paths checked: {len(attempted)}<br><br>'
        )
        most_likely_html = (
            (
                'You chose to continue without a Roblox login token.<br><br>'
                if skipped_token
                else 'Fleasion checked RobloxCookies.dat plus the selected login source, but found zero usable Roblox login tokens.<br><br>'
            )
            +
            'This token is required for authenticated asset downloads, account launches, '
            'private-server joins, and other account-aware features.<br><br>'
            'Sign in to roblox.com in Chrome, Firefox, Brave, Edge, Chromium, Opera, '
            'or Vivaldi, then choose that browser in Settings > Roblox Login. Safari may require '
            'Full Disk Access for Fleasion before macOS allows its cookie database to be read.<br><br>'
            'You can also store a token manually from Settings > Roblox Login > Import Token, '
            'or retry from Dashboard > Miscellaneous > Account Manager > Import Browser Login.<br><br>'
        )
    elif sys.platform.startswith('linux'):
        diagnostics_html = (
            'Diagnostics:<br>'
            f'Linux home: {html.escape(str(details.get("home") or "Unknown"))}<br>'
            f'Fleasion config root: {html.escape(str(details.get("local_appdata") or "Unknown"))}<br>'
            f'Default Sober cookie path: {html.escape(str(details.get("default_cookie_path") or "Unknown"))}<br>'
            f'Candidate paths checked: {len(attempted)}<br><br>'
        )
        most_likely_html = (
            (
                'You chose to continue without a Roblox login token.<br><br>'
                if skipped_token
                else 'Fleasion checked Sober cookies plus supported browser login stores, but found zero usable Roblox login tokens.<br><br>'
            )
            +
            'This token is required for authenticated asset downloads, account launches, '
            'private-server joins, and other account-aware features.<br><br>'
            'Sign in through Sober or log in to roblox.com in Firefox or a Chrome-family browser, '
            'then try again. You can retry from Dashboard > Miscellaneous > Account Manager > '
            'Import Browser Login.<br><br>'
        )
    else:
        diagnostics_html = (
            'Diagnostics:<br>'
            f'Windows username: {html.escape(str(details.get("username") or "Unknown"))}<br>'
            f'USERPROFILE: {html.escape(str(details.get("userprofile") or "Unknown"))}<br>'
            f'Fleasion LocalAppData: {html.escape(str(details.get("local_appdata") or "Unknown"))}<br>'
            f'Default cookie path: {html.escape(str(details.get("default_cookie_path") or "Unknown"))}<br>'
            f'Candidate paths checked: {len(attempted)}<br><br>'
        )
        most_likely_html = (
            'Most likely cause:<br>'
            'Fleasion is running under a different Windows user account than Roblox, '
            'or it inherited the wrong LocalAppData path during elevation/startup.<br><br>'
            'Quick fix:<br>'
            '1) Fully exit Fleasion from the system tray.<br>'
            '2) Start Fleasion from the same Windows account that runs Roblox.<br>'
            '3) If Windows shows a UAC prompt, do not approve it with a different admin account.<br>'
            '4) Launch Roblox once, then restart Fleasion.<br><br>'
        )

    msg = QMessageBox(_parent)
    if _on_top:
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    msg.setWindowTitle('Fleasion - Roblox Token Not Readable')
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(
        'Fleasion is continuing without a Roblox login token.'
        if skipped_token
        else 'Fleasion could not read a usable Roblox login token.'
    )
    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setInformativeText(
        (
            'Authenticated Roblox asset downloads may fail until a token is imported.<br><br>'
            if skipped_token
            else 'Authenticated Roblox asset downloads may fail until this is fixed.<br><br>'
        )
        + most_likely_html
        + existing_html
        + diagnostics_html
        + f'Need help? <a href="{html.escape(discord_url)}">{html.escape(APP_DISCORD)}</a>'
    )
    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        import webbrowser

        open_login_button = msg.addButton('Open Roblox Login', QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Ok)
    else:
        open_login_button = None
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)

    if icon_path := get_icon_path():
        from PyQt6.QtGui import QIcon
        msg.setWindowIcon(QIcon(str(icon_path)))

    for label in msg.findChildren(QLabel):
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        label.setOpenExternalLinks(True)

    msg.exec()
    if open_login_button is not None and msg.clickedButton() == open_login_button:
        if sys.platform.startswith('linux'):
            launch_as_standard_user('https://www.roblox.com/login')
        else:
            webbrowser.open('https://www.roblox.com/login')


class _ProxyErrorInvoker(QObject):
    """Main-thread bridge for proxy startup errors emitted from worker threads."""

    show_proxy_error = pyqtSignal(str, dict)
    disable_proxy_features = pyqtSignal(str)

    @pyqtSlot(str, dict)
    def handle_proxy_error(self, code: str, details: dict):
        if code == 'port_bind_failed':
            _show_proxy_bind_error_dialog(details)
        elif code == 'hosts_write_exhausted':
            _show_hosts_write_exhausted_dialog(details)
        elif code == 'macos_ca_patch_failed':
            _show_macos_ca_patch_failed_dialog(details)


def _disable_proxy_features_after_start_failure(config_manager, tray: SystemTray | None, reason: str):
    """Handle proxy startup failure without silently mutating the saved setting."""
    if not config_manager.proxy_features_enabled:
        return
    if sys.platform.startswith('linux'):
        log_buffer.log('Proxy', f'Linux proxy helper start failed; leaving proxy features enabled: {reason}')
        QMessageBox.warning(
            _visible_parent_widget(),
            'Fleasion - Linux Proxy Helper Unavailable',
            'Fleasion could not start the Linux proxy helper.\n\n'
            f'{reason}\n\n'
            'Proxy features remain enabled in Settings, so Fleasion will try again the next time you launch it.',
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

    completed = pyqtSignal(bool, dict)


class RobloxExitMonitor(QObject):
    """Monitors Roblox process and triggers cache deletion on exit."""

    _studio_detected = pyqtSignal()
    player_status_changed = pyqtSignal(bool)  # Emitted when RobloxPlayerBeta opens/closes (True = running)

    def __init__(self, config_manager, proxy_master=None, mod_manager=None):
        super().__init__()
        self.config_manager = config_manager
        self._proxy_master = proxy_master
        self._mod_manager = mod_manager
        self.was_running = False
        self._player_was_running = False
        self._studio_was_running = False
        self._studio_notified = False
        self._studio_suppress_session = False
        self._studio_detected.connect(self._on_studio_detected)

    def is_player_running(self) -> bool:
        """Return whether Roblox Player is currently running."""
        return is_roblox_running()

    @run_in_thread
    def check_roblox_status(self):
        """Check if Roblox has exited and trigger cache deletion if needed."""
        is_running = is_roblox_running()

        # --- Roblox Player: player status changed signal ---
        if self._player_was_running != is_running:
            self.player_status_changed.emit(is_running)
            if self._proxy_master is not None:
                self._proxy_master.set_roblox_player_running(is_running)

        # --- Roblox Player: launch detection - check CA cert on new launch ---
        if not self._player_was_running and is_running:
            if sys.platform.startswith('linux'):
                exe_path = Path('org.vinegarhq.Sober')
                if self._mod_manager is not None:
                    self._mod_manager.refresh_roblox_dirs()
                proxy_features_enabled = self.config_manager.proxy_features_enabled
                if self._proxy_master is not None and proxy_features_enabled:
                    run_in_thread(self._proxy_master.refresh_and_restart_roblox)(exe_path)
                elif self._proxy_master is None and proxy_features_enabled:
                    run_in_thread(check_and_patch_running_roblox_ca)(exe_path)
                elif not proxy_features_enabled:
                    log_buffer.log('Certificate', 'Sober launch detected: proxy features disabled, skipping proxy CA refresh')
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
                        self._mod_manager.refresh_roblox_dirs()
                    if self._proxy_master is not None and proxy_features_enabled:
                        run_in_thread(self._proxy_master.refresh_and_restart_roblox)(exe_path)
                    elif self._proxy_master is None and proxy_features_enabled:
                        run_in_thread(check_and_patch_running_roblox_ca)(exe_path)
                    elif not proxy_features_enabled:
                        log_buffer.log('Certificate', 'Roblox launch detected: proxy features disabled, skipping proxy CA refresh')
                else:
                    log_buffer.log('Certificate', 'Roblox launch detected but could not resolve exe path for CA check')
        self._player_was_running = is_running

        # --- Roblox Player: auto cache deletion on exit ---
        if self.config_manager.auto_delete_cache_on_exit:
            if self.was_running and not is_running:
                log_buffer.log('Cache', 'Roblox exited, deleting cache...')
                run_in_thread(self._delete_cache_background)()
            self.was_running = is_running
        else:
            self.was_running = False

        # --- Roblox Studio: patch CA cert on launch, show warning ---
        studio_running = is_studio_running()

        if not self._studio_was_running and studio_running:
            studio_exe_path = get_roblox_studio_exe_path()
            if studio_exe_path is None:
                for _ in range(10):
                    time.sleep(1.0)
                    studio_exe_path = get_roblox_studio_exe_path()
                    if studio_exe_path is not None:
                        break
            if studio_exe_path is not None and self.config_manager.proxy_features_enabled:
                if sys.platform == 'darwin':
                    log_buffer.log('Certificate', 'Studio launch detected on macOS: skipping proxy CA refresh')
                else:
                    run_in_thread(check_and_patch_running_roblox_ca)(studio_exe_path)
            elif studio_exe_path is not None:
                log_buffer.log('Certificate', 'Studio launch detected: proxy features disabled, skipping proxy CA refresh')
            else:
                log_buffer.log('Certificate', 'Studio launch detected but could not resolve exe path for CA check')

            if not self._studio_suppress_session and not self._studio_notified:
                self._studio_notified = True
                self._studio_detected.emit()

        if self._studio_was_running and not studio_running:
            self._studio_notified = False

        self._studio_was_running = studio_running

    def _on_studio_detected(self):
        """Show the Roblox Studio warning dialog (called on the main thread via signal)."""
        _top = QApplication.topLevelWidgets()
        _parent = next((w for w in _top if w.isVisible()), None)
        _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)
        dialog = QDialog(_parent)
        if _on_top:
            dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        dialog.setWindowTitle('Fleasion — Roblox Studio Detected')

        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        label = QLabel(
            'Roblox Studio is currently open.\n\n'
            'Asset modification and scraping may not work correctly while '
            'Roblox Studio is running.'
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        btn_layout = QHBoxLayout()
        suppress_btn = QPushButton("Don't Show for Session")
        ok_btn = QPushButton('OK')
        ok_btn.setDefault(True)
        ok_btn.setFixedWidth(80)

        btn_layout.addWidget(suppress_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
            dialog.setWindowIcon(QIcon(str(icon_path)))

        ok_btn.clicked.connect(dialog.accept)

        def _suppress():
            self._studio_suppress_session = True
            dialog.accept()

        suppress_btn.clicked.connect(_suppress)
        dialog.exec()

    def _delete_cache_background(self):
        """Delete cache in background thread."""
        messages = delete_cache()
        for msg in messages:
            log_buffer.log('Cache', msg)


def _looks_like_macos_fleasion_command(command: str) -> bool:
    """Return whether a macOS process command is a Fleasion app/dev launch."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return False

    executable = Path(tokens[0]).name.lower()
    if executable == 'fleasion' or executable.startswith('fleasion-v'):
        return True

    for index, token in enumerate(tokens):
        if Path(token).name == 'launcher.py':
            return True
        if token == '-m' and index + 1 < len(tokens) and tokens[index + 1].lower() == 'fleasion':
            return True
    return False


def _other_fleasion_pids() -> list:
    """Return PIDs of other Fleasion processes (excludes current process and its parent)."""
    import os
    import subprocess

    current_pid = os.getpid()
    parent_pid = os.getppid()
    safe_pids = {current_pid, parent_pid}
    exe_name = os.path.basename(sys.executable)
    pids = []

    if sys.platform != 'win32':
        try:
            result = subprocess.run(
                ['ps', '-axo', 'pid=,ppid=,command='],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for raw in result.stdout.splitlines():
                try:
                    pid_text, _ppid_text, command = raw.strip().split(None, 2)
                    pid = int(pid_text)
                except (ValueError, TypeError):
                    continue
                if pid not in safe_pids and _looks_like_macos_fleasion_command(command):
                    pids.append(pid)
        except Exception:
            pass
        return pids

    try:
        if exe_name.lower() not in ('python.exe', 'python3.exe'):
            result = subprocess.run(
                ['tasklist', '/FI', f'IMAGENAME eq {exe_name}', '/FO', 'CSV', '/NH'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=10
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip().strip('"')
                parts = line.split('","')
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1])
                        if pid not in safe_pids:
                            pids.append(pid)
                    except (ValueError, IndexError):
                        pass
        else:
            ps_cmd = (
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                'Select-Object ProcessId, CommandLine | ConvertTo-Json -Depth 1'
            )
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_cmd],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=30
            )
            try:
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    data = [data]
                for proc in data:
                    pid = int(proc.get('ProcessId', 0))
                    cmdline = (proc.get('CommandLine') or '').lower()
                    if pid in safe_pids or pid == 0:
                        continue
                    if 'launcher.py' in cmdline or 'fleasion' in cmdline:
                        pids.append(pid)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
    except Exception:
        pass

    return pids


def _request_running_instance_exit(timeout_ms: int = 2000) -> bool:
    """Ask the already-running Fleasion instance to exit through its Qt event loop."""
    try:
        socket = QLocalSocket()
        socket.connectToServer(_SINGLE_INSTANCE_CONTROL_SERVER)
        if not socket.waitForConnected(timeout_ms):
            return False

        socket.write(b'quit\n')
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        socket.waitForDisconnected(1000)
        return True
    except Exception:
        return False


def _wait_for_other_fleasion_instances_to_exit(timeout_seconds: float = 8.0) -> bool:
    """Wait until no other Fleasion processes remain."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _other_fleasion_pids():
            return True
        time.sleep(0.1)
    return not _other_fleasion_pids()


def _request_other_fleasion_instances_exit(timeout_seconds: float = 8.0) -> bool:
    """Return True if other instances were asked to exit and disappeared."""
    if not _other_fleasion_pids():
        return True
    if not _request_running_instance_exit():
        return False
    return _wait_for_other_fleasion_instances_to_exit(timeout_seconds)


def _handle_single_instance_command(socket: QLocalSocket, tray: SystemTray):
    try:
        command = bytes(socket.readAll()).decode('utf-8', errors='replace').strip().lower()
        if command == 'quit':
            tray._exit_app()
    except Exception:
        pass


def _start_single_instance_control_server(app: QApplication, tray: SystemTray) -> QLocalServer | None:
    """Start a local control endpoint for clean single-instance handoff."""
    server = QLocalServer(app)

    if not server.listen(_SINGLE_INSTANCE_CONTROL_SERVER):
        QLocalServer.removeServer(_SINGLE_INSTANCE_CONTROL_SERVER)
        if not server.listen(_SINGLE_INSTANCE_CONTROL_SERVER):
            log_buffer.log('App', 'Single-instance control server could not start')
            return None

    def _handle_connection():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is not None:
                socket.readyRead.connect(lambda s=socket: _handle_single_instance_command(s, tray))
                if socket.bytesAvailable() > 0:
                    _handle_single_instance_command(socket, tray)

    server.newConnection.connect(_handle_connection)
    return server


def kill_other_fleasion_instances():
    """Kill all other Fleasion instances except the current process."""
    import os
    import subprocess

    if _request_other_fleasion_instances_exit():
        return

    for pid in _other_fleasion_pids():
        try:
            if sys.platform != 'win32':
                os.kill(pid, signal.SIGTERM)
            else:
                subprocess.run(
                    ['taskkill', '/PID', str(pid)],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5,
                )
                if _wait_for_other_fleasion_instances_to_exit(2.0):
                    continue
                subprocess.run(
                    ['taskkill', '/F', '/PID', str(pid)],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5,
                )
        except Exception:
            pass


def _configure_opengl_for_legacy_viewers() -> None:
    """Configure Qt before any OpenGL preview widgets create contexts."""
    if sys.platform.startswith('linux'):
        os.environ.setdefault('QT_OPENGL', 'desktop')
    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    except Exception as exc:
        log_buffer.log('OpenGL', f'Could not enable shared OpenGL contexts: {exc}')
    try:
        from .cache.gl_format import configure_default_legacy_gl_format
        configure_default_legacy_gl_format()
    except Exception as exc:
        log_buffer.log('OpenGL', f'Could not configure default OpenGL format: {exc}')


def main():
    """Main application entry point."""
    import argparse as _ap
    _parser = _ap.ArgumentParser(add_help=False)
    _parser.add_argument('--no-dashboard', action='store_true',
                         help='Suppress dashboard on launch (used by autostart task)')
    _parser.add_argument('--kill-others', action='store_true',
                         help='Kill other Fleasion instances on startup (used when relaunching elevated)')
    _parser.add_argument('--proxy-debug', '-proxy-debug', action='store_true', help=_ap.SUPPRESS)
    _parser.add_argument('--proxy-debug-mode', choices=['a', 'b', 'c', 'd', 'e', 'full'], help=_ap.SUPPRESS)
    _parser.add_argument('--fleasion-user-localappdata', help=_ap.SUPPRESS)
    _parser.add_argument('--install-desktop-entry', '--install-linux-desktop', action='store_true',
                         help='Install the Linux desktop launcher and Polkit helper, then exit')
    _args, _ = _parser.parse_known_args()
    if _args.install_desktop_entry:
        if not sys.platform.startswith('linux'):
            print('Desktop entry installation is only supported on Linux.', file=sys.stderr)
            sys.exit(1)
        from .utils.platform_linux import install_desktop_entries

        result = install_desktop_entries()
        print(f'Installed desktop entry: {result["desktop_entry"]}')
        print(f'Installed launcher: {result["launcher"]}')
        if result.get('installed_app'):
            print(f'Installed app binary: {result["installed_app"]}')
        if result.get('installed_icon'):
            print(f'Installed icon: {result["installed_icon"]}')
        removed = result.get('removed_deprecated_entries') or []
        if removed:
            print('Removed deprecated non-admin desktop entries:')
            for path in removed:
                print(f'  {path}')
        sys.exit(0)

    _suppress_dashboard = _args.no_dashboard
    log_buffer.log('App', f'Version {__version__}')

    current_platform = platform.system()
    if current_platform not in {'Windows', 'Darwin', 'Linux'}:
        app = QApplication(sys.argv)
        QMessageBox.critical(
            None,
            'Unsupported Operating System',
            'Fleasion supports Windows, macOS, and Linux/Sober.\n\nThis application will now exit.',
            QMessageBox.StandardButton.Ok
        )
        sys.exit(1)

    _configure_opengl_for_legacy_viewers()

    # Create Qt application
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    if icon_path := get_icon_path():
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon_path)))
        if sys.platform == 'darwin':
            from .utils.platform_macos import set_application_icon
            set_application_icon(icon_path)

    if sys.platform == 'darwin' and _is_admin():
        QMessageBox.critical(
            None,
            'Fleasion - Do Not Run with sudo',
            'Run Fleasion as your normal macOS user.\n\n'
            'Fleasion installs a small privileged proxy helper when needed; the dashboard and menu-bar app must not run as root.',
        )
        sys.exit(1)

    # Single instance check.
    # When we've just been relaunched via UAC elevation, the non-elevated
    # instance may not have fully exited yet, leaving stale shared memory.
    # If we're admin, forcibly attach-and-detach to clear it so the
    # elevated instance can take over cleanly.
    if _is_admin():
        # If launched with --kill-others, kill before clearing stale memory so
        # the shared memory slot is freed by the time we try to claim it.
        if _args.kill_others:
            kill_other_fleasion_instances()
            import time as _time
            _time.sleep(0.3)
        _stale = QSharedMemory(_SINGLE_INSTANCE_KEY)
        if _stale.attach():
            _stale.detach()

    shared_memory = QSharedMemory(_SINGLE_INSTANCE_KEY)
    _shared_memory_created = shared_memory.create(1)
    if (
        not _shared_memory_created
        and sys.platform == 'darwin'
        and shared_memory.error() == QSharedMemory.SharedMemoryError.AlreadyExists
        and not _other_fleasion_pids()
    ):
        # A hard termination can leave Qt's POSIX shared-memory segment behind.
        # Attach/detach removes it when no real Fleasion process still owns it.
        _stale = QSharedMemory(_SINGLE_INSTANCE_KEY)
        if _stale.attach():
            _stale.detach()
        shared_memory = QSharedMemory(_SINGLE_INSTANCE_KEY)
        _shared_memory_created = shared_memory.create(1)

    if not _shared_memory_created:
        if shared_memory.error() == QSharedMemory.SharedMemoryError.AlreadyExists:
            # Another instance is already running.
            if _suppress_dashboard:
                sys.exit(0)
            # Non-admin processes cannot use taskkill on elevated processes — it
            # silently does nothing.  Branch on whether WE are admin rather than
            # trying to inspect the other process's token cross-privilege.
            msg_box = QMessageBox()
            msg_box.setWindowTitle('Fleasion Already Running')
            msg_box.setText('Another instance of Fleasion is already running (Check your system tray).')
            msg_box.setIcon(QMessageBox.Icon.Warning)

            # Set icon if available
            if icon_path := get_icon_path():
                from PyQt6.QtGui import QIcon
                msg_box.setWindowIcon(QIcon(str(icon_path)))

            msg_box.setInformativeText('Do you want to run another instance anyway?')

            if _is_admin() or sys.platform == 'darwin' or sys.platform.startswith('linux'):
                # Already elevated — can kill any process directly.
                kill_others_button = msg_box.addButton('Kill Others', QMessageBox.ButtonRole.AcceptRole)
                _kill_requires_elevation = False
            else:
                # Not admin — taskkill on an elevated process silently fails.
                # A single "Elevate & Kill Others" relaunches as admin with
                # --kill-others so the elevated copy handles it automatically.
                kill_others_button = msg_box.addButton('Elevate && Kill Others (Recommended)', QMessageBox.ButtonRole.AcceptRole)
                _kill_requires_elevation = True

            run_anyway_button = msg_box.addButton('Run Anyway (Bad)', QMessageBox.ButtonRole.AcceptRole)
            cancel_button = msg_box.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(cancel_button)

            msg_box.exec()

            if msg_box.clickedButton() == cancel_button:
                sys.exit(0)

            if msg_box.clickedButton() == kill_others_button:
                if _kill_requires_elevation:
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
    config_manager.settings['_runtime_proxy_debug'] = bool(_args.proxy_debug)
    config_manager.settings['_runtime_proxy_debug_mode'] = _args.proxy_debug_mode or 'full'

    # Gate non-admin launches before opening the usable GUI. Some Windows setups
    # show UAC as a taskbar item instead of foregrounding it, so startup must
    # block here until UAC is accepted, denied, or fails.
    _admin_prompt_needed = (
        sys.platform == 'win32'
        and config_manager.proxy_features_enabled
        and not _is_admin()
    )
    if sys.platform == 'darwin':
        from .utils.macos_proxy_helper import helper_is_ready

        start_proxy = config_manager.proxy_features_enabled and helper_is_ready()
    else:
        start_proxy = config_manager.proxy_features_enabled and not _admin_prompt_needed

    # Start tracking time wasted from the stored total
    time_tracker.init(config_manager.time_wasted_seconds)
    atexit.register(time_tracker.save, config_manager)

    proxy_error_invoker = _ProxyErrorInvoker()
    proxy_error_invoker.show_proxy_error.connect(proxy_error_invoker.handle_proxy_error)
    tray_ref: dict[str, SystemTray | None] = {'tray': None}

    def _handle_proxy_features_start_failure(reason: str):
        _disable_proxy_features_after_start_failure(config_manager, tray_ref.get('tray'), reason)

    proxy_error_invoker.disable_proxy_features.connect(_handle_proxy_features_start_failure)

    def _on_proxy_start_error(code: str, details: dict):
        if code == 'linux_helper_unavailable':
            proxy_error_invoker.disable_proxy_features.emit(
                'Linux Polkit approval was denied or the proxy helper could not start'
            )
            return
        if code not in ('port_bind_failed', 'hosts_write_exhausted', 'macos_ca_patch_failed'):
            return
        proxy_error_invoker.show_proxy_error.emit(code, dict(details))

    # Initialize proxy master
    proxy_master = ProxyMaster(config_manager, on_proxy_start_error=_on_proxy_start_error)

    # Initialize modification manager (pass cache_scraper for asset-id resolution)
    mod_manager = ModificationManager(
        cache_scraper=getattr(proxy_master, 'cache_scraper', None)
    )

    # Re-apply saved modifications on launch so the GUI state and Roblox files stay in sync.
    run_in_thread(mod_manager.reapply_all)()

    # ── Shutdown guards ───────────────────────────────────────────────────
    # 1. Graceful Windows shutdown / log-off: Qt fires commitDataRequest before
    #    the session ends, giving us a chance to clean up the hosts file.
    def _on_commit_data(_session):
        proxy_master.stop()
        mod_manager.restore_all()
    app.commitDataRequest.connect(_on_commit_data)

    # 2. Normal Python exit (sys.exit, end of main): last-resort fallback so
    #    the hosts file is cleaned up even if the tray Exit path was bypassed.
    atexit.register(proxy_master.stop)
    atexit.register(mod_manager.restore_all)

    # Start PreJsons download in background
    run_in_thread(download_prejsons)()

    # Check for updates in the background
    start_update_check()

    # Sync autostart on every launch (updates if launch method changed).
    # Only attempt when running elevated: the proxy needs hosts/port privileges.
    if config_manager.run_on_boot and (sys.platform == 'darwin' or sys.platform == 'win32') and (sys.platform == 'darwin' or _is_admin()):
        try:
            from .utils.autostart import sync_autostart
            if sys.platform != 'darwin' or not _is_admin():
                sync_autostart(True, CONFIG_DIR)
        except Exception:
            pass

    # Start proxy only if enabled and we have admin rights
    if start_proxy:
        proxy_master.start()
    elif not config_manager.proxy_features_enabled:
        log_buffer.log('Proxy', 'Proxy features disabled in settings: proxy not started')
    elif sys.platform == 'darwin':
        log_buffer.log('Proxy', 'Waiting for the macOS proxy helper before starting interception')
    else:
        log_buffer.log('Proxy', 'Read-only mode: proxy not started (no admin rights)')

    # Setup Roblox exit monitor for auto cache deletion (before tray to pass to it)
    roblox_monitor = RobloxExitMonitor(config_manager, proxy_master, mod_manager)

    # Create system tray
    tray = SystemTray(app, config_manager, proxy_master, mod_manager, roblox_monitor)
    tray_ref['tray'] = tray
    app.aboutToQuit.connect(tray.cleanup_tray_icon)
    single_instance_control_server = _start_single_instance_control_server(app, tray)
    log_buffer.log('App', f'Persistent log file: {LOG_FILE}')
    _admin_prompt_shown = False

    def _request_admin_once():
        nonlocal _admin_prompt_shown
        if _admin_prompt_shown or _is_admin():
            return
        _admin_prompt_shown = True

        gate = QDialog(None)
        gate.setModal(True)
        gate.setWindowTitle('Fleasion - Administrator Permission Required')
        gate.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        gate_layout = QVBoxLayout(gate)
        if sys.platform == 'darwin':
            gate_text = (
                'Fleasion is waiting for macOS administrator permission.\n\n'
                'Approve the helper install prompt so the normal-user app can use proxy features.'
            )
        else:
            gate_text = (
                'Fleasion is waiting for Windows administrator permission.\n\n'
                'If the UAC prompt is flashing on the taskbar, click it and choose Yes or No.'
            )
        gate_label = QLabel(gate_text)
        gate_label.setWordWrap(True)
        gate_layout.addWidget(gate_label)
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
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

    if _admin_prompt_needed:
        _request_admin_once()

    def _install_macos_helper_and_start_proxy():
        if sys.platform != 'darwin' or not config_manager.proxy_features_enabled or proxy_master.is_running:
            return

        from .utils.macos_proxy_helper import helper_is_ready, install_helper

        if helper_is_ready():
            proxy_master.start()
            return
        if _suppress_dashboard:
            log_buffer.log('ProxyHelper', 'Autostart launch skipped helper installation prompt; open Fleasion normally to install it')
            return

        prompt = QMessageBox(_visible_parent_widget())
        prompt.setWindowTitle('Fleasion - Install Proxy Helper')
        prompt.setIcon(QMessageBox.Icon.Information)
        prompt.setText('Install the Fleasion macOS proxy helper?')
        prompt.setInformativeText(
            'macOS requires a small root service to own local port 443, update /etc/hosts, '
            "and patch Roblox's SSL trust bundle.\n\n"
            'This requires one administrator approval now. Fleasion itself will keep running as your normal user, '
            'and future launches and Run on Boot will not ask for an administrator password.'
        )
        install_button = prompt.addButton('Install Helper', QMessageBox.ButtonRole.AcceptRole)
        cancel_button = prompt.addButton('Not Now', QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(install_button)
        prompt.exec()
        if prompt.clickedButton() == cancel_button:
            log_buffer.log('ProxyHelper', 'macOS proxy helper installation postponed')
            return

        ok, detail = install_helper()
        if ok:
            proxy_master.start()
            return

        log_buffer.log('ProxyHelper', f'macOS proxy helper installation failed: {detail}')
        QMessageBox.warning(
            _visible_parent_widget(),
            'Fleasion - Proxy Helper Installation Failed',
            f'Fleasion could not install or start the macOS proxy helper.\n\n{detail}',
        )

    if sys.platform == 'darwin' and config_manager.proxy_features_enabled and not start_proxy:
        _install_macos_helper_and_start_proxy()

    # Warn if no Roblox installations can be found (same scan used for cert injection)
    from .proxy.master import _find_roblox_dirs as _scan_roblox_dirs
    if not _scan_roblox_dirs():
        _top = QApplication.topLevelWidgets()
        _parent = next((w for w in _top if w.isVisible()), None)
        _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)
        _no_roblox_msg = QMessageBox(_parent)
        if _on_top:
            _no_roblox_msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        _no_roblox_msg.setWindowTitle('Fleasion — Roblox Not Found')
        _no_roblox_msg.setIcon(QMessageBox.Icon.Warning)
        _no_roblox_msg.setText('Roblox does not appear to be installed.')
        _no_roblox_msg.setInformativeText(
            'Fleasion could not find any Roblox installations on this computer.\n\n'
            'If this is incorrect, click OK and launch Roblox, Fleasion will attempt to detect it.\n\n'
            'Please close Fleasion, install Roblox, and then relaunch Fleasion.\n\n'
            'Without Roblox installed, the majority of Fleasion\'s features cannot be used.\n\n'
            'Note: To fully close Fleasion, right click Fleasion in the system tray and click Exit.'
        )
        _no_roblox_msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
            _no_roblox_msg.setWindowIcon(QIcon(str(icon_path)))
        _no_roblox_msg.exec()

    # Setup periodic status update
    status_timer = QTimer()
    status_timer.timeout.connect(tray.update_status)
    status_timer.start(1000)  # Update every second

    # Setup Roblox check timer
    roblox_check_timer = QTimer()
    roblox_check_timer.timeout.connect(roblox_monitor.check_roblox_status)
    roblox_check_timer.start(500)  # Check every 0.5 seconds

    # Show first-time setup guide if this is the first run.
    if not _suppress_dashboard and not config_manager.first_time_setup_complete:
        _top = QApplication.topLevelWidgets()
        _parent = next((w for w in _top if w.isVisible()), None)
        _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)
        welcome_box = _FirstTimeSetupMessageBox(_parent)
        if _on_top:
            welcome_box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        welcome_box.setWindowTitle('Welcome to Fleasion')
        welcome_box.setText(
            'Welcome to Fleasion!\n\n'
            'Quick setup guide:\n\n'
            '1. Use the Replacer tab to manage asset replacements. At the bottom, add IDs you want '
            'to replace, then choose a replacement ID, URL, or local file.\n\n'
            '2. To find asset IDs, click "Scraped games..." in the Replacer tab. Click an asset ID '
            'to add it to Asset IDs, or click Replacement ID to use that asset as the replacement.\n\n'
            '3. Click "Add new", then enable your config at the top next to "Enabled". For most '
            'users this means turning on "Default".\n\n'
            '4. Click "Clear Cache" before joining a game so Roblox downloads the '
            'assets through Fleasion instead of using cached originals.\n\n'
            '5. For assets that are not in Scraped games, open the Scraper tab, enable the cache '
            'scraper, clear cache, and join the Roblox game. Every loaded asset will appear there. '
            'Right-click assets to download them, Replace them, or Replace With them.\n\n'
            'Other tabs are optional tools and settings and are self explanatory.\n\n'
            'Fleasion is client-sided: only you see your changes. Roblox cannot ban you for local '
            'asset replacement, and game developers cannot meaningfully detect it. Game moderators '
            'can still ban users for any reason, so use your own judgment.'
        )
        welcome_box.setIcon(QMessageBox.Icon.Information)
        welcome_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        ok_button = welcome_box.button(QMessageBox.StandardButton.Ok)
        wait_seconds = 15
        remaining_seconds = wait_seconds
        if ok_button is not None:
            ok_button.setEnabled(False)
            ok_button.setText(f'OK ({remaining_seconds}s)')

            countdown_timer = QTimer(welcome_box)
            countdown_timer.setInterval(1000)

            def _update_welcome_countdown():
                nonlocal remaining_seconds
                remaining_seconds -= 1
                if remaining_seconds <= 0:
                    countdown_timer.stop()
                    welcome_box.allow_accept()
                    ok_button.setText('OK')
                    ok_button.setEnabled(True)
                else:
                    ok_button.setText(f'OK ({remaining_seconds}s)')

            countdown_timer.timeout.connect(_update_welcome_countdown)
            countdown_timer.start()
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
            welcome_box.setWindowIcon(QIcon(str(icon_path)))
        welcome_box.exec()
        config_manager.first_time_setup_complete = True
        tray._show_replacer_config()
    elif not _suppress_dashboard and config_manager.open_dashboard_on_launch:
        # Open dashboard on launch if enabled (suppressed when started by autostart task)
        tray._show_replacer_config()

    _auth_prompt_shown = False
    auth_check_invoker = _AuthCheckInvoker()

    def _handle_auth_check_complete(found: bool, details: dict):
        nonlocal _auth_prompt_shown
        if found or _auth_prompt_shown:
            return
        _auth_prompt_shown = True
        if sys.platform == 'darwin':
            try:
                from .utils.roblox_auth import get_auth_failure_details, get_roblosecurity, notify_auth_source_changed

                if config_manager.macos_auth_source:
                    log_buffer.log(
                        'Auth',
                        f'Configured Roblox login source {config_manager.macos_auth_source} did not produce a valid token; reopening browser picker',
                    )
                    config_manager.macos_auth_source = ''
                    notify_auth_source_changed()
                    choice_result = _choose_macos_auth_source_on_launch(config_manager, tray, force=True)
                    if choice_result in {'selected', 'already-configured'}:
                        retry_cookie = get_roblosecurity(include_keychain_browsers=True)
                        if retry_cookie:
                            return
                        details = get_auth_failure_details()
                    elif choice_result == 'skipped':
                        details = dict(details)
                        details['user_skipped_token'] = True
            except Exception as exc:
                log_buffer.log('Auth', f'Unexpected error while retrying macOS auth picker: {type(exc).__name__}: {exc}')
        _show_auth_cookie_unavailable_dialog(details)

    auth_check_invoker.completed.connect(_handle_auth_check_complete)
    initial_auth_choice = _choose_macos_auth_source_on_launch(config_manager, tray)
    if initial_auth_choice == 'skipped':
        _auth_prompt_shown = True
        try:
            from .utils.roblox_auth import get_auth_failure_details, get_roblosecurity

            get_roblosecurity(include_keychain_browsers=False)
            skip_details = get_auth_failure_details()
        except Exception as exc:
            log_buffer.log('Auth', f'Unexpected error while preparing token-skip warning: {type(exc).__name__}: {exc}')
            skip_details = {}
        skip_details = dict(skip_details)
        skip_details['user_skipped_token'] = True
        _show_auth_cookie_unavailable_dialog(skip_details)

    def _check_auth_cookie_once():
        try:
            from .utils.roblox_auth import get_auth_failure_details, get_roblosecurity

            if sys.platform == 'darwin':
                log_buffer.log('Auth', 'Running startup Roblox login discovery')
            cookie = get_roblosecurity(include_keychain_browsers=sys.platform == 'darwin' or sys.platform.startswith('linux'))
            details = get_auth_failure_details()
        except Exception as exc:
            log_buffer.log('Auth', f'Unexpected error during startup auth check: {type(exc).__name__}: {exc}')
            return
        auth_check_invoker.completed.emit(bool(cookie), details)

    QTimer.singleShot(1500, run_in_thread(_check_auth_cookie_once))

    # Run application
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
