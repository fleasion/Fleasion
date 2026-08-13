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
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtMultimedia',
    'PySide6.QtNetwork',
    'PySide6.QtQml',
    'PySide6.QtQuick',
    'PySide6.QtQuick3D',
    'PySide6.QtQuickControls2',
    'PySide6.QtWidgets',
]

_QT_QML_MODULES = [
    'Qt.labs.folderlistmodel',
    'Qt.labs.platform',
    'QtQml.Models',
    'QtQml.WorkerScript',
    'QtQuick.Controls.Basic',
    'QtQuick.Controls.FluentWinUI3',
    'QtQuick.Controls.Fusion',
    'QtQuick.Controls.impl',
    'QtQuick.Dialogs',
    'QtQuick.Layouts',
    'QtQuick.Templates',
    'QtQuick.Window',
]

_QT_QML_ROOT_MODULES = [
    'QtMultimedia',
    'QtQml',
    'QtQuick',
    'QtQuick.Controls',
    'QtQuick3D',
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
    'PyQt6',
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
    'PySide6.QAxContainer',
    'PySide6.Qsci',
    'PySide6.Qt3DAnimation',
    'PySide6.Qt3DCore',
    'PySide6.Qt3DExtras',
    'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic',
    'PySide6.Qt3DRender',
    'PySide6.QtBluetooth',
    'PySide6.QtCharts',
    'PySide6.QtDataVisualization',
    'PySide6.QtDesigner',
    'PySide6.QtGraphs',
    'PySide6.QtGraphsWidgets',
    'PySide6.QtHelp',
    'PySide6.QtNetworkAuth',
    'PySide6.QtNfc',
    'PySide6.QtPdf',
    'PySide6.QtPdfWidgets',
    'PySide6.QtPositioning',
    'PySide6.QtPrintSupport',
    'PySide6.QtRemoteObjects',
    'PySide6.QtSensors',
    'PySide6.QtSerialPort',
    'PySide6.QtSpatialAudio',
    'PySide6.QtSql',
    'PySide6.QtStateMachine',
    'PySide6.QtSvg',
    'PySide6.QtSvgWidgets',
    'PySide6.QtTest',
    'PySide6.QtTextToSpeech',
    'PySide6.QtWebChannel',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineQuick',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebSockets',
    'PySide6.QtXml',
    'PySide6.uic',
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
    '/PySide6/Qt/translations/',
    '\\PySide6\\Qt\\translations\\',
    'PySide6/Qt/translations/',
    'PySide6\\Qt\\translations\\',
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
        if 'PySide6/Qt/translations/' in normalised:
            return True
    return False


def _is_unused_qt_runtime_entry(entry: TocEntry) -> bool:
    return _entry_name_matches(
        entry,
        _UNUSED_QT_RUNTIME_NAMES,
    ) or _entry_path_contains(entry, _UNUSED_QT_RUNTIME_PATH_PARTS)


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
    os.environ.get('MACOS_TARGET_ARCH', 'universal2') if sys.platform == 'darwin' else None
)
# Keep UPX's size savings for the Windows one-file build, but leave the native
# Qt/PyQt graphics stack byte-for-byte as shipped by its wheels. The dashboard
# no longer depends on OpenGL at startup, so there is no reason to disable UPX
# for unrelated binaries while we diagnose driver-specific preview failures.
_use_upx = sys.platform == 'win32'
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
    ('src/fleasion/qml', 'fleasion/qml'),
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
# launcher import walk. Collecting the package explicitly also preserves its
# native extensions and the external ``numpy.libs`` runtime directory on
# Windows. The excludes above remove NumPy's test/build-only modules again.
_collect_package('numpy')

# lz4.__init__ imports the platform-specific lz4._version extension during
# package initialization. Collect the package as a unit so _version and
# block/frame native modules are guaranteed to travel with the frozen executable.
_collect_package('lz4')

# Keep Qt collection narrow. collect_all('PySide6') pulls in unrelated modules,
# Designer, SQL drivers, multimedia, translations, and other modules that the
# app does not use, which more than doubles the one-file executable size
hiddenimports.extend(_QT_HIDDEN_IMPORTS)

# PySide6's QtQml hook discovers the full QML tree. Keep the modules used by
# Fleasion and drop unrelated modules after Analysis to control bundle size
_qt_qml_module_paths = tuple(module_name.replace('.', '/') for module_name in _QT_QML_MODULES)
_qt_qml_root_paths = tuple(module_name.replace('.', '/') for module_name in _QT_QML_ROOT_MODULES)


def _is_unused_qt_qml_entry(entry: TocEntry) -> bool:
    for part in entry[:2]:
        normalised = str(part).replace('\\', '/')
        marker = 'PySide6/Qt/qml/'
        if marker not in normalised:
            continue
        relative = normalised.split(marker, maxsplit=1)[1]
        if any(
            relative == module_path or relative.startswith(f'{module_path}/')
            for module_path in _qt_qml_module_paths
        ):
            return False
        return not any(
            relative.startswith(f'{module_path}/') and '/' not in relative[len(module_path) + 1 :]
            for module_path in _qt_qml_root_paths
        )
    return False


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
    _existing_macos_helpers = [helper for helper in _wanted_macos_helpers if helper.exists()]
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
a.binaries = _drop_entries(a.binaries, _is_unused_qt_qml_entry)
a.datas = _drop_entries(a.datas, _is_unused_qt_qml_entry)
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
        'Qt6Network.dll',
        'Qt6Qml.dll',
        'Qt6QmlModels.dll',
        'Qt6Quick.dll',
        'Qt6QuickControls2.dll',
        'Qt6QuickTemplates2.dll',
        'Qt6ShaderTools.dll',
        'Qt6Svg.dll',
        'PyQt6/*.pyd',
        'qwindows.dll',
        'opengl32sw.dll',
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
            'LSMinimumSystemVersion': '12.0',
            'NSHighResolutionCapable': True,
        },
    )
