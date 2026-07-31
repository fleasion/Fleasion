"""Application paths and constants."""

import os
import sys
from pathlib import Path

from fleasion.utils.metadata import APP_NAME

# Process and proxy configuration
if sys.platform == 'darwin':
    ROBLOX_PROCESS = 'RobloxPlayer'
    ROBLOX_STUDIO_PROCESS = 'RobloxStudio'
elif sys.platform.startswith('linux'):
    ROBLOX_PROCESS = 'sober'
    ROBLOX_STUDIO_PROCESS = 'RobloxStudioBeta.exe'
else:
    ROBLOX_PROCESS = 'RobloxPlayerBeta.exe'
    ROBLOX_STUDIO_PROCESS = 'RobloxStudioBeta.exe'
PROXY_TARGET_HOST = 'assetdelivery.roblox.com'
PROXY_PORT = 443
MACOS_PROXY_BACKEND_PORT = 58443
MACOS_PROXY_HELPER_CONTROL_PORT = 58444
STRIPPABLE_ASSET_TYPES = {'TexturePack'}

# Icon
ICON_FILENAME = 'fleasionlogoHR.icns' if sys.platform == 'darwin' else 'fleasionlogoHR.ico'

_LOCAL_APPDATA_OVERRIDE_ARG = '--fleasion-user-localappdata='
_USER_HOME_ENV = 'FLEASION_USER_HOME'


def _get_user_home() -> Path:
    value = os.environ.get(_USER_HOME_ENV)
    if value:
        return Path(os.path.expandvars(value)).expanduser()
    return Path.home()


def _get_local_appdata() -> Path:
    """Return the intended interactive user's local application-data directory."""
    for arg in sys.argv[1:]:
        if arg.startswith(_LOCAL_APPDATA_OVERRIDE_ARG):
            value = arg.split('=', 1)[1].strip().strip('"')
            if value:
                return Path(os.path.expandvars(value))

    if sys.platform == 'darwin':
        return USER_HOME / 'Library' / 'Application Support'

    local_appdata = os.environ.get('LOCALAPPDATA')
    if local_appdata:
        return Path(local_appdata)

    if sys.platform == 'win32':
        return Path.home() / 'AppData' / 'Local'

    return USER_HOME


def _get_config_dir() -> Path:
    """Return Fleasion's app configuration directory."""
    if sys.platform.startswith('linux'):
        xdg_config_home = os.environ.get('XDG_CONFIG_HOME')
        if xdg_config_home:
            return Path(os.path.expandvars(xdg_config_home)).expanduser() / APP_NAME
        return USER_HOME / '.config' / APP_NAME
    return LOCAL_APPDATA / 'FleasionNT'


# Platform paths
USER_HOME = _get_user_home()
LOCAL_APPDATA = _get_local_appdata()
if sys.platform == 'darwin':
    STORAGE_DB = USER_HOME / 'Library' / 'Roblox' / 'rbx-storage.db'
    STORAGE_DB_GDK = USER_HOME / 'Library' / 'RobloxPCGDK' / 'rbx-storage.db'
elif sys.platform.startswith('linux'):
    STORAGE_DB = (
        USER_HOME
        / '.var'
        / 'app'
        / 'org.vinegarhq.Sober'
        / 'data'
        / 'sober'
        / 'appData'
        / 'rbx-storage.db'
    )
    STORAGE_DB_GDK = STORAGE_DB
else:
    STORAGE_DB = LOCAL_APPDATA / 'Roblox' / 'rbx-storage.db'
    # Microsoft Store (GDK) version of Roblox stores its DB here
    STORAGE_DB_GDK = LOCAL_APPDATA / 'RobloxPCGDK' / 'rbx-storage.db'

# Application directories
CONFIG_DIR = _get_config_dir()
APP_CACHE_DIR = CONFIG_DIR / 'cache'
CONFIG_FILE = CONFIG_DIR / 'settings.json'
CONFIGS_FOLDER = CONFIG_DIR / 'configs'
FASTFLAG_PROFILES_FOLDER = CONFIG_DIR / 'FastFlagProfiles'
LOGS_DIR = CONFIG_DIR / 'logs'
LOG_FILE = LOGS_DIR / 'fleasion.log'

# Proxy CA cert directory (replaces MITMPROXY_DIR)
PROXY_CA_DIR = CONFIG_DIR / 'proxy_ca'

# Preserved Proxy tab traffic (rows/requests/responses/highlight state),
# written when the tab's "Preserve" checkbox is on.
PROXY_TRAFFIC_FILE = CONFIG_DIR / 'proxy_traffic.json'

# PreJsons
CLOG_URL = 'https://raw.githubusercontent.com/fleasion/Fleasion/refs/heads/clog/CLOG.json'
PREJSONS_DIR = CONFIG_DIR / 'PreJsons'
ORIGINALS_DIR = PREJSONS_DIR / 'originals'
REPLACEMENTS_DIR = PREJSONS_DIR / 'replacements'

# Modifications
MODIFICATIONS_JSON = CONFIG_DIR / 'modifications.json'
MOD_ORIGINALS_DIR = CONFIG_DIR / 'ModOriginals'
MOD_CACHE_DIR = CONFIG_DIR / 'ModCache'


def get_icon_path() -> Path | None:
    """Get the path to the application icon file."""
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent.parent))
    candidates = (
        ('fleasionlogoHR.icns', 'fleasionlogoHR.ico')
        if sys.platform == 'darwin'
        else ('fleasionlogoHR.ico', 'fleasionlogoHR.icns')
    )
    for filename in candidates:
        path = base / filename
        if path.exists():
            return path
    return None
