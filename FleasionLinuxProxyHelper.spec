from __future__ import annotations

import sys

from PyInstaller.building.api import EXE, PYZ
from PyInstaller.building.build_main import Analysis

_use_upx = sys.platform == 'win32'

a = Analysis(
    ['src/fleasion/linux_proxy_helper_daemon.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
    name='fleasion-linux-proxy-helper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=_use_upx,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
