"""Qt graphics policy, native dependencies, and console signal handling."""

from __future__ import annotations

import os
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
)

from fleasion.localization import tr, verbatim
from fleasion.utils import (
    APP_NAME,
    log_buffer,
)

if sys.platform.startswith('linux'):
    from fleasion.utils import platform_linux
else:
    platform_linux = None


def configure_opengl_for_legacy_viewers() -> None:
    """Set only platform policy needed by future OpenGL preview widgets."""
    if sys.platform.startswith('linux'):
        os.environ.setdefault('QT_OPENGL', 'desktop')


def linux_gui_dependency_packages() -> list[str]:
    """Probe native packages without touching Qt widgets."""
    if not sys.platform.startswith('linux') or platform_linux is None:
        return []
    return platform_linux.missing_linux_gui_packages()


def check_linux_gui_dependencies() -> bool:
    """Check and report native dependencies for synchronous callers."""
    return report_linux_gui_dependencies(linux_gui_dependency_packages())


def report_linux_gui_dependencies(missing: list[str]) -> bool:
    """Present a completed background probe on the GUI thread."""
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


def install_gui_sigint_handler(app: QApplication) -> QTimer:
    """Exit the Qt event loop cleanly when the console receives Ctrl+C."""

    def _handle_sigint(signum: int, _frame: object) -> None:
        app.exit(128 + signum)

    def _poll_python_signals() -> None:
        # Enter Python periodically while Qt is otherwise idle so CPython can
        # dispatch pending console signals instead of injecting KeyboardInterrupt
        # into an arbitrary Qt callback
        return None

    signal.signal(signal.SIGINT, _handle_sigint)
    timer = QTimer(app)
    timer.setInterval(200)
    timer.timeout.connect(_poll_python_signals)
    timer.start()
    return timer
