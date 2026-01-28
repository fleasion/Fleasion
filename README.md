# Fleasion

A Windows application for intercepting and replacing Roblox game assets in real time. Fleasion runs a local proxy that sits between Roblox and its servers, letting you swap textures, audio, meshes, animations, and other assets before they reach the game client.

## System Tray

Fleasion runs in the background as a system tray application (bottom-right corner of your screen). Right-click the tray icon to access:

- **Dashboard** &mdash; configure asset replacements
- **Cache Viewer** &mdash; browse and export cached assets
- **Logs** &mdash; view real-time proxy logs
- **Settings** &mdash; theme (System/Light/Dark), auto-delete cache on exit, clear cache on launch, and more

## Important

After applying any changes in the Dashboard, you must **clear your Roblox cache** (or restart Roblox) so assets get re-downloaded through the proxy. Fleasion can handle this automatically:

- **Clear Cache on Launch** (on by default) &mdash; terminates Roblox and deletes `rbx-storage.db` when the proxy starts
- **Auto Delete Cache on Exit** (on by default) &mdash; deletes the cache database when Roblox closes
- Manual cache deletion is available from the tray menu and Cache Viewer

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

### Cache Viewer
- Browse all intercepted assets organized by type (80+ Roblox asset types)
- Search and filter by ID, name, type, hash, or URL
- **Live preview** for images, meshes (3D viewer), audio (playback), animations (3D rig), and texture packs
- Asset name resolution via Roblox API
- Export assets in multiple formats (converted, binary, raw)
- Copy converted files directly to clipboard

## Requirements

- **Windows** (required &mdash; uses Windows-specific APIs and mitmproxy local mode)
- **Python 3.14+**
- [**uv**](https://docs.astral.sh/uv/) package manager

## Installation

### From Source

```bash
# Clone the repository
git clone https://github.com/yourusername/fleasion.git
cd fleasion

# Install dependencies with uv
uv sync

# Run the application
uv run Fleasion
```

### Standalone Executable

Download `Fleasion NT.exe` from the [Releases](https://github.com/yourusername/fleasion/releases) page. No Python installation required.

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
│   ├── obj_viewer.py               # 3D mesh viewer (OpenGL)
│   ├── mesh_processing.py          # Mesh format conversion (Roblox mesh to OBJ)
│   ├── rbxm_parser.py              # Roblox binary model file parser
│   └── tools/
│       └── animpreview/            # Animation preview assets (R15/R6 OBJ models and rigs)
├── gui/
│   ├── replacer_config.py          # Main Dashboard window
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

## Building

To build a standalone executable:

```bash
# Install PyInstaller
uv add pyinstaller --dev

# Build (adjust paths and options as needed)
pyinstaller --onefile --name "Fleasion NT" --windowed src/Fleasion/app.py
```

## Community

- **Discord**: [discord.gg/hXyhKehEZF](https://discord.gg/hXyhKehEZF)
- **Donate**: [ko-fi.com/fleasion](https://ko-fi.com/fleasion)

## Credits

Script by Blockce, modified by 8ar

## License

This project is provided as-is for educational and personal use.
