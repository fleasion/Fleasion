"""Platform-aware Roblox target launching shared by QML workflows."""

from __future__ import annotations

import sys
from typing import Any

from ..utils.windows import launch_as_standard_user


def launch_roblox_target(proxy_master: Any | None, target: str) -> bool:
    """Launch a Roblox URI through the configured environment proxy when needed."""
    if proxy_master is not None and (sys.platform == 'darwin' or sys.platform.startswith('linux')):
        config = getattr(proxy_master, 'config_manager', None)
        if getattr(config, 'proxy_mode', '') == 'env' and bool(
            getattr(config, 'proxy_features_enabled', False)
        ):
            if sys.platform == 'darwin':
                from ..utils.platform_macos import relaunch_roblox_with_proxy_env
            else:
                from ..utils.platform_linux import relaunch_roblox_with_proxy_env

            return relaunch_roblox_with_proxy_env(proxy_master.roblox_env_proxy_url(), target)
    return launch_as_standard_user(target)


__all__ = ['launch_roblox_target']
