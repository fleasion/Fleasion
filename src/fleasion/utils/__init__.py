"""Utilities package."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

from .logging import LogBuffer, log_buffer
from .metadata import (
    APP_AUTHOR,
    APP_CONCEPT,
    APP_DISCORD,
    APP_LOGIC,
    APP_NAME,
    APP_REPO,
    APP_VERSION,
)
from .paths import (
    APP_CACHE_DIR,
    CLOG_URL,
    CONFIG_DIR,
    CONFIG_FILE,
    CONFIGS_FOLDER,
    ICON_FILENAME,
    LOCAL_APPDATA,
    LOG_FILE,
    LOGS_DIR,
    MACOS_PROXY_BACKEND_PORT,
    MACOS_PROXY_HELPER_CONTROL_PORT,
    MOD_CACHE_DIR,
    MOD_ORIGINALS_DIR,
    MODIFICATIONS_JSON,
    ORIGINALS_DIR,
    PREJSONS_DIR,
    PROXY_CA_DIR,
    PROXY_PORT,
    PROXY_TARGET_HOST,
    REPLACEMENTS_DIR,
    ROBLOX_PROCESS,
    ROBLOX_STUDIO_PROCESS,
    STORAGE_DB,
    STORAGE_DB_GDK,
    STRIPPABLE_ASSET_TYPES,
    USER_HOME,
    get_icon_path,
)
from .plural import format_count, pluralize
from .threading import run_in_thread
from .time_tracker import TimeTracker, time_tracker
from .windows import (
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


def start_update_check(  # ruff: ignore[non-empty-init-module]
    *args: object, **kwargs: object
) -> None:
    from .updater import (  # ruff: ignore[import-outside-top-level]
        start_update_check as _start_update_check,
    )

    compatible = cast('Callable[..., None]', _start_update_check)
    return compatible(*args, **kwargs)


__all__ = [  # ruff: ignore[unsorted-dunder-all]
    'APP_AUTHOR',
    'APP_LOGIC',
    'APP_CONCEPT',
    'APP_DISCORD',
    'APP_REPO',
    'APP_NAME',
    'APP_VERSION',
    'APP_CACHE_DIR',
    'CLOG_URL',
    'CONFIG_DIR',
    'CONFIG_FILE',
    'CONFIGS_FOLDER',
    'ICON_FILENAME',
    'LOCAL_APPDATA',
    'LOG_FILE',
    'LOGS_DIR',
    'MACOS_PROXY_BACKEND_PORT',
    'MACOS_PROXY_HELPER_CONTROL_PORT',
    'MOD_CACHE_DIR',
    'MOD_ORIGINALS_DIR',
    'MODIFICATIONS_JSON',
    'ORIGINALS_DIR',
    'PREJSONS_DIR',
    'PROXY_CA_DIR',
    'PROXY_PORT',
    'PROXY_TARGET_HOST',
    'REPLACEMENTS_DIR',
    'ROBLOX_PROCESS',
    'ROBLOX_STUDIO_PROCESS',
    'STORAGE_DB',
    'STORAGE_DB_GDK',
    'STRIPPABLE_ASSET_TYPES',
    'USER_HOME',
    'LogBuffer',
    'TimeTracker',
    'time_tracker',
    'delete_cache',
    'get_icon_path',
    'format_count',
    'get_roblox_player_exe_path',
    'get_roblox_process_identity',
    'get_roblox_studio_exe_path',
    'is_roblox_running',
    'is_studio_running',
    'log_buffer',
    'launch_as_standard_user',
    'open_folder',
    'pluralize',
    'run_in_thread',
    'start_update_check',
    'show_message_box',
    'terminate_roblox',
    'wait_for_roblox_exit',
    'wait_for_roblox_window',
]
