"""Cache module for storing and viewing intercepted Roblox assets."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cache_manager import CacheManager
    from .cache_viewer import CacheViewerTab

__all__ = ['CacheManager', 'CacheViewerTab']


def __getattr__(name: str) -> object:
    if name == 'CacheManager':
        return import_module('.cache_manager', __name__).CacheManager
    if name == 'CacheViewerTab':
        return import_module('.cache_viewer', __name__).CacheViewerTab
    msg = f'module {__name__!r} has no attribute {name!r}'
    raise AttributeError(msg)
