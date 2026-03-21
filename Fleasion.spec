# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ('src\\Fleasion\\fleasionlogo2.ico', '.'),
    ('src\\Fleasion\\cache\\tools\\animpreview', 'tools/animpreview'),
]
binaries = []
hiddenimports = []

# cryptography has Rust/C binary extensions that must be collected explicitly
tmp_ret = collect_all('cryptography')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# PyQt6 has many optional sub-packages; collect_all ensures nothing is missed
tmp_ret = collect_all('PyQt6')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# orjson is a compiled extension - make sure it's included
hiddenimports += collect_submodules('orjson')

# zstandard may be needed for CDN payload decompression
hiddenimports += collect_submodules('zstandard')

# requests + urllib3 for CacheScraper API calls
hiddenimports += collect_submodules('requests')
hiddenimports += collect_submodules('urllib3')

# win32 extensions (pywin32) - needed for .ROBLOSECURITY cookie decryption
hiddenimports += [
    'win32crypt',
    'win32api',
    'win32con',
    'pywintypes',
    'winreg',
]

a = Analysis(
    ['launcher.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6',
        'PyQt5',
        'mitmproxy',        # removed - replaced by proxy/server.py
        'mitmproxy_rs',     # removed
        'wsproto',          # mitmproxy dep, no longer needed
        'h2',               # mitmproxy dep, no longer needed
        'hyperframe',       # mitmproxy dep, no longer needed
    ],
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
    name='Fleasion',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
    'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll',
    'Qt6Network.dll', 'Qt6OpenGL.dll', 'Qt6Svg.dll',
    'libEGL.dll', 'libGLESv2.dll',
    ],
    runtime_tmpdir=None,
    console=False,          # no console window for end users
    # uac_admin is intentionally NOT set here.
    # We handle elevation at runtime in app.py so the user can choose
    # read-only mode if they decline UAC, rather than being blocked entirely.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['src\\Fleasion\\fleasionlogo2.ico'],
)
