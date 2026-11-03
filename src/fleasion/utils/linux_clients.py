"""Registry and discovery for supported Linux Roblox clients.

Sober uses Flatpak metadata and app-scoped proxy settings. Mocktail uses a
native Linux executable and isolated XDG data/cache roots. Rosemary uses a
native executable and the macOS Roblox.app bundle supplied by Nutmeg, process
arguments, or user-configured paths.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, Unpack, cast

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
    runtime: Literal['flatpak', 'rosemary', 'native'] = 'flatpak'

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
        if self.runtime == 'native':
            flatpak_root = resolved_home / '.local' / 'share' / self.xdg_namespace
            config_root = resolved_home / '.config' / self.xdg_namespace
            data_root = flatpak_root / 'android' / 'data'
            cache_root = resolved_home / '.cache' / self.xdg_namespace
        else:
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
            resource_roots=tuple(data_root / relative for relative in self.resource_relative_paths),
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

    def existing_resource_roots(self) -> tuple[Path, ...]:
        return tuple(root for root in self.resource_roots if root.is_dir())

    def owns_resource_path(self, path: str | Path) -> bool:
        return any(_is_within(path, root) for root in self.resource_roots)


@dataclass(frozen=True, slots=True)
class LinuxClientInstallation:
    """A detected descriptor paired with its launcher and resolved paths."""

    client: LinuxClientDescriptor
    paths: LinuxClientPaths
    executable: Path | None
    app_bundle: Path | None = None
    desktop_launcher: bool = False

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

    def launch_command(
        self, uri: str | None = None, *, require_inherited_environment: bool = False
    ) -> list[str] | None:
        if self.client.runtime == 'rosemary':
            # gtk-launch may activate the desktop handler through D-Bus, where
            # Fleasion's per-launch proxy variables are not inherited.
            if self.desktop_launcher and not require_inherited_environment:
                launcher = shutil.which('gtk-launch')
                if launcher is not None:
                    command = [launcher, self.desktop_id.removesuffix('.desktop')]
                    if uri:
                        command.append(uri)
                    return command
            if self.executable is None or self.app_bundle is None:
                return None
            command = [str(self.executable), 'run', str(self.app_bundle)]
        elif self.client.runtime == 'native':
            if self.executable is None:
                return None
            command = [str(self.executable)]
        else:
            if self.executable is None:
                return None
            command = [str(self.executable), 'run', self.app_id]
        if uri:
            if self.client.runtime == 'rosemary':
                command.extend(('-protocolString', uri))
            elif self.client.runtime == 'native':
                command.extend(('--launch-uri', uri))
            else:
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

MOCKTAIL_CLIENT = LinuxClientDescriptor(
    key='mocktail',
    display_name='Mocktail',
    app_id='space.bigrat.mocktail',
    desktop_ids=('space.bigrat.mocktail.desktop',),
    xdg_namespace='mocktail',
    process_names=('mocktail',),
    engine_process_names=('mocktail',),
    cgroup_marker='',
    config_filename='config.yaml',
    # Payloads are managed/versioned by Mocktail and must not be edited as
    # though they were Sober's writable asset overlay.
    resource_relative_paths=(),
    storage_db_relative_path=Path('appData/rbx-storage.db'),
    cache_storage_relative_path=Path('android/rbx-storage'),
    proxy_environment_names=(
        'ALL_PROXY', 'HTTPS_PROXY', 'HTTP_PROXY',
        'all_proxy', 'https_proxy', 'http_proxy', 'NO_PROXY', 'no_proxy',
    ),
    runtime='native',
)


ROSEMARY_CLIENT = LinuxClientDescriptor(
    key='rosemary',
    display_name='Rosemary (macOS Roblox)',
    app_id='rosemary',
    desktop_ids=('io.github.frolleks.Nutmeg.desktop',),
    xdg_namespace='rosemary',
    process_names=('rosemary',),
    engine_process_names=('rosemary',),
    cgroup_marker='',
    config_filename='',
    resource_relative_paths=(),
    runtime='rosemary',
)

LINUX_CLIENTS = (SOBER_CLIENT, MOCKTAIL_CLIENT, ROSEMARY_CLIENT)
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
    except OSError, subprocess.SubprocessError:
        return False
    return result.returncode == 0


@dataclass(frozen=True, slots=True)
class RosemarySession:
    """A running macOS Roblox Player, excluding Rosemary Studio and helpers."""

    pid: int
    bundle: Path
    launcher: Path
    start_ticks: int


def _rosemary_bundle(value: str) -> Path | None:
    path_text = value.removeprefix('/Volumes/Host') if value.startswith('/Volumes/Host/') else value
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        return None
    for part in (path, *path.parents):
        if part.name != 'Roblox.app':
            continue
        resources = part / 'Contents' / 'Resources'
        executable = part / 'Contents' / 'MacOS' / 'RobloxPlayer'
        if (
            path in {part, executable}
            and executable.is_file()
            and (resources / 'ssl' / 'cacert.pem').is_file()
        ):
            return part.resolve()
        return None
    return None


def _read_rosemary_session(process: Path) -> RosemarySession | None:
    try:
        command_line = (process / 'cmdline').read_bytes()
        stat_text = (process / 'stat').read_text(encoding='utf-8')
    except OSError:
        return None
    argv = [arg.decode('utf-8', errors='replace') for arg in command_line.split(b'\0') if arg]
    if len(argv) < 3 or Path(argv[0]).name != 'rosemary':
        return None
    args = argv[2:] if argv[1] == 'run' else argv[1:]
    options = {'--native', '--emulated', '--slow', '--new-instance', '--objc-debug'}
    while args and args[0] in options:
        args = args[1:]
    bundle = _rosemary_bundle(args[0]) if args else None
    if bundle is None:
        return None
    try:
        start_ticks = int(stat_text.rsplit(')', 1)[1].split()[19])
    except IndexError, ValueError:
        return None
    return RosemarySession(int(process.name), bundle, Path(argv[0]), start_ticks)


def read_rosemary_session(process: Path) -> RosemarySession | None:
    """Validate a single Rosemary Player process and its start generation."""
    return _read_rosemary_session(process)


def running_rosemary_players(
    *,
    proc_root: Path = Path('/proc'),
) -> tuple[RosemarySession, ...]:
    """Read argv from real Rosemary hosts, without confusing Studio or crash helpers."""
    try:
        processes = list(proc_root.iterdir())
    except OSError:
        return ()
    results = (
        session
        for process in processes
        if process.name.isdecimal()
        if (session := _read_rosemary_session(process)) is not None
    )
    return tuple(sorted(results, key=lambda session: session.pid))


@dataclass(frozen=True, slots=True)
class MocktailSession:
    """Native Mocktail Player process, keyed by PID and kernel start ticks."""

    pid: int
    start_ticks: int


def read_mocktail_session(  # ruff: ignore[too-many-return-statements]
    process: Path, *, executable: Path | None = None
) -> MocktailSession | None:
    """Reject helpers, updaters, and PID reuse using /proc/exe and stat."""
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if process.stat().st_uid != os.getuid():
            return None
        linked_exe = (process / 'exe').resolve(strict=True)
        if linked_exe.name != 'mocktail':
            return None
        if executable is not None and not linked_exe.samefile(executable):
            return None
        command_line = (process / 'cmdline').read_bytes()
        argv = [arg for arg in command_line.split(b'\0') if arg]
        if not argv or Path(os.fsdecode(argv[0])).name != 'mocktail':
            return None
        if (process / 'comm').read_text(encoding='utf-8').strip() != 'Main':
            return None
        with (process / 'maps').open(encoding='utf-8', errors='replace') as mapped:
            if not any('libroblox.so' in line.lower() for line in mapped):
                return None
        stat_text = (process / 'stat').read_text(encoding='utf-8')
        start_ticks = int(stat_text.rsplit(')', 1)[1].split()[19])
        return MocktailSession(int(process.name), start_ticks)
    except (OSError, ValueError, IndexError):
        return None


def running_mocktail_players(
    *, proc_root: Path = Path('/proc'), executable: Path | None = None
) -> tuple[MocktailSession, ...]:
    """Return only a real Mocktail Player binary, never associated helpers."""
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return ()
    matches = (
        session
        for entry in entries
        if entry.name.isdecimal()
        if (session := read_mocktail_session(entry, executable=executable)) is not None
    )
    return tuple(sorted(matches, key=lambda session: session.pid))


def running_rosemary_studios(*, proc_root: Path = Path('/proc')) -> tuple[int, ...]:
    """Find Rosemary Studio guests, which may share a Player cache prefix."""
    try:
        processes = list(proc_root.iterdir())
    except OSError:
        return ()
    studio_pids: list[int] = []
    for process in processes:
        if not process.name.isdecimal():
            continue
        try:
            argv = [
                arg.decode('utf-8', errors='replace')
                for arg in (process / 'cmdline').read_bytes().split(b'\0')
                if arg
            ]
        except OSError:
            continue
        if len(argv) < 3 or Path(argv[0]).name != 'rosemary' or argv[1] != 'run':
            continue
        if any(
            arg.endswith(('/RobloxStudio.app', '/RobloxStudio.app/Contents/MacOS/RobloxStudio'))
            for arg in argv[2:]
        ):
            studio_pids.append(int(process.name))
    return tuple(sorted(studio_pids))


def rosemary_guest_home(
    installation: LinuxClientInstallation,
    *,
    prefix_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve Nutmeg's macOS guest home without executing guest code.

    Invalid persisted configuration fails closed rather than guessing a
    different prefix for destructive cache operations.
    """
    home = installation.paths.home
    environment = os.environ if environ is None else environ
    xdg_config = environment.get('XDG_CONFIG_HOME', '')
    config_dir = (
        Path(xdg_config)
        if xdg_config and Path(xdg_config).is_absolute()
        else home / '.config'
    )
    config_path = config_dir / 'nutmeg' / 'config.json'
    settings: dict[str, object] = {}
    try:
        raw_settings: object = json.loads(config_path.read_text(encoding='utf-8'))
        if not isinstance(raw_settings, dict):
            return None
        settings = cast('dict[str, object]', raw_settings)
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError, ValueError):
        return None

    xdg_data = environment.get('XDG_DATA_HOME', '')
    data_home = (
        Path(xdg_data) if xdg_data and Path(xdg_data).is_absolute()
        else home / '.local/share'
    )
    prefix = data_home / 'nutmeg' / 'prefix'
    configured_prefix = prefix_path or environment.get('ROSEMARY_PREFIX') or settings.get('prefix_path')
    if configured_prefix:
        candidate = (
            Path(configured_prefix).expanduser()
            if isinstance(configured_prefix, str)
            else None
        )
        if candidate is None or not candidate.is_absolute():
            return None
        prefix = candidate
    guest_home_mode = environment.get('ROSEMARY_PREFIX_HOME') or settings.get('prefix_home')
    if guest_home_mode not in {None, '', 'host', 'prefix'}:
        return None
    if guest_home_mode == 'host':
        return home
    return prefix / 'root' / 'Users' / home.name


