"""Registry and discovery for supported Linux Roblox clients.

Sober, Mocktail and Cordial are the registered Flatpak backends. Keeping each
client's identity, paths, process markers, desktop handlers, and proxy
capabilities in a descriptor provides a small extension seam without spreading
client checks throughout the Linux platform helpers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

ROBLOX_URI_SCHEMES = (
    'x-scheme-handler/roblox',
    'x-scheme-handler/roblox-player',
)


def _resolved(path: str | Path) -> Path:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except OSError:
        return Path(path).expanduser().absolute()


def _is_within(path: str | Path, root: str | Path) -> bool:
    try:
        _resolved(path).relative_to(_resolved(root))
    except ValueError:
        return False
    return True


def _home_path(home: str | Path | None, environ: Mapping[str, str]) -> Path:
    if home is not None:
        return _resolved(home)
    configured = environ.get('HOME', '').strip()
    return _resolved(configured) if configured else _resolved(Path.home())


type ResourceBase = Literal['data', 'config']
"""Which XDG root a client's asset overlay lives under."""

type ProxyIntegration = Literal['flatpak_env', 'mocktail_config']
"""How Fleasion routes a client through its proxy.

``flatpak_env``: a ``flatpak override`` sets the standard proxy variables and
Fleasion's CA is written into each ``ssl/cacert.pem`` the client reads.

``mocktail_config``: the client has its own Fleasion integration. Fleasion
enables it in the client's config and copies its public CA certificate into the
client's sandbox; the client then routes itself and builds its own CA bundle.
"""


def _glob_flatpak_paths(root: Path, patterns: Iterable[str]) -> tuple[Path, ...]:
    """Expand ``patterns`` (relative to ``root``) in pattern order, deduplicated."""
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        try:
            matches = (
                sorted(root.glob(pattern)) if any(c in pattern for c in '*?[') else [root / pattern]
            )
        except OSError, ValueError:
            continue
        for match in matches:
            if match not in seen:
                seen.add(match)
                found.append(match)
    return tuple(found)


