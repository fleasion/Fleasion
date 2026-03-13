"""Application entrypoint."""

import platform
import sys

from PyQt6.QtCore import QTimer, QSharedMemory
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton

from .config import ConfigManager
from .prejsons import download_prejsons
from .proxy import ProxyMaster
from .tray import SystemTray
from .utils import delete_cache, get_icon_path, is_roblox_running, log_buffer, run_in_thread, start_update_check


class RobloxExitMonitor:
    """Monitors Roblox process and triggers cache deletion on exit."""

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.was_running = False

    @run_in_thread
    def check_roblox_status(self):
        """Check if Roblox has exited and trigger cache deletion if needed."""
        if not self.config_manager.auto_delete_cache_on_exit:
            self.was_running = False
            return

        is_running = is_roblox_running()

        # Detect transition from running to not running
        if self.was_running and not is_running:
            log_buffer.log('Cache', 'Roblox exited, deleting cache...')
            # Run cache deletion in background thread
            run_in_thread(self._delete_cache_background)()

        self.was_running = is_running

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

    # Single instance check
    # We use a unique key for the shared memory
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

    # Initialize config manager
    config_manager = ConfigManager()

    # Initialize proxy master
    proxy_master = ProxyMaster(config_manager)

    # Start PreJsons download in background
    run_in_thread(download_prejsons)()

    # Check for updates in the background
    start_update_check()

    # Start proxy automatically
    proxy_master.start()

    # Create system tray
    tray = SystemTray(app, config_manager, proxy_master)

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
    if not config_manager.first_time_setup_complete:
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
    elif config_manager.open_dashboard_on_launch:
        # Open dashboard on launch if enabled
        tray._show_replacer_config()

    # Run application
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
