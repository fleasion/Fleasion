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
from urllib.parse import urlparse

from mitmproxy import http

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

            # Find matching asset in cache_logs
            for asset_id, info in self.cache_logs.items():
                if not isinstance(info, dict):
                    continue

                location = info.get('location')
                if not location:
                    continue

                # Skip if already cached
                if 'cached' in info:
                    continue

                # Check if this URL matches the tracked location
                cached_base = location.split('?')[0]
                if cached_base != req_base:
                    continue

                # Get asset content
                content = flow.response.content
                if not content:
                    continue

                # Mark as cached in tracking log
                info['cached'] = True

                # Extract hash from path
                cache_hash = parsed_url.path.rsplit('/', 1)[-1]
                asset_type = info.get('assetTypeId', 0)

                # For KTX textures (types 1, 13), fetch PNG from API instead
                if asset_type in (1, 13) and content.startswith(b'\xABKTX'):
                    api_content = self._fetch_from_api(asset_id)
                    if api_content and api_content.startswith(b'\x89PNG'):
                        content = api_content
                        log_buffer.log('Cache', f'Converted KTX to PNG for asset {asset_id}')

                # For TexturePacks (type 63), fetch XML from API
                elif asset_type == 63:
                    api_content = self._fetch_from_api(asset_id)
                    if api_content and (api_content.startswith(b'<roblox>') or b'<roblox>' in api_content[:100]):
                        content = api_content
                        log_buffer.log('Cache', f'Fetched TexturePack XML for asset {asset_id}')

                # Build metadata
                metadata = {
                    'url': url,
                    'content_type': flow.response.headers.get('content-type', ''),
                    'content_length': len(content),
                    'hash': cache_hash,
                }

                # Store in cache manager
                success = self.cache_manager.store_asset(
                    asset_id=str(asset_id),
                    asset_type=asset_type,
                    data=content,
                    url=url,
                    metadata=metadata
                )

                if success:
                    type_name = self.cache_manager.get_asset_type_name(asset_type)
                    log_buffer.log(
                        'Cache',
                        f'Cached {type_name}: {asset_id} ({len(content)} bytes)'
                    )

                # Found match, stop searching
                break

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
        """Fetch asset content from Roblox asset delivery API."""
        import requests

        try:
            cookie = self._get_roblosecurity()
            headers = {'User-Agent': 'Roblox/WinInet'}
            if cookie:
                headers['Cookie'] = f'.ROBLOSECURITY={cookie};'

            api_url = f'https://assetdelivery.roblox.com/v1/asset/?id={asset_id}'
            response = requests.get(api_url, headers=headers, timeout=10)

            if response.status_code == 200 and response.content:
                return response.content
        except Exception as e:
            log_buffer.log('Cache', f'API fetch error for {asset_id}: {e}')

        return None

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
        log_buffer.log('Cache', 'Cleared asset tracking log')
