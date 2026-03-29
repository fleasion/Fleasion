"""Application entrypoint."""

import atexit
import platform
import sys
import time

from PyQt6.QtCore import Qt, QTimer, QSharedMemory, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QDialog, QVBoxLayout, QHBoxLayout, QLabel

from .config import ConfigManager
from .modifications import ModificationManager
from .prejsons import download_prejsons
from .proxy import ProxyMaster, check_and_patch_running_roblox_ca
from .tray import SystemTray
from .utils import delete_cache, get_icon_path, get_roblox_player_exe_path, is_roblox_running, is_studio_running, log_buffer, run_in_thread, start_update_check, CONFIG_DIR



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


def _relaunch_as_admin(extra_args: str = '') -> bool:
    """Silently attempt to relaunch elevated via UAC.

    Shows only the standard Windows UAC prompt (no extra dialog).
    Returns True if the elevated process was spawned (caller should exit).
    Returns False if the user declined or the relaunch failed.
    """
    import ctypes

    if getattr(sys, 'frozen', False):
        # Compiled .exe — sys.executable is the .exe itself
        exe = sys.executable
        existing = ' '.join(f'"{a}"' for a in sys.argv[1:]) if len(sys.argv) > 1 else ''
        combined = (existing + (' ' + extra_args.strip() if extra_args.strip() else '')).strip()
        params = combined if combined else None
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
            params = (f'--project "{cwd}" run fleasion ' + extra_args.strip()).strip()
        else:
            # Fallback: plain interpreter (may fail if venv is not activated,
            # but it's the best we can do without uv)
            exe = sys.executable
            combined = (' '.join(f'"{a}"' for a in sys.argv) + (' ' + extra_args.strip() if extra_args.strip() else '')).strip()
            params = combined if combined else None

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


def _attempt_silent_elevation(extra_args: str = '') -> bool:
    """Try to elevate silently on startup.

    If already admin, returns True immediately.
    Otherwise fires the UAC prompt. If the user accepts, the elevated
    copy launches and this function calls sys.exit(0) to close the
    non-elevated instance.  If the user declines, returns False so the
    caller continues in read-only mode — no extra dialog shown.
    """
    if _is_admin():
        return True

    success = _relaunch_as_admin(extra_args=extra_args)
    if success:
        # Elevated copy is now starting up — close this instance silently
        sys.exit(0)

    # User clicked "No" on UAC — stay open in read-only mode
    return False


class RobloxExitMonitor(QObject):
    """Monitors Roblox process and triggers cache deletion on exit."""

    _studio_detected = pyqtSignal()
    player_status_changed = pyqtSignal(bool)  # Emitted when RobloxPlayerBeta opens/closes (True = running)

    def __init__(self, config_manager):
        super().__init__()
        self.config_manager = config_manager
        self.was_running = False
        self._player_was_running = False
        self._studio_was_running = False
        self._studio_notified = False
        self._studio_suppress_session = False
        self._studio_detected.connect(self._on_studio_detected)

    @run_in_thread
    def check_roblox_status(self):
        """Check if Roblox has exited and trigger cache deletion if needed."""
        is_running = is_roblox_running()

        # --- Roblox Player: player status changed signal ---
        if self._player_was_running != is_running:
            self.player_status_changed.emit(is_running)

        # --- Roblox Player: launch detection - check CA cert on new launch ---
        if not self._player_was_running and is_running:
            exe_path = get_roblox_player_exe_path()
            if exe_path is None:
                # Process may still be initializing — retry for up to 10 s
                for _ in range(10):
                    time.sleep(1.0)
                    exe_path = get_roblox_player_exe_path()
                    if exe_path is not None:
                        break
            if exe_path is not None:
                run_in_thread(check_and_patch_running_roblox_ca)(exe_path)
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


def _other_fleasion_pids() -> list:
    """Return PIDs of other Fleasion processes (excludes current process and its parent)."""
    import json
    import os
    import subprocess

    current_pid = os.getpid()
    parent_pid = os.getppid()
    safe_pids = {current_pid, parent_pid}
    exe_name = os.path.basename(sys.executable)
    pids = []

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



