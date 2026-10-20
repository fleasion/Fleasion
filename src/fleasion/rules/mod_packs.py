"""Shareable mod packs: replacement configs, their files, filters and game profiles in one file.

A ``.fleasionpack`` file is a zip archive::

    manifest.json        name, author, description, contents
    configs/<n>.json     replacement configs, with pack-relative local paths
    files/<file>         the local replacement files those configs use

Packs come from other people, so reading one never trusts its contents:
member names, sizes, file types, JSON and every setting are validated, and
nothing is written until the user installs the previewed pack.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

from fleasion.rules.asset_filters import FilterSettings, active_steps, normalize_filter_settings
from fleasion.rules.game_profiles import GameProfile, normalize_game_profiles

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from fleasion.utils.json_types import JsonValue

PACK_EXTENSION = '.fleasionpack'
PACK_FORMAT = 1
PACK_FILE_PREFIX = 'pack:'
"""Marks a ``local_path`` that points inside the pack's ``files/`` folder."""

MAX_PACK_FILE_BYTES = 256 * 1024 * 1024
MAX_PACK_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_PACK_MEMBERS = 5000
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_TEXT_LENGTH = 2000

# Replacement files Roblox assets can be swapped for.  Anything else in a pack
# (scripts, executables, archives) is refused.
ALLOWED_FILE_EXTENSIONS = frozenset(
    {
        '.png',
        '.jpg',
        '.jpeg',
        '.webp',
        '.bmp',
        '.tga',
        '.gif',
        '.dds',
        '.ktx',
        '.ktx2',
        '.ogg',
        '.mp3',
        '.wav',
        '.flac',
        '.rbxm',
        '.rbxmx',
        '.mesh',
        '.obj',
        '.fbx',
        '.bin',
        '.ttf',
        '.otf',
    }
)
_UNSAFE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ModPackError(ValueError):
    """The pack is unreadable, unsafe or incomplete."""


class _ConfigStore(Protocol):
    """The parts of ConfigManager that packs read and write."""

    @property
    def config_names(self) -> list[str]: ...

    @property
    def configs_folder(self) -> Path: ...

    @property
    def enabled_configs(self) -> list[str]: ...

    @enabled_configs.setter
    def enabled_configs(self, value: list[str]) -> None: ...

    @property
    def asset_filters(self) -> FilterSettings: ...

    @asset_filters.setter
    def asset_filters(self, value: FilterSettings) -> None: ...

    @property
    def game_profiles(self) -> list[GameProfile]: ...

    @game_profiles.setter
    def game_profiles(self, value: list[GameProfile]) -> None: ...

    def get_replacement_rules(self, config_name: str) -> list[JsonValue]: ...

    def set_replacement_rules(self, config_name: str, rules: list[JsonValue]) -> None: ...

    def create_config(self, name: str) -> bool: ...


class PackManifest(TypedDict):
    format: int
    name: str
    author: str
    description: str
    version: str
    created: int
    configs: list[str]
    filters: FilterSettings | None
    game_profiles: list[GameProfile]


@dataclass
class ExportResult:
    path: Path
    file_count: int
    missing_files: list[str] = field(default_factory=list[str])


@dataclass
class PackPreview:
    """A validated pack, ready to show the user before installing."""

    source: Path
    manifest: PackManifest
    configs: dict[str, list[JsonValue]]
    files: dict[str, int]
    """Pack file name to uncompressed size."""

    @property
    def total_bytes(self) -> int:
        return sum(self.files.values())

    @property
    def rule_count(self) -> int:
        return sum(len(list(_iter_rules(rules))) for rules in self.configs.values())


@dataclass
class InstallResult:
    configs: dict[str, str]
    """Pack config name to the name it was installed as."""
    files_folder: Path
    profiles_added: list[int]
    profiles_skipped: list[int]
    filters_applied: bool


def safe_name(text: str, fallback: str = 'Mod Pack') -> str:
    """A name usable as a file or folder name on every platform."""
    cleaned = _UNSAFE_NAME_CHARS.sub('', text).strip().strip('.')
    return cleaned[:60] or fallback


def _clean_text(value: object, limit: int = MAX_TEXT_LENGTH) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ''


def _iter_rules(rules: Iterable[JsonValue]) -> Iterator[dict[str, JsonValue]]:
    """Yield every replacement rule, descending into groups."""
    for entry in rules:
        if not isinstance(entry, dict):
            continue
        if entry.get('type') == 'group':
            children = entry.get('children')
            if isinstance(children, list):
                yield from _iter_rules(children)
        else:
            yield entry


# Export


def _pack_file_name(source: Path, used: set[str]) -> str:
    digest = hashlib.sha256(str(source).encode('utf-8')).hexdigest()[:8]
    name = f'{digest}-{safe_name(source.stem, "file")}{source.suffix.lower()}'
    while name in used:
        digest = hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]
        name = f'{digest}-{safe_name(source.stem, "file")}{source.suffix.lower()}'
    used.add(name)
    return name


