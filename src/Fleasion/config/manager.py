"""Configuration management."""

import json
import threading
from copy import deepcopy
from pathlib import Path

from ..utils import CONFIG_DIR, CONFIG_FILE, CONFIGS_FOLDER, DEFAULT_SETTINGS


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
                with Path(CONFIG_FILE).open() as f:
                    loaded = json.load(f)
                if 'configs' in loaded:
                    self._migrate_old_format(loaded)
                    return {
                        'strip_textures': loaded.get('strip_textures', True),
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
                    with Path(config_path).open('w') as f:
                        json.dump(data, f, indent=2)
                except OSError:
                    pass

    def _ensure_default_config(self):
        """Ensure at least one default config exists."""
        if not self.config_names:
            default_path = CONFIGS_FOLDER / 'Default.json'
            with Path(default_path).open('w') as f:
                json.dump({'replacement_rules': []}, f, indent=2)

    def _save_settings(self):
        """Save settings to disk."""
        with self._lock:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with Path(CONFIG_FILE).open('w') as f:
                json.dump(self.settings, f, indent=2)

    def _get_config_path(self, name: str) -> Path:
        """Get the path for a config file."""
        return CONFIGS_FOLDER / f'{name}.json'

    def _load_config(self, name: str) -> dict:
        """Load a config from disk."""
        path = self._get_config_path(name)
        if path.exists():
            try:
                with Path(path).open() as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {'replacement_rules': []}

    def _save_config(self, name: str, data: dict):
        """Save a config to disk."""
        with self._lock:
            CONFIGS_FOLDER.mkdir(parents=True, exist_ok=True)
            with Path(self._get_config_path(name)).open('w') as f:
                json.dump(data, f, indent=2)

    @property
    def strip_textures(self) -> bool:
        """Get strip textures setting."""
        return self.settings.get('strip_textures', True)

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

    def save(self):
        """Save settings."""
        self._save_settings()

    def create_config(self, name: str) -> bool:
        """Create a new config. Returns True if successful."""
        if not name or name in self.config_names:
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
        ):
            return False
        config = self._load_config(name)
        self._save_config(new_name, deepcopy(config))
        return True

    def get_all_replacements(self) -> tuple[dict[int, int], set[int]]:
        """Get replacements from all enabled configs."""
        replacements: dict[int, int] = {}
        removals: set[int] = set()
        for config_name in self.enabled_configs:
            if config_name not in self.config_names:
                continue
            for rule in self.get_replacement_rules(config_name):
                # Skip disabled profiles
                if not rule.get('enabled', True):
                    continue
                ids = rule.get('replace_ids', [])
                if rule.get('remove'):
                    removals.update(ids)
                elif (target := rule.get('with_id')) is not None:
                    replacements.update(dict.fromkeys(ids, target))
        return replacements, removals
