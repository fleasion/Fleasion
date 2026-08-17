"""Import and register every decorated Fleasion QML type."""

from __future__ import annotations

from . import animation_conversion as animation_conversion
from . import animation_preview as animation_preview
from . import app_info as app_info
from . import cache as cache
from . import community_presets as community_presets
from . import font_preview as font_preview
from . import logs as logs
from . import modifications as modifications
from . import payload_preview as payload_preview
from . import proxy as proxy
from . import replacer as replacer
from . import roblox_document_preview as roblox_document_preview
from . import repair as repair
from . import settings as settings
from . import subplaces as subplaces
from . import subplace_blacklist as subplace_blacklist
from . import texture_pack_preview as texture_pack_preview
from . import utilities as utilities
from . import update as update

__all__ = [
    'animation_conversion',
    'animation_preview',
    'app_info',
    'cache',
    'community_presets',
    'font_preview',
    'logs',
    'modifications',
    'payload_preview',
    'proxy',
    'replacer',
    'roblox_document_preview',
    'repair',
    'settings',
    'subplaces',
    'subplace_blacklist',
    'texture_pack_preview',
    'utilities',
    'update',
]
