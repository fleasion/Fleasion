# Fleasion

A Windows and macOS application for intercepting and replacing Roblox game assets in real time. Fleasion runs a local proxy that sits between Roblox and its servers, letting you swap textures, audio, meshes, animations, and other assets before they reach the game client.

To request help or request content, join our community <a href="https://discord.com/invite/pdtce585f6">Discord server!</a>

<a href="https://discord.gg/hXyhKehEZF">
    <img src="https://invidget.switchblade.xyz/hXyhKehEZF" alt="Join our Discord server">
</a>

## Installation & Building

### Standalone Executable

Download the current standalone build from the [Releases](https://github.com/fleasion/Fleasion/releases) page. No Python installation required.

If the `.exe` fails to launch on startup with a `DLL load failed` error, move the executable to a different folder, such as your Documents directory. Windows can sometimes pick up bad DLLs from the same directory as the `.exe`, and placing it elsewhere avoids that conflict.

## Requirements for Building from Source

- **Windows 10+ or macOS**
- [**uv**](https://docs.astral.sh/uv/) package manager

### Building from Source

```bash
# Clone the repository
git clone https://github.com/fleasion/Fleasion.git
cd fleasion

# Run the application (auto-installs all dependencies)
uv run Fleasion

# (OPTIONAL) Compile as a standalone Windows executable
uv run pyinstaller Fleasion.spec

# (OPTIONAL) Build the native macOS application bundle
./scripts/build_macos.sh
```

## System Tray

Fleasion runs in the background as a system tray application. Right-click the tray icon to access:

- **Dashboard** &mdash; configure asset replacements
- **Delete Cache** &mdash; manually clear cached assets
- **Logs** &mdash; view real-time proxy logs
- **About** &mdash; application information
- **Settings** &mdash; theme (System/Light/Dark), auto-delete cache on exit, clear cache on launch, run on boot, and more

Left-click the tray icon to hide/unhide Fleasion window.

## Important

After applying any changes in the Dashboard, you must **clear your Roblox cache** (or restart Roblox) so assets get re-downloaded through the proxy. Fleasion can handle this automatically:

- **Clear Cache on Launch** (on by default) &mdash; terminates Roblox and deletes `rbx-storage.db` when the proxy starts
- **Auto Delete Cache on Exit** (on by default) &mdash; deletes the cache database when Roblox closes
- Manual cache deletion is available from the tray menu

## How It Works

Fleasion runs a lightweight custom asyncio HTTPS proxy on `127.0.0.1:443`. On startup it redirects `assetdelivery.roblox.com` and `fts.rbxcdn.com` to localhost via the system hosts file, installs a locally-generated CA certificate into Roblox's SSL directory so the TLS handshake succeeds, and intercepts all asset traffic. When Roblox requests assets from its CDN, Fleasion can:

- **Replace** assets by ID &mdash; swap one asset for another (different texture, audio, etc.)
- **Remove** assets &mdash; strip textures from the batch request entirely
- **Redirect** to CDN URLs or local files &mdash; serve your own content
- **Cache** original assets &mdash; browse, preview, and export everything Roblox downloads

All interception happens locally on your machine. Windows runs Fleasion elevated. On macOS, Fleasion installs a small root-owned relay/hosts helper with one administrator approval; the dashboard and menu-bar app always run as the normal user.

**VPN compatibility:** Because interception uses the system's hosts file (application layer), it should be compatible with most VPN software, as long as it respects the hosts file.

## Features

### Asset Replacement
- Configure replacement rules through the Dashboard GUI
- Replace assets by ID, redirect to external URLs, or serve local files
- Multiple configuration profiles &mdash; switch between different setups
- Import/export configurations as JSON
- Community preset support via PreJsons
- **Creator name column** in configuration list (off by default)
- **Asset name display** next to preview button

### Cache Scraper

The cache scraper is a live interception system that captures every asset Roblox downloads during gameplay. Enable it from the Dashboard and it works automatically in the background while you play.

**Two-stage interception:**

1. **Asset tracking** &mdash; intercepts batch requests to `assetdelivery.roblox.com/v1/assets/batch` to discover asset IDs, their CDN locations, and asset types before anything is downloaded
2. **CDN capture** &mdash; intercepts the actual downloads from `fts.rbxcdn.com`, caching the raw content with full metadata (URL, content type, hash, size, timestamp)

**Features:**

- **Column filtering** &mdash; right-click column headers to show/hide categories (Creator name and Roblox CDN link off by default)
- **Resizable columns** with saved preferences in settings
- **Sortable columns** with persistent adjustment storage

**Automatic format conversion:**

- **KTX textures** (Images, Decals) &mdash; converts KTX textures locally on device into usable PNGs
- **TexturePacks** &mdash; fetches the XML manifest that maps Color, Normal, Metalness, and Roughness texture IDs, then resolves each individual texture
- **3D Models** (SolidModels and Meshes) &mdash; Converts every single Mesh and SolidModel type into .obj files in both directions

**Performance:**

- All API conversion calls run in a background thread pool (4 workers) so the proxy never blocks waiting on network requests
- Connection pooling via persistent HTTP sessions reduces overhead on repeated API calls
- O(1) URL-to-asset lookups using hash maps instead of scanning every tracked asset

**What gets cached:**

Every asset type Roblox uses &mdash; images, decals, audio, meshes, animations, shirts, pants, hats, faces, accessories (80+ types). Each asset is stored with its type, original URL, content hash, file size, and capture timestamp.

### 3D Viewers & Preview

- **Mesh Viewer** (OpenGL-based):
  - 3D mesh preview with orbit and FPS camera modes
  - Wireframe and grid visualization (grid on by default for new users)
  - Optimized rendering with display list caching
  - Vertex color support
  - Auto-rotation capability

- **Animation Viewer**:
  - Live 3D animation playback with R15/R6 rig support
  - **Freecam movement** for better viewing angles
  - **Timescale controls** for slowing down or speeding up animations
  - Grid visualization (on by default)

- **Asset Conversion Support**:
  - **Mesh to CSG** &mdash; auto-convert `.mesh` files to `.obj` before injecting as CSG
  - **CSG to Mesh** &mdash; auto-convert CSG models to `.obj` before mesh replacement
  - **CSG to CSG** &mdash; replace CSG models directly via CDN links
  - **CDN OBJ Support** &mdash; download and convert OBJ files from CDN links (Discord, Cloudflare, etc.)

### Cache Viewer
- Browse all intercepted assets organized by type (80+ Roblox asset types)
- Search and filter by ID, name, type, hash, or URL
- **Live preview** for images, meshes (3D viewer), audio (playback), animations (3D rig), texture packs, and Jsons.
- Asset name resolution via Roblox API
- Export assets in multiple formats (converted, binary, raw)
- Copy converted files directly to clipboard
- **Category filtering** with clickable column header menu

## Usage

1. **Launch Fleasion** &mdash; the application starts in the system tray and automatically begins the proxy
2. **Open the Dashboard** &mdash; right-click the tray icon and select "Dashboard"
3. **Configure replacements** &mdash; add asset IDs you want to replace and specify replacement assets
4. **Launch Roblox** &mdash; the game's traffic will route through the proxy
5. **Clear cache** when changing replacements so Roblox re-downloads assets through the proxy

### First Launch

On first launch, Fleasion will:
- Generate a local CA certificate and install it into Roblox's SSL directory
- On macOS, offer to install the root-owned proxy helper with one administrator approval
- Show a welcome dialog explaining how the proxy works
- Open the Dashboard automatically

### Run on Boot

Fleasion can be configured to launch automatically via **Settings → Run on Boot**. On Windows this creates a Task Scheduler task with `RunLevel=HighestAvailable`. On macOS this creates an unprivileged LaunchAgent; the already-installed proxy helper starts separately as a LaunchDaemon, so boot launches do not request an administrator password.

## Project Structure

```
├── Fleasion.spec                 # PyInstaller specification for the standalone build
├── launcher.py                   # Thin launcher used to start the packaged app
├── pyproject.toml                # Project metadata and dependency configuration
├── README.md                     # Project overview, setup, and usage guide
├── pyinstaller_hooks/
│   └── rthook_harden_dll_search.py  # Runtime hook used by PyInstaller on Windows
├── scripts/
│   └── build_macos.sh            # Helper script for building the macOS app bundle
├── src/
│   └── Fleasion/
│       ├── __init__.py           # Package marker
│       ├── app.py                # Application entrypoint, lifecycle, and startup wiring
│       ├── macos_proxy_helper_daemon.py  # macOS helper daemon for the privileged proxy relay
│       ├── tray.py               # System tray / menu bar icon and menu wiring
│       ├── cache/
│       │   ├── __init__.py       # Cache package marker
│       │   ├── animation_viewer.py  # 3D animation preview with R15/R6 rigs
│       │   ├── audio_player.py      # Audio playback widget
│       │   ├── cache_json_viewer.py # JSON viewer for cached asset metadata
│       │   ├── cache_manager.py     # Asset storage, indexing, and export logic
│       │   ├── cache_viewer.py      # Cache browsing UI with search and preview
│       │   ├── font_viewer.py       # Font file preview widget
│       │   ├── mesh_processing.py   # Mesh format conversion helpers
│       │   ├── obj_viewer.py        # OpenGL mesh viewer with orbit/FPS camera modes
│       │   ├── rbxm_parser.py       # Roblox binary model file parser
│       │   ├── rbxm_preview.py      # Roblox model preview helpers
│       │   ├── roblox_class_names.py # Roblox class name lookup table
│       │   ├── roblox_document.py   # Roblox document helpers for cached content
│       │   └── tools/
│       │       ├── animpreview/
│       │       │   └── animpreview.py  # Animation preview assets and helpers
│       │       ├── image_to_ktx2/
│       │       │   └── converter.py    # Image to KTX2 converter
│       │       ├── ktx_to_png/
│       │       │   └── ktx_to_png.py   # KTX2 to PNG converter
│       │       ├── orm_compositor.py   # ORM texture channel compositor
│       │       └── solidmodel_converter/
│       │           ├── __init__.py     # Solid model converter package marker
│       │           ├── converter.py    # Solid model conversion entrypoint
│       │           ├── csg_mesh.py     # CSG mesh serialization helpers
│       │           ├── mesh_intermediary.py  # Intermediary conversion for .mesh and .bin data
│       │           ├── obj_to_csg.py   # OBJ to Roblox CSG converter
│       │           ├── obj_to_mesh.py   # OBJ to Roblox mesh converter
│       │           └── rbxm/
│       │               ├── __init__.py     # RBXM subpackage marker
│       │               ├── binary_reader.py # RBXM binary reader
│       │               ├── binary_writer.py # RBXM binary writer
│       │               ├── deserializer.py  # RBXM deserializer
│       │               ├── serializer.py    # RBXM serializer
│       │               ├── types.py         # RBXM type definitions
│       │               └── xml_writer.py    # RBXM XML writer
│       ├── config/
│       │   ├── __init__.py       # Config package marker
│       │   └── manager.py        # Settings persistence and config management
│       ├── gui/
│       │   ├── __init__.py       # GUI package marker
│       │   ├── about.py          # About dialog
│       │   ├── delete_cache.py   # Cache deletion window
│       │   ├── json_viewer.py    # JSON tree viewer with search and preview
│       │   ├── logs.py           # Real-time log viewer
│       │   ├── modifications_tab.py  # Client modifications tab
│       │   ├── prejsons_dialog.py    # Community preset browser dialog
│       │   ├── proxy_gate.py     # Proxy gate / connection flow UI
│       │   ├── rando_stuff_tab.py     # Misc tab for extra tools and helpers
│       │   ├── replacer_config.py     # Main Dashboard window with profile management
│       │   ├── settings_tab.py        # Settings tab mirroring tray menu options
│       │   ├── subplace_joiner_tab.py # Subplace browser and joiner tab
│       │   └── theme.py          # Theme management (System / Light / Dark)
│       ├── modifications/
│       │   ├── __init__.py       # Modifications package marker
│       │   ├── dds_to_png.py     # DDS texture to PNG conversion
│       │   ├── fflag_manager.py  # Fast flag read/write helpers
│       │   ├── font_utils.py     # Custom font installation helpers
│       │   ├── global_settings_manager.py  # GlobalSettings.json management
│       │   └── manager.py        # Modification orchestration (apply / revert)
│       ├── prejsons/
│       │   ├── __init__.py       # PreJsons package marker
│       │   └── downloader.py    # Community preset downloader
│       ├── proxy/
│       │   ├── __init__.py       # Proxy package marker
│       │   ├── master.py        # Proxy orchestration, hosts file management, cert setup
│       │   ├── server.py        # Asyncio TLS proxy server
│       │   ├── upstream.py      # Upstream proxy and request forwarding helpers
│       │   ├── windows_proxy.py # Windows-specific proxy integration
│       │   └── addons/
│       │       ├── __init__.py   # Proxy addons package marker
│       │       ├── cache_scraper.py   # Asset interception and caching addon
│       │       ├── texture_stripper.py # Asset replacement and texture removal addon
│       │       └── username_spoofer.py # Username spoofing addon
│       └── utils/
│           ├── __init__.py       # Utilities package marker
│           ├── anim_converter.py # Animation format conversion helpers
│           ├── autostart.py      # Windows Task Scheduler / macOS LaunchAgent run-on-boot helpers
│           ├── certs.py          # Local CA and leaf certificate generation
│           ├── clipboard.py      # Clipboard helper utilities
│           ├── http.py           # HTTP helper utilities
│           ├── logging.py        # Thread-safe log buffer
│           ├── macos_proxy_helper.py  # macOS privileged helper management
│           ├── paths.py          # Application paths and constants
│           ├── platform_macos.py # macOS-specific operations
│           ├── platform_windows.py # Windows-specific operations
│           ├── plural.py         # Pluralization helpers
│           ├── r15_to_r6.py      # R15 to R6 rig conversion helpers
│           ├── rig_data.py       # Rig bone definitions and mappings
│           ├── roblox_auth.py    # Roblox auth token helper for V1 APIs
│           ├── roblox_dirs.py    # Roblox directory discovery helpers
│           ├── threading.py      # Threading utilities
│           ├── time_tracker.py   # Session time tracking
│           ├── updater.py        # Update checker
│           └── windows.py        # Windows compatibility wrapper
├── tests/
│   ├── test_account_cookie_storage.py  # Cookie storage tests
│   ├── test_app_single_instance.py     # Single-instance app behavior tests
│   ├── test_autostart.py               # Run-on-boot tests
│   ├── test_config_manager.py          # Config manager tests
│   ├── test_macos_proxy_helper.py      # macOS helper tests
│   ├── test_modifications_manager.py   # Modification manager tests
│   ├── test_proxy_server.py            # Proxy server tests
│   ├── test_rgba_ktx2.py               # KTX/RGBA conversion tests
│   ├── test_roblox_browser_auth.py     # Roblox browser auth tests
│   ├── test_roblox_document.py         # Roblox document tests
│   ├── test_tray_dashboard.py          # Tray and dashboard integration tests
│   ├── test_upstream.py                # Upstream proxy tests
│   └── test_username_spoofer.py       # Username spoofer tests
└── build/  # Generated PyInstaller output (not source)
```

## Configuration

Settings are stored in `%LocalAppData%\FleasionNT\` on Windows and `~/Library/Application Support/FleasionNT/` on macOS:

| File / Directory | Purpose |
|---|---|
| `settings.json` | Application settings |
| `configs/` | Replacement configuration profiles (JSON) |
| `Cache/` | Cached asset files and index |
| `Exports/` | Exported assets |
| `PreJsons/` | Community preset data |
| `proxy_ca/` | Generated CA certificate and per-host leaf certificates |
| `logs/fleasion.log` | Persistent application and proxy log |
| `Temp/ConvertedMeshes/` | Temporary directory for OBJ/mesh conversions |

## Dependencies

| Package | Purpose |
|---|---|
| cryptography | Local CA and TLS certificate generation |
| PyQt6 | GUI framework |
| PyOpenGL | 3D mesh and animation rendering |
| DracoPy | Mesh decompression (Google Draco) |
| Pillow | Image processing |
| NumPy | Numerical operations |
| pywin32 | Windows API access |
| requests | HTTP client for API calls |
| sounddevice + soundfile | Audio playback |
| lz4 | Compression support |
| orjson | Fast JSON parsing |
| zstandard | CDN payload decompression |
| python-dateutil | Date parsing |

## Community

- **Discord**: [discord.gg/hXyhKehEZF](https://discord.gg/hXyhKehEZF)
- **Donate**: [ko-fi.com/fleasion](https://ko-fi.com/fleasion)

## Credits

- **@8ar__**, **@dis_spencer**, **@1_v** (Sky) &mdash; code
- **@Blockce**, **@0100152000022000** (Sky 2), **@emk530**, **@Yeha.** &mdash; logic and contributions
- Donators &mdash; for keeping the passion going

## License

This project is provided as-is for educational and personal use.
