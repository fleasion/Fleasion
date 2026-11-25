"""Single-instance state and the local command server."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QSharedMemory
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from fleasion.app.process_control import (
    SINGLE_INSTANCE_CONTROL_SERVER as _SINGLE_INSTANCE_CONTROL_SERVER,
    other_fleasion_pids as _other_fleasion_pids,
)
from fleasion.app.roblox_launch import launch_roblox_uri_for_instance
from fleasion.utils import (
    log_buffer,
    run_in_thread,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import (
        QApplication,
    )

    from fleasion.app.tray import SystemTray


SINGLE_INSTANCE_KEY = 'FleasionSingleInstance'


@dataclass(slots=True)
class SingleInstanceState:
    shared_memory: QSharedMemory | None = None
    control_server: QLocalServer | None = None
    app: QApplication | None = None
    tray: SystemTray | None = None


single_instance_state = SingleInstanceState()


def should_reclaim_stale_single_instance(
    error: QSharedMemory.SharedMemoryError,
) -> bool:
    """Return True when a stale Qt singleton marker can be safely reclaimed."""
    if error != QSharedMemory.SharedMemoryError.AlreadyExists:
        return False
    if not (sys.platform == 'darwin' or sys.platform.startswith('linux')):
        return False
    return not _other_fleasion_pids()


def handle_single_instance_command(socket: QLocalSocket, tray: SystemTray) -> None:
    try:
        command = bytes(socket.readAll().data()).decode('utf-8', errors='replace').strip()
    except RuntimeError:
        return
    if command.lower() == 'quit':
        tray.exit_app()
    elif command.lower() == 'quit-preserve-env-player':
        tray.exit_app(preserve_roblox=True)
    elif command.lower().startswith('launch-roblox\n'):
        target = command.split('\n', 1)[1].strip()
        if target.startswith(('roblox:', 'roblox-player:')):
            run_in_thread(launch_roblox_uri_for_instance)(tray, target)


def start_single_instance_control_server(
    app: QApplication, tray: SystemTray
) -> QLocalServer | None:
    """Start a local control endpoint for clean single-instance handoff."""
    server = QLocalServer(app)

    if not server.listen(_SINGLE_INSTANCE_CONTROL_SERVER):
        QLocalServer.removeServer(_SINGLE_INSTANCE_CONTROL_SERVER)
        if not server.listen(_SINGLE_INSTANCE_CONTROL_SERVER):
            log_buffer.log('App', 'Single-instance control server could not start')
            return None

    def _handle_connection() -> None:
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: handle_single_instance_command(s, tray))
            if socket.bytesAvailable() > 0:
                handle_single_instance_command(socket, tray)

    server.newConnection.connect(_handle_connection)
    return server
