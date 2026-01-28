"""Application entrypoint."""

import platform
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from .config import ConfigManager
from .prejsons import download_prejsons
from .proxy import ProxyMaster
from .tray import SystemTray
from .utils import run_in_thread


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

    # Show first-time message if this is the first run
    if not config_manager.first_time_setup_complete:
        QMessageBox.information(
            None,
            'Welcome to Fleasion',
            'Welcome to Fleasion!\n\n'
            'Fleasion runs in your system tray (bottom-right corner of your screen).\n\n'
            'Right-click the tray icon to access all features including:\n'
            '• Dashboard - View statistics and manage settings\n'
            '• Cache Viewer - Browse and export cached assets\n'
            '• Replacer Config - Configure asset replacements\n\n'
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
