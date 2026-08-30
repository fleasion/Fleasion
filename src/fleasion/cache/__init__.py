"""Cache module for storing and viewing intercepted Roblox assets."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cache_manager import CacheManager
    from .cache_viewer import CacheViewerTab

__all__ = ['CacheManager', 'CacheViewerTab']


def __getattr__(name: str) -> object:
    if name == 'CacheManager':
        from .cache_manager import CacheManager  # ruff: ignore[import-outside-top-level]

        return CacheManager
    if name == 'CacheViewerTab':
        from .cache_viewer import CacheViewerTab  # ruff: ignore[import-outside-top-level]

        return CacheViewerTab
    msg = f'module {__name__!r} has no attribute {name!r}'
    raise AttributeError(msg)
