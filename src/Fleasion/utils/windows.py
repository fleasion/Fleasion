"""Windows-specific utilities."""

import ctypes
import os
import subprocess
import time
from pathlib import Path

from .paths import ROBLOX_PROCESS, STORAGE_DB


def run_cmd(args: list[str]) -> str:
    """Run a Windows command and return its output."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout


def is_roblox_running() -> bool:
    """Check if Roblox is currently running."""
    return ROBLOX_PROCESS in run_cmd(['tasklist', '/FI', f'IMAGENAME eq {ROBLOX_PROCESS}'])


def terminate_roblox() -> bool:
    """Terminate Roblox if it's running. Returns True if it was running."""
    if not is_roblox_running():
        return False
    run_cmd(['taskkill', '/F', '/IM', ROBLOX_PROCESS])
    return True


def wait_for_roblox_exit(timeout: float = 10.0) -> bool:
    """Wait for Roblox to exit. Returns True if it exited before timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_roblox_running():
            return True
        time.sleep(0.5)
    return False


def delete_cache() -> list[str]:
    """Delete Roblox cache with cleanup. Returns list of status messages."""
    messages = []

    if is_roblox_running():
        messages.append('Roblox is running, terminating...')
        terminate_roblox()
        if wait_for_roblox_exit():
            messages.append('Roblox terminated successfully')
        else:
            messages.extend(['Roblox termination timed out', 'Cache deletion aborted'])
            return messages
    else:
        messages.append('Roblox is not running')

    # Delete rbx-storage.db
    if not STORAGE_DB.exists():
        messages.append('Storage database not found')
    else:
        try:
            STORAGE_DB.unlink()
            messages.append('Storage database deleted successfully')
        except PermissionError:
            messages.append('Failed: Permission denied - attempting to unlock...')
            # Try to unlock and retry
            try:
                import win32file
                import win32con
                import pywintypes

                # Try to force unlock by opening with delete sharing
                try:
                    handle = win32file.CreateFile(
                        str(STORAGE_DB),
                        win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                        win32con.FILE_SHARE_DELETE,
                        None,
                        win32con.OPEN_EXISTING,
                        0,
                        None
                    )
                    win32file.CloseHandle(handle)
                except pywintypes.error:
                    pass

                # Retry deletion
                STORAGE_DB.unlink()
                messages.append('Unlocked and deleted successfully')
            except ImportError:
                messages.append('Failed: pywin32 not available for unlock')
            except Exception as e:
                messages.append(f'Failed to unlock: {e}')
        except OSError as e:
            messages.append(f'Failed: {e}')

    # Delete rbx-storage folder
    import shutil
    storage_folder = STORAGE_DB.parent / 'rbx-storage'
    if storage_folder.exists():
        try:
            shutil.rmtree(storage_folder)
            messages.append('Storage folder deleted successfully')
        except PermissionError:
            messages.append('Failed to delete storage folder: Permission denied')
        except OSError as e:
            messages.append(f'Failed to delete storage folder: {e}')
    else:
        messages.append('Storage folder not found')

    return messages


def open_folder(path: Path):
    """Open a folder in Windows Explorer."""
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(path)


def show_message_box(title: str, message: str, icon: int = 0x40):
    """Show a Windows message box."""
    ctypes.windll.user32.MessageBoxW(0, message, title, icon)
