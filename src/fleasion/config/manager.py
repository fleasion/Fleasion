"""Configuration management."""

from __future__ import annotations

import importlib
import json
import locale
import stat
import threading
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeIs

from fleasion.utils.paths import CONFIG_DIR, CONFIG_FILE, CONFIGS_FOLDER

if TYPE_CHECKING:
    from collections.abc import Iterator

# Windows forbids these characters in file and folder names.
_INVALID_FILENAME_CHARS = frozenset('\\/:*?"<>|')
MAX_CONFIG_ASSET_FOLDER_DEPTH = 10
_CONTROL_BYTE_LIMIT = 0x20
_DELETE_BYTE = 0x7F
_TEXT_CONTROL_BYTES = frozenset({0x09, 0x0A, 0x0C, 0x0D})
_BINARY_CONTROL_RATIO = 0.1
_BINARY_REPLACEMENT_RATIO = 0.3
_LINUX_MAX_SCAN_CODE = 0x2FF
_WINDOWS_MAX_SCAN_CODE = 0xFF
_HOTKEY_KINDS = ('key', 'mouse_button')
_MOUSE_WHEEL_PLATFORMS = ('linux_evdev', 'windows')
_MOUSE_WHEEL_DIRECTIONS = ('up', 'down')
_LINUX_MOUSE_BUTTON_CODES = frozenset({0x110, 0x111, 0x112, 0x113, 0x114})
_WINDOWS_MOUSE_BUTTON_CODES = frozenset({1, 2, 4, 5, 6})
_WINDOWS_HOTKEY_PLATFORMS = (None, 'windows')
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type ReplacementRules = list[JsonValue]
type ReplacementKey = int | str
type ReplacementMaps = tuple[
    dict[ReplacementKey, int],
    set[ReplacementKey],
    dict[ReplacementKey, str],
    dict[ReplacementKey, str],
]
type FileSignature = tuple[int, int] | None
type ReplacementsSignature = tuple[tuple[str, FileSignature], ...]
type ObjectCollection = list[object] | tuple[object, ...] | set[object]


def _is_object_dict(value: object) -> TypeIs[dict[object, object]]:
    return isinstance(value, dict)


def _is_object_collection(value: object) -> TypeIs[ObjectCollection]:
    return isinstance(value, list | tuple | set)


