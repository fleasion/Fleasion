"""Application entrypoint."""

import platform
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from .config import ConfigManager
from .prejsons import download_prejsons
from .proxy import ProxyMaster
from .tray import SystemTray
from .utils import delete_cache, is_roblox_running, log_buffer, run_in_thread


class RobloxExitMonitor:
    """Monitors Roblox process and triggers cache deletion on exit."""

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.was_running = False

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

    # Initialize config manager
    config_manager = ConfigManager()

    # Initialize proxy master
    proxy_master = ProxyMaster(config_manager)

    # Start PreJsons download in background
    run_in_thread(download_prejsons)()

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
    roblox_check_timer.start(2000)  # Check every 2 seconds

    # Show first-time message if this is the first run
    if not config_manager.first_time_setup_complete:
        QMessageBox.information(
            None,
            'Welcome to Fleasion',
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
            'The dashboard will open now to get you started.',
            QMessageBox.StandardButton.Ok
        )
        config_manager.first_time_setup_complete = True
        tray._show_replacer_config()
    elif config_manager.open_dashboard_on_launch:
        # Open dashboard on launch if enabled
        tray._show_replacer_config()

    # Run application
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
