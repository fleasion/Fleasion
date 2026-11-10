"""Registry and discovery for supported Linux Roblox clients.

Sober, Nightcap and Cordial support Flatpak metadata and app-scoped proxy
settings. Mocktail and native community clients use isolated XDG roots. Rosemary uses a
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
        """Resolve the host-visible native or Flatpak paths for this descriptor."""
        environment = os.environ if environ is None else environ
        resolved_home = _home_path(home, environment)
        if self.runtime == 'native':
            flatpak_root = resolved_home / '.local' / 'share' / self.xdg_namespace
            config_root = resolved_home / '.config' / self.xdg_namespace
            data_root = flatpak_root if self.key == 'cordial' else flatpak_root / 'android' / 'data'
            cache_root = resolved_home / '.cache' / self.xdg_namespace
        else:
            flatpak_root = resolved_home / '.var' / 'app' / self.app_id
            config_root = flatpak_root / 'config' / self.xdg_namespace
            data_root = flatpak_root / 'data' / self.xdg_namespace
            if self.key == 'nightcap':
                data_root = data_root / 'android' / 'data'
            cache_root = flatpak_root / 'cache' / self.xdg_namespace
        return LinuxClientPaths(
            client=self,
            home=resolved_home,
            flatpak_root=flatpak_root,
            config_root=config_root,
            data_root=data_root,
            cache_root=cache_root,
            config_file=config_root / self.config_filename,
            resource_roots=(
                (data_root.parent.parent / 'asset_overlay',)
                if self.key == 'nightcap'
                else (config_root / 'overlay',)
                if self.key == 'cordial'
                else tuple(data_root / relative for relative in self.resource_relative_paths)
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
    """Host-visible paths for one concrete Linux client installation."""

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
                if self.key == 'cordial':
                    command.append(uri)
                else:
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
        'ALL_PROXY',
        'HTTPS_PROXY',
        'HTTP_PROXY',
        'all_proxy',
        'https_proxy',
        'http_proxy',
        'NO_PROXY',
        'no_proxy',
    ),
    runtime='native',
)


NIGHTCAP_CLIENT = LinuxClientDescriptor(
    key='nightcap',
    display_name='Nightcap',
    app_id='io.github.CoderDayton.nightcap',
    desktop_ids=('io.github.CoderDayton.nightcap.desktop',),
    xdg_namespace='mocktail',
    process_names=('mocktail',),
    engine_process_names=('Main',),
    cgroup_marker='app-flatpak-io.github.CoderDayton.nightcap',
    config_filename='config.yaml',
    resource_relative_paths=(),
    storage_db_relative_path=Path('appData/rbx-storage.db'),
    cache_storage_relative_path=Path('android/rbx-storage'),
    proxy_environment_names=MOCKTAIL_CLIENT.proxy_environment_names,
)

CORDIAL_CLIENT = LinuxClientDescriptor(
    key='cordial',
    display_name='Cordial',
    app_id='io.github.luohoa97.Cordial',
    desktop_ids=('io.github.luohoa97.Cordial.desktop',),
    xdg_namespace='cordial',
    process_names=('cordial-run',),
    engine_process_names=('cordial-run',),
    cgroup_marker='app-flatpak-io.github.luohoa97.Cordial',
    config_filename='shell.json',
    # Cordial's game storage/flags belong to individual named profiles
    # Never treat its build store or profiles as disposable shared cache
    resource_relative_paths=(),
    proxy_environment_names=MOCKTAIL_CLIENT.proxy_environment_names,
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

LINUX_CLIENTS = (SOBER_CLIENT, MOCKTAIL_CLIENT, NIGHTCAP_CLIENT, CORDIAL_CLIENT, ROSEMARY_CLIENT)
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


def _read_rosemary_candidate_cmdline(process: Path) -> bytes | None:
    """Reject unrelated argv prefixes without reading all of their metadata."""
    with (process / 'cmdline').open('rb') as stream:
        preview = stream.read(512)
        if not preview:
            return None
        # Do not filter on comm: Rosemary's guest can rename that field
        if b'\0' in preview and preview.split(b'\0', 1)[0].rsplit(b'/', 1)[-1] != b'rosemary':
            return None
        if process.stat().st_uid != os.getuid():
            return None
        return preview + stream.read()


def _read_rosemary_session(process: Path) -> RosemarySession | None:
    try:
        command_line = _read_rosemary_candidate_cmdline(process)
    except OSError:
        return None
    if command_line is None:
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
        stat_text = (process / 'stat').read_text(encoding='utf-8')
        start_ticks = int(stat_text.rsplit(')', 1)[1].split()[19])
    except OSError, IndexError, ValueError:
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
    process: Path, *, executable: Path | None = None, flatpak_id: str | None = None
) -> MocktailSession | None:
    """Reject helpers, updaters, and PID reuse using /proc/exe and stat."""
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        # Resolve /proc/PID/exe only for plausible Mocktail engine processes
        if (process / 'comm').read_text(encoding='utf-8').strip() != 'Main':
            return None
        if process.stat().st_uid != os.getuid():
            return None
        linked_exe = (process / 'exe').readlink()
        if linked_exe.name != 'mocktail':
            return None
        if executable is not None and not linked_exe.samefile(executable):
            return None
        if flatpak_id is not None:
            cgroup = (process / 'cgroup').read_text(encoding='utf-8', errors='replace')
            if f'app-flatpak-{flatpak_id}-' not in cgroup:
                return None
        command_line = (process / 'cmdline').read_bytes()
        argv = [arg for arg in command_line.split(b'\0') if arg]
        if not argv or Path(os.fsdecode(argv[0])).name != 'mocktail':
            return None
        with (process / 'maps').open(encoding='utf-8', errors='replace') as mapped:
            if not any('libroblox.so' in line.lower() for line in mapped):
                return None
        stat_text = (process / 'stat').read_text(encoding='utf-8')
        start_ticks = int(stat_text.rsplit(')', 1)[1].split()[19])
        return MocktailSession(int(process.name), start_ticks)
    except OSError, ValueError, IndexError:
        return None


def running_mocktail_players(
    *,
    proc_root: Path = Path('/proc'),
    executable: Path | None = None,
    flatpak_id: str | None = None,
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
        if (session := read_mocktail_session(entry, executable=executable, flatpak_id=flatpak_id))
        is not None
    )
    return tuple(sorted(matches, key=lambda session: session.pid))


def running_community_launchers(
    binary_name: Literal['mocktail', 'cordial-shell'],
    *,
    proc_root: Path = Path('/proc'),
    executable: Path | None = None,
    flatpak_id: str | None = None,
) -> tuple[MocktailSession, ...]:
    """Locate only the selected installation's startup launcher.

    The Roblox engine may not have mapped libroblox.so yet; detect the
    strictly verified launcher to arm automatic client switching in time.
    """
    if executable is None and flatpak_id is None:
        return ()
    try:
        processes = proc_root.iterdir()
    except OSError:
        return ()
    sessions: list[MocktailSession] = []
    for proc in processes:
        if not proc.name.isdecimal():
            continue
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            if proc.stat().st_uid != os.getuid():
                continue
            if (proc / 'comm').read_text(encoding='utf-8').strip() != binary_name:
                continue
            exe = proc / 'exe'
            if exe.readlink().name != binary_name:
                continue
            if executable is not None and not exe.samefile(executable):
                continue
            cmdline = (proc / 'cmdline').read_bytes().split(b'\0')
            if not cmdline or Path(os.fsdecode(cmdline[0])).name != binary_name:
                continue
            if flatpak_id is not None:
                cgroup = (proc / 'cgroup').read_text(encoding='utf-8', errors='replace')
                if f'app-flatpak-{flatpak_id}-' not in cgroup:
                    continue
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] in {'Z', 'X', 'x'}:
                continue
            sessions.append(MocktailSession(int(proc.name), int(fields[19])))
        except OSError, IndexError, ValueError:
            continue
    return tuple(sorted(sessions, key=lambda session: session.pid))


def running_cordial_players(
    *,
    proc_root: Path = Path('/proc'),
    executable: Path | None = None,
    flatpak_id: str | None = None,
    native_only: bool = False,
) -> tuple[MocktailSession, ...]:
    """Find only Cordial engine processes, not its GTK launcher or plugins."""
    try:
        processes = list(proc_root.iterdir())
    except OSError:
        return ()
    sessions: list[MocktailSession] = []
    for process in processes:
        if not process.name.isdecimal():
            continue
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            if process.stat().st_uid != os.getuid():
                continue
            if (process / 'comm').read_text(encoding='utf-8').strip() not in {
                'cordial-run',
                'Main',
            }:
                continue
            exe = (process / 'exe').readlink()
            if exe.name != 'cordial-run':
                continue
            if executable is not None and not (process / 'exe').samefile(executable):
                continue
            argv = (process / 'cmdline').read_bytes().split(b'\0')
            if not argv or Path(os.fsdecode(argv[0])).name != 'cordial-run':
                continue
            if flatpak_id is not None or native_only:
                cgroup = (process / 'cgroup').read_text(encoding='utf-8', errors='replace')
                if native_only and 'app-flatpak-' in cgroup:
                    continue
                if flatpak_id is not None and f'app-flatpak-{flatpak_id}-' not in cgroup:
                    continue
            with (process / 'maps').open(encoding='utf-8', errors='replace') as maps:
                if not any('libroblox.so' in line.lower() for line in maps):
                    continue
            fields = (process / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] in {'Z', 'X', 'x'}:
                continue
            sessions.append(MocktailSession(int(process.name), int(fields[19])))
        except OSError, IndexError, ValueError:
            continue
    return tuple(sorted(sessions, key=lambda session: session.pid))


def running_installed_cordial_players(
    installation: LinuxClientInstallation, *, proc_root: Path = Path('/proc')
) -> tuple[MocktailSession, ...]:
    """Enumerate engines belonging to this exact native or Flatpak installation."""
    if installation.key != 'cordial':
        return ()
    if installation.client.runtime == 'flatpak':
        return running_cordial_players(proc_root=proc_root, flatpak_id=installation.app_id)
    shell = installation.executable
    if shell is None or shell.name != 'cordial-shell':
        return ()
    engine = shell.with_name('cordial-run')
    if not engine.is_file():
        return ()
    return running_cordial_players(proc_root=proc_root, executable=engine, native_only=True)


@dataclass(frozen=True, slots=True)
class SoberSession:
    """A validated Sober process belonging to its Flatpak cgroup."""

    pid: int
    start_ticks: int


def _running_sober_sessions(*, proc_root: Path, launcher: bool | None) -> tuple[SoberSession, ...]:
    """Identify either the Roblox engine or its executable-backed Sober launcher."""
    try:
        processes = list(proc_root.iterdir())
    except OSError:
        return ()
    sessions: list[SoberSession] = []
    expected_comms = {'sober', 'Main'} if launcher is None else {'sober' if launcher else 'Main'}
    for process in processes:
        if not process.name.isdecimal():
            continue
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            comm = (process / 'comm').read_text(encoding='utf-8').strip()
            if comm not in expected_comms:
                continue
            if process.stat().st_uid != os.getuid():
                continue
            cgroup = (process / 'cgroup').read_text(encoding='utf-8', errors='replace')
            if f'{SOBER_CLIENT.cgroup_marker}-' not in cgroup:
                continue
            # Flatpak /app paths exist inside Sober's mount namespace, not ours
            if comm == 'sober' and (process / 'exe').readlink().name != 'sober':
                continue
            stat_text = (process / 'stat').read_text(encoding='utf-8')
            fields = stat_text.rsplit(')', 1)[1].split()
            if fields[0] in {'Z', 'X', 'x'}:
                continue
            start_ticks = int(fields[19])
        except OSError, ValueError, IndexError:
            continue
        sessions.append(SoberSession(int(process.name), start_ticks))
    return tuple(sorted(sessions, key=lambda session: session.pid))


def running_sober_players(*, proc_root: Path = Path('/proc')) -> tuple[SoberSession, ...]:
    """Find Sober's engine, excluding unrelated processes also named Main."""
    return _running_sober_sessions(proc_root=proc_root, launcher=False)


