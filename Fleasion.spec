# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.version import build_artifact_version, macos_bundle_version, read_project_version
from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from typing import TypeAlias, TypeVar

    from PyInstaller.building.api import COLLECT, EXE, PYZ
    from PyInstaller.building.build_main import Analysis
    from PyInstaller.building.osx import BUNDLE

    CollectionEntry: TypeAlias = tuple[str, str]
    TocEntry: TypeAlias = tuple[object, ...]
    TocItem = TypeVar('TocItem', bound=tuple[object, ...])


_QT_HIDDEN_IMPORTS = [
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtNetwork',
    'PyQt6.QtOpenGL',
    'PyQt6.QtWidgets',
]

_OPENGL_HIDDEN_IMPORTS = [
    'OpenGL.platform.glx',
    'OpenGL.platform.egl',
]

_COMPILED_HIDDEN_IMPORTS = [
    'DracoPy',
    'certifi',
    'orjson',
    'zstandard',
]

_WINDOWS_HIDDEN_IMPORTS = [
    'win32crypt',
    'win32api',
    'win32con',
    'win32security',
    'pywintypes',
    'winreg',
]

_BASE_EXCLUDES = [
    'PySide6',
    'PyQt5',
    'mitmproxy',  # removed - replaced by proxy/server.py
    'mitmproxy_rs',  # removed
    'wsproto',  # mitmproxy dep, no longer needed
    'h2',  # mitmproxy dep, no longer needed
    'hyperframe',  # mitmproxy dep, no longer needed
]

_NUMPY_EXCLUDES = [
    'numpy._pyinstaller.tests',
    'numpy.conftest',
    'numpy.f2py',
]

# NumPy's native extensions are sensitive to binary rewriting.  Keep them
# uncompressed so endpoint security products and older Windows loaders see the
# original wheel binaries after PyInstaller extracts the one-file bundle.
_NUMPY_UPX_EXCLUDES = [
    'numpy/*.pyd',
    'numpy/*/*.pyd',
    'numpy/*/*/*.pyd',
    'numpy.libs/*.dll',
]

_QT_EXCLUDES = [
    'PyQt6.QAxContainer',
    'PyQt6.Qsci',
    'PyQt6.Qt3DAnimation',
    'PyQt6.Qt3DCore',
    'PyQt6.Qt3DExtras',
    'PyQt6.Qt3DInput',
    'PyQt6.Qt3DLogic',
    'PyQt6.Qt3DRender',
    'PyQt6.QtBluetooth',
    'PyQt6.QtCharts',
    'PyQt6.QtDataVisualization',
    'PyQt6.QtDesigner',
    'PyQt6.QtGraphs',
    'PyQt6.QtGraphsWidgets',
    'PyQt6.QtHelp',
    'PyQt6.QtMultimedia',
    'PyQt6.QtMultimediaWidgets',
    'PyQt6.QtNetworkAuth',
    'PyQt6.QtNfc',
    'PyQt6.QtPdf',
    'PyQt6.QtPdfWidgets',
    'PyQt6.QtPositioning',
    'PyQt6.QtPrintSupport',
    'PyQt6.QtQml',
    'PyQt6.QtQuick',
    'PyQt6.QtQuick3D',
    'PyQt6.QtQuickWidgets',
    'PyQt6.QtRemoteObjects',
    'PyQt6.QtSensors',
    'PyQt6.QtSerialPort',
    'PyQt6.QtSpatialAudio',
    'PyQt6.QtSql',
    'PyQt6.QtStateMachine',
    'PyQt6.QtSvg',
    'PyQt6.QtSvgWidgets',
    'PyQt6.QtTest',
    'PyQt6.QtTextToSpeech',
    'PyQt6.QtWebChannel',
    'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebEngineQuick',
    'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebSockets',
    'PyQt6.QtXml',
    'PyQt6.uic',
]

