"""Startup dialogs and main-thread notification bridges."""

from __future__ import annotations

from fleasion.app.dialogs.auth import schedule_startup_auth_check
from fleasion.app.dialogs.common import (
    show_admin_required_dialog,
    visible_parent_widget,
    window_handle,
)
from fleasion.app.dialogs.hosts import show_env_proxy_stale_hosts_dialog
from fleasion.app.dialogs.permissions import show_roblox_permission_failure
from fleasion.app.dialogs.proxy import (
    ProxyErrorInvoker,
    disable_proxy_features_after_start_failure,
    manual_upstream_credentials_missing,
)
from fleasion.app.dialogs.startup import (
    complete_first_time_setup,
    prepare_env_proxy_migration,
    prompt_first_time_language,
    should_sync_autostart_on_launch,
    show_desktop_integration_failure,
    show_env_proxy_migration,
    show_run_on_boot_failure,
)

__all__ = [
    'ProxyErrorInvoker',
    'complete_first_time_setup',
    'disable_proxy_features_after_start_failure',
    'manual_upstream_credentials_missing',
    'prepare_env_proxy_migration',
    'prompt_first_time_language',
    'schedule_startup_auth_check',
    'should_sync_autostart_on_launch',
    'show_admin_required_dialog',
    'show_desktop_integration_failure',
    'show_env_proxy_migration',
    'show_env_proxy_stale_hosts_dialog',
    'show_roblox_permission_failure',
    'show_run_on_boot_failure',
    'visible_parent_widget',
    'window_handle',
]
