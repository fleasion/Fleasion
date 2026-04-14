"""Configuration management."""

import json
import threading
from copy import deepcopy
from pathlib import Path

from ..utils import CONFIG_DIR, CONFIG_FILE, CONFIGS_FOLDER, DEFAULT_SETTINGS

# Windows forbids these characters in file and folder names.
_INVALID_FILENAME_CHARS = frozenset('\\/:*?"<>|')


class ConfigManager:
    """Manages application settings and replacement configurations."""

    def __init__(self):
        self._lock = threading.Lock()
        self.settings = self._load_settings()
        self._ensure_default_config()
        # Clean up enabled_configs to only include existing configs
        self.settings['enabled_configs'] = [
            c
            for c in self.settings.get('enabled_configs', [])
            if c in self.config_names
        ]
        # Ensure last_config is valid
        if self.settings.get('last_config') not in self.config_names:
            self.settings['last_config'] = (
                self.config_names[0] if self.config_names else 'Default'
            )

    def _load_settings(self) -> dict:
        """Load settings from disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIGS_FOLDER.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                with Path(CONFIG_FILE).open(encoding='utf-8') as f:
                    loaded = json.load(f)
                if 'configs' in loaded:
                    self._migrate_old_format(loaded)
                    return {
                        'strip_textures': loaded.get('strip_textures', False),
                        'enabled_configs': [],
                        'last_config': loaded.get('active_config', 'Default'),
                        'theme': 'System',
                    }
                # Migrate from old active_config to new format
                if 'active_config' in loaded and 'enabled_configs' not in loaded:
                    loaded['enabled_configs'] = [loaded['active_config']]
                    loaded['last_config'] = loaded['active_config']
                    del loaded['active_config']
                return {**DEFAULT_SETTINGS, **loaded}
            except (json.JSONDecodeError, OSError):
                pass
        return deepcopy(DEFAULT_SETTINGS)

    def _migrate_old_format(self, old_config: dict):
        """Migrate old config format to new format."""
        configs = old_config.get('configs', {})
        for name, data in configs.items():
            config_path = CONFIGS_FOLDER / f'{name}.json'
            if not config_path.exists():
                try:
                    with Path(config_path).open('w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2)
                except OSError:
                    pass

    def _ensure_default_config(self):
        """Ensure at least one default config exists."""
        if not self.config_names:
            default_path = CONFIGS_FOLDER / 'Default.json'
            with Path(default_path).open('w', encoding='utf-8') as f:
                json.dump({'replacement_rules': []}, f, indent=2)

    def _save_settings(self):
        """Save settings to disk."""
        with self._lock:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with Path(CONFIG_FILE).open('w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=2)

    def _get_config_path(self, name: str) -> Path:
        """Get the path for a config file."""
        return CONFIGS_FOLDER / f'{name}.json'

    def _load_config(self, name: str) -> dict:
        """Load a config from disk."""
        path = self._get_config_path(name)
        if path.exists():
            try:
                with Path(path).open(encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {'replacement_rules': []}

    def _save_config(self, name: str, data: dict):
        """Save a config to disk."""
        with self._lock:
            CONFIGS_FOLDER.mkdir(parents=True, exist_ok=True)
            with Path(self._get_config_path(name)).open('w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

    @property
    def strip_textures(self) -> bool:
        """Get strip textures setting."""
        return self.settings.get('strip_textures', False)

    @strip_textures.setter
    def strip_textures(self, value: bool):
        """Set strip textures setting."""
        self.settings['strip_textures'] = value
        self._save_settings()

    @property
    def theme(self) -> str:
        """Get theme setting."""
        return self.settings.get('theme', 'System')

    @theme.setter
    def theme(self, value: str):
        """Set theme setting."""
        self.settings['theme'] = value
        self._save_settings()

    @property
    def audio_volume(self) -> int:
        """Get audio volume setting (0-100)."""
        return self.settings.get('audio_volume', 70)

    @audio_volume.setter
    def audio_volume(self, value: int):
        """Set audio volume setting (0-100)."""
        self.settings['audio_volume'] = max(0, min(100, value))
        self._save_settings()

    @property
    def always_on_top(self) -> bool:
        """Get always on top setting."""
        return self.settings.get('always_on_top', False)

    @always_on_top.setter
    def always_on_top(self, value: bool):
        """Set always on top setting."""
        self.settings['always_on_top'] = value
        self._save_settings()

    @property
    def open_dashboard_on_launch(self) -> bool:
        """Get open dashboard on launch setting."""
        return self.settings.get('open_dashboard_on_launch', True)

    @open_dashboard_on_launch.setter
    def open_dashboard_on_launch(self, value: bool):
        """Set open dashboard on launch setting."""
        self.settings['open_dashboard_on_launch'] = value
        self._save_settings()

    @property
    def first_time_setup_complete(self) -> bool:
        """Get first time setup complete flag."""
        return self.settings.get('first_time_setup_complete', False)

    @first_time_setup_complete.setter
    def first_time_setup_complete(self, value: bool):
        """Set first time setup complete flag."""
        self.settings['first_time_setup_complete'] = value
        self._save_settings()

    @property
    def auto_delete_cache_on_exit(self) -> bool:
        """Get auto delete cache on Roblox exit setting."""
        return self.settings.get('auto_delete_cache_on_exit', True)

    @auto_delete_cache_on_exit.setter
    def auto_delete_cache_on_exit(self, value: bool):
        """Set auto delete cache on Roblox exit setting."""
        self.settings['auto_delete_cache_on_exit'] = value
        self._save_settings()

    @property
    def clear_cache_on_launch(self) -> bool:
        """Get clear cache on launch setting."""
        return self.settings.get('clear_cache_on_launch', True)

    @clear_cache_on_launch.setter
    def clear_cache_on_launch(self, value: bool):
        """Set clear cache on launch setting."""
        self.settings['clear_cache_on_launch'] = value
        self._save_settings()

    @property
    def run_on_boot(self) -> bool:
        return self.settings.get('run_on_boot', False)

    @run_on_boot.setter
    def run_on_boot(self, value: bool):
        self.settings['run_on_boot'] = value
        self._save_settings()

    @property
    def close_to_tray(self) -> bool:
        """Get close to tray setting."""
        return self.settings.get('close_to_tray', True)

    @close_to_tray.setter
    def close_to_tray(self, value: bool):
        """Set close to tray setting."""
        self.settings['close_to_tray'] = value
        self._save_settings()

    @property
    def close_scraped_games_on_open(self) -> bool:
        return self.settings.get('close_scraped_games_on_open', True)

    @close_scraped_games_on_open.setter
    def close_scraped_games_on_open(self, value: bool):
        self.settings['close_scraped_games_on_open'] = value
        self._save_settings()

    @property
    def window_geometry(self) -> str:
        """Get the saved window geometry (hex string)."""
        return self.settings.get('window_geometry', '')

    @window_geometry.setter
    def window_geometry(self, value: str):
        """Set the window geometry."""
        self.settings['window_geometry'] = value
        self._save_settings()

    @property
    def auto_convert_anim_rig(self) -> bool:
        return True

    @auto_convert_anim_rig.setter
    def auto_convert_anim_rig(self, value: bool):
        self.settings['auto_convert_anim_rig'] = value
        self._save_settings()

    @property
    def skip_non_player_anim_replace(self) -> bool:
        return self.settings.get('skip_non_player_anim_replace', False)

    @skip_non_player_anim_replace.setter
    def skip_non_player_anim_replace(self, value: bool):
        self.settings['skip_non_player_anim_replace'] = value
        self._save_settings()

    @property
    def scraper_blacklist(self) -> list[str]:
        return self.settings.get('scraper_blacklist', [])

    @scraper_blacklist.setter
    def scraper_blacklist(self, value: list[str]):
        self.settings['scraper_blacklist'] = value
        self._save_settings()

    @property
    def show_names(self) -> bool:
        return self.settings.get('show_names', True)

    @show_names.setter
    def show_names(self, value: bool):
        self.settings['show_names'] = value
        self._save_settings()

    @property
    def show_creator_id(self) -> bool:
        return self.settings.get('show_creator_id', False)

    @show_creator_id.setter
    def show_creator_id(self, value: bool):
        self.settings['show_creator_id'] = value
        self._save_settings()

    @property
    def export_naming(self) -> list[str]:
        """Get export naming options (name, id, hash)."""
        return self.settings.get('export_naming', ['id'])

    @export_naming.setter
    def export_naming(self, value: list[str]):
        """Set export naming options."""
        self.settings['export_naming'] = value
        self._save_settings()

    def is_export_naming_enabled(self, option: str) -> bool:
        """Check if an export naming option is enabled."""
        return option in self.export_naming

    def toggle_export_naming(self, option: str) -> bool:
        """Toggle an export naming option. Returns new state."""
        options = self.export_naming.copy()
        if option in options:
            options.remove(option)
            new_state = False
        else:
            options.append(option)
            new_state = True
        self.export_naming = options
        return new_state

    @property
    def enabled_configs(self) -> list[str]:
        """Get list of enabled configs."""
        return self.settings.get('enabled_configs', [])

    @enabled_configs.setter
    def enabled_configs(self, value: list[str]):
        """Set list of enabled configs."""
        self.settings['enabled_configs'] = value
        self._save_settings()

    def is_config_enabled(self, name: str) -> bool:
        """Check if a config is enabled."""
        return name in self.enabled_configs

    def toggle_config_enabled(self, name: str) -> bool:
        """Toggle a config's enabled state. Returns new state."""
        configs = self.enabled_configs.copy()
        if name in configs:
            configs.remove(name)
            new_state = False
        else:
            configs.append(name)
            new_state = True
        self.enabled_configs = configs
        return new_state

    def set_config_enabled(self, name: str, enabled: bool):
        """Set a config's enabled state."""
        configs = self.enabled_configs.copy()
        if enabled and name not in configs:
            configs.append(name)
        elif not enabled and name in configs:
            configs.remove(name)
        self.enabled_configs = configs

    @property
    def last_config(self) -> str:
        """Get the last displayed config."""
        name = self.settings.get('last_config', 'Default')
        if name not in self.config_names:
            name = self.config_names[0] if self.config_names else 'Default'
            self.settings['last_config'] = name
        return name

    @last_config.setter
    def last_config(self, value: str):
        """Set the last displayed config."""
        self.settings['last_config'] = value
        self._save_settings()

    @property
    def config_names(self) -> list[str]:
        """Get list of all config names."""
        CONFIGS_FOLDER.mkdir(parents=True, exist_ok=True)
        return sorted([p.stem for p in CONFIGS_FOLDER.glob('*.json')])

    def refresh_config_names(self):
        """Refresh config names from disk (for external changes)."""
        # config_names property already reads from disk, this is just for clarity
        pass

    def get_replacement_rules(self, config_name: str) -> list:
        """Get rules for a specific config."""
        return self._load_config(config_name).get('replacement_rules', [])

    def set_replacement_rules(self, config_name: str, rules: list):
        """Set rules for a specific config."""
        config = self._load_config(config_name)
        config['replacement_rules'] = rules
        self._save_config(config_name, config)

    @property
    def replacement_rules(self) -> list:
        """Get rules for the currently displayed (last) config."""
        return self.get_replacement_rules(self.last_config)

    @replacement_rules.setter
    def replacement_rules(self, value: list):
        """Set rules for the currently displayed (last) config."""
        self.set_replacement_rules(self.last_config, value)

    @property
    def time_wasted_seconds(self) -> int:
        """Get total time wasted in seconds (cumulative across sessions)."""
        return self.settings.get('time_wasted_seconds', 0)

    @time_wasted_seconds.setter
    def time_wasted_seconds(self, value: int):
        """Set total time wasted in seconds."""
        self.settings['time_wasted_seconds'] = max(0, int(value))
        self._save_settings()

    def save(self):
        """Save settings."""
        self._save_settings()

    @staticmethod
    def is_valid_config_name(name: str) -> bool:
        """Return True if *name* is safe to use as a Windows filename."""
        if not name or not name.strip():
            return False
        # Characters Windows forbids in file/folder names
        return not any(c in name for c in _INVALID_FILENAME_CHARS)

    def create_config(self, name: str) -> bool:
        """Create a new config. Returns True if successful."""
        if not name or name in self.config_names or not self.is_valid_config_name(name):
            return False
        self._save_config(name, {'replacement_rules': []})
        return True

    def delete_config(self, name: str) -> bool:
        """Delete a config. Returns True if successful."""
        if name not in self.config_names or len(self.config_names) <= 1:
            return False
        try:
            self._get_config_path(name).unlink()
            # Remove from enabled configs if present
            if name in self.enabled_configs:
                configs = self.enabled_configs.copy()
                configs.remove(name)
                self.enabled_configs = configs
            # Update last_config if needed
            if self.last_config == name:
                self.settings['last_config'] = self.config_names[0]
                self._save_settings()
            return True
        except OSError:
            return False

    def rename_config(self, old_name: str, new_name: str) -> bool:
        """Rename a config. Returns True if successful."""
        if (
            not new_name
            or old_name not in self.config_names
            or new_name in self.config_names
            or not self.is_valid_config_name(new_name)
        ):
            return False
        try:
            self._get_config_path(old_name).rename(self._get_config_path(new_name))
            # Update enabled_configs
            if old_name in self.enabled_configs:
                configs = self.enabled_configs.copy()
                configs.remove(old_name)
                configs.append(new_name)
                self.enabled_configs = configs
            # Update last_config
            if self.settings['last_config'] == old_name:
                self.settings['last_config'] = new_name
                self._save_settings()
            return True
        except OSError:
            return False

    def duplicate_config(self, name: str, new_name: str) -> bool:
        """Duplicate a config. Returns True if successful."""
        if (
            not new_name
            or name not in self.config_names
            or new_name in self.config_names
            or not self.is_valid_config_name(new_name)
        ):
            return False
        config = self._load_config(name)
        self._save_config(new_name, deepcopy(config))
        return True

    def get_all_replacements(self) -> tuple[dict[int | str, int], set[int | str], dict[int | str, str], dict[int | str, str]]:
        """Get replacements from all enabled configs.

        Returns
        -------
        tuple
            - replacements: dict mapping asset IDs/types to replacement IDs
            - removals: set of asset IDs/types to remove entirely
            - cdn_replacements: dict mapping asset IDs/types to CDN URLs
            - local_replacements: dict mapping asset IDs/types to local file paths

        """
        replacements: dict[int | str, int] = {}
        removals: set[int | str] = set()
        cdn_replacements: dict[int | str, str] = {}
        local_replacements: dict[int | str, str] = {}

        # Map of known asset type names to their IDs (for validation)
        ASSET_TYPES = {
            'image': 1, 'tshirt': 2, 'audio': 3, 'mesh': 4, 'lua': 5,
            'html': 6, 'text': 7, 'hat': 8, 'place': 9, 'model': 10,
            'shirt': 11, 'pants': 12, 'decal': 13, 'avatar': 16, 'head': 17,
            'face': 18, 'gear': 19, 'badge': 21, 'groupemblem': 22,
            'animation': 24, 'arms': 25, 'legs': 26, 'torso': 27,
            'rightarm': 28, 'leftarm': 29, 'leftleg': 30, 'rightleg': 31,
            'package': 32, 'youtubevideo': 33, 'gamepass': 34, 'app': 35,
            'code': 37, 'plugin': 38, 'solidmodel': 39, 'meshpart': 40,
            'hairaccessory': 41, 'faceaccessory': 42, 'neckaccessory': 43,
            'shoulderaccessory': 44, 'frontaccessory': 45, 'backaccessory': 46,
            'waistaccessory': 47, 'climbanimation': 48, 'deathanimation': 49,
            'fallanimation': 50, 'idleanimation': 51, 'jumpanimation': 52,
            'runanimation': 53, 'swimanimation': 54, 'walkanimation': 55,
            'poseanimation': 56, 'earaccessory': 57, 'eyeaccessory': 58,
            'localizationtablemanifest': 59, 'emoteanimation': 61, 'video': 62,
            'texturepack': 63, 'tshirtaccessory': 64, 'shirtaccessory': 65,
            'pantsaccessory': 66, 'jacketaccessory': 67, 'sweateraccessory': 68,
            'shortsaccessory': 69, 'leftshoeaccessory': 70, 'rightshoeaccessory': 71,
            'dressskirtaccessory': 72, 'fontfamily': 73, 'fontface': 74,
            'meshhiddensurfaceremoval': 75, 'eyebrowaccessory': 76,
            'eyelashaccessory': 77, 'moodanimation': 78, 'dynamichead': 79,
            'codesnippet': 80,
        }

        for config_name in self.enabled_configs:
            if config_name not in self.config_names:
                continue
            for rule in self.get_replacement_rules(config_name):
                # Skip disabled profiles
                if not rule.get('enabled', True):
                    continue

                ids = rule.get('replace_ids', [])
                mode = rule.get('mode', 'id')

                # Legacy support: convert old 'remove' boolean to mode
                if 'remove' in rule and 'mode' not in rule:
                    mode = 'remove' if rule.get('remove') else 'id'

                parsed_ids: list[int | str] = []
                for v in ids:
                    if isinstance(v, str) and ':' in v:
                        parts = v.split(':', 1)
                        # "parentId:mapIndex" slot key (e.g. "7547298786:1") — keep as str
                        if parts[0].isdigit() and parts[1].isdigit():
                            parsed_ids.append(v)
                            continue
                        # "TexturePack:N" wildcard — replace N-th slot of every TexturePack
                        if parts[0] == 'TexturePack' and parts[1].isdigit():
                            parsed_ids.append(v)
                            continue
                        continue
                    
                    # Try to parse as integer ID first
                    try:
                        parsed_ids.append(int(v))
                        continue
                    except (TypeError, ValueError):
                        pass
                    
                    # Check if it's a known asset type name (case-insensitive)
                    # Convert to numeric ID for proper texture_stripper matching
                    if isinstance(v, str):
                        v_lower = v.lower()
                        # Virtual animation rig-filter types - kept as canonical string keys
                        _VIRTUAL_ANIM = {
                            'r6animation':        'R6Animation',
                            'r15animation':       'R15Animation',
                            'nonplayeranimation': 'NonPlayerAnimation',
                            'r6 animation':        'R6Animation',
                            'r15 animation':       'R15Animation',
                            'non-player animation': 'NonPlayerAnimation',
                        }
                        if v_lower in _VIRTUAL_ANIM:
                            parsed_ids.append(_VIRTUAL_ANIM[v_lower])
                        elif v_lower in ASSET_TYPES:
                            numeric_id = ASSET_TYPES[v_lower]
                            parsed_ids.append(numeric_id)

                if mode == 'remove':
                    removals.update(parsed_ids)
                elif mode == 'cdn':
                    cdn_url = rule.get('cdn_url')
                    if cdn_url:
                        cdn_replacements.update(dict.fromkeys(parsed_ids, cdn_url))
                    else:
                        # Empty CDN URL means remove
                        removals.update(parsed_ids)
                elif mode == 'local':
                    local_path = rule.get('local_path')
                    if local_path:
                        local_replacements.update(dict.fromkeys(parsed_ids, local_path))
                    else:
                        # Empty local path means remove
                        removals.update(parsed_ids)
                elif mode == 'id':
                    # Empty with_id means remove
                    if (target := rule.get('with_id')) is not None:
                        replacements.update(dict.fromkeys(parsed_ids, target))
                    else:
                        removals.update(parsed_ids)

        return replacements, removals, cdn_replacements, local_replacements