_UNUSED_QT_RUNTIME_NAMES = {
    'libqpdf.so',
    'libqtiff.so',
    'libQt6Pdf.so.6',
    'qpdf.dll',
    'qtiff.dll',
    'Qt6Pdf.dll',
    'libqpdf.dylib',
    'libqtiff.dylib',
    'QtPdf.framework',
}

_UNUSED_QT_RUNTIME_PATH_PARTS = (
    '/PyQt6/Qt6/translations/',
    '\\PyQt6\\Qt6\\translations\\',
    'PyQt6/Qt6/translations/',
    'PyQt6\\Qt6\\translations\\',
)

_HOST_AUDIO_LIB_PREFIXES = (
    'libportaudio.so',
    'libasound.so',
    'libjack.so',
    'libpulse.so',
    'libpulsecommon-',
    'libpipewire-',
)


def _run_pyinstaller_spec(spec_path: str, *, env: dict[str, str] | None = None) -> None:
    build_env = os.environ.copy()
    if env:
        build_env.update(env)
    command = [
        sys.executable,
        '-m',
        'fleasion.scripts._pyinstaller',
    ]
    if os.environ.get('FLEASION_CLEAN_BUILD') == '1':
        command.append('--clean')
    command.extend(['--noconfirm', spec_path])
    subprocess.run(
        command,
        check=True,
        env=build_env,
    )


def _collect_package(package: str) -> None:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)


def _collect_optional_package(package: str) -> None:
    if importlib.util.find_spec(package):
        _collect_package(package)


def _entry_name_matches(entry: TocEntry, names: set[str]) -> bool:
    return any(Path(str(part)).name in names for part in entry[:2])


def _entry_path_contains(entry: TocEntry, path_parts: tuple[str, ...]) -> bool:
    for part in entry[:2]:
        text = str(part)
        normalised = text.replace('\\', '/')
        if any(path_part in text for path_part in path_parts):
            return True
        if 'PyQt6/Qt6/translations/' in normalised:
            return True
    return False


def _is_unused_qt_runtime_entry(entry: TocEntry) -> bool:
    return _entry_name_matches(
        entry,
        _UNUSED_QT_RUNTIME_NAMES,
    ) or _entry_path_contains(entry, _UNUSED_QT_RUNTIME_PATH_PARTS)


def _entry_name_startswith(entry: TocEntry, prefixes: tuple[str, ...]) -> bool:
    return any(Path(str(part)).name.startswith(prefixes) for part in entry[:2])


def _drop_entries(
    entries: Iterable[TocItem],
    predicate: Callable[[TocItem], bool],
) -> list[TocItem]:
    return [entry for entry in entries if not predicate(entry)]


def _build_linux_helper() -> None:
    _run_pyinstaller_spec('FleasionLinuxProxyHelper.spec')


def _build_macos_helper(target_arch: str | None) -> None:
    helper_env = {'MACOS_TARGET_ARCH': target_arch} if target_arch else None
    _run_pyinstaller_spec('FleasionDarwinProxyHelper.spec', env=helper_env)
    if target_arch in _bundled_macos_helpers:
        shutil.copy2(_bundled_legacy_macos_helper, _bundled_macos_helpers[target_arch])


try:
    _app_version = read_project_version()
    _artifact_version = build_artifact_version(_app_version)
    _bundle_version = macos_bundle_version(_app_version)
except (OSError, ValueError) as exc:
    raise SystemExit(f'Could not resolve the Fleasion build version: {exc}') from exc
_exe_name = f'Fleasion-v{_artifact_version}'
try:
    _distribution_version = distribution_version('fleasion')
except PackageNotFoundError:
    raise SystemExit('Fleasion distribution metadata is missing. Run uv sync before building.')
if _distribution_version != _app_version:
    raise SystemExit(
        f'Fleasion distribution metadata is {_distribution_version}, but pyproject.toml '
        f'declares {_app_version}. Run uv sync before building.'
    )
if sys.platform == 'win32':
    _exe_name = f'{_exe_name}-Windows'
