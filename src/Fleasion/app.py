"""Application entrypoint."""

import platform
import sys

from PyQt6.QtCore import QTimer, QSharedMemory, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QDialog, QVBoxLayout, QHBoxLayout, QLabel

from .config import ConfigManager
from .prejsons import download_prejsons
from .proxy import ProxyMaster
from .tray import SystemTray
from .utils import delete_cache, get_icon_path, is_roblox_running, is_studio_running, log_buffer, run_in_thread, start_update_check



# ---------------------------------------------------------------------------
# UAC / elevation helpers
# ---------------------------------------------------------------------------

def _is_admin() -> bool:
    """Return True if the current process has administrator privileges."""
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_as_admin() -> bool:
    """Silently attempt to relaunch elevated via UAC.

    Shows only the standard Windows UAC prompt (no extra dialog).
    Returns True if the elevated process was spawned (caller should exit).
    Returns False if the user declined or the relaunch failed.
    """
    import ctypes

    if getattr(sys, 'frozen', False):
        # Compiled .exe — sys.executable is the .exe itself
        exe = sys.executable
        params = ' '.join(f'"{a}"' for a in sys.argv[1:]) if len(sys.argv) > 1 else None
    else:
        # Dev / uv run — locate the uv executable and replay the original
        # invocation through it.  Running the Python interpreter directly in
        # the elevated process would miss the uv-managed virtualenv entirely,
        # causing import failures and a silent crash.
        import shutil, os
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
            params = f'--project "{cwd}" run fleasion'
        else:
            # Fallback: plain interpreter (may fail if venv is not activated,
            # but it's the best we can do without uv)
            exe = sys.executable
            params = ' '.join(f'"{a}"' for a in sys.argv)

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
    sei.hwnd         = None
    sei.lpVerb       = 'runas'
    sei.lpFile       = exe
    sei.lpParameters = params
    sei.lpDirectory  = None
    # SW_HIDE (0) for dev/uv mode: hides the uv.exe console wrapper.
    # SW_SHOWNORMAL (1) for compiled .exe: the exe IS the app, we need windows to show.
    sei.nShow        = 0 if not getattr(sys, 'frozen', False) else 1
    sei.hInstApp     = None

    ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
    return bool(ok)


def _attempt_silent_elevation() -> bool:
    """Try to elevate silently on startup.

    If already admin, returns True immediately.
    Otherwise fires the UAC prompt. If the user accepts, the elevated
    copy launches and this function calls sys.exit(0) to close the
    non-elevated instance.  If the user declines, returns False so the
    caller continues in read-only mode — no extra dialog shown.
    """
    if _is_admin():
        return True

    success = _relaunch_as_admin()
    if success:
        # Elevated copy is now starting up — close this instance silently
        sys.exit(0)

    # User clicked "No" on UAC — stay open in read-only mode
    return False


