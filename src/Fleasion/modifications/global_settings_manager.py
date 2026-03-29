"""GlobalBasicSettings manager — reads/writes GlobalBasicSettings_13.xml.

Finds all Roblox installations across all user accounts and modifies the
FramerateCap setting in GlobalBasicSettings_13.xml.
"""

from __future__ import annotations

import os
import shutil
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

from ..utils import log_buffer

GLOBAL_SETTINGS_REL = Path('GlobalBasicSettings_13.xml')


class GlobalSettingsManager:
    """Manages the GlobalBasicSettings_13.xml file across all user Roblox installations."""

    def __init__(self, stash_dir: Path):
        self._stash_dir = stash_dir
        self._user_roblox_dirs = self._find_all_user_roblox_dirs()

    @staticmethod
    def _find_all_user_roblox_dirs() -> list[Path]:
        """Scan C:\\Users\\* for AppData\\Local\\Roblox directories."""
        roblox_dirs: list[Path] = []
        users_dir = Path('C:/Users')
        
        if not users_dir.exists():
            log_buffer.log('GlobalSettings', 'C:\\Users directory not found')
            return roblox_dirs
        
        try:
            for user_folder in os.listdir(users_dir):
                user_path = users_dir / user_folder
                if not user_path.is_dir():
                    continue
                
                roblox_local = user_path / 'AppData' / 'Local' / 'Roblox'
                if roblox_local.exists():
                    roblox_dirs.append(roblox_local)
        except OSError as e:
            log_buffer.log('GlobalSettings', f'Error scanning users: {e}')
        
        return roblox_dirs

    @staticmethod
    def _remove_read_only(path: Path) -> None:
        """Remove read-only attribute from a file."""
        if path.exists():
            try:
                current = stat.S_IMODE(os.stat(str(path)).st_mode)
                os.chmod(str(path), current | stat.S_IWUSR)
            except OSError:
                pass

    @staticmethod
    def _set_read_only(path: Path, read_only: bool) -> None:
        """Set or remove read-only attribute on a file."""
        if path.exists():
            try:
                if read_only:
                    os.chmod(str(path), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                else:
                    current = stat.S_IMODE(os.stat(str(path)).st_mode)
                    os.chmod(str(path), current | stat.S_IWUSR)
            except OSError:
                pass

    @staticmethod
    def _get_read_only_state(path: Path) -> bool:
        """Check if file is read-only."""
        if not path.exists():
            return False
        try:
            current = stat.S_IMODE(os.stat(str(path)).st_mode)
            # Read-only if no write permissions for owner
            return not (current & stat.S_IWUSR)
        except OSError:
            return False

    def _read_framerate_cap(self, xml_path: Path) -> int | None:
        """Read the current FramerateCap value from GlobalBasicSettings_13.xml."""
        if not xml_path.exists():
            return None
        
        try:
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            
            # Navigate through the XML structure to find FramerateCap
            # Structure: <roblox><Item class="UserGameSettings"><Properties>
            #            <int name="FramerateCap">240</int>
            for item in root.findall('Item'):
                if item.get('class') == 'UserGameSettings':
                    for props in item.findall('Properties'):
                        for int_elem in props.findall('int'):
                            if int_elem.get('name') == 'FramerateCap':
                                try:
                                    return int(int_elem.text or 0)
                                except (ValueError, TypeError):
                                    return None
            return None
        except Exception as e:
            log_buffer.log('GlobalSettings', f'Error reading XML: {e}')
            return None

    def _write_framerate_cap(self, xml_path: Path, framerate: int) -> None:
        """Write the FramerateCap value to GlobalBasicSettings_13.xml."""
        if not xml_path.exists():
            log_buffer.log('GlobalSettings', f'XML file not found: {xml_path}')
            return
        
        # Check and store read-only state
        was_read_only = self._get_read_only_state(xml_path)
        
        try:
            # Remove read-only temporarily if needed
            if was_read_only:
                self._remove_read_only(xml_path)
            
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            
            found = False
            # Navigate to FramerateCap and update it
            for item in root.findall('Item'):
                if item.get('class') == 'UserGameSettings':
                    for props in item.findall('Properties'):
                        for int_elem in props.findall('int'):
                            if int_elem.get('name') == 'FramerateCap':
                                int_elem.text = str(framerate)
                                found = True
                                break
            
            if found:
                tree.write(str(xml_path), encoding='utf-8', xml_declaration=True)
                log_buffer.log('GlobalSettings', f'Updated FramerateCap to {framerate} in {xml_path.name}')
            else:
                log_buffer.log('GlobalSettings', 'FramerateCap element not found in XML')
            
            # Restore read-only state if it was set
            if was_read_only:
                self._set_read_only(xml_path, True)
        
        except Exception as e:
            log_buffer.log('GlobalSettings', f'Error writing framerate cap: {e}')
            # Try to restore read-only state on error
            if was_read_only:
                try:
                    self._set_read_only(xml_path, True)
                except Exception:
                    pass

    def write(self, framerate: int | None) -> None:
        """Write FramerateCap to GlobalBasicSettings_13.xml in all user Roblox dirs."""
        if framerate is None or framerate == 0:
            # Clear the value by restoring originals
            self.restore()
            return
        
        for roblox_dir in self._user_roblox_dirs:
            dst = roblox_dir / GLOBAL_SETTINGS_REL
            stash = self._stash_dir / roblox_dir.parent.name / GLOBAL_SETTINGS_REL
            
            # Stash original once
            if dst.exists() and not stash.exists():
                stash.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, stash)
                # Also preserve read-only state
                if self._get_read_only_state(dst):
                    with open(stash, 'a'):
                        pass  # Touch file
                    self._set_read_only(stash, True)
            
            # Write the framerate cap
            if dst.exists():
                self._write_framerate_cap(dst, framerate)
        
        log_buffer.log('GlobalSettings', f'Wrote FramerateCap={framerate} to {len(self._user_roblox_dirs)} Roblox dir(s)')

    def restore(self) -> None:
        """Restore GlobalBasicSettings_13.xml in all user Roblox dirs from stash."""
        for roblox_dir in self._user_roblox_dirs:
            dst = roblox_dir / GLOBAL_SETTINGS_REL
            stash = self._stash_dir / roblox_dir.parent.name / GLOBAL_SETTINGS_REL
            
            if stash.exists():
                # Make sure destination is writable before restoring
                self._remove_read_only(dst)
                shutil.copy2(stash, dst)
                
                # Restore the read-only state
                if self._get_read_only_state(stash):
                    self._set_read_only(dst, True)
                
                stash.unlink()
        
        log_buffer.log('GlobalSettings', 'Restored GlobalBasicSettings_13.xml')
