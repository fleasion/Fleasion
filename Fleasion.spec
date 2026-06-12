# -*- mode: python ; coding: utf-8 -*-
import os, re, pathlib, sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

_paths_src = pathlib.Path('src/Fleasion/utils/paths.py').read_text()
_version = re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", _paths_src).group(1)
_macos_target_arch = os.environ.get('MACOS_TARGET_ARCH', 'universal2') if sys.platform == 'darwin' else None
_bundled_macos_helper = pathlib.Path('dist/fleasion-proxy-helper')

datas = [
    ('src/Fleasion/fleasionlogoHR.ico', '.'),
    ('src/Fleasion/fleasionlogoHR.icns', '.'),
    ('src/Fleasion/macos_proxy_helper_daemon.py', '.'),
    ('src/Fleasion/cache/tools/animpreview', 'tools/animpreview'),
    ('src/Fleasion/modifications/bundled/empty.mp3', 'Fleasion/modifications/bundled'),
    ('src/Fleasion/modifications/bundled/empty.ogg', 'Fleasion/modifications/bundled'),
    ('src/Fleasion/modifications/bundled/empty.mesh', 'Fleasion/modifications/bundled'),
    ('src/Fleasion/modifications/bundled/empty.tex', 'Fleasion/modifications/bundled'),
]
binaries = []
if sys.platform == 'win32':
    binaries.append(('src/Fleasion/cache/tools/ktx_to_png/ktx.dll', '.'))
hiddenimports = []

# cryptography has Rust/C binary extensions that must be collected explicitly
tmp_ret = collect_all('cryptography')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# numpy has C-extensions (.pyd files) that must be bundled - without this, the .exe fails with C-extension import errors
tmp_ret = collect_all('numpy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# PyQt6 has many optional sub-packages; collect_all ensures nothing is missed
tmp_ret = collect_all('PyQt6')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# zstandard is a compiled C extension - collect_all ensures the .pyd is bundled
tmp_ret = collect_all('zstandard')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# orjson is a compiled extension - make sure it's included
hiddenimports += collect_submodules('orjson')

# zstandard may be needed for CDN payload decompression
hiddenimports += collect_submodules('zstandard')

# requests + urllib3 for CacheScraper API calls
hiddenimports += collect_submodules('requests')
hiddenimports += collect_submodules('urllib3')

# certifi provides a bundled public CA store for urllib HTTPS fallbacks
tmp_ret = collect_all('certifi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

if sys.platform == 'win32':
    # win32 extensions (pywin32) - needed for .ROBLOSECURITY cookie decryption
    hiddenimports += [
        'win32crypt',
        'win32api',
        'win32con',
        'pywintypes',
        'winreg',
    ]
elif sys.platform == 'darwin':
    if _bundled_macos_helper.exists():
        datas.append((str(_bundled_macos_helper), '.'))
    else:
        raise SystemExit(
            'Missing dist/fleasion-proxy-helper. Build the macOS helper first with '
            'PyInstaller or use ./scripts/build_macos.sh.'
        )
    tmp_ret = collect_all('browser_cookie3')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    tmp_ret = collect_all('Cryptodome')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

a = Analysis(
    ['launcher.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyinstaller_hooks/rthook_harden_dll_search.py'] if sys.platform == 'win32' else [],
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
    [] if sys.platform == 'darwin' else a.binaries,
    [] if sys.platform == 'darwin' else a.datas,
    [],
    name=f'Fleasion-v{_version}',
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
    exclude_binaries=sys.platform == 'darwin',
    # uac_admin is intentionally NOT set here.
    # We handle elevation at runtime in app.py so the user can choose
    # read-only mode if they decline UAC, rather than being blocked entirely.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=_macos_target_arch,
    codesign_identity=None,
    entitlements_file=None,
    icon=(
        ['src/Fleasion/fleasionlogoHR.ico']
        if sys.platform == 'win32'
        else ['src/Fleasion/fleasionlogoHR.icns']
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
        upx=True,
        name='Fleasion',
    )
    app = BUNDLE(
        coll,
        name='Fleasion.app',
        icon='src/Fleasion/fleasionlogoHR.icns',
        bundle_identifier='com.fleasion.app',
        info_plist={
            'CFBundleDisplayName': 'Fleasion',
            'CFBundleName': 'Fleasion',
            'CFBundleShortVersionString': _version,
            'CFBundleVersion': _version,
            'LSUIElement': True,
            'NSHighResolutionCapable': True,
        },
    )
