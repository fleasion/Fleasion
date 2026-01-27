"""Cache manager for storing and organizing intercepted Roblox assets."""

import json
import gzip
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional

from ..utils import CONFIG_DIR


class CacheManager:
    """Manages cached Roblox assets organized by type."""

    # Asset types mapping
    ASSET_TYPES = {
        1: 'Image', 2: 'TShirt', 3: 'Audio', 4: 'Mesh', 5: 'Lua',
        6: 'HTML', 7: 'Text', 8: 'Hat', 9: 'Place', 10: 'Model',
        11: 'Shirt', 12: 'Pants', 13: 'Decal', 16: 'Avatar', 17: 'Head',
        18: 'Face', 19: 'Gear', 21: 'Badge', 22: 'GroupEmblem',
        24: 'Animation', 25: 'Arms', 26: 'Legs', 27: 'Torso',
        28: 'RightArm', 29: 'LeftArm', 30: 'LeftLeg', 31: 'RightLeg',
        32: 'Package', 33: 'YouTubeVideo', 34: 'GamePass', 35: 'App',
        37: 'Code', 38: 'Plugin', 39: 'SolidModel', 40: 'MeshPart',
        41: 'HairAccessory', 42: 'FaceAccessory', 43: 'NeckAccessory',
        44: 'ShoulderAccessory', 45: 'FrontAccessory', 46: 'BackAccessory',
        47: 'WaistAccessory', 48: 'ClimbAnimation', 49: 'DeathAnimation',
        50: 'FallAnimation', 51: 'IdleAnimation', 52: 'JumpAnimation',
        53: 'RunAnimation', 54: 'SwimAnimation', 55: 'WalkAnimation',
        56: 'PoseAnimation', 57: 'EarAccessory', 58: 'EyeAccessory',
        59: 'LocalizationTableManifest', 61: 'EmoteAnimation', 62: 'Video',
        63: 'TexturePack', 64: 'TShirtAccessory', 65: 'ShirtAccessory',
        66: 'PantsAccessory', 67: 'JacketAccessory', 68: 'SweaterAccessory',
        69: 'ShortsAccessory', 70: 'LeftShoeAccessory', 71: 'RightShoeAccessory',
        72: 'DressSkirtAccessory', 73: 'FontFamily', 74: 'FontFace',
        75: 'MeshHiddenSurfaceRemoval', 76: 'EyebrowAccessory',
        77: 'EyelashAccessory', 78: 'MoodAnimation', 79: 'DynamicHead',
        80: 'CodeSnippet',
    }

    def __init__(self, config_manager=None):
        """Initialize cache manager."""
        self.cache_dir = CONFIG_DIR / 'FleasionNT' / 'Cache'
        self.export_dir = CONFIG_DIR / 'FleasionNT' / 'Exports'
        self.index_file = self.cache_dir / 'index.json'
        self.config_manager = config_manager

        # Create cache directory structure
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)

        # Load or create index
        self.index = self._load_index()

    def _load_index(self) -> dict:
        """Load cache index from disk."""
        if self.index_file.exists():
            try:
                with self.index_file.open('r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {'assets': {}, 'version': '1.0'}

    def _save_index(self):
        """Save cache index to disk."""
        try:
            with self.index_file.open('w', encoding='utf-8') as f:
                json.dump(self.index, f, indent=2)
        except OSError as e:
            print(f'Failed to save cache index: {e}')

    def get_asset_type_name(self, type_id: int) -> str:
        """Get asset type name from ID."""
        return self.ASSET_TYPES.get(type_id, f'Unknown({type_id})')

    def get_asset_path(self, asset_id: str, asset_type: int) -> Path:
        """Get storage path for an asset."""
        type_name = self.get_asset_type_name(asset_type)
        type_dir = self.cache_dir / type_name
        type_dir.mkdir(exist_ok=True)
        return type_dir / f'{asset_id}.bin'

    def store_asset(self, asset_id: str, asset_type: int, data: bytes,
                   url: str = '', metadata: Optional[dict] = None) -> bool:
        """
        Store an asset in the cache.

        Args:
            asset_id: Asset ID (usually from URL)
            asset_type: Roblox asset type ID
            data: Raw asset data
            url: Original URL
            metadata: Additional metadata

        Returns:
            True if stored successfully
        """
        try:
            # Store the asset file
            asset_path = self.get_asset_path(asset_id, asset_type)

            # Compress data if it's large
            if len(data) > 10240:  # 10KB threshold
                with gzip.open(asset_path, 'wb') as f:
                    f.write(data)
                compressed = True
            else:
                asset_path.write_bytes(data)
                compressed = False

            # Calculate hash for deduplication
            file_hash = hashlib.sha256(data).hexdigest()[:16]

            # Update index
            asset_key = f'{asset_type}_{asset_id}'
            self.index['assets'][asset_key] = {
                'id': asset_id,
                'type': asset_type,
                'type_name': self.get_asset_type_name(asset_type),
                'url': url,
                'size': len(data),
                'compressed': compressed,
                'hash': file_hash,
                'cached_at': datetime.now().isoformat(),
                'metadata': metadata or {},
            }

            self._save_index()
            return True

        except Exception as e:
            print(f'Failed to store asset {asset_id}: {e}')
            return False

    def get_asset(self, asset_id: str, asset_type: int) -> Optional[bytes]:
        """
        Retrieve an asset from cache.

        Args:
            asset_id: Asset ID
            asset_type: Asset type ID

        Returns:
            Asset data or None if not found
        """
        try:
            asset_path = self.get_asset_path(asset_id, asset_type)
            if not asset_path.exists():
                return None

            asset_key = f'{asset_type}_{asset_id}'
            asset_info = self.index['assets'].get(asset_key, {})

            if asset_info.get('compressed', False):
                with gzip.open(asset_path, 'rb') as f:
                    return f.read()
            else:
                return asset_path.read_bytes()

        except Exception as e:
            print(f'Failed to retrieve asset {asset_id}: {e}')
            return None

    def get_asset_info(self, asset_id: str, asset_type: int) -> Optional[dict]:
        """Get metadata about a cached asset."""
        asset_key = f'{asset_type}_{asset_id}'
        return self.index['assets'].get(asset_key)

    def list_assets(self, asset_type: Optional[int] = None) -> list[dict]:
        """
        List all cached assets, optionally filtered by type.

        Args:
            asset_type: Optional asset type ID to filter by

        Returns:
            List of asset metadata dictionaries
        """
        # Take a snapshot to avoid dictionary changed during iteration
        assets = list(dict(self.index['assets']).values())

        if asset_type is not None:
            assets = [a for a in assets if a['type'] == asset_type]

        # Sort by cached_at descending (newest first)
        assets.sort(key=lambda a: a.get('cached_at', ''), reverse=True)

        return assets

    def export_asset(self, asset_id: str, asset_type: int,
                    output_path: Optional[Path] = None, resolved_name: str = None) -> Optional[Path]:
        """
        Export an asset to the exports folder with smart naming and conversion.

        Args:
            asset_id: Asset ID
            asset_type: Asset type ID
            output_path: Optional custom output path
            resolved_name: Optional resolved asset name for filename

        Returns:
            Path to exported file or None on failure
        """
        try:
            data = self.get_asset(asset_id, asset_type)
            if not data:
                return None

            if output_path is None:
                type_name = self.get_asset_type_name(asset_type)
                export_type_dir = self.export_dir / type_name
                export_type_dir.mkdir(exist_ok=True)

                # Determine filename based on config settings
                asset_info = self.get_asset_info(asset_id, asset_type)
                hash_val = asset_info.get('hash', '') if asset_info else ''

                # Build filename from enabled naming options
                filename_parts = []
                if self.config_manager:
                    naming_options = self.config_manager.export_naming
                    if 'name' in naming_options and resolved_name:
                        # Sanitize resolved name
                        sanitized_name = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in resolved_name)
                        filename_parts.append(sanitized_name[:100])
                    if 'id' in naming_options and asset_id:
                        filename_parts.append(asset_id)
                    if 'hash' in naming_options and hash_val:
                        filename_parts.append(hash_val)

                # Fallback if no options enabled or no config manager
                if not filename_parts:
                    filename_parts.append(asset_id if asset_id else hash_val)

                filename = '_'.join(filename_parts)[:200]  # Limit total length

                # Determine extension and conversion based on type
                if asset_type == 4:  # Mesh - convert to OBJ
                    from . import mesh_processing
                    try:
                        obj_data = mesh_processing.convert(data)
                        if obj_data:
                            output_path = export_type_dir / f'{filename}.obj'
                            output_path.write_text(obj_data, encoding='utf-8')
                            return output_path
                    except Exception:
                        pass  # Fall through to binary export

                elif asset_type == 3:  # Audio - export as OGG/MP3
                    # Check file signature to determine format
                    if data.startswith(b'OggS'):
                        output_path = export_type_dir / f'{filename}.ogg'
                    elif data.startswith(b'ID3') or data.startswith(b'\xFF\xFB'):
                        output_path = export_type_dir / f'{filename}.mp3'
                    else:
                        output_path = export_type_dir / f'{filename}.ogg'  # Default to ogg

                elif asset_type in (1, 13):  # Image, Decal - export as PNG
                    # Data should already be PNG (converted from KTX at scrape time)
                    output_path = export_type_dir / f'{filename}.png'
                    output_path.write_bytes(data)
                    return output_path

                elif asset_type == 63:  # TexturePack - export individual textures
                    return self._export_texturepack(data, asset_id, export_type_dir, filename)

                elif asset_type == 24:  # Animation - export as RBXMX
                    output_path = export_type_dir / f'{filename}.rbxmx'

                else:
                    # Default binary export
                    output_path = export_type_dir / f'{filename}.bin'

            output_path.write_bytes(data)
            return output_path

        except Exception as e:
            print(f'Failed to export asset {asset_id}: {e}')
            return None

    def _export_texturepack(self, data: bytes, asset_id: str,
                           export_type_dir: Path, base_filename: str) -> Optional[Path]:
        """
        Export texture pack by extracting all textures to subfolders.

        Args:
            data: XML data of texture pack
            asset_id: Asset ID of the texture pack
            export_type_dir: Base export directory for TexturePack
            base_filename: Base filename for the export

        Returns:
            Path to export directory or None on failure
        """
        import xml.etree.ElementTree as ET

        try:
            # Parse XML
            xml_text = data.decode('utf-8', errors='replace')
            root = ET.fromstring(xml_text)

            # Extract texture map IDs
            map_order = ['color', 'normal', 'metalness', 'roughness']
            maps = {}
            for elem in map_order:
                node = root.find(elem)
                if node is not None and node.text:
                    maps[elem.capitalize()] = node.text

            if not maps:
                return None

            # Create base folder for this texture pack
            pack_dir = export_type_dir / base_filename
            pack_dir.mkdir(exist_ok=True)

            exported_count = 0
            for map_name, map_id in maps.items():
                # Create subfolder for this texture type
                type_dir = pack_dir / map_name
                type_dir.mkdir(exist_ok=True)

                # Get cached texture (type 1 = Image)
                texture_data = self.get_asset(str(map_id), 1)
                if not texture_data:
                    continue

                # Get hash
                texture_info = self.get_asset_info(str(map_id), 1)
                texture_hash = texture_info.get('hash', '') if texture_info else ''

                # Build filename: Name_ID_Hash.png
                filename_parts = [map_name, map_id]
                if texture_hash:
                    filename_parts.append(texture_hash)
                texture_filename = '_'.join(filename_parts)

                # Write texture
                texture_path = type_dir / f'{texture_filename}.png'
                texture_path.write_bytes(texture_data)
                exported_count += 1

            return pack_dir if exported_count > 0 else None

        except Exception as e:
            print(f'Failed to export texture pack {asset_id}: {e}')
            return None

    def delete_asset(self, asset_id: str, asset_type: int) -> bool:
        """
        Delete an asset from cache.

        Args:
            asset_id: Asset ID
            asset_type: Asset type ID

        Returns:
            True if deleted successfully
        """
        try:
            asset_path = self.get_asset_path(asset_id, asset_type)
            if asset_path.exists():
                asset_path.unlink()

            asset_key = f'{asset_type}_{asset_id}'
            if asset_key in self.index['assets']:
                del self.index['assets'][asset_key]
                self._save_index()

            return True

        except Exception as e:
            print(f'Failed to delete asset {asset_id}: {e}')
            return False

    def clear_cache(self, asset_type: Optional[int] = None) -> int:
        """
        Clear cached assets.

        Args:
            asset_type: Optional asset type to clear, or None for all

        Returns:
            Number of assets deleted
        """
        count = 0
        assets_to_delete = []

        for asset_key, asset_info in self.index['assets'].items():
            if asset_type is None or asset_info['type'] == asset_type:
                assets_to_delete.append((asset_info['id'], asset_info['type']))

        for asset_id, atype in assets_to_delete:
            if self.delete_asset(asset_id, atype):
                count += 1

        return count

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        # Take a snapshot to avoid dictionary changed during iteration
        assets_snapshot = dict(self.index['assets'])

        total_assets = len(assets_snapshot)
        total_size = sum(a.get('size', 0) for a in assets_snapshot.values())

        types_count = {}
        for asset_info in assets_snapshot.values():
            type_name = asset_info.get('type_name', 'Unknown')
            types_count[type_name] = types_count.get(type_name, 0) + 1

        return {
            'total_assets': total_assets,
            'total_size': total_size,
            'types_count': types_count,
        }
