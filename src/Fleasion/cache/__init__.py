"""Cache module for storing and viewing intercepted Roblox assets."""

from .cache_manager import CacheManager
from .cache_viewer import CacheViewerTab

__all__ = ['CacheManager', 'CacheViewerTab']

# Note: The full Reference implementation with 3D viewer, audio player, etc.
# is available in Reference/HoprTest2/modules/cache/main.py
# This simplified version provides core caching functionality.
# To add full features, adapt the Reference code using the patterns established here.