def running_sober_launchers(*, proc_root: Path = Path('/proc')) -> tuple[SoberSession, ...]:
    """Find Sober itself, including its pre-engine launcher window."""
    return _running_sober_sessions(proc_root=proc_root, launcher=True)


def sober_flatpak_session_identity(*, proc_root: Path = Path('/proc')) -> str | None:
    """Identify one verified Sober Flatpak scope across launcher/engine transitions.

    When several distinct Sober instances are active, refuse to infer which
    instance is owned; a broad Flatpak kill could terminate unrelated sessions.
    """
    sessions = _running_sober_sessions(proc_root=proc_root, launcher=None)
    prefix = f'{SOBER_CLIENT.cgroup_marker}-'
    scopes: set[str] = set()
    for session in sessions:
        try:
            cgroups = (proc_root / str(session.pid) / 'cgroup').read_text(
                encoding='utf-8', errors='replace'
            )
        except OSError:
            continue
        for component in cgroups.replace('\n', '/').split('/'):
            if component.startswith(prefix) and component.endswith('.scope'):
                scopes.add(component)
    return next(iter(scopes)) if len(scopes) == 1 else None


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
        Path(xdg_config) if xdg_config and Path(xdg_config).is_absolute() else home / '.config'
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
    except OSError, UnicodeDecodeError, ValueError:
        return None

    xdg_data = environment.get('XDG_DATA_HOME', '')
    data_home = (
        Path(xdg_data) if xdg_data and Path(xdg_data).is_absolute() else home / '.local/share'
    )
    prefix = data_home / 'nutmeg' / 'prefix'
    configured_prefix = (
        prefix_path or environment.get('ROSEMARY_PREFIX') or settings.get('prefix_path')
    )
    if configured_prefix:
        candidate = (
            Path(configured_prefix).expanduser() if isinstance(configured_prefix, str) else None
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
        else None
    )
    running_binary = next(
        (
            str(session.launcher)
            for session in sorted(sessions, key=lambda s: s.start_ticks, reverse=True)
            if session.launcher.is_file()
        ),
        None,
    )
    executable_path = binary or running_binary or which('rosemary')
    executable = Path(executable_path) if executable_path is not None else None
    configured_bundle = _rosemary_bundle(str(app_path)) if app_path else None
    bundle = (
        configured_bundle
        or next(
            (
                session.bundle
                for session in sorted(sessions, key=lambda s: s.start_ticks, reverse=True)
            ),
            None,
        )
        or _nutmeg_app_bundle(home)
    )
    if not (desktop or sessions or (executable is not None and bundle is not None)):
        return None
    paths = ROSEMARY_CLIENT.paths(home=home, environ=environ)
    paths = replace(paths, resource_roots=(bundle / 'Contents' / 'Resources',) if bundle else ())
    return LinuxClientInstallation(
        ROSEMARY_CLIENT, paths, executable, bundle, desktop and configured_bundle is None
    )


