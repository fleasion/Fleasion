"""Proxy package."""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .master import (
        ProxyMaster as ProxyMaster,
        check_and_patch_running_roblox_ca as check_and_patch_running_roblox_ca,
    )

__all__ = ['ProxyMaster', 'check_and_patch_running_roblox_ca']


def __getattr__(name: str) -> object:
    if name in __all__:
        master = importlib.import_module('.master', __name__)
        values = {
            'ProxyMaster': master.ProxyMaster,
            'check_and_patch_running_roblox_ca': master.check_and_patch_running_roblox_ca,
        }
        return values[name]
    raise AttributeError(name)
