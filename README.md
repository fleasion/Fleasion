# Fleasion

A Windows application for intercepting and replacing Roblox game assets in real time. Fleasion runs a local proxy that sits between Roblox and its servers, letting you swap textures, audio, meshes, animations, and other assets before they reach the game client.

To request help or request content, join our community <a href="https://discord.com/invite/pdtce585f6">Discord server!</a>

<a href="https://discord.gg/hXyhKehEZF">
    <img src="https://invidget.switchblade.xyz/hXyhKehEZF" alt="Join our Discord server">
</a>

## Installation & Building

### Standalone Executable

Download `Fleasion.exe` from the [Releases](https://github.com/qrhrqiohj/Fleasion/releases) page. No Python installation required.

## Requirements for Building from Source

- **Windows** (required &mdash; uses Windows-specific APIs)
- **Python 3.14+**
- [**uv**](https://docs.astral.sh/uv/) package manager

### Building from Source

```bash
# Clone the repository
git clone https://github.com/qrhrqiohj/Fleasion.git
cd fleasion

# Install dependencies with uv
uv sync

# Run the application
uv run Fleasion
```

### Building a Standalone Executable

To build a standalone executable, within the "Fleasion" folder, run:

```bash
uv run pyinstaller Fleasion.spec
```

## System Tray

Fleasion runs in the background as a system tray application (bottom-right corner of your screen). Right-click the tray icon to access:

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

All interception happens locally on your machine. The proxy requires administrator privileges to write the hosts file and bind port 443 &mdash; Fleasion will prompt for UAC elevation on first launch.

**VPN compatibility:** Because interception uses the system's hosts file (application layer), it should be compatible with most VPN software, as long as it respects your Windows hosts file.

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
- **3D Models** (SolidMdodels and Meshes) &mdash; Converts every single Mesh and SolidModel type into .obj files in both directions

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
- Prompt for administrator privileges (required to modify the hosts file and bind port 443)
- Show a welcome dialog explaining how the proxy works
- Open the Dashboard automatically

### Run on Boot

Fleasion can be configured to launch automatically at Windows logon via **Settings → Run on Boot**. This creates a Windows Task Scheduler task with `RunLevel=HighestAvailable`, so the proxy starts elevated without a UAC prompt on every boot. The task updates itself automatically if the installation path or launch method changes.

## Project Structure

```
src/Fleasion/
├── app.py                          # Application entrypoint, UAC elevation, lifecycle
├── tray.py                         # System tray icon and menu
├── config/
│   └── manager.py                  # Settings persistence and config management
├── proxy/
│   ├── master.py                   # Proxy orchestration, hosts file management, cert setup
│   ├── server.py                   # Asyncio TLS proxy server (direct HTTPS interception)
│   └── addons/
│       ├── cache_scraper.py        # Asset interception and caching addon
│       └── texture_stripper.py     # Asset replacement and texture removal addon
├── cache/
│   ├── cache_manager.py            # Asset storage, indexing, and export
│   ├── cache_viewer.py             # Cache browsing UI with search and preview
│   ├── cache_json_viewer.py        # Embedded JSON viewer for cache entries
│   ├── animation_viewer.py         # 3D animation preview with R15/R6 rigs
│   ├── audio_player.py             # Audio playback widget
│   ├── font_viewer.py              # Font file preview widget
│   ├── obj_viewer.py               # 3D mesh viewer (OpenGL) with orbit/FPS camera
│   ├── mesh_processing.py          # Mesh format conversion (Roblox mesh to OBJ)
│   ├── rbxm_parser.py              # Roblox binary model file parser
│   └── tools/
│       ├── orm_compositor.py       # ORM texture channel compositor (metalness/roughness)
│       ├── solidmodel_converter/
│       │   ├── converter.py        # RBXM deserializer entry point
│       │   ├── obj_to_mesh.py      # OBJ to Roblox V2.00 mesh format converter
│       │   ├── obj_to_csg.py       # OBJ to Roblox CSGMDL converter
│       │   ├── csg_mesh.py         # CSGMDL serialization utilities
│       │   ├── mesh_intermediary.py# .mesh/.bin to OBJ intermediary conversion
│       │   └── rbxm/               # RBXM binary format reader/writer
│       │       ├── binary_reader.py
│       │       ├── binary_writer.py
│       │       ├── deserializer.py
│       │       ├── serializer.py
│       │       ├── types.py
│       │       └── xml_writer.py
│       ├── image_to_ktx2/
│       │   └── converter.py        # PNG/image to KTX2 texture converter
│       ├── ktx_to_png/
│       │   └── ktx_to_png.py       # KTX2 texture to PNG converter
│       └── animpreview/            # Animation preview assets (R15/R6 OBJ models and rigs)
│           └── animpreview.py
├── gui/
│   ├── replacer_config.py          # Main Dashboard window with profile management
│   ├── modifications_tab.py        # Client modifications tab (fonts, fflags, global settings)
│   ├── subplace_joiner_tab.py      # Subplace browser and server joiner tab
│   ├── rando_stuff_tab.py          # Misc tab (reserved server rejoin, multi-instance, accounts)
│   ├── prejsons_dialog.py          # Community preset browser dialog
│   ├── json_viewer.py              # JSON tree viewer with search and asset preview
│   ├── theme.py                    # Theme management (System/Light/Dark)
│   ├── about.py                    # About dialog
│   ├── logs.py                     # Real-time log viewer
│   └── delete_cache.py             # Cache deletion window
├── modifications/
│   ├── manager.py                  # Modification orchestration (apply/revert)
│   ├── fflag_manager.py            # Fast flags (FFlag) read/write
│   ├── global_settings_manager.py  # GlobalSettings.json management
│   ├── font_utils.py               # Custom font installation
│   └── dds_to_png.py               # DDS texture to PNG conversion
├── prejsons/
│   └── downloader.py               # Community preset downloader
└── utils/
    ├── paths.py                    # Application paths and constants
    ├── certs.py                    # Local CA and leaf certificate generation
    ├── autostart.py                # Windows Task Scheduler run-on-boot management
    ├── logging.py                  # Thread-safe log buffer
    ├── threading.py                # Threading utilities
    ├── time_tracker.py             # Session time tracking
    ├── anim_converter.py           # Animation format conversion (R6↔R15, KeyframeSeq↔CurveAnim)
    ├── r15_to_r6.py                # R15 to R6 rig conversion utilities
    ├── rig_data.py                 # Rig bone definitions and mappings
    ├── roblox_auth.py              # Centralized helper to get your Roblox Token to use for Roblox V1 APIs
    ├── updater.py                  # Update checker
    └── windows.py                  # Windows-specific operations (process management, cache deletion)
```

## Configuration

Settings are stored in `%LocalAppData%\FleasionNT\`:

| File / Directory | Purpose |
|---|---|
| `settings.json` | Application settings |
| `configs/` | Replacement configuration profiles (JSON) |
| `Cache/` | Cached asset files and index |
| `Exports/` | Exported assets |
| `PreJsons/` | Community preset data |
| `proxy_ca/` | Generated CA certificate and per-host leaf certificates |
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

## Community

- **Discord**: [discord.gg/hXyhKehEZF](https://discord.gg/hXyhKehEZF)
- **Donate**: [ko-fi.com/fleasion](https://ko-fi.com/fleasion)

## Credits

- **@8ar__**, **@dis_spencer**, **@1_v** (Sky) &mdash; code
- **@Blockce**, **@0100152000022000** (Sky 2), **@emk530**, **@Yeha.** &mdash; logic and contributions
- Donators &mdash; for keeping the passion going

## License

This project is provided as-is for educational and personal use.
