"""Temporary Windows diagnostics for unexplained idle CPU and memory usage.

This profiler deliberately uses only the Windows API and the Python standard
library so it does not introduce another dependency into the diagnostic build.
It records process memory, every native thread's CPU time, and Python stacks
when a thread belongs to the Python interpreter.  Native Qt/Windows threads
still appear in the output, even though they do not have Python frames.
"""

from __future__ import annotations

import atexit
import ctypes
import json
import os
import platform
import sys
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import FrameType

_SAMPLE_INTERVAL_SECONDS = 1.0
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_QUERY_LIMITED_INFORMATION = 0x0800
_THREAD_QUERY_INFORMATION = 0x0040
_INHERIT_THREAD_HANDLE = False


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class _PythonThreadDetails(TypedDict):
    name: str
    daemon: bool
    python_stack: str


class _Kernel32(Protocol):
    CreateToolhelp32Snapshot: Callable[[int, int], int]
    Thread32First: Callable[[int, object], int]
    Thread32Next: Callable[[int, object], int]
    OpenThread: Callable[[int, bool, int], int]
    GetThreadTimes: Callable[[int, object, object, object, object], int]
    GetProcessTimes: Callable[[object, object, object, object, object], int]
    CloseHandle: Callable[[int], int]
    GetCurrentProcess: Callable[[], int]


class _Psapi(Protocol):
    GetProcessMemoryInfo: Callable[[object, object, int], int]


if TYPE_CHECKING:

    def _kernel32() -> _Kernel32: ...

    def _psapi() -> _Psapi: ...

    def _python_frames() -> Mapping[int, FrameType]: ...

    def _json_values(values: list[str]) -> list[JsonValue]: ...

    def _memory_json(value: dict[str, int] | None) -> JsonObject | None: ...

    def _json_objects(values: list[JsonObject]) -> list[JsonValue]: ...
else:

    def _kernel32() -> _Kernel32:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.Thread32First.restype = wintypes.BOOL
        kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
        kernel32.Thread32Next.restype = wintypes.BOOL
        kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
        kernel32.OpenThread.restype = wintypes.HANDLE
        kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetThreadTimes.restype = wintypes.BOOL
        kernel32.GetThreadTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        return kernel32

    def _psapi() -> _Psapi:
        psapi = ctypes.windll.psapi
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessMemoryCountersEx),
            wintypes.DWORD,
        ]
        return psapi

    def _python_frames() -> Mapping[int, FrameType]:
        current_frames = cast(
            'Callable[[], Mapping[int, FrameType]]',
            vars(sys)['_current_frames'],
        )
        return current_frames()

    def _json_values(values: list[str]) -> list[JsonValue]:
        return values

    def _memory_json(value: dict[str, int] | None) -> JsonObject | None:
        return value

    def _json_objects(values: list[JsonObject]) -> list[JsonValue]:
        return values


class _FileTime(ctypes.Structure):
    _fields_ = [
        ('dwLowDateTime', wintypes.DWORD),
        ('dwHighDateTime', wintypes.DWORD),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ThreadID', wintypes.DWORD),
        ('th32OwnerProcessID', wintypes.DWORD),
        ('tpBasePri', wintypes.LONG),
        ('tpDeltaPri', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
    ]


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ('cb', wintypes.DWORD),
        ('PageFaultCount', wintypes.DWORD),
        ('PeakWorkingSetSize', ctypes.c_size_t),
        ('WorkingSetSize', ctypes.c_size_t),
        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
        ('PagefileUsage', ctypes.c_size_t),
        ('PeakPagefileUsage', ctypes.c_size_t),
        ('PrivateUsage', ctypes.c_size_t),
    ]


