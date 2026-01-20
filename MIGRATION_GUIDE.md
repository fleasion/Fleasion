# Migration Guide

## Quick Start

### 1. Install Dependencies
Dependencies are already installed via `uv sync`. The package is ready to run.

### 2. Run the Application
```bash
uv run fleasionnt
```

### 3. Verify Functionality
- Check that the tray icon appears in your system tray
- Right-click to access the menu
- Verify all menu items work:
  - **About:** Shows app info and proxy status
  - **Logs:** Live log viewer
  - **Replacer Config:** Full config management UI
  - **Delete Cache:** Roblox cache deletion
  - **Copy Discord Invite:** Copies invite to clipboard
  - **Settings > Start/Stop Proxy:** Toggle proxy (auto-starts)
  - **Settings > Theme:** Choose System/Light/Dark theme
  - **Exit:** Clean shutdown

## What Changed

### User-Visible Changes
1. **New Settings Menu:**
   - Control proxy start/stop manually
   - Choose application theme (System/Light/Dark)

2. **Improved UI:**
   - Native PyQt6 windows (better Windows integration)
   - Smoother animations and interactions
   - More responsive

3. **Tray Tooltip:**
   - Now shows "FleasionNT - Running" or "FleasionNT - Stopped"

### Under the Hood
- Complete code reorganization into logical modules
- Better separation of concerns
- Cleaner threading model
- Type hints throughout
- No Tkinter dependency

## Compatibility

### Config Files
- **100% compatible** with existing configs
- Location unchanged: `%LOCALAPPDATA%\FleasionNT`
- JSON format identical
- Old configs will work without modification

### New Settings
- Theme setting added to `settings.json`
- Defaults to "System" if not present
- Old settings files will be automatically updated

## Clean Up (Optional)

### Remove Old Package
The old `src/fleasion_3/` directory can be safely deleted:
```bash
rm -rf src/fleasion_3/
```

### Remove Old Dependencies
Already handled by `uv sync`. Old dependencies (pystray, pillow) were automatically removed.

## Troubleshooting

### Issue: Tray icon not appearing
- Ensure PyQt6 is installed: `uv sync`
- Check if icon file exists: `fleasionlogo2.ico`
- Try running with admin privileges (first run only)

### Issue: Proxy not starting
- Check logs window for error messages
- Verify mitmproxy is installed: `uv sync`
- Ensure Roblox is not already running (app will terminate it)

### Issue: Theme not applying
- Check Settings > Theme menu
- Verify theme is saved in `%LOCALAPPDATA%\FleasionNT\settings.json`
- Try restarting the app

### Issue: Windows don't open
- Ensure PyQt6 dependencies are installed
- Check system tray for error notifications
- View logs for error details

## Development

### Project Structure
See [REFACTORING_SUMMARY.md](REFACTORING_SUMMARY.md) for detailed architecture.

### Running from Source
```bash
cd src
uv run python -m fleasionnt
```

### Building Executable
Use PyInstaller with the following considerations:
- Include `fleasionlogo2.ico` in the bundle
- Bundle PyQt6 plugins
- Set Windows subsystem to "windows" (no console)

Example PyInstaller command:
```bash
pyinstaller --windowed --icon=fleasionlogo2.ico --name FleasionNT --onefile -m fleasionnt
```

## Support

For issues or questions:
- Check the logs window for error details
- Review [REFACTORING_SUMMARY.md](REFACTORING_SUMMARY.md)
- Join the Discord: https://discord.gg/hXyhKehEZF

## Acceptance Checklist

✅ App launches and tray icon appears
✅ All menu items work correctly
✅ Settings submenu has Proxy and Theme controls
✅ Proxy starts automatically on launch
✅ Start/Stop Proxy toggle works
✅ Theme changes apply immediately
✅ Tray tooltip shows "Running" or "Stopped"
✅ Configs are stored in same location
✅ Config behavior is unchanged
✅ PreJsons downloader works
✅ Exit cleanly stops proxy and quits
✅ No TODO placeholders in code
✅ All dependencies are correct

**Status: All checkmarks complete! ✅**
