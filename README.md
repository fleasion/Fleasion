> [!CAUTION]
> Roblox has patched Fleasion. It now runs on an older Roblox version that it installs for you, and that version will likely stop working within days. Always install and launch Roblox **from Fleasion**, not from a bootstrapper or the official Roblox app.

> [!IMPORTANT]
> Official Fleasion downloads are published only on this repository's [Releases](https://github.com/fleasion/Fleasion/releases) page. The official website is [fleasion.github.io](https://fleasion.github.io/), and the official community is our [Discord server](https://discord.gg/hXyhKehEZF).

# Fleasion

Fleasion replaces Roblox game assets in real time on Windows, macOS and Linux (Sober). It runs a local proxy between Roblox and its servers, so you can swap textures, audio, meshes, animations and other assets before they reach the game client.

For help or content requests, join our Discord server.

<a href="https://discord.gg/hXyhKehEZF">
    <img src="https://invidget.switchblade.xyz/hXyhKehEZF" alt="Join our Discord server">
</a>

## Installation

Download the latest build from the [Releases](https://github.com/fleasion/Fleasion/releases) page. Python is not required.

To run Fleasion from source, build it, or contribute code, see [CONTRIBUTING.md](CONTRIBUTING.md).

<details>
<summary><b>Troubleshooting installation</b></summary>

- **`DLL load failed` on Windows:** move the `.exe` to a different folder, such as Documents. Windows can load incompatible DLLs from the folder the `.exe` is in. Also clear your `%temp%` folder to remove stale `_MEI` folders.
- **The GUI does not start on Linux:** install PortAudio using your distribution's package manager.
- **Arch based distributions** also need Qt's native widgets package:

  ```bash
  sudo pacman -S --needed qt6-base
  ```

</details>

## Usage

1. **Launch Fleasion.** It starts in the system tray and starts the proxy automatically.
2. **Open the Dashboard** by right clicking the tray icon and selecting **Dashboard**.
3. **Install Roblox** by clicking **Install**, then use **Save and Launch** to start it.
4. **Add replacements** in the Replacer tab.
5. **Clear the cache** whenever you change replacements, so Roblox downloads the assets again through the proxy.

> [!NOTE]
> After changing anything in the Dashboard, clear your Roblox cache or restart Roblox. Fleasion can do this for you:
>
> - **Clear Cache on Launch** (on by default) closes Roblox and deletes `rbx-storage.db` when the proxy starts.
> - **Auto Delete Cache on Exit** (on by default) deletes the cache database when Roblox closes.
> - You can also clear the cache manually from the tray menu.

<details>
<summary><b>What happens on first launch</b></summary>

On first launch, Fleasion:

- Generates a local CA certificate and adds it to Roblox's SSL trust bundle.
- In Env Proxy mode, runs as your normal user and relaunches only Roblox Player with the local proxy settings.
- In Hosts File mode, requests the Windows administrator prompt, macOS helper or Linux Polkit helper needed to edit the hosts file and use port 443.
- Shows a welcome dialog explaining how the proxy works.
- Opens the Dashboard.

</details>

<details>
<summary><b>System tray</b></summary>

Fleasion runs in the background from the system tray. Left click the icon to show or hide the window. Right click it to open:

- **Dashboard:** configure asset replacements.
- **Delete Cache:** clear cached assets manually.
- **Logs:** view live proxy logs.
- **About:** application information.
- **Settings:** theme (System, Light or Dark), cache options, run on boot and more.

</details>

<details>
<summary><b>Run on boot</b></summary>

Turn on **Settings > Run on Boot** to start Fleasion automatically:

- **Windows** creates a per user Task Scheduler task that runs with your normal permissions.
- **macOS** creates a per user LaunchAgent.
- **Linux** creates a per user XDG autostart entry.

Env Proxy launches at boot never ask for administrator access. In Hosts File mode, Fleasion may still request the platform helper or administrator access after it starts. **Settings > Create desktop/start menu integration on boot** adds or refreshes the launcher entry on every platform.

</details>

## How it works

Fleasion runs a lightweight HTTPS proxy on your machine and supports two routing modes. When Roblox requests assets, Fleasion can:

- **Replace** assets by ID, swapping one texture, sound or model for another.
- **Remove** assets, such as stripping textures from a request entirely.
- **Redirect** assets to your own CDN URLs or local files.
- **Cache** the original assets so you can browse, preview and export everything Roblox downloads.

Everything happens locally on your machine.

<details>
<summary><b>Proxy modes</b></summary>

- **Roblox Env Proxy** (default) listens on the loopback port `58443` and relaunches Roblox Player with proxy environment variables. If Windows has reserved that port, Fleasion picks another free loopback port automatically. This mode does not change your hosts file or use port 443. Roblox Player's `ssl/cacert.pem` still receives Fleasion's local CA so the Player trusts the intercepted traffic.
  - Microsoft Store and Xbox (GDK) Roblox is started through package aware activation with a scoped environment. If that is not available, Fleasion leaves the client alone and reports it instead of relaunching it.
  - Roblox Studio is never relaunched, intercepted, patched or closed in this mode.
- **Hosts File** is the older compatibility mode. It points the intercepted Roblox hosts to localhost and uses port 443, so it still needs administrator access or the platform helper.

Env Proxy runs the Fleasion app and proxy as your normal user on every platform. A one time administrator prompt may still appear to repair an unusually protected Roblox Player install or an old Windows autostart task.

</details>

<details>
<summary><b>Certificates and FastFlags</b></summary>

Fleasion sets `DFFlagHttpUseEmbeddedCaCerts=False` on every platform so Roblox trusts the patched `ssl/cacert.pem` bundle. This override and its refresh interval are delivered through ClientSettings responses even when custom FastFlags are off. Windows and macOS also receive them in their startup settings.

Sober's pinned bootstrap requests stay tunneled, and Linux interception starts after Sober's existing bootstrap delay. These internal overrides do not appear in your saved FastFlag list, and certificate verification stays on.

When custom FastFlags are on, Fleasion merges your overrides into ClientSettings responses and pre-fills the platform startup settings, so flags that Roblox reads early in startup apply immediately.

</details>

<details>
<summary><b>Linux client</b></summary>

Fleasion supports the Sober Flatpak (`org.vinegarhq.Sober`). It uses Sober's asset overlay at `~/.var/app/org.vinegarhq.Sober/data/sober/asset_overlay` and writes Sober FastFlags to `~/.var/app/org.vinegarhq.Sober/config/sober/config.json`.

Client paths, launch behavior, process detection and proxy support all go through a client registry, so other Linux clients can be added later without platform specific code spreading through Fleasion. Sober is currently the only supported client.

</details>

<details>
<summary><b>macOS notes</b></summary>

- Fleasion finds standard Roblox, Froststrap managed `RobloxPlayer.app` versions and AppleBlox custom Roblox paths. It copies managed files and certificate state into Froststrap and AppleBlox restore snapshots. AppleBlox recreates `Contents/MacOS/ClientSettings` right before launch, so Fleasion also merges its allowed FastFlags into that file before Roblox reads it. Fleasion's values win when they conflict.
- Fleasion verifies the helper patched `ssl/cacert.pem` before writing hosts entries. If verification fails, the proxy does not start.
- Account Manager launches use Roblox auth ticket `roblox-player:` links on macOS. Place, private server, job ID and plain app launches are attempted, but Roblox may reject some of them. Opening Roblox normally uses the account already signed in to Roblox.
- On first launch, Fleasion asks which browser is signed in to roblox.com. It reads that browser when a feature needs your Roblox login, so macOS may ask for permission to access browser data. Choose **Always Allow** to avoid approving it every time. Fleasion can also reuse a valid encrypted Chrome based cache. If the cache is ambiguous, Fleasion keeps it and skips repeat prompts at startup. To change browsers or import a login manually, use **Settings > Roblox Login** or **Miscellaneous > Account Manager > Import Browser Login**.

</details>

> [!WARNING]
> **VPNs:** Env Proxy only affects the Roblox Player process and normally works alongside a VPN. Hosts File mode only works if your VPN respects the local hosts file.
>
> **Roblox rules:** asset replacement happens on your client, so only you see the changes. There are no known detections or reported bans for local asset replacement at the time of writing, but Roblox does not allow these modifications and game moderators may still take action. Use your own judgment.

## Features

<details>
<summary><b>Asset replacement</b></summary>

- Set up replacement rules in the Dashboard.
- Replace assets by ID, redirect them to external URLs or serve local files.
- Keep multiple configuration profiles and switch between them.
- Import and export configurations as JSON.
- Use community presets through PreJsons.
- Show a **creator name** column in the configuration list (off by default).
- See the **asset name** next to the preview button.

</details>

<details>
<summary><b>Custom FastFlags</b></summary>

Fleasion can override Roblox FastFlags through the proxy while Roblox is running. Open **Fast Flags** in the Dashboard's **Modifications** tab to:

- Edit custom FastFlags live, with changes applied through the proxy.
- Import and export FastFlags as JSON.
- Save named profiles and load them by replacing or merging with the current editor.
- Pre-fill the Windows FastFlag cache so flags read at startup apply before the first refresh.

> [!CAUTION]
> Custom FastFlags bypass Roblox's small list of locally allowed flags, and **Roblox can ban your account for using them**. Fleasion asks you to confirm the risk before turning them on. Use them entirely at your own risk.

</details>

<details>
<summary><b>Cache scraper</b></summary>

The cache scraper captures every asset Roblox downloads while you play. Turn it on in the Dashboard and it runs in the background.

**How it captures assets**

1. **Asset tracking:** reads asset batch requests to `assetdelivery.roblox.com` to learn each asset's ID, type and download location before anything downloads.
2. **CDN capture:** reads the actual downloads from Roblox's CDN and caches the content with its URL, content type, hash, size and timestamp.

**Table**

- Right click column headers to show or hide columns. Creator name and Roblox CDN link are hidden by default.
- Columns can be resized and sorted, and your layout is saved.

**Automatic conversion**

- **KTX textures** (images and decals) are converted to PNG on your device.
- **TexturePacks** are resolved through their XML manifest into separate Color, Normal, Metalness and Roughness textures.
- **3D models** (meshes and SolidModels) are converted to and from `.obj`.

**Performance**

- Conversions run on a background pool of 4 threads, so the proxy never waits on network calls.
- Reused HTTP sessions reduce overhead on repeated API calls.
- Asset lookups use hash maps instead of scanning every tracked asset.

**What gets cached**

Every asset type Roblox uses, including images, decals, audio, meshes, animations, clothing and accessories. Each asset is stored with its type, original URL, content hash, file size and capture time.

</details>

<details>
<summary><b>3D viewers and previews</b></summary>

**Mesh viewer** (OpenGL)

- Orbit and first person camera modes.
- Wireframe and grid display (grid is on by default).
- Cached rendering for smooth performance.
- Vertex color support and auto rotation.

**Animation viewer**

- Live 3D playback on R15 and R6 rigs.
- Free camera for better viewing angles.
- Playback speed controls.
- Grid display (on by default).

**Conversion support**

- **Mesh to CSG:** converts `.mesh` files to `.obj` before injecting them as CSG.
- **CSG to mesh:** converts CSG models to `.obj` before replacing a mesh.
- **CSG to CSG:** replaces CSG models directly through CDN links.
- **CDN OBJ files:** downloads and converts OBJ files from links such as Discord or Cloudflare.

</details>

<details>
<summary><b>Cache viewer</b></summary>

- Browse every captured asset, grouped by type.
- Search and filter by ID, name, type, hash or URL.
- Preview images, meshes, audio, animations, texture packs and JSON files.
- Look up asset names through the Roblox API.
- Export assets as converted, binary or raw files.
- Copy converted files straight to your clipboard.
- Filter by category from the column header menu.

</details>

## Configuration

Settings are stored in:

- `%LocalAppData%\FleasionNT\` on Windows
- `~/Library/Application Support/FleasionNT/` on macOS
- `~/.config/Fleasion/` on Linux

<details>
<summary><b>Files and folders</b></summary>

| File or folder | Purpose |
| --- | --- |
| `settings.json` | Application settings |
| `configs/` | Replacement configuration profiles (JSON) |
| `FastFlagProfiles/` | Named custom FastFlag profiles (JSON) |
| `Cache/` | Cached asset files and index |
| `Exports/` | Exported assets |
| `PreJsons/` | Community preset data |
| `proxy_ca/` | Generated CA certificate and per host certificates |
| `logs/fleasion.log` | Application and proxy log |
| `Temp/ConvertedMeshes/` | Temporary OBJ and mesh conversions |

</details>

<details>
<summary><b>Sharing configs with files</b></summary>

Config JSON files go directly inside `configs/`. If a config needs its own files, such as an OBJ model, put them in a folder inside `configs/`:

```text
configs/
├── Replace all weapons with sticks.json
└── StickObj/
    └── stick.obj
```

In the replacement field, `/StickObj/stick.obj` means "use `stick.obj` from the `StickObj` folder inside Fleasion's configs folder". This works the same on Windows, macOS and Linux. The **Browse** button and drag and drop use this format automatically when the file is inside a configs subfolder.

- Asset folders can be nested up to 10 levels deep.
- Config JSON files must stay at the root of `configs/`. JSON files inside asset folders are not listed as configs.
- Normal file paths still work. On macOS and Linux, if a `/Folder/file` path matches both a configs asset and an absolute path, the file inside configs is used.

</details>

## Community

- **Discord:** [discord.gg/hXyhKehEZF](https://discord.gg/hXyhKehEZF)
- **Donate:** [ko-fi.com/fleasion](https://ko-fi.com/fleasion)

## License

This project is provided as is for educational and personal use.
