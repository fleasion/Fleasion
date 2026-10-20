"""Registry and discovery for supported Linux Roblox clients.

Sober, Mocktail and Cordial are the registered backends. Sober ships only as a
Flatpak; Mocktail and Cordial can also be installed from distro packages (deb,
rpm, pacman) or run as AppImages, which use the host XDG directories instead of
``~/.var/app/<id>``. Keeping each client's identity, paths, process markers,
desktop handlers, and proxy capabilities in a descriptor provides a small
extension seam without spreading client checks throughout the Linux platform
helpers.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, Unpack

if TYPE_CHECKING:
    from collections.abc import (
        Callable,
        Iterable,
        Mapping,
        Sequence,
    )


class CommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str],
        capture_output: bool,
        text: Literal[True],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


class _QueryHandlerKwargs(TypedDict, total=False):
    home: str | Path | None
    environ: Mapping[str, str] | None
    which: Callable[[str], str | None] | None
    run: CommandRunner | None


def _command_runner(run: CommandRunner | None) -> CommandRunner:
    if run is None:
        return subprocess.run
    return run


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


type InstallKind = Literal['flatpak', 'native', 'appimage']
"""How a client is installed, which decides its paths, launcher, and proxy route."""

INSTALL_KINDS: tuple[InstallKind, ...] = ('flatpak', 'native', 'appimage')

type ResourceBase = Literal['data', 'config']
"""Which XDG root a client's asset overlay lives under."""

type ProxyIntegration = Literal['flatpak_env', 'mocktail_config']
"""How Fleasion routes a client through its proxy.

``flatpak_env``: the standard proxy variables are set for the client (a
``flatpak override`` for a Flatpak, a user desktop entry otherwise) and
Fleasion's CA is written into each ``ssl/cacert.pem`` the client reads.

``mocktail_config``: the client has its own Fleasion integration. Fleasion
enables it in the client's config and copies its public CA certificate into the
client's sandbox; the client then routes itself and builds its own CA bundle.
"""


def _xdg_home(environ: Mapping[str, str], name: str, default: Path) -> Path:
    # The XDG spec says relative values are invalid and must be ignored
    configured = environ.get(name, '').strip()
    if configured and Path(configured).is_absolute():
        return _resolved(configured)
    return default


def _glob_client_paths(bases: Mapping[str, Path], patterns: Iterable[str]) -> tuple[Path, ...]:
    """Expand ``patterns`` in pattern order, deduplicated.

    Each pattern starts with ``config/``, ``data/`` or ``cache/``, which is
    replaced by that XDG base directory of the installation.
    """
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        head, _, rest = pattern.partition('/')
        root = bases.get(head)
        if root is None or not rest:
            continue
        try:
            matches = sorted(root.glob(rest)) if any(c in rest for c in '*?[') else [root / rest]
        except OSError, ValueError:
            continue
        for match in matches:
            if match not in seen:
                seen.add(match)
                found.append(match)
    return tuple(found)


