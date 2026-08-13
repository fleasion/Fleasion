from types import SimpleNamespace

from fleasion.utils.microprofiler import start_microprofiler
import fleasion.utils.microprofiler as microprofiler


class _FakeWindowsFunction:
    def __init__(self):
        self.argtypes = None
        self.restype = None


def test_windows_api_signatures_use_pointer_sized_handles(monkeypatch):
    names = (
        'CreateToolhelp32Snapshot',
        'Thread32First',
        'Thread32Next',
        'OpenThread',
        'GetThreadTimes',
        'GetProcessTimes',
        'CloseHandle',
        'GetCurrentProcess',
    )
    kernel32 = SimpleNamespace(**{name: _FakeWindowsFunction() for name in names})
    monkeypatch.setattr(
        microprofiler.ctypes,
        'windll',
        SimpleNamespace(kernel32=kernel32),
        raising=False,
    )

    result = microprofiler._kernel32()

    assert result is kernel32
    assert kernel32.CreateToolhelp32Snapshot.argtypes == [
        microprofiler.wintypes.DWORD,
        microprofiler.wintypes.DWORD,
    ]
    assert kernel32.Thread32First.argtypes[0] is microprofiler.wintypes.HANDLE
    assert kernel32.OpenThread.argtypes[-1] is microprofiler.wintypes.DWORD
    assert kernel32.CloseHandle.argtypes == [microprofiler.wintypes.HANDLE]
    assert kernel32.GetCurrentProcess.argtypes == []


def test_memory_api_signature_uses_pointer_sized_process_handle(monkeypatch):
    psapi = SimpleNamespace(GetProcessMemoryInfo=_FakeWindowsFunction())
    monkeypatch.setattr(
        microprofiler.ctypes,
        'windll',
        SimpleNamespace(psapi=psapi),
        raising=False,
    )

    result = microprofiler._psapi()

    assert result is psapi
    assert psapi.GetProcessMemoryInfo.argtypes[0] is microprofiler.wintypes.HANDLE
    assert psapi.GetProcessMemoryInfo.argtypes[-1] is microprofiler.wintypes.DWORD


def test_microprofiler_disabled_is_inert(monkeypatch):
    monkeypatch.setattr('fleasion.utils.microprofiler.sys.platform', 'win32')

    assert start_microprofiler(enabled=False) is None


def test_microprofiler_is_inert_outside_windows(monkeypatch):
    monkeypatch.setattr('fleasion.utils.microprofiler.sys.platform', 'linux')

    assert start_microprofiler(enabled=True) is None
