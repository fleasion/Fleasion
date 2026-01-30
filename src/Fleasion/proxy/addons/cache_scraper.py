"""Cache scraper addon - intercepts and caches Roblox assets BEFORE replacement.

Uses a two-stage approach matching the Reference implementation:
1. Stage 1: Intercept assetdelivery.roblox.com/v1/assets/batch to track asset IDs and CDN locations
2. Stage 2: Intercept fts.rbxcdn.com to download and cache actual asset content

For KTX textures and TexturePacks, fetches converted PNG data from asset delivery API.
"""

import gzip
import json
import os
import re
import base64
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from mitmproxy import http
import requests

from ...cache.cache_manager import CacheManager
from ...utils import log_buffer


class CacheScraper:
    """Mitmproxy addon that intercepts and caches Roblox assets."""

    DELIVERY_ENDPOINT = '/v1/assets/batch'
    ASSET_DELIVERY_HOST = 'assetdelivery.roblox.com'
    CDN_HOST = 'fts.rbxcdn.com'

    def __init__(self, cache_manager: CacheManager):
        """
        Initialize cache scraper.

        Args:
            cache_manager: CacheManager instance
        """
        self.cache_manager = cache_manager
        self.enabled = True
        # Track asset IDs and their CDN locations (like Reference cache_logs)
        self.cache_logs: dict = {}
        # Fast URL lookup: maps base URL to asset_id for O(1) matching
        self._url_to_asset: dict[str, str] = {}
        # Thread pool for async API calls (avoid blocking proxy event loop)
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='cache_api')
        # Requests session for connection pooling
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': 'Roblox/WinInet'})
        # Configure connection pooling
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=2,
            pool_block=False
        )
        self._session.mount('https://', adapter)

    def response(self, flow: http.HTTPFlow) -> None:
        """
        Handle HTTP responses - cache assets before any modification.

        Uses a two-stage approach:
        1. Intercept assetdelivery.roblox.com/v1/assets/batch to track asset IDs and locations
        2. Intercept fts.rbxcdn.com to download and cache actual content

        Args:
            flow: HTTP flow containing request and response
        """
        if not self.enabled:
            return

        # Only process successful responses
        if flow.response is None or flow.response.status_code != 200:
            return

        url = flow.request.pretty_url
        parsed_url = urlparse(url)
        hostname = parsed_url.hostname

        # Stage 1: Track asset IDs and their CDN locations from asset delivery API
        if hostname == self.ASSET_DELIVERY_HOST:
            if parsed_url.path == self.DELIVERY_ENDPOINT:
                self._handle_asset_delivery(flow)

        # Stage 2: Cache actual content from CDN
        elif hostname == self.CDN_HOST:
            self._handle_cdn_download(flow, url, parsed_url)

    def _handle_asset_delivery(self, flow: http.HTTPFlow) -> None:
        """
        Handle asset delivery batch response - extract asset IDs and CDN locations.

        Args:
            flow: HTTP flow
        """
        try:
            req_encoding = flow.request.headers.get('Content-Encoding', '').lower()
            res_encoding = flow.response.headers.get('Content-Encoding', '').lower()

            req_json = self._parse_body(flow.request.content, req_encoding)
            res_json = self._parse_body(flow.response.content, res_encoding)

            if not req_json or not res_json:
                return

            if not isinstance(req_json, list) or not isinstance(res_json, list):
                return

            tracked_count = 0
            for index, item in enumerate(req_json):
                if not isinstance(item, dict):
                    continue

                if 'assetId' not in item:
                    continue

                asset_id = item['assetId']

                # Skip if already tracked
                if asset_id in self.cache_logs:
                    continue

                # Get corresponding response item
                if index >= len(res_json):
                    continue

                res_item = res_json[index]
                if not isinstance(res_item, dict):
                    continue

                # Extract location and type
                location = res_item.get('location')
                asset_type = res_item.get('assetTypeId')

                if location is not None and asset_type is not None:
                    self.cache_logs[asset_id] = {
                        'location': location,
                        'assetTypeId': asset_type,
                    }
                    # Build URL lookup for O(1) matching
                    base_url = location.split('?')[0]
                    self._url_to_asset[base_url] = asset_id
                    tracked_count += 1

            if tracked_count > 0:
                log_buffer.log('Cache', f'Tracking {tracked_count} asset(s) for caching')

        except Exception as e:
            log_buffer.log('Cache', f'Error in asset delivery handler: {e}')

    def _handle_cdn_download(self, flow: http.HTTPFlow, url: str, parsed_url) -> None:
        """
        Handle CDN download - cache the actual asset content.

        Args:
            flow: HTTP flow
            url: Request URL
            parsed_url: Parsed URL
        """
        try:
            req_base = url.split('?')[0]

            # O(1) URL lookup instead of O(n) linear search
            asset_id = self._url_to_asset.get(req_base)
            if not asset_id:
                return

            # Get asset info
            info = self.cache_logs.get(asset_id)
            if not info or not isinstance(info, dict):
                return

            # Skip if already cached
            if 'cached' in info:
                return

            # Get asset content
            content = flow.response.content
            if not content:
                return

            # Mark as cached in tracking log
            info['cached'] = True

            # Extract hash from path
            cache_hash = parsed_url.path.rsplit('/', 1)[-1]
            asset_type = info.get('assetTypeId', 0)

            # For KTX textures and TexturePacks, queue API conversion in background
            # DO NOT block the proxy handler waiting for API response
            needs_api_conversion = False
            if asset_type in (1, 13) and content.startswith(b'\xABKTX'):
                # KTX texture - queue PNG conversion
                needs_api_conversion = True
            elif asset_type == 63:
                # TexturePack - queue XML fetch
                needs_api_conversion = True

            if needs_api_conversion:
                # Submit to background thread pool - does NOT block
                try:
                    self._executor.submit(
                        self._fetch_and_update_cache,
                        asset_id,
                        asset_type,
                        url,
                        metadata={'url': url, 'content_type': flow.response.headers.get('content-type', ''), 'hash': cache_hash}
                    )
                except RuntimeError as e:
                    log_buffer.log('Cache', f'Failed to submit conversion task: {e}')

            # Build metadata
            metadata = {
                'url': url,
                'content_type': flow.response.headers.get('content-type', ''),
                'content_length': len(content),
                'hash': cache_hash,
            }

            # Store in cache manager asynchronously to avoid blocking proxy handler
            try:
                self._executor.submit(
                    self._store_asset_async,
                    asset_id,
                    asset_type,
                    content,
                    url,
                    metadata
                )
            except RuntimeError as e:
                log_buffer.log('Cache', f'Failed to submit cache store task: {e}')

        except Exception as e:
            log_buffer.log('Cache', f'Error in CDN handler: {e}')

    def _parse_body(self, content: bytes, encoding: str):
        """
        Parse body content, handling gzip compression.

        Args:
            content: Raw content bytes
            encoding: Content encoding

        Returns:
            Parsed JSON or None
        """
        if not content:
            return None

        try:
            if encoding == 'gzip':
                try:
                    content = gzip.decompress(content)
                except OSError:
                    # Not actually gzipped, use raw bytes
                    pass

            return json.loads(content)

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log_buffer.log('Cache', f'Failed to parse JSON: {e}')
            return None

    def _fetch_from_api(self, asset_id: str) -> bytes | None:
        """Fetch asset content from Roblox asset delivery API (uses connection pooling)."""
        try:
            cookie = self._get_roblosecurity()
            headers = {}
            if cookie:
                headers['Cookie'] = f'.ROBLOSECURITY={cookie};'

            api_url = f'https://assetdelivery.roblox.com/v1/asset/?id={asset_id}'
            # Use session for connection pooling and reduced timeout
            response = self._session.get(api_url, headers=headers, timeout=5)

            if response.status_code == 200 and response.content:
                return response.content
        except Exception as e:
            log_buffer.log('Cache', f'API fetch error for {asset_id}: {e}')

        return None

    def _fetch_and_update_cache(self, asset_id: str, asset_type: int, url: str, metadata: dict):
        """Background worker to fetch API content and update cache (runs in thread pool)."""
        try:
            api_content = self._fetch_from_api(asset_id)

            if not api_content:
                return

            # Validate content type
            is_valid = False
            content_desc = ''

            if asset_type in (1, 13) and api_content.startswith(b'\x89PNG'):
                # KTX converted to PNG
                is_valid = True
                content_desc = 'PNG'
            elif asset_type == 63 and (api_content.startswith(b'<roblox>') or b'<roblox>' in api_content[:100]):
                # TexturePack XML
                is_valid = True
                content_desc = 'XML'

            if not is_valid:
                return

            # Update metadata
            metadata['content_length'] = len(api_content)

            # Store in cache (cache_manager has its own locking)
            success = self.cache_manager.store_asset(
                asset_id=str(asset_id),
                asset_type=asset_type,
                data=api_content,
                url=url,
                metadata=metadata
            )

            if success:
                type_name = self.cache_manager.get_asset_type_name(asset_type)
                log_buffer.log('Cache', f'Converted {type_name} to {content_desc}: {asset_id}')

        except Exception as e:
            log_buffer.log('Cache', f'Background conversion error for {asset_id}: {e}')

    def _store_asset_async(self, asset_id: str, asset_type: int, data: bytes, url: str, metadata: dict):
        """Background worker to store cached asset data."""
        try:
            success = self.cache_manager.store_asset(
                asset_id=str(asset_id),
                asset_type=asset_type,
                data=data,
                url=url,
                metadata=metadata
            )

            if success:
                type_name = self.cache_manager.get_asset_type_name(asset_type)
                log_buffer.log(
                    'Cache',
                    f'Cached {type_name}: {asset_id} ({len(data)} bytes)'
                )
        except Exception as e:
            log_buffer.log('Cache', f'Cache store error for {asset_id}: {e}')

    def _get_roblosecurity(self) -> str | None:
        """Get .ROBLOSECURITY cookie from Roblox local storage."""
        try:
            import win32crypt
        except ImportError:
            return None

        path = os.path.expandvars(r'%LocalAppData%/Roblox/LocalStorage/RobloxCookies.dat')
        try:
            if not os.path.exists(path):
                return None
            with open(path, 'r') as f:
                data = json.load(f)
            cookies_data = data.get('CookiesData')
            if not cookies_data:
                return None
            enc = base64.b64decode(cookies_data)
            dec = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
            s = dec.decode(errors='ignore')
            m = re.search(r'\.ROBLOSECURITY\s+([^\s;]+)', s)
            return m.group(1) if m else None
        except Exception:
            return None

    def set_enabled(self, enabled: bool):
        """Enable or disable the cache scraper."""
        self.enabled = enabled
        status = 'enabled' if enabled else 'disabled'
        log_buffer.log('Cache', f'Cache scraper {status}')

    def clear_tracking(self):
        """Clear the asset tracking log."""
        self.cache_logs.clear()
        self._url_to_asset.clear()
        log_buffer.log('Cache', 'Cleared asset tracking log')
