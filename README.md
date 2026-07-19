# Fleasion

A Windows, macOS, and Linux/Sober application for intercepting and replacing Roblox game assets in real time. Fleasion runs a local proxy that sits between Roblox and its servers, letting you swap textures, audio, meshes, animations, and other assets before they reach the game client.

To request help or request content, join our community <a href="https://discord.com/invite/pdtce585f6">Discord server!</a>

<a href="https://discord.gg/hXyhKehEZF">
    <img src="https://invidget.switchblade.xyz/hXyhKehEZF" alt="Join our Discord server">
</a>

## Installation & Building

### Standalone Executable

Download the current standalone build from the [Releases](https://github.com/fleasion/Fleasion/releases) page. No Python installation required.

If the `.exe` fails to launch on startup with a `DLL load failed` error, move the executable to a different folder, such as your Documents directory. Windows can sometimes pick up bad DLLs from the same directory as the `.exe`, and placing it elsewhere avoids that conflict. Also clear your windows `%temp%` directory to remove any stale MEI files.

If you're on Linux and having issues with launching the GUI, please install `PortAudio` on your distro. How? Look it up.

## Requirements for Building from Source

- **Windows 10+, macOS 11+, or Linux with Sober Flatpak**
- [**uv**](https://docs.astral.sh/uv/) package manager
- **Python 3.14+**
- Linux desktop installs need `pkexec`/Polkit available (installed by default on Mint and most desktop distributions)

### Building from Source

```bash
# Clone the repository
git clone https://github.com/fleasion/Fleasion.git
cd Fleasion

# Run the application (auto-installs all dependencies)
uv run fleasion

# (OPTIONAL) Compile as a standalone application
uv run build
```

`uv run build` is the build command on Windows, Linux, and macOS. On macOS, it builds a universal release app by default. The output is copied to `dist/Fleasion-v{APP_VERSION}.app`, mirrored at `dist/Fleasion.app`, and zipped as `dist/Fleasion-v{APP_VERSION}-MacOS-Universal.zip`.

On Apple Silicon, the command builds the arm64 slice with the normal `uv` environment, bootstraps an ignored x86_64 build environment under `.tools/`, and resolves Python for both from the tracked `.python-version` pin. It builds the Intel slice under Rosetta, merges the app with `lipo`, signs it ad hoc, and verifies every Mach-O binary contains both `arm64` and `x86_64`. Rosetta must be installed for the Intel build:

```bash
softwareupdate --install-rosetta --agree-to-license
```

For local single-architecture builds, set `MACOS_TARGET_ARCH=arm64` or `MACOS_TARGET_ARCH=x86_64`.

To bump Fleasion for a release, update the project version once:

```bash
uv version --bump patch
# or: uv version <new-version>
```

Runtime code, PyInstaller naming, and GitHub release workflows read that value from `pyproject.toml`.

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

Fleasion runs a lightweight custom asyncio HTTPS proxy on `127.0.0.1:443`. On startup it redirects `assetdelivery.roblox.com` and `fts.rbxcdn.com` to localhost via the system hosts file, installs a locally-generated CA certificate into Roblox's `ssl/cacert.pem` trust bundle so the TLS handshake succeeds, and intercepts all asset traffic. When custom FastFlags are enabled, it also intercepts Roblox ClientSettings requests so those overrides can be applied before the client receives them. When Roblox requests assets from its CDN, Fleasion can:

- **Replace** assets by ID &mdash; swap one asset for another (different texture, audio, etc.)
- **Remove** assets &mdash; strip textures from the batch request entirely
- **Redirect** to CDN URLs or local files &mdash; serve your own content
- **Cache** original assets &mdash; browse, preview, and export everything Roblox downloads

All interception happens locally on your machine. Windows runs Fleasion elevated. On macOS, Fleasion installs a small root-owned relay/hosts/CA-patching helper with one administrator approval; the dashboard and menu-bar app always run as the normal user.
On Linux, Fleasion targets the Sober Flatpak client (`org.vinegarhq.Sober`). It uses Sober's asset overlay at `~/.var/app/org.vinegarhq.Sober/data/sober/asset_overlay` and writes Sober FFlags to `~/.var/app/org.vinegarhq.Sober/config/sober/config.json`. Proxy interception needs root permission because Fleasion updates `/etc/hosts` and listens on local port 443.

**VPN compatibility:** Because interception uses the system's hosts file (application layer), it should be compatible with most VPN software, as long as it respects the hosts file.

**Roblox policy and moderation:** Fleasion's normal asset replacement is client-side, so only you see the changes. The project has no known detections or reported warnings/bans for local asset replacement at the time of writing, but Roblox has stated that these modifications are not permitted and game moderators may still take action. Use your own judgment.

## Features

### Asset Replacement

- Configure replacement rules through the Dashboard GUI
- Replace assets by ID, redirect to external URLs, or serve local files
- Multiple configuration profiles &mdash; switch between different setups
- Import/export configurations as JSON
- Community preset support via PreJsons
- **Creator name column** in configuration list (off by default)
- **Asset name display** next to preview button

### Custom FastFlags

Fleasion can override Roblox ClientSettings FastFlags through the proxy while Roblox is running. Open the **Fast Flags** section in the Dashboard's **Modifications** tab to:

- Edit custom FastFlags live, with changes refreshed through the proxy
- Import and export FastFlags as JSON
- Save named JSON profiles, then load them by replacing or merging with the current editor
- Prime the Windows Roblox FastFlag cache so startup-sensitive flags can apply before the first dynamic refresh

**Important:** Custom FastFlags bypass Roblox's small allowlist of locally permitted flags. Roblox can ban your account for using them. Fleasion shows a risk confirmation before enabling the feature; use custom FastFlags entirely at your own risk.

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

- Generate a local CA certificate and install it into Roblox's SSL trust bundle
- On macOS, offer to install the root-owned proxy helper with one administrator approval. The helper owns local port 443, updates `/etc/hosts`, and patches Roblox `ssl/cacert.pem`.
- On Linux, use the installed desktop launcher or startup prompt to relaunch through Polkit; the desktop installer intentionally creates only the proxy-capable launcher, not the deprecated non-admin/read-only entry.
- Show a welcome dialog explaining how the proxy works
- Open the Dashboard automatically

### macOS Notes

- AppleBlox is not supported by Fleasion's macOS release path. Use the normal Roblox app bundle.
- Fleasion must verify the helper-patched Roblox `ssl/cacert.pem` before it writes hosts entries. If verification fails, the proxy will not start.
- Account Manager selected-account launches use Roblox auth-ticket `roblox-player:` URIs on macOS. Place, private-server, job-id, and plain app launches are attempted, but Roblox may still reject some app-launch flows; opening Roblox normally can use the account already signed in to Roblox.
- On first macOS launch, Fleasion asks which browser is signed in to roblox.com. It reads that browser directly when account-aware features need a Roblox login token, so macOS may ask for browser-data access; choose **Always Allow** if you do not want to approve it every time. Fleasion can also reuse a valid encrypted Chrome-family cache when present; if cache recovery is ambiguous, startup preserves it and skips surprise repeat prompts. Change the browser or store a manually imported encrypted token from **Settings -> Roblox Login**, or use **Miscellaneous -> Account Manager -> Import Browser Login** to re-import a browser login explicitly.

### Run on Boot

Fleasion can be configured to launch automatically via **Settings -> Run on Boot**. On Windows this creates a Task Scheduler task with `RunLevel=HighestAvailable`. On macOS this creates an unprivileged LaunchAgent; the already-installed proxy helper starts separately as a LaunchDaemon, so boot launches do not request an administrator password. **Settings -> Create desktop/start menu integration on boot** adds or refreshes the OS launcher entry on Windows, macOS, and Linux. On Linux, packaged builds are copied into `~/.local/share/Fleasion`, and the launcher starts through Polkit so proxy interception can update `/etc/hosts` and bind local port 443.

## Project Structure

<details>
<summary>See here</summary>

```text
├── Fleasion.spec   # PyInstaller specification for the standalone build
├── launcher.py     # Thin launcher used to start the packaged app
├── pyproject.toml  # Project metadata and dependency configuration
├── README.md       # Project overview, setup, and usage guide
├── pyinstaller_hooks/
│   └── rthook_harden_dll_search.py  # Runtime hook used by PyInstaller on Windows
├── scripts/
│   └── clear_first_time_setup.py  # Development utility for resetting initial setup
├── src/
│   └── fleasion/
│       ├── __init__.py                   # Package marker
│       ├── app.py                        # Application entrypoint, lifecycle, and startup wiring
│       ├── macos_proxy_helper_daemon.py  # macOS helper daemon for the privileged proxy relay
│       ├── tray.py                       # System tray / menu bar icon and menu wiring
│       ├── cache/
│       │   ├── __init__.py            # Cache package marker
│       │   ├── animation_viewer.py    # 3D animation preview with R15/R6 rigs
│       │   ├── audio_player.py        # Audio playback widget
│       │   ├── cache_json_viewer.py   # JSON viewer for cached asset metadata
│       │   ├── cache_manager.py       # Asset storage, indexing, and export logic
│       │   ├── cache_viewer.py        # Cache browsing UI with search and preview
│       │   ├── font_viewer.py         # Font file preview widget
│       │   ├── mesh_processing.py     # Mesh format conversion helpers
│       │   ├── obj_viewer.py          # OpenGL mesh viewer with orbit/FPS camera modes
│       │   ├── rbxm_parser.py         # Roblox binary model file parser
│       │   ├── rbxm_preview.py        # Roblox model preview helpers
│       │   ├── roblox_class_names.py  # Roblox class name lookup table
│       │   ├── roblox_document.py     # Roblox document helpers for cached content
│       │   └── tools/
│       │       ├── animpreview/
│       │       │   └── animpreview.py  # Animation preview assets and helpers
│       │       ├── image_to_ktx2/
│       │       │   └── converter.py  # Image to KTX2 converter
│       │       ├── ktx_to_png/
│       │       │   └── ktx_to_png.py  # KTX2 to PNG converter
│       │       ├── orm_compositor.py  # ORM texture channel compositor
│       │       └── solidmodel_converter/
│       │           ├── __init__.py           # Solid model converter package marker
│       │           ├── converter.py          # Solid model conversion entrypoint
│       │           ├── csg_mesh.py           # CSG mesh serialization helpers
│       │           ├── mesh_intermediary.py  # Intermediary conversion for .mesh and .bin data
│       │           ├── obj_to_csg.py         # OBJ to Roblox CSG converter
│       │           ├── obj_to_mesh.py        # OBJ to Roblox mesh converter
│       │           └── rbxm/
│       │               ├── __init__.py       # RBXM subpackage marker
│       │               ├── binary_reader.py  # RBXM binary reader
│       │               ├── binary_writer.py  # RBXM binary writer
│       │               ├── deserializer.py   # RBXM deserializer
│       │               ├── serializer.py     # RBXM serializer
│       │               ├── types.py          # RBXM type definitions
│       │               └── xml_writer.py     # RBXM XML writer
│       ├── config/
│       │   ├── __init__.py  # Config package marker
│       │   └── manager.py   # Settings persistence and config management
│       ├── gui/
│       │   ├── __init__.py             # GUI package marker
│       │   ├── about.py                # About dialog
│       │   ├── delete_cache.py         # Cache deletion window
│       │   ├── json_viewer.py          # JSON tree viewer with search and preview
│       │   ├── logs.py                 # Real-time log viewer
│       │   ├── modifications_tab.py    # Client modifications tab
│       │   ├── prejsons_dialog.py      # Community preset browser dialog
│       │   ├── proxy_gate.py           # Proxy gate / connection flow UI
│       │   ├── rando_stuff_tab.py      # Misc tab for extra tools and helpers
│       │   ├── replacer_config.py      # Main Dashboard window with profile management
│       │   ├── settings_tab.py         # Settings tab mirroring tray menu options
│       │   ├── subplace_joiner_tab.py  # Subplace browser and joiner tab
│       │   └── theme.py                # Theme management (System / Light / Dark)
│       ├── modifications/
│       │   ├── __init__.py                 # Modifications package marker
│       │   ├── dds_to_png.py               # DDS texture to PNG conversion
│       │   ├── fflag_manager.py            # Fast flag read/write helpers
│       │   ├── font_utils.py               # Custom font installation helpers
│       │   ├── global_settings_manager.py  # GlobalSettings.json management
│       │   └── manager.py                  # Modification orchestration (apply / revert)
│       ├── prejsons/
│       │   ├── __init__.py       # PreJsons package marker
│       │   └── downloader.py     # Community preset downloader
│       ├── proxy/
│       │   ├── __init__.py       # Proxy package marker
│       │   ├── master.py         # Proxy orchestration, hosts file management, cert setup
│       │   ├── server.py         # Asyncio TLS proxy server
│       │   ├── upstream.py       # Upstream proxy and request forwarding helpers
│       │   ├── windows_proxy.py  # Windows-specific proxy integration
│       │   └── addons/
│       │       ├── __init__.py          # Proxy addons package marker
│       │       ├── cache_scraper.py     # Asset interception and caching addon
│       │       ├── texture_stripper.py  # Asset replacement and texture removal addon
│       │       └── username_spoofer.py  # Username spoofing addon
│       ├── scripts/
│       │   ├── build.py         # Cross-platform build command
│       │   └── _macos_build.py  # Universal macOS build and packaging orchestration
│       └── utils/
│           ├── __init__.py            # Utilities package marker
│           ├── anim_converter.py      # Animation format conversion helpers
│           ├── autostart.py           # Windows Task Scheduler / macOS LaunchAgent run-on-boot helpers
│           ├── certs.py               # Local CA and leaf certificate generation
│           ├── clipboard.py           # Clipboard helper utilities
│           ├── http.py                # HTTP helper utilities
│           ├── logging.py             # Thread-safe log buffer
│           ├── macos_proxy_helper.py  # macOS privileged helper management
│           ├── paths.py               # Application paths and constants
│           ├── platform_macos.py      # macOS-specific operations
│           ├── platform_windows.py    # Windows-specific operations
│           ├── plural.py              # Pluralization helpers
│           ├── r15_to_r6.py           # R15 to R6 rig conversion helpers
│           ├── rig_data.py            # Rig bone definitions and mappings
│           ├── roblox_auth.py         # Roblox auth token helper for V1 APIs
│           ├── roblox_dirs.py         # Roblox directory discovery helpers
│           ├── threading.py           # Threading utilities
│           ├── time_tracker.py        # Session time tracking
│           ├── updater.py             # Update checker
│           └── windows.py             # Windows compatibility wrapper
├── tests/
│   ├── test_account_cookie_storage.py  # Cookie storage tests
│   ├── test_app_single_instance.py     # Single-instance app behavior tests
│   ├── test_autostart.py               # Run-on-boot tests
│   ├── test_build_script.py             # Cross-platform build dispatch tests
│   ├── test_config_manager.py          # Config manager tests
│   ├── test_macos_proxy_helper.py      # macOS helper tests
│   ├── test_modifications_manager.py   # Modification manager tests
│   ├── test_proxy_server.py            # Proxy server tests
│   ├── test_rgba_ktx2.py               # KTX/RGBA conversion tests
│   ├── test_roblox_browser_auth.py     # Roblox browser auth tests
│   ├── test_roblox_document.py         # Roblox document tests
│   ├── test_tray_dashboard.py          # Tray and dashboard integration tests
│   ├── test_upstream.py                # Upstream proxy tests
│   └── test_username_spoofer.py        # Username spoofer tests
└── build/  # Generated PyInstaller output (not source)
```

</details>

## Configuration

Settings are stored in:

- `%LocalAppData%\FleasionNT\` on Windows
- `~/Library/Application Support/FleasionNT/` on macOS
- `~/.config/Fleasion/` on Linux

| File / Directory | Purpose |
| --- | --- |
| `settings.json` | Application settings |
| `configs/` | Replacement configuration profiles (JSON) |
| `FastFlagProfiles/` | Named custom FastFlag profiles (JSON) |
| `Cache/` | Cached asset files and index |
| `Exports/` | Exported assets |
| `PreJsons/` | Community preset data |
| `proxy_ca/` | Generated CA certificate and per-host leaf certificates |
| `logs/fleasion.log` | Persistent application and proxy log |
| `Temp/ConvertedMeshes/` | Temporary directory for OBJ/mesh conversions |

## Community

- **Discord**: [discord.gg/hXyhKehEZF](https://discord.gg/hXyhKehEZF)
- **Donate**: [ko-fi.com/fleasion](https://ko-fi.com/fleasion)

## Credits

- **@8ar__**, **@dis_spencer**, **@1_v** (Sky) &mdash; code
- **@Blockce**, **@0100152000022000** (Sky 2), **@emk530**, **@yeha.** (Leah) &mdash; logic and contributions
- Donators &mdash; for keeping the passion going

## License

This project is provided as-is for educational and personal use.
