"""Runtime-neutral startup migrations shared by the legacy and QML frontends."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config.manager import ConfigManager
    from .modifications import ModificationManager


def prepare_env_proxy_migration(config: ConfigManager) -> bool:
    """Persist Env Proxy for pre-migration installs and report whether acknowledgement is due."""
    if config.env_proxy_migration_v1_complete:
        return False

    # Persist the new default before any proxy service is constructed. Existing
    # users get an acknowledgement; first-run users learn about the mode in setup.
    config.proxy_mode = 'env'
    return bool(config.first_time_setup_complete)


def restore_read_only_guard_state(
    config: ConfigManager,
    modification_manager: ModificationManager,
) -> None:
    """Restore files left read-only by old builds before normal startup work begins."""
    if config.lock_roblox_files_read_only:
        return

    if not config.read_only_lock_migration_v1_complete:
        legacy_paths = (
            roblox_dir / 'ssl' / 'cacert.pem' for roblox_dir in modification_manager.roblox_dirs
        )
        modification_manager.clear_managed_file_read_only(legacy_paths, clear_untracked=True)
        config.read_only_lock_migration_v1_complete = True
        return

    modification_manager.clear_managed_file_read_only(clear_untracked=False)
