# -*- mode: python ; coding: utf-8 -*-
import os
import sys

_macos_target_arch = os.environ.get('MACOS_TARGET_ARCH') if sys.platform == 'darwin' else None

a = Analysis(
    ['src/Fleasion/macos_proxy_helper_daemon.py'],
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
    name='fleasion-proxy-helper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=_macos_target_arch,
    codesign_identity=None,
    entitlements_file=None,
)
