# Fleasion

A Windows application for intercepting and replacing Roblox game assets in real time. Fleasion runs a local proxy that sits between Roblox and its servers, letting you swap textures, audio, meshes, animations, and other assets before they reach the game client.

To request help or request content, join our community <a href="https://discord.com/invite/pdtce585f6">Discord server!</a>

<a href="https://discord.gg/hXyhKehEZF">
    <img src="https://invidget.switchblade.xyz/hXyhKehEZF" alt="Join our Discord server">
</a>

## Requirements

- **Windows** (required &mdash; uses Windows-specific APIs and mitmproxy local mode)
- **Python 3.14+**
- [**uv**](https://docs.astral.sh/uv/) package manager

## Installation & Building

### Standalone Executable

Download `Fleasion.exe` from the [Releases](https://github.com/qrhrqiohj/Fleasion/releases) page. No Python installation required.

### From Source

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

To build a standalone executable, within the "Fleasion" folder:

```bash
# Install PyInstaller
uv add pyinstaller --dev

# Build
uv run pyinstaller Fleasion.spec
```

## System Tray

Fleasion runs in the background as a system tray application (bottom-right corner of your screen). Right-click the tray icon to access:

- **Dashboard** &mdash; configure asset replacements
- **Delete Cache** &mdash; manually clear cached assets
- **Logs** &mdash; view real-time proxy logs
- **About** &mdash; application information
- **Settings** &mdash; theme (System/Light/Dark), auto-delete cache on exit, clear cache on launch, and more

Left-click the tray icon to hide/unhide Fleasion window.

## Important

After applying any changes in the Dashboard, you must **clear your Roblox cache** (or restart Roblox) so assets get re-downloaded through the proxy. Fleasion can handle this automatically:

- **Clear Cache on Launch** (on by default) &mdash; terminates Roblox and deletes `rbx-storage.db` when the proxy starts
- **Auto Delete Cache on Exit** (on by default) &mdash; deletes the cache database when Roblox closes
- Manual cache deletion is available from the tray menu

## How It Works

Fleasion uses [mitmproxy](https://mitmproxy.org/) in local mode to intercept HTTP traffic from `RobloxPlayerBeta.exe`. When Roblox requests assets from its CDN, Fleasion can:

- **Replace** assets by ID &mdash; swap one asset for another (different texture, audio, etc.)
- **Remove** assets &mdash; strip textures from the batch request entirely
- **Redirect** to CDN URLs or local files &mdash; serve your own content
- **Cache** original assets &mdash; browse, preview, and export everything Roblox downloads

The proxy installs a local CA certificate into Roblox's SSL directory to decrypt HTTPS traffic. All interception happens locally on your machine.

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

- **Optimized JSON parsing** using orjson for faster performance
- **Column filtering** &mdash; right-click column headers to show/hide categories (Creator name and Roblox CDN link off by default)
- **Resizable columns** with saved preferences in settings
- **Sortable columns** with persistent adjustment storage

**Automatic format conversion:**

- **KTX textures** (Images, Decals) &mdash; automatically fetches the converted PNG version from the asset delivery API so you get usable image files instead of raw KTX data
- **TexturePacks** &mdash; fetches the XML manifest that maps Color, Normal, Metalness, and Roughness texture IDs, then resolves each individual texture

**Performance:**

- All API conversion calls run in a background thread pool (4 workers) so the proxy never blocks waiting on network requests
- Connection pooling via persistent HTTP sessions reduces overhead on repeated API calls
- O(1) URL-to-asset lookups using hash maps instead of scanning every tracked asset

**What gets cached:**

Every asset type Roblox uses &mdash; images, decals, audio, meshes, animations, shirts, pants, hats, faces, accessories (80+ types). Each asset is stored with its type, original URL, content hash, file size, and capture timestamp. Assets are compressed on disk when larger than 10KB.

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
- **Live preview** for images, meshes (3D viewer), audio (playback), animations (3D rig), and texture packs
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
- Install mitmproxy CA certificates into Roblox's SSL directory
- Show a welcome dialog explaining how the proxy works
- Open the Dashboard automatically

## Project Structure

```
src/Fleasion/
├── app.py                          # Application entrypoint and lifecycle
├── tray.py                         # System tray icon and menu
├── config/
│   └── manager.py                  # Settings persistence and config management
├── proxy/
│   ├── master.py                   # mitmproxy orchestration and certificate setup
│   └── addons/
│       ├── cache_scraper.py        # Asset interception and caching addon
│       └── texture_stripper.py     # Asset replacement and texture removal addon
├── cache/
│   ├── cache_manager.py            # Asset storage, indexing, and export
│   ├── cache_viewer.py             # Cache browsing UI with search and preview
│   ├── animation_viewer.py         # 3D animation preview with R15/R6 rigs
│   ├── audio_player.py             # Audio playback widget
│   ├── obj_viewer.py               # 3D mesh viewer (OpenGL) with orbit/FPS camera
│   ├── mesh_processing.py          # Mesh format conversion (Roblox mesh to OBJ)
│   ├── rbxm_parser.py              # Roblox binary model file parser
│   └── tools/
│       ├── solidmodel_converter/
│       │   ├── obj_to_mesh.py      # OBJ to Roblox V2.00 mesh format converter
│       │   ├── obj_to_csg.py       # OBJ to Roblox CSGMDL converter
│       │   └── csg_mesh.py         # CSGMDL serialization utilities
│       └── animpreview/            # Animation preview assets (R15/R6 OBJ models and rigs)
├── gui/
│   ├── replacer_config.py          # Main Dashboard window with profile management
│   ├── json_viewer.py              # JSON tree viewer with search
│   ├── theme.py                    # Theme management (System/Light/Dark)
│   ├── about.py                    # About dialog
│   ├── logs.py                     # Real-time log viewer
│   └── delete_cache.py             # Cache deletion window
├── prejsons/
│   └── downloader.py               # Community preset downloader
└── utils/
    ├── paths.py                    # Application paths and constants
    ├── logging.py                  # Thread-safe log buffer
    ├── threading.py                # Threading utilities
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
| `Temp/ConvertedMeshes/` | Temporary directory for OBJ/mesh conversions |

## Dependencies

| Package | Purpose |
|---|---|
| mitmproxy | HTTPS proxy framework |
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

- **@8ar__**, **@dis_spencer** &mdash; code
- **@1_v** (Sky), **@Blockce**, **@0100152000022000** (Sky 2), **@emk530**, **@Yeha.** &mdash; logic and contributions
- Donators &mdash; for keeping the passion going

## License

This project is provided as-is for educational and personal use.
