"""Read-only discovery of Stud 2.x's launcher and versioned macOS Player."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

STUD_APP_ID = 'io.github.catpieleaf.Stud'
_STUD_DESKTOP_ID = f'{STUD_APP_ID}.desktop'


@dataclass(frozen=True, slots=True)
class StudSource:
    """An installed Stud launcher with its own XDG data and config roots."""

    command: tuple[str, ...]
    data_root: Path
    config_root: Path
    flatpak: bool

    @property
    def bundle(self) -> Path | None:
        """Resolve Stud's active build, without looking at stale version dirs."""
        current = self.data_root / 'client/current'
        try:
            if current.is_symlink() or not current.is_file():
                return None
            upload = current.read_text(encoding='utf-8').splitlines()[0].strip()
        except OSError, UnicodeError, IndexError:
            return None
        if (
            not upload.startswith('version-')
            or not 8 <= len(upload) <= 100
            or not all(
                character.isascii() and (character.isalnum() or character in '-_')
                for character in upload
            )
        ):
            return None
        candidate = self.data_root / 'client' / upload / 'RobloxPlayer.app'
        if candidate.is_symlink():
            return None
        return (
            candidate
            if (
                (candidate / 'Contents/MacOS/RobloxPlayer').is_file()
                and (candidate / 'Contents/Resources/ssl/cacert.pem').is_file()
            )
            else None
        )

    @property
    def prefix(self) -> Path:
        """Return the only prefix Stud uses for this installation."""
        return self.data_root / 'prefix'


def _native_stud_command(
    home: Path, environ: Mapping[str, str], which: Callable[[str], str | None]
) -> tuple[str, ...]:
    """Find native packages and AppImages without registering URI handlers."""
    executable = which('stud')
    if executable and Path(executable).is_file() and os.access(executable, os.X_OK):
        return (executable,)
    applications = [home / '.local/share/applications']
    applications.extend(
        Path(root) / 'applications'
        for root in environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':')
        if root
    )
    for directory in applications:
        desktop = directory / _STUD_DESKTOP_ID
        try:
            entries = desktop.read_text(encoding='utf-8').splitlines()
        except OSError:
            continue
        line = next((item[5:] for item in entries if item.startswith('Exec=')), '')
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        if not tokens:
            continue
        name = Path(tokens[0]).name.casefold()
        if not (name in {'stud', 'stud-ui'} or name.endswith('.appimage')):
            continue
        if not Path(tokens[0]).is_file() or not os.access(tokens[0], os.X_OK):
            continue
        if len(tokens) == 1 or all(token in {'%u', '%U', '%f', '%F'} for token in tokens[1:]):
            return (tokens[0],)
    return ()


def discover_stud_sources(
    home: Path,
    environ: Mapping[str, str],
    which: Callable[[str], str | None],
    *,
    flatpak_installed: bool,
) -> tuple[StudSource, ...]:
    """Discover separately sandboxed and native Stud installations."""
    found: list[StudSource] = []
    command = _native_stud_command(home, environ, which)
    data = environ.get('XDG_DATA_HOME', '')
    config = environ.get('XDG_CONFIG_HOME', '')
    data_base = Path(data) if data and Path(data).is_absolute() else home / '.local/share'
    config_base = Path(config) if config and Path(config).is_absolute() else home / '.config'
    if command:
        found.append(StudSource(command, data_base / 'stud', config_base / 'stud', flatpak=False))
    flatpak = which('flatpak')
    if flatpak and flatpak_installed:
        sandbox = home / '.var/app' / STUD_APP_ID
        found.append(
            StudSource(
                (flatpak, 'run', STUD_APP_ID),
                sandbox / 'data/stud',
                sandbox / 'config/stud',
                flatpak=True,
            )
        )
    return tuple(found)


def stud_data_root_for_bundle(bundle: Path) -> Path | None:
    """Identify Stud Player bundles from their versioned and isolated paths."""
    if bundle.name != 'RobloxPlayer.app' or not bundle.parent.name.startswith('version-'):
        return None
    if bundle.parent.parent.name != 'client' or bundle.parent.parent.parent.name != 'stud':
        return None
    return bundle.parent.parent.parent


def discover_running_stud_source(  # ruff: ignore[too-many-return-statements]
    bundle: Path,
    player_pid: int,
    *,
    home: Path,
    environ: Mapping[str, str],
    proc_root: Path,
    flatpak_executable: str | None,
    flatpak_installed: bool,
) -> StudSource | None:
    """Recover an unregistered native/AppImage Stud launcher from a verified Player."""
    from .linux_clients import read_rosemary_session  # ruff: ignore[import-outside-top-level]
    from .stud_relaunch import stud_launcher_pid  # ruff: ignore[import-outside-top-level]

    root = stud_data_root_for_bundle(bundle)
    if root is None:
        return None
    session = read_rosemary_session(proc_root / str(player_pid))
    if session is None or session.bundle != bundle:
        return None
    parent_pid = stud_launcher_pid(session, proc_root=proc_root)
    if parent_pid is None:
        return None
    native_data = environ.get('XDG_DATA_HOME', '')
    native_config = environ.get('XDG_CONFIG_HOME', '')
    data = (
        Path(native_data)
        if native_data and Path(native_data).is_absolute()
        else home / '.local/share'
    ) / 'stud'
    config = (
        Path(native_config)
        if native_config and Path(native_config).is_absolute()
        else home / '.config'
    ) / 'stud'
    sandbox = home / '.var/app' / STUD_APP_ID
    if root == sandbox / 'data/stud':
        if not flatpak_executable or not flatpak_installed:
            return None
        return StudSource(
            (flatpak_executable, 'run', STUD_APP_ID),
            root,
            sandbox / 'config/stud',
            flatpak=True,
        )
    if root != data:
        return None
    proc = proc_root / str(parent_pid)
    try:
        items = (proc / 'environ').read_bytes().split(b'\0')
        appimage = next(
            (item[len(b'APPIMAGE=') :] for item in items if item.startswith(b'APPIMAGE=')), b''
        )
        argv = (proc / 'cmdline').read_bytes().split(b'\0')
        raw = appimage or argv[0]
        launch_path = Path(raw.decode('utf-8'))
    except OSError, UnicodeError, IndexError:
        return None
    if (
        not launch_path.is_absolute()
        or not launch_path.is_file()
        or not os.access(launch_path, os.X_OK)
    ):
        return None
    if appimage:
        if not launch_path.name.endswith('.AppImage'):
            return None
    elif launch_path.name not in {'stud', 'stud-ui'} or '/.mount_' in str(launch_path):
        # A transient AppImage mount cannot be reused after Stud exits
        return None
    return StudSource((str(launch_path),), root, config, flatpak=False)
