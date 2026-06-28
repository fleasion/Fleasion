"""Harden Windows DLL search order for the frozen application.

PyInstaller's Windows bootloader points the process at ``sys._MEIPASS`` with
SetDllDirectoryW. That is the only temporary extraction directory Fleasion
should trust; stale ``_MEI*`` directories inherited from other frozen apps and
DLLs in the current/executable directory must not be implicit candidates.
"""

from __future__ import annotations

import os
import sys


_DLL_DIRECTORY_COOKIES = []


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((os.path.abspath(path), root)) == root
    except ValueError:
        return False


def _add_dll_directory(kernel32, path: str) -> None:
    if not os.path.isdir(path):
        return

    cookie = kernel32.AddDllDirectory(path)
    if cookie:
        _DLL_DIRECTORY_COOKIES.append(cookie)


def _harden_windows_dll_search() -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    try:
        kernel32.SetDefaultDllDirectories.argtypes = [ctypes.c_ulong]
        kernel32.SetDefaultDllDirectories.restype = ctypes.c_int
        kernel32.AddDllDirectory.argtypes = [ctypes.c_wchar_p]
        kernel32.AddDllDirectory.restype = ctypes.c_void_p
        kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel32.SetDllDirectoryW.restype = ctypes.c_int
    except AttributeError:
        return

    load_library_search_user_dirs = 0x00000400
    load_library_search_system32 = 0x00000800

    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return
    meipass = os.path.abspath(meipass)

    qt_root = os.path.join(meipass, "PyQt6", "Qt6")
    if not os.path.isdir(qt_root):
        qt_root = os.path.join(meipass, "PyQt6", "Qt")

    # Exclude the outer .exe directory and the current working directory from
    # implicit DLL lookup, and discard any inherited/stale SetDllDirectoryW
    # value before adding Fleasion's own extraction directory back explicitly.
    kernel32.SetDllDirectoryW("")
    kernel32.SetDefaultDllDirectories(
        load_library_search_user_dirs | load_library_search_system32
    )

    for directory in (
        meipass,
        os.path.join(qt_root, "bin"),
        os.path.join(meipass, "pywin32_system32"),
    ):
        if _is_within(directory, meipass):
            _add_dll_directory(kernel32, directory)


_harden_windows_dll_search()
