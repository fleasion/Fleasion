"""Application entrypoint."""

import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from .config import ConfigManager
from .prejsons import download_prejsons
from .proxy import ProxyMaster
from .tray import SystemTray
from .utils import run_in_thread


def main():
    """Main application entry point."""
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

    # Run application
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