def export_pack(
    store: _ConfigStore,
    config_names: Iterable[str],
    destination: Path,
    *,
    name: str,
    author: str = '',
    description: str = '',
    include_filters: bool = True,
    include_profiles: bool = True,
    resolve_local_path: Callable[[str], Path],
) -> ExportResult:
    """Write the chosen configs, and everything they need, to ``destination``."""
    selected = [config for config in dict.fromkeys(config_names) if config in store.config_names]
    if not selected:
        msg = 'Choose at least one config to put in the pack'
        raise ModPackError(msg)

    files: dict[Path, str] = {}
    used_names: set[str] = set()
    missing: list[str] = []
    configs: dict[str, list[JsonValue]] = {}
    for config in selected:
        rules = json.loads(json.dumps(store.get_replacement_rules(config)))
        for rule in _iter_rules(rules):
            local_path = rule.get('local_path')
            if not isinstance(local_path, str) or not local_path:
                continue
            source = resolve_local_path(local_path)
            if not source.is_file() or source.suffix.lower() not in ALLOWED_FILE_EXTENSIONS:
                missing.append(local_path)
                continue
            if source not in files:
                files[source] = _pack_file_name(source, used_names)
            rule['local_path'] = f'{PACK_FILE_PREFIX}files/{files[source]}'
        configs[config] = rules

    filters = store.asset_filters if include_filters and active_steps(store.asset_filters) else None
    profiles: list[GameProfile] = []
    if include_profiles:
        for profile in store.game_profiles:
            kept = [config for config in profile['configs'] if config in configs]
            if kept or profile['filters'] is not None:
                profiles.append({**profile, 'configs': kept})

    manifest: PackManifest = {
        'format': PACK_FORMAT,
        'name': _clean_text(name, 80) or 'Mod Pack',
        'author': _clean_text(author, 80),
        'description': _clean_text(description),
        'version': '1',
        'created': int(time.time()),
        'configs': selected,
        'filters': filters,
        'game_profiles': profiles,
    }
    destination = destination.with_suffix(PACK_EXTENSION)
    temporary = destination.with_name(f'.{destination.name}.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest, indent=2))
            for index, config in enumerate(selected):
                archive.writestr(
                    f'configs/{index}.json',
                    json.dumps({'name': config, 'replacement_rules': configs[config]}, indent=2),
                )
            for source, pack_name in files.items():
                archive.write(source, f'files/{pack_name}')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return ExportResult(destination, len(files), missing)


# Import


def _member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        msg = f'The pack contains an unsafe path: {name}'
        raise ModPackError(msg)
    return path


def _read_json(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> object:
    if info.file_size > MAX_JSON_BYTES:
        msg = f'{info.filename} is too large'
        raise ModPackError(msg)
    try:
        return json.loads(archive.read(info).decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        msg = f'{info.filename} is not valid JSON'
        raise ModPackError(msg) from exc


def _validate_manifest(raw: object) -> PackManifest:
    if not isinstance(raw, dict):
        msg = 'The pack manifest is not an object'
        raise ModPackError(msg)
    manifest = cast('dict[str, object]', raw)
    pack_format = manifest.get('format')
    if not isinstance(pack_format, int) or pack_format > PACK_FORMAT:
        msg = 'This pack was made by a newer Fleasion; update Fleasion to install it'
        raise ModPackError(msg)
    raw_filters = manifest.get('filters')
    created = manifest.get('created')
    return {
        'format': pack_format,
        'name': _clean_text(manifest.get('name'), 80) or 'Mod Pack',
        'author': _clean_text(manifest.get('author'), 80),
        'description': _clean_text(manifest.get('description')),
        'version': _clean_text(manifest.get('version'), 20),
        'created': created if isinstance(created, int) else 0,
        'configs': [],
        'filters': normalize_filter_settings(raw_filters) if raw_filters is not None else None,
        'game_profiles': normalize_game_profiles(manifest.get('game_profiles')),
    }


def _check_rules(rules: list[JsonValue], files: dict[str, int]) -> None:
    for rule in _iter_rules(rules):
        local_path = rule.get('local_path')
        if not isinstance(local_path, str) or not local_path:
            continue
        if not local_path.startswith(PACK_FILE_PREFIX):
            # A path outside the pack would point at the installer's own disk
            rule['local_path'] = ''
            rule['enabled'] = False
            continue
        member = local_path.removeprefix(f'{PACK_FILE_PREFIX}files/')
        if member not in files:
            msg = f'A config uses {member}, which is missing from the pack'
            raise ModPackError(msg)


def read_pack(path: Path) -> PackPreview:
    """Open and validate a pack without installing anything."""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        msg = 'This is not a Fleasion mod pack'
        raise ModPackError(msg) from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_PACK_MEMBERS:
            msg = 'The pack contains too many files'
            raise ModPackError(msg)
        by_name = {info.filename: info for info in infos if not info.is_dir()}
        manifest_info = by_name.get('manifest.json')
        if manifest_info is None:
            msg = 'This is not a Fleasion mod pack (manifest.json is missing)'
            raise ModPackError(msg)
        manifest = _validate_manifest(_read_json(archive, manifest_info))

        files: dict[str, int] = {}
        config_infos: list[zipfile.ZipInfo] = []
        for name, info in by_name.items():
            member = _member_path(name)
            if member.parts[0] == 'files' and len(member.parts) == 2:
                if member.suffix.lower() not in ALLOWED_FILE_EXTENSIONS:
                    msg = f'The pack contains a file type Fleasion will not install: {member.name}'
                    raise ModPackError(msg)
                if info.file_size > MAX_PACK_FILE_BYTES:
                    msg = f'{member.name} is too large'
                    raise ModPackError(msg)
                files[member.name] = info.file_size
            elif (
                member.parts[0] == 'configs' and len(member.parts) == 2 and member.suffix == '.json'
            ):
                config_infos.append(info)
        if sum(files.values()) > MAX_PACK_TOTAL_BYTES:
            msg = 'The pack is larger than 1 GB'
            raise ModPackError(msg)

        configs: dict[str, list[JsonValue]] = {}
        for info in sorted(config_infos, key=lambda item: item.filename):
            raw = _read_json(archive, info)
            if not isinstance(raw, dict):
                continue
            data = cast('dict[str, object]', raw)
            name = safe_name(_clean_text(data.get('name'), 80), '')
            rules = data.get('replacement_rules')
            if not name or not isinstance(rules, list) or name in configs:
                continue
            rule_list = cast('list[JsonValue]', rules)
            _check_rules(rule_list, files)
            configs[name] = rule_list
    if not configs:
        msg = 'The pack has no replacement configs'
        raise ModPackError(msg)
    manifest['configs'] = list(configs)
    return PackPreview(path, manifest, configs, files)


def _unique_config_name(name: str, taken: set[str], pack_name: str) -> str:
    if name not in taken:
        return name
    candidate = safe_name(f'{name} ({pack_name})', name)
    counter = 2
    while candidate in taken:
        candidate = safe_name(f'{name} ({pack_name} {counter})', name)
        counter += 1
    return candidate


def install_pack(
    preview: PackPreview,
    store: _ConfigStore,
    *,
    enable_configs: bool = True,
    apply_filters: bool = False,
    add_profiles: bool = True,
) -> InstallResult:
    """Install a previewed pack: copy its files and add its configs and profiles."""
    manifest = preview.manifest
    pack_folder = safe_name(f'Pack - {manifest["name"]}')
    files_folder = store.configs_folder / pack_folder
    files_folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(preview.source) as archive:
        for member_name in preview.files:
            target = files_folder / member_name
            with archive.open(f'files/{member_name}') as source:
                data = source.read(MAX_PACK_FILE_BYTES + 1)
            if len(data) > MAX_PACK_FILE_BYTES:
                msg = f'{member_name} is too large'
                raise ModPackError(msg)
            target.write_bytes(data)

    taken = set(store.config_names)
    installed: dict[str, str] = {}
    for config, rules in preview.configs.items():
        local_rules: list[JsonValue] = json.loads(json.dumps(rules))
        for rule in _iter_rules(local_rules):
            local_path = rule.get('local_path')
            if isinstance(local_path, str) and local_path.startswith(PACK_FILE_PREFIX):
                member = local_path.removeprefix(f'{PACK_FILE_PREFIX}files/')
                rule['local_path'] = f'/{pack_folder}/{member}'
        name = _unique_config_name(config, taken, manifest['name'])
        taken.add(name)
        store.create_config(name)
        store.set_replacement_rules(name, local_rules)
        installed[config] = name

    if enable_configs:
        store.enabled_configs = list(dict.fromkeys([*store.enabled_configs, *installed.values()]))

    added: list[int] = []
    skipped: list[int] = []
    if add_profiles and manifest['game_profiles']:
        profiles = store.game_profiles
        existing = {profile['place_id'] for profile in profiles}
        for profile in manifest['game_profiles']:
            if profile['place_id'] in existing:
                skipped.append(profile['place_id'])
                continue
            renamed = [installed[name] for name in profile['configs'] if name in installed]
            profiles.append({**profile, 'configs': renamed})
            added.append(profile['place_id'])
        store.game_profiles = profiles

    filters_applied = False
    if apply_filters and manifest['filters'] is not None:
        store.asset_filters = manifest['filters']
        filters_applied = True
    return InstallResult(installed, files_folder, added, skipped, filters_applied)