elif sys.platform.startswith('linux'):
    _exe_name = f'{_exe_name}-Linux'
_macos_target_arch = (
    os.environ.get('MACOS_TARGET_ARCH', 'universal2')
    if sys.platform == 'darwin'
    else None
)
# Do not rewrite bundled Qt/PyQt/OpenGL native binaries with UPX. Fleasion's
# Windows dashboard can fall back between vendor and software OpenGL paths at
# runtime, so preserving the exact wheels' DLL/PYD bytes is worth the larger
# one-file executable for compatibility and diagnosability.
_use_upx = False
_bundled_macos_helpers = {
    'arm64': Path('dist/fleasion-proxy-helper-arm64'),
    'x86_64': Path('dist/fleasion-proxy-helper-x86_64'),
}
_bundled_legacy_macos_helper = Path('dist/fleasion-proxy-helper')
_bundled_linux_helper = Path('dist/fleasion-linux-proxy-helper')

datas: list[CollectionEntry] = [
    ('src/fleasion/fleasionlogoHR.ico', '.'),
    ('src/fleasion/fleasionlogoHR.icns', '.'),
    ('src/fleasion/macos_proxy_helper_daemon.py', '.'),
    ('src/fleasion/cache/tools/animpreview', 'tools/animpreview'),
    ('src/fleasion/modifications/bundled/empty.mp3', 'fleasion/modifications/bundled'),
    ('src/fleasion/modifications/bundled/empty.ogg', 'fleasion/modifications/bundled'),
    ('src/fleasion/modifications/bundled/empty.mesh', 'fleasion/modifications/bundled'),
    ('src/fleasion/modifications/bundled/empty.tex', 'fleasion/modifications/bundled'),
]
datas.extend(copy_metadata('fleasion'))
binaries: list[CollectionEntry] = []
if sys.platform == 'win32':
    binaries.append(('src/fleasion/cache/tools/ktx_to_png/ktx.dll', '.'))
hiddenimports: list[str] = []

# NumPy is imported from feature modules that are not all reached during the
# launcher import walk.  Collecting the package explicitly also preserves its
# native extensions and the external ``numpy.libs`` runtime directory on
# Windows.  The excludes above remove NumPy's test/build-only modules again.
_collect_package('numpy')

# lz4.__init__ imports the platform-specific lz4._version extension during
# package initialization. Some Windows PyInstaller analyses have omitted that
# extension even when lz4.block itself was discovered, causing the replacement
# process used by Env Proxy -> Hosts File mode switches to die before main().
# Collect the package as a unit so _version and block/frame native modules are
# guaranteed to travel with the frozen executable.
_collect_package('lz4')

# Keep Qt collection narrow. collect_all('PyQt6') pulls in QML/QtQuick,
# Designer, SQL drivers, multimedia, translations, and other modules that the
# app does not use, which more than doubles the one-file executable size
hiddenimports.extend(_QT_HIDDEN_IMPORTS)

# PyOpenGL resolves platform backends dynamically. The upstream PyInstaller
# hook includes GLX on Linux, but Wayland sessions can select EGL instead
hiddenimports.extend(collect_submodules('OpenGL.arrays'))
hiddenimports.extend(_OPENGL_HIDDEN_IMPORTS)

# sounddevice/soundfile are single-file modules, but their native runtime
# libraries live in sibling data packages that PyInstaller does not discover
for audio_runtime_package in ('_sounddevice_data', '_soundfile_data'):
    _collect_optional_package(audio_runtime_package)

# certifi provides a bundled public CA store for urllib HTTPS fallbacks
datas.extend(collect_data_files('certifi', includes=['cacert.pem']))
hiddenimports.extend(_COMPILED_HIDDEN_IMPORTS)

if sys.platform == 'win32':
    # win32 extensions (pywin32) - needed for .ROBLOSECURITY cookie decryption
    hiddenimports.extend(_WINDOWS_HIDDEN_IMPORTS)
