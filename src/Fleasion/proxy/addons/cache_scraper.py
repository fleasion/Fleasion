"""CacheScraper: intercepts and caches Roblox assets before any replacement.

This is a single instance (created by ProxyMaster) so all state is instance-level.
The GUI calls set_enabled() and clear_tracking() directly - no IPC needed since
everything runs in the same process.
"""

import base64
import gzip
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from urllib.parse import urlparse

import requests

from ...cache.cache_manager import CacheManager
from ...utils import log_buffer

try:
    import orjson
    def _loads(s):
        return orjson.loads(s)
except ImportError:
    import json
    def _loads(s):
        return json.loads(s)

logger = logging.getLogger(__name__)

ASSET_DELIVERY_HOST = 'assetdelivery.roblox.com'
CDN_HOST = 'fts.rbxcdn.com'
DELIVERY_ENDPOINT = '/v1/assets/batch'


class CacheScraper:
    """Caches Roblox assets as they are intercepted by the proxy."""

    def __init__(self, cache_manager: CacheManager) -> None:
        self.cache_manager = cache_manager
        self.enabled: bool = False

        self._lock = Lock()
        # asset_id -> {'location': str, 'assetTypeId': int, 'cached'?: True}
        self.cache_logs: dict = {}
        # base CDN URL (no query) -> list[asset_id]  (1:many – same replacement ID → same CDN URL)
        self._url_to_asset: dict[str, list] = {}

        # Background thread pool for API conversion (KTX->PNG etc.)
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='cache_api')

        # Real IPs for intercepted hosts - set by ProxyMaster after DNS resolution
        # (before the hosts file is written). Keyed by hostname.
        # Used to bypass our own hosts file when making direct API calls.
        self._real_ips: dict[str, str] = {}

        # (session removed - API fetches use _https_get() with raw ssl for SNI control)

    # ------------------------------------------------------------------
    # Called from server MITM thread for assetdelivery batch responses
    # ------------------------------------------------------------------

    def process_batch_response(self, req_body: bytes, resp_body: bytes) -> None:
        """Stage 1: extract asset IDs and CDN locations from batch response."""
        if not self.enabled or not req_body or not resp_body:
            return
        try:
            req_json = _loads(req_body)
            res_json = _loads(resp_body)
        except Exception:
            return

        if not isinstance(req_json, list) or not isinstance(res_json, list):
            return

        tracked = 0
        # Late joiners: newly tracked assets whose CDN URL already has a
        # cached sibling from a previous batch.  The Roblox client won't
        # re-fetch the CDN URL, so we must copy the content ourselves.
        to_copy: list[tuple] = []  # (source_id, dest_id, asset_type, url)

        with self._lock:
            for idx, item in enumerate(req_json):
                if not isinstance(item, dict) or 'assetId' not in item:
                    continue
                asset_id = item['assetId']
                if asset_id in self.cache_logs:
                    continue
                if idx >= len(res_json):
                    continue
                res_item = res_json[idx]
                if not isinstance(res_item, dict):
                    continue
                location = res_item.get('location')
                asset_type = res_item.get('assetTypeId')
                if location is not None and asset_type is not None:
                    self.cache_logs[asset_id] = {'location': location, 'assetTypeId': asset_type}
                    base_url = location.split('?')[0]
                    url_list = self._url_to_asset.setdefault(base_url, [])

                    # Check if a sibling for this CDN URL is already cached
                    # (from a previous batch).  If so, mark this one cached and
                    # schedule a content copy instead of waiting for a CDN
                    # response that will never arrive.
                    cached_sibling = None
                    for sibling_id in url_list:
                        sibling_info = self.cache_logs.get(sibling_id)
                        if sibling_info and 'cached' in sibling_info:
                            cached_sibling = sibling_id
                            break
                    if cached_sibling is not None:
                        self.cache_logs[asset_id]['cached'] = True
                        to_copy.append((cached_sibling, asset_id, asset_type, location))

                    url_list.append(asset_id)
                    tracked += 1

        # Submit copy tasks outside the lock
        for source_id, dest_id, asset_type, url in to_copy:
            try:
                self._executor.submit(
                    self._copy_cached_asset, source_id, dest_id, asset_type, url,
                )
            except RuntimeError as exc:
                log_buffer.log('Cache', f'Failed to submit copy task: {exc}')

        if tracked > 0:
            log_buffer.log('Cache', f'Tracking {tracked} asset(s) for caching')

    # ------------------------------------------------------------------
    # Called from server MITM thread for fts.rbxcdn.com responses
    # ------------------------------------------------------------------

    def process_cdn_response(self, full_url: str, path: str, body: bytes, content_type: str) -> None:
        """Stage 2: cache the actual CDN asset bytes."""
        if not self.enabled or not body:
            return

        base_url = full_url.split('?')[0]

        with self._lock:
            asset_ids = self._url_to_asset.get(base_url)
            if not asset_ids:
                return
            # Collect all asset IDs that still need caching for this CDN URL
            pending: list[tuple[int, int]] = []  # (asset_id, asset_type)
            for aid in asset_ids:
                info = self.cache_logs.get(aid)
                if info and 'cached' not in info:
                    info['cached'] = True
                    pending.append((aid, info.get('assetTypeId', 0)))
            if not pending:
                return

        cache_hash = path.rsplit('/', 1)[-1]
        metadata = {
            'url': full_url,
            'content_type': content_type,
            'content_length': len(body),
            'hash': cache_hash,
        }

        # Decompress the body to inspect its true magic bytes.
        # The CDN often serves assets gzip-wrapped regardless of type.
        # We must look at the inner bytes to avoid misidentifying a mesh
        # as an Image or TexturePack (which causes preview failures).
        inner = body
        if body[:2] == b'\x1f\x8b':
            import gzip as _gzip
            try:
                inner = _gzip.decompress(body)
            except Exception:
                inner = body
        elif body[:4] == b'\x28\xb5\x2f\xfd':
            try:
                import zstandard
                inner = zstandard.ZstdDecompressor().decompress(
                    body, max_output_size=64 * 1024 * 1024)
            except Exception:
                inner = body

        # Store / convert for every original asset ID that shares this CDN URL
        for asset_id, asset_type in pending:
            needs_conversion = (
                (asset_type in (1, 13) and inner[:8] in (b'\xabKTX 20\xbb', b'\xabKTX 11\xbb')) or
                asset_type == 63
            )

            if needs_conversion:
                try:
                    self._executor.submit(
                        self._fetch_and_update_cache,
                        asset_id, asset_type, full_url, metadata, body, inner,
                    )
                except RuntimeError as exc:
                    log_buffer.log('Cache', f'Failed to submit conversion task: {exc}')
            else:
                try:
                    self._executor.submit(
                        self._store_asset_async,
                        asset_id, asset_type, inner, full_url, metadata,
                    )
                except RuntimeError as exc:
                    log_buffer.log('Cache', f'Failed to submit cache store task: {exc}')

    # ------------------------------------------------------------------
    # Background workers
    # ------------------------------------------------------------------

    def set_real_ips(self, real_ips: dict[str, str]) -> None:
        """Called by ProxyMaster after DNS resolution (before hosts file is written).
        Stores real IPs so API calls can bypass our hosts file redirect.
        """
        self._real_ips = real_ips
        log_buffer.log('Cache', f'API bypass configured for: {list(real_ips.keys())}')

    def _https_get(self, hostname: str, path: str, extra_headers: dict | None = None,
                   timeout: float = 8.0, max_redirects: int = 6,
                   return_status: bool = False) -> 'bytes | None | tuple[bytes | None, int | None]':
        """Make an HTTPS GET request, bypassing our hosts file by connecting to the
        real IP while passing the original hostname as SNI and Host header.

        Uses raw ssl + http.client so we have complete control over SNI — unlike
        requests/urllib3 which uses the connection-target URL as the SNI hostname,
        breaking TLS when we swap hostname -> IP.

        Critical: we advertise Accept-Encoding: gzip, deflate but NOT zstd.
        Roblox's assetdelivery reads Accept-Encoding to decide which CDN URL to
        redirect to.  Without zstd support signalled, it redirects to the
        gzip-compressed PNG version of the asset (not the KTX2+zstd game-client
        version).  This is exactly what the original mitmproxy implementation got
        because Python's requests library sends gzip/deflate Accept-Encoding by
        default, never zstd.
        """
        import ssl
        import socket
        import http.client
        from urllib.parse import urlparse

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        cur_hostname = hostname
        cur_path = path

        for _ in range(max_redirects):
            real_ip = self._real_ips.get(cur_hostname, cur_hostname)
            try:
                raw_sock = socket.create_connection((real_ip, 443), timeout=timeout)
                ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=cur_hostname)
            except Exception as exc:
                log_buffer.log('Cache', f'Socket connect failed {cur_hostname} ({real_ip}): {exc}')
                return None

            try:
                conn = http.client.HTTPConnection.__new__(http.client.HTTPSConnection)
                http.client.HTTPConnection.__init__(conn, real_ip, 443, timeout=timeout)
                conn.sock = ssl_sock

                # Match the headers that Python's requests library sends by default.
                # Accept-Encoding: gzip, deflate signals we do NOT support zstd, so
                # assetdelivery redirects to the PNG CDN URL, not the KTX2+zstd one.
                req_headers = {
                    'Host': cur_hostname,
                    'User-Agent': 'Roblox/WinInet',
                    'Accept-Encoding': 'gzip, deflate',
                    'Accept': '*/*',
                    'Connection': 'close',
                }
                if extra_headers:
                    req_headers.update(extra_headers)

                conn.request('GET', cur_path, headers=req_headers)
                resp = conn.getresponse()

                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get('Location', '')
                    resp.read()
                    ssl_sock.close()
                    if not location:
                        return None
                    parsed = urlparse(location)
                    cur_hostname = (parsed.hostname or cur_hostname).lower()
                    cur_path = parsed.path
                    if parsed.query:
                        cur_path += '?' + parsed.query
                    continue

                if resp.status == 200:
                    data = resp.read()
                    ssl_sock.close()
                    # Decompress gzip — assetdelivery wraps PNG in gzip when
                    # Accept-Encoding: gzip was advertised
                    ce = resp.headers.get('Content-Encoding', '').lower()
                    if ce == 'gzip' and data:
                        import gzip as _gzip
                        try:
                            data = _gzip.decompress(data)
                        except Exception:
                            pass
                    elif data[:4] == b'\x28\xb5\x2f\xfd':  # zstd magic
                        try:
                            import zstandard
                            data = zstandard.ZstdDecompressor().decompress(
                                data, max_output_size=32 * 1024 * 1024)
                        except Exception:
                            pass
                    result = data if data else None
                    return (result, 200) if return_status else result

                status = resp.status
                resp.read()
                ssl_sock.close()
                return (None, status) if return_status else None
            except Exception as exc:
                try:
                    ssl_sock.close()
                except Exception:
                    pass
                log_buffer.log('Cache', f'HTTP error {cur_hostname}: {exc}')
                return (None, None) if return_status else None

        return (None, None) if return_status else None  # too many redirects

    # ------------------------------------------------------------------
    # Creator place-ID cache (class-level, shared across threads)
    # ------------------------------------------------------------------
    _creator_place_cache: dict[int, list[int]] = {}
    # Fast-path: creator_id -> last place_id that successfully downloaded an asset.
    # Avoids re-iterating the full games list for the same creator.
    _creator_last_success: dict[int, int] = {}

    def _fetch_creator_info(self, asset_id: str) -> tuple[int | None, int | None]:
        """Look up the creator ID and type for an asset via develop.roblox.com.

        Returns (creator_id, creator_type) or (None, None).
        creator_type: 1 = User, 2 = Group.
        """
        try:
            cookie = self._get_roblosecurity()
            extra = {'Accept': 'application/json'}
            if cookie:
                extra['Cookie'] = f'.ROBLOSECURITY={cookie};'
            raw = self._https_get(
                'develop.roblox.com',
                f'/v1/assets?assetIds={asset_id}',
                extra_headers=extra,
            )
            if not raw:
                return None, None
            import json as _json
            data = _json.loads(raw).get('data', [])
            if not data:
                return None, None
            item = data[0]
            creator_obj = item.get('creator') or {}
            creator_id = creator_obj.get('targetId') or item.get('creatorTargetId')
            creator_type = creator_obj.get('typeId') or item.get('creatorType')
            if creator_id is not None:
                creator_id = int(creator_id)
            if creator_type is not None:
                creator_type = int(creator_type)
            return creator_id, creator_type
        except Exception as exc:
            log_buffer.log('Cache', f'Creator info lookup failed for {asset_id}: {exc}')
            return None, None

    def _fetch_place_ids_for_creator(self, creator_id: int, creator_type: int) -> list[int]:
        """Get place IDs owned by the given creator, trying multiple pages.

        Uses games.roblox.com which is public and needs no auth.
        Returns a list of rootPlace.id values (may be empty).
        """
        # Check cache first
        if creator_id in self._creator_place_cache:
            cached = self._creator_place_cache[creator_id]
            return cached if isinstance(cached, list) else ([cached] if cached else [])

        try:
            if creator_type == 1:  # User
                host = 'games.roblox.com'
                base_paths = [f'/v2/users/{creator_id}/games?sortOrder=Asc&limit=100']
            elif creator_type == 2:  # Group
                # Scan public (non-hidden) games first — they're much more likely
                # to contain the asset.  Only fall back to ALL games (including
                # hidden/private) when the public set comes up empty or none of
                # its place IDs succeed later.
                host = 'games.roblox.com'
                base_paths = [
                    f'/v2/groups/{creator_id}/gamesV2?accessFilter=2&limit=100&sortOrder=Asc',  # public
                    f'/v2/groups/{creator_id}/gamesV2?accessFilter=1&limit=100&sortOrder=Asc',  # all (hidden too)
                ]
            else:
                self._creator_place_cache[creator_id] = []
                return []

            place_ids: list[int] = []
            seen: set[int] = set()
            cursor = ''
            # Paginate through up to 3 pages per base path (300 games max with limit=100)
            for base_path in base_paths:
                cursor = ''
                for _page in range(3):
                    path = base_path + (f'&cursor={cursor}' if cursor else '')
                    raw = self._https_get(host, path, extra_headers={'Accept': 'application/json'})
                    if not raw:
                        break

                    import json as _json
                    resp = _json.loads(raw)
                    games = resp.get('data', [])
                    for game in games:
                        root_place = game.get('rootPlace')
                        if root_place and root_place.get('id'):
                            pid = int(root_place['id'])
                            if pid not in seen:
                                place_ids.append(pid)
                                seen.add(pid)

                    cursor = resp.get('nextPageCursor') or ''
                    if not cursor:
                        break

            self._creator_place_cache[creator_id] = place_ids
            if place_ids:
                log_buffer.log('Cache', f'Found {len(place_ids)} place(s) for creator {creator_id}')
            else:
                log_buffer.log('Cache', f'No games found for creator {creator_id}')
            return place_ids
        except Exception as exc:
            log_buffer.log('Cache', f'Place ID lookup failed for creator {creator_id}: {exc}')
            self._creator_place_cache[creator_id] = []
            return []

    def _fetch_asset_with_place_id_retry(
        self, asset_id: str, extra_headers: dict | None = None,
    ) -> tuple[bytes | None, int | None]:
        """Download an asset, retrying with Roblox-Place-Id on 403.

        Tries ALL place IDs from the creator's games list until one works,
        since only the specific game that uses the asset will grant access.

        Returns (data, status_code). status_code is the final HTTP status
        (200 on success, 403/404/etc on failure).
        """
        hdrs = dict(extra_headers) if extra_headers else {}
        data, status = self._https_get(
            'assetdelivery.roblox.com',
            f'/v1/asset/?id={asset_id}',
            extra_headers=hdrs or None,
            return_status=True,
        )
        if data:
            return data, status

        if status != 403:
            return None, status

        # 403 — attempt place-ID bypass
        log_buffer.log('Cache', f'Asset {asset_id} returned 403, looking up creator...')
        creator_id, creator_type = self._fetch_creator_info(asset_id)
        if creator_id is None:
            log_buffer.log('Cache', f'Could not resolve creator for asset {asset_id}')
            return None, 403

        place_ids = self._fetch_place_ids_for_creator(creator_id, creator_type)
        if not place_ids:
            log_buffer.log('Cache', f'No places found for creator {creator_id} of asset {asset_id}')
            return None, 403

        # Fast-path: if we previously succeeded with a place ID for this creator,
        # try it first before iterating the full list.
        last_success = self._creator_last_success.get(creator_id)
        if last_success is not None and last_success in place_ids:
            log_buffer.log('Cache', f'Trying cached place {last_success} for asset {asset_id}')
            retry_hdrs = {**hdrs, 'Roblox-Place-Id': str(last_success)}
            data, status = self._https_get(
                'assetdelivery.roblox.com',
                f'/v1/asset/?id={asset_id}',
                extra_headers=retry_hdrs,
                return_status=True,
            )
            if data:
                log_buffer.log('Cache', f'Successfully downloaded privated asset {asset_id} (cached place {last_success})')
                return data, status

        # Try each place ID until one works
        for place_id in place_ids:
            if place_id == last_success:
                continue  # Already tried above
            log_buffer.log('Cache', f'Trying asset {asset_id} with Roblox-Place-Id: {place_id}')
            retry_hdrs = {**hdrs, 'Roblox-Place-Id': str(place_id)}
            data, status = self._https_get(
                'assetdelivery.roblox.com',
                f'/v1/asset/?id={asset_id}',
                extra_headers=retry_hdrs,
                return_status=True,
            )
            if data:
                log_buffer.log('Cache', f'Successfully downloaded privated asset {asset_id} (place {place_id})')
                self._creator_last_success[creator_id] = place_id
                return data, status

        log_buffer.log('Cache', f'All {len(place_ids)} place IDs failed for asset {asset_id}')
        return None, 403

    def _fetch_from_api(self, asset_id: str) -> bytes | None:
        """Fetch asset from Roblox delivery API for KTX->PNG / TexturePack conversion.

        The /v1/asset/ endpoint performs server-side conversion:
          - KTX textures  -> PNG
          - TexturePacks  -> XML

        Uses _https_get() which connects by real IP with correct SNI so the
        CDN TLS handshake succeeds regardless of our hosts file entries.
        Uses place-ID retry for privated assets.
        """
        try:
            cookie = self._get_roblosecurity()
            extra = {}
            if cookie:
                extra['Cookie'] = f'.ROBLOSECURITY={cookie};'
            data, _status = self._fetch_asset_with_place_id_retry(asset_id, extra_headers=extra or None)
            return data
        except Exception as exc:
            log_buffer.log('Cache', f'API fetch error for {asset_id}: {exc}')
        return None

    def _fetch_and_update_cache(
        self, asset_id: str, asset_type: int, url: str,
        metadata: dict, original_content: bytes | None = None,
        inner_content: bytes | None = None,
    ) -> None:
        try:
            # Try local KTX conversion first (KTX1 ETC and KTX2 BasisU/UASTC).
            # Falls through to API fetch if conversion returns None (unsupported format).
            if asset_type in (1, 13) and inner_content:
                try:
                    from ...cache.tools.ktx_to_png import convert as _ktx_convert
                    png_bytes = _ktx_convert(inner_content)
                except Exception:
                    png_bytes = None
                if png_bytes and png_bytes[:4] == b'\x89PNG':
                    metadata['content_length'] = len(png_bytes)
                    success = self.cache_manager.store_asset(
                        asset_id=str(asset_id), asset_type=asset_type,
                        data=png_bytes, url=url, metadata=metadata,
                    )
                    if success:
                        log_buffer.log('Cache', f'KTX\u2192PNG (local): {asset_id}')
                    return

            api_content = self._fetch_from_api(asset_id)
            if api_content:
                is_valid = False
                content_desc = ''
                if asset_type in (1, 13) and api_content[:4] == b'\x89PNG':
                    is_valid, content_desc = True, 'PNG'
                elif asset_type == 63 and b'<roblox>' in api_content[:100]:
                    is_valid, content_desc = True, 'XML'

                if is_valid:
                    metadata['content_length'] = len(api_content)
                    success = self.cache_manager.store_asset(
                        asset_id=str(asset_id), asset_type=asset_type,
                        data=api_content, url=url, metadata=metadata,
                    )
                    if success:
                        type_name = self.cache_manager.get_asset_type_name(asset_type)
                        log_buffer.log('Cache', f'Converted {type_name} to {content_desc}: {asset_id}')
                        # For TexturePack: also preserve the raw KTX2 CDN bytes as sidecar
                        if asset_type == 63 and inner_content:
                            self.cache_manager.store_raw_asset(str(asset_id), asset_type, inner_content)
                    return

            if original_content is not None:
                metadata['content_length'] = len(original_content)
                success = self.cache_manager.store_asset(
                    asset_id=str(asset_id), asset_type=asset_type,
                    data=original_content, url=url, metadata=metadata,
                )
                if success:
                    type_name = self.cache_manager.get_asset_type_name(asset_type)
                    log_buffer.log('Cache', f'Cached {type_name} (raw fallback): {asset_id}')
        except Exception as exc:
            log_buffer.log('Cache', f'Background conversion error for {asset_id}: {exc}')
            if original_content is not None:
                try:
                    self.cache_manager.store_asset(
                        asset_id=str(asset_id), asset_type=asset_type,
                        data=original_content, url=url, metadata=metadata,
                    )
                except Exception:
                    pass

    def _store_asset_async(
        self, asset_id: str, asset_type: int, data: bytes, url: str, metadata: dict,
    ) -> None:
        try:
            success = self.cache_manager.store_asset(
                asset_id=str(asset_id), asset_type=asset_type,
                data=data, url=url, metadata=metadata,
            )
            if success:
                type_name = self.cache_manager.get_asset_type_name(asset_type)
                log_buffer.log('Cache', f'Cached {type_name}: {asset_id} ({len(data)} bytes)')
        except Exception as exc:
            log_buffer.log('Cache', f'Cache store error for {asset_id}: {exc}')

    def _copy_cached_asset(
        self, source_id, dest_id, asset_type: int, url: str,
    ) -> None:
        """Copy an already-cached asset to a new asset ID (cross-batch replication)."""
        try:
            data = self.cache_manager.get_asset(str(source_id), asset_type)
            if data:
                success = self.cache_manager.store_asset(
                    asset_id=str(dest_id), asset_type=asset_type,
                    data=data, url=url, metadata={'replicated_from': str(source_id)},
                )
                if success:
                    type_name = self.cache_manager.get_asset_type_name(asset_type)
                    log_buffer.log('Cache', f'Replicated {type_name}: {dest_id} (from {source_id})')
        except Exception as exc:
            log_buffer.log('Cache', f'Replication error {source_id}->{dest_id}: {exc}')

    # ------------------------------------------------------------------
    # GUI-callable interface (same as before - no change needed in GUI code)
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        log_buffer.log('Cache', f'Cache scraper {"enabled" if enabled else "disabled"}')

    def clear_tracking(self) -> None:
        with self._lock:
            self.cache_logs.clear()
            self._url_to_asset.clear()
        log_buffer.log('Cache', 'Cleared asset tracking log')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_roblosecurity(self) -> str | None:
        try:
            import win32crypt
        except ImportError:
            return None
        path = os.path.expandvars(r'%LocalAppData%/Roblox/LocalStorage/RobloxCookies.dat')
        try:
            if not os.path.exists(path):
                return None
            import json as _json
            with open(path) as f:
                data = _json.load(f)
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
