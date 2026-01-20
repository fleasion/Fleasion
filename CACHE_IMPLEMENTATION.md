# Cache System Implementation Guide

## What's Been Implemented

The cache system has been successfully integrated into Fleasion with the following features:

### Core Functionality
1. **Cache Manager** (`src/Fleasion/cache/cache_manager.py`)
   - Stores intercepted Roblox assets organized by type
   - Automatic compression for files > 10KB
   - JSON-based index for fast lookups
   - Export functionality to export folder
   - Statistics and management APIs

2. **Cache Scraper Addon** (`src/Fleasion/proxy/addons/cache_scraper.py`)
   - Intercepts ALL Roblox asset requests BEFORE the texture replacer
   - Caches original unmodified assets
   - Supports multiple URL patterns and asset types
   - Automatic asset type detection

3. **Cache Viewer Tab** (`src/Fleasion/cache/cache_viewer.py`)
   - Integrated as a tab in the Replacer Config window
   - Split view: Table on left, Preview on right
   - Table view of all cached assets
   - Filter by asset type
   - Search by asset ID
   - Export selected or all assets
   - Delete individual assets or clear entire cache
   - Real-time statistics
   - Auto-refresh every 2 seconds

4. **3D Mesh Viewer** (`src/Fleasion/cache/obj_viewer.py`)
   - OpenGL-based 3D viewer for mesh assets
   - Mouse controls: drag to rotate, scroll to zoom
   - Auto-rotate mode
   - Wireframe overlay
   - Displays vertex/face counts
   - Works with all Roblox mesh versions (v1-v7)

5. **Mesh Processing** (`src/Fleasion/cache/mesh_processing.py`)
   - Complete Roblox mesh converter (versions 1.x through 7.00)
   - Handles Draco-compressed v6/v7 meshes
   - Converts to OBJ format for viewing
   - LOD (Level of Detail) support

### Storage Structure
```
%LOCALAPPDATA%/Fleasion/FleasionNT/
├── Cache/
│   ├── index.json           # Asset metadata index
│   ├── Image/               # Image assets (type 1)
│   ├── Audio/               # Audio assets (type 3)
│   ├── Mesh/                # Mesh assets (type 4)
│   └── [Other Types]/       # Organized by asset type
└── Exports/                 # Exported assets
    ├── Image/
    ├── Audio/
    └── [Other Types]/
```

## Installation & Usage

### 1. Install Dependencies
```bash
uv sync
```

This will install all required dependencies including:
- PyOpenGL (3D rendering with OpenGL)
- pygame (audio playback - for future audio player)
- mutagen (audio metadata - for future audio player)
- DracoPy (v6/v7 mesh decoding)
- Pillow (image processing)
- numpy, pywin32, requests

### 2. Run Fleasion
```bash
uv run Fleasion
```

### 3. Access Cache Viewer
1. Start the proxy (from system tray)
2. Open "Replacer Config" from system tray
3. Click on the "Cache" tab
4. Assets will appear automatically as they're intercepted

### 4. Preview Assets
- Click on any asset in the table to preview it
- **Meshes (Type 4)**: View in real-time 3D with rotation and zoom
  - Left-click and drag to rotate
  - Scroll wheel to zoom
  - "Reset View" button to reset camera
  - "Auto Rotate" for animated rotation
- **Images (Type 1, 13)**: View with automatic scaling
- **Other types**: Hex dump preview

### 5. Export Assets
- Select an asset and click "Export Selected" to choose a custom location
- Click "Export All" to export all filtered assets to the Exports folder
- Use "Open Export Folder" to view exported files

## Reference Implementation

The full-featured cache viewer from `Reference/HoprTest2/modules/cache/` includes:
- 3D mesh preview with pyvista
- Audio player with pygame
- Image viewer with zooming
- Mesh conversion to OBJ format
- Game cards UI with thumbnails
- Advanced filtering and search

### Files Available in Reference
All these files have been copied to `src/Fleasion/cache/`:
- `main.py` (2483 lines) - Full cache viewer UI
- `mesh_processing.py` - Complete mesh conversion (v1-v7)
- `game_card_widget.py` - Card-based UI widget
- `gameCard.py` - UI definition
- `dialog1_ui.py` through `dialog4_ui.py` - Dialog UIs
- `PresetWindow.py` - Preset management
- `tab.ui` - Tab UI definition
- `tools/` - 3D model files for preview

