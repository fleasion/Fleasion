"""Utilities package."""

from __future__ import annotations

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


def start_update_check() -> None:  # ruff: ignore[non-empty-init-module]
    from .updater import (  # ruff: ignore[import-outside-top-level]
        start_update_check as _start_update_check,
    )

    return _start_update_check()


__all__ = [
    'APP_AUTHOR',
    'APP_CACHE_DIR',
    'APP_CONCEPT',
    'APP_DISCORD',
    'APP_LOGIC',
    'APP_NAME',
    'APP_REPO',
    'APP_VERSION',
    'CLOG_URL',
    'CONFIGS_FOLDER',
    'CONFIG_DIR',
    'CONFIG_FILE',
    'ICON_FILENAME',
    'LOCAL_APPDATA',
    'LOGS_DIR',
    'LOG_FILE',
    'MACOS_PROXY_BACKEND_PORT',
    'MACOS_PROXY_HELPER_CONTROL_PORT',
    'MODIFICATIONS_JSON',
    'MOD_CACHE_DIR',
    'MOD_ORIGINALS_DIR',
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
    'delete_cache',
    'format_count',
    'get_icon_path',
    'get_roblox_player_exe_path',
    'get_roblox_process_identity',
    'get_roblox_studio_exe_path',
    'is_roblox_running',
    'is_studio_running',
    'launch_as_standard_user',
    'log_buffer',
    'open_folder',
    'pluralize',
    'run_in_thread',
    'show_message_box',
    'start_update_check',
    'terminate_roblox',
    'time_tracker',
    'wait_for_roblox_exit',
    'wait_for_roblox_window',
]