elif sys.platform == 'darwin':
    _build_macos_helper(_macos_target_arch)
    _wanted_macos_helpers = (
        [_bundled_macos_helpers[_macos_target_arch]]
        if _macos_target_arch in _bundled_macos_helpers
        else list(_bundled_macos_helpers.values())
    )
    _existing_macos_helpers = [
        helper for helper in _wanted_macos_helpers if helper.exists()
    ]
    if not _existing_macos_helpers and _bundled_legacy_macos_helper.exists():
        _existing_macos_helpers = [_bundled_legacy_macos_helper]
    if not _existing_macos_helpers:
        raise SystemExit(
            'Missing dist/fleasion-proxy-helper-arm64 or dist/fleasion-proxy-helper-x86_64. '
            'Fleasion.spec could not build the macOS helper.'
        )
    for helper in _existing_macos_helpers:
        datas.append((str(helper), '.'))
    _collect_package('browser_cookie3')
    _collect_package('Cryptodome')
elif sys.platform.startswith('linux'):
    _build_linux_helper()
    if not _bundled_linux_helper.exists():
        raise SystemExit(
            'Missing dist/fleasion-linux-proxy-helper. '
            'Fleasion.spec could not build the Linux proxy helper.'
        )
    datas.append((str(_bundled_linux_helper), '.'))
    datas.append(('src/fleasion/linux_proxy_helper_daemon.py', '.'))

a = Analysis(
    ['launcher.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[*_BASE_EXCLUDES, *_NUMPY_EXCLUDES, *_QT_EXCLUDES],
    noarchive=False,
    optimize=0,
)

a.binaries = _drop_entries(a.binaries, _is_unused_qt_runtime_entry)
a.datas = _drop_entries(a.datas, _is_unused_qt_runtime_entry)
if sys.platform.startswith('linux'):
    # The sounddevice hook and dependency scan can collect the build machine's
    # audio backend stack. That can silence playback on other distros, so the
    # GUI player uses host PortAudio and host audio backend libraries
    a.binaries = _drop_entries(
        a.binaries,
        lambda entry: _entry_name_startswith(entry, _HOST_AUDIO_LIB_PREFIXES),
    )
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [] if sys.platform == 'darwin' else a.binaries,
    [] if sys.platform == 'darwin' else a.datas,
    [],
    name=_exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=_use_upx,
    upx_exclude=[
        *_NUMPY_UPX_EXCLUDES,
        'Qt6Core.dll',
        'Qt6Gui.dll',
        'Qt6Widgets.dll',
        'Qt6Network.dll',
        'Qt6OpenGL.dll',
        'Qt6Svg.dll',
        'libEGL.dll',
        'libGLESv2.dll',
    ],
    runtime_tmpdir=None,
    console=False,  # no console window for end users
    exclude_binaries=sys.platform == 'darwin',
    # uac_admin is intentionally NOT set here.
    # We handle elevation at runtime in app.py so the user can choose
    # read-only mode if they decline UAC, rather than being blocked entirely
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=_macos_target_arch,
    codesign_identity=None,
    entitlements_file=None,
    icon=(
        ['src/fleasion/fleasionlogoHR.ico']
        if sys.platform == 'win32'
        else ['src/fleasion/fleasionlogoHR.icns']
        if sys.platform == 'darwin'
        else None
    ),
)

if sys.platform == 'darwin':
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=_use_upx,
        name='Fleasion',
    )
    app = BUNDLE(
        coll,
        name='Fleasion.app',
        icon='src/fleasion/fleasionlogoHR.icns',
        bundle_identifier='com.fleasion.app',
        info_plist={
            'CFBundleDisplayName': 'Fleasion',
            'CFBundleName': 'Fleasion',
            'CFBundleShortVersionString': _bundle_version,
            'CFBundleVersion': _bundle_version,
            'LSUIElement': True,
            'NSHighResolutionCapable': True,
        },
    )
