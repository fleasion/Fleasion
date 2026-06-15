"""Harden Windows DLL search order for the frozen application.

PyInstaller's Windows bootloader points the process at ``sys._MEIPASS`` with
SetDllDirectoryW, but Windows still searches the directory containing the
outer .exe before that directory. For a one-file build launched from Downloads,
that lets unrelated same-named DLLs beside the .exe shadow bundled Qt DLLs.
"""

from __future__ import annotations

import os
import sys


_DLL_DIRECTORY_COOKIES = []


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
    except AttributeError:
        return

    load_library_search_user_dirs = 0x00000400
    load_library_search_system32 = 0x00000800

    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return

    qt_root = os.path.join(meipass, "PyQt6", "Qt6")
    if not os.path.isdir(qt_root):
        qt_root = os.path.join(meipass, "PyQt6", "Qt")

    # Register only directories that belong to the extracted application.
    _add_dll_directory(kernel32, meipass)
    _add_dll_directory(kernel32, os.path.join(qt_root, "bin"))
    _add_dll_directory(kernel32, os.path.join(meipass, "pywin32_system32"))

    # Exclude the outer .exe directory and the current working directory from
    # implicit DLL lookup. Bundled directories above and System32 remain valid.
    kernel32.SetDefaultDllDirectories(
        load_library_search_user_dirs | load_library_search_system32
    )


_harden_windows_dll_search()
