"""Cache scraper addon - intercepts and caches Roblox assets BEFORE replacement."""

import re
from urllib.parse import urlparse, parse_qs

from mitmproxy import http

from ...cache.cache_manager import CacheManager
from ...utils import log_buffer


class CacheScraper:
    """Mitmproxy addon that intercepts and caches Roblox assets."""

    # Roblox asset delivery domains
    ASSET_DOMAINS = [
        'assetdelivery.roblox.com',
        'c0.rbxcdn.com',
        'c1.rbxcdn.com',
        'c2.rbxcdn.com',
        'c3.rbxcdn.com',
        'c4.rbxcdn.com',
        'c5.rbxcdn.com',
        'c6.rbxcdn.com',
        'c7.rbxcdn.com',
        't0.rbxcdn.com',
        't1.rbxcdn.com',
        't2.rbxcdn.com',
        't3.rbxcdn.com',
        't4.rbxcdn.com',
        't5.rbxcdn.com',
        't6.rbxcdn.com',
        't7.rbxcdn.com',
    ]

    def __init__(self, cache_manager: CacheManager):
        """
        Initialize cache scraper.

        Args:
            cache_manager: CacheManager instance
        """
        self.cache_manager = cache_manager
        self.enabled = True

    def response(self, flow: http.HTTPFlow) -> None:
        """
        Handle HTTP responses - cache assets before any modification.

        This runs BEFORE the texture_stripper addon, ensuring we cache
        the original unmodified assets.

        Args:
            flow: HTTP flow containing request and response
        """
        if not self.enabled:
            return

        # Only process successful responses
        if flow.response.status_code != 200:
            return

        # Check if this is a Roblox asset domain
        host = flow.request.pretty_host.lower()
        if not any(domain in host for domain in self.ASSET_DOMAINS):
            return

        url = flow.request.pretty_url
        path = flow.request.path

        # Extract asset ID and type from URL
        asset_info = self._extract_asset_info(url, path)
        if not asset_info:
            return

        asset_id = asset_info['id']
        asset_type = asset_info['type']

        # Get response content (before any modifications)
        content = flow.response.content

        if not content or len(content) == 0:
            return

        # Extract metadata
        metadata = {
            'url': url,
            'content_type': flow.response.headers.get('content-type', ''),
            'content_length': len(content),
        }

        # Store the asset
        success = self.cache_manager.store_asset(
            asset_id=asset_id,
            asset_type=asset_type,
            data=content,
            url=url,
            metadata=metadata
        )

        if success:
            type_name = self.cache_manager.get_asset_type_name(asset_type)
            log_buffer.log(
                'Cache',
                f'Cached {type_name} asset: {asset_id} ({len(content)} bytes)'
            )

    def _extract_asset_info(self, url: str, path: str) -> dict | None:
        """
        Extract asset ID and type from URL.

        Args:
            url: Full URL
            path: URL path

        Returns:
            Dict with 'id' and 'type' keys, or None if not an asset URL
        """
        # Pattern 1: /v1/assets?id=123456789&assetType=4
        if '/v1/assets' in path or '/v1/asset' in path:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)

            asset_id = params.get('id', params.get('assetId', [None]))[0]
            asset_type = params.get('assetType', params.get('type', ['0']))[0]

            if asset_id:
                try:
                    return {
                        'id': str(asset_id),
                        'type': int(asset_type) if asset_type else 0
                    }
                except ValueError:
                    pass

        # Pattern 2: /asset?id=123456789
        match = re.search(r'[?&]id=(\d+)', url)
        if match:
            asset_id = match.group(1)

            # Try to extract type from URL
            type_match = re.search(r'[?&](?:assetType|type)=(\d+)', url)
            asset_type = int(type_match.group(1)) if type_match else 0

            return {
                'id': asset_id,
                'type': asset_type
            }

        # Pattern 3: Direct CDN URLs like /123456789
        # These are usually meshes or textures
        match = re.search(r'/(\d{8,})', path)
        if match:
            asset_id = match.group(1)

            # Infer type from domain/path
            asset_type = self._infer_type_from_url(url, path)

            return {
                'id': asset_id,
                'type': asset_type
            }

        return None

    def _infer_type_from_url(self, url: str, path: str) -> int:
        """
        Infer asset type from URL patterns.

        Args:
            url: Full URL
            path: URL path

        Returns:
            Asset type ID (0 if unknown)
        """
        url_lower = url.lower()
        path_lower = path.lower()

        # Texture/Image URLs (most common on CDN)
        if any(x in url_lower for x in ['t0.rbxcdn', 't1.rbxcdn', 't2.rbxcdn',
                                          't3.rbxcdn', 't4.rbxcdn', 't5.rbxcdn',
                                          't6.rbxcdn', 't7.rbxcdn']):
            return 1  # Image

        # Content/asset URLs
        if any(x in url_lower for x in ['c0.rbxcdn', 'c1.rbxcdn', 'c2.rbxcdn',
                                          'c3.rbxcdn', 'c4.rbxcdn', 'c5.rbxcdn',
                                          'c6.rbxcdn', 'c7.rbxcdn']):
            # Could be mesh, audio, or other
            if '.mesh' in path_lower or 'meshes' in path_lower:
                return 4  # Mesh
            elif '.mp3' in path_lower or '.ogg' in path_lower or 'audio' in path_lower:
                return 3  # Audio
            elif '.rbxm' in path_lower:
                return 10  # Model
            else:
                return 0  # Unknown

        # AssetDelivery service
        if 'assetdelivery' in url_lower:
            # Try to infer from path
            if 'mesh' in path_lower:
                return 4  # Mesh
            elif 'texture' in path_lower or 'image' in path_lower:
                return 1  # Image
            elif 'audio' in path_lower:
                return 3  # Audio

        return 0  # Unknown type

    def set_enabled(self, enabled: bool):
        """Enable or disable the cache scraper."""
        self.enabled = enabled
        status = 'enabled' if enabled else 'disabled'
        log_buffer.log('Cache', f'Cache scraper {status}')