def _nutmeg_desktop_exists(home: Path, environ: Mapping[str, str]) -> bool:
    paths = [home / '.local/share/applications/io.github.frolleks.Nutmeg.desktop']
    paths.extend(
        Path(root) / 'applications/io.github.frolleks.Nutmeg.desktop'
        for root in environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':')
        if root
    )
    for path in paths:
        try:
            content = path.read_text(encoding='utf-8')
        except OSError:
            continue
        exec_line = next(
            (line[5:].strip() for line in content.splitlines() if line.startswith('Exec=')), ''
        )
        if not exec_line:
            continue
        try:
            tokens = shlex.split(exec_line)
        except ValueError:
            continue
        if tokens and (not tokens[0].startswith('/') or Path(tokens[0]).is_file()):
            return True
    return False


def _nutmeg_app_bundle(home: Path) -> Path | None:
    root = home / '.local' / 'share' / 'nutmeg' / 'versions'
    try:
        bundles = list(root.glob('*/Roblox.app'))
    except OSError:
        return None
    for path in sorted(bundles, reverse=True):
        if (bundle := _rosemary_bundle(str(path))) is not None:
            return bundle
    return None


def _rosemary_installation(
    *,
    home: Path,
    environ: Mapping[str, str],
    which: Callable[[str], str | None],
    proc_root: Path,
    app_path: str | Path | None = None,
    binary_path: str | Path | None = None,
) -> LinuxClientInstallation | None:
    sessions = running_rosemary_players(proc_root=proc_root)
    desktop = _nutmeg_desktop_exists(home, environ)
    configured_binary = Path(binary_path).expanduser() if binary_path else None
    binary = (
        str(configured_binary)
        if configured_binary is not None
        and configured_binary.is_file()
        and os.access(configured_binary, os.X_OK)
        else which('rosemary')
    )
    running_binary = next(
        (str(session.launcher) for session in sessions if session.launcher.is_file()), None
    )
    executable_path = binary or running_binary
    executable = Path(executable_path) if executable_path is not None else None
    configured_bundle = _rosemary_bundle(str(app_path)) if app_path else None
    bundle = (
        configured_bundle
        or next((session.bundle for session in sessions), None)
        or _nutmeg_app_bundle(home)
    )
    if not (desktop or sessions or (executable is not None and bundle is not None)):
        return None
    paths = ROSEMARY_CLIENT.paths(home=home, environ=environ)
    paths = replace(paths, resource_roots=(bundle / 'Contents' / 'Resources',) if bundle else ())
    return LinuxClientInstallation(
        ROSEMARY_CLIENT, paths, executable, bundle, desktop and configured_bundle is None
    )