@dataclass(frozen=True, slots=True)
class LinuxClientDescriptor:
    """Static Flatpak identity and capabilities for one Linux client."""

    key: str
    display_name: str
    app_id: str
    desktop_ids: tuple[str, ...]
    xdg_namespace: str
    process_names: tuple[str, ...]
    engine_process_names: tuple[str, ...]
    cgroup_marker: str
    config_filename: str
    resource_relative_paths: tuple[Path, ...]
    storage_db_relative_path: Path | None = None
    cache_storage_relative_path: Path | None = None
    proxy_environment_names: tuple[str, ...] = ()
    proxy_passthrough_hosts: frozenset[str] = frozenset()
    clientsettings_route_delay_seconds: float = 0.0
    resource_base: ResourceBase = 'data'
    proxy_integration: ProxyIntegration = 'flatpak_env'
    # The patterns below are relative to the Flatpak root (~/.var/app/<id>)
    # and may contain globs, for clients with one directory per profile
    ca_bundle_dir_patterns: tuple[str, ...] = ()
    """Extra roots whose ``ssl/cacert.pem`` must trust Fleasion's CA."""
    original_asset_dir_patterns: tuple[str, ...] = ()
    """Unmodified extracted copies of the APK's ``assets/`` directory."""
    fflag_file_patterns: tuple[str, ...] = ()
    """Bloxstrap-style flat FastFlag JSON files the client reads at launch."""
    settings_dir_patterns: tuple[str, ...] = ()
    """Directories holding ``GlobalBasicSettings_13.xml`` (default: data root)."""
    storage_path_patterns: tuple[str, ...] = ()
    """Roblox HTTP cache paths removed by Delete Cache, beside ``storage_db``."""

    @property
    def desktop_id(self) -> str:
        return self.desktop_ids[0]

    def paths(
        self,
        *,
        home: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> LinuxClientPaths:
        """Resolve the host-visible Flatpak paths for this descriptor."""
        environment = os.environ if environ is None else environ
        resolved_home = _home_path(home, environment)
        flatpak_root = resolved_home / '.var' / 'app' / self.app_id
        config_root = flatpak_root / 'config' / self.xdg_namespace
        data_root = flatpak_root / 'data' / self.xdg_namespace
        cache_root = flatpak_root / 'cache' / self.xdg_namespace
        return LinuxClientPaths(
            client=self,
            home=resolved_home,
            flatpak_root=flatpak_root,
            config_root=config_root,
            data_root=data_root,
            cache_root=cache_root,
            config_file=config_root / self.config_filename,
            resource_roots=tuple(
                (config_root if self.resource_base == 'config' else data_root) / relative
                for relative in self.resource_relative_paths
            ),
            storage_db=(
                data_root / self.storage_db_relative_path
                if self.storage_db_relative_path is not None
                else None
            ),
            cache_storage_dir=(
                cache_root / self.cache_storage_relative_path
                if self.cache_storage_relative_path is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class LinuxClientPaths:
    """Host-visible paths for one concrete Flatpak client installation."""

    client: LinuxClientDescriptor
    home: Path
    flatpak_root: Path
    config_root: Path
    data_root: Path
    cache_root: Path
    config_file: Path
    resource_roots: tuple[Path, ...]
    storage_db: Path | None
    cache_storage_dir: Path | None

    @property
    def resource_base_root(self) -> Path:
        """The XDG root which must exist before resource roots are created."""
        return self.config_root if self.client.resource_base == 'config' else self.data_root

    def existing_resource_roots(self) -> tuple[Path, ...]:
        return tuple(root for root in self.resource_roots if root.is_dir())

    def ca_bundle_dirs(self) -> tuple[Path, ...]:
        """Existing non-overlay roots whose ``ssl/cacert.pem`` the client reads."""
        return tuple(
            path
            for path in _glob_flatpak_paths(self.flatpak_root, self.client.ca_bundle_dir_patterns)
            if (path / 'ssl' / 'cacert.pem').is_file()
        )

    def original_asset_dirs(self) -> tuple[Path, ...]:
        """Existing extracted ``assets/`` copies to read original files from."""
        return tuple(
            path
            for path in _glob_flatpak_paths(
                self.flatpak_root, self.client.original_asset_dir_patterns
            )
            if path.is_dir()
        )

    def fflag_files(self) -> tuple[Path, ...]:
        """FastFlag files to manage, limited to directories the client created.

        The directory part is globbed so a profile without a flags file yet is
        still included.
        """
        files: list[Path] = []
        for pattern in self.client.fflag_file_patterns:
            directory, _, name = pattern.rpartition('/')
            files.extend(
                parent / name
                for parent in _glob_flatpak_paths(self.flatpak_root, (directory,))
                if parent.is_dir()
            )
        return tuple(files)

    def settings_dirs(self) -> tuple[Path, ...]:
        """Directories holding the client's ``GlobalBasicSettings_13.xml``."""
        if not self.client.settings_dir_patterns:
            return (self.data_root,) if self.data_root.is_dir() else ()
        return tuple(
            path
            for path in _glob_flatpak_paths(self.flatpak_root, self.client.settings_dir_patterns)
            if path.is_dir()
        )

    def storage_paths(self) -> tuple[Path, ...]:
        """Existing Roblox HTTP cache files and folders for Delete Cache."""
        return tuple(
            path
            for path in _glob_flatpak_paths(self.flatpak_root, self.client.storage_path_patterns)
            if path.exists() or path.is_symlink()
        )

    def owns_resource_path(self, path: str | Path) -> bool:
        return any(_is_within(path, root) for root in self.resource_roots)


@dataclass(frozen=True, slots=True)
class LinuxClientInstallation:
    """A detected descriptor paired with its launcher and resolved paths."""

    client: LinuxClientDescriptor
    paths: LinuxClientPaths
    executable: Path | None

    @property
    def key(self) -> str:
        return self.client.key

    @property
    def display_name(self) -> str:
        return self.client.display_name

    @property
    def app_id(self) -> str:
        return self.client.app_id

    @property
    def desktop_id(self) -> str:
        return self.client.desktop_id

    def launch_command(self, uri: str | None = None) -> list[str] | None:
        if self.executable is None:
            return None
        command = [str(self.executable), 'run', self.app_id]
        if uri:
            command.append(uri)
        return command

    def existing_resource_roots(self) -> tuple[Path, ...]:
        return self.paths.existing_resource_roots()

    def owns_resource_path(self, path: str | Path) -> bool:
        return self.paths.owns_resource_path(path)


SOBER_CLIENT = LinuxClientDescriptor(
    key='sober',
    display_name='Sober',
    app_id='org.vinegarhq.Sober',
    desktop_ids=('org.vinegarhq.Sober.desktop',),
    xdg_namespace='sober',
    process_names=('sober', 'Sober', 'org.vinegarhq.Sober'),
    engine_process_names=('Main',),
    cgroup_marker='app-flatpak-org.vinegarhq.Sober',
    config_filename='config.json',
    resource_relative_paths=(Path('asset_overlay'), Path('exe')),
    storage_db_relative_path=Path('appData/rbx-storage.db'),
    cache_storage_relative_path=Path('rbx-storage'),
    proxy_environment_names=(
        'ALL_PROXY',
        'HTTPS_PROXY',
        'HTTP_PROXY',
        'all_proxy',
        'https_proxy',
        'http_proxy',
        'NO_PROXY',
        'no_proxy',
    ),
    proxy_passthrough_hosts=frozenset({'sober.vinegarhq.org', 'raw.githubusercontent.com'}),
    clientsettings_route_delay_seconds=30.0,
)

# https://github.com/komaruworld/mocktail
MOCKTAIL_CLIENT = LinuxClientDescriptor(
    key='mocktail',
    display_name='Mocktail',
    app_id='space.bigrat.mocktail',
    desktop_ids=('space.bigrat.mocktail.desktop',),
    xdg_namespace='mocktail',
    process_names=('mocktail',),
    engine_process_names=('mocktail',),
    cgroup_marker='app-flatpak-space.bigrat.mocktail',
    config_filename='config.yaml',
    # Mocktail's Roblox payload is hash-checked by its updater, so it has no
    # writable asset overlay; its own Fleasion integration builds the CA bundle
    resource_relative_paths=(),
    proxy_integration='mocktail_config',
    fflag_file_patterns=('config/mocktail/fflags.json',),
    settings_dir_patterns=(
        'data/mocktail/android/data/files/appData',
        'data/mocktail/android/data/appData',
    ),
    storage_path_patterns=tuple(
        f'data/mocktail/android/data/{prefix}rbx-storage{suffix}'
        for prefix in ('', 'appData/', 'files/appData/', 'cache/')
        for suffix in ('', '.db', '.db-wal', '.db-shm')
    ),
)

# Phone and Quest builds extract to separate caches; the update store keeps
# one extracted copy per downloaded version
_CORDIAL_EXTRACTED_ASSETS = (
    'cache/cordial/assets',
    'cache/cordial/assets-arm64-v8a',
    'data/cordial/builds/*/assets',
)

# https://github.com/luohoa97/cordial
CORDIAL_CLIENT = LinuxClientDescriptor(
    key='cordial',
    display_name='Cordial',
    app_id='io.github.luohoa97.Cordial',
    desktop_ids=('io.github.luohoa97.Cordial.desktop',),
    xdg_namespace='cordial',
    process_names=('cordial-shell', 'cordial-run'),
    engine_process_names=('cordial-run',),
    cgroup_marker='app-flatpak-io.github.luohoa97.Cordial',
    config_filename='config.json',
    # The user overlay mirrors the APK's assets/ exactly like Sober's
    # asset_overlay, but lives under $XDG_CONFIG_HOME/cordial/overlay
    resource_relative_paths=(Path('overlay'),),
    resource_base='config',
    proxy_environment_names=SOBER_CLIENT.proxy_environment_names,
    # curl trusts ./exe/cacert.pem, a symlink to the extracted APK bundle, so
    # the extracted copies need the CA as well as the overlay
    ca_bundle_dir_patterns=_CORDIAL_EXTRACTED_ASSETS,
    original_asset_dir_patterns=_CORDIAL_EXTRACTED_ASSETS,
    fflag_file_patterns=('data/cordial/profiles/*/flags.json',),
    settings_dir_patterns=('data/cordial/profiles/*/data/files/appData',),
    storage_path_patterns=tuple(
        f'data/cordial/profiles/*/data/{prefix}rbx-storage{suffix}'
        for prefix in ('', 'files/appData/', 'cache/')
        for suffix in ('', '.db', '.db-wal', '.db-shm')
    ),
)

# A backend belongs here only after its descriptor and any truly
# client-specific adapters have been implemented and tested.
LINUX_CLIENTS = (SOBER_CLIENT, MOCKTAIL_CLIENT, CORDIAL_CLIENT)
LINUX_CLIENTS_BY_KEY: Mapping[str, LinuxClientDescriptor] = MappingProxyType(
    {client.key: client for client in LINUX_CLIENTS}
)


def get_linux_client(key: str) -> LinuxClientDescriptor:
    normalized = str(key).strip().casefold()
    try:
        return LINUX_CLIENTS_BY_KEY[normalized]
    except KeyError as exc:
        choices = ', '.join(LINUX_CLIENTS_BY_KEY)
        raise ValueError(f'unknown Linux client {key!r}; expected one of: {choices}') from exc


def _flatpak_probe_env(
    environ: Mapping[str, str],
    *,
    home: Path,
) -> dict[str, str]:
    """Return a host-tool environment without PyInstaller's private libraries."""
    env = dict(environ)
    env['HOME'] = str(home)
    original_library_path = env.pop('LD_LIBRARY_PATH_ORIG', None)
    if original_library_path is not None:
        if original_library_path:
            env['LD_LIBRARY_PATH'] = original_library_path
        else:
            env.pop('LD_LIBRARY_PATH', None)
        return env

    bundle_root = getattr(sys, '_MEIPASS', None)
    library_path = env.get('LD_LIBRARY_PATH')
    if bundle_root and library_path:
        try:
            bundle_path = Path(bundle_root).resolve()
            entries = [
                entry
                for entry in library_path.split(os.pathsep)
                if entry and Path(entry).resolve() != bundle_path
            ]
        except OSError:
            entries = []
        if entries:
            env['LD_LIBRARY_PATH'] = os.pathsep.join(entries)
        else:
            env.pop('LD_LIBRARY_PATH', None)
    return env


def _flatpak_info_succeeds(
    executable: str | Path | None,
    app_id: str,
    run: Callable[..., subprocess.CompletedProcess] | None,
    *,
    env: Mapping[str, str],
) -> bool:
    if executable is None:
        return False
    runner = subprocess.run if run is None else run
    try:
        result = runner(
            [str(executable), 'info', app_id],
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return result.returncode == 0


def detect_installed_clients(
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: Callable[..., subprocess.CompletedProcess] | None = None,
) -> tuple[LinuxClientInstallation, ...]:
    """Detect registered Flatpak clients in deterministic registry order."""
    environment = os.environ if environ is None else environ
    find_executable = shutil.which if which is None else which
    flatpak = find_executable('flatpak')
    detected: list[LinuxClientInstallation] = []
    for client in LINUX_CLIENTS:
        paths = client.paths(home=home, environ=environment)
        # Flatpak keeps ~/.var/app/<id> data after an uninstall unless the user
        # explicitly removes application data.  Treat only `flatpak info` as
        # authoritative installation evidence so stale data is never modified.
        probe_env = _flatpak_probe_env(environment, home=paths.home)
        if not _flatpak_info_succeeds(flatpak, client.app_id, run, env=probe_env):
            continue
        detected.append(
            LinuxClientInstallation(
                client=client,
                paths=paths,
                executable=Path(flatpak) if flatpak else None,
            )
        )
    return tuple(detected)


def query_default_roblox_handlers(
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: Callable[..., subprocess.CompletedProcess] | None = None,
) -> tuple[str, ...]:
    """Return successful desktop-handler answers in scheme-priority order."""
    environment = dict(os.environ if environ is None else environ)
    environment['HOME'] = str(_home_path(home, environment))
    find_executable = shutil.which if which is None else which
    xdg_mime = find_executable('xdg-mime')
    if xdg_mime is None:
        return ()
    runner = subprocess.run if run is None else run
    handlers: list[str] = []
    for scheme in ROBLOX_URI_SCHEMES:
        try:
            result = runner(
                [xdg_mime, 'query', 'default', scheme],
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except OSError, subprocess.SubprocessError:
            continue
        handler = result.stdout.strip() if result.returncode == 0 else ''
        if handler and handler not in handlers:
            handlers.append(handler)
    return tuple(handlers)


def query_default_roblox_handler(**kwargs) -> str | None:
    handlers = query_default_roblox_handlers(**kwargs)
    return handlers[0] if handlers else None


def select_linux_client(
    selection: str = 'auto',
    *,
    installed: Sequence[LinuxClientInstallation] | None = None,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: Callable[..., subprocess.CompletedProcess] | None = None,
) -> LinuxClientInstallation | None:
    """Resolve ``auto`` or a registered key to an installed client."""
    normalized = str(selection).strip().casefold()
    if normalized != 'auto' and normalized not in LINUX_CLIENTS_BY_KEY:
        choices = ', '.join(('auto', *LINUX_CLIENTS_BY_KEY))
        raise ValueError(f'Linux client selection must be one of: {choices}')
    installations = (
        tuple(installed)
        if installed is not None
        else detect_installed_clients(
            home=home,
            environ=environ,
            which=which,
            run=run,
        )
    )
    by_key = {installation.key: installation for installation in installations}
    if normalized != 'auto':
        return by_key.get(normalized)
    if len(installations) <= 1:
        return installations[0] if installations else None

    handlers = query_default_roblox_handlers(
        home=home,
        environ=environ,
        which=which,
        run=run,
    )
    for handler in handlers:
        for installation in installations:
            if handler in installation.client.desktop_ids:
                return installation
    for descriptor in LINUX_CLIENTS:
        if descriptor.key in by_key:
            return by_key[descriptor.key]
    return None


def identify_resource_owner(
    path: str | Path,
    installations: Iterable[LinuxClientInstallation],
) -> LinuxClientInstallation | None:
    """Return the registered installation which owns a resource path."""
    return next(
        (installation for installation in installations if installation.owns_resource_path(path)),
        None,
    )
