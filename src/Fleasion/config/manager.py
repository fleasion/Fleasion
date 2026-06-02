"""Configuration management."""

import json
import locale
import stat
import threading
from copy import deepcopy
from pathlib import Path

from ..utils.paths import CONFIG_DIR, CONFIG_FILE, CONFIGS_FOLDER, DEFAULT_SETTINGS

# Windows forbids these characters in file and folder names.
_INVALID_FILENAME_CHARS = frozenset('\\/:*?"<>|')
_FALLBACK_JSON_ENCODINGS = (
    'utf-8-sig',
    'utf-16',
    'utf-16-le',
    'utf-16-be',
    'utf-32',
    'utf-32-le',
    'utf-32-be',
    'cp1252',
)


class ConfigManager:
    """Manages application settings and replacement configurations."""

    def __init__(self):
        self._lock = threading.Lock()
        self.settings = self._load_settings()
        self._ensure_default_config()
        self.reconcile_configs(save=False)

    def _load_settings(self) -> dict:
        """Load settings from disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIGS_FOLDER.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                loaded = self._load_json_file(Path(CONFIG_FILE))
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
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
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

    @staticmethod
    def _clear_read_only(path: Path) -> None:
        """Clear the read-only attribute on an existing file."""
        if not path.exists():
            return
        try:
            path.chmod(path.stat().st_mode | stat.S_IWRITE)
        except OSError:
            pass

    @staticmethod
    def _fallback_json_encodings() -> tuple[str, ...]:
        """Return legacy text encodings to try after strict JSON decoding fails."""
        preferred = locale.getpreferredencoding(False)
        encodings: list[str] = []
        for encoding in (*_FALLBACK_JSON_ENCODINGS, preferred):
            if encoding and encoding.lower() not in {e.lower() for e in encodings}:
                encodings.append(encoding)
        return tuple(encodings)

    def _load_json_file(self, path: Path) -> dict:
        """Load JSON and recover legacy non-UTF files when possible."""
        raw = path.read_bytes()
        decode_error: UnicodeDecodeError | None = None

        try:
            return json.loads(raw)
        except UnicodeDecodeError as exc:
            decode_error = exc

        for encoding in self._fallback_json_encodings():
            try:
                text = raw.decode(encoding)
                loaded = json.loads(text)
            except (LookupError, UnicodeDecodeError, json.JSONDecodeError):
                continue

            # Normalize recovered configs back to UTF-8 JSON so future launches
            # do not depend on locale-specific decoding.
            try:
                self._clear_read_only(path)
                with path.open('w', encoding='utf-8') as f:
                    json.dump(loaded, f, indent=2)
            except OSError:
                pass
            return loaded

        if decode_error is not None:
            raise decode_error
        return json.loads(raw)

    def _load_config(self, name: str) -> dict:
        """Load a config from disk."""
        path = self._get_config_path(name)
        if path.exists():
            try:
                self._clear_read_only(path)
                return self._load_json_file(Path(path))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                pass
        return {'replacement_rules': []}

    def _save_config(self, name: str, data: dict):
        """Save a config to disk."""
        with self._lock:
            CONFIGS_FOLDER.mkdir(parents=True, exist_ok=True)
            path = self._get_config_path(name)
            self._clear_read_only(path)
            with Path(path).open('w', encoding='utf-8') as f:
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
    def proxy_features_enabled(self) -> bool:
        """Get proxy feature toggle."""
        return self.settings.get('proxy_features_enabled', True)

    @proxy_features_enabled.setter
    def proxy_features_enabled(self, value: bool):
        """Set proxy feature toggle."""
        self.settings['proxy_features_enabled'] = value
        self._save_settings()

    @property
    def upstream_transport_mode(self) -> str:
        mode = str(self.settings.get('upstream_transport_mode', 'auto') or 'auto').lower()
        valid = {'auto', 'direct_ip', 'system_proxy', 'http_connect', 'socks5'}
        return mode if mode in valid else 'auto'

    @upstream_transport_mode.setter
    def upstream_transport_mode(self, value: str):
        value = str(value or 'auto').lower()
        self.settings['upstream_transport_mode'] = value if value in {
            'auto', 'direct_ip', 'system_proxy', 'http_connect', 'socks5'
        } else 'auto'
        self._save_settings()

    @property
    def wire_preserving_passthrough(self) -> bool:
        value = self.settings.get('wire_preserving_passthrough', False)
        if isinstance(value, str):
            return value.strip().lower() in {'1', 'true', 'yes', 'on'}
        return bool(value)

    @wire_preserving_passthrough.setter
    def wire_preserving_passthrough(self, value: bool):
        self.settings['wire_preserving_passthrough'] = bool(value)
        self._save_settings()

    @property
    def upstream_http_connect_host(self) -> str:
        return str(self.settings.get('upstream_http_connect_host', '') or '')

    @upstream_http_connect_host.setter
    def upstream_http_connect_host(self, value: str):
        self.settings['upstream_http_connect_host'] = str(value or '').strip()
        self._save_settings()

    @property
    def upstream_http_connect_port(self) -> int:
        try:
            return max(0, min(65535, int(self.settings.get('upstream_http_connect_port', 0) or 0)))
        except (TypeError, ValueError):
            return 0

    @upstream_http_connect_port.setter
    def upstream_http_connect_port(self, value: int):
        self.settings['upstream_http_connect_port'] = max(0, min(65535, int(value or 0)))
        self._save_settings()

    @property
    def upstream_http_connect_username(self) -> str:
        return str(self.settings.get('upstream_http_connect_username', '') or '')

    @upstream_http_connect_username.setter
    def upstream_http_connect_username(self, value: str):
        self.settings['upstream_http_connect_username'] = str(value or '')
        self._save_settings()

    @property
    def upstream_http_connect_password(self) -> str:
        return str(self.settings.get('upstream_http_connect_password', '') or '')

    @upstream_http_connect_password.setter
    def upstream_http_connect_password(self, value: str):
        self.settings['upstream_http_connect_password'] = str(value or '')
        self._save_settings()

    @property
    def upstream_socks5_host(self) -> str:
        return str(self.settings.get('upstream_socks5_host', '') or '')

    @upstream_socks5_host.setter
    def upstream_socks5_host(self, value: str):
        self.settings['upstream_socks5_host'] = str(value or '').strip()
        self._save_settings()

    @property
    def upstream_socks5_port(self) -> int:
        try:
            return max(0, min(65535, int(self.settings.get('upstream_socks5_port', 0) or 0)))
        except (TypeError, ValueError):
            return 0

    @upstream_socks5_port.setter
    def upstream_socks5_port(self, value: int):
        self.settings['upstream_socks5_port'] = max(0, min(65535, int(value or 0)))
        self._save_settings()

    @property
    def upstream_socks5_username(self) -> str:
        return str(self.settings.get('upstream_socks5_username', '') or '')

    @upstream_socks5_username.setter
    def upstream_socks5_username(self, value: str):
        self.settings['upstream_socks5_username'] = str(value or '')
        self._save_settings()

    @property
    def upstream_socks5_password(self) -> str:
        return str(self.settings.get('upstream_socks5_password', '') or '')

    @upstream_socks5_password.setter
    def upstream_socks5_password(self, value: str):
        self.settings['upstream_socks5_password'] = str(value or '')
        self._save_settings()

    @property
    def vpn_compat_max_assetdelivery_connections(self) -> int:
        try:
            return max(1, min(128, int(self.settings.get('vpn_compat_max_assetdelivery_connections', 16) or 16)))
        except (TypeError, ValueError):
            return 16

    @vpn_compat_max_assetdelivery_connections.setter
    def vpn_compat_max_assetdelivery_connections(self, value: int):
        self.settings['vpn_compat_max_assetdelivery_connections'] = max(1, min(128, int(value or 16)))
        self._save_settings()

    @property
    def vpn_compat_max_cdn_connections(self) -> int:
        try:
            return max(1, min(256, int(self.settings.get('vpn_compat_max_cdn_connections', 32) or 32)))
        except (TypeError, ValueError):
            return 32

    @vpn_compat_max_cdn_connections.setter
    def vpn_compat_max_cdn_connections(self, value: int):
        self.settings['vpn_compat_max_cdn_connections'] = max(1, min(256, int(value or 32)))
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
    def multi_instance_launching(self) -> bool:
        """Get multi-instance launching setting."""
        return self.settings.get('multi_instance_launching', False)

    @multi_instance_launching.setter
    def multi_instance_launching(self, value: bool):
        """Set multi-instance launching setting."""
        self.settings['multi_instance_launching'] = value
        self._save_settings()

    @property
    def close_scraped_games_on_open(self) -> bool:
        return self.settings.get('close_scraped_games_on_open', True)

    @close_scraped_games_on_open.setter
    def close_scraped_games_on_open(self, value: bool):
        self.settings['close_scraped_games_on_open'] = value
        self._save_settings()

    @property
    def close_scraped_games_menu_on_open(self) -> bool:
        return self.settings.get('close_scraped_games_menu_on_open', True)

    @close_scraped_games_menu_on_open.setter
    def close_scraped_games_menu_on_open(self, value: bool):
        self.settings['close_scraped_games_menu_on_open'] = value
        self._save_settings()

    @property
    def close_viewer_on_replace(self) -> bool:
        return self.settings.get('close_viewer_on_replace', True)

    @close_viewer_on_replace.setter
    def close_viewer_on_replace(self, value: bool):
        self.settings['close_viewer_on_replace'] = value
        self._save_settings()

    @property
    def show_replacer_notifications(self) -> bool:
        """Get show replacer notifications setting."""
        return self.settings.get('show_replacer_notifications', True)

    @show_replacer_notifications.setter
    def show_replacer_notifications(self, value: bool):
        """Set show replacer notifications setting."""
        self.settings['show_replacer_notifications'] = value
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
    def subplace_blacklist(self) -> list[str]:
        return self.settings.get('subplace_blacklist', [])

    @subplace_blacklist.setter
    def subplace_blacklist(self, value: list[str]):
        self.settings['subplace_blacklist'] = value
        self._save_settings()

    @property
    def subplace_blacklist_mode(self) -> str:
        mode = self.settings.get('subplace_blacklist_mode', 'block')
        return mode if mode in ('block', 'stall') else 'block'

    @subplace_blacklist_mode.setter
    def subplace_blacklist_mode(self, value: str):
        self.settings['subplace_blacklist_mode'] = value if value in ('block', 'stall') else 'block'
        self._save_settings()

    @property
    def username_spoofer(self) -> dict:
        default = deepcopy(DEFAULT_SETTINGS.get('username_spoofer', {}))
        saved = self.settings.get('username_spoofer', {})
        if isinstance(saved, dict):
            default.update(saved)
        return default

    @username_spoofer.setter
    def username_spoofer(self, value: dict):
        base = deepcopy(DEFAULT_SETTINGS.get('username_spoofer', {}))
        if isinstance(value, dict):
            base.update({
                'save_settings': bool(value.get('save_settings', base.get('save_settings', False))),
                'others_name': str(value.get('others_name', base.get('others_name', ''))),
                'others_apply_ingame': bool(value.get('others_apply_ingame', base.get('others_apply_ingame', False))),
                'others_verified': bool(value.get('others_verified', base.get('others_verified', False))),
                'self_name': str(value.get('self_name', base.get('self_name', ''))),
                'self_apply_ingame': bool(value.get('self_apply_ingame', base.get('self_apply_ingame', False))),
                'self_verified': bool(value.get('self_verified', base.get('self_verified', False))),
                'self_game_creator': bool(value.get('self_game_creator', base.get('self_game_creator', False))),
            })
        self.settings['username_spoofer'] = base
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
        current_configs = set(self.config_names)
        return [
            name
            for name in self.settings.get('enabled_configs', [])
            if name in current_configs
        ]

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
        if name not in self.config_names:
            self.reconcile_configs()
            return False
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
        if name not in self.config_names:
            self.reconcile_configs()
            return
        configs = self.enabled_configs.copy()
        if enabled and name not in configs:
            configs.append(name)
        elif not enabled and name in configs:
            configs.remove(name)
        self.enabled_configs = configs

    def reconcile_configs(self, save: bool = True) -> bool:
        """Synchronize settings with config files currently on disk.

        Returns True when the active settings changed.
        """
        self._ensure_default_config()
        current_configs = self.config_names
        changed = False

        enabled = self.settings.get('enabled_configs', [])
        cleaned_enabled = [
            name
            for name in enabled
            if name in current_configs
        ]
        if cleaned_enabled != enabled:
            self.settings['enabled_configs'] = cleaned_enabled
            changed = True

        last_config = self.settings.get('last_config', 'Default')
        if last_config not in current_configs:
            self.settings['last_config'] = current_configs[0] if current_configs else 'Default'
            changed = True

        if changed and save:
            self._save_settings()
        return changed

    @property
    def last_config(self) -> str:
        """Get the last displayed config."""
        self.reconcile_configs()
        return self.settings.get('last_config', 'Default')

    @last_config.setter
    def last_config(self, value: str):
        """Set the last displayed config."""
        self.settings['last_config'] = value
        self.reconcile_configs(save=False)
        self._save_settings()

    @property
    def config_names(self) -> list[str]:
        """Get list of all config names."""
        CONFIGS_FOLDER.mkdir(parents=True, exist_ok=True)
        return sorted([p.stem for p in CONFIGS_FOLDER.glob('*.json')])

    def refresh_config_names(self):
        """Refresh config names from disk (for external changes)."""
        self.reconcile_configs()

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

    @staticmethod
    def _iter_replacement_rules(entries: list):
        """Yield profile rules depth-first, skipping organizational groups."""
        for entry in entries:
            if isinstance(entry, dict) and entry.get('type') == 'group':
                yield from ConfigManager._iter_replacement_rules(entry.get('children', []))
            else:
                yield entry

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
            for rule in self._iter_replacement_rules(self.get_replacement_rules(config_name)):
                if not isinstance(rule, dict):
                    continue
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