def detect_installed_clients(
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: CommandRunner | None = None,
    proc_root: Path = Path('/proc'),
    rosemary_app_path: str | Path | None = None,
    rosemary_binary_path: str | Path | None = None,
) -> tuple[LinuxClientInstallation, ...]:
    """Detect Flatpak clients and native Rosemary independently."""
    environment = os.environ if environ is None else environ
    find_executable = shutil.which if which is None else which
    flatpak = find_executable('flatpak')
    detected: list[LinuxClientInstallation] = []
    resolved_home = _home_path(home, environment)
    for client in LINUX_CLIENTS:
        if client.runtime != 'flatpak':
            continue
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
    mocktail_binary = find_executable('mocktail')
    if mocktail_binary:
        binary = Path(mocktail_binary)
        if binary.is_file() and os.access(binary, os.X_OK):
            detected.append(
                LinuxClientInstallation(
                    client=MOCKTAIL_CLIENT,
                    paths=MOCKTAIL_CLIENT.paths(home=home, environ=environment),
                    executable=binary,
                )
            )
    rosemary_proc_root = (
        proc_root
        if (
            proc_root != Path('/proc')
            or environ is None
            or home is None
            or resolved_home == _home_path(None, os.environ)
        )
        else Path('/nonexistent-fleasion-test-proc')
    )
    rosemary = _rosemary_installation(
        home=resolved_home,
        environ=environment,
        which=find_executable,
        proc_root=rosemary_proc_root,
        app_path=rosemary_app_path,
        binary_path=rosemary_binary_path,
    )
    if rosemary is not None:
        detected.append(rosemary)
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


def select_linux_client(  # ruff: ignore[too-many-return-statements]
    selection: str = 'auto',
    *,
    installed: Sequence[LinuxClientInstallation] | None = None,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: CommandRunner | None = None,
    rosemary_running: bool | None = None,
    mocktail_running: bool | None = None,
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
    if rosemary_running is None:
        rosemary_running = (
            home is None or _home_path(home, os.environ) == _home_path(None, os.environ)
        ) and bool(running_rosemary_players())
    if 'rosemary' in by_key and rosemary_running:
        return by_key['rosemary']
    if mocktail_running is None:
        mocktail_running = (
            home is None or _home_path(home, os.environ) == _home_path(None, os.environ)
        ) and bool(running_mocktail_players())
    if 'mocktail' in by_key and mocktail_running:
        return by_key['mocktail']

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
