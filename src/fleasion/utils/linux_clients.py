"""Registry and discovery for supported Linux Roblox clients.

Sober is currently the only registered backend. Keeping its identity, paths,
process markers, desktop handlers, and proxy capabilities in a descriptor
provides a small extension seam without spreading client checks throughout the
Linux platform helpers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

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

# A future backend belongs here only after its descriptor and any truly
# client-specific adapters have been implemented and tested.
LINUX_CLIENTS = (SOBER_CLIENT,)
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


def _flatpak_info_succeeds(
    executable: str | Path | None,
    app_id: str,
    run: Callable[..., subprocess.CompletedProcess] | None,
) -> bool:
    if executable is None:
        return False
    runner = subprocess.run if run is None else run
    try:
        result = runner(
            [str(executable), 'info', app_id],
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
        if not (paths.flatpak_root.is_dir() or _flatpak_info_succeeds(flatpak, client.app_id, run)):
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
