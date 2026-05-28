"""Application paths and constants."""

import sys
import os
from pathlib import Path

# Application metadata
APP_NAME = 'Fleasion'
APP_VERSION = '2.0.1'
APP_AUTHOR = '@8ar__, @dis_spencer, @1_v'
APP_LOGIC = '@blockce, @0100152000022000, @Yeha., @emk530'
APP_CONCEPT = '@cro.p'
APP_DISCORD = 'discord.gg/hXyhKehEZF'
APP_REPO = 'https://github.com/fleasion/Fleasion'

# Process and proxy configuration
ROBLOX_PROCESS = 'RobloxPlayerBeta.exe'
ROBLOX_STUDIO_PROCESS = 'RobloxStudioBeta.exe'
PROXY_TARGET_HOST = 'assetdelivery.roblox.com'
PROXY_PORT = 443
STRIPPABLE_ASSET_TYPES = {'TexturePack'}

# Icon
ICON_FILENAME = 'fleasionlogoHR.ico'

_LOCAL_APPDATA_OVERRIDE_ARG = '--fleasion-user-localappdata='


def _get_local_appdata() -> Path:
    """Return the intended interactive user's LocalAppData directory."""
    for arg in sys.argv[1:]:
        if arg.startswith(_LOCAL_APPDATA_OVERRIDE_ARG):
            value = arg.split('=', 1)[1].strip().strip('"')
            if value:
                return Path(os.path.expandvars(value))

    local_appdata = os.environ.get('LOCALAPPDATA')
    if local_appdata:
        return Path(local_appdata)

    return Path.home() / 'AppData' / 'Local'


# Windows paths
LOCAL_APPDATA = _get_local_appdata()
STORAGE_DB = LOCAL_APPDATA / 'Roblox' / 'rbx-storage.db'
# Microsoft Store (GDK) version of Roblox stores its DB here
STORAGE_DB_GDK = LOCAL_APPDATA / 'RobloxPCGDK' / 'rbx-storage.db'

# Application directories
CONFIG_DIR = LOCAL_APPDATA / 'FleasionNT'
APP_CACHE_DIR = CONFIG_DIR / 'cache'
CONFIG_FILE = CONFIG_DIR / 'settings.json'
CONFIGS_FOLDER = CONFIG_DIR / 'configs'

# Proxy CA cert directory (replaces MITMPROXY_DIR)
PROXY_CA_DIR = CONFIG_DIR / 'proxy_ca'

# PreJsons
CLOG_URL = 'https://raw.githubusercontent.com/fleasion/Fleasion/refs/heads/clog/CLOG.json'
PREJSONS_DIR = CONFIG_DIR / 'PreJsons'
ORIGINALS_DIR = PREJSONS_DIR / 'originals'
REPLACEMENTS_DIR = PREJSONS_DIR / 'replacements'

# Modifications
MODIFICATIONS_JSON = CONFIG_DIR / 'modifications.json'
MOD_ORIGINALS_DIR = CONFIG_DIR / 'ModOriginals'
MOD_CACHE_DIR = CONFIG_DIR / 'ModCache'

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
    'proxy_features_enabled': True,
    'upstream_transport_mode': 'auto',
    'upstream_http_connect_host': '',
    'upstream_http_connect_port': 0,
    'upstream_http_connect_username': '',
    'upstream_http_connect_password': '',
    'upstream_socks5_host': '',
    'upstream_socks5_port': 0,
    'upstream_socks5_username': '',
    'upstream_socks5_password': '',
    'wire_preserving_passthrough': True,
    'vpn_compat_max_assetdelivery_connections': 16,
    'vpn_compat_max_cdn_connections': 32,
    'run_on_boot': True,
    'close_to_tray': True,
    'close_scraped_games_menu_on_open': True,
    'close_viewer_on_replace': True,
    'show_replacer_notifications': True,
    'multi_instance_launching': False,
    # Scraper tab - column visibility
    'scraper_column_visibility': {
        'hash_name':  True,
        'creator':    False,
        'asset_id':   True,
        'type':       True,
        'size':       True,
        'cached_at':  True,
        'url':        False,
    },
    'scraper_column_widths': {},
    'time_wasted_seconds': 0,
    'auto_convert_anim_rig': False,
    'skip_non_player_anim_replace': False,
    'scraper_blacklist': [],
    'subplace_blacklist': [],
    'subplace_blacklist_mode': 'block',
    'username_spoofer': {
        'save_settings': False,
        'others_name': '',
        'others_apply_ingame': False,
        'others_verified': False,
        'self_name': '',
        'self_apply_ingame': False,
        'self_verified': False,
        'self_game_creator': False,
    },
}


def get_icon_path() -> Path | None:
    """Get the path to the application icon file."""
    path = Path(getattr(sys, '_MEIPASS', Path(__file__).parent.parent)) / ICON_FILENAME
    return path if path.exists() else None
