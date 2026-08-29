"""Platform-aware Roblox target launching shared by QML workflows."""

from __future__ import annotations

import sys
from typing import Any

from ..utils.windows import launch_as_standard_user


def launch_roblox_target(proxy_master: Any | None, target: str) -> bool:
    """Launch a Roblox URI through the configured environment proxy when needed."""
    if sys.platform.startswith('linux'):
        # ProxyMaster arms the selected Flatpak environment while Env Proxy is active,
        # so preserve the one-time Roblox URI instead of synthesizing a relaunch.
        return launch_as_standard_user(target)
    if proxy_master is not None and sys.platform == 'darwin':
        config = getattr(proxy_master, 'config_manager', None)
        if getattr(config, 'proxy_mode', '') == 'env' and bool(
            getattr(config, 'proxy_features_enabled', False)
        ):
            from ..utils.platform_macos import relaunch_roblox_with_proxy_env

            return relaunch_roblox_with_proxy_env(proxy_master.roblox_env_proxy_url(), target)
    return launch_as_standard_user(target)


__all__ = ['launch_roblox_target']