class RobloxExitMonitor(QObject):
    """Monitors Roblox process and triggers cache deletion on exit."""

    _studio_detected = pyqtSignal()

    def __init__(self, config_manager):
        super().__init__()
        self.config_manager = config_manager
        self.was_running = False
        self._studio_was_running = False
        self._studio_notified = False
        self._studio_suppress_session = False
        self._studio_detected.connect(self._on_studio_detected)

    @run_in_thread
    def check_roblox_status(self):
        """Check if Roblox has exited and trigger cache deletion if needed."""
        # --- Roblox Player: auto cache deletion on exit ---
        if self.config_manager.auto_delete_cache_on_exit:
            is_running = is_roblox_running()
            if self.was_running and not is_running:
                log_buffer.log('Cache', 'Roblox exited, deleting cache...')
                run_in_thread(self._delete_cache_background)()
            self.was_running = is_running
        else:
            self.was_running = False

        # --- Roblox Studio: warn that scraping/modification is paused ---
        studio_running = is_studio_running()

        if not self._studio_was_running and studio_running:
            # Studio just opened
            if not self._studio_suppress_session and not self._studio_notified:
                self._studio_notified = True
                self._studio_detected.emit()

        if self._studio_was_running and not studio_running:
            # Studio just closed — reset so the warning shows again next time
            self._studio_notified = False

        self._studio_was_running = studio_running

    def _on_studio_detected(self):
        """Show the Roblox Studio warning dialog (called on the main thread via signal)."""
        dialog = QDialog()
        dialog.setWindowTitle('Fleasion — Roblox Studio Detected')

        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        label = QLabel(
            'Roblox Studio is currently open.\n\n'
            'No asset modification or scraping will occur while '
            'Roblox Studio is running. Close Roblox Studio to resume normal operation.'
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


def kill_other_fleasion_instances():
    """Kill all other Fleasion instances except the current process."""
    import json
    import os
    import subprocess

    current_pid = os.getpid()
    parent_pid = os.getppid()
    safe_pids = {current_pid, parent_pid}
    exe_name = os.path.basename(sys.executable)

    try:
        if exe_name.lower() not in ('python.exe', 'python3.exe'):
            # Compiled executable — find all processes with same image name and kill others
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
                            subprocess.run(
                                ['taskkill', '/F', '/PID', str(pid)],
                                capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW
                            )
                    except (ValueError, IndexError):
                        pass
        else:
            # Dev mode — use PowerShell CimInstance to get command lines and identify Fleasion processes.
            # We exclude both the current PID and its parent PID: under debugpy the parent is the
            # launcher process that also has 'launcher.py' in its command line, and killing it would
            # cascade-terminate the current process.
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
                        subprocess.run(
                            ['taskkill', '/F', '/PID', str(pid)],
                            capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW
                        )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
    except Exception:
        pass


def main():
    """Main application entry point."""
    import argparse as _ap
    _parser = _ap.ArgumentParser(add_help=False)
    _parser.add_argument('--no-dashboard', action='store_true',
                         help='Suppress dashboard on launch (used by autostart task)')
    _args, _ = _parser.parse_known_args()
    _suppress_dashboard = _args.no_dashboard

    # Check if running on Windows
    if platform.system() != 'Windows':
        app = QApplication(sys.argv)
        QMessageBox.critical(
            None,
            'Unsupported Operating System',
            'Fleasion only supports Windows.\n\nThis application will now exit.',
            QMessageBox.StandardButton.Ok
        )
        sys.exit(1)

    # Create Qt application
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Single instance check.
    # When we've just been relaunched via UAC elevation, the non-elevated
    # instance may not have fully exited yet, leaving stale shared memory.
    # If we're admin, forcibly attach-and-detach to clear it so the
    # elevated instance can take over cleanly.
    if _is_admin():
        _stale = QSharedMemory('FleasionSingleInstance')
        if _stale.attach():
            _stale.detach()

    shared_memory = QSharedMemory('FleasionSingleInstance')
    if not shared_memory.create(1):
        if shared_memory.error() == QSharedMemory.SharedMemoryError.AlreadyExists:
            # Another instance is already running
            msg_box = QMessageBox()
            msg_box.setWindowTitle('Fleasion Already Running')
            msg_box.setText('Another instance of Fleasion is already running (Check your system tray).')
            msg_box.setInformativeText('Do you want to run another instance anyway?')
            msg_box.setIcon(QMessageBox.Icon.Warning)
            
            # Set icon if available
            if icon_path := get_icon_path():
                from PyQt6.QtGui import QIcon
                msg_box.setWindowIcon(QIcon(str(icon_path)))

            kill_others_button = msg_box.addButton('Kill Others', QMessageBox.ButtonRole.AcceptRole)
            run_anyway_button = msg_box.addButton('Run Anyway', QMessageBox.ButtonRole.AcceptRole)
            cancel_button = msg_box.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(cancel_button)

            msg_box.exec()

            if msg_box.clickedButton() == cancel_button:
                sys.exit(0)

            if msg_box.clickedButton() == kill_others_button:
                kill_other_fleasion_instances()

            # If "Run Anyway" or "Kill Others" is clicked, we proceed.
            # Note: shared_memory object will be garbage collected or go out of scope,
            # but since we didn't successfully create it, we don't hold the lock.

    # Silently attempt UAC elevation. Shows only the standard Windows UAC prompt.
    # If the user accepts, this instance exits and the elevated copy takes over.
    # If declined, we stay open in read-only mode with no extra dialogs.
    start_proxy = _attempt_silent_elevation()
    if not start_proxy and not _is_admin():
        # Schedule a tray notification once the tray is ready (deferred so tray exists)
        _show_readonly_notice = True
    else:
        _show_readonly_notice = False

    # Initialize config manager
    config_manager = ConfigManager()

    # Initialize proxy master
    proxy_master = ProxyMaster(config_manager)

    # Start PreJsons download in background
    run_in_thread(download_prejsons)()

    # Check for updates in the background
    start_update_check()

    # Sync autostart task on every launch (updates if launch method changed)
    if config_manager.run_on_boot:
        try:
            from .utils.autostart import sync_autostart
            from .utils import CONFIG_DIR
            sync_autostart(True, CONFIG_DIR)
        except Exception:
            pass

    # Start proxy only if we have admin rights
    if start_proxy:
        proxy_master.start()
    else:
        log_buffer.log('Proxy', 'Read-only mode: proxy not started (no admin rights)')

    # Create system tray
    tray = SystemTray(app, config_manager, proxy_master)
    if _show_readonly_notice:
        def _show_readonly_dialog():
            from PyQt6.QtWidgets import QMessageBox
            from PyQt6.QtGui import QIcon
            msg = QMessageBox()
            msg.setWindowTitle('Fleasion — Read-Only Mode')
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setText('Administrator rights were not granted.')
            msg.setInformativeText(
                'Asset interception, scraping, and replacement will not work.\n\n'
                'Relaunch Fleasion as Administrator to enable the proxy.'
            )
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            if icon_path := get_icon_path():
                msg.setWindowIcon(QIcon(str(icon_path)))
            msg.exec()
        QTimer.singleShot(1500, _show_readonly_dialog)

    # Setup periodic status update
    status_timer = QTimer()
    status_timer.timeout.connect(tray.update_status)
    status_timer.start(1000)  # Update every second

    # Setup Roblox exit monitor for auto cache deletion
    roblox_monitor = RobloxExitMonitor(config_manager)
    roblox_check_timer = QTimer()
    roblox_check_timer.timeout.connect(roblox_monitor.check_roblox_status)
    roblox_check_timer.start(1000)  # Check every 1 second

    # Show first-time message if this is the first run
    if not _suppress_dashboard and not config_manager.first_time_setup_complete:
        welcome_box = QMessageBox()
        welcome_box.setWindowTitle('Welcome to Fleasion')
        welcome_box.setText(
            'Welcome to Fleasion!\n\n'
            'Fleasion runs in your system tray (bottom-right corner of your screen).\n'
            'Right-click the tray icon to access:\n'
            '• Dashboard - Configure asset replacements\n'
            '• Cache Viewer - Browse and export cached assets\n'
            '• Settings - Customize behavior\n\n'
            'IMPORTANT:\n'
            'After applying any changes in the dashboard, you must clear your Roblox cache '
            '(or restart Roblox) so assets get re-downloaded through the proxy.\n\n'
            'HOW IT WORKS:\n'
            'Fleasion uses a local proxy to intercept network traffic between Roblox and its servers. '
            'This allows you to modify assets (images, audio, etc.) before they reach your game.\n\n'
            'The dashboard will open now to get you started.'
        )
        welcome_box.setIcon(QMessageBox.Icon.Information)
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
            welcome_box.setWindowIcon(QIcon(str(icon_path)))
        welcome_box.exec()
        config_manager.first_time_setup_complete = True
        tray._show_replacer_config()
    elif not _suppress_dashboard and config_manager.open_dashboard_on_launch:
        # Open dashboard on launch if enabled (suppressed when started by autostart task)
        tray._show_replacer_config()

    # Run application
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
