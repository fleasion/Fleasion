import importlib.util
import sys
from pathlib import Path


class _FakeWinFunc:
    def __init__(self, callback):
        self._callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._callback(*args)


class _FakeKernel32:
    def __init__(self):
        self.added_dirs = []
        self.default_masks = []
        self.set_dll_dirs = []
        self._next_cookie = 1
        self.AddDllDirectory = _FakeWinFunc(self._add_dll_directory)
        self.SetDefaultDllDirectories = _FakeWinFunc(self._set_default_dll_directories)
        self.SetDllDirectoryW = _FakeWinFunc(self._set_dll_directory)

    def _add_dll_directory(self, path):
        self.added_dirs.append(path)
        cookie = self._next_cookie
        self._next_cookie += 1
        return cookie

    def _set_default_dll_directories(self, mask):
        self.default_masks.append(mask)
        return 1

    def _set_dll_directory(self, path):
        self.set_dll_dirs.append(path)
        return 1


def _load_runtime_hook(name: str):
    hook_path = Path(__file__).resolve().parents[1] / "pyinstaller_hooks" / "rthook_harden_dll_search.py"
    spec = importlib.util.spec_from_file_location(name, hook_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_runtime_hook_trusts_only_current_meipass(monkeypatch, tmp_path):
    meipass = tmp_path / "_MEI12345"
    qt_bin = meipass / "PyQt6" / "Qt6" / "bin"
    pywin32_dir = meipass / "pywin32_system32"
    qt_bin.mkdir(parents=True)
    pywin32_dir.mkdir()

    fake_kernel32 = _FakeKernel32()

    import ctypes

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=True: fake_kernel32, raising=False)

    module = _load_runtime_hook("fleasion_test_runtime_hook")

    assert fake_kernel32.set_dll_dirs == [""]
    assert fake_kernel32.default_masks == [0x00000400 | 0x00000800]
    assert fake_kernel32.added_dirs == [
        str(meipass),
        str(qt_bin),
        str(pywin32_dir),
    ]
    assert module._DLL_DIRECTORY_COOKIES == [1, 2, 3]


def test_frozen_ktx_loader_does_not_search_executable_directory(monkeypatch, tmp_path):
    from Fleasion.cache.tools.ktx_to_png import ktx_to_png

    source_dir = tmp_path / "source"
    meipass = tmp_path / "_MEI54321"
    exe_dir = tmp_path / "downloads"
    source_dir.mkdir()
    meipass.mkdir()
    exe_dir.mkdir()
    exe_dll = exe_dir / "ktx.dll"
    exe_dll.write_bytes(b"not Fleasion's bundled DLL")

    monkeypatch.setattr(ktx_to_png, "__file__", str(source_dir / "ktx_to_png.py"))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "Fleasion.exe"))

    assert ktx_to_png._find_ktx_dll() is None

    bundled_dll = meipass / "ktx.dll"
    bundled_dll.write_bytes(b"bundled DLL")

    assert ktx_to_png._find_ktx_dll() == str(bundled_dll)
