"""Cache module for storing and viewing intercepted Roblox assets."""

from typing import TYPE_CHECKING

from .cache_manager import CacheManager

if TYPE_CHECKING:
    from .cache_viewer import CacheViewerTab

__all__ = ['CacheManager', 'CacheViewerTab']


def __getattr__(name):
    if name == 'CacheViewerTab':
        from .cache_viewer import CacheViewerTab
        return CacheViewerTab
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
