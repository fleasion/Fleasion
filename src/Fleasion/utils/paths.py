"""Application paths and constants."""

import sys
from pathlib import Path

# Application metadata
APP_NAME = 'Fleasion'
APP_VERSION = '1.4.0'
APP_AUTHOR = 'Code by @8ar__ | Logic by @blockce, @1_v, @0100152000022000'
APP_DISCORD = 'discord.gg/hXyhKehEZF'

# Process and proxy configuration
ROBLOX_PROCESS = 'RobloxPlayerBeta.exe'
PROXY_TARGET_HOST = 'assetdelivery.roblox.com'
STRIPPABLE_ASSET_TYPES = {'TexturePack'}

# Icon
ICON_FILENAME = 'fleasionlogo2.ico'

# Windows paths
LOCAL_APPDATA = Path.home() / 'AppData' / 'Local'
MITMPROXY_DIR = Path.home() / '.mitmproxy'
STORAGE_DB = LOCAL_APPDATA / 'Roblox' / 'rbx-storage.db'

# Application directories
CONFIG_DIR = LOCAL_APPDATA / 'FleasionNT'
CONFIG_FILE = CONFIG_DIR / 'settings.json'
CONFIGS_FOLDER = CONFIG_DIR / 'configs'

# PreJsons
CLOG_URL = 'https://raw.githubusercontent.com/qrhrqiohj/PFTEST/refs/heads/main/CLOG.json'
PREJSONS_DIR = CONFIG_DIR / 'PreJsons'
ORIGINALS_DIR = PREJSONS_DIR / 'originals'
REPLACEMENTS_DIR = PREJSONS_DIR / 'replacements'

# Default settings
DEFAULT_SETTINGS = {
    'strip_textures': False,
    'enabled_configs': [],
    'last_config': 'Default',
    'theme': 'System',  # System, Light, Dark
    'audio_volume': 70,  # 0-100
    'always_on_top': False,
    'open_dashboard_on_launch': True,
    'first_time_setup_complete': False,
    'auto_delete_cache_on_exit': True,
    'clear_cache_on_launch': True,
}


def get_icon_path() -> Path | None:
    """Get the path to the application icon file."""
    path = Path(getattr(sys, '_MEIPASS', Path(__file__).parent.parent)) / ICON_FILENAME
    return path if path.exists() else None
