"""Windows Roblox multi-instance watcher independent of the QML presentation."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import sys
import threading

from ..utils.logging import log_buffer


class MultiInstanceController:
    """Remove Roblox's singleton event from concurrently running clients."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def supported(self) -> bool:
        return sys.platform == 'win32'

    def start(self) -> bool:
        if not self.supported:
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._watch,
            name='fleasion-multi-instance',
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _watch(self) -> None:
        stripped: set[int] = set()
        while not self._stop.wait(0.2):
            try:
                pids = self._roblox_pids()
                if len(pids) > 1:
                    for pid in pids - stripped:
                        if self._close_singleton(pid):
                            stripped.add(pid)
                stripped.intersection_update(pids)
            except Exception as exc:
                log_buffer.log('multiinstance', f'Multi-instance watcher failed: {exc}')

    @staticmethod
    def _roblox_pids() -> set[int]:
        kernel32 = ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ('dwSize', wintypes.DWORD),
                ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', wintypes.DWORD),
                ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD),
                ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', wintypes.DWORD),
                ('szExeFile', ctypes.c_wchar * 260),
            ]

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if not snapshot:
            return set()
        result: set[int] = set()
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(ProcessEntry)
            if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    if 'robloxplayerbeta' in entry.szExeFile.lower():
                        result.add(int(entry.th32ProcessID))
                    if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)
        return result

    @staticmethod
    def _close_singleton(pid: int) -> bool:
        kernel32 = ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]
        kernelbase = ctypes.windll.kernelbase  # pyright: ignore[reportAttributeAccessIssue]
        ntdll = ctypes.windll.ntdll  # pyright: ignore[reportAttributeAccessIssue]
        our_handle = kernel32.OpenEventW(0x00100000, False, 'ROBLOX_singletonEvent')
        if not our_handle:
            return False
        process = kernel32.OpenProcess(0x0040 | 0x0400, False, pid)
        if not process:
            kernel32.CloseHandle(our_handle)
            return False

        class HandleEntry(ctypes.Structure):
            _fields_ = [
                ('handleValue', ctypes.c_size_t),
                ('handleCount', ctypes.c_size_t),
                ('pointerCount', ctypes.c_size_t),
                ('grantedAccess', wintypes.ULONG),
                ('objectTypeIndex', wintypes.ULONG),
                ('handleAttributes', wintypes.ULONG),
                ('reserved', wintypes.ULONG),
            ]

        found = False
        try:
            size = 4096
            while True:
                buffer = (ctypes.c_ubyte * size)()
                return_length = wintypes.ULONG(0)
                status = ntdll.NtQueryInformationProcess(
                    process, 51, buffer, size, ctypes.byref(return_length)
                )
                if status != 0xC0000004:
                    break
                size = int(return_length.value) + 4096
            if status != 0:
                return False
            raw = bytes(buffer)
            header_size = ctypes.sizeof(ctypes.c_size_t) * 2
            entry_size = ctypes.sizeof(HandleEntry)
            count = ctypes.c_size_t.from_buffer_copy(raw[: ctypes.sizeof(ctypes.c_size_t)]).value
            for index in range(count):
                offset = header_size + index * entry_size
                entry = HandleEntry.from_buffer_copy(raw[offset : offset + entry_size])
                duplicate = wintypes.HANDLE()
                if not kernel32.DuplicateHandle(
                    process,
                    wintypes.HANDLE(entry.handleValue),
                    ctypes.c_void_p(-1),
                    ctypes.byref(duplicate),
                    0,
                    False,
                    0x00000002,
                ):
                    continue
                same = kernelbase.CompareObjectHandles(our_handle, duplicate)
                kernel32.CloseHandle(duplicate)
                if not same:
                    continue
                closed_duplicate = wintypes.HANDLE()
                kernel32.DuplicateHandle(
                    process,
                    wintypes.HANDLE(entry.handleValue),
                    ctypes.c_void_p(-1),
                    ctypes.byref(closed_duplicate),
                    0,
                    False,
                    0x00000001,
                )
                kernel32.CloseHandle(closed_duplicate)
                found = True
                break
        finally:
            kernel32.CloseHandle(process)
            kernel32.CloseHandle(our_handle)
        return found
