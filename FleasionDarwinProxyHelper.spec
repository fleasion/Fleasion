from __future__ import annotations

import os
import sys

from PyInstaller.building.api import EXE, PYZ
from PyInstaller.building.build_main import Analysis

_macos_target_arch = os.environ.get('MACOS_TARGET_ARCH') if sys.platform == 'darwin' else None
_use_upx = sys.platform == 'win32'


a = Analysis(
    ['src/fleasion/macos_proxy_helper_daemon.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=['_ssl'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='fleasion-proxy-helper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=_use_upx,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=_macos_target_arch,
    codesign_identity=None,
    entitlements_file=None,
)