def native_nightcap_desktop(home: Path, environ: Mapping[str, str]) -> bool:
    roots = [home / '.local/share/applications']
    roots.extend(
        Path(root) / 'applications'
        for root in environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':')
        if root
    )
    for root in roots:
        desktop = root / NIGHTCAP_CLIENT.desktop_id
        if (root / MOCKTAIL_CLIENT.desktop_id).is_file():
            continue
        try:
            contents = desktop.read_text(encoding='utf-8')
        except OSError:
            continue
        # Ignore Flatpak-exported desktop files that still name the app ID
        if any(
            line.startswith('Exec=') and 'mocktail' in line and 'flatpak' not in line
            for line in contents.splitlines()
        ):
            return True
    return False


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
    """Detect installed Flatpak and native Linux Roblox clients."""
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
            native_nightcap = native_nightcap_desktop(resolved_home, environment)
            native_client = (
                replace(NIGHTCAP_CLIENT, runtime='native') if native_nightcap else MOCKTAIL_CLIENT
            )
            if not any(item.key == native_client.key for item in detected):
                detected.append(
                    LinuxClientInstallation(
                        client=native_client,
                        paths=native_client.paths(home=home, environ=environment),
                        executable=binary,
                    )
                )
    # The Cordial shell, not cordial-run, is the public CLI/deep-link entry
    cordial_shell = find_executable('cordial-shell')
    if cordial_shell and not any(item.key == 'cordial' for item in detected):
        shell = Path(cordial_shell)
        if shell.is_file() and os.access(shell, os.X_OK):
            native = replace(CORDIAL_CLIENT, runtime='native')
            detected.append(
                LinuxClientInstallation(
                    client=native,
                    paths=native.paths(home=home, environ=environment),
                    executable=shell,
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


def select_linux_client(
    selection: str = 'auto',
    *,
    installed: Sequence[LinuxClientInstallation] | None = None,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: CommandRunner | None = None,
    proc_root: Path = Path('/proc'),
    running_client_starts: Mapping[str, int] | None = None,
    observed_running_starts: dict[str, int] | None = None,
    recent_client_key: str | None = None,
    rosemary_running: bool | None = None,
    mocktail_running: bool | None = None,
) -> LinuxClientInstallation | None:
    """Prefer the newest verified Player in auto mode, then the desktop handler."""
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
            proc_root=proc_root,
        )
    )
    by_key = {installation.key: installation for installation in installations}
    if normalized != 'auto':
        return by_key.get(normalized)
    if len(installations) <= 1:
        return installations[0] if installations else None

    starts = dict(running_client_starts) if running_client_starts is not None else {}
    current_home = home is None or _home_path(
        home, os.environ if environ is None else environ
    ) == _home_path(None, os.environ)
    if running_client_starts is None and (current_home or proc_root != Path('/proc')):
        if 'rosemary' in by_key:
            rosemary_bundle = by_key['rosemary'].app_bundle
            rosemary_sessions = tuple(
                session
                for session in running_rosemary_players(proc_root=proc_root)
                if rosemary_bundle is None or session.bundle == rosemary_bundle
            )
            if rosemary_sessions:
                starts['rosemary'] = max(session.start_ticks for session in rosemary_sessions)
        for key in ('mocktail', 'nightcap'):
            if key not in by_key:
                continue
            installation = by_key[key]
            sessions = running_mocktail_players(
                proc_root=proc_root,
                executable=installation.executable
                if installation.client.runtime == 'native'
                else None,
                flatpak_id=installation.app_id
                if installation.client.runtime == 'flatpak'
                else None,
            )
            launchers = running_community_launchers(
                'mocktail',
                proc_root=proc_root,
                executable=installation.executable
                if installation.client.runtime == 'native'
                else None,
                flatpak_id=installation.app_id
                if installation.client.runtime == 'flatpak'
                else None,
            )
            if sessions or launchers:
                starts[key] = max(session.start_ticks for session in (*sessions, *launchers))
        if 'cordial' in by_key:
            installation = by_key['cordial']
            sessions = running_installed_cordial_players(installation, proc_root=proc_root)
            launchers = running_community_launchers(
                'cordial-shell',
                proc_root=proc_root,
                executable=installation.executable
                if installation.client.runtime == 'native'
                else None,
                flatpak_id=installation.app_id
                if installation.client.runtime == 'flatpak'
                else None,
            )
            if sessions or launchers:
                starts['cordial'] = max(session.start_ticks for session in (*sessions, *launchers))
        if 'sober' in by_key:
            # The native Sober window runs before its Android Main process starts
            # It still represents the most recently opened Roblox client
            sober_sessions = _running_sober_sessions(proc_root=proc_root, launcher=None)
            if sober_sessions:
                starts['sober'] = max(session.start_ticks for session in sober_sessions)
    # Preserve the existing explicit process-presence overrides for callers
    # which cannot supply start ticks
    for key, override in (('rosemary', rosemary_running), ('mocktail', mocktail_running)):
        if override is True:
            starts.setdefault(key, 0)
        elif override is False:
            starts.pop(key, None)
    active = {key: ticks for key, ticks in starts.items() if key in by_key}
    if observed_running_starts is not None:
        # Reuse verified process presence instead of enumerating /proc twice
        observed_running_starts.update(active)
    if active:
        newest = max(active.values())
        newest_keys = {key for key, ticks in active.items() if ticks == newest}
        if len(newest_keys) == 1:
            return by_key[next(iter(newest_keys))]
    else:
        # Keep the last detected client after it exits; otherwise Auto would
        # clear the desktop handler's cache instead of the just-closed Player
        newest_keys = {recent_client_key} if recent_client_key in by_key else set[str]()

    handlers = query_default_roblox_handlers(
        home=home,
        environ=environ,
        which=which,
        run=run,
    )
    for handler in handlers:
        for installation in installations:
            if (not newest_keys or installation.key in newest_keys) and (
                handler in installation.client.desktop_ids
            ):
                return installation
    for descriptor in LINUX_CLIENTS:
        if descriptor.key in by_key and (not newest_keys or descriptor.key in newest_keys):
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