def _is_object_list(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _preserve_runtime_type[T](value: object, expected_type: type[T]) -> T:
    """Describe an existing runtime contract without coercing its value."""
    if TYPE_CHECKING:
        assert isinstance(value, expected_type)
    return value


def _preserve_int_convertible(value: object) -> str | int | float:
    if TYPE_CHECKING:
        assert isinstance(value, str | int | float)
    return value


def _preserve_path_value(value: object) -> str | Path:
    if TYPE_CHECKING:
        assert isinstance(value, str | Path)
    return value


def _preserve_json_object(value: JsonValue) -> JsonObject:
    if TYPE_CHECKING:
        assert isinstance(value, dict)
    return value


def _preserve_replacement_rules(value: JsonValue) -> ReplacementRules:
    if TYPE_CHECKING:
        assert isinstance(value, list)
    return value


def _is_json_value(value: object) -> TypeIs[JsonValue]:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if _is_object_list(value):
        return all(_is_json_value(item) for item in value)
    if _is_object_dict(value):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _preserve_json_value(value: object) -> JsonValue:
    if TYPE_CHECKING:
        assert _is_json_value(value)
    return value


def _is_str_list(value: object) -> TypeIs[list[str]]:
    return _is_object_list(value) and all(isinstance(item, str) for item in value)


def _preserve_str_list(value: object) -> list[str]:
    if TYPE_CHECKING:
        assert _is_str_list(value)
    return value


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


def _normalise_linux_client(value: object) -> str:
    """Return ``auto`` or a key from the live Linux client registry."""
    normalized = str(value or 'auto').casefold()
    try:
        linux_clients = importlib.import_module('fleasion.utils.linux_clients')
        supported = linux_clients.LINUX_CLIENTS_BY_KEY
    except (ImportError, AttributeError):
        # Keep isolated config loading and recovery usable even when platform
        # modules are unavailable. Sober is the compatibility implementation.
        supported = {'sober': None}
    return normalized if normalized == 'auto' or normalized in supported else 'auto'


def _config_asset_parts(value: str | Path) -> tuple[str, ...] | None:
    """Return valid portable Configs asset parts, or ``None`` for a normal path."""
    text = str(value or '').strip()
    if not text.startswith('/') or text.startswith('//') or '\\' in text:
        return None

    parts = tuple(text[1:].split('/'))
    # Assets must live below at least one folder in Configs.  The final part is
    # the filename, so the number of folders is one less than the part count.
    folder_depth = len(parts) - 1
    if (
        folder_depth < 1
        or folder_depth > MAX_CONFIG_ASSET_FOLDER_DEPTH
        or any(part in {'', '.', '..'} for part in parts)
    ):
        return None
    return parts


def resolve_local_replacement_path(value: str | Path) -> Path:
    """Resolve a portable ``/Folder/file`` replacement path.

    A matching file below the Configs folder takes priority.  If it is absent,
    an existing operating-system absolute path keeps its historical meaning.
    Otherwise the Configs candidate is returned so missing-file diagnostics
    point users at the portable layout they requested.
    """
    text = str(value or '').strip()
    parts = _config_asset_parts(text)
    if parts is None:
        return Path(text)

    configs_candidate = CONFIGS_FOLDER.joinpath(*parts)
    if configs_candidate.is_file():
        return configs_candidate

    os_path = Path(text)
    if os_path.is_file():
        return os_path
    return configs_candidate


def local_replacement_path_for_storage(value: str | Path) -> str:
    """Use portable ``/Folder/file`` notation for files inside Configs."""
    path = Path(value)
    try:
        relative = path.resolve().relative_to(CONFIGS_FOLDER.resolve())
    except OSError, ValueError:
        return str(path)

    folder_depth = len(relative.parts) - 1
    if (
        folder_depth < 1
        or folder_depth > MAX_CONFIG_ASSET_FOLDER_DEPTH
        or any(part in {'', '.', '..'} for part in relative.parts)
    ):
        return str(path)
    return f'/{relative.as_posix()}'


_ASSET_TYPE_IDS = {
    'image': 1,
    'tshirt': 2,
    'audio': 3,
    'mesh': 4,
    'lua': 5,
    'html': 6,
    'text': 7,
    'hat': 8,
    'place': 9,
    'model': 10,
    'shirt': 11,
    'pants': 12,
    'decal': 13,
    'avatar': 16,
    'head': 17,
    'face': 18,
    'gear': 19,
    'badge': 21,
    'groupemblem': 22,
    'animation': 24,
    'arms': 25,
    'legs': 26,
    'torso': 27,
    'rightarm': 28,
    'leftarm': 29,
    'leftleg': 30,
    'rightleg': 31,
    'package': 32,
    'youtubevideo': 33,
    'gamepass': 34,
    'app': 35,
    'code': 37,
    'plugin': 38,
    'solidmodel': 39,
    'meshpart': 40,
    'hairaccessory': 41,
    'faceaccessory': 42,
    'neckaccessory': 43,
    'shoulderaccessory': 44,
    'frontaccessory': 45,
    'backaccessory': 46,
    'waistaccessory': 47,
    'climbanimation': 48,
    'deathanimation': 49,
    'fallanimation': 50,
    'idleanimation': 51,
    'jumpanimation': 52,
    'runanimation': 53,
    'swimanimation': 54,
    'walkanimation': 55,
    'poseanimation': 56,
    'earaccessory': 57,
    'eyeaccessory': 58,
    'localizationtablemanifest': 59,
    'emoteanimation': 61,
    'video': 62,
    'texturepack': 63,
    'tshirtaccessory': 64,
    'shirtaccessory': 65,
    'pantsaccessory': 66,
    'jacketaccessory': 67,
    'sweateraccessory': 68,
    'shortsaccessory': 69,
    'leftshoeaccessory': 70,
    'rightshoeaccessory': 71,
    'dressskirtaccessory': 72,
    'fontfamily': 73,
    'fontface': 74,
    'meshhiddensurfaceremoval': 75,
    'eyebrowaccessory': 76,
    'eyelashaccessory': 77,
    'moodanimation': 78,
    'dynamichead': 79,
    'codesnippet': 80,
}
_RESERVED_ASSET_TYPE_ID_MAX = max(_ASSET_TYPE_IDS.values())
_VIRTUAL_ANIM_TYPES = {
    'r6animation': 'R6Animation',
    'r15animation': 'R15Animation',
    'nonplayeranimation': 'NonPlayerAnimation',
    'r6 animation': 'R6Animation',
    'r15 animation': 'R15Animation',
    'non-player animation': 'NonPlayerAnimation',
}


def _parse_config_asset_id(value: object) -> int | None:
    """Parse an actual integer ID without truncating JSON floats."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _parse_qualified_replacement_key(value: str) -> str | None:
    prefix, suffix = value.split(':', 1)
    if prefix.isdigit() and suffix.isdigit():
        return value if int(prefix) > _RESERVED_ASSET_TYPE_ID_MAX else None
    if prefix == 'TexturePack' and suffix.isdigit():
        return value
    return None


def _parse_replacement_keys(values: ReplacementRules) -> list[ReplacementKey]:
    parsed_ids: list[ReplacementKey] = []
    for value in values:
        if isinstance(value, str) and ':' in value:
            if qualified_key := _parse_qualified_replacement_key(value):
                parsed_ids.append(qualified_key)
            continue

        numeric_id = _parse_config_asset_id(value)
        if numeric_id is not None:
            if not 1 <= numeric_id <= _RESERVED_ASSET_TYPE_ID_MAX:
                parsed_ids.append(numeric_id)
            continue

        if isinstance(value, str):
            value_lower = value.lower()
            if value_lower in _VIRTUAL_ANIM_TYPES:
                parsed_ids.append(_VIRTUAL_ANIM_TYPES[value_lower])
            elif value_lower in _ASSET_TYPE_IDS:
                parsed_ids.append(_ASSET_TYPE_IDS[value_lower])
    return parsed_ids


def _replacement_mode(rule: JsonObject) -> JsonValue:
    mode = rule.get('mode', 'id')
    if 'remove' in rule and 'mode' not in rule:
        return 'remove' if rule.get('remove') else 'id'
    return mode


def _apply_replacement_rule(rule: JsonObject, targets: ReplacementMaps) -> None:
    replacements, removals, cdn_replacements, local_replacements = targets
    parsed_ids = _parse_replacement_keys(_preserve_replacement_rules(rule.get('replace_ids', [])))
    mode = _replacement_mode(rule)

    if mode == 'remove':
        removals.update(parsed_ids)
        return
    if mode == 'cdn':
        cdn_url = rule.get('cdn_url')
        if cdn_url:
            cdn_replacements.update(dict.fromkeys(parsed_ids, _preserve_runtime_type(cdn_url, str)))
        else:
            removals.update(parsed_ids)
        return
    if mode == 'local':
        local_path = rule.get('local_path')
        if local_path:
            resolved_path = str(resolve_local_replacement_path(_preserve_path_value(local_path)))
            local_replacements.update(dict.fromkeys(parsed_ids, resolved_path))
        else:
            removals.update(parsed_ids)
        return
    if mode != 'id':
        return

    target = rule.get('with_id')
    if target is None:
        removals.update(parsed_ids)
        return
    target_id = _parse_config_asset_id(target)
    if target_id is None or 0 <= target_id <= _RESERVED_ASSET_TYPE_ID_MAX:
        return
    replacements.update(dict.fromkeys(parsed_ids, target_id))


ConfigFileStatus = Literal['valid', 'invalid', 'binary', 'unreadable']


@dataclass(frozen=True)
class ConfigFileInspection:
    """Result of inspecting a candidate file for import into the configs folder."""

    status: ConfigFileStatus
    data: JsonObject | None = None


def _looks_like_utf16_or_utf32(raw: bytes) -> bool:
    """Return whether NUL placement is consistent with a text Unicode encoding."""
    sample = raw[:8192]
    if not sample or b'\x00' not in sample:
        return False
    if sample.startswith((b'\xff\xfe', b'\xfe\xff', b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        return True

    nul_positions = [index for index, value in enumerate(sample) if value == 0]
    if len(nul_positions) < max(2, len(sample) // 8):
        return False
    even_nuls = sum(index % 2 == 0 for index in nul_positions)
    odd_nuls = len(nul_positions) - even_nuls
    return max(even_nuls, odd_nuls) >= len(nul_positions) * 0.8


def _is_probably_binary(raw: bytes) -> bool:
    """Use conservative content heuristics to distinguish binary from invalid text."""
    if not raw or _looks_like_utf16_or_utf32(raw):
        return False

    sample = raw[:8192]
    if b'\x00' in sample:
        return True

    try:
        sample.decode('utf-8')
    except UnicodeDecodeError:
        pass
    else:
        return False

    replacement_count = sample.decode('utf-8', errors='replace').count('\ufffd')
    control_count = sum(
        (value < _CONTROL_BYTE_LIMIT and value not in _TEXT_CONTROL_BYTES) or value == _DELETE_BYTE
        for value in sample
    )
    return (
        control_count / len(sample) > _BINARY_CONTROL_RATIO
        or replacement_count / len(sample) > _BINARY_REPLACEMENT_RATIO
    )


DEFAULT_SETTINGS: JsonObject = {
    'strip_textures': False,
    'enabled_configs': [],
    'last_config': 'Default',
    'theme': 'System',  # System, Light, Dark
    'language': 'en',
    'audio_volume': 70,  # 0-100
    'always_on_top': False,
    'open_dashboard_on_launch': True,
    'first_time_setup_complete': False,
    'auto_delete_cache_on_exit': True,
    'clear_cache_on_launch': True,
    'proxy_features_enabled': True,
    'proxy_mode': 'env',
    # Linux client selection is registry-backed. Sober is the only registered
    # implementation today; ``auto`` leaves room for future backends.
    'linux_client': 'auto',
    'env_proxy_migration_v1_complete': False,
    'lock_roblox_files_read_only': False,
    'read_only_lock_migration_v1_complete': False,
    'close_env_proxy_roblox_on_exit': True,
    'custom_fflags_enabled': False,
    'custom_fflags_warning_accepted': False,
    'custom_fflags': {},
    # Per-platform UI state. Keeping this separate from custom_fflags means a
    # disabled flag retains its chosen value and can be restored by a hotkey.
    'custom_fflag_disabled': [],
    'custom_fflag_keybinds': {},
    'linux_fflag_keybind_setup_prompted': False,
    'macos_auth_source': '',
    'upstream_transport_mode': 'auto',
    'upstream_http_connect_host': '',
    'upstream_http_connect_port': 0,
    'upstream_http_connect_username': '',
    'upstream_http_connect_password': '',
    'upstream_socks5_host': '',
    'upstream_socks5_port': 0,
    'upstream_socks5_username': '',
    'upstream_socks5_password': '',
    'wire_preserving_passthrough': False,
    'vpn_compat_max_assetdelivery_connections': 16,
    'vpn_compat_max_cdn_connections': 32,
    'run_on_boot': True,
    'desktop_integration': True,
    'close_to_tray': True,
    'close_scraped_games_menu_on_open': True,
    'close_viewer_on_replace': True,
    'show_replacer_notifications': True,
    'multi_instance_launching': False,
    'export_naming': ['name', 'id'],
    # Scraper tab - column visibility
    'scraper_column_visibility': {
        'hash_name': True,
        'creator': False,
        'asset_id': True,
        'type': True,
        'size': True,
        'cached_at': True,
        'url': False,
    },
    'scraper_column_widths': {},
    'proxy_traffic_column_widths': {},
    'proxy_traffic_preserve': False,
    'auto_replace_rules': [],
    'auto_replace_rules_column_widths': {},
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


def _json_loads(raw: bytes | str) -> JsonValue:
    return json.loads(raw)


def _write_json(path: Path, data: JsonValue) -> None:
    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def _normalize_custom_fflags(value: object) -> dict[str, str]:
    if not _is_object_dict(value):
        return {}
    normalized: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name).strip()
        if not name:
            continue
        if isinstance(raw_value, bool):
            normalized[name] = 'True' if raw_value else 'False'
        elif isinstance(raw_value, str):
            normalized[name] = raw_value
        elif isinstance(raw_value, int | float):
            normalized[name] = str(raw_value)
    return normalized


def _normalize_custom_fflag_disabled(value: object) -> list[str]:
    if not _is_object_collection(value):
        return []
    return sorted({str(name).strip() for name in value if str(name).strip()}, key=str.casefold)


def _valid_scan_code(value: object, maximum: int) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= maximum


def _normalize_mouse_wheel_binding(
    platform: object,
    direction: object,
    modifiers: int,
) -> dict[str, int | str] | None:
    if platform not in _MOUSE_WHEEL_PLATFORMS or direction not in _MOUSE_WHEEL_DIRECTIONS:
        return None
    return {
        'platform': _preserve_runtime_type(platform, str),
        'kind': 'mouse_wheel',
        'direction': _preserve_runtime_type(direction, str),
        'modifiers': modifiers,
    }


def _normalize_linux_binding(
    scan_code: object,
    kind: object,
    modifiers: int,
) -> dict[str, int | str] | None:
    if not _valid_scan_code(scan_code, _LINUX_MAX_SCAN_CODE) or kind not in _HOTKEY_KINDS:
        return None
    if kind == 'mouse_button' and scan_code not in _LINUX_MOUSE_BUTTON_CODES:
        return None
    return {
        'platform': 'linux_evdev',
        **({'kind': 'mouse_button'} if kind == 'mouse_button' else {}),
        'scan_code': scan_code,
        'modifiers': modifiers,
    }


def _normalize_windows_binding(
    scan_code: object,
    kind: object,
    extended: object,
    modifiers: int,
    platform: object,
) -> dict[str, int | bool | str] | None:
    if not isinstance(extended, bool):
        return None
    if not _valid_scan_code(scan_code, _WINDOWS_MAX_SCAN_CODE) or kind not in _HOTKEY_KINDS:
        return None
    if kind == 'mouse_button' and scan_code not in _WINDOWS_MOUSE_BUTTON_CODES:
        return None
    return {
        **({'platform': 'windows'} if platform == 'windows' else {}),
        **({'kind': 'mouse_button'} if kind == 'mouse_button' else {}),
        'scan_code': scan_code,
        'extended': extended,
        'modifiers': modifiers,
    }


def _normalize_custom_fflag_keybinds(
    value: object,
) -> dict[str, dict[str, int | bool | str]]:
    """Keep valid physical scan-code bindings for the platform hotkey services."""
    if not _is_object_dict(value):
        return {}

    normalized: dict[str, dict[str, int | bool | str]] = {}
    for raw_name, raw_binding in value.items():
        name = str(raw_name).strip()
        if not name or not _is_object_dict(raw_binding):
            continue
        scan_code = raw_binding.get('scan_code')
        modifiers = raw_binding.get('modifiers', 0)
        platform = raw_binding.get('platform')
        kind = raw_binding.get('kind', 'key')
        extended = raw_binding.get('extended', False)
        if not isinstance(modifiers, int) or isinstance(modifiers, bool) or modifiers & ~0x0F:
            continue
        binding: dict[str, int | bool | str] | None = None
        if kind == 'mouse_wheel':
            binding = _normalize_mouse_wheel_binding(
                platform,
                raw_binding.get('direction'),
                modifiers,
            )
        elif platform == 'linux_evdev':
            binding = _normalize_linux_binding(scan_code, kind, modifiers)
        elif platform in _WINDOWS_HOTKEY_PLATFORMS:
            # Untagged bindings are the Windows format used before platform
            # tagging was added, so preserve them for existing users.
            binding = _normalize_windows_binding(scan_code, kind, extended, modifiers, platform)
        if binding is not None:
            normalized[name] = binding
    return normalized


class ConfigManager:
    """Manages application settings and replacement configurations."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._config_names_cache: list[str] | None = None
        self._config_names_signature: tuple[tuple[str, int, int], ...] | None = None
        self._config_data_cache: dict[str, tuple[FileSignature, JsonObject]] = {}
        self._all_replacements_cache_signature: ReplacementsSignature | None = None
        self._all_replacements_cache: ReplacementMaps | None = None
        self._replacements_generation = 0
        self.settings = self._load_settings()
        self._ensure_default_config()
        self.reconcile_configs(save=False)

    @staticmethod
    def _file_signature(path: Path) -> FileSignature:
        try:
            stat_result = path.stat()
        except OSError:
            return None
        return stat_result.st_mtime_ns, stat_result.st_size

    @staticmethod
    def _scan_config_files() -> tuple[list[str], tuple[tuple[str, int, int], ...]]:
        CONFIGS_FOLDER.mkdir(parents=True, exist_ok=True)
        entries: list[tuple[str, str, int, int]] = []
        for path in CONFIGS_FOLDER.glob('*.json'):
            try:
                stat_result = path.stat()
            except OSError:
                continue
            entries.append((path.stem, path.name, stat_result.st_mtime_ns, stat_result.st_size))
        entries.sort(key=itemgetter(0))
        return [name for name, *_ in entries], tuple(
            (filename, mtime, size) for _, filename, mtime, size in entries
        )

    def _mark_replacements_dirty(self) -> None:
        with self._lock:
            self._all_replacements_cache_signature = None
            self._all_replacements_cache = None
            self._replacements_generation += 1

    @property
    def replacements_generation(self) -> int:
        """Monotonic version used to invalidate in-flight proxy routes."""
        with self._lock:
            return self._replacements_generation

    def invalidate_replacements_cache(self) -> None:
        """Invalidate resolved replacement mappings after an external asset change."""
        self._mark_replacements_dirty()

    def _refresh_config_names_cache(self) -> list[str]:
        names, signature = self._scan_config_files()
        if signature != self._config_names_signature:
            live_names = set(names)
            self._config_data_cache = {
                name: cached
                for name, cached in self._config_data_cache.items()
                if name in live_names
            }
            self._config_names_cache = names
            self._config_names_signature = signature
            self._mark_replacements_dirty()
        return list(self._config_names_cache or [])

    def _load_settings(self) -> JsonObject:
        """Load settings from disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIGS_FOLDER.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                loaded = _preserve_json_object(self._load_json_file(Path(CONFIG_FILE)))
                settings = self._settings_from_loaded(loaded)
            except json.JSONDecodeError, OSError, UnicodeDecodeError:
                pass
            else:
                return settings
        return deepcopy(DEFAULT_SETTINGS)

    def _settings_from_loaded(self, loaded: JsonObject) -> JsonObject:
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

    @staticmethod
    def _migrate_old_format(old_config: JsonObject) -> None:
        """Migrate old config format to new format."""
        configs = _preserve_json_object(old_config.get('configs', {}))
        for name, data in configs.items():
            config_path = CONFIGS_FOLDER / f'{name}.json'
            if not config_path.exists():
                with suppress(OSError):
                    _write_json(Path(config_path), data)

    def _ensure_default_config(self) -> None:
        """Ensure at least one default config exists."""
        if not self.config_names:
            self._save_config('Default', {'replacement_rules': []})

    def _save_settings(self) -> None:
        """Save settings to disk."""
        with self._lock:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _write_json(Path(CONFIG_FILE), self.settings)

    @staticmethod
    def _get_config_path(name: str) -> Path:
        """Get the path for a config file."""
        return CONFIGS_FOLDER / f'{name}.json'

    @staticmethod
    def _clear_read_only(path: Path) -> None:
        """Clear the read-only attribute on an existing file."""
        if not path.exists():
            return
        with suppress(OSError):
            path.chmod(path.stat().st_mode | stat.S_IWRITE)

    @staticmethod
    def _fallback_json_encodings() -> tuple[str, ...]:
        """Return legacy text encodings to try after strict JSON decoding fails."""
        preferred = locale.getpreferredencoding(do_setlocale=False)
        encodings: list[str] = []
        seen: set[str] = set()
        for encoding in (*_FALLBACK_JSON_ENCODINGS, preferred):
            normalized = encoding.lower() if encoding else ''
            if normalized and normalized not in seen:
                encodings.append(encoding)
                seen.add(normalized)
        return tuple(encodings)

    def _decode_json_bytes(self, raw: bytes) -> tuple[JsonValue, bool]:
        """Decode JSON bytes and report whether a fallback text encoding was used."""
        decode_error: UnicodeDecodeError | None = None
        json_error: json.JSONDecodeError | None = None

        try:
            return _json_loads(raw), False
        except UnicodeDecodeError as exc:
            decode_error = exc
        except json.JSONDecodeError as exc:
            json_error = exc

        for encoding in self._fallback_json_encodings():
            try:
                text = raw.decode(encoding)
                loaded = _json_loads(text)
            except LookupError, UnicodeDecodeError, json.JSONDecodeError:
                continue
            return loaded, True

        if decode_error is not None:
            raise decode_error
        if json_error is not None:
            raise json_error
        return _json_loads(raw), False

    def _load_json_file(self, path: Path) -> JsonValue:
        """Load JSON and recover legacy non-UTF files when possible."""
        raw = path.read_bytes()
        loaded, recovered = self._decode_json_bytes(raw)

        # Normalize recovered configs back to UTF-8 JSON so future launches
        # do not depend on locale-specific decoding.
        if recovered:
            try:
                self._clear_read_only(path)
                _write_json(path, loaded)
            except OSError:
                pass
        return loaded

    def inspect_config_file(self, path: Path) -> ConfigFileInspection:
        """Inspect an external file without modifying it or requiring a .json suffix."""
        path = Path(path)
        try:
            raw = path.read_bytes()
        except OSError:
            return ConfigFileInspection('unreadable')

        try:
            loaded, _recovered = self._decode_json_bytes(raw)
        except UnicodeDecodeError, json.JSONDecodeError:
            if _is_probably_binary(raw):
                return ConfigFileInspection('binary')
            return ConfigFileInspection('invalid')

        # The existing loader accepts both the current object form and the
        # legacy root-list form. Scalar JSON values are not Fleasion configs.
        if not isinstance(loaded, dict | list):
            return ConfigFileInspection('invalid')
        return ConfigFileInspection('valid', self._normalize_config_data(loaded))

    @staticmethod
    def config_import_destination(path: Path) -> Path:
        """Return the same path with its final extension normalized to .json."""
        path = Path(path)
        if path.suffix:
            return path.with_suffix('.json')
        return path.with_name(f'{path.name}.json')

    def import_config_file(self, path: Path) -> Path:
        """Safely adopt an inspected external config and invalidate config caches."""
        path = Path(path)
        destination = self.config_import_destination(path)
        same_file = False
        if destination.exists():
            try:
                same_file = path.samefile(destination)
            except OSError:
                same_file = path == destination
            if not same_file:
                raise FileExistsError(destination)

        if (
            same_file
            and path.name != destination.name
            and path.name.casefold() == destination.name.casefold()
        ) or (not same_file and path != destination):
            path.rename(destination)
        with self._lock:
            self._config_names_cache = None
            self._config_names_signature = None
            self._mark_replacements_dirty()
        return destination

    @staticmethod
    def _normalize_config_data(data: JsonValue) -> JsonObject:
        """Return a valid config object from decoded JSON data."""
        if isinstance(data, dict):
            rules = data.get('replacement_rules', [])
            if not isinstance(rules, list):
                data = data.copy()
                data['replacement_rules'] = []
            return data
        if isinstance(data, list):
            return {'replacement_rules': data}
        return {'replacement_rules': []}

    def _load_config(self, name: str) -> JsonObject:
        """Load a config from disk."""
        path = self._get_config_path(name)
        signature = self._file_signature(path)
        cached = self._config_data_cache.get(name)
        if cached is not None and cached[0] == signature:
            return cached[1]

        if path.exists():
            try:
                self._clear_read_only(path)
                loaded = self._normalize_config_data(self._load_json_file(Path(path)))
                self._config_data_cache[name] = (self._file_signature(path), loaded)
            except json.JSONDecodeError, OSError, UnicodeDecodeError:
                pass
            else:
                return loaded
        loaded: JsonObject = {'replacement_rules': []}
        self._config_data_cache[name] = (signature, loaded)
        return loaded

    def _save_config(self, name: str, data: JsonObject) -> None:
        """Save a config to disk."""
        with self._lock:
            CONFIGS_FOLDER.mkdir(parents=True, exist_ok=True)
            path = self._get_config_path(name)
            self._clear_read_only(path)
            _write_json(Path(path), data)
            self._config_data_cache[name] = (self._file_signature(path), data)
            self._config_names_cache = None
            self._config_names_signature = None
            self._mark_replacements_dirty()

    @property
    def strip_textures(self) -> bool:
        """Get strip textures setting."""
        return _preserve_runtime_type(self.settings.get('strip_textures', False), bool)

    @strip_textures.setter
    def strip_textures(self, value: bool) -> None:
        """Set strip textures setting."""
        self.settings['strip_textures'] = value
        self._save_settings()

    @property
    def theme(self) -> str:
        """Get theme setting."""
        return _preserve_runtime_type(self.settings.get('theme', 'System'), str)

    @theme.setter
    def theme(self, value: str) -> None:
        """Set theme setting."""
        self.settings['theme'] = value
        self._save_settings()

    @property
    def language(self) -> str:
        """Return a supported language code, falling back to English."""
        normalize_language = importlib.import_module('fleasion.localization').normalize_language
        return normalize_language(_preserve_runtime_type(self.settings.get('language', 'en'), str))

    @language.setter
    def language(self, value: str) -> None:
        """Persist a supported language code, using English for invalid values."""
        normalize_language = importlib.import_module('fleasion.localization').normalize_language
        self.settings['language'] = normalize_language(value)
        self._save_settings()

    @property
    def audio_volume(self) -> int:
        """Get audio volume setting (0-100)."""
        return _preserve_runtime_type(self.settings.get('audio_volume', 70), int)

    @audio_volume.setter
    def audio_volume(self, value: int) -> None:
        """Set audio volume setting (0-100)."""
        self.settings['audio_volume'] = max(0, min(100, value))
        self._save_settings()

    @property
    def always_on_top(self) -> bool:
        """Get always on top setting."""
        return _preserve_runtime_type(self.settings.get('always_on_top', False), bool)

    @always_on_top.setter
    def always_on_top(self, value: bool) -> None:
        """Set always on top setting."""
        self.settings['always_on_top'] = value
        self._save_settings()

    @property
    def open_dashboard_on_launch(self) -> bool:
        """Get open dashboard on launch setting."""
        return _preserve_runtime_type(self.settings.get('open_dashboard_on_launch', True), bool)

    @open_dashboard_on_launch.setter
    def open_dashboard_on_launch(self, value: bool) -> None:
        """Set open dashboard on launch setting."""
        self.settings['open_dashboard_on_launch'] = value
        self._save_settings()

    @property
    def first_time_setup_complete(self) -> bool:
        """Get first time setup complete flag."""
        return _preserve_runtime_type(self.settings.get('first_time_setup_complete', False), bool)

    @first_time_setup_complete.setter
    def first_time_setup_complete(self, value: bool) -> None:
        """Set first time setup complete flag."""
        self.settings['first_time_setup_complete'] = value
        self._save_settings()

    @property
    def auto_delete_cache_on_exit(self) -> bool:
        """Get auto delete cache on Roblox exit setting."""
        return _preserve_runtime_type(self.settings.get('auto_delete_cache_on_exit', True), bool)

    @auto_delete_cache_on_exit.setter
    def auto_delete_cache_on_exit(self, value: bool) -> None:
        """Set auto delete cache on Roblox exit setting."""
        self.settings['auto_delete_cache_on_exit'] = value
        self._save_settings()

    @property
    def clear_cache_on_launch(self) -> bool:
        """Get clear cache on launch setting."""
        return _preserve_runtime_type(self.settings.get('clear_cache_on_launch', True), bool)

    @clear_cache_on_launch.setter
    def clear_cache_on_launch(self, value: bool) -> None:
        """Set clear cache on launch setting."""
        self.settings['clear_cache_on_launch'] = value
        self._save_settings()

    @property
    def proxy_features_enabled(self) -> bool:
        """Get proxy feature toggle."""
        return _preserve_runtime_type(self.settings.get('proxy_features_enabled', True), bool)

    @proxy_features_enabled.setter
    def proxy_features_enabled(self, value: bool) -> None:
        """Set proxy feature toggle."""
        self.settings['proxy_features_enabled'] = value
        self._save_settings()

    @property
    def proxy_mode(self) -> str:
        """How Roblox traffic is routed into Fleasion's local proxy."""
        mode = str(self.settings.get('proxy_mode', 'env') or 'env').lower()
        return mode if mode in {'hosts', 'env'} else 'env'

    @proxy_mode.setter
    def proxy_mode(self, value: str) -> None:
        value = str(value or 'env').lower()
        self.settings['proxy_mode'] = value if value in {'hosts', 'env'} else 'env'
        self._save_settings()

    @property
    def linux_client(self) -> str:
        """Preferred registered Linux Roblox client."""
        return _normalise_linux_client(self.settings.get('linux_client', 'auto'))

    @linux_client.setter
    def linux_client(self, value: str) -> None:
        self.settings['linux_client'] = _normalise_linux_client(value)
        self._save_settings()

    @property
    def env_proxy_migration_v1_complete(self) -> bool:
        """Whether the one-time Env Proxy default migration was acknowledged."""
        return bool(self.settings.get('env_proxy_migration_v1_complete', False))

    @env_proxy_migration_v1_complete.setter
    def env_proxy_migration_v1_complete(self, value: bool) -> None:
        self.settings['env_proxy_migration_v1_complete'] = bool(value)
        self._save_settings()

    @property
    def lock_roblox_files_read_only(self) -> bool:
        """Whether active modification targets should remain read-only."""
        return bool(self.settings.get('lock_roblox_files_read_only', False))

    @lock_roblox_files_read_only.setter
    def lock_roblox_files_read_only(self, value: bool) -> None:
        self.settings['lock_roblox_files_read_only'] = bool(value)
        self._save_settings()

    @property
    def read_only_lock_migration_v1_complete(self) -> bool:
        return bool(self.settings.get('read_only_lock_migration_v1_complete', False))

    @read_only_lock_migration_v1_complete.setter
    def read_only_lock_migration_v1_complete(self, value: bool) -> None:
        self.settings['read_only_lock_migration_v1_complete'] = bool(value)
        self._save_settings()

    @property
    def close_env_proxy_roblox_on_exit(self) -> bool:
        """Whether Fleasion should close its Env-proxied Player on exit."""
        return bool(self.settings.get('close_env_proxy_roblox_on_exit', True))

    @close_env_proxy_roblox_on_exit.setter
    def close_env_proxy_roblox_on_exit(self, value: bool) -> None:
        self.settings['close_env_proxy_roblox_on_exit'] = bool(value)
        self._save_settings()

    @property
    def custom_fflags_enabled(self) -> bool:
        """Whether remote ClientSettings responses should receive custom overrides."""
        return bool(self.settings.get('custom_fflags_enabled', False))

    @custom_fflags_enabled.setter
    def custom_fflags_enabled(self, value: bool) -> None:
        self.settings['custom_fflags_enabled'] = bool(value)
        self._save_settings()

    @property
    def custom_fflags_warning_accepted(self) -> bool:
        """Whether the one-time custom FastFlag risk warning was accepted."""
        return bool(self.settings.get('custom_fflags_warning_accepted', False))

    @custom_fflags_warning_accepted.setter
    def custom_fflags_warning_accepted(self, value: bool) -> None:
        self.settings['custom_fflags_warning_accepted'] = bool(value)
        self._save_settings()

    @property
    def custom_fflags(self) -> dict[str, str]:
        """Return a normalized copy of the saved custom FastFlags."""
        return _normalize_custom_fflags(self.settings.get('custom_fflags', {}))

    @custom_fflags.setter
    def custom_fflags(self, value: object) -> None:
        self.settings['custom_fflags'] = _preserve_json_value(_normalize_custom_fflags(value))
        self._save_settings()

    @property
    def custom_fflag_disabled(self) -> list[str]:
        """Names of custom FastFlags temporarily disabled by the Windows manager."""
        return _normalize_custom_fflag_disabled(self.settings.get('custom_fflag_disabled', []))

    @custom_fflag_disabled.setter
    def custom_fflag_disabled(self, value: object) -> None:
        self.settings['custom_fflag_disabled'] = _preserve_json_value(
            _normalize_custom_fflag_disabled(value)
        )
        self._save_settings()

    @property
    def custom_fflag_keybinds(self) -> dict[str, dict[str, int | bool | str]]:
        """Platform global hotkeys keyed by custom FastFlag name."""
        return _normalize_custom_fflag_keybinds(self.settings.get('custom_fflag_keybinds', {}))

    @custom_fflag_keybinds.setter
    def custom_fflag_keybinds(self, value: object) -> None:
        self.settings['custom_fflag_keybinds'] = _preserve_json_value(
            _normalize_custom_fflag_keybinds(value)
        )
        self._save_settings()

    @property
    def linux_fflag_keybind_setup_prompted(self) -> bool:
        """Whether the on-demand Linux keybind permission setup was shown."""
        return bool(self.settings.get('linux_fflag_keybind_setup_prompted', False))

    @linux_fflag_keybind_setup_prompted.setter
    def linux_fflag_keybind_setup_prompted(self, value: bool) -> None:
        self.settings['linux_fflag_keybind_setup_prompted'] = bool(value)
        self._save_settings()

    @property
    def macos_auth_source(self) -> str:
        value = str(self.settings.get('macos_auth_source', '') or '')
        valid = {
            '',
            'manual',
            'Chrome',
            'Safari',
            'Firefox',
            'Brave',
            'Edge',
            'Chromium',
            'Opera',
            'Vivaldi',
        }
        return value if value in valid else ''

    @macos_auth_source.setter
    def macos_auth_source(self, value: str) -> None:
        value = str(value or '')
        valid = {
            '',
            'manual',
            'Chrome',
            'Safari',
            'Firefox',
            'Brave',
            'Edge',
            'Chromium',
            'Opera',
            'Vivaldi',
        }
        self.settings['macos_auth_source'] = value if value in valid else ''
        self._save_settings()

    @property
    def upstream_transport_mode(self) -> str:
        mode = str(self.settings.get('upstream_transport_mode', 'auto') or 'auto').lower()
        valid = {'auto', 'direct_ip', 'system_proxy', 'http_connect', 'socks5'}
        return mode if mode in valid else 'auto'

    @upstream_transport_mode.setter
    def upstream_transport_mode(self, value: str) -> None:
        value = str(value or 'auto').lower()
        self.settings['upstream_transport_mode'] = (
            value
            if value in {'auto', 'direct_ip', 'system_proxy', 'http_connect', 'socks5'}
            else 'auto'
        )
        self._save_settings()

    @property
    def wire_preserving_passthrough(self) -> bool:
        value = self.settings.get('wire_preserving_passthrough', False)
        if isinstance(value, str):
            return value.strip().lower() in {'1', 'true', 'yes', 'on'}
        return bool(value)

    @wire_preserving_passthrough.setter
    def wire_preserving_passthrough(self, value: bool) -> None:
        self.settings['wire_preserving_passthrough'] = bool(value)
        self._save_settings()

    @property
    def upstream_http_connect_host(self) -> str:
        return str(self.settings.get('upstream_http_connect_host', '') or '')

    @upstream_http_connect_host.setter
    def upstream_http_connect_host(self, value: str) -> None:
        self.settings['upstream_http_connect_host'] = str(value or '').strip()
        self._save_settings()

    @property
    def upstream_http_connect_port(self) -> int:
        try:
            return max(
                0,
                min(
                    65535,
                    int(
                        _preserve_int_convertible(
                            self.settings.get('upstream_http_connect_port', 0) or 0
                        )
                    ),
                ),
            )
        except TypeError, ValueError:
            return 0

    @upstream_http_connect_port.setter
    def upstream_http_connect_port(self, value: int) -> None:
        self.settings['upstream_http_connect_port'] = max(0, min(65535, int(value or 0)))
        self._save_settings()

    @property
    def upstream_http_connect_username(self) -> str:
        return str(self.settings.get('upstream_http_connect_username', '') or '')

    @upstream_http_connect_username.setter
    def upstream_http_connect_username(self, value: str) -> None:
        self.settings['upstream_http_connect_username'] = str(value or '')
        self._save_settings()

    @property
    def upstream_http_connect_password(self) -> str:
        return str(self.settings.get('upstream_http_connect_password', '') or '')

    @upstream_http_connect_password.setter
    def upstream_http_connect_password(self, value: str) -> None:
        self.settings['upstream_http_connect_password'] = str(value or '')
        self._save_settings()

    @property
    def upstream_socks5_host(self) -> str:
        return str(self.settings.get('upstream_socks5_host', '') or '')

    @upstream_socks5_host.setter
    def upstream_socks5_host(self, value: str) -> None:
        self.settings['upstream_socks5_host'] = str(value or '').strip()
        self._save_settings()

    @property
    def upstream_socks5_port(self) -> int:
        try:
            return max(
                0,
                min(
                    65535,
                    int(
                        _preserve_int_convertible(self.settings.get('upstream_socks5_port', 0) or 0)
                    ),
                ),
            )
        except TypeError, ValueError:
            return 0

    @upstream_socks5_port.setter
    def upstream_socks5_port(self, value: int) -> None:
        self.settings['upstream_socks5_port'] = max(0, min(65535, int(value or 0)))
        self._save_settings()

    @property
    def upstream_socks5_username(self) -> str:
        return str(self.settings.get('upstream_socks5_username', '') or '')

    @upstream_socks5_username.setter
    def upstream_socks5_username(self, value: str) -> None:
        self.settings['upstream_socks5_username'] = str(value or '')
        self._save_settings()

    @property
    def upstream_socks5_password(self) -> str:
        return str(self.settings.get('upstream_socks5_password', '') or '')

    @upstream_socks5_password.setter
    def upstream_socks5_password(self, value: str) -> None:
        self.settings['upstream_socks5_password'] = str(value or '')
        self._save_settings()

    @property
    def vpn_compat_max_assetdelivery_connections(self) -> int:
        try:
            return max(
                1,
                min(
                    128,
                    int(
                        _preserve_int_convertible(
                            self.settings.get('vpn_compat_max_assetdelivery_connections', 16) or 16
                        )
                    ),
                ),
            )
        except TypeError, ValueError:
            return 16

    @vpn_compat_max_assetdelivery_connections.setter
    def vpn_compat_max_assetdelivery_connections(self, value: int) -> None:
        self.settings['vpn_compat_max_assetdelivery_connections'] = max(
            1, min(128, int(value or 16))
        )
        self._save_settings()

    @property
    def vpn_compat_max_cdn_connections(self) -> int:
        try:
            return max(
                1,
                min(
                    256,
                    int(
                        _preserve_int_convertible(
                            self.settings.get('vpn_compat_max_cdn_connections', 32) or 32
                        )
                    ),
                ),
            )
        except TypeError, ValueError:
            return 32

    @vpn_compat_max_cdn_connections.setter
    def vpn_compat_max_cdn_connections(self, value: int) -> None:
        self.settings['vpn_compat_max_cdn_connections'] = max(1, min(256, int(value or 32)))
        self._save_settings()

    @property
    def run_on_boot(self) -> bool:
        return _preserve_runtime_type(self.settings.get('run_on_boot', False), bool)

    @run_on_boot.setter
    def run_on_boot(self, value: bool) -> None:
        self.settings['run_on_boot'] = value
        self._save_settings()

    @property
    def desktop_integration(self) -> bool:
        return _preserve_runtime_type(self.settings.get('desktop_integration', True), bool)

    @desktop_integration.setter
    def desktop_integration(self, value: bool) -> None:
        self.settings['desktop_integration'] = value
        self._save_settings()

    @property
    def close_to_tray(self) -> bool:
        """Get close to tray setting."""
        return _preserve_runtime_type(self.settings.get('close_to_tray', True), bool)

    @close_to_tray.setter
    def close_to_tray(self, value: bool) -> None:
        """Set close to tray setting."""
        self.settings['close_to_tray'] = value
        self._save_settings()

    @property
    def multi_instance_launching(self) -> bool:
        """Get multi-instance launching setting."""
        return _preserve_runtime_type(self.settings.get('multi_instance_launching', False), bool)

    @multi_instance_launching.setter
    def multi_instance_launching(self, value: bool) -> None:
        """Set multi-instance launching setting."""
        self.settings['multi_instance_launching'] = value
        self._save_settings()

    @property
    def close_scraped_games_on_open(self) -> bool:
        return _preserve_runtime_type(self.settings.get('close_scraped_games_on_open', True), bool)

    @close_scraped_games_on_open.setter
    def close_scraped_games_on_open(self, value: bool) -> None:
        self.settings['close_scraped_games_on_open'] = value
        self._save_settings()

    @property
    def close_scraped_games_menu_on_open(self) -> bool:
        return _preserve_runtime_type(
            self.settings.get('close_scraped_games_menu_on_open', True), bool
        )

    @close_scraped_games_menu_on_open.setter
    def close_scraped_games_menu_on_open(self, value: bool) -> None:
        self.settings['close_scraped_games_menu_on_open'] = value
        self._save_settings()

    @property
    def close_viewer_on_replace(self) -> bool:
        return _preserve_runtime_type(self.settings.get('close_viewer_on_replace', True), bool)

    @close_viewer_on_replace.setter
    def close_viewer_on_replace(self, value: bool) -> None:
        self.settings['close_viewer_on_replace'] = value
        self._save_settings()

    @property
    def show_replacer_notifications(self) -> bool:
        """Get show replacer notifications setting."""
        return _preserve_runtime_type(self.settings.get('show_replacer_notifications', True), bool)

    @show_replacer_notifications.setter
    def show_replacer_notifications(self, value: bool) -> None:
        """Set show replacer notifications setting."""
        self.settings['show_replacer_notifications'] = value
        self._save_settings()

    @property
    def window_geometry(self) -> str:
        """Get the saved window geometry (hex string)."""
        return _preserve_runtime_type(self.settings.get('window_geometry', ''), str)

    @window_geometry.setter
    def window_geometry(self, value: str) -> None:
        """Set the window geometry."""
        self.settings['window_geometry'] = value
        self._save_settings()

    @property
    def auto_convert_anim_rig(self) -> bool:
        return True

    @auto_convert_anim_rig.setter
    def auto_convert_anim_rig(self, value: bool) -> None:
        self.settings['auto_convert_anim_rig'] = value
        self._save_settings()

    @property
    def skip_non_player_anim_replace(self) -> bool:
        return _preserve_runtime_type(
            self.settings.get('skip_non_player_anim_replace', False), bool
        )

    @skip_non_player_anim_replace.setter
    def skip_non_player_anim_replace(self, value: bool) -> None:
        self.settings['skip_non_player_anim_replace'] = value
        self._save_settings()

    @property
    def scraper_blacklist(self) -> list[str]:
        return _preserve_str_list(self.settings.get('scraper_blacklist', []))

    @scraper_blacklist.setter
    def scraper_blacklist(self, value: list[str]) -> None:
        self.settings['scraper_blacklist'] = _preserve_json_value(value)
        self._save_settings()

    @property
    def subplace_blacklist(self) -> list[str]:
        return _preserve_str_list(self.settings.get('subplace_blacklist', []))

    @subplace_blacklist.setter
    def subplace_blacklist(self, value: list[str]) -> None:
        self.settings['subplace_blacklist'] = _preserve_json_value(value)
        self._save_settings()

    @property
    def subplace_blacklist_mode(self) -> str:
        mode = self.settings.get('subplace_blacklist_mode', 'block')
        return mode if isinstance(mode, str) and mode in {'block', 'stall'} else 'block'

    @subplace_blacklist_mode.setter
    def subplace_blacklist_mode(self, value: str) -> None:
        self.settings['subplace_blacklist_mode'] = value if value in {'block', 'stall'} else 'block'
        self._save_settings()

    @property
    def username_spoofer(self) -> JsonObject:
        default = _preserve_json_object(deepcopy(DEFAULT_SETTINGS.get('username_spoofer', {})))
        saved = self.settings.get('username_spoofer', {})
        if isinstance(saved, dict):
            default.update(saved)
        return default

    @username_spoofer.setter
    def username_spoofer(self, value: object) -> None:
        base = _preserve_json_object(deepcopy(DEFAULT_SETTINGS.get('username_spoofer', {})))
        if _is_object_dict(value):
            base.update(
                {
                    'save_settings': bool(
                        value.get('save_settings', base.get('save_settings', False))
                    ),
                    'others_name': str(value.get('others_name', base.get('others_name', ''))),
                    'others_apply_ingame': bool(
                        value.get(
                            'others_apply_ingame',
                            base.get('others_apply_ingame', False),
                        )
                    ),
                    'others_verified': bool(
                        value.get('others_verified', base.get('others_verified', False))
                    ),
                    'self_name': str(value.get('self_name', base.get('self_name', ''))),
                    'self_apply_ingame': bool(
                        value.get('self_apply_ingame', base.get('self_apply_ingame', False))
                    ),
                    'self_verified': bool(
                        value.get('self_verified', base.get('self_verified', False))
                    ),
                    'self_game_creator': bool(
                        value.get('self_game_creator', base.get('self_game_creator', False))
                    ),
                }
            )
        self.settings['username_spoofer'] = base
        self._save_settings()

    @property
    def show_names(self) -> bool:
        return _preserve_runtime_type(self.settings.get('show_names', True), bool)

    @show_names.setter
    def show_names(self, value: bool) -> None:
        self.settings['show_names'] = value
        self._save_settings()

    @property
    def show_creator_id(self) -> bool:
        return _preserve_runtime_type(self.settings.get('show_creator_id', False), bool)

    @show_creator_id.setter
    def show_creator_id(self, value: bool) -> None:
        self.settings['show_creator_id'] = value
        self._save_settings()

    @property
    def export_naming(self) -> list[str]:
        """Get export naming options (name, id, hash)."""
        return _preserve_str_list(self.settings.get('export_naming', ['name', 'id']))

    @export_naming.setter
    def export_naming(self, value: list[str]) -> None:
        """Set export naming options."""
        self.settings['export_naming'] = _preserve_json_value(value)
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
            for name in _preserve_str_list(self.settings.get('enabled_configs', []))
            if name in current_configs
        ]

    @enabled_configs.setter
    def enabled_configs(self, value: list[str]) -> None:
        """Set list of enabled configs."""
        self.settings['enabled_configs'] = _preserve_json_value(value)
        self._mark_replacements_dirty()
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

    def set_config_enabled(
        self,
        name: str,
        enabled: bool,
    ) -> None:
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

    def reconcile_configs(
        self,
        save: bool = True,
    ) -> bool:
        """Synchronize settings with config files currently on disk.

        Returns True when the active settings changed.
        """
        self._ensure_default_config()
        current_configs = self.config_names
        changed = False

        enabled = _preserve_str_list(self.settings.get('enabled_configs', []))
        cleaned_enabled = [name for name in enabled if name in current_configs]
        if cleaned_enabled != enabled:
            self.settings['enabled_configs'] = _preserve_json_value(cleaned_enabled)
            changed = True

        last_config = _preserve_runtime_type(self.settings.get('last_config', 'Default'), str)
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
        return _preserve_runtime_type(self.settings.get('last_config', 'Default'), str)

    @last_config.setter
    def last_config(self, value: str) -> None:
        """Set the last displayed config."""
        self.settings['last_config'] = value
        self.reconcile_configs(save=False)
        self._save_settings()

    @property
    def configs_folder(self) -> Path:
        """Return the folder this manager currently uses for config files."""
        return Path(CONFIGS_FOLDER)

    @property
    def config_names(self) -> list[str]:
        """Get list of all config names."""
        return self._refresh_config_names_cache()

    def refresh_config_names(self) -> None:
        """Refresh config names from disk (for external changes)."""
        self.reconcile_configs()

    def get_replacement_rules(self, config_name: str) -> ReplacementRules:
        """Get rules for a specific config."""
        return _preserve_replacement_rules(
            self._load_config(config_name).get('replacement_rules', [])
        )

    def set_replacement_rules(self, config_name: str, rules: ReplacementRules) -> None:
        """Set rules for a specific config."""
        config = self._load_config(config_name)
        config['replacement_rules'] = rules
        self._save_config(config_name, config)

    @property
    def replacement_rules(self) -> ReplacementRules:
        """Get rules for the currently displayed (last) config."""
        return self.get_replacement_rules(self.last_config)

    @replacement_rules.setter
    def replacement_rules(self, value: ReplacementRules) -> None:
        """Set rules for the currently displayed (last) config."""
        self.set_replacement_rules(self.last_config, value)

    @property
    def time_wasted_seconds(self) -> int:
        """Get total time wasted in seconds (cumulative across sessions)."""
        return _preserve_runtime_type(self.settings.get('time_wasted_seconds', 0), int)

    @time_wasted_seconds.setter
    def time_wasted_seconds(self, value: int) -> None:
        """Set total time wasted in seconds."""
        self.settings['time_wasted_seconds'] = max(0, int(value))
        self._save_settings()

    def save(self) -> None:
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
            self._delete_config(name)
        except OSError:
            return False
        return True

    def _delete_config(self, name: str) -> None:
        self._get_config_path(name).unlink()
        self._config_data_cache.pop(name, None)
        self._config_names_cache = None
        self._config_names_signature = None
        self._mark_replacements_dirty()
        if name in self.enabled_configs:
            configs = self.enabled_configs.copy()
            configs.remove(name)
            self.enabled_configs = configs
        if self.last_config == name:
            self.settings['last_config'] = self.config_names[0]
            self._save_settings()

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
            self._rename_config(old_name, new_name)
        except OSError:
            return False
        return True

    def _rename_config(self, old_name: str, new_name: str) -> None:
        self._get_config_path(old_name).rename(self._get_config_path(new_name))
        cached = self._config_data_cache.pop(old_name, None)
        if cached is not None:
            self._config_data_cache[new_name] = (
                self._file_signature(self._get_config_path(new_name)),
                cached[1],
            )
        self._config_names_cache = None
        self._config_names_signature = None
        self._mark_replacements_dirty()
        if old_name in self.enabled_configs:
            configs = self.enabled_configs.copy()
            configs.remove(old_name)
            configs.append(new_name)
            self.enabled_configs = configs
        if self.settings['last_config'] == old_name:
            self.settings['last_config'] = new_name
            self._save_settings()

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
    def _iter_replacement_rules(entries: ReplacementRules) -> Iterator[JsonValue]:
        """Yield profile rules depth-first, skipping organizational groups."""
        for entry in entries:
            if isinstance(entry, dict) and entry.get('type') == 'group':
                yield from ConfigManager._iter_replacement_rules(
                    _preserve_replacement_rules(entry.get('children', []))
                )
            else:
                yield entry

    def get_all_replacements(
        self,
    ) -> ReplacementMaps:
        """Get replacements from all enabled configs.

        Returns
        -------
        tuple
            - replacements: dict mapping asset IDs/types to replacement IDs
            - removals: set of asset IDs/types to remove entirely
            - cdn_replacements: dict mapping asset IDs/types to CDN URLs
            - local_replacements: dict mapping asset IDs/types to local file paths

        """
        enabled_configs = tuple(self.enabled_configs)
        signature = tuple(
            (config_name, self._file_signature(self._get_config_path(config_name)))
            for config_name in enabled_configs
        )
        if (
            self._all_replacements_cache is not None
            and self._all_replacements_cache_signature == signature
        ):
            return self._all_replacements_cache

        targets: ReplacementMaps = ({}, set(), {}, {})
        for config_name in enabled_configs:
            for rule in self._iter_replacement_rules(self.get_replacement_rules(config_name)):
                if isinstance(rule, dict) and rule.get('enabled', True):
                    _apply_replacement_rule(rule, targets)

        self._all_replacements_cache_signature = signature
        self._all_replacements_cache = targets
        return self._all_replacements_cache
