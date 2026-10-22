from __future__ import annotations

import hashlib
import io
import os
import threading
import zipfile
from typing import TYPE_CHECKING, Self

import pytest

from fleasion.utils import roblox_installer
from fleasion.utils.paths import FORCED_ROBLOX_VERSION, ROBLOX_PROCESS
from fleasion.utils.roblox_installer import (
    PACKAGE_DIRECTORY_MAP,
    Package,
    download_single_package,
    extract_package,
    fetch_manifest,
    get_forced_version_exe_path,
    is_forced_version_installed,
)

if TYPE_CHECKING:
    from pathlib import Path


class _FakeResponse:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self._data = io.BytesIO(payload)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._data.read(size)


def _serve(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    def fake_urlopen(*_: object, **__: object) -> _FakeResponse:
        return _FakeResponse(payload)

    monkeypatch.setattr('urllib.request.urlopen', fake_urlopen)


def _use_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(roblox_installer, 'LOCAL_APPDATA', tmp_path)
    monkeypatch.setattr(roblox_installer, 'CONFIG_DIR', tmp_path / 'config')


def test_package_directory_map_covers_core_packages() -> None:
    assert PACKAGE_DIRECTORY_MAP['RobloxApp.zip'] == ''
    assert PACKAGE_DIRECTORY_MAP['ssl.zip'] == 'ssl'
    assert PACKAGE_DIRECTORY_MAP['shaders.zip'] == 'shaders'


def test_forced_version_is_missing_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr('sys.platform', 'win32')
    assert not is_forced_version_installed()


@pytest.mark.parametrize('use_config_dir', [True, False])
def test_forced_version_is_found_in_either_install_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, use_config_dir: bool
) -> None:
    _use_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr('sys.platform', 'win32')
    base = tmp_path / 'config' if use_config_dir else tmp_path / 'Roblox'
    target_dir = base / 'Versions' / FORCED_ROBLOX_VERSION
    target_dir.mkdir(parents=True)
    exe_path = target_dir / ROBLOX_PROCESS
    exe_path.write_bytes(b'binary')
    assert is_forced_version_installed()
    assert get_forced_version_exe_path() == exe_path


def test_macos_finds_roblox_app_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('sys.platform', 'darwin')
    app = tmp_path / 'Roblox.app'
    macos_dir = app / 'Contents' / 'MacOS'
    macos_dir.mkdir(parents=True)
    exe_file = macos_dir / 'RobloxPlayer'
    exe_file.write_bytes(b'fake-mac-binary')

    def candidate_dirs(local_appdata: Path | None = None) -> list[Path]:
        del local_appdata
        return [app]

    monkeypatch.setattr(roblox_installer, 'get_forced_version_candidate_dirs', candidate_dirs)
    assert get_forced_version_exe_path() == exe_file


def test_extract_package_extracts_into_mapped_directory(tmp_path: Path) -> None:
    staging = tmp_path / 'staging'
    staging.mkdir()
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, 'w') as zf:
        zf.writestr('cacert.pem', 'cert-content')
        zf.writestr('../escape.txt', 'outside')
    zip_file = tmp_path / 'ssl.zip'
    zip_file.write_bytes(zip_bytes.getvalue())

    extract_package(
        Package(name='ssl.zip', signature='abc', packed_size=10, size=20), zip_file, staging
    )

    assert (staging / 'ssl' / 'cacert.pem').read_text() == 'cert-content'
    assert not (staging / 'escape.txt').exists()


def test_fetch_manifest_keeps_only_zip_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(
        monkeypatch,
        (
            'v0\n'
            'RobloxApp.zip\n0dd34f347f8b5c4a8c12b99154ecf1c9\n100\n200\n'
            'RobloxPlayerLauncher.exe\naaa\n1\n2\n'
            'ssl.zip\n7aead7c29ad16cd54c0022c6bb6fbb7c\n50\n80\n'
        ).encode(),
    )
    packages = fetch_manifest(FORCED_ROBLOX_VERSION)
    assert [p.name for p in packages] == ['RobloxApp.zip', 'ssl.zip']
    assert packages[0].signature == '0dd34f347f8b5c4a8c12b99154ecf1c9'
    assert packages[0].packed_size == 100
    assert packages[0].size == 200


def test_download_reports_progress_without_deadlocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b'x' * 200000
    monkeypatch.setattr(roblox_installer, 'get_download_cache_dirs', lambda: [tmp_path / 'cache'])
    _serve(monkeypatch, payload)

    tracker: dict[str, int] = {}
    lock = threading.Lock()
    seen: list[int] = []
    result: list[Path] = []

    def on_progress() -> None:
        with lock:
            seen.append(sum(tracker.values()))

    package = Package(
        name='RobloxApp.zip',
        signature=hashlib.md5(payload).hexdigest(),  # noqa: S324
        packed_size=len(payload),
        size=len(payload),
    )

    def run() -> None:
        result.append(
            download_single_package(
                package, FORCED_ROBLOX_VERSION, tmp_path / 'cache', tracker, lock, on_progress
            )
        )

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(10)

    assert not worker.is_alive()
    assert result[0].read_bytes() == payload
    assert seen[-1] == len(payload)


def _replacer_window_attributes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> set[str]:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6.QtWidgets import QApplication

    from fleasion.config import manager as manager_module
    from fleasion.config.manager import ConfigManager
    from fleasion.gui.replacer_config import ReplacerConfigWindow

    _app = QApplication.instance() or QApplication([])
    config_dir = tmp_path / 'FleasionConfig'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', config_dir / 'configs')
    monkeypatch.setattr('sys.platform', platform)

    window = ReplacerConfigWindow(ConfigManager())
    try:
        return {
            name
            for name in ('install_launch_button', 'footer_close_button')
            if hasattr(window, name)
        }
    finally:
        window.close()


def test_linux_replacer_window_has_no_install_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _replacer_window_attributes(tmp_path, monkeypatch, 'linux') == set()


def test_macos_replacer_window_has_install_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _replacer_window_attributes(tmp_path, monkeypatch, 'darwin') == {
        'install_launch_button',
        'footer_close_button',
    }