## Next Steps: Adding Full Features

To add the complete Reference implementation features, follow these steps:

### Phase 1: PySide6 → PyQt6 Conversion
The Reference code uses PySide6. Convert remaining files:

1. **Import Changes:**
   ```python
   # PySide6 → PyQt6
   from PySide6.QtCore import Signal → from PyQt6.QtCore import pyqtSignal
   from PySide6.QtWidgets import ... → from PyQt6.QtWidgets import ...
   ```

2. **Signal/Slot Changes:**
   ```python
   # PySide6
   clicked = Signal()

   # PyQt6
   clicked = pyqtSignal()
   ```

3. **Enum Changes:**
   ```python
   # PySide6
   Qt.AlignCenter

   # PyQt6
   Qt.AlignmentFlag.AlignCenter
   ```

### Phase 2: Integrate 3D Mesh Viewer
1. Study `Reference/HoprTest2/modules/cache/main.py` lines 800-1200 (mesh viewer section)
2. Add pyvista QtInteractor to cache_viewer.py
3. Use `mesh_processing.convert()` to convert meshes to OBJ
4. Display in 3D viewport

### Phase 3: Integrate Audio Player
1. Study lines 1400-1600 in Reference main.py
2. Add pygame mixer initialization
3. Create audio player controls (play/pause/stop)
4. Display audio metadata with mutagen

### Phase 4: Integrate Image Viewer
1. Study lines 600-800 in Reference main.py
2. Add QLabel with pixmap display
3. Implement zoom in/out
4. Support image formats

### Phase 5: Add Game Cards UI
1. Use the adapted `game_card_widget.py`
2. Create grid layout for cards
3. Load thumbnails for each asset
4. Add click handlers for selection

## Code Integration Pattern

When adding features from Reference, follow this pattern:

```python
# In cache_viewer.py

# 1. Add to imports
from pyvistaqt import QtInteractor
import pyvista as pv

# 2. Add to _setup_ui
def _setup_ui(self):
    # ... existing code ...

    # Add preview panel
    self._create_preview_panel(main_layout)

# 3. Create the feature
def _create_preview_panel(self, parent_layout):
    preview_group = QGroupBox('Preview')
    preview_layout = QVBoxLayout()

    # Add 3D viewer
    self.plotter = QtInteractor(preview_group)
    preview_layout.addWidget(self.plotter.interactor)

    preview_group.setLayout(preview_layout)
    parent_layout.addWidget(preview_group)

# 4. Connect to selection
def _on_asset_selected(self, asset):
    if asset['type'] == 4:  # Mesh
        self._preview_mesh(asset)
    elif asset['type'] == 1:  # Image
        self._preview_image(asset)

def _preview_mesh(self, asset):
    data = self.cache_manager.get_asset(asset['id'], asset['type'])
    # Use mesh_processing.convert() to get OBJ
    from .mesh_processing import convert
    obj_content = convert(data)
    # Display in plotter
    # ... (see Reference implementation)
```

## Asset Type Reference

Use `CacheManager.ASSET_TYPES` for the complete mapping (80 types):
- Type 1: Image/Texture
- Type 3: Audio
- Type 4: Mesh
- Type 10: Model
- Type 24: Animation
- And many more...

## Troubleshooting

### Cache not populating
1. Ensure proxy is running (check system tray)
2. Launch Roblox AFTER starting proxy
3. Check logs window for "Cached" messages

### Export folder not opening
- Path: `%LOCALAPPDATA%/Fleasion/FleasionNT/Exports`
- Create manually if needed

### Dependencies not installing
```bash
uv sync --reinstall
```

## Architecture Notes

### Addon Order is Critical
In `proxy/master.py`, the cache scraper is added BEFORE the texture stripper:

```python
# Cache scraper first - caches original assets
self._master.addons.add(CacheScraper(self.cache_manager))

# Texture stripper second - modifies responses
self._master.addons.add(TextureStripper(self.config_manager))
```

This ensures we always cache the original unmodified assets.

### Thread Safety
- CacheManager uses file-based storage (thread-safe)
- Index updates are atomic (write to temp, then rename)
- UI updates happen on Qt main thread via signals

## Contributing

When enhancing the cache viewer:
1. Follow the existing code style (strict typing, numpy docstrings)
2. Test with various asset types
3. Handle errors gracefully (assets may be corrupted)
4. Update this documentation

## License

Same as Fleasion main project.
