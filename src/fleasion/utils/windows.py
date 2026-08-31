"""Compatibility wrapper for platform-specific desktop utilities.

Historically Fleasion imported process/cache/launch helpers from this module.
Keep that import path stable while dispatching to the current OS backend.
"""

from __future__ import annotations

import importlib
import sys

if sys.platform == 'win32':
    from .platform_windows import (
        delete_cache,
        get_roblox_player_exe_path,
        get_roblox_process_identity,
        get_roblox_studio_exe_path,
        is_roblox_running,
        is_studio_running,
        launch_as_standard_user,
        open_folder,
        show_message_box,
        terminate_roblox,
        wait_for_roblox_exit,
        wait_for_roblox_window,
    )

    _backend_name = '.platform_windows'
elif sys.platform == 'darwin':
    from .platform_macos import (
        delete_cache,
        get_roblox_player_exe_path,
        get_roblox_process_identity,
        get_roblox_studio_exe_path,
        is_roblox_running,
        is_studio_running,
        launch_as_standard_user,
        open_folder,
        show_message_box,
        terminate_roblox,
        wait_for_roblox_exit,
        wait_for_roblox_window,
    )

    _backend_name = '.platform_macos'
elif sys.platform.startswith('linux'):
    from .platform_linux import (
        delete_cache,
        get_roblox_player_exe_path,
        get_roblox_process_identity,
        get_roblox_studio_exe_path,
        is_roblox_running,
        is_studio_running,
        launch_as_standard_user,
        open_folder,
        show_message_box,
        terminate_roblox,
        wait_for_roblox_exit,
        wait_for_roblox_window,
    )

    _backend_name = '.platform_linux'
else:
    msg = 'Fleasion supports Windows, macOS, and Linux only.'
    raise RuntimeError(msg)

_backend = importlib.import_module(_backend_name, __package__)


__all__ = [
    'delete_cache',
    'get_roblox_player_exe_path',
    'get_roblox_process_identity',
    'get_roblox_studio_exe_path',
    'is_roblox_running',
    'is_studio_running',
    'launch_as_standard_user',
    'open_folder',
    'show_message_box',
    'terminate_roblox',
    'wait_for_roblox_exit',
    'wait_for_roblox_window',
]


def __getattr__(name: str) -> object:
    """Forward less-common compatibility attributes to the active platform backend."""
    return getattr(_backend, name)
