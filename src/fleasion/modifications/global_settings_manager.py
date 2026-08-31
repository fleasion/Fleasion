"""GlobalBasicSettings manager — reads/writes GlobalBasicSettings_13.xml."""

from __future__ import annotations

import contextlib
import importlib
import shutil
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError

from fleasion.utils import USER_HOME, format_count, log_buffer

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET

from .stash_paths import resource_stash_dir

GLOBAL_SETTINGS_REL = Path('GlobalBasicSettings_13.xml')
DEFAULT_FRAMERATE_CAP = 60


def _framerate_element(root: ET.Element | None) -> ET.Element | None:
    if root is None:
        return None
    return root.find("./Item[@class='UserGameSettings']/Properties/int[@name='FramerateCap']")


def _global_settings_stash_path(stash_dir: Path, roblox_dir: Path) -> Path:
    """Return a collision-free settings stash, migrating legacy Sober data."""
    if not sys.platform.startswith('linux'):
        return Path(stash_dir) / roblox_dir.parent.name / GLOBAL_SETTINGS_REL

    destination = resource_stash_dir(stash_dir, roblox_dir) / GLOBAL_SETTINGS_REL
    if 'org.vinegarhq.Sober' not in roblox_dir.parts or destination.exists():
        return destination

    # Previous Linux builds used the generic parent name (normally ``data``),
    # which collides across Flatpaks.  Claim it only for the known Sober root.
    legacy = Path(stash_dir) / roblox_dir.parent.name / GLOBAL_SETTINGS_REL
    if not legacy.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        legacy.replace(destination)
    except OSError:
        with contextlib.suppress(OSError):
            shutil.copy2(legacy, destination)
    return destination




def _windows_user_roblox_dirs(users_dir: Path) -> list[Path]:
    roblox_dirs: list[Path] = []
    for user_path in users_dir.iterdir():
        if not user_path.is_dir():
            continue
        roblox_local = user_path / 'AppData' / 'Local' / 'Roblox'
        if roblox_local.exists():
            roblox_dirs.append(roblox_local)
    return roblox_dirs


