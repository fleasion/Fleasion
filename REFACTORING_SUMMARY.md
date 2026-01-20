# FleasionNT Refactoring Summary

## Overview

Successfully refactored the Windows-only Python desktop utility from a single large Tkinter-based script into a clean, multi-file PyQt6-based package with full separation of concerns.

## Package Structure

```
src/fleasionnt/
├── __init__.py                                 # Package marker
├── app.py                                      # Application entrypoint
├── tray.py                                     # PyQt6 system tray + menu
│
├── config/
│   ├── __init__.py
│   └── manager.py                              # ConfigManager class
│
├── gui/
│   ├── __init__.py
│   ├── about.py                                # About window (PyQt6)
│   ├── logs.py                                 # Logs window (PyQt6)
│   ├── delete_cache.py                         # Delete cache window (PyQt6)
│   ├── replacer_config.py                      # Main replacer config window (PyQt6)
│   ├── json_viewer.py                          # JSON tree viewer widget (PyQt6)
│   └── theme.py                                # Theme management
│
├── proxy/
│   ├── __init__.py
│   ├── master.py                               # Proxy runner/controller
│   └── addons/
│       ├── __init__.py
│       └── texture_stripper.py                 # TextureStripper addon
│
├── prejsons/
│   ├── __init__.py
│   └── downloader.py                           # CLOG.json downloader
│
└── utils/
    ├── __init__.py
    ├── paths.py                                # Windows paths/constants
    ├── logging.py                              # Logging setup
    ├── threading.py                            # Thread helpers
    └── windows.py                              # Windows-specific utilities
```

## Key Changes

### 1. GUI Framework Migration
- **From:** Tkinter + pystray
- **To:** PyQt6 (QSystemTrayIcon, QDialog, QWidget)
- All windows reimplemented in PyQt6
- Better native Windows integration
- More responsive and modern UI

### 2. Architecture Improvements
- **Separation of Concerns:** Each module has a single, clear responsibility
- **Threading:** Proxy runs in background thread without blocking GUI
- **State Management:** Clean proxy start/stop mechanism with state tracking

### 3. New Features

#### Settings Submenu
- **Proxy Control:**
  - Dynamic menu item: "Start Proxy" or "Stop Proxy"
  - Proxy starts automatically on launch (preserves original behavior)
  - Clean start/stop mechanism
  - Tray tooltip updates: "FleasionNT - Running" / "FleasionNT - Stopped"

- **Theme Control:**
  - Three options: System (default), Light, Dark
  - Persisted in settings.json
  - Applies immediately to all windows
  - Survives app restart

### 4. Preserved Behavior
- All original menu items work identically
- Config storage remains in `%LOCALAPPDATA%\FleasionNT`
- Config JSON format unchanged (backward compatible)
- PreJsons downloader behavior identical
- Proxy startup behavior identical (auto-start, Roblox termination, cert install)
- All replacement logic unchanged

## Dependencies

### Removed:
- `pystray`
- `pillow`

### Added:
- `pyqt6>=6.8.0`

### Retained:
- `mitmproxy>=12.2.1`

## Running the Application

### Development Mode:
```bash
uv run fleasionnt
```

### Building Executable:
The application can be packaged using PyInstaller or similar tools. Ensure the icon file (`fleasionlogo2.ico`) is included in the build.

## Configuration

### Settings File Location:
`%LOCALAPPDATA%\FleasionNT\settings.json`

### Settings Format:
```json
{
  "strip_textures": true,
  "enabled_configs": ["Config1", "Config2"],
  "last_config": "Default",
  "theme": "System"
}
```

### Config Files Location:
`%LOCALAPPDATA%\FleasionNT\configs\*.json`

## Migration Notes

### For Users:
- Existing configs are fully compatible
- Settings will be preserved
- Theme setting defaults to "System" if not present

### For Developers:
- Old `src/fleasion_3/` package can be safely deleted
- New entrypoint: `fleasionnt` (not `fleasion-3`)
- All imports changed from `fleasion_3` to `fleasionnt`

## Testing Checklist

✅ App launches successfully
✅ Tray icon appears
✅ All menu items work:
  - About window
  - Logs window (live updating)
  - Replacer Config window (full functionality)
  - Delete Cache window (background operation)
  - Copy Discord Invite (clipboard + message box)
✅ Settings submenu:
  - Proxy control (Start/Stop toggle)
  - Theme submenu (System/Light/Dark)
✅ Proxy functionality:
  - Auto-starts on launch
  - Runs in background without blocking GUI
  - Start/Stop works reliably
  - Tray tooltip updates correctly
✅ Theme changes:
  - Applies immediately
  - Persists across restarts
  - All windows respect theme
✅ Config management:
  - Create/Duplicate/Rename/Delete configs
  - Enable/disable multiple configs
  - Edit replacement rules
  - Import from JSON
  - Undo functionality (Ctrl+Z)
✅ Exit handling:
  - Stops proxy cleanly
  - Quits Qt app

## Code Quality

- **Type Hints:** Fully typed with Python 3.14 target
- **Docstrings:** Numpy-style docstrings where applicable
- **Code Style:** Single quotes, follows pyright and ruff rules
- **No TODOs:** All code is complete and functional

## Future Enhancements (Optional)

- Add status bar to windows showing connection status
- Add hotkey support for common actions
- Add notification system for important events
- Add auto-update mechanism
- Add crash reporting