def kill_other_fleasion_instances():
    """Kill all other Fleasion instances except the current process."""
    import subprocess

    for pid in _other_fleasion_pids():
        try:
            subprocess.run(
                ['taskkill', '/F', '/PID', str(pid)],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except Exception:
            pass


def main():
    """Main application entry point."""
    import argparse as _ap
    _parser = _ap.ArgumentParser(add_help=False)
    _parser.add_argument('--no-dashboard', action='store_true',
                         help='Suppress dashboard on launch (used by autostart task)')
    _parser.add_argument('--kill-others', action='store_true',
                         help='Kill other Fleasion instances on startup (used when relaunching elevated)')
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
        # If launched with --kill-others, kill before clearing stale memory so
        # the shared memory slot is freed by the time we try to claim it.
        if _args.kill_others:
            kill_other_fleasion_instances()
            import time as _time
            _time.sleep(0.3)
        _stale = QSharedMemory('FleasionSingleInstance')
        if _stale.attach():
            _stale.detach()

    shared_memory = QSharedMemory('FleasionSingleInstance')
    if not shared_memory.create(1):
        if shared_memory.error() == QSharedMemory.SharedMemoryError.AlreadyExists:
            # Another instance is already running.
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

            if _is_admin():
                # Already elevated — can kill any process directly.
                kill_others_button = msg_box.addButton('Kill Others', QMessageBox.ButtonRole.AcceptRole)
                _kill_requires_elevation = False
            else:
                # Not admin — taskkill on an elevated process silently fails.
                # A single "Elevate & Kill Others" relaunches as admin with
                # --kill-others so the elevated copy handles it automatically.
                kill_others_button = msg_box.addButton('Elevate && Kill Others', QMessageBox.ButtonRole.AcceptRole)
                _kill_requires_elevation = True

            run_anyway_button = msg_box.addButton('Run Anyway', QMessageBox.ButtonRole.AcceptRole)
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

    # Initialize modification manager (pass cache_scraper for asset-id resolution)
    mod_manager = ModificationManager(
        cache_scraper=getattr(proxy_master, 'cache_scraper', None)
    )

    # Crash recovery: if a previous session left a stash, re-apply
    if (CONFIG_DIR / 'ModOriginals').exists() and any((CONFIG_DIR / 'ModOriginals').iterdir()):
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

    # Sync autostart task on every launch (updates if launch method changed)
    if config_manager.run_on_boot:
        try:
            from .utils.autostart import sync_autostart
            sync_autostart(True, CONFIG_DIR)
        except Exception:
            pass

    # Start proxy only if we have admin rights
    if start_proxy:
        proxy_master.start()
    else:
        log_buffer.log('Proxy', 'Read-only mode: proxy not started (no admin rights)')

    # Setup Roblox exit monitor for auto cache deletion (before tray to pass to it)
    roblox_monitor = RobloxExitMonitor(config_manager)

    # Create system tray
    tray = SystemTray(app, config_manager, proxy_master, mod_manager, roblox_monitor)
    if _show_readonly_notice:
        def _show_readonly_dialog():
            from PyQt6.QtWidgets import QMessageBox, QApplication
            from PyQt6.QtGui import QIcon
            _top = QApplication.topLevelWidgets()
            _parent = next((w for w in _top if w.isVisible()), None)
            _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)
            msg = QMessageBox(_parent)
            if _on_top:
                msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
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

    # Setup Roblox check timer
    roblox_check_timer = QTimer()
    roblox_check_timer.timeout.connect(roblox_monitor.check_roblox_status)
    roblox_check_timer.start(500)  # Check every 0.5 seconds

    # Show first-time message if this is the first run
    if not _suppress_dashboard and not config_manager.first_time_setup_complete:
        _top = QApplication.topLevelWidgets()
        _parent = next((w for w in _top if w.isVisible()), None)
        _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)
        welcome_box = QMessageBox(_parent)
        if _on_top:
            welcome_box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
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