@dataclass(frozen=True, slots=True)
class LinuxClientDescriptor:
    """Static identity and capabilities for one Linux client."""

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
    native_commands: tuple[str, ...] = ()
    """Launchers a distro package (deb, rpm, pacman) installs on ``PATH``."""
    appimage_prefix: str | None = None
    """Casefolded AppImage filename prefix, e.g. ``cordial`` for ``Cordial-*.AppImage``."""
    # The patterns below start with the XDG base they live under (config/,
    # data/ or cache/) and may contain globs, for clients with one directory
    # per profile
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

    @property
    def install_kinds(self) -> tuple[InstallKind, ...]:
        """The install methods this client is distributed as."""
        kinds: list[InstallKind] = ['flatpak']
        if self.native_commands:
            kinds.append('native')
        if self.appimage_prefix:
            kinds.append('appimage')
        return tuple(kinds)

    def all_paths(
        self,
        *,
        home: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> tuple[LinuxClientPaths, ...]:
        """Paths for every install method, for cleanup that must not miss one.

        Native packages and AppImages share the host XDG directories, so they
        are listed once.
        """
        kinds: dict[InstallKind, None] = {}
        for kind in self.install_kinds:
            kinds['flatpak' if kind == 'flatpak' else 'native'] = None
        return tuple(self.paths(home=home, environ=environ, kind=kind) for kind in kinds)

    def paths(
        self,
        *,
        home: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
        kind: InstallKind = 'flatpak',
    ) -> LinuxClientPaths:
        """Resolve the host-visible paths for one install method of this client."""
        environment = os.environ if environ is None else environ
        resolved_home = _home_path(home, environment)
        if kind == 'flatpak':
            flatpak_root: Path | None = resolved_home / '.var' / 'app' / self.app_id
            config_home = flatpak_root / 'config'
            data_home = flatpak_root / 'data'
            cache_home = flatpak_root / 'cache'
        else:
            # Native packages and AppImages run unsandboxed on the host XDG dirs
            flatpak_root = None
            config_home = _xdg_home(environment, 'XDG_CONFIG_HOME', resolved_home / '.config')
            data_home = _xdg_home(environment, 'XDG_DATA_HOME', resolved_home / '.local' / 'share')
            cache_home = _xdg_home(environment, 'XDG_CACHE_HOME', resolved_home / '.cache')
        config_root = config_home / self.xdg_namespace
        data_root = data_home / self.xdg_namespace
        cache_root = cache_home / self.xdg_namespace
        return LinuxClientPaths(
            client=self,
            kind=kind,
            home=resolved_home,
            flatpak_root=flatpak_root,
            config_home=config_home,
            data_home=data_home,
            cache_home=cache_home,
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
    """Host-visible paths for one concrete client installation."""

    client: LinuxClientDescriptor
    kind: InstallKind
    home: Path
    flatpak_root: Path | None
    """``~/.var/app/<id>`` for a Flatpak; ``None`` for native and AppImage installs."""
    config_home: Path
    data_home: Path
    cache_home: Path
    config_root: Path
    data_root: Path
    cache_root: Path
    config_file: Path
    resource_roots: tuple[Path, ...]
    storage_db: Path | None
    cache_storage_dir: Path | None

    def _glob(self, patterns: Iterable[str]) -> tuple[Path, ...]:
        return _glob_client_paths(
            {'config': self.config_home, 'data': self.data_home, 'cache': self.cache_home},
            patterns,
        )

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
            for path in self._glob(self.client.ca_bundle_dir_patterns)
            if (path / 'ssl' / 'cacert.pem').is_file()
        )

    def original_asset_dirs(self) -> tuple[Path, ...]:
        """Existing extracted ``assets/`` copies to read original files from."""
        return tuple(
            path for path in self._glob(self.client.original_asset_dir_patterns) if path.is_dir()
        )

    def fflag_files(self) -> tuple[Path, ...]:
        """FastFlag files to manage, limited to directories the client created.

        The directory part is globbed so a profile without a flags file yet is
        still included.
        """
        files: list[Path] = []
        for pattern in self.client.fflag_file_patterns:
            directory, _, name = pattern.rpartition('/')
            files.extend(parent / name for parent in self._glob((directory,)) if parent.is_dir())
        return tuple(files)

    def settings_dirs(self) -> tuple[Path, ...]:
        """Directories holding the client's ``GlobalBasicSettings_13.xml``."""
        if not self.client.settings_dir_patterns:
            return (self.data_root,) if self.data_root.is_dir() else ()
        return tuple(
            path for path in self._glob(self.client.settings_dir_patterns) if path.is_dir()
        )

    def storage_paths(self) -> tuple[Path, ...]:
        """Existing Roblox HTTP cache files and folders for Delete Cache."""
        return tuple(
            path
            for path in self._glob(self.client.storage_path_patterns)
            if path.exists() or path.is_symlink()
        )

    def owns_resource_path(self, path: str | Path) -> bool:
        return any(_is_within(path, root) for root in self.resource_roots)


@dataclass(frozen=True, slots=True)
class LinuxClientInstallation:
    """A detected descriptor paired with its launcher and resolved paths.

    ``executable`` is the ``flatpak`` binary for a Flatpak, and the client's
    own launcher or AppImage file otherwise.
    """

    client: LinuxClientDescriptor
    paths: LinuxClientPaths
    executable: Path | None
    desktop_entry: Path | None = None
    """The AppImage's desktop entry, when an integration tool created one."""

    @property
    def key(self) -> str:
        return self.client.key

    @property
    def kind(self) -> InstallKind:
        return self.paths.kind

    @property
    def is_flatpak(self) -> bool:
        return self.paths.kind == 'flatpak'

    @property
    def display_name(self) -> str:
        return self.client.display_name

    @property
    def app_id(self) -> str:
        return self.client.app_id

    @property
    def desktop_id(self) -> str:
        # AppImage integration tools name entries after the file, not the app ID
        if self.desktop_entry is not None:
            return self.desktop_entry.name
        return self.client.desktop_id

    def launch_command(self, uri: str | None = None) -> list[str] | None:
        if self.executable is None:
            return None
        command = [str(self.executable)]
        if self.is_flatpak:
            command += ['run', self.app_id]
        if uri:
            command.append(uri)
        return command

    def owns_process_cgroup(self, cgroup: str) -> bool:
        """Whether a process in ``cgroup`` belongs to this installation.

        A Flatpak runs in its own app scope. Native and AppImage processes share
        their names with the Flatpak's, so they are told apart by not being in it.
        """
        marker = self.client.cgroup_marker
        if not marker:
            return True
        return (marker in cgroup) == self.is_flatpak

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
    native_commands=('mocktail',),
    appimage_prefix='mocktail',
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
    process_names=('cordial-run',),
    engine_process_names=('cordial-run',),
    cgroup_marker='app-flatpak-io.github.luohoa97.Cordial',
    config_filename='config.json',
    # The packages' desktop entry runs cordial-shell; plain `cordial` is a CLI
    native_commands=('cordial-shell',),
    appimage_prefix='cordial',
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
    settings_dir_patterns=tuple(
        f'data/cordial/profiles/*/{engine}data/files/appData' for engine in ('', 'quest/')
    ),
    storage_path_patterns=tuple(
        f'data/cordial/profiles/*/{engine}data/{prefix}rbx-storage{suffix}'
        for engine in ('', 'quest/')
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
        msg = f'unknown Linux client {key!r}; expected one of: {choices}'
        raise ValueError(msg) from exc


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
    run: CommandRunner | None,
    *,
    env: Mapping[str, str],
) -> bool:
    if executable is None:
        return False
    runner = _command_runner(run)
    try:
        result = runner(
            [str(executable), 'info', app_id],
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


# Where AppImage users and integration tools (AppImageLauncher, Gear Lever)
# conventionally keep AppImages, relative to the home directory
_APPIMAGE_DIRS = (Path('Applications'), Path('AppImages'), Path('.local') / 'bin')


def desktop_entry_program(entry: Path) -> Path | None:
    """Return the program an ``Exec=`` line in ``[Desktop Entry]`` runs.

    ``env NAME=value`` prefixes are skipped so entries Fleasion or the user
    wrapped still resolve to the client.
    """
    try:
        lines = entry.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return None
    in_main_group = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('['):
            in_main_group = stripped == '[Desktop Entry]'
            continue
        if not in_main_group or not stripped.startswith('Exec='):
            continue
        try:
            argv = shlex.split(stripped.removeprefix('Exec='))
        except ValueError:
            return None
        if argv and Path(argv[0]).name == 'env':
            argv = argv[1:]
            while argv and '=' in argv[0] and not argv[0].startswith('-'):
                argv = argv[1:]
        return Path(argv[0]) if argv else None
    return None


def _is_client_appimage(client: LinuxClientDescriptor, path: Path) -> bool:
    name = path.name.casefold()
    return (
        client.appimage_prefix is not None
        and name.startswith(client.appimage_prefix)
        and name.endswith('.appimage')
        and path.is_file()
    )


def _newest_file(paths: Iterable[Path]) -> Path | None:
    """Return the most recently modified existing path, tie-broken by name."""
    stamped: list[tuple[float, str, Path]] = []
    for path in paths:
        try:
            stamped.append((path.stat().st_mtime, str(path), path))
        except OSError:
            continue
    return max(stamped)[2] if stamped else None


def _find_appimage(
    client: LinuxClientDescriptor, paths: LinuxClientPaths
) -> tuple[Path, Path | None] | None:
    """Return the newest AppImage of ``client`` and its desktop entry, if any.

    AppImages are not installed anywhere, so only the user's own desktop
    entries and the conventional AppImage folders are searched.
    """
    if client.appimage_prefix is None:
        return None
    entries: dict[Path, Path] = {}
    try:
        desktop_files = sorted((paths.data_home / 'applications').glob('*.desktop'))
    except OSError:
        desktop_files = []
    for entry in desktop_files:
        program = desktop_entry_program(entry)
        if program is not None and _is_client_appimage(client, program):
            entries.setdefault(_resolved(program), entry)

    candidates = set(entries)
    for directory in _APPIMAGE_DIRS:
        try:
            children = list((paths.home / directory).iterdir())
        except OSError:
            continue
        candidates.update(
            _resolved(child) for child in children if _is_client_appimage(client, child)
        )

    newest = _newest_file(candidates)
    return (newest, entries.get(newest)) if newest is not None else None


def _find_native_launcher(
    client: LinuxClientDescriptor, find_executable: Callable[[str], str | None]
) -> Path | None:
    for command in client.native_commands:
        found = find_executable(command)
        # Flatpak can export app commands onto PATH; those are the Flatpak
        if found and '/flatpak/exports/' not in found:
            return Path(found)
    return None


def desktop_data_dirs(paths: LinuxClientPaths, environ: Mapping[str, str]) -> tuple[Path, ...]:
    """XDG data dirs in desktop-entry lookup order, user dir first."""
    host_data_home = _xdg_home(environ, 'XDG_DATA_HOME', paths.home / '.local' / 'share')
    configured = environ.get('XDG_DATA_DIRS', '').strip() or '/usr/local/share:/usr/share'
    dirs = [host_data_home]
    dirs += [Path(entry) for entry in configured.split(os.pathsep) if Path(entry).is_absolute()]
    # Elevated sessions often drop Flatpak's additions to XDG_DATA_DIRS
    dirs += [
        host_data_home / 'flatpak' / 'exports' / 'share',
        Path('/var/lib/flatpak/exports/share'),
    ]
    return tuple(dict.fromkeys(dirs))


def _desktop_handler_kind(
    client: LinuxClientDescriptor, paths: LinuxClientPaths, environ: Mapping[str, str]
) -> InstallKind | None:
    """Which install of ``client`` its desktop ID launches, as the desktop resolves it.

    The Flatpak and the distro packages export the same desktop ID, and the
    first match in the XDG data dirs is the one a browser's Play button runs.
    """
    for data_dir in desktop_data_dirs(paths, environ):
        entry = data_dir / 'applications' / client.desktop_id
        if not entry.is_file():
            continue
        program = desktop_entry_program(entry)
        if program is None:
            return None
        if program.name == 'flatpak':
            return 'flatpak'
        if program.name in client.native_commands:
            return 'native'
        if _is_client_appimage(client, program):
            return 'appimage'
        return None
    return None


def detect_installed_clients(
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: CommandRunner | None = None,
) -> tuple[LinuxClientInstallation, ...]:
    """Detect registered clients in deterministic registry order.

    Each client is reported once. When it is installed more than one way, the
    install its desktop entry launches wins, then Flatpak, native, AppImage.
    """
    environment = os.environ if environ is None else environ
    find_executable = shutil.which if which is None else which
    flatpak = find_executable('flatpak')
    detected: list[LinuxClientInstallation] = []
    for client in LINUX_CLIENTS:
        found: dict[InstallKind, LinuxClientInstallation] = {}
        paths = client.paths(home=home, environ=environment)
        # Flatpak keeps ~/.var/app/<id> data after an uninstall unless the user
        # explicitly removes application data.  Treat only `flatpak info` as
        # authoritative installation evidence so stale data is never modified.
        probe_env = _flatpak_probe_env(environment, home=paths.home)
        if _flatpak_info_succeeds(flatpak, client.app_id, run, env=probe_env):
            found['flatpak'] = LinuxClientInstallation(
                client=client,
                paths=paths,
                executable=Path(flatpak) if flatpak else None,
            )
        host_paths = client.paths(home=home, environ=environment, kind='native')
        native = _find_native_launcher(client, find_executable)
        if native is not None:
            found['native'] = LinuxClientInstallation(
                client=client, paths=host_paths, executable=native
            )
        appimage = _find_appimage(client, host_paths)
        if appimage is not None:
            found['appimage'] = LinuxClientInstallation(
                client=client,
                paths=client.paths(home=home, environ=environment, kind='appimage'),
                executable=appimage[0],
                desktop_entry=appimage[1],
            )
        if not found:
            continue
        preferred = (
            _desktop_handler_kind(client, host_paths, environment) if len(found) > 1 else None
        )
        detected.append(
            found[preferred]
            if preferred in found
            else next(found[kind] for kind in INSTALL_KINDS if kind in found)
        )
    return tuple(detected)


def query_default_roblox_handlers(
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: CommandRunner | None = None,
) -> tuple[str, ...]:
    """Return successful desktop-handler answers in scheme-priority order."""
    environment = dict(os.environ if environ is None else environ)
    environment['HOME'] = str(_home_path(home, environment))
    find_executable = shutil.which if which is None else which
    xdg_mime = find_executable('xdg-mime')
    if xdg_mime is None:
        return ()
    runner = _command_runner(run)
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


def query_default_roblox_handler(**kwargs: Unpack[_QueryHandlerKwargs]) -> str | None:
    handlers = query_default_roblox_handlers(**kwargs)
    return handlers[0] if handlers else None


def select_linux_client(
    selection: str = 'auto',
    *,
    installed: Sequence[LinuxClientInstallation] | None = None,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: CommandRunner | None = None,
) -> LinuxClientInstallation | None:
    """Resolve ``auto`` or a registered key to an installed client."""
    normalized = str(selection).strip().casefold()
    if normalized != 'auto' and normalized not in LINUX_CLIENTS_BY_KEY:
        choices = ', '.join(('auto', *LINUX_CLIENTS_BY_KEY))
        msg = f'Linux client selection must be one of: {choices}'
        raise ValueError(msg)
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
            if handler in {*installation.client.desktop_ids, installation.desktop_id}:
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
