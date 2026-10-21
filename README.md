> [!CAUTION]
> Official Fleasion downloads are published only through this repository's [GitHub Releases](https://github.com/fleasion/Fleasion/releases). The official project website is [fleasion.github.io](https://fleasion.github.io/), and the official community is our [Discord server](https://discord.gg/hXyhKehEZF).

# Fleasion

A Windows, macOS, and Linux/Sober application for intercepting and replacing Roblox game assets in real time. Fleasion runs a local proxy that sits between Roblox and its servers, letting you swap textures, audio, meshes, animations, and other assets before they reach the game client.

To request help or request content, join our community <a href="https://discord.com/invite/pdtce585f6">Discord server!</a>

<a href="https://discord.gg/hXyhKehEZF">
    <img src="https://invidget.switchblade.xyz/hXyhKehEZF" alt="Join our Discord server">
</a>

## Installation

### Standalone Executable

Download the current standalone build from the [Releases](https://github.com/fleasion/Fleasion/releases) page. No Python installation required.

If the `.exe` fails to launch on startup with a `DLL load failed` error, move the executable to a different folder, such as your Documents directory. Windows can sometimes pick up bad DLLs from the same directory as the `.exe`, and placing it elsewhere avoids that conflict. Also clear your windows `%temp%` directory to remove any stale MEI files.

If you're on Linux and having issues with launching the GUI, please install `PortAudio` on your distro. How? Look it up.

Arch-based Linux systems also require Qt's native Widgets/runtime package:

```bash
sudo pacman -S --needed qt6-base
```

Want to run Fleasion from source, build it, or contribute code? See [CONTRIBUTING.md](CONTRIBUTING.md).

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

Fleasion runs a lightweight custom asyncio HTTPS proxy and supports two routing modes:

- **Roblox Env Proxy** normally listens only on the loopback high port `58443` and relaunches Roblox Player with proxy environment variables. If Windows has reserved or excluded that port, Fleasion automatically selects another free loopback port and passes it to Player. It does not modify the system hosts file or bind privileged port 443. Roblox Player's own `ssl/cacert.pem` still receives Fleasion's local CA so Player trusts the intercepted TLS traffic. Microsoft Store/Xbox (GDK) Roblox uses package-aware activation with a scoped environment block; if that activation is unavailable, Fleasion leaves the activated client untouched and reports the fallback rather than directly relaunching it. Roblox Studio is not relaunched, intercepted, patched, or closed by this mode.
- **Hosts File** is the legacy compatibility path. It redirects the intercepted Roblox hosts to localhost and uses local port 443, so it still requires the platform's administrator/helper path.

When custom FastFlags are enabled, Fleasion also intercepts Roblox ClientSettings requests and pre-seeds platform startup settings so overrides needed early in Player startup are available immediately. When Roblox requests assets from its CDN, Fleasion can:

- **Replace** assets by ID &mdash; swap one asset for another (different texture, audio, etc.)
- **Remove** assets &mdash; strip textures from the batch request entirely
- **Redirect** to CDN URLs or local files &mdash; serve your own content
- **Cache** original assets &mdash; browse, preview, and export everything Roblox downloads

All interception happens locally on your machine. Env Proxy runs the Fleasion GUI and proxy as the normal user on Windows, macOS, and Linux. A one-time administrator prompt can still be needed to repair an unusually protected Player installation or an inaccessible legacy Windows autostart task. Hosts File mode retains its existing administrator/helper requirements.

### Linux client

Fleasion currently supports the Sober Flatpak (`org.vinegarhq.Sober`). It uses Sober's asset overlay at `~/.var/app/org.vinegarhq.Sober/data/sober/asset_overlay` and writes Sober FastFlags to `~/.var/app/org.vinegarhq.Sober/config/sober/config.json`.

Linux client identity, paths, launch behavior, process detection, and proxy capabilities are routed through a client registry so another backend can be added without spreading platform-specific assumptions throughout Fleasion. Sober is the only registered implementation today.

**VPN compatibility:** Env Proxy is scoped to the Player process and normally coexists with system VPN settings. Hosts File mode depends on the VPN respecting local hosts-file resolution.

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

Every asset type Roblox uses &mdash; images, decals, audio, meshes, animations, shirts, pants, hats, faces, accessories (dozens of types). Each asset is stored with its type, original URL, content hash, file size, and capture timestamp.

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

- Browse all intercepted assets organized by type (dozens of Roblox asset types)
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
- In Env Proxy mode, run as the normal user and relaunch only Roblox Player with the local proxy environment
- In Hosts File mode, request the existing Windows elevation, macOS helper, or Linux Polkit helper needed for hosts-file and port-443 access
- Show a welcome dialog explaining how the proxy works
- Open the Dashboard automatically

### macOS Notes

- Fleasion discovers normal Roblox, Froststrap-managed `RobloxPlayer.app` versions, and AppleBlox custom Roblox paths. It mirrors managed files and CA state into Froststrap/AppleBlox restore snapshots. Because AppleBlox recreates `Contents/MacOS/ClientSettings` immediately before launch, Fleasion also merges its allowlisted FastFlags into that launch file before Roblox consumes it; conflicting Fleasion values take precedence.
- Fleasion must verify the helper-patched Roblox `ssl/cacert.pem` before it writes hosts entries. If verification fails, the proxy will not start.
- Account Manager selected-account launches use Roblox auth-ticket `roblox-player:` URIs on macOS. Place, private-server, job-id, and plain app launches are attempted, but Roblox may still reject some app-launch flows; opening Roblox normally can use the account already signed in to Roblox.
- On first macOS launch, Fleasion asks which browser is signed in to roblox.com. It reads that browser directly when account-aware features need a Roblox login token, so macOS may ask for browser-data access; choose **Always Allow** if you do not want to approve it every time. Fleasion can also reuse a valid encrypted Chrome-family cache when present; if cache recovery is ambiguous, startup preserves it and skips surprise repeat prompts. Change the browser or store a manually imported encrypted token from **Settings -> Roblox Login**, or use **Miscellaneous -> Account Manager -> Import Browser Login** to re-import a browser login explicitly.

### Run on Boot

Fleasion can be configured to launch automatically via **Settings -> Run on Boot**. Windows creates a per-user Task Scheduler task with `InteractiveToken` and `LeastPrivilege`; macOS creates an unprivileged per-user LaunchAgent; Linux creates a per-user XDG autostart entry. Env Proxy boot launches do not elevate the GUI. If a user selects Hosts File mode, Fleasion can still request the platform-specific helper or administrator access after launch. **Settings -> Create desktop/start menu integration on boot** adds or refreshes the OS launcher entry on Windows, macOS, and Linux.

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

### Sharing Configs with Files

Replacement config JSON files live directly inside `configs/`. If a config needs its own files,
such as an OBJ model, put those files in a folder inside `configs/`:

```text
configs/
├── Replace all weapons with sticks.json
└── StickObj/
    └── stick.obj
```

In the replacement field, `/StickObj/stick.obj` means “use `stick.obj` from the `StickObj`
folder inside Fleasion's Configs folder.” This notation works unchanged on Windows, macOS, and
Linux. The **Browse** button and drag-and-drop automatically use this shareable notation when
the selected file is inside a Configs subfolder.

Asset folders may be nested up to 10 folders deep. Config JSON files remain at the root of
`configs/`; JSON files inside asset subfolders are not listed as configs. Normal operating-system
file paths remain supported. On macOS and Linux, when both a Configs asset and an absolute path
could match the same `/Folder/file` text, the file inside Configs takes priority.

## Community

- **Discord**: [discord.gg/hXyhKehEZF](https://discord.gg/hXyhKehEZF)
- **Donate**: [ko-fi.com/fleasion](https://ko-fi.com/fleasion)

## License

This project is provided as-is for educational and personal use.