def _filetime_seconds(filetime: _FileTime) -> float:
    """Convert a Windows FILETIME to seconds of CPU time."""
    value = (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime
    return value / 10_000_000.0


def _thread_ids(process_id: int) -> list[int]:
    """Return all native thread IDs currently owned by *process_id*."""
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        return []

    result: list[int] = []
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(_ThreadEntry32)
        if not kernel32.Thread32First(snapshot, ctypes.byref(entry)):
            return result
        while True:
            if entry.th32OwnerProcessID == process_id:
                result.append(int(entry.th32ThreadID))
            if not kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def _thread_cpu_seconds(thread_id: int) -> float | None:
    """Read user+kernel CPU seconds for a native thread."""
    kernel32 = _kernel32()
    access = _THREAD_QUERY_INFORMATION | _THREAD_QUERY_LIMITED_INFORMATION
    handle = kernel32.OpenThread(access, _INHERIT_THREAD_HANDLE, thread_id)
    if not handle:
        return None

    try:
        creation = _FileTime()
        exit_time = _FileTime()
        kernel_time = _FileTime()
        user_time = _FileTime()
        if not kernel32.GetThreadTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        return _filetime_seconds(kernel_time) + _filetime_seconds(user_time)
    finally:
        kernel32.CloseHandle(handle)


def _process_cpu_seconds(process_handle: int) -> float | None:
    """Read user+kernel CPU seconds for the current process."""
    kernel32 = _kernel32()
    creation = _FileTime()
    exit_time = _FileTime()
    kernel_time = _FileTime()
    user_time = _FileTime()
    if not kernel32.GetProcessTimes(
        process_handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        return None
    return _filetime_seconds(kernel_time) + _filetime_seconds(user_time)


def _process_memory(process_handle: int) -> dict[str, int] | None:
    """Read working-set/private-memory counters for the current process."""
    psapi = _psapi()
    counters = _ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(_ProcessMemoryCountersEx)
    if not psapi.GetProcessMemoryInfo(
        process_handle,
        ctypes.byref(counters),
        counters.cb,
    ):
        return None
    return {
        'page_fault_count': int(counters.PageFaultCount),
        'peak_working_set_bytes': int(counters.PeakWorkingSetSize),
        'working_set_bytes': int(counters.WorkingSetSize),
        'pagefile_usage_bytes': int(counters.PagefileUsage),
        'peak_pagefile_usage_bytes': int(counters.PeakPagefileUsage),
        'private_bytes': int(counters.PrivateUsage),
    }


def _python_thread_details() -> dict[int, _PythonThreadDetails]:
    """Return names and sampled Python stacks indexed by native thread ID."""
    frames = _python_frames()
    result: dict[int, _PythonThreadDetails] = {}
    for thread in threading.enumerate():
        native_id = thread.native_id
        if native_id is None:
            continue
        ident = thread.ident
        frame = frames.get(ident) if ident is not None else None
        stack = ''
        if frame is not None:
            stack = ''.join(traceback.format_stack(frame, limit=24)).strip()
            stack = stack[-12_000:]
        result[int(native_id)] = {
            'name': thread.name,
            'daemon': thread.daemon,
            'python_stack': stack,
        }
    return result


def _required_process_handle(value: int | None) -> int:
    if TYPE_CHECKING:
        assert value is not None
    return value


def _thread_cpu_percent(record: JsonObject) -> float:
    value = record.get('cpu_percent_one_core', 0.0)
    return float(value) if isinstance(value, int | float) else 0.0


class MicroProfiler:
    """Background sampler that writes one JSON object per line."""

    def __init__(
        self, output_path: Path, interval_seconds: float = _SAMPLE_INTERVAL_SECONDS
    ) -> None:
        self.output_path = output_path
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name='FleasionMicroProfiler',
            daemon=True,
        )
        self._previous_thread_cpu: dict[int, float] = {}
        self._previous_process_cpu: float | None = None
        self._previous_sample_time: float | None = None
        self._process_handle: int | None = None

    def start(self) -> None:
        """Write metadata and start sampling."""
        self._process_handle = _kernel32().GetCurrentProcess()
        self._write(
            {
                'record_type': 'header',
                'timestamp': time.time(),
                'pid': os.getpid(),
                'executable': sys.executable,
                'argv': _json_values(sys.argv),
                'python': sys.version,
                'platform': platform.platform(),
                'interval_seconds': self.interval_seconds,
            }
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop sampling and wait briefly for the final write."""
        self._stop_event.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=max(2.0, self.interval_seconds + 1.0))

    def _write(self, record: JsonObject) -> None:
        with self.output_path.open('a', encoding='utf-8') as output:
            output.write(json.dumps(record, separators=(',', ':'), default=str))
            output.write('\n')

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                self._write(self._sample())
            except (
                Exception  # ruff: ignore[blind-except]
            ) as exc:  # Diagnostics must never terminate Fleasion.
                try:
                    self._write(
                        {
                            'record_type': 'profiler_error',
                            'timestamp': time.time(),
                            'error': f'{type(exc).__name__}: {exc}',
                        }
                    )
                except (OSError, TypeError, ValueError):
                    return

    def _sample(self) -> JsonObject:
        now = time.monotonic()
        process_id = os.getpid()
        python_threads = _python_thread_details()
        thread_records: list[JsonObject] = []
        current_thread_cpu: dict[int, float] = {}

        for thread_id in _thread_ids(process_id):
            cpu_seconds = _thread_cpu_seconds(thread_id)
            if cpu_seconds is None:
                continue
            current_thread_cpu[thread_id] = cpu_seconds
            record: JsonObject = {
                'thread_id': thread_id,
                'cpu_seconds': round(cpu_seconds, 6),
            }
            details = python_threads.get(thread_id)
            if details is not None:
                record['name'] = details['name']
                record['daemon'] = details['daemon']
                record['python_stack'] = details['python_stack']
            if self._previous_sample_time is not None:
                elapsed = now - self._previous_sample_time
                previous_cpu = self._previous_thread_cpu.get(thread_id)
                if previous_cpu is not None and elapsed > 0:
                    record['cpu_percent_one_core'] = round(
                        max(0.0, cpu_seconds - previous_cpu) / elapsed * 100.0,
                        2,
                    )
            thread_records.append(record)

        process_handle = _required_process_handle(self._process_handle)
        process_cpu = _process_cpu_seconds(process_handle)
        process_cpu_percent = None
        if (
            process_cpu is not None
            and self._previous_process_cpu is not None
            and self._previous_sample_time is not None
        ):
            elapsed = now - self._previous_sample_time
            if elapsed > 0:
                process_cpu_percent = round(
                    max(0.0, process_cpu - self._previous_process_cpu) / elapsed * 100.0,
                    2,
                )

        self._previous_thread_cpu = current_thread_cpu
        self._previous_process_cpu = process_cpu
        self._previous_sample_time = now
        thread_records.sort(key=_thread_cpu_percent, reverse=True)

        memory_error = None
        try:
            memory = _process_memory(process_handle)
        except (AttributeError, OSError) as exc:
            memory = None
            memory_error = f'{type(exc).__name__}: {exc}'

        result: JsonObject = {
            'record_type': 'sample',
            'timestamp': time.time(),
            'monotonic_seconds': now,
            'process_cpu_percent_one_core': process_cpu_percent,
            'thread_count': len(thread_records),
            'memory': _memory_json(memory),
            'threads': _json_objects(thread_records),
        }
        if memory_error is not None:
            result['memory_error'] = memory_error
        return result


def _output_path() -> Path:
    """Choose a diagnostic log beside the executable, with a safe fallback."""
    executable_dir = Path(sys.executable).resolve().parent
    filename = f'Fleasion-microprofile-{os.getpid()}.jsonl'
    try:
        executable_dir.mkdir(parents=True, exist_ok=True)
        path = executable_dir / filename
        with path.open('a', encoding='utf-8'):
            pass
    except OSError:
        fallback_dir = Path.home() / 'AppData' / 'Local' / 'FleasionNT' / 'logs'
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return fallback_dir / filename
    return path


def start_microprofiler(
    *,
    enabled: bool,
    interval_seconds: float = _SAMPLE_INTERVAL_SECONDS,
) -> MicroProfiler | None:
    """Start the Windows profiler when explicitly enabled by the caller."""
    if not enabled or sys.platform != 'win32':
        return None
    try:
        profiler = MicroProfiler(_output_path(), interval_seconds)
        profiler.start()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
    atexit.register(profiler.stop)
    return profiler