class GlobalSettingsManager:
    """Manage GlobalBasicSettings_13.xml for the current platform/client roots."""

    def __init__(self, stash_dir: Path) -> None:
        self._stash_dir = stash_dir
        self._user_roblox_dirs = self._find_all_user_roblox_dirs()

    def refresh_roblox_dirs(self) -> None:
        """Refresh client-scoped settings roots after a Linux selection change."""
        self._user_roblox_dirs = self._find_all_user_roblox_dirs()

    @staticmethod
    def _find_all_user_roblox_dirs() -> list[Path]:
        """Find user Roblox data directories."""
        roblox_dirs: list[Path] = []
        if sys.platform == 'darwin':
            roblox_local = USER_HOME / 'Library' / 'Roblox'
            if roblox_local.exists():
                roblox_dirs.append(roblox_local)
            else:
                log_buffer.log('GlobalSettings', '~/Library/Roblox directory not found')
            return roblox_dirs

        if sys.platform.startswith('linux'):
            try:
                platform_linux = importlib.import_module('fleasion.utils.platform_linux')
                roblox_dirs.extend(platform_linux.find_linux_global_settings_dirs())
            except (ImportError, AttributeError, OSError) as exc:
                log_buffer.log(
                    'GlobalSettings',
                    f'Could not discover Linux Roblox settings directories: {exc}',
                )
            if not roblox_dirs:
                log_buffer.log('GlobalSettings', 'Linux Roblox settings directories not found')
            return roblox_dirs

        users_dir = Path('C:/Users')

        if not users_dir.exists():
            log_buffer.log('GlobalSettings', 'C:\\Users directory not found')
            return roblox_dirs

        try:
            roblox_dirs.extend(_windows_user_roblox_dirs(users_dir))
        except OSError as exc:
            log_buffer.log('GlobalSettings', f'Error scanning users: {exc}')

        return roblox_dirs

    @staticmethod
    def _remove_read_only(path: Path) -> None:
        """Remove read-only attribute from a file."""
        if path.exists():
            try:
                current = stat.S_IMODE(path.stat().st_mode)
                path.chmod(current | stat.S_IWUSR)
            except OSError:
                pass

    @staticmethod
    def _set_read_only(path: Path, read_only: bool) -> None:
        """Set or remove read-only attribute on a file."""
        if path.exists():
            try:
                if read_only:
                    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                else:
                    current = stat.S_IMODE(path.stat().st_mode)
                    path.chmod(current | stat.S_IWUSR)
            except OSError:
                pass

    @staticmethod
    def _get_read_only_state(path: Path) -> bool:
        """Check if file is read-only."""
        if not path.exists():
            return False
        try:
            current = stat.S_IMODE(path.stat().st_mode)
            # Read-only if no write permissions for owner
            return not (current & stat.S_IWUSR)
        except OSError:
            return False

    def _read_framerate_cap(self, xml_path: Path) -> int | None:
        """Read the current FramerateCap value from GlobalBasicSettings_13.xml."""
        if not xml_path.exists():
            return None

        try:
            root = DefusedElementTree.parse(xml_path).getroot()
        except (ParseError, DefusedXmlException, OSError) as exc:
            log_buffer.log('GlobalSettings', f'Error reading XML: {exc}')
            return None

        element = _framerate_element(root)
        if element is None:
            return None
        try:
            return int(element.text or 0)
        except (TypeError, ValueError):
            return None

    def read_framerate_cap(self) -> int | None:
        """Return the first active persisted cap, if Roblox has one."""
        for roblox_dir in self._user_roblox_dirs:
            cap = self._read_framerate_cap(roblox_dir / GLOBAL_SETTINGS_REL)
            if cap is not None:
                return cap
        return None

    def _write_framerate_cap(self, xml_path: Path, framerate: int) -> None:
        """Write the FramerateCap value to GlobalBasicSettings_13.xml."""
        if not xml_path.exists():
            log_buffer.log('GlobalSettings', f'XML file not found: {xml_path}')
            return

        was_read_only = self._get_read_only_state(xml_path)
        if was_read_only:
            self._remove_read_only(xml_path)

        try:
            tree = DefusedElementTree.parse(xml_path)
        except (ParseError, DefusedXmlException, OSError) as exc:
            log_buffer.log('GlobalSettings', f'Error reading framerate cap: {exc}')
            if was_read_only:
                self._set_read_only(xml_path, read_only=True)
            return

        element = _framerate_element(tree.getroot())
        if element is None:
            log_buffer.log('GlobalSettings', 'FramerateCap element not found in XML')
            if was_read_only:
                self._set_read_only(xml_path, read_only=True)
            return
        element.text = str(framerate)
        try:
            tree.write(xml_path, encoding='utf-8', xml_declaration=True)
        except OSError as exc:
            log_buffer.log('GlobalSettings', f'Error writing framerate cap: {exc}')
        else:
            log_buffer.log(
                'GlobalSettings',
                f'Updated FramerateCap to {framerate} in {xml_path.name}',
            )
        finally:
            if was_read_only:
                self._set_read_only(xml_path, read_only=True)

    def write(self, framerate: int | None) -> None:
        """Write FramerateCap to GlobalBasicSettings_13.xml in all user Roblox dirs."""
        if framerate is None or framerate == 0:
            # Clear the value by restoring originals
            self.restore()
            return

        for roblox_dir in self._user_roblox_dirs:
            dst = roblox_dir / GLOBAL_SETTINGS_REL
            stash = _global_settings_stash_path(self._stash_dir, roblox_dir)

            # Stash original once
            if dst.exists() and not stash.exists():
                stash.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, stash)
                # Also preserve read-only state
                if self._get_read_only_state(dst):
                    with Path(stash).open('a', encoding='utf-8'):
                        pass  # Touch file
                    self._set_read_only(stash, read_only=True)

            # Write the framerate cap
            if dst.exists():
                self._write_framerate_cap(dst, framerate)

        log_buffer.log(
            'GlobalSettings',
            f'Wrote FramerateCap={framerate} to {format_count(self._user_roblox_dirs, "Roblox dir")}',
        )

    def reset_framerate_cap(self) -> None:
        """Set Roblox's framerate cap to its explicit default of 60 FPS.

        This is intentionally separate from ``restore()``.  Restore is used
        during lifecycle cleanup and must not overwrite an untracked setting;
        an explicit UI selection of ``Default`` must correct stale caps left by
        older Fleasion versions.
        """
        reset = 0

        for roblox_dir in self._user_roblox_dirs:
            dst = roblox_dir / GLOBAL_SETTINGS_REL
            stash = _global_settings_stash_path(self._stash_dir, roblox_dir)

            if stash.exists():
                self._remove_read_only(stash)
                stash.unlink()

            if dst.exists():
                self._write_framerate_cap(dst, DEFAULT_FRAMERATE_CAP)
                reset += 1

        log_buffer.log(
            'GlobalSettings',
            (f'Reset FramerateCap={DEFAULT_FRAMERATE_CAP} in {format_count(reset, "Roblox dir")}'),
        )

    def restore(self) -> None:
        """Restore GlobalBasicSettings_13.xml in all user Roblox dirs from stash."""
        for roblox_dir in self._user_roblox_dirs:
            dst = roblox_dir / GLOBAL_SETTINGS_REL
            stash = _global_settings_stash_path(self._stash_dir, roblox_dir)

            if stash.exists():
                # Make sure destination is writable before restoring
                self._remove_read_only(dst)
                shutil.copy2(stash, dst)

                # Restore the read-only state
                if self._get_read_only_state(stash):
                    self._set_read_only(dst, read_only=True)

                stash.unlink()

        log_buffer.log('GlobalSettings', 'Restored GlobalBasicSettings_13.xml')
